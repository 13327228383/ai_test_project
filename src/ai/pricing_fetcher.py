# -*- coding: utf-8 -*-
from __future__ import annotations

"""

DeepSeek 模型定价实时获取器 (PricingFetcher)

=============================================

从 DeepSeek 官方定价页面抓取并解析最新模型定价，缓存于 MySQL system_config 表 24 小时。

"""


import json

import re

import time


import requests


# ── 缓存 TTL ──

import logging

_log = logging.getLogger(__name__)
_CACHE_TTL = 86400  # 24 小时

# ── MySQL system_config 表中的缓存键 ──

_CACHE_KEY = "ai_pricing_cache"


# ── 内置默认定价（抓取失败时的回退，覆盖 DeepSeek / OpenAI / Anthropic） ──

_FALLBACK_PRICING: dict[str, dict] = {
    # DeepSeek
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.14, "output": 0.28},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "o1": {"input": 15.00, "output": 60.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
    # Anthropic
    "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "_default": {"input": 0.435, "output": 0.87},
}


_MODEL_ALIASES = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
}


class PricingFetcher:
    """DeepSeek 模型定价获取器（惰性抓取 + 本地缓存）。"""

    def __init__(self):

        self._pricing: dict[str, dict] = {}

        self._fetched_at: float = 0.0

    @staticmethod
    def _load_cache_json() -> dict | None:
        """从 MySQL system_config 表读取定价缓存 JSON。失败返回 None。"""

        try:

            from src.core.db_manager import get_connection

            conn = get_connection()

            try:

                row = conn.execute(
                    "SELECT `value` FROM system_config WHERE `key`=%s",
                    (_CACHE_KEY,),
                ).fetchone()

            finally:

                try:
                    conn.close()

                except Exception as _pass_e:

                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

            if not row:

                return None

            return json.loads(row["value"])

        except Exception as e:

            _log.warning("定价缓存加载失败: %s", e)

            return None

    @staticmethod
    def _save_cache_json(cache_data: dict) -> None:
        """将定价缓存 JSON 写入 MySQL system_config 表。"""

        try:

            from src.core.db_manager import get_connection

            conn = get_connection()

            try:

                conn.execute(
                    "INSERT INTO system_config (`key`, `value`, `description`) "
                    "VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE `value`=%s, `updated_at`=CURRENT_TIMESTAMP",
                    (
                        _CACHE_KEY,
                        json.dumps(cache_data, ensure_ascii=False),
                        "AI 模型定价缓存（DeepSeek 官网抓取，24h TTL）",
                        json.dumps(cache_data, ensure_ascii=False),
                    ),
                )

                conn.commit()

            finally:

                try:
                    conn.close()

                except Exception as _pass_e:

                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

        except Exception as e:

            _log.warning("定价缓存保存异常: %s", str(e)[:80])

            pass

    def get_price(self, model_name: str) -> dict:
        """获取指定模型的定价（美元/百万Token）。"""

        self._ensure_loaded()

        resolved = _MODEL_ALIASES.get(model_name, model_name)

        if resolved in self._pricing:

            return dict(self._pricing[resolved])

        if model_name in self._pricing:

            return dict(self._pricing[model_name])

        for key in sorted(self._pricing.keys(), key=len, reverse=True):

            if key != "_default" and model_name.startswith(key):

                return dict(self._pricing[key])

        return dict(self._pricing.get("_default", _FALLBACK_PRICING["_default"]))

    def refresh(self) -> bool:

        return self._fetch_and_cache()

    def get_all(self) -> dict[str, dict]:

        self._ensure_loaded()

        return dict(self._pricing)

    def _ensure_loaded(self):

        if self._pricing:

            return

        if self._try_load_cache():

            return

        if self._fetch_and_cache():

            return

        self._pricing = dict(_FALLBACK_PRICING)

        self._fetched_at = time.time()

    def _try_load_cache(self) -> bool:

        try:

            data = self._load_cache_json()

            if not data:

                return False

            age = time.time() - data.get("fetched_at", 0)

            if age > _CACHE_TTL:

                return False

            self._pricing = data.get("pricing", {})

            self._fetched_at = data.get("fetched_at", 0)

            return bool(self._pricing)

        except Exception as e:
            _log.warning("定价缓存加载异常: %s", e, exc_info=True)
            return False

    def _fetch_and_cache(self) -> bool:

        try:

            pricing = self._fetch_pricing_page()

            if not pricing:

                return False

            self._pricing = pricing

            self._fetched_at = time.time()

            self._save_cache()

            return True

        except Exception as e:
            _log.warning("定价缓存加载异常: %s", e, exc_info=True)
            return False

    def _save_cache(self):

        try:

            # ★ 与 _fetch_pricing_page 保持一致：使用 config 配置的 URL

            try:

                from src.core.config_loader import config

                source_url = (
                    config.deepseek_pricing_url()
                    or "https://api-docs.deepseek.com/quick_start/pricing"
                )

            except Exception:
                _log.warning("读取DeepSeek定价URL配置失败", exc_info=True)

                source_url = "https://api-docs.deepseek.com/quick_start/pricing"

            cache_data = {
                "fetched_at": self._fetched_at,
                "fetched_ts": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(self._fetched_at)
                ),
                "source": source_url,
                "pricing": self._pricing,
            }

            self._save_cache_json(cache_data)

        except Exception as e:

            _log.warning("定价缓存保存异常: %s", str(e)[:80])

            pass

    @staticmethod
    def _fetch_pricing_page() -> dict[str, dict]:

        # ★ 修复：使用 config.deepseek_pricing_url() 可配置项，而非硬编码 URL

        try:

            from src.core.config_loader import config

            url = config.deepseek_pricing_url()

        except Exception:
            _log.warning("读取DeepSeek定价URL配置失败", exc_info=True)

            url = "https://api-docs.deepseek.com/quick_start/pricing"

        if not url:

            url = "https://api-docs.deepseek.com/quick_start/pricing"

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        resp = requests.get(url, headers=headers, timeout=15)

        resp.raise_for_status()

        text = resp.text

        table_match = re.search(
            r"<table[^>]*>(.*?)</table>", text, re.DOTALL | re.IGNORECASE
        )

        if not table_match:

            return {}

        table_html = table_match.group(1)

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL | re.IGNORECASE)

        if len(rows) < 2:

            return {}

        header_cells = re.findall(
            r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], re.DOTALL | re.IGNORECASE
        )

        models = []

        for cell in header_cells:

            clean = re.sub(r"<[^>]+>", "", cell).strip()

            found = re.findall(r"(deepseek[-\w]+)", clean, re.IGNORECASE)

            if found:

                models.append(found[0].lower())

            elif clean.lower().startswith("model"):

                models.append("")

        if not models or len(models) < 2:

            return {}

        pricing: dict[str, dict] = {}

        for m in models[1:]:

            if m:

                pricing[m] = {"input": 0.0, "output": 0.0, "cache_hit_discount": 0.1}

        for row in rows[1:]:

            cells = re.findall(
                r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL | re.IGNORECASE
            )

            if len(cells) < 2:

                continue

            row_label = re.sub(r"<[^>]+>", "", cells[0]).strip().lower()

            is_input = "input" in row_label and "cache miss" in row_label

            is_output = "output" in row_label

            if not (is_input or is_output):

                continue

            for i in range(1, min(len(cells), len(models))):

                model_name = models[i] if i < len(models) else ""

                if not model_name or model_name not in pricing:

                    continue

                cell_text = re.sub(r"<[^>]+>", "", cells[i]).strip()

                price = PricingFetcher._extract_price(cell_text)

                if price is not None:

                    if is_output:

                        pricing[model_name]["output"] = price

                    elif is_input:

                        pricing[model_name]["input"] = price

        pricing = {
            k: v for k, v in pricing.items() if v["input"] > 0 and v["output"] > 0
        }

        if pricing:

            first_model = next(iter(pricing.values()))

            pricing["_default"] = {
                "input": first_model["input"],
                "output": first_model["output"],
            }

        return pricing

    @staticmethod
    def _extract_price(cell_text: str) -> float | None:

        prices = re.findall(r"\$([\d.]+)", cell_text)

        if not prices:

            return None

        vals = [float(p) for p in prices]

        return min(vals)


pricing_fetcher = PricingFetcher()
