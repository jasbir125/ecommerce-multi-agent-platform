# Security

Identity model (demo): every call between components carries `X-Service-Token` (a shared secret, checked in constant time) plus
`X-Customer-Id` and `X-Roles` for the customer and roles it acts for. The Streamlit UI plays the trusted front end and sets these headers
for the persona chosen in the sidebar. Services enforce the rest themselves:

- **Ownership:** a customer can only read or change their own orders, shipments and workflows (403 otherwise, and an audit event).
- **Roles:** approving needs MANAGER/APPROVER, workflow control needs OPERATOR/ADMIN/AGENT, policy ingestion needs ADMIN.
- **Agents are read-only:** the LLM agents can only call read tools; every write goes through deterministic code that re-reads the approval first, and the Spring services re-check approval, amounts and idempotency keys.
- **Untrusted text:** customer messages and retrieved policy text are data, never instructions; suspicious text is withheld and audited.
- **Logs:** passwords, tokens, keys, e-mails and phone numbers are masked before they are written (see `logging.md`).

Not in scope for the demo (the spec's architecture shows an API gateway with OAuth2/OIDC/JWT in front): real user authentication.
Anyone holding the service token can claim any customer or role, so the token must stay secret and the services must sit behind a
gateway that authenticates users before setting these headers.

## AWS deployment

Terraform (`infra/aws/terraform`) puts only the load balancer in public subnets, keeps data stores private behind a security group, stores every secret in Secrets Manager, and gives only the agent host the Bedrock permissions. Residual risks are listed in `infra/aws/deployment.md` (plaintext Kafka and Redis inside the VPC, no user authentication, HTTP unless a certificate is supplied).
