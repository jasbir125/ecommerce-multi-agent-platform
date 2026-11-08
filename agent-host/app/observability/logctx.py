"""Correlated, readable logging.

Every log line carries the ids needed to follow one customer request through the whole platform:

    2026-11-08 10:15:02.114 INFO  [agent-host trace=4bf9... corr=3f1c... wf=WF-2F04 agent=shipment] agent.tool: TOOL < get_shipment_status 41ms ok result={...}

- corr   one id per customer request. Created by the UI (X-Correlation-Id), kept by every service and Kafka event.
- wf     the workflow (case) id, e.g. WF-2F040AB9C9.
- trace  the OpenTelemetry trace id (the same id Jaeger shows).
- agent  the graph step / agent that is running (supervisor, order, shipment, policy, ...).

Payloads in log lines are masked (tokens, passwords, e-mail, phone) and cut to LOG_PAYLOAD_CHARS characters.

Settings (environment): LOG_LEVEL (INFO), LOG_FORMAT (text | json), LOG_PAYLOAD_CHARS (1500),
LOG_LLM_CONTENT (true: log prompts and answers of model calls, masked and truncated; false: sizes only).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
from contextvars import ContextVar
from typing import Any, Iterator

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
workflow_id: ContextVar[str | None] = ContextVar("workflow_id", default=None)
customer_id: ContextVar[str | None] = ContextVar("customer_id", default=None)
agent: ContextVar[str | None] = ContextVar("agent", default=None)
# Name of the graph step the current task is running (each parallel step runs in its own task).
CURRENT_NODE: ContextVar[str | None] = ContextVar("current_node", default=None)
_VARS = {"correlation_id": correlation_id, "workflow_id": workflow_id, "customer_id": customer_id, "agent": agent}
# Ids learned while a request is being handled (the customer after login, the workflow after it is created) are also
# written here, because code running in a child task cannot change the parent's context variables.
request_info: ContextVar[dict | None] = ContextVar("request_info", default=None)

_SENSITIVE_KEYS = r"password|passwd|secret|token|accesstoken|refreshtoken|idtoken|authorization|apikey|api_key|privatekey|private_key|servicetoken|cardnumber|cvv|ssn"
_SENSITIVE_JSON = re.compile(r'("(?:' + _SENSITIVE_KEYS + r')"\s*:\s*)"(?:[^"\\]|\\.)*"', re.I)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.-])\+?\d{1,3}[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w-])")
_API_KEY = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_WF = re.compile(r"WF-[A-Za-z0-9]+")


def payload_limit() -> int:
    try:
        return int(os.getenv("LOG_PAYLOAD_CHARS", "1500"))
    except ValueError:
        return 1500


def llm_content_enabled() -> bool:
    return os.getenv("LOG_LLM_CONTENT", "true").lower() == "true"


def clean(value: Any, limit: int | None = None) -> str:
    """Text form of `value` that is safe to log: secrets and personal data masked, whitespace flattened, length capped."""
    limit = limit or payload_limit()
    if value is None:
        return "-"
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "replace")
    text = value if isinstance(value, str) else json.dumps(value, default=_default, ensure_ascii=False)
    text = _SENSITIVE_JSON.sub(r'\1"***"', text)
    text = _JWT.sub("[jwt]", text)
    text = _BEARER.sub("Bearer ***", text)
    text = _API_KEY.sub("[api-key]", text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    text = re.sub(r"\s*[\r\n]+\s*", " ", text)
    if len(text) > limit:
        return f"{text[:limit]}...(+{len(text) - limit} chars)"
    return text or "-"


def _default(o: Any) -> Any:
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    return str(o)


# ---------------------------------------------------------------------------- context
def bind(**ids: str | None) -> list[tuple[ContextVar, Any]]:
    """Set the given ids for the current task. Returns tokens for `unbind`. Unknown names are ignored; None values are skipped."""
    tokens = []
    info = request_info.get()
    for name, value in ids.items():
        var = _VARS.get(name)
        if var is not None and value is not None:
            tokens.append((var, var.set(value)))
            if info is not None and name in ("customer_id", "workflow_id"):
                info[name] = value
    return tokens


def unbind(tokens: list[tuple[ContextVar, Any]]) -> None:
    for var, token in reversed(tokens):
        try:
            var.reset(token)
        except ValueError:               # reset from another context: harmless
            pass


@contextlib.contextmanager
def scope(**ids: str | None) -> Iterator[None]:
    tokens = bind(**ids)
    try:
        yield
    finally:
        unbind(tokens)


def workflow_from_text(text: str | None) -> str | None:
    m = _WF.search(text or "")
    return m.group() if m else None


def trace_id() -> str:
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else "-"
    except Exception:  # noqa: BLE001 - tracing is optional
        return "-"


# --------------------------------------------------------------------------- logging
class ContextFilter(logging.Filter):
    """Adds the current ids to every record so formatters can print them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.corr = correlation_id.get() or "-"
        record.wf = workflow_id.get() or "-"
        record.agent = agent.get() or "-"
        record.customer = customer_id.get() or "-"
        record.trace = trace_id()
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}", "level": record.levelname,
               "service": self.service, "logger": record.name, "message": record.getMessage(),
               "trace": getattr(record, "trace", "-"), "corr": getattr(record, "corr", "-"), "wf": getattr(record, "wf", "-"),
               "agent": getattr(record, "agent", "-"), "customer": getattr(record, "customer", "-")}
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_logging(service: str) -> None:
    """Replace the root handlers with one that prints the correlation ids. Call once at start-up."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(ContextFilter())
    if os.getenv("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(logging.Formatter(
            f"%(asctime)s.%(msecs)03d %(levelname)-5s [{service} trace=%(trace)s corr=%(corr)s wf=%(wf)s agent=%(agent)s] %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    for noisy in ("mcp.client.streamable_http", "httpx", "httpx2", "httpcore", "aiokafka", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)      # per-call lines come from our own loggers, with ids
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
