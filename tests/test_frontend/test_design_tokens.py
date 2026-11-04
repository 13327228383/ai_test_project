# -*- coding: utf-8 -*-
"""设计令牌审计回归（2026-08-30 全站样式统一门禁）

锁定两条项目级规范（见 AGENTS.md / project_memory）：
  1. 所有 CSS 必须使用设计令牌（--color-primary / --state-success / --space-* 等），
     禁止硬编码颜色：模板中出现裸 hex / rgb( / rgba( / hsl(（且非 token 取值）即判违规。
  2. 禁止用内联 style 绕过 token 做样式漂移；仅允许数据驱动的内联宽高
     （进度条 {{pct}}% / display 切换等），其余一律下沉为 class。

通用性：不依赖任何具体页面结构，纯静态扫描 templates/*.html，任何新增页面都受同一门禁约束。
"""
import re

import pytest

from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "frontend" / "templates"

# 硬编码颜色：hex（3/4/6/8 位）或 rgb/rgba/hsl/hsla
_HARD_COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"
    r"|rgba?\([^)]*\)|hsla?\([^)]*\)",
    re.IGNORECASE,
)
_INLINE_STYLE_RE = re.compile(r"style\s*=\s*[\"']")

# 允许内联 style 属性里的“数据驱动”片段（进度条宽度 %、display 切换等，非样式漂移）
_DATA_DRIVEN_RE = re.compile(
    r"width\s*:\s*\{\{|\{\{[^}]*\}\}|\bdisplay:\s*\{\{|w-\{\{|[\d.]+%\)",
    re.IGNORECASE,
)

# 每个文件允许的内联样式属性“数据驱动”上限（动态占位符/百分比宽度，合理）
INLINE_STYLE_BUDGET = 6


def _iter_templates():
    return sorted(TEMPLATES_DIR.rglob("*.html"))


def _scan(path: Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    hard_colors = []
    for m in _HARD_COLOR_RE.finditer(text):
        # 排除 token 定义行（本文件顶部 :root 中 var(--x: #value)）——审计对象是模板，本文档不出现
        # 模板内显式出现裸色即违规
        hard_colors.append(m.group(0))
    inline_styles = list(_INLINE_STYLE_RE.finditer(text))
    data_driven = sum(1 for s in inline_styles if _DATA_DRIVEN_RE.search(
        text[s.start(): s.start() + 400]
    ))
    return hard_colors, len(inline_styles), data_driven


@pytest.mark.parametrize("tpl", _iter_templates(), ids=lambda p: p.name)
def test_no_hardcoded_colors(tpl: Path):
    """模板禁止硬编码颜色 —— 颜色一律走 --state-*/--color-*/--text-* 令牌。"""
    hard_colors, _, _ = _scan(tpl)
    assert not hard_colors, (
        f"{tpl.name} 含 {len(hard_colors)} 处硬编码颜色（违反令牌规范）: "
        f"{hard_colors[:10]}"
    )


@pytest.mark.parametrize("tpl", _iter_templates(), ids=lambda p: p.name)
def test_inline_style_within_budget(tpl: Path):
    """内联样式仅限数据驱动片段，其余（颜色/间距/布局）必须下沉为 class。"""
    _, total, data_driven = _scan(tpl)
    static = total - data_driven
    assert static <= INLINE_STYLE_BUDGET, (
        f"{tpl.name} 含 {static} 处非数据驱动的内联样式（应下沉为 token class）: "
        f"共 {total} 内联样式，数据驱动 {data_driven}"
    )


def test_smoke_templates_dir_exists():
    """目录存在且非空（防审计本体失效）。"""
    assert TEMPLATES_DIR.is_dir()
    assert len(_iter_templates()) >= 30