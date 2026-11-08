"""Tracing, metrics and a middleware for the MCP servers.

- Continues the caller's trace: the agent host sends a W3C `traceparent` header; each tool call becomes a span under it,
  and the outbound HTTP calls to the Spring services (instrumented httpx) carry the trace on.
- Counts tool calls by result and guardrail rejections by code, and times every tool call. Served at /metrics.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from . import logsetup

log = logging.getLogger("mcp.observability")
tool_log = logging.getLogger("mcp.tool")
TOOL_CALLS = Counter("mcp_tool_calls", "MCP tool calls", ["server", "tool", "result"])
TOOL_LATENCY = Histogram("mcp_tool_latency_seconds", "MCP tool call duration", ["server", "tool"],
                         buckets=(.01, .025, .05, .1, .25, .5, 1, 2, 5, 10))
REJECTIONS = Counter("mcp_guardrail_rejections", "Tool calls refused by a guardrail or a domain rule, by error code", ["server", "code"])
_CODE = re.compile(r"^([A-Z][A-Z0-9_]{3,}):")
_tracer = None


def setup(service_name: str) -> None:
    """Enable tracing if OTEL_HTTP_ENDPOINT is set (OTLP over HTTP to the collector) and instrument outbound HTTP."""
    global _tracer
    endpoint = os.getenv("OTEL_HTTP_ENDPOINT")
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
    log.info("tracing enabled -> %s", endpoint)


def metrics_response(_request: Any) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class ObservabilityMiddleware(Middleware):
    def __init__(self, server: str):
        self.server = server

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool = getattr(context.message, "name", "unknown")
        start = time.perf_counter()
        h = get_http_headers(include_all=True)
        tokens = (logsetup.corr.set(h.get("x-correlation-id") or "-"), logsetup.wf.set(h.get("x-workflow-id") or "-"),
                  logsetup.customer.set(h.get("x-customer-id") or "-"))
        tool_log.info("TOOL IN  %s args=%s", tool, logsetup.clean(getattr(context.message, "arguments", None)))
        span_cm = None
        if _tracer is not None:
            from opentelemetry import propagate, trace
            parent = propagate.extract(get_http_headers(include_all=True))
            span_cm = _tracer.start_as_current_span(f"mcp.tool {tool}", context=parent, kind=trace.SpanKind.SERVER,
                                                    attributes={"mcp.server": self.server, "mcp.tool": tool})
            span = span_cm.__enter__()
        try:
            result = await call_next(context)
            TOOL_CALLS.labels(self.server, tool, "ok").inc()
            tool_log.info("TOOL OUT %s ok %.0fms result=%s", tool, (time.perf_counter() - start) * 1000,
                          logsetup.clean(getattr(result, "structured_content", None) or [getattr(c, "text", c) for c in getattr(result, "content", [])]))
            return result
        except Exception as e:  # noqa: BLE001
            m = _CODE.match(str(e))
            TOOL_CALLS.labels(self.server, tool, "error").inc()
            tool_log.warning("TOOL OUT %s REFUSED/FAILED %.0fms error=%s", tool, (time.perf_counter() - start) * 1000, logsetup.clean(str(e), 500))
            REJECTIONS.labels(self.server, m.group(1) if m else "ERROR").inc()
            if span_cm is not None:
                span.record_exception(e)
                span.set_attribute("mcp.error_code", m.group(1) if m else "ERROR")
            raise
        finally:
            TOOL_LATENCY.labels(self.server, tool).observe(time.perf_counter() - start)
            if span_cm is not None:
                span_cm.__exit__(None, None, None)
            for var, token in zip((logsetup.corr, logsetup.wf, logsetup.customer), tokens):
                var.reset(token)
