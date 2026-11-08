# Hallucination controls

Principle: the model may phrase and summarise; it may not invent policy, numbers, versions, statuses or results.

## Prevention
- Facts come from tools and services, never from model memory (orders, shipments, policy, stock, approvals).
- Business rules are deterministic code in Spring services; the policy-service `validate` endpoint decides refund eligibility.
- Structured output for every decision, temperature 0, per-agent models.
- Policy answers are **retrieved, reranked, cited and validated** before anyone sees them (`agent-host/app/rag`).
- Context is minimal and budgeted; whole documents never reach the model.
- Policy versions are explicit; cached answers are keyed on the policy corpus version.

## Detection (RAG answers, `rag/grounding_validator.py`)
Every statement the model writes must cite numbered passages, and is checked in code:

| Code | Meaning |
|---|---|
| `CITATION_MISSING` / `CITATION_INVALID` | a claim with no source, or a source number that does not exist |
| `FABRICATED_NUMBER` | a number, amount or duration (digits or words) not present in the cited passages |
| `FABRICATED_VERSION` | a policy version that is not the cited one |
| `UNSUPPORTED_CLAIM` | too little of the statement's content appears in the cited passages |
| `CONTRADICTION` | the statement and its best-matching passage disagree in polarity (can / cannot) |
| `INSTRUCTION_LIKE_TEXT` | the statement looks like an instruction (injection echo) |
| `EMPTY_STATEMENT` / `NO_STATEMENTS` | nothing to check |

Other detectors: invalid action enum, refund above the order amount, fabricated ids or unverified refund/replacement claims in customer
messages (`guardrails/output_guardrails.py`), low retrieval confidence, conflicting regional/global policies, prompt injection in questions,
retrieved text and tool output.

## Response
1. One failing statement fails the answer. The model gets the reasons and one retry.
2. Still failing, or the model errors: **extractive fallback**, sentences copied word for word from the evidence, validated the same way.
3. Nothing passes: *"I could not verify this from the available policy information. Please contact support."* with `insufficientEvidence: true`.
4. In a workflow, insufficient or ungrounded policy evidence means the decision step **escalates** to a human; no refund is proposed.
5. Failures are written to the audit trail (`GUARDRAIL_OUTPUT_REJECTED`, `PROMPT_INJECTION_SUSPECTED_IN_POLICY`).

## Prompt injection in retrieved text
Flagged at ingestion (stored, never returned), withheld by policy-service, scanned again in agent-host, wrapped as data in the prompt, and any statement echoing instructions is rejected. Tested with a poisoned document in `evaluation/rag/run_answer_eval.py`.

## Limits
The validator is lexical and numeric, not a full entailment model. It catches fabricated numbers/versions, missing citations, off-topic claims and
polarity flips reliably; a subtle paraphrase that changes meaning without changing words can pass. An LLM judge for borderline statements is a possible extension.
