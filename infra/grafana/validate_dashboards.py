"""Runs every PromQL expression in the dashboards against a live Prometheus.

    python infra/grafana/validate_dashboards.py [--prom http://localhost:9090]

ERROR = the query is invalid or references nothing that can exist; fail the check.
EMPTY = valid query, no data yet (normal for rare events such as compensation or degraded searches).
Exit code 1 on any ERROR, or if fewer than --min-data of the panels have data.
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--prom", default="http://localhost:9090")
ap.add_argument("--min-data", type=float, default=0.75)
args = ap.parse_args()

rows, errors = [], []
for f in sorted((Path(__file__).parent / "dashboards").glob("*.json")):
    board = json.loads(f.read_text())
    for p in board["panels"]:
        for t in p.get("targets", []):
            r = httpx.get(f"{args.prom}/api/v1/query", params={"query": t["expr"]}, timeout=20)
            j = r.json()
            if j.get("status") != "success":
                errors.append((board["uid"], p["title"], j.get("error", r.text)[:120]))
                state = "ERROR"
            else:
                state = "DATA" if j["data"]["result"] else "EMPTY"
            rows.append((board["uid"], p["title"], state))

by_board: dict[str, dict[str, int]] = {}
for uid, _, s in rows:
    by_board.setdefault(uid, {"DATA": 0, "EMPTY": 0, "ERROR": 0})[s] += 1
for uid, c in by_board.items():
    print(f"{uid:<20} data={c['DATA']:<3} empty={c['EMPTY']:<3} error={c['ERROR']}")
print("\nno data yet:")
for uid, title, s in rows:
    if s == "EMPTY":
        print(f"  [{uid}] {title}")
for e in errors:
    print("ERROR", e)
data = sum(1 for *_, s in rows if s == "DATA")
print(f"\n{data}/{len(rows)} queries return data ({data / len(rows):.0%})")
sys.exit(1 if errors or data / len(rows) < args.min_data else 0)
