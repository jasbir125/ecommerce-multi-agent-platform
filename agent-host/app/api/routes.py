"""HTTP API of the agent host."""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import StartRunRequest
from app.runner import RunError, RunManager

router = APIRouter(prefix="/api/agent")


def runner_of(request: Request) -> RunManager:
    return request.app.state.runner


def authenticate(request: Request, x_service_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.mcp_service_token
    if not x_service_token or not hmac.compare_digest(x_service_token.encode(), expected.encode()):
        raise HTTPException(401, "invalid service token")


def caller(x_customer_id: str | None = Header(default=None), x_roles: str | None = Header(default=None)):
    roles = {r.strip().upper() for r in (x_roles or "").split(",") if r.strip()}
    return x_customer_id, roles


def check_access(run: dict, who) -> None:
    customer, roles = who
    if customer and not roles & {"OPERATOR", "ADMIN"} and run.get("customerId") != customer:
        raise HTTPException(403, "not your workflow")


def to_http(e: RunError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": str(e)})


@router.post("/runs", dependencies=[Depends(authenticate)])
async def start_run(body: StartRunRequest, stream: bool = False, runner: RunManager = Depends(runner_of), who=Depends(caller)):
    if who[0] and who[0] != body.customer_id:
        raise HTTPException(403, "customerId does not match the authenticated customer")
    args = dict(customer_id=body.customer_id, order_id=body.order_id, message=body.message,
                workflow_id=body.workflow_id, request_id=body.request_id)
    if not stream:
        try:
            return await runner.start(**args)
        except RunError as e:
            raise to_http(e)
    return await _sse(runner.stream_start(**args))


@router.post("/runs/{workflow_id}/resume", dependencies=[Depends(authenticate)])
async def resume_run(workflow_id: str, stream: bool = False, runner: RunManager = Depends(runner_of), who=Depends(caller)):
    try:
        check_access(await runner.get(workflow_id), who)
        if not stream:
            return await runner.resume(workflow_id)
    except RunError as e:
        raise to_http(e)
    return await _sse(runner.stream_resume(workflow_id))


@router.get("/runs/{workflow_id}", dependencies=[Depends(authenticate)])
async def get_run(workflow_id: str, runner: RunManager = Depends(runner_of), who=Depends(caller)):
    try:
        run = await runner.get(workflow_id)
    except RunError as e:
        raise to_http(e)
    check_access(run, who)
    return run


@router.get("/runs/{workflow_id}/trace", dependencies=[Depends(authenticate)])
async def get_trace(workflow_id: str, runner: RunManager = Depends(runner_of), who=Depends(caller)):
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
