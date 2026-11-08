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
| Answered (of answerable) | 31/32 | 9/10 |
| Cited the right policy section | 100% | 100% |
| Expected facts in the answer | 100% | 100% |
| Answers grounded / fabricated numbers | 100% / 0 | 100% / 0 |
| Abstained on unanswerable | 10/10 | 3/3 |
| Adversarial cases passed | 8/8 | — |
| Poisoned document ignored | yes | — |
| Latency p50 / p95 | 1.1 s / 1.7 s | 1.0 s / 1.3 s |
| Tokens / cost per question | ~430 / $0.00008 | ~470 / $0.00009 |

Reading the numbers: the pipeline retrieves once from policy-service (hybrid search + rerank) and uses one model call to write the answer. It never fabricated
a number and abstained on every unanswerable question. The price of having no model-based query rewrite is one paraphrased question on each set
("lost-paraphrase", "h-signoff") that now ends in "could not verify" and is escalated instead of being answered. Treat the hold-out set as the honest
estimate; 13 questions is small, so add cases as real questions arrive. (Measured with `make eval-rag` / `make eval-rag-holdout`, OpenAI embeddings and gpt-4o-mini.)

## Workflow and guardrails
Unit tests: agent-host 106, policy-service 44, MCP servers 39, Streamlit UI 48 (headless renders with `AppTest`). Live scenario scripts: `mcp-servers/scripts/smoke_test.py`, `streamlit-ui/scripts/live_smoke.py` (drives the real UI through request, approval, resume, inspection and admin).
The 14-scenario workflow datasets listed in `evaluation/golden-datasets/` are filled in Phase 7.

## Golden datasets

`evaluation/golden-datasets/*.json` holds the 14 scenarios from the spec. Each case states the input, expected intent, expected tools, approval requirement, final status, guardrail behaviour, RAG confidence and citations.

`make eval` (`evaluation/run_golden.py`) runs them against the running stack with real model calls: it creates a fresh order, submits the request, lets a manager approve or reject, then checks the dataset's expectations (final status, tools called / never called, audit events, customer message, database rows, who may see what). Cases in mode `component`, `api` or `mcp`, and the duplicate-Kafka-event case, are skipped by the runner and covered by the unit tests in `agent-host/tests` and the service tests.

The partial-failure case needs no special hook: the runner sets the replacement product's stock to 0 while the case waits for approval, so the refund succeeds and the replacement reservation fails. The runner reaches the database through `docker exec platform-postgres psql`, so the stack must run in Docker.
