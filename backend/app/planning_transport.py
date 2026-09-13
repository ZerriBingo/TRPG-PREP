"""Reversible fact identifiers for bounded planning requests, never stored facts."""
from __future__ import annotations

import json
from typing import Any


class PlanningTransport:
    def __init__(self, client: Any, fact_ids: list[str], context: dict | None = None):
        self.client = client
        self.encode_ids = {fact_id: f"f{index}" for index, fact_id in enumerate(sorted(set(fact_ids)))}
        context = context or {}
        for prefix, items in (
            ("chapter_", [*context.get("chapters", []), *context.get("adventure_index", [])]),
            ("thread_", context.get("threads", [])),
            ("story_beat_", context.get("beats", [])),
        ):
            for index, identifier in enumerate(sorted({item["id"] for item in items})):
                self.encode_ids[identifier] = f"{prefix}{index}"
        self.decode_ids = {value: key for key, value in self.encode_ids.items()}

    def _translate(self, value: Any, mapping: dict[str, str], identifiers: bool = False) -> Any:
        if isinstance(value, dict):
            translated = {
                mapping.get(key, key): self._translate(item, mapping, identifiers or key in {
                    "id", "fact_id", "fact_ids", "reference_fact_ids", "global_reference_fact_ids",
                    "field_sources", "missing_chapters", "missing_global", "enum",
                    "chapter_id", "chapter_ids", "chapter_fact_ids",
                    "thread_id", "thread_ids", "story_beat_ids",
                }) for key, item in value.items()
            }
            if len(translated) != len(value):
                raise ValueError("planning response contains conflicting fact identifiers")
            return translated
        if isinstance(value, list):
            return [self._translate(item, mapping, identifiers) for item in value]
        if isinstance(value, str) and identifiers:
            return mapping.get(value, value)
        return value

    def messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        result = []
        for message in messages:
            lines = []
            for line in message["content"].splitlines():
                prefix, separator, body = line.partition("_JSON=")
                try:
                    value = json.loads(body if separator else line)
                except (ValueError, TypeError):
                    lines.append(line)
                    continue
                encoded = json.dumps(self._translate(value, self.encode_ids), ensure_ascii=False, separators=(",", ":"))
                lines.append(prefix + separator + encoded if separator else encoded)
            result.append({**message, "content": "\n".join(lines)})
        return result

    def chat_json(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        response = self.client.chat_json(self.messages(messages), **kwargs)
        return self._translate(response, self.decode_ids)
