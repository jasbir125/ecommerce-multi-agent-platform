#!/usr/bin/env python3
"""Follow one request through every service.

    python3 scripts/trace_request.py <correlation-id | workflow-id | order-id>  [--since 2h] [--service agent] [--full] [--no-http]

Reads the logs of all running containers (`docker compose logs`), keeps the lines that carry the id, widens the search to the
other ids seen on those lines (the case's workflow id <-> its correlation ids, so the manager's approval click is included),
sorts everything by time and prints one readable timeline:

    10:15:01.226  workflow-service    INFO   <- POST /api/workflows 201 40ms caller=jwt:alice ... req={...} resp={...}
    10:15:01.810  agent-host          INFO   AGENT > shipment received input={...}
    10:15:02.044  ecommerce-mcp       INFO   TOOL IN  get_shipment_status args={...}

Standard library only. Where a deployment has no docker compose (AWS), run the same search in CloudWatch Logs Insights:
    fields @timestamp, @log, @message | filter @message like /<id>/ | sort @timestamp asc
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

LINE = re.compile(r"^(?P<container>\S+)\s+\|\s+(?P<ts>\d{4}-\d\d-\d\dT[\d:.]+Z)\s?(?P<msg>.*)$")
WF = re.compile(r"\bWF-[A-Za-z0-9]{6,}\b")
WF_CONTEXT = re.compile(r"\bwf=(WF-[A-Za-z0-9]{6,})")
CORR = re.compile(r"\bcorr=([0-9a-fA-F-]{8,64})")
CORR_JSON = re.compile(r'"correlationId"\s*:\s*"([^"]+)"')
SPRING = re.compile(r"^\S+\s+(?P<lvl>[A-Z]+)\s+\[[^\]]*\]\s+\d+\s+---\s+\[[^\]]*\]\s+\[[^\]]*\]\s+\[[^\]]*\]\s+(?P<logger>\S+)\s*:\s(?P<rest>.*)$")
PYTHON = re.compile(r"^\d{4}-\d\d-\d\d \S+\s+(?P<lvl>[A-Z]+)\s+\[(?P<ctx>[^\]]*)\]\s+(?P<logger>\S+):\s(?P<rest>.*)$")
LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\b")
HTTP_NOISE = re.compile(r"(-> (GET|POST)|=> (GET|POST))")


def tidy(msg: str) -> tuple[str, str]:
    """(level, 'logger: message') without the repeated timestamps, thread names and context brackets."""
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


def fetch_logs(since: str) -> list[tuple[str, str, str]]:
    out = subprocess.run(["docker", "compose", "--profile", "apps", "logs", "--no-color", "--timestamps", "--since", since],
                         capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        sys.exit(f"docker compose logs failed: {out.stderr.strip()[:300]}")
    rows = []
    for raw in out.stdout.splitlines():
        m = LINE.match(raw)
        if m:
            rows.append((m["ts"], m["container"].removeprefix("platform-"), m["msg"]))
    return rows


def related_ids(rows, seed: str) -> set[str]:
    """The id itself, the case's workflow id, and every correlation id used on that case's own log lines
    (the customer's request, the manager's approval click, the resume after approval...).
    A line belongs to a case only if the workflow id is in its context (wf=...) or in the URL path, so list endpoints that merely
    mention many workflows in their response do not drag unrelated requests in."""
    wfs = {seed} if WF.fullmatch(seed) else set()
    if not wfs:
        for _, _, msg in rows:
            if seed in msg:
                wfs.update(WF_CONTEXT.findall(msg))
    corrs = {seed}
    for _, _, msg in rows:
        if seed in msg or any(f"wf={w}" in msg or f"/{w}" in msg for w in wfs):
            corrs.update(CORR.findall(msg))
            corrs.update(CORR_JSON.findall(msg))
    return {i for i in wfs | corrs | {seed} if i and i != "-"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", help="correlation id, workflow id (WF-...) or order id")
    ap.add_argument("--since", default="3h", help="how far back to read logs (default 3h)")
    ap.add_argument("--service", help="only show services whose name contains this text")
    ap.add_argument("--full", action="store_true", help="do not shorten long lines")
    ap.add_argument("--no-expand", action="store_true", help="match only the given id")
    args = ap.parse_args()

    rows = fetch_logs(args.since)
    ids = {args.id} if args.no_expand else related_ids(rows, args.id)
    hits = sorted((r for r in rows if any(i in r[2] for i in ids)), key=lambda r: r[0])
    if args.service:
        hits = [r for r in hits if args.service in r[1]]
    if not hits:
        sys.exit(f"No log lines contain {args.id} in the last {args.since}. Check the id, or widen --since.")

    print(f"# {len(hits)} log lines, {len({h[1] for h in hits})} services, ids followed: {', '.join(sorted(ids))}")
    seen = set()
    for ts, svc, msg in hits:
        key = (ts, svc, msg)
        if key in seen:
            continue
        seen.add(key)
        lvl, text = tidy(msg)
        if not args.full and len(text) > 420:
            text = text[:420] + " ...(use --full)"
        print(f"{ts[11:23]}  {svc:<24} {lvl:<5}  {text}")


if __name__ == "__main__":
    main()
