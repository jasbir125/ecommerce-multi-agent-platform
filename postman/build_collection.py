"""Generates postman/ecommerce-platform.postman_collection.json and the local environment. Run: python3 postman/build_collection.py"""
import json, pathlib, uuid

HERE = pathlib.Path(__file__).parent
JSON = [{"key": "Content-Type", "value": "application/json"}]


def req(name, method, svc, path, body=None, query=None, auth=None, headers=None, tests=None, desc=""):
    url = {"raw": "{{%s}}%s" % (svc, path), "host": ["{{%s}}" % svc], "path": [p for p in path.split("?")[0].strip("/").split("/") if p]}
    if query:
        url["query"] = [{"key": k, "value": v, "disabled": dis} for k, v, dis in query]
    r = {"method": method, "header": list(headers or []), "url": url, "description": desc}
    if body is not None:
        r["header"] = r["header"] + JSON
        r["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    if auth == "none":
        r["auth"] = {"type": "noauth"}
    elif auth:
        r["auth"] = {"type": "bearer", "bearer": [{"key": "token", "value": "{{token_%s}}" % auth, "type": "string"}]}
    item = {"name": name, "request": r}
    if tests:
        item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": tests.split("\n")}}]
    return item


def folder(name, items, desc=""):
    return {"name": name, "description": desc, "item": items}


def setvar(var, expr):
    return f"pm.test('status 2xx', () => pm.expect(pm.response.code).to.be.within(200, 299));\nconst j = pm.response.json();\nconst v = {expr};\nif (v !== undefined) pm.collectionVariables.set('{var}', v);"


def login(user, extra=""):
    return req(f"Login as {user}", "POST", "authUrl", "/api/auth/token", {"username": user, "password": "{{demoPassword}}"}, auth="none",
               tests="pm.test('status 200', () => pm.response.to.have.status(200));\nconst t = pm.response.json().accessToken;\n"
                     f"pm.collectionVariables.set('token_{user}', t);{extra}",
               desc=f"Issues an RS256 JWT for {user} and stores it as {{{{token_{user}}}}}.")



S = lambda c: [c]
items = []

items.append(folder("00 Auth (auth-service :8093)", [
    login("alice"), login("bob"), login("olivia"), login("manny"), login("admin"),
    req("JWKS (public keys)", "GET", "authUrl", "/.well-known/jwks.json", auth="none"),
    req("Login - wrong password (expect 401)", "POST", "authUrl", "/api/auth/token", {"username": "alice", "password": "wrong"}, auth="none",
        tests="pm.test('rejected', () => pm.expect(pm.response.code).to.be.oneOf([401, 429]));"),
], "Run the five logins first (each stores token_<user>; every request below states which user it uses). Customers (alice=CUST-1001, bob=CUST-2002), operator (olivia), manager (manny), admin."))

items.append(folder("01 Inventory (:8084)", [
    req("Search products", "GET", "inventoryUrl", "/api/catalog/products/search", query=[("q", "headphones", False), ("category", "", True), ("maxPrice", "150", True), ("inStockOnly", "true", False)]),
    req("Get product stock", "GET", "inventoryUrl", "/api/inventory/{{productId}}"),
    req("Reserve stock (internal)", "POST", "inventoryUrl", "/api/inventory/reservations",
        {"workflowId": "{{svcWorkflowId}}", "productId": "{{productId}}", "quantity": 1, "idempotencyKey": "{{$guid}}"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], tests=setvar("reservationId", "j.reservationId")),
    req("Get reservation (internal)", "GET", "inventoryUrl", "/api/inventory/reservations/{{reservationId}}", auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}]),
    req("Reserve stock again (to release)", "POST", "inventoryUrl", "/api/inventory/reservations",
        {"workflowId": "{{svcWorkflowId}}-X", "productId": "{{productId}}", "quantity": 1, "idempotencyKey": "{{$guid}}"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], tests=setvar("releaseReservationId", "j.reservationId")),
    req("Release reservation (internal)", "POST", "inventoryUrl", "/api/inventory/reservations/{{releaseReservationId}}/release",
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], desc="Idempotent."),
]))

items.append(folder("02 Approval (:8085)", [
    req("Create approval request (internal)", "POST", "approvalUrl", "/api/approvals",
        {"workflowId": "{{svcWorkflowId}}", "requestedAction": "REFUND", "amount": 129.99, "reason": "Shipment lost", "riskLevel": "MEDIUM"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], tests=setvar("approvalId", "j.approvalId")),
    req("List approvals (status filter)", "GET", "approvalUrl", "/api/approvals", auth="manny", query=[("status", "PENDING", False)], desc="Use the manager token (manny)."),
    req("Get approval", "GET", "approvalUrl", "/api/approvals/{{approvalId}}", auth="manny"),
    req("Approve (manager)", "POST", "approvalUrl", "/api/approvals/{{approvalId}}/decision", {"decision": "APPROVED", "decidedBy": "manny", "comment": "Evidence checked"}, auth="manny", desc="Manager (manny) only."),
    req("Create second approval (to reject)", "POST", "approvalUrl", "/api/approvals", {"workflowId": "{{svcWorkflowId}}-R", "requestedAction": "REFUND", "amount": 10, "reason": "demo", "riskLevel": "LOW"}, auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], tests=setvar("rejectApprovalId", "j.approvalId")),
    req("Reject (manager)", "POST", "approvalUrl", "/api/approvals/{{rejectApprovalId}}/decision", {"decision": "REJECTED", "decidedBy": "manny", "comment": "Needs manual review"}, auth="manny"),
]))

items.append(folder("03 Payment (:8083)", [
    req("Initiate refund (internal)", "POST", "paymentUrl", "/api/payments/refunds",
        {"workflowId": "{{svcWorkflowId}}", "orderId": "{{orderId}}", "amount": 129.99, "approvalId": "{{approvalId}}", "idempotencyKey": "{{$guid}}"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}],
        tests=setvar("refundId", "j.refundId"), desc="Requires an APPROVED, unexpired approval for the same workflow and amount."),
    req("Get refund", "GET", "paymentUrl", "/api/payments/refunds/{{refundId}}"),
]))

items.append(folder("04 Order (:8081)", [
    req("Get order", "GET", "orderUrl", "/api/orders/{{orderId}}"),
    req("List customer orders", "GET", "orderUrl", "/api/customers/{{customerId}}/orders"),
    req("Get order owner (internal only)", "GET", "orderUrl", "/api/orders/{{orderId}}/owner", auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], desc="Internal endpoint: rejects end-user tokens; send the service token."),
    req("Cancel order", "POST", "orderUrl", "/api/orders/{{orderId}}/cancel", tests="pm.test('shipped orders cannot be cancelled (409)', () => pm.response.to.have.status(409));", desc="Expected: shipped orders return 409."),
    req("Create replacement order (internal)", "POST", "orderUrl", "/api/orders/replacement",
        {"workflowId": "{{svcWorkflowId}}", "originalOrderId": "{{orderId}}", "reservationId": "{{reservationId}}", "idempotencyKey": "{{$guid}}"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}],
        desc="Needs a RESERVED reservation for the same workflow (run Inventory > Reserve first)."),
]))

items.append(folder("05 Shipment (:8082)", [
    req("Get shipment", "GET", "shipmentUrl", "/api/shipments/{{orderId}}"),
    req("Create carrier case (internal)", "POST", "shipmentUrl", "/api/shipments/{{orderId}}/carrier-case",
        {"workflowId": "{{svcWorkflowId}}", "reason": "No scan for 5 days"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], desc="Created once per shipment exception."),
]))

items.append(folder("06 Policy (:8086)", [
    req("Ingest policy (ADMIN)", "POST", "policyUrl", "/api/policies/ingest", {"policyKey": "demo-policy", "version": "1", "title": "Demo policy", "content": "# Demo\n\n## Gift cards\nGift cards are not refundable.", "contentType": "text/markdown", "policyType": "general"},
        query=[("wait", "true", False)], auth="admin", tests=setvar("policyId", "j.documentId") + "\npm.collectionVariables.set('jobId', j.jobId);", desc="Admin only."),
    req("Search policies (RAG)", "POST", "policyUrl", "/api/policies/search", {"query": "refund for a delayed shipment", "region": None, "policyType": "refund", "topK": 5, "debug": False}),
    req("Validate refund eligibility", "POST", "policyUrl", "/api/policies/validate", {"orderId": "{{orderId}}", "orderStatus": "SHIPPED", "orderAmount": 129.99, "refundAmount": 129.99, "delayDays": 5, "shipmentStatus": "LOST", "region": None}),
    req("List policies", "GET", "policyUrl", "/api/policies"),
    req("Get policy by id", "GET", "policyUrl", "/api/policies/{{policyId}}"),
    req("Get policy by key + version", "GET", "policyUrl", "/api/policies/refund-policy/2"),
    req("Get ingestion job (ADMIN)", "GET", "policyUrl", "/api/policies/jobs/{{jobId}}", auth="admin"),
    req("Archive policy (ADMIN)", "POST", "policyUrl", "/api/policies/{{policyId}}/archive", auth="admin", desc="Archive the policy created by Ingest."),
]))

items.append(folder("07 Audit (:8087)", [
    req("Append audit event (internal)", "POST", "auditUrl", "/api/audit/events", {"workflowId": "{{svcWorkflowId}}", "agentName": "postman", "eventType": "MANUAL_NOTE", "payload": {"note": "hello"}, "actor": "postman", "correlationId": "{{$guid}}"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}]),
    req("Audit trail for workflow", "GET", "auditUrl", "/api/audit/workflows/{{svcWorkflowId}}", auth="olivia"),
]))

items.append(folder("08 Workflow (:8088)", [
    req("Create workflow (alice)", "POST", "workflowUrl", "/api/workflows", {"orderId": "{{orderId}}", "message": "My order {{orderId}} has not arrived and the tracking is stuck.", "requestId": "{{$guid}}"},
        tests=setvar("workflowId", "j.workflowId"), desc="Use alice's token. Customer id comes from the token."),
    req("List workflows", "GET", "workflowUrl", "/api/workflows", auth="olivia", query=[("status", "", True)], desc="Operator or manager token."),
    req("Get workflow", "GET", "workflowUrl", "/api/workflows/{{workflowId}}"),
    req("Workflow trace (operator)", "GET", "workflowUrl", "/api/workflows/{{workflowId}}/trace", auth="olivia", desc="Audit events and Kafka events; operator token."),
    req("Recent Kafka events", "GET", "workflowUrl", "/api/workflows/events/recent", auth="olivia", query=[("limit", "50", False), ("topic", "", True)]),
    req("Resume workflow (internal)", "POST", "workflowUrl", "/api/workflows/{{workflowId}}/resume", {"idempotencyKey": "{{$guid}}", "targetStatus": "ESCALATED", "statePatch": {}, "reason": "postman"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}, {"key": "X-Roles", "value": "OPERATOR"}]),
    req("AI classify intent", "POST", "workflowUrl", "/api/ai/classify", {"message": "My package is lost, I want my money back"}),
    req("AI recommend resolution", "POST", "workflowUrl", "/api/ai/recommend", {"workflowId": "{{workflowId}}", "orderId": "{{orderId}}", "orderAmount": 129.99, "refundEligible": True, "verifiedFacts": {"shipmentStatus": "LOST"}}),
]))

items.append(folder("09 Notification (:8089)", [
    req("Notify customer (internal)", "POST", "notificationUrl", "/api/notifications/customer", {"workflowId": "{{svcWorkflowId}}", "customerId": "{{customerId}}", "channel": "EMAIL", "template": "REFUND_INITIATED", "params": {"refundId": "{{refundId}}", "orderId": "{{orderId}}", "amount": "129.99"}},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}, {"key": "X-Customer-Id", "value": "{{customerId}}"}], desc="Result templates are verified against the owning service."),
    req("Notify internal team (internal)", "POST", "notificationUrl", "/api/notifications/internal", {"workflowId": "{{svcWorkflowId}}", "channel": "SLACK", "message": "Manual follow-up needed"},
        auth="none", headers=[{"key": "X-Service-Token", "value": "{{serviceToken}}"}]),
]))

items.append(folder("10 Agent host (:8092)", [
    req("Health", "GET", "agentUrl", "/health", auth="none"),
    req("Start run (usually not needed: creating a workflow starts the agent via Kafka)", "POST", "agentUrl", "/api/agent/runs", {"customerId": "{{customerId}}", "orderId": "{{walkOrderId}}", "message": "My order {{walkOrderId}} has not arrived.", "requestId": "{{$guid}}"},
        query=[("stream", "false", False)], tests=setvar("workflowId", "j.workflowId"), desc="Runs the supervisor graph; pauses at the approval. stream=true returns SSE."),
    req("Get run", "GET", "agentUrl", "/api/agent/runs/{{workflowId}}"),
    req("Run trace", "GET", "agentUrl", "/api/agent/runs/{{workflowId}}/trace"),
    req("Resume run (after approval)", "POST", "agentUrl", "/api/agent/runs/{{workflowId}}/resume", query=[("stream", "false", False)]),
    req("Admin metrics", "GET", "agentUrl", "/api/agent/admin/metrics", auth="olivia", query=[("hours", "24", False)], desc="Operator token."),
    req("Ask policy (RAG)", "POST", "agentUrl", "/api/agent/rag/ask", {"question": "How many days do I have to return an item?", "region": "EU"}),
    req("Prometheus metrics", "GET", "agentUrl", "/metrics", auth="none"),
]))

items.append(folder("11 MCP servers (:8090 ecommerce, :8091 policy)", [
    req("Ecommerce MCP health", "GET", "ecommerceMcpUrl", "/health", auth="none"),
    req("Policy MCP health", "GET", "policyMcpUrl", "/health", auth="none"),
    req("Ecommerce MCP metrics", "GET", "ecommerceMcpUrl", "/metrics", auth="none"),
    req("Policy MCP metrics", "GET", "policyMcpUrl", "/metrics", auth="none"),
], "The MCP endpoint itself (/mcp) speaks the MCP protocol (JSON-RPC over streamable HTTP) and needs an MCP client; only the plain HTTP routes are listed here."))

items.append(folder("12 Actuator health (all Spring services)", [
    req(f"{n} health", "GET", u, "/actuator/health", auth="none") for n, u in
    [("auth", "authUrl"), ("order", "orderUrl"), ("shipment", "shipmentUrl"), ("payment", "paymentUrl"), ("inventory", "inventoryUrl"),
     ("approval", "approvalUrl"), ("policy", "policyUrl"), ("audit", "auditUrl"), ("workflow", "workflowUrl"), ("notification", "notificationUrl")]]))

items.append(folder("99 End-to-end walk-through", [
    login("alice"), login("manny"),
    req("2. Start run (agent host)", "POST", "agentUrl", "/api/agent/runs", {"customerId": "CUST-1001", "orderId": "{{walkOrderId}}", "message": "My order {{walkOrderId}} has not arrived.", "requestId": "{{$guid}}"},
        tests=setvar("workflowId", "j.workflowId") + "\npm.test('paused for approval', () => pm.expect(j.waitingForApproval).to.eql(true));\nif (j.approvalId) pm.collectionVariables.set('approvalId', j.approvalId);"),
    req("4. Approve", "POST", "approvalUrl", "/api/approvals/{{approvalId}}/decision", {"decision": "APPROVED", "decidedBy": "manny", "comment": "ok"}, auth="manny"),
    req("5. Get run (repeat until status is COMPLETED)", "GET", "agentUrl", "/api/agent/runs/{{workflowId}}",
        tests="const j = pm.response.json();\npm.test('status ' + j.status, () => pm.expect(pm.response.code).to.eql(200));\nconsole.log(j.status, j.customerResponse);"),
    req("6. Audit trail", "GET", "auditUrl", "/api/audit/workflows/{{workflowId}}", auth="olivia"),
], "Log in, start a run as alice, approve as manny, then poll the run (the agent host resumes by itself through Kafka). Each order can be refunded once: run postman/new_order.sh and set the `orderId` variable to the id it prints."))

variables = {"orderUrl": "http://localhost:8081", "shipmentUrl": "http://localhost:8082", "paymentUrl": "http://localhost:8083", "inventoryUrl": "http://localhost:8084",
             "approvalUrl": "http://localhost:8085", "policyUrl": "http://localhost:8086", "auditUrl": "http://localhost:8087", "workflowUrl": "http://localhost:8088",
             "notificationUrl": "http://localhost:8089", "ecommerceMcpUrl": "http://localhost:8090", "policyMcpUrl": "http://localhost:8091", "agentUrl": "http://localhost:8092",
             "authUrl": "http://localhost:8093", "demoPassword": "demo-pass", "walkOrderId": "ORD-8802", "svcWorkflowId": "", "tokensAt": "", "rejectApprovalId": "", "serviceToken": "local-demo-token", "customerId": "CUST-1001", "orderId": "ORD-8801",
             "productId": "PRD-101", "accessToken": "", "workflowId": "", "approvalId": "", "reservationId": "", "refundId": "", "policyId": "", "jobId": ""}

collection = {
    "info": {"_postman_id": str(uuid.uuid4()), "name": "Ecommerce Multi-Agent Order Recovery Platform",
             "description": "Every REST endpoint of every service. 1) Run '00 Auth > Login as ...', 2) pick the active user, 3) run requests. "
                            "'internal' requests use X-Service-Token (trusted platform calls); end users use the Bearer JWT. Or run '99 End-to-end walk-through'.",
             "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
    "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token_alice}}", "type": "string"}]},
    "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": ["const cv = pm.collectionVariables;", "if (!cv.get('svcWorkflowId')) cv.set('svcWorkflowId', 'WF-PM' + Math.random().toString(16).slice(2, 10).toUpperCase());", "// Tokens live 15 minutes: log everyone in again when they are missing or older than 10 minutes.", "const age = Date.now() - Number(cv.get('tokensAt') || 0);", "if (pm.info.requestName.indexOf('Login') !== 0 && (age > 600000 || !cv.get('token_alice'))) {", "  cv.set('tokensAt', String(Date.now()));", "  ['alice', 'bob', 'olivia', 'manny', 'admin'].forEach(u => pm.sendRequest({", "    url: pm.variables.get('authUrl') + '/api/auth/token', method: 'POST', header: {'Content-Type': 'application/json'},", "    body: {mode: 'raw', raw: JSON.stringify({username: u, password: pm.variables.get('demoPassword')})}", "  }, (err, res) => { if (!err && res.code === 200) cv.set('token_' + u, res.json().accessToken); }));", "}"]}}],
    "variable": [{"key": k, "value": v} for k, v in variables.items()],
    "item": items,
}
(HERE / "ecommerce-platform.postman_collection.json").write_text(json.dumps(collection, indent=2))
env = {"id": str(uuid.uuid4()), "name": "Local (docker compose)", "values": [{"key": k, "value": v, "enabled": True} for k, v in variables.items() if k.endswith("Url")], "_postman_variable_scope": "environment"}
(HERE / "local.postman_environment.json").write_text(json.dumps(env, indent=2))
n = sum(len(f["item"]) for f in items)
print(f"{len(items)} folders, {n} requests")
