"""HTTP client for everything the UI talks to.

Identity: the UI sends the service token plus X-Customer-Id and X-Roles for the persona chosen in the sidebar.
This is a demo stand-in for a real login (OIDC in front of the gateway); the services enforce ownership and roles
themselves, so a customer persona cannot read another customer's workflow even if the UI allowed it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from config import Config


@dataclass(frozen=True)
class Identity:
    persona: str                      # customer | operator | manager
    customer_id: str | None
    name: str = "demo-user"
    token: str | None = None          # login mode: the user's access token (the only credential the UI holds)

    @property
    def roles(self) -> str:
        return {"operator": "OPERATOR", "manager": "MANAGER,OPERATOR"}.get(self.persona, "")

    @property
    def is_staff(self) -> bool:
        return self.persona in ("operator", "manager")

    @property
    def can_decide(self) -> bool:
        return self.persona == "manager"


class SessionExpired(Exception):
    """The access token was refused (expired or invalid): the user must sign in again."""


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
    def _headers(self, who: Identity) -> dict[str, str]:
        if who.token:                                  # login mode: identity comes from the signed token, nothing else is sent
            return {"Authorization": f"Bearer {who.token}", "Content-Type": "application/json"}
        h = {"X-Service-Token": self.cfg.service_token, "Content-Type": "application/json"}
        if who.customer_id:
            h["X-Customer-Id"] = who.customer_id
        if who.roles:
            h["X-Roles"] = who.roles
        return h

    def _call(self, method: str, base: str, path: str, who: Identity, *, ok_404: bool = False, **kw) -> Any:
        try:
            r = self._http.request(method, f"{base}{path}", headers=self._headers(who), **kw)
        except httpx.TransportError as e:
            raise ApiError(0, "UNREACHABLE", f"Cannot reach {base}: {e.__class__.__name__}") from e
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code == 401 and who.token:
            raise SessionExpired("Your session has expired. Please sign in again.")
        if r.status_code >= 400:
            raise _error_from(r)
        return r.json() if r.content else {}

    # ---------------------------------------------------------------- login
    def login(self, username: str, password: str) -> dict:
        """Exchange credentials for an access token (auth-service)."""
        try:
            r = self._http.post(f"{self.cfg.auth_url}/api/auth/token", json={"username": username, "password": password})
        except httpx.TransportError as e:
            raise ApiError(0, "UNREACHABLE", f"Cannot reach the sign-in service: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise _error_from(r)
        return r.json()

    # ------------------------------------------------------------ workflows
    def create_workflow(self, who: Identity, order_id: str, message: str) -> dict:
        return self._call("POST", self.cfg.workflow_url, "/api/workflows", who, json={"orderId": order_id, "message": message})

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

    def stream_run(self, who: Identity, order_id: str, message: str) -> Iterator[dict]:
        """Starts a run and yields its progress events (server-sent events) as they happen."""
        body = {"customerId": who.customer_id, "orderId": order_id, "message": message}
        try:
            with self._http.stream("POST", f"{self.cfg.agent_url}/api/agent/runs", params={"stream": "true"},
                                   headers=self._headers(who), json=body, timeout=httpx.Timeout(300.0, connect=3.0)) as r:
                if r.status_code == 401 and who.token:
                    raise SessionExpired("Your session has expired. Please sign in again.")
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

    def decide(self, who: Identity, approval_id: str, decision: str, comment: str = "") -> dict:
        return self._call("POST", self.cfg.approval_url, f"/api/approvals/{approval_id}/decision", who,
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
        for name, url in (("auth-service", f"{self.cfg.auth_url}/actuator/health"),
                          ("workflow-service", f"{self.cfg.workflow_url}/actuator/health"),
                          ("approval-service", f"{self.cfg.approval_url}/actuator/health"),
                          ("agent-host", f"{self.cfg.agent_url}/health")):
            try:
                out[name] = self._http.get(url, timeout=2.0).status_code == 200
            except httpx.HTTPError:
                out[name] = False
        return out
