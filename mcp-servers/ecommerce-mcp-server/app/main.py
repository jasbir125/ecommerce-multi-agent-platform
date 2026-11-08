"""ecommerce-mcp-server: controlled MCP access to the Spring Boot ecommerce services."""
import logging

from fastmcp import FastMCP
from mcp_common import logsetup, observability
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from app.config import Settings, get_settings
from app.deps import Deps
from app.prompts import ecommerce_prompts
from app.resources import ecommerce_resources
from app.security import TokenAuthMiddleware
from app.tools import read_tools, write_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

INSTRUCTIONS = ("Tools for investigating and recovering delayed ecommerce orders. Identity comes from the "
                "authenticated session, not from arguments. Write tools only request actions; the domain "
                "services decide. Never claim an action succeeded without a successful result.")


def create_server(settings: Settings | None = None, deps: Deps | None = None) -> FastMCP:
    settings = settings or get_settings()
    deps = deps or Deps.build(settings)
    mcp = FastMCP("ecommerce-mcp-server", middleware=[observability.ObservabilityMiddleware("ecommerce-mcp-server")], instructions=INSTRUCTIONS)
    read_tools.register(mcp, deps)
    write_tools.register(mcp, deps)
    ecommerce_resources.register(mcp, deps)
    ecommerce_prompts.register(mcp, deps)

    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics(request):
        return observability.metrics_response(request)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request):
        return JSONResponse({"status": "ok"})

    return mcp


if __name__ == "__main__":
    s = get_settings()
    logsetup.setup_logging("ecommerce-mcp-server")
    observability.setup("ecommerce-mcp-server")
    create_server(s).run(
        transport="http", host="0.0.0.0", port=s.ecommerce_mcp_port, path="/mcp",
        middleware=[Middleware(TokenAuthMiddleware, token=s.mcp_service_token, exempt_paths=("/health", "/metrics"))])
