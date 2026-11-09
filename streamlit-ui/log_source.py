"""Rebuilds one request's journey from the application containers' logs (the same idea as `make trace`).

The UI container reads the logs of the other containers through the Docker API socket (mounted read-only in docker-compose.yml),
keeps the lines that carry the workflow id or any correlation id used on that case, and sorts them by time.
"""
from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass

import httpx

SOCKET = "/var/run/docker.sock"
INFRA = {"postgres", "kafka", "kafka-ui", "redis", "otel-collector", "jaeger", "prometheus", "grafana"}   # not part of a request's story

WF = re.compile(r"\bWF-[A-Za-z0-9]{6,}\b")
WF_CONTEXT = re.compile(r"\bwf=(WF-[A-Za-z0-9]{6,})")
CORR = re.compile(r"\bcorr=([0-9a-fA-F-]{8,64})")
CORR_JSON = re.compile(r'"correlationId"\s*:\s*"([^"]+)"')
DOCKER_LINE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\dT[\d:.]+Z)\s?(?P<msg>.*)$")
SPRING = re.compile(r"^\S+\s+(?P<lvl>[A-Z]+)\s+\[[^\]]*\]\s+\d+\s+---\s+\[[^\]]*\]\s+\[[^\]]*\]\s+\[[^\]]*\]\s+(?P<logger>\S+)\s*:\s(?P<rest>.*)$")
PYTHON = re.compile(r"^\d{4}-\d\d-\d\d \S+\s+(?P<lvl>[A-Z]+)\s+\[(?P<ctx>[^\]]*)\]\s+(?P<logger>\S+):\s(?P<rest>.*)$")
LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\b")
# the UI's own polling of a workflow (every few seconds) would drown the real story
POLLING = re.compile(r"\bGET\b\S*\s+\S*/api/(?:(?:workflows|agent/runs|audit/workflows)/WF-[A-Za-z0-9]+(?:/trace)?|approvals|workflows(?:/events/recent)?)(?:\?\S*)?(?:\s|$)")
# lines that are a request or a response between two parts of the system
EXCHANGE = re.compile(r"(-> (GET|POST|PUT|PATCH|DELETE)|<- (GET|POST|PUT|PATCH|DELETE)|=> (GET|POST|PUT|PATCH|DELETE)|<= (GET|POST|PUT|PATCH|DELETE)"
                      r"|AGENT [><]|TOOL (IN|OUT)|LLM [><]|RAG [><]|EVENT |RUN )")


@dataclass(frozen=True)
class Row:
    ts: str            # ISO timestamp from Docker (UTC)
    service: str
    level: str
    text: str          # logger and message, without the repeated timestamp / thread / context brackets
    raw: str           # the line exactly as the service wrote it

    @property
    def exchange(self) -> bool:
        return bool(EXCHANGE.search(self.text))


def tidy(msg: str) -> tuple[str, str]:
    m = SPRING.match(msg)
    if m:
        return m["lvl"], f"{m['logger']}: {m['rest']}"
    m = PYTHON.match(msg)
    if m:
        agent = re.search(r"agent=(\S+)", m["ctx"])
        tag = f"[{agent[1]}] " if agent and agent[1] != "-" else ""
        return m["lvl"], f"{tag}{m['logger']}: {m['rest']}"
    found = LEVEL.search(msg)
    return (found[1] if found else "INFO"), msg


def frames(data: bytes) -> str:
    """Docker multiplexes stdout/stderr as [type, 0, 0, 0, size(4 bytes)] + payload. Returns the plain text."""
    out, i = [], 0
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i + 4:i + 8])[0]
        out.append(data[i + 8:i + 8 + size].decode("utf-8", "replace"))
        i += 8 + size
    return "".join(out)


def related_ids(rows: list[Row], workflow_id: str) -> set[str]:
    """The workflow id plus every correlation id used on that case's own lines (the customer's request, the manager's approval click,
    the resume after approval...)."""
    ids = {workflow_id}
    for r in rows:
        if workflow_id in r.raw:
            ids.update(CORR.findall(r.raw))
            ids.update(CORR_JSON.findall(r.raw))
    return {i for i in ids if i and i != "-"}


def journey(rows: list[Row], workflow_id: str) -> tuple[list[Row], set[str]]:
    rows = [r for r in rows if not POLLING.search(r.text)]
    ids = related_ids(rows, workflow_id)
    hits = sorted({(r.ts, r.service, r.raw): r for r in rows if any(i in r.raw for i in ids)}.values(), key=lambda r: (r.ts, r.service))
    return hits, ids


class LogSource:
    def __init__(self, socket: str = SOCKET, project: str | None = None, transport: httpx.BaseTransport | None = None):
        self.project = project or os.environ.get("COMPOSE_PROJECT_NAME", "ecommerce-multi-agent-platform")
        self.client = httpx.Client(base_url="http://docker", timeout=20, transport=transport or httpx.HTTPTransport(uds=socket))

    def unavailable_reason(self) -> str | None:
        """None when the Docker socket answers, otherwise a short reason."""
        try:
            self.client.get("/_ping").raise_for_status()
            return None
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {str(e)[:120]}"

    def rows(self, since_epoch: int) -> list[Row]:
        filters = json.dumps({"label": [f"com.docker.compose.project={self.project}"]})
        containers = self.client.get("/containers/json", params={"filters": filters}).json()
        out: list[Row] = []
        for c in containers:
            service = c["Labels"].get("com.docker.compose.service", c["Names"][0].lstrip("/"))
            if service in INFRA:
                continue
            r = self.client.get(f"/containers/{c['Id']}/logs", params={"stdout": 1, "stderr": 1, "timestamps": 1, "since": since_epoch})
            if r.status_code != 200:
                continue
            for line in frames(r.content).splitlines():
                m = DOCKER_LINE.match(line)
                if m:
                    level, text = tidy(m["msg"])
                    out.append(Row(m["ts"], service, level, text, m["msg"]))
        return out

    def journey(self, workflow_id: str, since_epoch: int) -> tuple[list[Row], set[str]]:
        return journey(self.rows(since_epoch), workflow_id)
