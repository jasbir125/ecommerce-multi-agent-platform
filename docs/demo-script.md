# Demo script (UI)

Open http://localhost:8501 with the stack running (`make up`, key in `.env`). Use a fresh order each time (`ORD-8801` .. `ORD-8804`).

1. **Customer request.** As *Customer* (`CUST-1001`), keep the default text, set the order to `ORD-8801`, choose *Live (streamed)* and submit.
2. Watch the steps stream in: guardrails, supervisor, then order and shipment together, then policy, product and risk together, then the decision.
3. The run **pauses**: "Waiting for a manager to approve". Nothing has been executed.
4. Open the **Timeline** tab (overlapping bars = parallel agents) and **Agent trace** (supervisor plan, each MCP tool call, tokens and cost per agent).
5. Open **RAG evidence**: the cited policy (`refund-policy v2`), confidence, grounding status, all retrieved passages with their scores, which were shown to the model and which were cited.
6. Switch to *Manager*, open **Approvals**, expand "Evidence from the agents", add a comment and **Approve**.
7. Back in the workflow: the agent host resumes by itself (Kafka), executes the carrier case, refund, reservation and replacement order, and the customer message names the refund and replacement ids.
8. **Kafka events** tab: every event with correlation id and payload. **Audit trail**: the full append-only record.
9. **Rejection:** repeat with `ORD-8802`; as manager choose **Reject**. Nothing is executed and the customer is told it needs manual review.
10. **Policy that cannot answer:** *Policy assistant* -> ask "do you ship to Antarctica?" -> "could not verify", no business action.
11. **Region rule:** ask "how many days do I have to return an item" with region *EU* (14 days, the EU policy overrides) and without (30 days).
12. **Admin** (as *Operator*): cost and tokens per run, RAG abstention and cache rates, questions the policy could not answer, failures and compensation.

Still to demonstrate in later phases: prompt-injection and partial-failure datasets (Phase 7), Bedrock deployment (Phase 8).
