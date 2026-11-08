"""Pure ASGI middleware: one "in" and one "out" log line for every API call, with correlation ids and bodies."""
from __future__ import annotations

import logging
import time
import uuid

from app.observability import logctx

log = logging.getLogger("http.access")
SKIP = ("/health", "/metrics")


class RequestLogMiddleware:
    """  -> POST /api/agent/runs from=1.2.3.4
         <- POST /api/agent/runs 200 17.1s caller=CUST-1001 req={...} resp={...}

    The correlation id is taken from X-Correlation-Id (or the trace id of a W3C traceparent, or created) and returned in
    the response header. Streaming (SSE) responses are logged by size only; their steps are logged by the agents."""

    def __init__(self, app, service: str = "agent-host"):
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(SKIP):
            return await self.app(scope, receive, send)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        corr = headers.get("x-correlation-id") or _trace_of(headers.get("traceparent")) or str(uuid.uuid4())
        wf = headers.get("x-workflow-id") or logctx.workflow_from_text(scope["path"])
        info: dict = {}
        info_token = logctx.request_info.set(info)
        tokens = logctx.bind(correlation_id=corr, workflow_id=wf)
        limit = logctx.payload_limit()
        target = scope["method"] + " " + scope["path"] + (("?" + scope["query_string"].decode()) if scope.get("query_string") else "")
        client = (scope.get("client") or ("-",))[0]
        log.info("-> %s from=%s corr=%s wf=%s", target, client, corr, wf or "-")

        req_body, res_body = bytearray(), bytearray()
        state = {"status": 500, "stream": False, "chunks": 0}

        async def recv():
            message = await receive()
            if message["type"] == "http.request" and len(req_body) < limit * 2:
                req_body.extend(message.get("body", b""))
            return message

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                hdrs = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-correlation-id"]
                hdrs.append((b"x-correlation-id", corr.encode()))
                message = {**message, "headers": hdrs}
                state["stream"] = any(k.lower() == b"content-type" and b"text/event-stream" in v for k, v in hdrs)
            elif message["type"] == "http.response.body":
                state["chunks"] += 1
                if not state["stream"] and len(res_body) < limit * 2:
                    res_body.extend(message.get("body", b""))
            await send(message)

        t0 = time.perf_counter()
        try:
            await self.app(scope, recv, send_wrapper)
        except Exception:
            log.exception("<- %s unhandled error corr=%s", target, corr)
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000
            resp = f"<stream, {state['chunks']} chunks>" if state["stream"] else logctx.clean(bytes(res_body), limit)
            wf_now = info.get("workflow_id") or wf or logctx.workflow_from_text(bytes(res_body).decode("utf-8", "replace")) or \
                logctx.workflow_from_text(bytes(req_body).decode("utf-8", "replace"))
            if wf_now:
                logctx.bind(workflow_id=wf_now)
            status = state["status"]
            line = "<- %s %s %s caller=%s corr=%s wf=%s req=%s resp=%s"
            args = (target, status, f"{ms:.0f}ms", info.get("customer_id") or "-", corr, wf_now or "-",
                    logctx.clean(bytes(req_body), limit), resp)
            (log.error if status >= 500 else log.warning if status >= 400 else log.info)(line, *args)
            logctx.unbind(tokens)
            logctx.request_info.reset(info_token)


def _trace_of(traceparent: str | None) -> str | None:
    parts = (traceparent or "").split("-")
    return parts[1] if len(parts) == 4 and len(parts[1]) == 32 else None
