from mcp_common.security import RateLimiter, TokenAuthMiddleware

EXPECTED_TOOLS = {"get_order_status", "get_payment_status", "get_shipment_status", "search_products", "check_inventory",
                  "get_approval_status", "create_approval_request", "initiate_refund", "reserve_inventory",
                  "release_inventory", "create_replacement_order", "create_carrier_case", "notify_customer",
                  "create_support_task"}


async def run_asgi(app, path="/mcp", auth=None):
    sent = []
    headers = [(b"authorization", auth.encode())] if auth else []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)
    await app({"type": "http", "path": path, "headers": headers}, receive, send)
    return sent[0]["status"]


async def inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def test_middleware_rejects_missing_and_wrong_token():
    app = TokenAuthMiddleware(inner, token="secret-token")
    assert await run_asgi(app) == 401
    assert await run_asgi(app, auth="Bearer nope") == 401
    assert await run_asgi(app, auth="Bearer secret-token") == 200


async def test_health_is_open():
    assert await run_asgi(TokenAuthMiddleware(inner, token="t"), path="/health") == 200


def test_rate_limiter_blocks_after_max():
    rl = RateLimiter(2)
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a") and rl.allow("b")


async def test_exposes_expected_tools_resources_prompts(make_client):
    async with make_client() as c:
        assert {t.name for t in await c.list_tools()} == EXPECTED_TOOLS
        assert {p.name for p in await c.list_prompts()} >= {"delayed_shipment_investigation", "safe_action_rules"}
        assert "ecommerce://rules/business" in {str(r.uri) for r in await c.list_resources()}


async def test_read_tools_are_marked_read_only(make_client):
    async with make_client() as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert tools["get_order_status"].annotations.readOnlyHint is True
    assert tools["initiate_refund"].annotations.readOnlyHint is False
