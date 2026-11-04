# -*- coding: utf-8 -*-
"""
智能分段引擎 (Smart Segmenter)
==============================
按自然语言边界分段，禁止硬截断。

分段优先级（逐级降级）：
  1. \\n\\n（段落边界）
  2. \\n（行边界）
  3. 。！？. ! ?（句子边界）
  4. , ，、：: ; ；（短语边界）
  5. 丢弃剩余（禁止硬截断）

★ 统一功能点提取：extract_feature_points() 可用于 Requirement/Figma/Upload 等场景。
★ 零硬编码：max_len 由调用方传入，min_segment_len=100 为类常量。
"""

import re
import logging

_log = logging.getLogger("rag_engine")

# 句子结束符（中英文）
_SENTENCE_ENDS = re.compile(r"[。！？.!?]")
# 短语分隔符（中英文）
_PHRASE_SEPS = re.compile(r"[,，、：:;；]")
# 最小分段长度（太短则合并到前一段）
_MIN_SEGMENT_LEN = 100

# ══════════════════════════════════════════════════════════
# 统一功能点提取器 — 适用于 Requirement / Figma / Upload / UI Context
# ══════════════════════════════════════════════════════════

# 功能实体关键词（通用，不写死业务词）
_FEATURE_TYPES = [
    "按钮",
    "输入框",
    "表格",
    "列表",
    "菜单",
    "标签页",
    "弹窗",
    "对话框",
    "表单",
    "字段",
    "下拉",
    "选择器",
    "开关",
    "复选框",
    "单选框",
    "搜索",
    "筛选",
    "排序",
    "分页",
    "导入",
    "导出",
    "上传",
    "下载",
    "新增",
    "删除",
    "编辑",
    "查看",
    "审批",
    "提交",
    "取消",
    "确认",
    "保存",
    "重置",
    "刷新",
    "回退",
    "button",
    "input",
    "table",
    "list",
    "menu",
    "tab",
    "dialog",
    "form",
    "field",
    "select",
    "checkbox",
    "radio",
    "search",
]

# 预编译所有 feat_type 的正则模式（模块级，避免双重循环内重复 compile）
_FEATURE_PATTERNS = [
    (ft, re.compile(r"([^\s，,。；;]{0,15}?" + re.escape(ft) + r")", re.IGNORECASE))
    for ft in _FEATURE_TYPES
]

# 大纲标题模式（带层级，单一权威定义：切片与功能点提取共用）
#   md: #/##/###… → 层级 = # 个数
#   中文: 第X章/第X节 → 章=1 节=2
#   数字: 1. / 1.2 / 1.2.3 → 层级 = 点号数 + 1
#   方括号: 【标题】 → 层级 1
_HEADING_PATTERN = re.compile(
    r"^[ \t]*"
    r"(?:"
    r"(?P<md>#{1,6})[ \t]+(?P<md_title>[^\n]+)"
    r"|(?P<cn>第[一二三四五六七八九十百零两0-9]{1,6}[章节部分])[ \t:：]*(?P<cn_title>[^\n]*)"
    r"|(?P<num>[0-9]{1,3}(?:\.[0-9]{1,3})*)[ \t]+(?P<num_title>[^\n]+)"
    r"|(?P<bracket>【[^】\n]{1,30}】)[ \t:：]*(?P<bracket_title>[^\n]*)"
    r")",
    re.MULTILINE,
)


def _heading_level(match) -> int:
    """标题层级（1 起）。用于大纲树递归切分。"""
    if match.group("md"):
        return len(match.group("md"))
    if match.group("cn"):
        return 1 if ("章" in match.group("cn") or "部分" in match.group("cn")) else 2
    if match.group("num"):
        return match.group("num").count(".") + 1
    return 1


def _heading_title(match) -> str:
    for group in ("md_title", "cn_title", "num_title", "bracket_title"):
        value = match.group(group)
        if value:
            return value.strip()
    return ""

# 页面/区域标题模式（必须独占一行，避免正文里的"模块/列表"字样被误判为标题）
_REGION_PATTERN = re.compile(
    r"(?:^|\n)[ \t]*"
    r"(?:##\s+|【|】\s*|-+\s*|\*\*\s*)?"
    r"([^\n]{2,30}?(?:区域|模块|分组|面板|表单|列表|表格|工具栏|菜单栏|导航|侧边栏|顶栏|头部|底部|弹窗|对话框|卡片|区块|section|group|panel))"
    r"[ \t]*(?:[:：][ \t]*)?(?=\n|$)",
    re.IGNORECASE | re.MULTILINE,
)


def extract_feature_points(
    text: str, max_chars: int = 2500, focus_keyword: str = ""
) -> str:
    """统一功能点提取器 — 模拟人类提炼重点，禁止硬截断。

    适用于：
    - Requirement chunk：提取操作+预期功能点
    - Figma page chunk：提取UI功能点
    - Upload切片：提取文档功能点
    - UI Context摘要：提取页面功能点

    策略：
    1. 按大纲/区域标题分组
    2. 提取每组的关键功能点（按钮/输入框/表格等）
    3. 去重、合并同类
    4. 有focus_keyword时优先显示相关组
    5. 超长时丢弃低优先级内容，而非硬截断
    """
    if not text or not text.strip():
        return ""

    lines = text.split("\n")
    groups = {}  # group_name -> list of features
    current_group = "全局"
    group_order = []  # 保持顺序

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 识别大纲/区域标题
        heading_match = _HEADING_PATTERN.match(line)
        region_match = _REGION_PATTERN.match(line)
        if heading_match or region_match:
            # 提取标题文本
            if region_match:
                current_group = region_match.group(1).strip()
            else:
                # 大纲标题：取标题正文（带层级正则统一解析）
                current_group = (_heading_title(heading_match) or line.strip())[:30]
            if current_group not in groups:
                groups[current_group] = []
                group_order.append(current_group)
            continue

        # 提取功能点
        for feat_type, pattern in _FEATURE_PATTERNS:
            for m in pattern.finditer(line):
                feat = m.group(1)
                if current_group not in groups:
                    groups[current_group] = []
                    group_order.append(current_group)
                if feat not in groups[current_group]:
                    groups[current_group].append(feat)

    # 如果没提取到任何功能点，降级到段落级摘要
    if not any(groups.values()):
        paragraphs = [x for p in re.split(r"\n\s*\n", text) if (x := p.strip())]
        result_parts = []
        for para in paragraphs:
            # ★ 先切分段落，再检查长度（避免长段落直接被丢弃）
            if len(para) > 500:
                sub_segs = SmartSegmentMixin._smart_segment(para, max_len=500)
                para = sub_segs[0] if sub_segs else ""
            if not para:
                continue
            if len("\n".join(result_parts)) + len(para) > max_chars:
                break  # 丢弃剩余，禁止硬截断
            result_parts.append(para)
        return "\n".join(result_parts).strip()

    # 按focus_keyword排序优先级
    ordered_groups = []
    if focus_keyword:
        for gname in group_order:
            if focus_keyword in gname or gname in focus_keyword:
                ordered_groups.append((gname, True))
        for gname in group_order:
            if not (focus_keyword in gname or gname in focus_keyword):
                ordered_groups.append((gname, False))
    else:
        ordered_groups = [(g, False) for g in group_order]

    # 构建摘要（超长时丢弃低优先级内容）
    result_lines = []
    current_len = 0
    for gname, is_focus in ordered_groups:
        feats = groups.get(gname, [])
        if not feats:
            continue
        prefix = "★ " if is_focus else "  "
        feat_line = f"{prefix}[{gname}] " + "、".join(feats[:10])
        if len(feats) > 10:
            feat_line += f" 等{len(feats)}项"

        if current_len + len(feat_line) > max_chars:
            break  # 丢弃剩余，不截断
        result_lines.append(feat_line)
        current_len += len(feat_line) + 1

    return "\n".join(result_lines) if result_lines else ""


class SmartSegmentMixin:
    """智能分段能力。"""

    @staticmethod
    def _smart_segment(
        text: str, max_len: int = 2000, min_segment_len: int = _MIN_SEGMENT_LEN
    ) -> list[str]:
        """
        将文本按自然边界智能分段，每段不超过 max_len。
        优先自然边界；所有自然边界都不可用时按 max_len 兜底切分 —— 保证内容不丢失。
        """
        if not text:
            return []
        text = str(text)
        if len(text) <= max_len:
            return [text]

        segments = []

        # ── Level 1: 段落边界 ──
        paragraphs = text.split("\n\n")
        current = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            candidate = (current + "\n\n" + para) if current else para
            if len(candidate) <= max_len:
                current = candidate
            else:
                if current:
                    segments.append(current)
                if len(para) <= max_len:
                    current = para
                else:
                    sub_segs = SmartSegmentMixin._split_long_block(para, max_len)
                    if sub_segs:
                        *full_segs, current = sub_segs
                        segments.extend(full_segs)
                    else:
                        current = ""

        if current:
            segments.append(current)

        segments = SmartSegmentMixin._merge_short_segments(
            segments, min_segment_len, max_len
        )

        # 兜底：任何边界都不可用时按 max_len 切分，保证内容不丢失
        return segments if segments else SmartSegmentMixin._hard_cut(text, max_len)

    @staticmethod
    def _hard_cut(text: str, max_len: int) -> list[str]:
        """最后兜底：按 max_len 硬切。仅在无任何自然边界时调用，保证零丢失。"""
        if not text or max_len <= 0:
            return []
        _log.debug("[Segment] 无自然边界，按 %d 字兜底切分: %d 字符", max_len, len(text))
        return [text[i : i + max_len] for i in range(0, len(text), max_len)]

    @staticmethod
    def _split_long_block(text: str, max_len: int) -> list[str]:
        """逐级降级切分超长文本块：换行 → 句子 → 短语 → 兜底硬切。"""
        if not text or not text.strip():
            return []

        # ── Level 2: 按换行切 ──
        lines = text.split("\n")
        line_segs = []
        current_line = ""

        for line in lines:
            line = line.rstrip()
            if not line:
                if current_line:
                    line_segs.append(current_line)
                    current_line = ""
                continue
            candidate = (current_line + "\n" + line) if current_line else line
            if len(candidate) <= max_len:
                current_line = candidate
            else:
                if current_line:
                    line_segs.append(current_line)
                if len(line) <= max_len:
                    current_line = line
                else:
                    if current_line:
                        line_segs.append(current_line)
                        current_line = ""
                    # 降级到句子切分
                    sentence_parts = SmartSegmentMixin._split_by_sentence(line, max_len)
                    if sentence_parts:
                        *full, maybe_last = sentence_parts
                        line_segs.extend(full)
                        current_line = maybe_last
                    else:
                        # 句子/短语边界都不可用 → 兜底硬切，保证该行不丢失
                        line_segs.extend(
                            SmartSegmentMixin._hard_cut(line, max_len)
                        )

        if current_line:
            line_segs.append(current_line)

        return line_segs

    @staticmethod
    def _split_by_sentence(text: str, max_len: int) -> list[str]:
        """按句子边界切分。找不到边界时降级到短语，仍找不到则丢弃。"""
        if len(text) <= max_len:
            return [text]

        segs = []
        remaining = text
        while remaining:
            if len(remaining) <= max_len:
                segs.append(remaining)
                break
            window = remaining[:max_len]
            best_pos = -1
            for m in _SENTENCE_ENDS.finditer(window):
                pos = m.end()
                if pos > max_len * 0.3:
                    best_pos = pos
            if best_pos > 0:
                segs.append(remaining[:best_pos])
                remaining = remaining[best_pos:].lstrip()
            else:
                # ── Level 4: 短语边界 ──
                sub = SmartSegmentMixin._split_by_phrase(remaining, max_len)
                segs.extend(sub)
                break
        return segs

    @staticmethod
    def _split_by_phrase(text: str, max_len: int) -> list[str]:
        """按短语分隔符切分；仍无边界时按 max_len 兜底切分，保证不丢失。"""
        if len(text) <= max_len:
            return [text]

        segs = []
        remaining = text
        while remaining:
            if len(remaining) <= max_len:
                segs.append(remaining)
                break
            window = remaining[:max_len]
            best_pos = -1
            for m in _PHRASE_SEPS.finditer(window):
                pos = m.end()
                if pos > max_len * 0.3:
                    best_pos = pos
            if best_pos > 0:
                segs.append(remaining[:best_pos])
                remaining = remaining[best_pos:].lstrip()
            else:
                # 找不到任何自然边界 → 兜底硬切（禁止丢内容）
                segs.extend(SmartSegmentMixin._hard_cut(remaining, max_len))
                break
        return segs

    @staticmethod
    def _merge_short_segments(
        segments: list[str], min_len: int, max_len: int
    ) -> list[str]:
        """合并过短的分段到前一段。"""
        if not segments or len(segments) <= 1:
            return segments

        result = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            if len(seg) < min_len and result:
                merged = result[-1] + "\n" + seg
                if len(merged) <= max_len:
                    result[-1] = merged
                else:
                    result.append(seg)
            else:
                result.append(seg)
        return result

    @staticmethod
    def _segment_figma_page(text: str, max_len: int = 1500) -> list[str]:
        """Figma页面专用分段：优先按功能区域拆分，禁止硬截断。"""
        if not text or not text.strip():
            return []

        # Level 1: 按区域标题/分组拆分
        matches = list(_REGION_PATTERN.finditer(text))
        if len(matches) >= 2:
            segments = []
            for i, m in enumerate(matches):
                start = m.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                seg = text[start:end].strip()
                if seg:
                    if len(seg) > max_len:
                        sub_segs = SmartSegmentMixin._smart_segment(
                            seg, max_len=max_len
                        )
                        segments.extend(sub_segs)
                    else:
                        segments.append(seg)
            if segments:
                _log.debug("[FigmaSegment] 按区域拆分: %d 段", len(segments))
                return segments

        # Level 2: 降级到智能分段（已禁止硬截断）
        return SmartSegmentMixin._smart_segment(text, max_len=max_len)

    @staticmethod
    def _segment_by_outline(text: str, max_len: int = 500) -> list[str]:
        """按标题层级递归切分（外层章节 → 子标题 → 自然边界兜底）。

        三条不变量：
          1. 覆盖：标题前导语、正文、尾段都必须落进某个 chunk，不丢内容；
          2. 不长于 max_len（除纯标题合并的极小溢出），避免嵌入截断盲区；
          3. 不产出"只有标题没有正文"的 chunk（并入下一段）。
        """
        if not text or not text.strip():
            return []

        matches = list(_HEADING_PATTERN.finditer(text))
        if not matches:
            return SmartSegmentMixin._smart_segment(text, max_len=max_len)

        segments: list[str] = []
        # 首个标题前的导语（旧实现直接丢弃 → 覆盖缺口）
        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                segments.extend(
                    SmartSegmentMixin._smart_segment(preamble, max_len=max_len)
                )

        # 只按最高层级切章节，子标题留给 _segment_section 递归
        top_level = min(_heading_level(m) for m in matches)
        tops = [m for m in matches if _heading_level(m) == top_level]
        for i, m in enumerate(tops):
            end = tops[i + 1].start() if i + 1 < len(tops) else len(text)
            section = text[m.start() : end].strip()
            if section:
                segments.extend(
                    SmartSegmentMixin._segment_section(section, max_len=max_len)
                )

        folded = SmartSegmentMixin._fold_heading_only(segments)
        _log.debug(
            "[OutlineSegment] 层级=%d 章节=%d → %d 段", top_level, len(tops), len(folded)
        )
        return folded

    @classmethod
    def _segment_section(cls, section: str, max_len: int = 500) -> list[str]:
        """递归切分单个章节：超长时先按子标题切，无子标题再按自然边界降级。"""
        section = section.strip()
        if not section:
            return []
        if len(section) <= max_len:
            return [section]

        inner = [m for m in _HEADING_PATTERN.finditer(section) if m.start() > 0]
        if not inner:
            return cls._smart_segment(section, max_len=max_len)

        child_level = min(_heading_level(m) for m in inner)
        children = [m for m in inner if _heading_level(m) == child_level]

        parts: list[str] = []
        # 本章节自身标题到第一个子标题之间的正文
        head_part = section[: children[0].start()].strip()
        if head_part:
            parts.append(head_part)
        for i, m in enumerate(children):
            end = children[i + 1].start() if i + 1 < len(children) else len(section)
            parts.extend(
                cls._segment_section(section[m.start() : end], max_len=max_len)
            )
        return parts

    @staticmethod
    def _is_heading_only(segment: str) -> bool:
        """整段只有标题行、没有正文。"""
        lines = [ln.strip() for ln in segment.split("\n") if ln.strip()]
        return bool(lines) and all(_HEADING_PATTERN.match(ln) for ln in lines)

    @staticmethod
    def _fold_heading_only(segments: list[str]) -> list[str]:
        """把"只有标题"的片段并到下一段（末尾则并到前一段），避免空 chunk。"""
        result: list[str] = []
        pending = ""
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            if SmartSegmentMixin._is_heading_only(seg):
                pending = (pending + "\n\n" + seg).strip()
                continue
            result.append((pending + "\n\n" + seg).strip() if pending else seg)
            pending = ""
        if pending:
            if result:
                result[-1] = (result[-1] + "\n\n" + pending).strip()
            else:
                result.append(pending)
        return result
