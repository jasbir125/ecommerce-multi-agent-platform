"""HTTP client for everything the UI talks to.

Identity: the UI sends the service token plus X-Customer-Id and X-Roles for the persona chosen in the sidebar.
The services enforce ownership and roles
themselves, so a customer persona cannot read another customer's workflow even if the UI allowed it.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from config import Config

log = logging.getLogger("ui.api")
_SECRET = re.compile(r'("(?:password|token|accessToken|authorization|secret)"\s*:\s*)"[^"]*"', re.I)


def new_correlation_id() -> str:
    """One id per user action. It travels in X-Correlation-Id through every service, agent and Kafka event."""
    return str(uuid.uuid4())


def _safe(text: str | bytes | None, limit: int = 800) -> str:
    if not text:
        return "-"
    t = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    t = _SECRET.sub(r'\1"***"', t).replace("\n", " ")
    return t if len(t) <= limit else t[:limit] + f"...(+{len(t) - limit} chars)"


@dataclass(frozen=True)
class Identity:
    persona: str                      # customer | operator | manager
    customer_id: str | None
    name: str = "demo-user"

    @property
    def roles(self) -> str:
        return {"operator": "OPERATOR", "manager": "MANAGER,OPERATOR"}.get(self.persona, "")

    @property
    def is_staff(self) -> bool:
        return self.persona in ("operator", "manager")

    @property
    def can_decide(self) -> bool:
        return self.persona == "manager"


class ApiError(Exception):
    """A service answered with an error. `code` is the service's stable error code when it gave one."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def __str__(self) -> str:
        return f"{self.message} ({self.code}, HTTP {self.status})" if self.code else f"{self.message} (HTTP {self.status})"


def _error_from(r: httpx.Response) -> ApiError:
    try:
        body = r.json()
    except ValueError:
        return ApiError(r.status_code, "", r.text[:200] or "request failed")
    detail = body.get("detail", body)
    if isinstance(detail, dict):                                   # agent-host style {"code","message"}
        return ApiError(r.status_code, detail.get("code", ""), detail.get("message", str(detail)))
    return ApiError(r.status_code, body.get("code", ""), str(detail))


class ApiClient:
    def __init__(self, cfg: Config | None = None, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg or Config.from_env()
        self._http = httpx.Client(timeout=httpx.Timeout(30.0, connect=3.0), transport=transport)

    # ------------------------------------------------------------- plumbing
    def _headers(self, who: Identity, corr: str | None = None) -> dict[str, str]:
        corr_h = {"X-Correlation-Id": corr or new_correlation_id()}
        h = {"X-Service-Token": self.cfg.service_token, "Content-Type": "application/json", **corr_h}
        if who.customer_id:
            h["X-Customer-Id"] = who.customer_id
        if who.roles:
            h["X-Roles"] = who.roles
        return h

    def _call(self, method: str, base: str, path: str, who: Identity, *, ok_404: bool = False, corr: str | None = None, **kw) -> Any:
        headers = self._headers(who, corr)
        cid, t0 = headers["X-Correlation-Id"], time.perf_counter()
        log.info("-> %s %s%s user=%s corr=%s body=%s", method, base, path, who.name, cid, _safe(json.dumps(kw["json"]) if "json" in kw else None))
        try:
            r = self._http.request(method, f"{base}{path}", headers=headers, **kw)
        except httpx.TransportError as e:
            log.warning("<- %s %s%s UNREACHABLE %s corr=%s", method, base, path, e.__class__.__name__, cid)
            raise ApiError(0, "UNREACHABLE", f"Cannot reach {base}: {e.__class__.__name__}") from e
        (log.warning if r.status_code >= 400 else log.info)("<- %s %s%s %s %.0fms corr=%s resp=%s", method, base, path, r.status_code,
                                                           (time.perf_counter() - t0) * 1000, cid, _safe(r.content))
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code >= 400:
            raise _error_from(r)
        return r.json() if r.content else {}

    # ------------------------------------------------------------ workflows
    def create_workflow(self, who: Identity, order_id: str, message: str, correlation_id: str | None = None) -> dict:
        return self._call("POST", self.cfg.workflow_url, "/api/workflows", who, corr=correlation_id, json={"orderId": order_id, "message": message})

    def get_workflow(self, who: Identity, workflow_id: str) -> dict:
        return self._call("GET", self.cfg.workflow_url, f"/api/workflows/{workflow_id}", who)

    def list_workflows(self, who: Identity, status: str | None = None) -> list[dict]:
        return self._call("GET", self.cfg.workflow_url, "/api/workflows", who, params={"status": status} if status else None)

    def workflow_trace(self, who: Identity, workflow_id: str) -> dict:
        """Workflow, state history, audit events and Kafka events (with payloads)."""
        return self._call("GET", self.cfg.workflow_url, f"/api/workflows/{workflow_id}/trace", who)

    def recent_events(self, who: Identity, limit: int = 50, topic: str | None = None) -> list[dict]:
        return self._call("GET", self.cfg.workflow_url, "/api/workflows/events/recent", who,
                          params={"limit": limit, **({"topic": topic} if topic else {})})

    # ------------------------------------------------------------ agent host
    def get_run(self, who: Identity, workflow_id: str) -> dict | None:
        return self._call("GET", self.cfg.agent_url, f"/api/agent/runs/{workflow_id}", who, ok_404=True)

    def run_trace(self, who: Identity, workflow_id: str) -> dict | None:
        return self._call("GET", self.cfg.agent_url, f"/api/agent/runs/{workflow_id}/trace", who, ok_404=True)

    def stream_run(self, who: Identity, order_id: str, message: str, correlation_id: str | None = None) -> Iterator[dict]:
        """Starts a run and yields its progress events (server-sent events) as they happen."""
        body = {"customerId": who.customer_id, "orderId": order_id, "message": message}
        headers = self._headers(who, correlation_id)
        log.info("-> POST %s/api/agent/runs?stream=true user=%s corr=%s body=%s", self.cfg.agent_url, who.name, headers["X-Correlation-Id"], _safe(json.dumps(body)))
        try:
            with self._http.stream("POST", f"{self.cfg.agent_url}/api/agent/runs", params={"stream": "true"},
                                   headers=headers, json=body, timeout=httpx.Timeout(300.0, connect=3.0)) as r:
                log.info("<- stream opened status=%s corr=%s", r.status_code, headers["X-Correlation-Id"])
                if r.status_code >= 400:
                    r.read()
                    raise _error_from(r)
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        yield json.loads(line[6:])
        except httpx.TransportError as e:
            raise ApiError(0, "UNREACHABLE", f"Lost connection to the agent host: {e.__class__.__name__}") from e

    # ------------------------------------------------------------ approvals
    def list_approvals(self, who: Identity, status: str | None = None) -> list[dict]:
        return self._call("GET", self.cfg.approval_url, "/api/approvals", who, params={"status": status} if status else None)

    def decide(self, who: Identity, approval_id: str, decision: str, comment: str = "", correlation_id: str | None = None) -> dict:
        return self._call("POST", self.cfg.approval_url, f"/api/approvals/{approval_id}/decision", who, corr=correlation_id,
                          json={"decision": decision, "decidedBy": who.name, "comment": comment or None})

    # ----------------------------------------------------------------- audit
    def audit(self, who: Identity, workflow_id: str) -> list[dict]:
        return self._call("GET", self.cfg.audit_url, f"/api/audit/workflows/{workflow_id}", who)

    # ------------------------------------------------------------------- RAG
    def ask_policy(self, who: Identity, question: str, **filters: str | None) -> dict:
        body = {"question": question, **{k: v for k, v in filters.items() if v}}
        return self._call("POST", self.cfg.agent_url, "/api/agent/rag/ask", who, json=body)

    # ----------------------------------------------------------------- admin
    def admin_metrics(self, who: Identity, hours: int = 24) -> dict:
        return self._call("GET", self.cfg.agent_url, "/api/agent/admin/metrics", who, params={"hours": hours})

    def health(self) -> dict[str, bool]:
        out = {}
        for name, url in (("workflow-service", f"{self.cfg.workflow_url}/actuator/health"),
                          ("approval-service", f"{self.cfg.approval_url}/actuator/health"),
                          ("agent-host", f"{self.cfg.agent_url}/health")):
            try:
                out[name] = self._http.get(url, timeout=2.0).status_code == 200
            except httpx.HTTPError:
                out[name] = False
        return out
