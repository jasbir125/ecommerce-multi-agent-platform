"""OpenTelemetry tracing for graph nodes and tool calls (no-op unless an OTLP endpoint is set)."""
from __future__ import annotations

import contextlib
import logging
from typing import Iterator

log = logging.getLogger("agent.tracing")
_tracer = None


def setup_tracing(endpoint: str | None, service_name: str = "agent-host") -> None:
    global _tracer
    if not endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    base = endpoint.rstrip("/")
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    log.info("OpenTelemetry tracing enabled -> %s", base)


@contextlib.contextmanager
def span(name: str, **attributes) -> Iterator[None]:
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, str(v))
        yield
