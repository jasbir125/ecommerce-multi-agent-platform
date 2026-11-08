"""Retrieval quality evaluation for the policy-service (no LLM involved).

    python evaluation/rag/run_retrieval_eval.py [--k 3] [--mode hybrid|vector|keyword] [--out report.json]

Metrics (answerable questions): hit@1, hit@k, MRR, fact recall in the returned evidence, false-abstain rate.
Metrics (unanswerable questions): abstention rate. Also: latency p50/p95 and the effect of the query cache.
Exit code 1 if a quality gate fails, so this can run in CI.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
HEADERS = {"X-Service-Token": "local-demo-token", "Content-Type": "application/json"}
GATES = {"hit@3": 0.90, "hit@6": 0.95, "mrr": 0.75, "fact_recall": 0.85, "abstention": 0.90, "false_abstain": 0.10}

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:8086")
ap.add_argument("--k", type=int, default=3)
ap.add_argument("--mode", default="hybrid")
ap.add_argument("--set", default="golden_set", help="golden_set (regression suite) or golden_holdout (unseen questions)")
ap.add_argument("--out", default=None)
ap.add_argument("--verbose", action="store_true")
args = ap.parse_args()
args.out = args.out or str(ROOT.parent / "reports" / f"rag_retrieval_{args.set}.json")

cases = json.loads((ROOT / f"{args.set}.json").read_text())["cases"]
client = httpx.Client(base_url=args.url, headers=HEADERS, timeout=30)


def search(case, k):
    body = {"query": case["query"], "topK": k, "mode": args.mode, **case["filters"]}
    t0 = time.perf_counter()
    r = client.post("/api/policies/search", json=body)
    r.raise_for_status()
    return r.json(), (time.perf_counter() - t0) * 1000


def matches(src, expected):
    return src["policyKey"] == expected["policyKey"] and ("section" not in expected or src["section"] == expected["section"])


rows, lat = [], []
for c in cases:
    res, ms = search(c, args.k)
    lat.append(ms)
    srcs = res["sources"]
    row = {"raw": res["diagnostics"]["timings"].get("rawConfidence", res["confidence"]), "id": c["id"], "category": c["category"], "answerable": c["answerable"], "abstained": res["insufficientEvidence"],
           "confidence": res["confidence"], "latency_ms": round(ms), "top": [f'{s["policyKey"]}/{s["section"]}' for s in srcs]}
    if c["answerable"]:
        ranks = [i + 1 for i, s in enumerate(srcs) if any(matches(s, e) for e in c["expect"]["any_of"])]
        row["rank"] = ranks[0] if ranks else None
        evidence = " ".join(s.get("text") or "" for s in srcs).lower()
        row["facts_found"] = [f for f in c["expect"]["facts"] if f.lower() in evidence]
        row["facts_missing"] = [f for f in c["expect"]["facts"] if f.lower() not in evidence]
    rows.append(row)

ans = [r for r in rows if r["answerable"]]
una = [r for r in rows if not r["answerable"]]
ranked = [r for r in ans if not r["abstained"]]
metrics = {
    "cases": len(rows), "answerable": len(ans), "unanswerable": len(una), "mode": args.mode, "k": args.k,
    "hit@1": sum(r["rank"] == 1 for r in ans) / len(ans),
    f"hit@{args.k}": sum(r["rank"] is not None for r in ans) / len(ans),
    "mrr": sum(1 / r["rank"] for r in ans if r["rank"]) / len(ans),
    "fact_recall": sum(not r["facts_missing"] for r in ans) / len(ans),
    "false_abstain": sum(r["abstained"] for r in ans) / len(ans),
    "abstention": sum(r["abstained"] for r in una) / len(una),
    "latency_p50_ms": round(statistics.median(lat)), "latency_p95_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1]),
}
metrics["hit@3"] = metrics.get("hit@3", metrics[f"hit@{args.k}"])
# Recall of a wider candidate set: this is what the agent-host reranker works from.
wide = 0
for c in ans_cases if (ans_cases := [c for c in cases if c["answerable"]]) else []:
    res, _ = search(c, 6)
    wide += any(any(matches(s, e) for e in c["expect"]["any_of"]) for s in res["sources"])
metrics["hit@6"] = wide / len(ans)
# cache effect: repeat the whole set
rep = []
for c in cases:
    _, ms = search(c, args.k)
    rep.append(ms)
metrics["cached_latency_p50_ms"] = round(statistics.median(rep))

conf_ok = [r["raw"] for r in ans if r["rank"]]
conf_bad = [r["raw"] for r in una]
metrics["raw_conf_answerable_min"] = round(min(conf_ok), 2) if conf_ok else None
metrics["raw_conf_unanswerable_max"] = round(max(conf_bad), 2) if conf_bad else None
# best abstention threshold on this set: maximise (answered-correctly + abstained-correctly)
best = max(((sum(r["raw"] >= t for r in ans if r["rank"]) + sum(r["raw"] < t for r in una), t) for t in [x / 100 for x in range(10, 70, 1)]))
metrics["best_threshold_on_this_set"] = best[1]

print(f"\n{'metric':<30}{'value':>10}")
for k, v in metrics.items():
    print(f"{k:<30}{(round(v, 3) if isinstance(v, float) else v)!s:>10}")
if args.verbose or "--verbose" in sys.argv:
    pass
print("\nproblems:")
for r in rows:
    if r["answerable"] and (r["abstained"] or r["rank"] is None or r["rank"] > 1 or r["facts_missing"]):
        print(f'  [{r["id"]}] rank={r["rank"]} abstained={r["abstained"]} conf={r["confidence"]} missing={r["facts_missing"]} top={r["top"][:2]}')
    if not r["answerable"] and not r["abstained"]:
        print(f'  [{r["id"]}] should abstain but answered conf={r["confidence"]} top={r["top"][:2]}')

Path(args.out).parent.mkdir(parents=True, exist_ok=True)
Path(args.out).write_text(json.dumps({"metrics": metrics, "rows": rows}, indent=1))
failed = {} if args.set != "golden_set" else {g: metrics[g] for g, t in GATES.items() if (metrics[g] < t if g not in ("false_abstain",) else metrics[g] > t)}
print("\nquality gates:", "n/a (informational for this set)" if args.set != "golden_set" else "PASS" if not failed else f"FAIL {failed}")
sys.exit(1 if failed else 0)
