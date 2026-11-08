"""Kafka triggers: start a run when a customer request arrives, resume it when an approval is decided."""
from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from app.observability import logctx
from app.observability.tracing import extract_context, span
from app.runner import RunManager

log = logging.getLogger("agent.kafka")
TOPICS = ["workflow.started", "workflow.approval.decided"]


class TriggerConsumer:
    def __init__(self, bootstrap: str, runner: RunManager):
        self._bootstrap, self._runner = bootstrap, runner
        self._task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="kafka-triggers")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for j in list(self._jobs):
            j.cancel()

    async def _loop(self) -> None:
        delay = 2
        while True:
            consumer = AIOKafkaConsumer(*TOPICS, bootstrap_servers=self._bootstrap, group_id="recovery-agent-host",
                                        auto_offset_reset="latest", enable_auto_commit=True)
            try:
                await consumer.start()
                log.info("Kafka trigger consumer listening on %s", TOPICS)
                delay = 2
                async for msg in consumer:
                    self._handle(msg.topic, msg.value, {k: v.decode() for k, v in (msg.headers or []) if isinstance(v, bytes)})
            except asyncio.CancelledError:
                await consumer.stop()
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Kafka consumer error: %s (retry in %ss)", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            finally:
                try:
                    await consumer.stop()
                except Exception:  # noqa: BLE001
                    pass

    def _handle(self, topic: str, value: bytes, headers: dict[str, str] | None = None) -> None:
        try:
            env = json.loads(value)
        except ValueError:
            log.warning("ignoring malformed message on %s", topic)
            return
        # Long-running work happens in a task so the consumer keeps polling.
        job = asyncio.create_task(self._dispatch(topic, env, extract_context(headers)))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    async def _dispatch(self, topic: str, env: dict, trace_ctx=None) -> None:
        wf, payload = env.get("workflowId"), env.get("payload", {})
        tokens = logctx.bind(correlation_id=env.get("correlationId"), workflow_id=wf)
        log.info("KAFKA IN  topic=%s type=%s eventId=%s corr=%s wf=%s cause=%s payload=%s", topic, env.get("type"), env.get("eventId"),
                 env.get("correlationId"), wf, env.get("causationId"), logctx.clean(payload))
        try:
            if not await self._runner.claim_event(env["eventId"]):
                log.info("KAFKA IN  topic=%s eventId=%s duplicate delivery, skipped", topic, env.get("eventId"))
                return                                    # duplicate delivery
            if topic == "workflow.started":
                with span("workflow.run", context=trace_ctx, workflow_id=wf, trigger=topic):       # child of the request's trace
                    await self._runner.start_from_workflow(wf, payload["customerId"])
            elif topic == "workflow.approval.decided":
                if (await self._runner.snapshot(wf)).values:   # only runs this host started
                    with span("workflow.resume", context=trace_ctx, workflow_id=wf, trigger=topic):
                        await self._runner.resume(wf)
        except Exception:  # noqa: BLE001
            log.exception("failed to process %s event eventId=%s", topic, env.get("eventId"))
        finally:
            logctx.unbind(tokens)
