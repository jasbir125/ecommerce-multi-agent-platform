"""policy-mcp-server: FastMCP server exposing tools/resources/prompts. Real tools arrive in Phase 3."""
from fastmcp import FastMCP

mcp = FastMCP("policy-mcp-server")


@mcp.tool()
def ping() -> str:
    """Placeholder tool so the server starts and can be listed by MCP clients."""
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8091, path="/mcp")
