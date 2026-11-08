"""OpenTelemetry tracing for the agent host (no-op unless OTEL_HTTP_ENDPOINT is set).

A trace follows a request across services: the HTTP request (or the Kafka event, which carries a `traceparent`
header) -> run -> graph step -> model call / MCP tool call -> MCP server -> Spring service.
Outbound HTTP (including MCP) is instrumented, so the trace context travels on automatically.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

log = logging.getLogger("agent.tracing")
_tracer = None


def setup_tracing(endpoint: str | None, service_name: str = "agent-host") -> None:
    global _tracer
    if not endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    _tracer = trace.get_tracer(service_name)
    log.info("OpenTelemetry tracing enabled -> %s", endpoint)


def instrument_app(app) -> None:
    """Server spans for incoming HTTP requests (health and metrics excluded)."""
    if _tracer is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")


def extract_context(carrier: dict[str, str] | None):
    """Trace context from message/request headers (W3C traceparent)."""
    if _tracer is None or not carrier:
        return None
    from opentelemetry import propagate
    return propagate.extract(carrier)


@contextlib.contextmanager
def span(name: str, context: Any = None, **attributes) -> Iterator[Any]:
    """A span that is the current one inside the block. `context` makes it a child of a remote trace."""
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name, context=context) as s:
        for k, v in attributes.items():
            s.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
        yield s


def start_span(name: str, **attributes):
    """A span that is ended later by the caller (for work that starts and ends in different callbacks)."""
    if _tracer is None:
        return None
    s = _tracer.start_span(name)
    for k, v in attributes.items():
        s.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
    return s


def end_span(s, **attributes) -> None:
    if s is None:
        return
    for k, v in attributes.items():
        s.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
    s.end()
