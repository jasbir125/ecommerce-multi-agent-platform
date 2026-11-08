"""Deterministic checks that an answer says only what its cited passages say.

For every statement the model wrote we check, in code and without another model:
  citations exist and are valid; no instruction-like text; every number, amount and duration appears in the cited
  passages; every policy version mentioned matches a cited passage; enough of the statement's content words appear
  in the cited passages; and its polarity (can / cannot) matches the best-matching sentence in them.
A single failing statement fails the whole answer, because one wrong policy claim is worse than no answer.
"""
from __future__ import annotations

import re

from app.rag.models import INSTRUCTION_LIKE, ContextPack, GroundingReport, Issue, Statement

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "and", "for", "are", "was", "with", "that", "this", "from", "have", "has", "not", "you", "your", "can",
         "will", "what", "when", "how", "does", "about", "may", "any", "all", "its", "who", "which", "into", "than",
         "then", "they", "their", "been", "being", "should", "would", "could", "also", "only", "must", "within", "after",
         "before", "per", "our", "out", "off", "get", "got", "did", "each", "every", "both", "such", "these", "those"}
# Words that negate a claim ("cannot be cancelled"). "no" is handled separately: "no carrier scan" is not a negation.
_NEGATION = {"not", "never", "cannot", "cant", "isnt", "arent", "wont", "dont", "doesnt", "didnt", "wasnt", "werent", "shouldnt", "mustnt"}
_CLAUSE_NO = re.compile(r"(^|[.;:,]\s+)no\s+(?!longer\b)\w+|\bno longer\b|\bno more\b", re.IGNORECASE)
_NUMBER_WORDS = {w: str(n) for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())}
_NUMBER_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
                      "ninety": "90", "hundred": "100"})
_VERSION = re.compile(r"\b(?:version|v)\s*(\d+(?:\.\d+)*)\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")
_MARKER = re.compile(r"\[\d+\]")


def numbers(text: str) -> set[str]:
    """Every number in the text, digits or words, as normalised strings ("three (3)" -> {\"3\"})."""
    text = _VERSION.sub(" ", _MARKER.sub(" ", text))
    out = {m.replace(",", "").lstrip("0") or "0" for m in _DIGITS.findall(text)}
    out |= {_NUMBER_WORDS[w] for w in _WORD.findall(text.lower()) if w in _NUMBER_WORDS}
    return out


def content_stems(text: str) -> set[str]:
    words = [w for w in _WORD.findall(_MARKER.sub(" ", text).lower().replace("'", "")) if len(w) > 2 and w not in _STOP]
    return {w[:6] for w in words if not w.isdigit() and w not in _NUMBER_WORDS}


def _negated(text: str) -> bool:
    words = _WORD.findall(text.lower().replace("'", ""))
    if any(w in _NEGATION for w in words):
        return True
    # "No refunds are issued ..." / "no longer" negate; "when there is no carrier scan" does not.
    return bool(_CLAUSE_NO.search(text.strip()) and (text.strip().lower().startswith("no ") or " no longer " in text.lower() or " no more " in text.lower()))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def validate(statements: list[Statement], pack: ContextPack, min_support: float = 0.5) -> GroundingReport:
    if not statements:
        return GroundingReport(passed=False, score=0.0, issues=[Issue(code="NO_STATEMENTS", detail="the answer is empty")])
    issues: list[Issue] = []
    bad: set[int] = set()

    def flag(i: int, code: str, detail: str = "") -> None:
        issues.append(Issue(code=code, statement=i, detail=detail))
        bad.add(i)

    for i, st in enumerate(statements):
        text = _MARKER.sub("", st.text).strip()
        if not text:
            flag(i, "EMPTY_STATEMENT")
            continue
        if not st.cites:
            flag(i, "CITATION_MISSING", text[:80])
            continue
        items = [pack.get(n) for n in st.cites]
        if any(x is None for x in items):
            flag(i, "CITATION_INVALID", f"cites {st.cites}, available {[x.n for x in pack.items]}")
            continue
        if INSTRUCTION_LIKE.search(text):
            flag(i, "INSTRUCTION_LIKE_TEXT", text[:80])
            continue
        cited = " ".join(f"{x.text} {x.chunk.section or ''}" for x in items)
        extra = numbers(text) - numbers(cited)
        if extra:
            flag(i, "FABRICATED_NUMBER", f"{sorted(extra)} not in the cited passages")
        versions = {x.chunk.version for x in items}
        wrong = {v for v in _VERSION.findall(text)} - versions
        if wrong:
            flag(i, "FABRICATED_VERSION", f"mentions version {sorted(wrong)}, cited passages are version {sorted(versions)}")
        stems = content_stems(text)
        if stems:
            support = len(stems & content_stems(cited)) / len(stems)
            if support < min_support:
                flag(i, "UNSUPPORTED_CLAIM", f"only {support:.0%} of its content appears in the cited passages")
        # polarity: compare with the cited sentence that overlaps this statement the most
        best, best_overlap = None, 0.0
        for sent in (s for x in items for s in _sentences(x.text)):
            ss = content_stems(sent)
            overlap = len(stems & ss) / len(stems) if stems else 0
            if overlap > best_overlap:
                best, best_overlap = sent, overlap
        if best is not None and best_overlap >= 0.5 and _negated(text) != _negated(best):
            flag(i, "CONTRADICTION", f"statement and passage disagree: {best[:80]!r}")

    score = round(1 - len(bad) / len(statements), 3)
    return GroundingReport(passed=not issues, score=score, issues=issues)


def describe(report: GroundingReport) -> str:
    """Feedback text for a retry."""
    return "; ".join(f"statement {i.statement}: {i.code} {i.detail}".strip() for i in report.issues[:5])
