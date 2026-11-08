"""Agent host: FastAPI service that runs the LangGraph supervisor workflow."""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.evaluation.evaluator import RunEvaluator
from app.graph.deps import Deps
from app.graph.supervisor_graph import build_graph
from app.kafka_consumer import TriggerConsumer
from app.llm import ModelFactory
from app.observability.tracing import setup_tracing
from app.runner import RunManager
from app.services.workflow_client import WorkflowClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("agent.main")


def checkpoint_serde():
    """Allow-list the pydantic models we store in checkpoints (LangGraph blocks unknown types)."""
    import inspect

    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from app.models import order, policy, product, resolution, risk, shipment, workflow_state
    allowed = []
    for mod in (order, policy, product, resolution, risk, shipment, workflow_state):
        allowed += [(mod.__name__, name) for name, cls in inspect.getmembers(mod, inspect.isclass) if cls.__module__ == mod.__name__]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


async def make_checkpointer(stack: AsyncExitStack, settings):
    """Durable checkpoints in Postgres so a run can pause for approval and survive a restart."""
    if settings.checkpointer == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(settings.database_url, serde=checkpoint_serde()))
            await saver.setup()
            log.info("Using Postgres checkpointer")
            return saver
        except Exception as e:  # noqa: BLE001
            log.warning("Postgres checkpointer unavailable (%s); falling back to in-memory", e)
    from langgraph.checkpoint.memory import InMemorySaver
    log.info("Using in-memory checkpointer (runs do not survive a restart)")
    return InMemorySaver(serde=checkpoint_serde())


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_tracing(settings.otel_http_endpoint)
    async with AsyncExitStack() as stack:
        redis = None
        try:
            import redis.asyncio as aioredis
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await redis.ping()
        except Exception as e:  # noqa: BLE001
            log.warning("Redis unavailable (%s); using process-local locks", e)
            redis = None
        deps = Deps(settings, ModelFactory(settings), WorkflowClient(settings), RunEvaluator(settings.database_url))
        graph = build_graph(await make_checkpointer(stack, settings))
        runner = RunManager(settings, deps, graph, redis)
        app.state.settings, app.state.runner = settings, runner
        consumer = None
        if settings.agent_kafka_enabled:
            consumer = TriggerConsumer(settings.kafka_bootstrap_servers, runner)
            consumer.start()
        log.info("agent-host ready (provider=%s)", settings.model_provider)
        yield
        if consumer:
            await consumer.stop()
        await deps.workflows.aclose()


app = FastAPI(title="agent-host", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
