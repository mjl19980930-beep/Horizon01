#!/usr/bin/env python3
"""Stable entrypoint for Weiyu-style AI topic collection.

This wrapper keeps the existing collector intact, but makes production
adjustments for GitHub Actions:
1. Ask DeepSeek for strict JSON when generating topic cards and translations.
2. Keep the enriched candidate pool smaller so the job does not hit timeout.
3. Filter weakly related non-AI items before they reach Feishu.
"""

import asyncio
import json
import math
import os
import re
from typing import Any

import run_topic_collector_weiyu as weiyu


AI_RELEVANCE_PATTERN = re.compile(
    r"\b("
    r"ai|artificial intelligence|openai|chatgpt|gpt-?\d*|claude|anthropic|gemini|deepseek|llm|"
    r"large language model|agentic|ai agent|agents?|codex|cursor|windsurf|vibe coding|"
    r"prompt|rag|mcp|model context protocol|transformer|hugging\s*face|langchain|langgraph|"
    r"machine learning|neural|diffusion|midjourney|sora|runway|perplexity|notebooklm|"
    r"automation|workflow|copilot"
    r")\b",
    re.IGNORECASE,
)

ZH_AI_RELEVANCE_PATTERN = re.compile(
    r"AI|人工智能|大模型|语言模型|智能体|代理|提示词|工作流|自动化|开源模型|多模态|生成式|"
    r"深度学习|机器学习|神经网络|图像生成|视频生成|编程助手|代码助手|知识库|检索增强"
)


CARD_PROMPT_MARKERS = ("输出严格JSON", "字段只能是", '"zh"')


def _looks_like_json_prompt(messages: list[dict[str, str]]) -> bool:
    system_text = "\n".join(message.get("content", "") for message in messages if message.get("role") == "system")
    return any(marker in system_text for marker in CARD_PROMPT_MARKERS)


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
    if _looks_like_json_prompt(messages):
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


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def translate_title_stable(title: str) -> str:
    title = weiyu.clean_text(title, 180)
    if not weiyu.is_mostly_english(title):
        return ""
    if title in weiyu.TRANSLATION_CACHE:
        return weiyu.TRANSLATION_CACHE[title]
    translated = ""
    try:
        raw = deepseek_chat_stable(
            [
                {
                    "role": "system",
                    "content": (
                        "你只做英文标题到中文标题的翻译。输出严格JSON，格式只能是 {\"zh\": \"中文标题\"}。"
                        "中文要自然、短、适合内容选题库，不要解释，不要加引号，不要保留英文。"
                    ),
                },
                {"role": "user", "content": title},
            ],
            max_tokens=120,
            temperature=0.1,
        )
        parsed = extract_json_object_stable(raw)
        translated = weiyu.clean_text(str(parsed.get("zh") or ""), 120)
    except Exception as exc:
        print(f"标题翻译失败，保留英文标题：{exc}")
    if translated and not _has_chinese(translated):
        translated = ""
    weiyu.TRANSLATION_CACHE[title] = translated
    return translated


def title_with_translation_stable(title: str) -> str:
    title = weiyu.clean_text(title, 180)
    translated = translate_title_stable(title)
    return f"{title}（{translated}）" if translated and translated.lower() != title.lower() else title


def _candidate_text(item: Any) -> str:
    base = weiyu.base
    pieces = [
        base.text_of(item, "title", default=""),
        base.text_of(item, "ai_summary", "summary", "description", "content"),
        " ".join(base.tags_of(item)),
        str(base.metadata_of(item).get("platform") or ""),
        str(base.get_attr(item, "source_type") or ""),
    ]
    return "\n".join(piece for piece in pieces if piece)


def is_ai_relevant_item(item: Any) -> bool:
    text = _candidate_text(item)
    if not text.strip():
        return False
    if AI_RELEVANCE_PATTERN.search(text) or ZH_AI_RELEVANCE_PATTERN.search(text):
        return True
    try:
        return weiyu.base.topic_relevance_score(item) >= 1
    except Exception:
        return False


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
    relevant_items = [item for item in items if is_ai_relevant_item(item)]
    if relevant_items:
        items = relevant_items
    target_pool_size = max(limit, base_limit * 3)
    items = weiyu.base.sort_items(list(items))[:target_pool_size]
    metrics["selected_items"] = len(items)
    metrics["ai_relevant_items"] = len(relevant_items)
    metrics["internal_limit"] = base_limit
    metrics["target_pool_size"] = target_pool_size
    return items, metrics


weiyu.deepseek_chat = deepseek_chat_stable
weiyu.extract_json_object = extract_json_object_stable
weiyu.translate_title = translate_title_stable
weiyu.title_with_translation = title_with_translation_stable
weiyu.base.collect_with_horizon = collect_with_social_stable


if __name__ == "__main__":
    raise SystemExit(asyncio.run(weiyu.base.main()))
