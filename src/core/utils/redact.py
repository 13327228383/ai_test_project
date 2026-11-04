# -*- coding: utf-8 -*-
"""通用敏感信息脱敏（单一真源，禁止各处另写）。

用途（2026-09-03 新增，安全加固第一层）:
  - 报告 / 日志 / 错误回显常会打印连接串、密钥、token。若明文外泄即使引擎代码被
    反编译也无秘密可拿（secret 本身存 MySQL，不落代码）。本模块提供一个幂等的
    通用脱敏器：凡命中常见 secret 形态（OpenAI/Ark/JWT 令牌、password/token/
    secret/apiKey=value、长随机串）一律替换为 ``***``。

设计约束:
  - 纯标准库，零依赖，可被单测零成本导入。
  - redact(str) 是纯函数、幂等、不 raise。
  - logging_factory 提供一个 logging.Filter，可挂到任意 logger/root，对消息做站内脱敏。
  - 只做"形态"识别，不做任何业务假设 → 跨系统通用，不认靶子。
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
import re
from typing import Any
import logging

_log = logging.getLogger(__name__)

_MASK = "***"

#: 常见 bearer 令牌形态（OpenAI sk- / Ark ark- / JWT eyJ.. / 长随机）
_RE_BEARER = re.compile(
    r"\b(?:(?:sk|skproj|sk-proj)[-_]?[A-Za-z0-9]{12,}|"
    r"ark-[0-9a-fA-F-]{8,}-[0-9a-fA-F]{6,}|"
    r"eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}|"
    r"AK[A-Za-z0-9]{16,})\b"
)

#: key=value 形态的敏感键（password/secret/token/api key/authorization…）
_RE_KEYVAL = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|credential|api[ _-]?key|access[ _-]?token|"
    r"auth[ _-]?token|token|authorization)\b\s*(?::|=)\s*"
    r"[^\s,;&\"'|]{3,}"
)

#: 长随机串（>=24 位 hex / base64 / URLSafe 明文），多为密钥或 hash
_RE_LONGRANDOM = re.compile(r"\b[A-Za-z0-9_\-+/=]{24,}\b")


def redact(text: Any) -> str:
    """对任意可字符串化文本做敏感脱敏；非 str 先 str()；异常兜底原样。"""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    try:
        out = _RE_BEARER.sub(_MASK, text)
        out = _RE_KEYVAL.sub(lambda m: _MASK, out)
        out = _RE_LONGRANDOM.sub(_MASK, out)
        return out
    except Exception:
        return text


def redact_url_query(url: str) -> str:
    """对 URL query 中敏感参数值做脱敏（token/api_key/signature 等）。"""
    if not url:
        return url or ""
    try:
        return re.sub(
            r"(?i)(token|api_key|apikey|secret|signature|password|key)=(?P<v>[^&#]+)",
            r"\1=***",
            url,
        )
    except Exception:
        return url


def make_sensitive_filter() -> logging.Filter:
    """构造一个 logging.Filter：对 record.msg 与其 args 中的敏感形态就地脱敏。

    用法（引擎启动最早处挂到 root，或用 logging.getLogger(...).addFilter）::

        logging.getLogger().addFilter(make_sensitive_filter())

    说明：Filter.filter 允许原位改写 record.msg / record.args 且开销极小；
    msg 为格式化串时按 record.args 对应的消息定位。此处对已 format(record.getMessage())
    不可行（会双次 %），故仅对 msg 本身与 args 的字符串元素脱敏，覆盖 str(obj) 场景。
    """
    class _SensitiveFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            with guard("filter: if isinstance(record.msg, str): reco", log=_log):
                if isinstance(record.msg, str):
                    record.msg = redact(record.msg)
                if record.args:
                    if isinstance(record.args, dict):
                        record.args = {
                            k: (redact(v) if isinstance(v, str) else v)
                            for k, v in record.args.items()
                        }
                    else:
                        record.args = tuple(
                            redact(a) if isinstance(a, str) else a for a in record.args
                        )
            return True

    return _SensitiveFilter()