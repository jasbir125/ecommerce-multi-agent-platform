"""Builds the evidence the generator sees: numbered, de-duplicated, scanned for injection, within a token budget."""
from __future__ import annotations

import re

from app.rag.models import INSTRUCTION_LIKE, Chunk, ContextItem, ContextPack, est_tokens

_WORD = re.compile(r"[a-z0-9]+")


def _stems(text: str) -> set[str]:
    return {w[:6] for w in _WORD.findall(text.lower()) if len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def compress(text: str, question: str, max_tokens: int) -> str:
    """Keep the sentences that overlap the question (in original order) until the budget is used."""
    if est_tokens(text) <= max_tokens:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    q = _stems(question)
    ranked = sorted(range(len(sentences)), key=lambda i: len(q & _stems(sentences[i])) + (0.05 if i == 0 else 0), reverse=True)
    keep, used = [], 0
    for i in ranked:
        t = est_tokens(sentences[i])
        if not keep or used + t <= max_tokens:
            keep.append(i)
            used += t
    return " ".join(sentences[i] for i in sorted(keep))


def build_context(question: str, chunks: list[Chunk], max_tokens: int = 700, max_items: int = 5) -> ContextPack:
    pack = ContextPack()
    seen: list[set[str]] = []
    per_item = max(80, max_tokens // max(1, min(max_items, len(chunks))))
    for c in chunks:
        if len(pack.items) >= max_items:
            break
        if c.injection_suspected or not c.text:
            pack.dropped.append(f"withheld:{c.chunk_id}")
            continue
        if INSTRUCTION_LIKE.search(c.text):                       # second scan: never trust the first one alone
            pack.dropped.append(f"injection:{c.chunk_id}")
            continue
        s = _stems(c.text)
        if any(_jaccard(s, o) > 0.85 for o in seen):
            pack.dropped.append(f"duplicate:{c.chunk_id}")
            continue
        text = compress(c.text, question, per_item)
        t = est_tokens(text)
        if pack.tokens + t > max_tokens and pack.items:
            pack.dropped.append(f"budget:{c.chunk_id}")
            continue
        seen.append(s)
        pack.items.append(ContextItem(n=len(pack.items) + 1, chunk=c, text=text))
        pack.tokens += t
    return pack


def render(pack: ContextPack) -> str:
    """Prompt text. Each passage is wrapped so the model can tell evidence from instructions."""
    return "\n".join(f'<context id="{i.n}" policy="{i.chunk.policy_key}" version="{i.chunk.version}" section="{i.chunk.section}">\n{i.text}\n</context>'
                     for i in pack.items)


def apply_region_precedence(chunks: list[Chunk], region: str | None) -> tuple[list[Chunk], list[str]]:
    """A policy written for the customer's region overrides the global policy on the same topic (policy type).
    The model cannot be trusted to know that, so global passages are removed here when a regional one applies."""
    if not region or region.upper() == "ALL":
        return chunks, []
    r = region.upper()
    regional_types = {c.policy_type for c in chunks if c.region.upper() == r and c.policy_type}
    kept, dropped = [], []
    for c in chunks:
        if c.region.upper() == "ALL" and c.policy_type in regional_types:
            dropped.append(f"overridden-by-region:{c.chunk_id}")
        else:
            kept.append(c)
    return kept, dropped
