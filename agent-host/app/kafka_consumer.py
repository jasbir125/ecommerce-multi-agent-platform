"""Kafka triggers: start a run when a customer request arrives, resume it when an approval is decided."""
from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

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
                    self._handle(msg.topic, msg.value)
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

    def _handle(self, topic: str, value: bytes) -> None:
        try:
            env = json.loads(value)
        except ValueError:
            log.warning("ignoring malformed message on %s", topic)
            return
        # Long-running work happens in a task so the consumer keeps polling.
        job = asyncio.create_task(self._dispatch(topic, env))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    async def _dispatch(self, topic: str, env: dict) -> None:
        try:
            if not await self._runner.claim_event(env["eventId"]):
                return                                    # duplicate delivery
            wf, payload = env.get("workflowId"), env.get("payload", {})
            if topic == "workflow.started":
                await self._runner.start_from_workflow(wf, payload["customerId"])
            elif topic == "workflow.approval.decided":
                if (await self._runner.snapshot(wf)).values:   # only runs this host started
                    await self._runner.resume(wf)
        except Exception:  # noqa: BLE001
            log.exception("failed to process %s event", topic)
