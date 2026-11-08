# Multi-agent collaboration (Option C)

Bedrock supervisor mode: a supervisor agent routes sub-tasks to collaborator agents and merges their answers. Mapping from this platform:

| Platform (LangGraph) | Bedrock |
|---|---|
| `supervisor` node plans which specialists run | Supervisor agent with collaborators |
| order / shipment agents run in parallel, then policy / product / risk | Parallel collaboration |
| Specialists are read-only ReAct agents | Collaborators with read-only action groups |
| Decision, approval pause and execution are deterministic nodes | Not an agent: run them in the workflow service / Step Functions after human approval |
| Per-run token budget and cost tracking | Agent traces + CloudWatch metrics; budgets need your own wrapper |
| Evaluation datasets | Re-point `evaluation/evaluators` at the Bedrock agent alias via a new runner |

Recommendation: keep the LangGraph runtime and Bedrock models/guardrails (Option A). Choose Option C only if the organisation requires AWS-managed orchestration; then keep decision, approval and money movement outside the model-driven agents, re-run the golden datasets (especially approval-rejection, prompt-injection, partial-failure) and compare before switching traffic.

Each Bedrock agent needs an alias bound to a numbered version; roll out by moving the alias, roll back by moving it back.
