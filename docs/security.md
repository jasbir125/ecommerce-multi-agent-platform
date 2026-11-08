# Security

_Placeholder - filled in a later phase (see README roadmap)._

## Phase 7 addendum

- **Identity:** end users present an RS256 Bearer JWT issued by `auth-service` (JWKS at `/.well-known/jwks.json`, key rotation supported); identity comes from the token only. Trusted platform components use `X-Service-Token` plus identity headers. Internal-only endpoints reject end-user tokens.
- **Hardening:** body-size limits, unknown-route and malformed-JSON handling, startup security check (refuses insecure settings when `PLATFORM_ENV=production`), fault injection off by default and refused in production.
- **Verification:** `make eval-security` runs 81 live checks.
- **Residual risks:** demo users and the shared service token are for local use only; replace with an IdP and per-service credentials before production (see Phase 8 deployment notes).

## AWS deployment addendum (Phase 8)

Terraform (`infra/aws/terraform`) puts only the load balancer in public subnets, keeps data stores private behind a security group, stores every secret in Secrets Manager, gives only the agent host the Bedrock permissions, and sets `PLATFORM_ENV=production`. Residual risks are listed in `infra/aws/deployment.md` (plaintext Kafka and Redis inside the VPC, demo identity provider, HTTP unless a certificate is supplied).
