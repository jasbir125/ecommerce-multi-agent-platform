"""Shared dependencies handed to graph nodes through the LangGraph config."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import Settings
from app.evaluation.evaluator import RunEvaluator
from app.guardrails.tool_guardrails import ToolPolicy
from app.llm import ModelFactory, UsageTracker
from app.mcp.client import McpGateway
from app.models.workflow_state import WorkflowState
from app.services.workflow_client import WorkflowClient


@dataclass
class RunContext:
    """In-memory helpers for one workflow run (rebuilt after a restart; durable state lives in the checkpoint)."""
    gateway: McpGateway
    usage: UsageTracker
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    tool_policy: ToolPolicy | None = None


@dataclass
class Deps:
    settings: Settings
    models: ModelFactory
    workflows: WorkflowClient
    evaluator: RunEvaluator | None = None
    runs: dict[str, RunContext] = field(default_factory=dict)

    def ensure(self, workflow_id: str, customer_id: str, order_id: str) -> RunContext:
        """Create (once) the per-run helpers. The runner calls this first so the usage tracker
        can be attached to the graph config as a callback."""
        ctx = self.runs.get(workflow_id)
        if ctx is None:
            ctx = RunContext(
                gateway=McpGateway(self.settings, customer_id, workflow_id),
                usage=UsageTracker(self.settings.run_token_budget),
                tool_policy=ToolPolicy(order_id, self.settings.max_tool_calls_per_agent))
            self.runs[workflow_id] = ctx
        return ctx

    def run(self, state: WorkflowState) -> RunContext:
        return self.ensure(state.workflow_id, state.customer_id, state.order_id)

    def release(self, workflow_id: str) -> None:
        self.runs.pop(workflow_id, None)


def deps_of(config: RunnableConfig) -> Deps:
    return config["configurable"]["deps"]
