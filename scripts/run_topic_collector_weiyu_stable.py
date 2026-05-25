#!/usr/bin/env python3
"""Stable entrypoint for Weiyu-style AI topic collection.

This wrapper keeps the existing collector intact, but makes production
adjustments for GitHub Actions:
1. Ask DeepSeek for strict JSON when generating topic cards.
2. Keep the enriched candidate pool smaller so the job does not hit timeout.
3. Filter weakly related non-AI items before they reach Feishu.
4. Write the first Feishu column as 标题, with source, link and reliability.
"""

import asyncio
import json
import os
import re
import urllib.parse
from typing import Any

import run_topic_collector_weiyu as weiyu


AI_RELEVANCE_PATTERN = re.compile(
    r"\b("
    r"ai|artificial intelligence|openai|chatgpt|gpt-?\d*|claude|anthropic|gemini|deepseek|llm|"
    r"large language model|agentic|ai agent|ai agents|codex|cursor|windsurf|vibe coding|"
    r"prompt|rag|mcp|model context protocol|transformer|hugging\s*face|langchain|langgraph|"
    r"machine learning|neural|diffusion|midjourney|sora|runway|perplexity|notebooklm|"
    r"copilot|foundation model|generative ai|semantic search|inference|fine[- ]?tuning|"
    r"gpu|hbm|nvidia|cuda|embedding|vector database|claude code|openai api"
    r")\b",
    re.IGNORECASE,
)

ZH_AI_RELEVANCE_PATTERN = re.compile(
    r"AI|人工智能|大模型|语言模型|智能体|提示词|工作流|自动化|开源模型|多模态|生成式|"
    r"深度学习|机器学习|神经网络|图像生成|视频生成|编程助手|代码助手|知识库|检索增强|"
    r"推理模型|微调|向量库|嵌入|算力|英伟达|高带宽内存|Claude|Gemini|OpenAI|DeepSeek"
)

KNOWN_AI_SOURCE_PATTERN = re.compile(
    r"openai|anthropic|claude|google ai|gemini|hugging face|tldr ai|the decoder|venturebeat ai|"
    r"simon willison|langchain|langgraph|transformers|deepseek|perplexity|nvidia",
    re.IGNORECASE,
)

CARD_PROMPT_MARKERS = ("输出严格JSON", "字段只能是")
TITLE_ALIASES = ("标题", "热点", "Title")

STABLE_FIELD_NAMES = [
    "标题",
    "原文标题",
    "原文发表日期",
    "原文链接",
    "论证源",
    "内容来源",
    "原文核心观点",
    "原文逐字稿/操作说明",
    "来源平台",
    "一级分类",
    "AI评分",
    "可靠性",
    "标签",
    "切入点",
    "选题标题",
    "开头钩子",
    "中间论证",
    "结尾收束",
    "选题受众",
    "选题目的",
    "适合内容形态",
    "状态",
    "去重Key",
    "AI摘要",
]


def _looks_like_card_prompt(messages: list[dict[str, str]]) -> bool:
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


def translate_title_stable(title: str) -> str:
    # Avoid a separate title-translation API call. The topic-card call already
    # generates a Chinese, source-specific angle; item_to_record_stable uses it
    # as the bracketed Chinese hint for English source titles.
    return ""


def title_with_translation_stable(title: str) -> str:
    return weiyu.clean_text(title, 180)


def _candidate_text(item: Any) -> str:
    base = weiyu.base
    pieces = [
        base.text_of(item, "title", default=""),
        base.text_of(item, "url", "link"),
        base.text_of(item, "ai_summary", "summary", "description", "content"),
        " ".join(base.tags_of(item)),
        str(base.metadata_of(item).get("platform") or ""),
        str(base.get_attr(item, "source_type") or ""),
        base.source_label(item),
    ]
    return "\n".join(piece for piece in pieces if piece)


def is_ai_relevant_item(item: Any) -> bool:
    text = _candidate_text(item)
    if not text.strip():
        return False
    if KNOWN_AI_SOURCE_PATTERN.search(text):
        return True
    return bool(AI_RELEVANCE_PATTERN.search(text) or ZH_AI_RELEVANCE_PATTERN.search(text))


def _internal_limit(user_limit: int) -> int:
    raw = os.getenv("HORIZON_INTERNAL_LIMIT", "10")
    try:
        value = int(raw)
    except ValueError:
        value = 10
    return max(5, min(value, max(user_limit, 5)))


def fetch_social_sources_stable(hours: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not (os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN")):
        print("X/Twitter 未配置：请在 GitHub Secrets 添加 X_BEARER_TOKEN 或 TWITTER_BEARER_TOKEN。")
    if not os.getenv("YOUTUBE_API_KEY", "").strip():
        print("YouTube 未配置：请在 GitHub Secrets 添加 YOUTUBE_API_KEY。")
    return weiyu.fetch_social_sources(hours)


async def collect_with_social_stable(hours: int, limit: int) -> tuple[list[Any], dict[str, Any]]:
    # The original collector enriches roughly limit * 3 items. For a manual
    # limit of 20 that can become 60 deep-enriched records, which is why the
    # GitHub job sometimes reaches the 45-minute timeout. We still write up to
    # the user's requested limit, but only deep-enrich a tighter candidate pool.
    base_limit = _internal_limit(limit)
    items, metrics = await weiyu._ORIGINAL_COLLECT_WITH_HORIZON(hours, base_limit)
    social_items, social_metrics = await asyncio.to_thread(fetch_social_sources_stable, hours)
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


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _title_hint_from_record(record: dict[str, str]) -> str:
    for field in ("选题标题", "原文核心观点", "切入点"):
        value = weiyu.clean_text(record.get(field, ""), 80)
        if _has_chinese(value):
            value = re.split(r"[。！？!?\n]", value)[0].strip()
            value = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", value)
            return weiyu.clean_text(value, 60)
    return ""


def _domain_from_url(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _evidence_sources(item: Any, source: str, url: str) -> str:
    metadata = weiyu.base.metadata_of(item)
    merged = metadata.get("merged_sources") or []
    pieces: list[str] = []
    if isinstance(merged, list):
        pieces.extend(str(value) for value in merged if str(value).strip())
    for key in ("feed_name", "repo", "channel", "username", "platform"):
        value = metadata.get(key)
        if value:
            pieces.append(str(value))
    if source:
        pieces.append(source)
    domain = _domain_from_url(url)
    if domain:
        pieces.append(domain)
    deduped: list[str] = []
    for piece in pieces:
        cleaned = weiyu.clean_text(piece, 80)
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return "、".join(deduped[:6]) or "单一来源"


def _title_block(title: str, evidence: str, source: str, url: str, reliability: str) -> str:
    return "\n".join(
        [
            f"标题：{title}",
            f"论证源：{evidence or '单一来源'}",
            f"内容来源：{source or '未标注'}",
            f"原文链接：{url or '未抓到'}",
            f"可靠性：{reliability or '待验证'}",
        ]
    )


def item_to_record_stable(item: Any) -> dict[str, str]:
    record = weiyu.item_to_record(item)
    raw_title = weiyu.clean_text(weiyu.base.text_of(item, "title", default="未命名"), 180)
    current_title = record.get("原文标题") or record.get("Title") or raw_title
    if weiyu.is_mostly_english(raw_title) and "（" not in current_title:
        hint = _title_hint_from_record(record)
        if hint:
            current_title = f"{raw_title}（{hint}）"
            record["原文标题"] = current_title
    source = record.get("来源平台") or weiyu.base.source_label(item)
    url = record.get("原文链接") or weiyu.base.text_of(item, "url", "link")
    reliability = record.get("可靠性") or weiyu.base.reliability(item, source, url, record.get("AI摘要", ""))
    evidence = _evidence_sources(item, source, url)
    primary_title = _title_block(current_title, evidence, source, url, reliability)
    record["标题"] = primary_title
    record["Title"] = primary_title
    record["热点"] = primary_title
    record["原文标题"] = current_title
    record["论证源"] = evidence
    record["内容来源"] = source
    return record


def _field_items(self: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query: dict[str, Any] = {"page_size": 100}
        if page_token:
            query["page_token"] = page_token
        result = self._request("GET", f"{self._base_url()}/fields?{urllib.parse.urlencode(query)}")
        data = result.get("data", {})
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            return items
        page_token = data.get("page_token", "")


def ensure_fields_stable(self: Any) -> None:
    items = _field_items(self)
    by_name = {item.get("field_name"): item for item in items if item.get("field_name")}
    if "标题" not in by_name:
        for legacy_name in ("热点", "Title"):
            legacy = by_name.get(legacy_name)
            field_id = legacy.get("field_id") if isinstance(legacy, dict) else None
            if not field_id:
                continue
            try:
                self._request("PUT", f"{self._base_url()}/fields/{field_id}", {"field_name": "标题"})
                print(f"已将飞书字段 {legacy_name} 重命名为 标题。")
                return ensure_fields_stable(self)
            except Exception as exc:
                print(f"字段 {legacy_name} 重命名为 标题失败，继续尝试创建/写入标题字段：{exc}")
    existing = set(by_name)
    missing = [name for name in STABLE_FIELD_NAMES if name not in existing]
    if not missing:
        return
    if os.getenv("FEISHU_AUTO_CREATE_FIELDS", "true").lower() != "true":
        raise RuntimeError("飞书表格缺少字段：" + "、".join(missing))
    for name in missing:
        self._request("POST", f"{self._base_url()}/fields", {"field_name": name, "type": 1})


def batch_create_stable(self: Any, records: list[dict[str, str]]) -> int:
    allowed_fields = {item.get("field_name") for item in _field_items(self) if item.get("field_name")}
    written = 0
    for start in range(0, len(records), 100):
        chunk = records[start : start + 100]
        if not chunk:
            continue
        filtered = []
        for record in chunk:
            fields = {key: value for key, value in record.items() if key in allowed_fields}
            for alias in TITLE_ALIASES:
                if alias in allowed_fields and alias not in fields:
                    fields[alias] = record.get("标题") or record.get("Title") or record.get("原文标题") or ""
            filtered.append({"fields": fields})
        self._request("POST", f"{self._base_url()}/records/batch_create", {"records": filtered})
        written += len(filtered)
    return written


weiyu.FIELD_NAMES = STABLE_FIELD_NAMES
weiyu.base.FIELD_NAMES = STABLE_FIELD_NAMES
weiyu.deepseek_chat = deepseek_chat_stable
weiyu.extract_json_object = extract_json_object_stable
weiyu.translate_title = translate_title_stable
weiyu.title_with_translation = title_with_translation_stable
weiyu.base.collect_with_horizon = collect_with_social_stable
weiyu.base.item_to_record = item_to_record_stable
weiyu.base.FeishuBitableClient.ensure_fields = ensure_fields_stable
weiyu.base.FeishuBitableClient.batch_create = batch_create_stable


if __name__ == "__main__":
    raise SystemExit(asyncio.run(weiyu.base.main()))