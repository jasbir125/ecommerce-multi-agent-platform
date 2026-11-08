# Bedrock Guardrails

Created by `terraform/bedrock.tf` (`enable_guardrail = true`), versioned with `aws_bedrock_guardrail_version`, attached to every model call of the agent host.

| Control | Setting |
|---|---|
| Content filters | Hate, sexual, violence, misconduct: HIGH; insults: MEDIUM |
| Prompt attacks | HIGH on input (output filtering does not apply to this type) |
| PII | E-mail, phone, address anonymised; card number, password, AWS secret key blocked |
| Denied topics | Approval bypass (skip/pre-empt manager approval or go outside policy); legal advice |
| Grounding / relevance | Thresholds 0.75 / 0.5. These apply only to calls that supply a grounding source; ordinary agent calls are unaffected |
| Blocked messages | Neutral text that tells the customer a support agent will follow up |

Layering: the Bedrock guardrail is an additional filter. The platform's own guardrails (input validation, tool allow-lists, policy citation checks, output claims checks, approval gating; see `docs/guardrails.md`) stay on and are the ones covered by the evaluation datasets. Anonymised PII means the model sees masked values: check a few runs after enabling it and loosen a rule if an agent starts failing to find an order (order and customer ids are not PII types and are not touched).

Rollout: start with the DRAFT guardrail in a test environment, review the traces (`trace = enabled` is set), then pin the numbered version (done by Terraform).
