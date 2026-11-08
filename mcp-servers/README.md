# MCP servers

Two FastMCP servers that give agents controlled access to the Spring Boot services. They never touch the database; they call the services' REST APIs.

| Server | Port | Tools |
|---|---|---|
| `ecommerce-mcp-server` | 8090 | read: `get_order_status`, `get_payment_status`, `get_shipment_status`, `search_products`, `check_inventory`, `get_approval_status` / write: `create_approval_request`, `initiate_refund`, `reserve_inventory`, `release_inventory`, `create_replacement_order`, `create_carrier_case` |
| `policy-mcp-server` | 8091 | `search_policy`, `get_policy_version`, `validate_refund_eligibility` |

Plus resources (`ecommerce://orders/{id}`, `policy://{key}/{version}`, ...) and prompts.

## Security model
- Callers send `Authorization: Bearer <MCP_SERVICE_TOKEN>`. `/health` is open.
- The end customer comes from headers (`X-Customer-Id`, `X-Workflow-Id`, `X-Correlation-Id`, `X-Roles`), **never from tool arguments**, so a model cannot request another customer's data. The Spring services re-check ownership anyway.
- Write tools check: server write switch, identity, workflow id (must match `X-Workflow-Id`), idempotency key, amount/quantity limits, per-customer rate limit; then write an audit event.
- Text from catalogs and policies is scanned for injection phrases; flagged text is withheld and an audit event is written.
- Upstream failures become tool errors telling the agent not to guess.

## Run locally
```bash
uv venv --python 3.12 .venv && source .venv/bin/activate     # fastmcp needs Python 3.10+
uv pip install -r ecommerce-mcp-server/requirements-dev.txt
cd ecommerce-mcp-server && PYTHONPATH=..:. python -m app.main     # :8090/mcp
cd policy-mcp-server    && PYTHONPATH=..:. python -m app.main     # :8091/mcp
PYTHONPATH=..:. pytest                                            # run inside each server folder
```
