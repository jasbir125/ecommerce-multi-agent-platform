"""Redis cache for final, validated answers. Every failure is swallowed: the cache can only make things faster."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.rag.models import RagResult

log = logging.getLogger("rag.cache")


def make_key(question: str, filters: dict[str, Any], corpus_version: str, chunk_ids: list[str], model: str, version: str = "rag1") -> str:
    raw = json.dumps([question.strip().lower(), sorted((k, str(v)) for k, v in filters.items() if v), corpus_version, chunk_ids, model, version])
    return "rag:answer:" + hashlib.sha256(raw.encode()).hexdigest()


class AnswerCache:
    def __init__(self, redis=None, ttl: int = 900):
        self.redis, self.ttl = redis, ttl

    async def get(self, key: str) -> RagResult | None:
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(key)
            return RagResult.model_validate_json(raw) if raw else None
        except Exception as e:  # noqa: BLE001
            log.debug("answer cache get failed: %s", e)
            return None

    async def set(self, key: str, result: RagResult) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.set(key, result.model_dump_json(), ex=self.ttl)
        except Exception as e:  # noqa: BLE001
            log.debug("answer cache set failed: %s", e)
