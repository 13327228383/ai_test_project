# -*- coding: utf-8 -*-
"""数值容错工具 —— 全仓 ``_safe_float`` 的**单一真源**（整改 B3.1）。

背景
----
整改前全仓存在 3 份 ``_safe_float``，且**签名并不一致**（这一点设计文档未识别）：

===================================================  ==========================
位置                                                  签名
===================================================  ==========================
``src/extensions/perf_baseline/schemas.py:70``        ``_safe_float(value, default=0.0)``   值级
``src/backend/routes/case_review/_routes.py:149``     ``_safe_float(c, key)``               字典级
``src/pipeline/utils/case_store/crud/read.py:299``    ``_safe_float(c, key)``               字典级
===================================================  ==========================

因此本模块提供**两个**函数而非一个，分别承接上述两种调用形态，
避免把字典级调用强行塞进值级签名造成静默错误：

- :func:`safe_float`    —— 值级：``safe_float(v, default=0.0)``
- :func:`safe_float_of` —— 字典级：``safe_float_of(mapping, key, default=0.0)``

行为约定
--------
- 永不 raise：``None`` / 空串 / 非数值 / ``NaN`` / ``inf`` 一律回落 ``default``；
- 无 IO、无第三方依赖、不 import 项目内其它模块 —— 保证可被任何层零成本引用，
  且**不会产生循环导入**（``src.core.utils`` 是依赖图叶子节点）。

.. note::
   **相对整改前的行为差异（已知且有意）**：原两处字典级实现对 ``NaN`` / ``inf``
   会原样返回（``float("nan")`` 不抛异常），本模块统一回落 ``default``。
   这是修正而非退化 —— ``NaN`` 参与 ``sum()`` / ``round()`` 会污染整条统计链路。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["safe_float", "safe_float_of", "_safe_float"]


def safe_float(value: Any, default: float = 0.0) -> float:
    """宽容转 float（值级）。

    参数：
        value: 任意值，允许为 ``None`` / 字符串 / 数值 / 不可转换对象。
        default: 转换失败时的回落值，默认 ``0.0``。

    返回：
        转换后的 float；``None`` / 空串 / 非数值 / ``NaN`` / ``inf`` 一律返回 ``default``。
    """
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(num) or math.isinf(num):
        return default
    return num


def safe_float_of(mapping: Any, key: str, default: float = 0.0) -> float:
    """从映射中取键并宽容转 float（字典级）。

    等价于 ``safe_float(mapping.get(key), default)``，但额外容忍
    ``mapping`` 本身不是映射（无 ``get`` 方法）的情况。

    参数：
        mapping: 任意支持 ``.get(key)`` 的对象；不支持时直接返回 ``default``。
        key: 待取的键名。
        default: 取值失败或转换失败时的回落值，默认 ``0.0``。

    返回：
        转换后的 float，永不 raise。
    """
    if mapping is None:
        return default
    getter = getattr(mapping, "get", None)
    if not callable(getter):
        return default
    try:
        raw = getter(key)
    except (TypeError, ValueError, KeyError):
        return default
    return safe_float(raw, default)


#: 向后兼容别名 —— 各调用点保留原有下划线私名，调用处零改动。
_safe_float = safe_float
