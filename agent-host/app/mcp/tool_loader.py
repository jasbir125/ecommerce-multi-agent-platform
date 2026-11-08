"""Helpers for turning MCP tool results into plain Python data."""
from __future__ import annotations

import json
from typing import Any


def parse_tool_result(result: Any) -> Any:
    """MCP tools return a list of content blocks; ours carry JSON in the first text block."""
    if isinstance(result, list):
        text = "".join(b.get("text", "") for b in result if isinstance(b, dict))
    else:
        text = str(result)
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}
