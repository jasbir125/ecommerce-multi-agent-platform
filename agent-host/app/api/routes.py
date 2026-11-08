"""HTTP API of the agent host."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import AskPolicyRequest, StartRunRequest
from app.runner import RunError, RunManager
from app.security import Principal, authenticate

router = APIRouter(prefix="/api/agent")


def runner_of(request: Request) -> RunManager:
    return request.app.state.runner


def check_access(run: dict, who: Principal) -> None:
    """Customers see only their own workflows; operators and admins see all."""
    if not who.has_role("OPERATOR", "ADMIN") and run.get("customerId") != who.customer:
        raise HTTPException(403, "not your workflow")


def to_http(e: RunError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": str(e)})


@router.post("/runs")
async def start_run(body: StartRunRequest, stream: bool = False, runner: RunManager = Depends(runner_of), who: Principal = Depends(authenticate)):
    if who.customer and who.customer != body.customer_id:
        raise HTTPException(403, "customerId does not match the authenticated customer")
    args = dict(customer_id=body.customer_id, order_id=body.order_id, message=body.message,
                workflow_id=body.workflow_id, request_id=body.request_id)
    if not stream:
        try:
            return await runner.start(**args)
        except RunError as e:
            raise to_http(e)
    return await _sse(runner.stream_start(**args))


@router.post("/runs/{workflow_id}/resume")
async def resume_run(workflow_id: str, stream: bool = False, runner: RunManager = Depends(runner_of), who: Principal = Depends(authenticate)):
    try:
        check_access(await runner.get(workflow_id), who)
        if not stream:
            return await runner.resume(workflow_id)
    except RunError as e:
        raise to_http(e)
    return await _sse(runner.stream_resume(workflow_id))


@router.get("/runs/{workflow_id}")
async def get_run(workflow_id: str, runner: RunManager = Depends(runner_of), who: Principal = Depends(authenticate)):
    try:
        run = await runner.get(workflow_id)
    except RunError as e:
        raise to_http(e)
    check_access(run, who)
    return run


@router.get("/runs/{workflow_id}/trace")
async def get_trace(workflow_id: str, runner: RunManager = Depends(runner_of), who: Principal = Depends(authenticate)):
    try:
        check_access(await runner.get(workflow_id), who)
        return await runner.trace(workflow_id)
    except RunError as e:
        raise to_http(e)


async def _sse(events) -> StreamingResponse:
    """Server-sent events. The first event is pulled before streaming so errors become real HTTP statuses."""
    try:
        first = await events.__anext__()
    except RunError as e:
        raise to_http(e)

    async def body():
        yield f"data: {json.dumps(first, default=str)}\n\n"
        try:
            async for ev in events:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except RunError as e:
            yield f"data: {json.dumps({'event': 'error', 'code': e.code, 'message': str(e)})}\n\n"
    return StreamingResponse(body(), media_type="text/event-stream")


@router.get("/admin/metrics")
async def admin_metrics(request: Request, hours: int = 24, who: Principal = Depends(authenticate)):
    """Cost, token usage and RAG quality for the admin panel (OPERATOR or ADMIN only)."""
    if not who.has_role("OPERATOR", "ADMIN"):
        raise HTTPException(403, "operator role required")
    from app.api import admin
    try:
        return await admin.metrics(request.app.state.settings, max(1, min(hours, 24 * 30)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, {"code": "METRICS_UNAVAILABLE", "message": str(e)[:150]})


@router.post("/rag/ask")
async def ask_policy(body: AskPolicyRequest, request: Request, who: Principal = Depends(authenticate)):
    """Answer a policy question with the full RAG pipeline: grounded, cited, validated, or an honest 'could not verify'."""
    from app.llm import BudgetExceeded, UsageTracker
    from app.mcp.client import McpGateway
    from app.rag.pipeline import build_pipeline

    runner: RunManager = request.app.state.runner
    settings = request.app.state.settings
    customer = who.customer or "SYSTEM"
    if not runner.ask_limiter.allow(customer):
        raise HTTPException(429, {"code": "RATE_LIMITED", "message": "Too many policy questions; try again in a minute"})
    usage = UsageTracker(settings.run_token_budget)
    pipeline = build_pipeline(runner.deps, McpGateway(settings, customer, "WF-RAG-ASK"), {"callbacks": [usage]})
    filters = {"region": body.region, "policy_type": body.policy_type, "product_category": body.product_category,
               "customer_segment": body.customer_segment, "version": body.version, "as_of_date": body.as_of_date}
    try:
        result = await pipeline.answer(body.question, filters)
    except BudgetExceeded:
        raise HTTPException(429, {"code": "TOKEN_BUDGET", "message": "Token budget exceeded for this question"})
    result.diagnostics["usage"] = usage.summary()
    return result
