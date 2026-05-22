#!/usr/bin/env python3
"""Stable entrypoint for Weiyu-style AI topic collection.

This wrapper keeps the existing collector intact, but makes two production
adjustments for GitHub Actions:
1. Ask DeepSeek for strict JSON when generating topic cards.
2. Keep the enriched candidate pool smaller so the job does not hit timeout.
"""

import asyncio
import json
import math
import os
import re
from typing import Any

import run_topic_collector_weiyu as weiyu


def _looks_like_card_prompt(messages: list[dict[str, str]]) -> bool:
    system_text = "\n".join(message.get("content", "") for message in messages if message.get("role") == "system")
    return "输出严格JSON" in system_text or "字段只能是" in system_text


def deepseek_chat_stable(messages: list[dict[str, str]], max_tokens: int = 1000, temperature: float = 0.35) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    payload: dict[str, Any] = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if _looks_like_card_prompt(messages):
        payload["response_format"] = {"type": "json_object"}
    result = weiyu.request_json(
        "https://api.deepseek.com/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        payload,
        45,
    )
    return result["choices"][0]["message"]["content"]


def _strip_json_fence(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _loose_card_fields(text: str) -> dict[str, str]:
    body = _strip_json_fence(text)
    result: dict[str, str] = {}
    field_positions: list[tuple[str, int, int]] = []
    for field in weiyu.CARD_FIELDS:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*', body)
        if match:
            field_positions.append((field, match.start(), match.end()))
    field_positions.sort(key=lambda row: row[1])
    for index, (field, _field_start, value_start) in enumerate(field_positions):
        value_end = field_positions[index + 1][1] if index + 1 < len(field_positions) else len(body)
        value = body[value_start:value_end].strip().rstrip(",").strip()
        if value.startswith('"'):
            value = value[1:]
        if value.endswith('"'):
            value = value[:-1]
        value = value.replace('\\"', '"').replace('\\n', '\n')
        value = re.sub(r"\n\s*\n+", "\n", value).strip()
        if value:
            result[field] = value
    return result


def extract_json_object_stable(text: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        loose = _loose_card_fields(cleaned)
        if loose:
            return loose
        raise


def _internal_limit(user_limit: int) -> int:
    raw = os.getenv("HORIZON_INTERNAL_LIMIT", "10")
    try:
        value = int(raw)
    except ValueError:
        value = 10
    return max(5, min(value, max(user_limit, 5)))


async def collect_with_social_stable(hours: int, limit: int) -> tuple[list[Any], dict[str, Any]]:
    # The original collector enriches roughly limit * 3 items. For a manual
    # limit of 20 that can become 60 deep-enriched records, which is why the
    # GitHub job sometimes reaches the 45-minute timeout. We still write up to
    # the user's requested limit, but only deep-enrich a tighter candidate pool.
    base_limit = _internal_limit(limit)
    items, metrics = await weiyu._ORIGINAL_COLLECT_WITH_HORIZON(hours, base_limit)
    social_items, social_metrics = await asyncio.to_thread(weiyu.fetch_social_sources, hours)
    metrics.update(social_metrics)
    if social_items:
        items = weiyu.base.sort_items(list(items) + social_items)
    target_pool_size = max(limit, base_limit * 3)
    items = weiyu.base.sort_items(list(items))[:target_pool_size]
    metrics["selected_items"] = len(items)
    metrics["internal_limit"] = base_limit
    metrics["target_pool_size"] = target_pool_size
    return items, metrics


weiyu.deepseek_chat = deepseek_chat_stable
weiyu.extract_json_object = extract_json_object_stable
weiyu.base.collect_with_horizon = collect_with_social_stable


if __name__ == "__main__":
    raise SystemExit(asyncio.run(weiyu.base.main()))
