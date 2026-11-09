"""Test doubles shared by the agent tests: a gateway, a workflow client, a model factory and a ReAct stand-in.

Nothing here talks to a network, a database, Kafka or a real model, so every agent branch can be exercised.
"""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.graph.deps import Deps
from app.models.order import OrderSnapshot
from app.models.shipment import ShipmentSnapshot
from app.models.workflow_state import WorkflowState


class FakeGateway:
    """MCP gateway double. `responses[tool]` is a value, an Exception to raise, or a callable(args)."""

    def __init__(self, responses: dict[str, Any] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict) -> Any:
        self.calls.append((name, args))
        outcome = self.responses.get(name)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome(args) if callable(outcome) else outcome

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeWorkflows:
    """Workflow-service double: records audit events."""

    def __init__(self):
        self.audits: list[tuple[str, str]] = []

    async def audit(self, workflow_id, agent, event, payload, customer_id=None):
        self.audits.append((agent, event))

    def events(self) -> list[str]:
        return [event for _, event in self.audits]


class FakeModels:
    """Model factory double. `results` is consumed one per call; an Exception in it is raised."""

    def __init__(self, *results: Any):
        self.results = list(results)
        self.prompts: list[str] = []

    async def structured(self, agent, schema, messages, config=None):
        self.prompts.append(messages[-1].content)
        outcome = self.results.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_deps(gateway: FakeGateway | None = None, models: FakeModels | None = None, **settings: Any) -> tuple[Deps, FakeWorkflows]:
    workflows = FakeWorkflows()
    deps = Deps(Settings(**settings), models=models, workflows=workflows)
    deps.ensure("WF-1", "CUST-1001", "ORD-7842").gateway = gateway or FakeGateway()
    return deps, workflows


def order(amount: Any = 129.99, quantity: int | None = 1, product_id: str | None = "PRD-100", currency: str = "USD",
          payment: str = "CAPTURED") -> OrderSnapshot:
    return OrderSnapshot(order_id="ORD-7842", status="SHIPPED", payment_status=payment, total_amount=amount, quantity=quantity,
                         product_id=product_id, product_name="Headphones", currency=currency)


def shipment(exception: str = "LOST", verified: bool = True, delay: int = 5) -> ShipmentSnapshot:
    return ShipmentSnapshot(order_id="ORD-7842", verified=verified, status=exception, delay_days=delay, exception_type=exception)


def make_state(**overrides: Any) -> WorkflowState:
    fields: dict[str, Any] = {"workflow_id": "WF-1", "request_id": "REQ-1", "customer_id": "CUST-1001", "order_id": "ORD-7842",
                              "customer_message": "My order is late, please refund me."}
    return WorkflowState(**{**fields, **overrides})


def patch_react(monkeypatch, module, *, tool_calls: list[dict] | None = None, structured: Any = None, error: BaseException | None = None,
                summary: str = "agent summary") -> None:
    """Replace the ReAct runner of an agent module. It can log tool calls into ctx.tool_log, return a structured answer, or raise."""
    async def fake_react(deps, ctx, agent, system, prompt, config, response_format=None, extra_tools=None):
        if error is not None:
            raise error
        for call in tool_calls or []:
            ctx.tool_log.append({"agent": agent, **call})
        return summary, structured
    monkeypatch.setattr(module, "run_react", fake_react)
