# -*- coding: utf-8 -*-
from __future__ import annotations

"""AI 响应解析"""

import json
import logging

_log = logging.getLogger(__name__)


def _parse_ai_response(raw: str) -> list[dict] | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    for attempt in range(4):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "action" in data:
                return [data]
            return None
        except json.JSONDecodeError as e:
            if (
                attempt == 0
                and "Extra data" in str(e)
                and hasattr(e, "pos")
                and e.pos > 0
            ):
                raw = raw[: e.pos]
                _log.info("    [AI] Extra data 截断修复: pos=%d", e.pos)
            elif attempt == 1:
                start, end = raw.find("["), raw.rfind("]")
                if start != -1 and end != -1 and end > start:
                    raw = raw[start : end + 1]
            elif attempt == 2:
                start, end = raw.find("{"), raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    raw = raw[start : end + 1]
            else:
                break
    _log.info("    [AI] 无法解析响应: %s", raw[:120])
    return None
