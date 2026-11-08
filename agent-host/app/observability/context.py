"""Task-local context shared by the tracing wrapper and the MCP gateway."""
from contextvars import ContextVar

# Name of the graph step the current task is running (each parallel step runs in its own task).
CURRENT_NODE: ContextVar[str | None] = ContextVar("current_node", default=None)
