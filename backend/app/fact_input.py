"""Shared projection and transport batching for promoted source facts.

The artifact and structure stages read the same promoted facts, so they must see the
same fields, in the same order, under the same request budgets. This module owns that
contract and nothing else: it knows no card, chapter, prompt or job model, and it
imports no generation module.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Sequence

# Keep individual model requests comfortably below the hard input ceiling. A job may
# contain several of these batches; a batch is a transport unit, never a product
# preparation unit, so dividing one never changes what belongs together.
MAX_FACT_BATCH_CHARS = 80_000
MAX_SINGLE_FACT_CHARS = MAX_FACT_BATCH_CHARS
MAX_FACT_BATCH_TOKENS = 22_000
MAX_SINGLE_FACT_TOKENS = MAX_FACT_BATCH_TOKENS
MAX_FACTS_PER_BATCH = 200


class FactInputError(ValueError):
    """A single fact cannot be sent inside one bounded request."""


def estimate_tokens(value: Any) -> int:
    """Conservative fallback when the configured upstream exposes no tokenizer."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    cjk_count = sum(1 for character in text if _is_cjk(character))
    other_count = max(len(text) - cjk_count, 0)
    return cjk_count + (other_count + 3) // 4


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF


def fact_payload(facts: Sequence[Any]) -> list[dict[str, Any]]:
    """Project reviewed facts into the only shape a model request may see.

    A model candidate is not a promoted fact, so it is never sent as source material.
    Field names, order and values are the existing contract; changing them would
    change what every stage reads.
    """
    return [
        {
            "id": fact.id,
            "text": fact.text,
            "kind": fact.kind,
            "visibility": fact.visibility,
            "evidence_status": fact.evidence_status,
            "source_refs": [ref.model_dump(mode="json") for ref in fact.source_refs],
            "tags": fact.tags,
            "notes": fact.notes,
        }
        for fact in facts
        if fact.evidence_status != "model_candidate"
    ]


def fact_batches(
    facts: Sequence[Any],
    *,
    error_factory: Callable[[str], Exception] = FactInputError,
) -> list[list[Any]]:
    """Pack facts in the given source order using the conservative tokenizer fallback.

    `error_factory` lets each caller keep its own generation error type for the one
    case this module cannot express as a batch: a single fact that exceeds a whole
    request by itself.
    """
    batches: list[list[Any]] = []
    current: list[Any] = []
    for fact in facts:
        single_payload = fact_payload([fact])
        single_serialized = json.dumps(single_payload, ensure_ascii=False)
        single_size = len(single_serialized)
        single_tokens = estimate_tokens(single_serialized)
        if single_size > MAX_SINGLE_FACT_CHARS or single_tokens > MAX_SINGLE_FACT_TOKENS:
            raise error_factory(
                f"事实 {fact.id} 单条输入约 {single_size} 字符 / {single_tokens} 估算 token，"
                "超过单批保护上限；请拆分或缩短该事实后重试。"
            )
        candidate = [*current, fact]
        serialized = json.dumps(fact_payload(candidate), ensure_ascii=False)
        size = len(serialized)
        tokens = estimate_tokens(serialized)
        if current and (
            size > MAX_FACT_BATCH_CHARS
            or tokens > MAX_FACT_BATCH_TOKENS
            or len(candidate) > MAX_FACTS_PER_BATCH
        ):
            batches.append(current)
            current = [fact]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches
