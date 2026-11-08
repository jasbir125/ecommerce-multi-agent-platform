# Evaluation

## RAG (Phase 5)
Datasets (`evaluation/rag/`): `golden_set.json` (42: 32 answerable with expected policy/section/facts, 10 unanswerable; regression suite, retrieval was tuned
on it), `golden_holdout.json` (13 unseen questions, never used for tuning), `golden_adversarial.json` (8: leading questions, false premises, injection,
prompt-extraction, number bait, fake policy).

```bash
python evaluation/rag/run_retrieval_eval.py [--set golden_holdout]     # retrieval only, no model; gates on the golden set
python evaluation/rag/run_answer_eval.py    [--set golden_holdout]     # full pipeline with real model calls + adversarial + poisoned document
python evaluation/rag/ingestion_lifecycle_check.py                     # ingest, dedup, incremental, cache invalidation, HTML, poisoning, archive
```
Reports are written to `evaluation/reports/`. Quality gates make the scripts exit 1 (usable in CI).

| Measure | Golden | Hold-out |
|---|---|---|
| Retrieval hit@1 / hit@3 / hit@6 / MRR (service only) | 0.875 / 0.906 / 0.969 / 0.891 | 0.90 / 0.90 / 0.90 / 0.90 |
| Answered (of answerable) | 32/32 | 10/10 |
| Cited the right policy section | 100% | 100% |
| Expected facts in the answer | 100% | 100% |
| Answers grounded / fabricated numbers | 100% / 0 | 100% / 0 |
| Abstained on unanswerable | 10/10 | 3/3 |
| Adversarial cases passed | 8/8 | — |
| Poisoned document ignored | yes | — |
| Latency p50 / p95 (uncached) | 2.8 s / 3.6 s | 2.7 s / 3.5 s |
| Tokens / cost per question | ~1,030 / $0.0002 | ~1,140 / $0.0002 |

Reading the numbers: the service-only retrieval layer alone is not enough (it abstained on one answerable hold-out question and answered one unanswerable one).
The full pipeline fixed both, because query rewriting rescued the first and the model reranker rejected the second. Treat the hold-out set as the honest estimate;
13 questions is small, so add cases as real questions arrive.

## Workflow and guardrails
Unit tests: agent-host 106, policy-service 44, MCP servers 39, Streamlit UI 48 (headless renders with `AppTest`). Live scenario scripts: `agent-host/scripts/e2e_demo.py`, `mcp-servers/scripts/smoke_test.py`, `streamlit-ui/scripts/live_smoke.py` (drives the real UI through request, approval, resume, inspection and admin).
The 14-scenario workflow datasets listed in `evaluation/golden-datasets/` are filled in Phase 7.
