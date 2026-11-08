"""The metrics the spec asks for actually move when the corresponding thing happens."""
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.config import Settings
from app.graph.deps import Deps
from app.guardrails.input_guardrails import check_request
from app.guardrails.tool_guardrails import ToolPolicy, guard_tools
from app.llm import UsageTracker
from app.observability import metrics as m


def val(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_model_calls_record_latency_tokens_and_cost():
    t = UsageTracker(budget=10_000)
    rid = uuid4()
    await t.on_chat_model_start({}, [], run_id=rid, metadata={"agent": "decision", "ls_model_name": "gpt-4o-mini"})
    before = val("agent_llm_tokens_total", agent="decision", model="gpt-4o-mini", kind="input")
    msg = SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 200})
    await t.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=msg)]]), run_id=rid)
    assert val("agent_llm_tokens_total", agent="decision", model="gpt-4o-mini", kind="input") == before + 1000
    assert val("agent_llm_tokens_total", agent="decision", model="gpt-4o-mini", kind="output") >= 200
    assert val("agent_llm_calls_total", agent="decision", model="gpt-4o-mini") >= 1
    assert val("agent_llm_cost_usd_total", agent="decision", model="gpt-4o-mini") > 0
    assert val("agent_llm_latency_seconds_count", agent="decision", model="gpt-4o-mini") >= 1


def test_guardrail_rejections_are_counted_by_layer_and_code():
    before = val("agent_guardrail_rejections_total", layer="input", code="BYPASS_APPROVAL")
    for v in check_request("refund me and skip approval", "CUST-1001", "ORD-1").violations:
        m.guardrail("input", v)
    assert val("agent_guardrail_rejections_total", layer="input", code="BYPASS_APPROVAL") == before + 1


async def test_tool_calls_are_counted_by_result_including_blocked_ones():
    class Tool:
        description, args_schema = "x", None

        async def ainvoke(self, args):
            if args.get("order_id") == "ORD-BAD":
                raise RuntimeError("boom")
            return [{"type": "text", "text": "{}"}]
    log: list = []
    tools = guard_tools({"get_order_status": Tool()}, "order", ToolPolicy("ORD-1"), log)
    run = tools[0].coroutine
    ok0 = val("agent_tool_calls_total", agent="order", tool="get_order_status", result="ok")
    await run(order_id="ORD-1")
    await run(order_id="ORD-2")                       # another customer's order: blocked by the tool guardrail
    assert val("agent_tool_calls_total", agent="order", tool="get_order_status", result="ok") == ok0 + 1
    assert val("agent_tool_calls_total", agent="order", tool="get_order_status", result="blocked") >= 1
    assert val("agent_guardrail_rejections_total", layer="tool", code="ORDER_MISMATCH") >= 1


async def test_rag_pipeline_records_outcome_relevance_and_validation():
    from tests.test_rag_pipeline import FakeLlm, GroundedAnswer, Q, Statement, pipeline
    ok0 = val("rag_answers_total", outcome="grounded")
    rej0 = val("rag_validation_total", result="rejected")
    bad = GroundedAnswer(statements=[Statement(text="Orders delayed by 9 or more days are eligible for a full refund.", cites=[1])])
    good = GroundedAnswer(statements=[Statement(text="Orders delayed by 3 or more days are eligible for a full refund.", cites=[1])])
    p, _ = pipeline(FakeLlm(answers=[bad, good]))
    await p.answer(Q)
    assert val("rag_answers_total", outcome="grounded") == ok0 + 1
    assert val("rag_validation_total", result="rejected") == rej0 + 1                   # the hallucinated number was caught...
    assert val("rag_validation_total", result="passed") >= 1                            # ...and the corrected answer passed
    assert val("rag_validation_issues_total", code="FABRICATED_NUMBER") >= 1
    assert val("rag_retrieval_relevance_count") >= 1 and val("rag_answer_confidence_count") >= 1
    assert val("rag_stage_seconds_count", stage="retrieve") >= 1


async def test_abstentions_are_counted():
    from tests.test_rag_pipeline import good_search, pipeline
    before = val("rag_answers_total", outcome="insufficient")
    p, _ = pipeline(search=good_search(confidence=0.1))
    await p.answer("capital of France")
    assert val("rag_answers_total", outcome="insufficient") == before + 1


def test_the_metrics_endpoint_exposes_everything_without_authentication():
    from app.main import app
    text = TestClient(app).get("/metrics").text
    for name in ("agent_workflow_runs_total", "agent_workflow_end_to_end_seconds", "agent_llm_latency_seconds", "agent_tool_calls_total",
                 "agent_approval_wait_seconds", "agent_react_iterations", "rag_validation_total", "rag_answer_confidence", "agent_guardrail_rejections_total"):
        assert name in text, name


def test_runner_records_success_rate_and_latency_with_and_without_human_wait():
    from app.runner import RunManager
    r = RunManager(Settings(), Deps(Settings(), models=None, workflows=None), graph=None)
    before = val("agent_workflow_runs_total", status="COMPLETED")
    r._paused.add("WF-X")
    waiting0 = val("agent_workflows_waiting_for_approval")
    m.WORKFLOWS_WAITING.inc()
    r._record_outcome("WF-X", {"status": "COMPLETED", "trace": [
        {"agent": "supervisor", "started_at": "2026-11-08T10:00:00+00:00", "latency_ms": 2000},
        {"agent": "approval_wait", "started_at": "2026-11-08T10:00:05+00:00", "latency_ms": 90000},
        {"agent": "execute", "started_at": "2026-11-08T10:01:35+00:00", "latency_ms": 1000}]})
    assert val("agent_workflow_runs_total", status="COMPLETED") == before + 1
    assert val("agent_workflows_waiting_for_approval") == waiting0                      # no longer paused
    assert val("agent_workflow_active_seconds_sum", status="COMPLETED") >= 3.0         # the human wait is not "active" time
    assert val("agent_workflow_end_to_end_seconds_count", status="COMPLETED") >= 1
