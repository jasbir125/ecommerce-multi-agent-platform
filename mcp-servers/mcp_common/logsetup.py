"""Readable, correlated logs for the MCP servers (same format as the agent host).

    2026-11-08 10:15:02.114 INFO  [ecommerce-mcp-server corr=3f1c... wf=WF-2F04 customer=CUST-1001] mcp.tool: TOOL IN  get_shipment_status args={...}

LOG_LEVEL (INFO), LOG_FORMAT (text|json), LOG_PAYLOAD_CHARS (1500).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from contextvars import ContextVar

corr: ContextVar[str] = ContextVar("corr", default="-")
wf: ContextVar[str] = ContextVar("wf", default="-")
customer: ContextVar[str] = ContextVar("customer", default="-")

_KEYS = r"password|passwd|secret|token|accesstoken|refreshtoken|idtoken|authorization|apikey|api_key|privatekey|private_key|servicetoken|cardnumber|cvv|ssn"
_SENSITIVE = re.compile(r'("(?:' + _KEYS + r')"\s*:\s*)"(?:[^"\\]|\\.)*"', re.I)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.-])\+?\d{1,3}[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w-])")
_API_KEY = re.compile(r"sk-[A-Za-z0-9_-]{16,}")


def clean(value, limit: int | None = None) -> str:
    """Safe-to-log text: secrets and personal data masked, whitespace flattened, length capped."""
    limit = limit or int(os.getenv("LOG_PAYLOAD_CHARS", "1500"))
    if value is None:
        return "-"
    text = value if isinstance(value, str) else json.dumps(value, default=lambda o: getattr(o, "model_dump", lambda **_: str(o))(), ensure_ascii=False)
    for pattern, repl in ((_SENSITIVE, r'\1"***"'), (_JWT, "[jwt]"), (_BEARER, "Bearer ***"), (_API_KEY, "[api-key]"), (_EMAIL, "[email]"), (_PHONE, "[phone]")):
        text = pattern.sub(repl, text)
    text = re.sub(r"\s*[\r\n]+\s*", " ", text)
    return f"{text[:limit]}...(+{len(text) - limit} chars)" if len(text) > limit else (text or "-")


class _Filter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.corr, record.wf, record.customer = corr.get(), wf.get(), customer.get()
        return True


class _Json(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}", "level": record.levelname, "service": self.service,
               "logger": record.name, "message": record.getMessage(), "corr": record.corr, "wf": record.wf, "customer": record.customer}
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_logging(service: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_Filter())
    if os.getenv("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(_Json(service))
    else:
        handler.setFormatter(logging.Formatter(
            f"%(asctime)s.%(msecs)03d %(levelname)-5s [{service} corr=%(corr)s wf=%(wf)s customer=%(customer)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    for noisy in ("httpx", "httpcore", "mcp.server", "mcp.server.streamable_http", "uvicorn.access", "docket"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
