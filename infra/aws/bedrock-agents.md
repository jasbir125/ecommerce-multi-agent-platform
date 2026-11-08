# Amazon Bedrock

## How Bedrock is used today

| Use | Where | Status |
|---|---|---|
| Model provider | `agent-host`: `MODEL_PROVIDER=bedrock` (LangChain `ChatBedrockConverse`, credentials from the task role) | Implemented, unit-tested, **not run against live Bedrock** |
| Guardrails provider | `terraform/bedrock.tf` creates the guardrail; the agent host attaches it to every model call (`BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION`) | Implemented in Terraform and code |
| Knowledge Base | Alternative to the built-in pgvector RAG | Design only (`knowledge-base.md`) |
| Bedrock Agents / multi-agent collaboration | Alternative runtime for the agents | Design only (`multi-agent-collaboration.md`, `action-groups.md`) |

Run locally against Bedrock: set `MODEL_PROVIDER=bedrock`, `AWS_REGION`, `BEDROCK_MODEL_ID` and AWS credentials (profile or env vars) for the agent host. Other providers keep working as the fallback only when their API key is set.

## The platform's own agents versus Bedrock Agents

The platform's LangGraph supervisor is the default runtime because it provides things Bedrock Agents do not: deterministic write nodes that only run after the approval is re-read, a Postgres-checkpointed human-approval pause, a grounding validator for policy answers, and the evaluation harness. Bedrock is therefore used as the **model and guardrail layer**. Option C (Bedrock-managed agents) is documented for teams that want AWS to orchestrate; its trade-offs are listed in `multi-agent-collaboration.md`.

## Agent design for Option C

```
Supervisor agent (collaboration mode: SUPERVISOR)
  +-- Order collaborator     action group OrderActions
  +-- Shipment collaborator  action group ShipmentActions
  +-- Policy collaborator    action group PolicyActions + knowledge base
  +-- Product collaborator   action group CatalogActions
  +-- Risk collaborator      no write actions
```

Each agent needs: foundation model, instructions (reuse the prompts in `agent-host/app/agents/`), action groups, optional knowledge base, the guardrail from `terraform/bedrock.tf`, memory (session summary) and an alias bound to a numbered version. Write actions (`initiateRefund`, `createReplacementOrder`) must not be exposed to model-driven agents: keep them behind the approval step as in the LangGraph design (see `action-groups.md`).
