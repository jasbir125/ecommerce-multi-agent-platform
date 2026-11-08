"""Runs the golden datasets and reports, per case, whether every expectation held.

    python -m evaluation.evaluators.run_golden                      # everything (needs the stack for live cases)
    python -m evaluation.evaluators.run_golden --mode component     # only in-process cases (no stack, no model)
    python -m evaluation.evaluators.run_golden --scenario partial-failure --verbose

Modes: component (real graph, scripted model), live (real stack), api / mcp (direct service probes), rag (policy assistant).
Fault cases need the services started with PLATFORM_FAULTS_ENABLED=true (`make eval-golden` does this).
Exit code 1 if any check fails. Reports: evaluation/reports/golden_report.json and golden_report.md.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evaluation.evaluators.checks import Observed, evaluate  # noqa: E402

DATASETS = ROOT / "evaluation" / "golden-datasets"
REPORTS = ROOT / "evaluation" / "reports"


def load(scenario: str | None) -> list[dict]:
    out = []
    for f in sorted(DATASETS.glob("*.json")):
        d = json.loads(f.read_text())
        if scenario and d["scenario"] != scenario:
            continue
        for c in d["cases"]:
            out.append({**c, "scenario": d["scenario"]})
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario")
    ap.add_argument("--case")
    ap.add_argument("--mode", default="all", help="all | component | live | api | mcp | rag")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cases = [c for c in load(args.scenario) if (not args.case or c["id"] == args.case) and (args.mode == "all" or c["mode"] == args.mode)]
    if not cases:
        print("no cases selected")
        return 1
    live = None
    if any(c["mode"] != "component" for c in cases):
        from evaluation.evaluators.live_runner import LiveEnv
        live = LiveEnv()
        await live.prepare()
    from evaluation.evaluators.component_runner import run_component

    results = []
    for c in cases:
        t0 = time.time()
        try:
            obs = await (run_component(c) if c["mode"] == "component" else live.run(c))
            checks = evaluate(c, obs)
            error = None
        except Exception as e:  # noqa: BLE001
            obs, checks, error = Observed(), [], f"{type(e).__name__}: {e}"
        failed = [x for x in checks if not x[1]]
        ok = not failed and error is None
        results.append({"scenario": c["scenario"], "id": c["id"], "mode": c["mode"], "passed": ok, "seconds": round(time.time() - t0, 1), "error": error,
                        "checks": [{"name": n, "ok": k, "detail": d} for n, k, d in checks], "final_status": obs.final_status, "message": obs.message[:200]})
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {c['scenario']:<28} {c['id']:<34} [{c['mode']:<9}] {len(checks) - len(failed)}/{len(checks)} checks  {results[-1]['seconds']}s")
        if error:
            print(f"      ERROR {error}")
        for n, _, d in failed:
            print(f"      x {n}" + (f"  ({d})" if d else ""))
        if args.verbose and ok:
            for n, _, d in checks:
                print(f"      + {n}")
    if live:
        await live.close()

    passed = sum(r["passed"] for r in results)
    total_checks = sum(len(r["checks"]) for r in results)
    ok_checks = sum(x["ok"] for r in results for x in r["checks"])
    print(f"\n{passed}/{len(results)} cases passed, {ok_checks}/{total_checks} checks")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "golden_report.json").write_text(json.dumps({"cases": results, "passed": passed, "total": len(results)}, indent=1))
    md = ["# Golden dataset report", "", f"{passed}/{len(results)} cases passed ({ok_checks}/{total_checks} checks)", "",
          "| scenario | case | mode | result | final status | checks |", "|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['scenario']} | {r['id']} | {r['mode']} | {'PASS' if r['passed'] else 'FAIL'} | {r['final_status'] or '-'} | {sum(x['ok'] for x in r['checks'])}/{len(r['checks'])} |")
    (REPORTS / "golden_report.md").write_text("\n".join(md) + "\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
