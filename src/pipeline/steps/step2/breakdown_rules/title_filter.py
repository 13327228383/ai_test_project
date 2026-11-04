# -*- coding: utf-8 -*-
"""模块概述拆解 + 标题过滤规则：清洗阶段。

★ Fix54 (2026-07-09): 初版 — 简单过滤标题类需求
★ Fix55b (2026-07-09): 升级 — "总体说明"类不再过滤，而是拆解为多个原子操作
  问题: "总体说明：用户可在本模块查询辅材信息，并进行批量导入、单个新建、单个编辑操作。"
        包含 4 个原子操作，简单过滤会丢失有价值的需求。
  策略:
    1. "总体说明"类 → 提取操作关键词，拆解为多个原子操作三元组
    2. 纯标题类（"查询条件"、"列表字段"等）→ 仍然过滤
"""

import re
import logging
from typing import Any
from .base import CleanRule

# ★ v25 根因Z3 修复(2026-07-21): import _infer_success_hint 用于拆解后重新推断 expected_feedback
from .numbered_list import _infer_success_hint

_log = logging.getLogger(__name__)


# ── 操作动词 → 语义类别映射（通用，无业务硬编码）──
_ACTION_VERB_TO_OP = {
    "查询": "查询{obj}",
    "搜索": "搜索{obj}",
    "筛选": "筛选{obj}",
    "检索": "检索{obj}",
    "查找": "查找{obj}",
    "批量导入": "批量导入{obj}",
    "导入": "导入{obj}",
    "上传": "上传{obj}",
    "单个新建": "单个新建{obj}",
    "新建": "新建{obj}",
    "新增": "新增{obj}",
    "添加": "添加{obj}",
    "创建": "创建{obj}",
    "单个编辑": "单个编辑{obj}",
    "编辑": "编辑{obj}",
    "修改": "修改{obj}",
    "更新": "更新{obj}",
    "删除": "删除{obj}",
    "移除": "移除{obj}",
    "导出": "导出{obj}",
    "下载": "下载{obj}",
    "保存": "保存{obj}",
    "提交": "提交{obj}",
    "确认": "确认{obj}",
    "取消": "取消{obj}",
    "复制": "复制{obj}",
    "审核": "审核{obj}",
    "审批": "审批{obj}",
    "启用": "启用{obj}",
    "停用": "停用{obj}",
    "打标": "打标{obj}",
    "标签": "打标签{obj}",
    "刷新": "刷新{obj}",
    "重置": "重置{obj}",
}

# 按长度降序排列（长词优先匹配，避免"导入"先于"批量导入"匹配）
_SORTED_ACTION_VERBS = sorted(_ACTION_VERB_TO_OP.keys(), key=len, reverse=True)

# 完整陈述句的句末标点：标题是片段，陈述句是需求，不得按"片段"特征过滤
_SENTENCE_END_RE = re.compile(r"[。！？；]\s*$")


class TitleFilterRule(CleanRule):
    """模块概述拆解 + 标题过滤。

    对"总体说明"类文本：提取操作关键词，拆解为多个原子操作。
    对纯标题类文本：过滤掉。
    """

    rule_id = "title_filter"
    name = "模块概述拆解与标题过滤"
    description = "总体说明类拆解为原子操作，纯标题类过滤"
    priority = 80

    # ── 可拆解的概述前缀（含"总体说明"等）──
    _OVERVIEW_PREFIXES = (
        "总体说明",
        "模块说明",
        "功能概述",
        "模块概述",
        "功能说明",
        "模块功能",
    )

    # ── 纯标题前缀（不可拆解，直接过滤）──
    _PURE_TITLE_PREFIXES = (
        "路径：",
        "路径:",
        "查询条件",
        "筛选条件",
        "搜索条件",
        "批量操作",
        "列表操作",
        "页面操作",
        "列表字段",
        "字段列表",
        "字段说明",
        "注意事项",
        "备注说明",
        "补充说明",
        "系统要求",
        "性能要求",
        "安全要求",
        "输入说明",
        "输出说明",
        "异常说明",
        "相关页面",
        "前置条件",
        "后置条件",
        "业务规则",
        "规则说明",
        "原型示例",
        "界面示意",
        "页面布局",
    )

    # ── 标题后缀 ──
    _TITLE_SUFFIXES = (
        "：",
        ":",
        "说明",
        "描述",
        "介绍",
        "概述",
        "如下",
        "列表",
    )

    # ── 动作动词集合（用于判断是否是真正的操作）──
    _ACTION_VERBS = set(_ACTION_VERB_TO_OP.keys()) | {
        "点击",
        "输入",
        "选择",
        "打开",
        "关闭",
        "查看",
        "浏览",
        "跳转",
        "切换",
        "粘贴",
        "剪切",
        "清空",
        "关联",
        "解除",
        "绑定",
        "解绑",
        "通过",
        "驳回",
        "系统",
        "自动",
        "校验",
        "验证",
        "检查",
        "展示",
        "显示",
        "出现",
        "弹出",
        "用户",
        "管理员",
    }

    def apply(self, triples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not triples:
            return []

        result = []
        removed = 0
        expanded = 0

        for t in triples:
            operation = (t.get("operation") or "").strip()

            # 先尝试拆解总体说明
            expanded_ops = self._try_expand_overview(operation, t)
            if expanded_ops is not None:
                # 成功拆解
                result.extend(expanded_ops)
                expanded += 1
                continue

            # 判断是否是纯标题
            if self._is_pure_title(operation, t.get("source_quote", "")):
                removed += 1
                continue

            # 保留有效需求
            result.append(t)

        if removed > 0 or expanded > 0:
            _log.info(
                "[TitleFilter] 过滤 %d 条纯标题，拆解 %d 条总体说明 → %d 条原子操作，最终 %d 条",
                removed,
                expanded,
                len(result) - (len(triples) - removed - expanded),
                len(result),
            )

        return result

    @classmethod
    def _try_expand_overview(
        cls, text: str, original: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """尝试将"总体说明"类文本拆解为多个原子操作。

        Returns:
            None: 不是总体说明类文本，交给后续逻辑处理
            []: 是总体说明类但无法拆解（返回空列表表示过滤）
            [triple1, triple2, ...]: 成功拆解的原子操作列表
        """
        if not text:
            return None

        # 检查是否是概述类前缀
        is_overview = False
        overview_text = text
        for prefix in cls._OVERVIEW_PREFIXES:
            if text.startswith(prefix):
                is_overview = True
                # 去掉前缀和冒号
                overview_text = text[len(prefix) :].lstrip("：: \t")
                break

        if not is_overview:
            return None

        # 提取操作关键词
        actions = cls._extract_actions_from_overview(overview_text)
        if not actions:
            # 是总体说明但没提取到操作 → 返回空列表（过滤）
            _log.debug("[TitleFilter] 总体说明无法拆解: %s", text[:60])
            return []

        # 提取操作对象（从上下文推断）
        obj = cls._infer_object(overview_text)

        # 构建原子操作三元组
        result = []
        seen_ops = set()
        for verb, op_template in actions:
            operation = (
                op_template.format(obj=obj) if "{obj}" in op_template else op_template
            )
            # 去重
            if operation in seen_ops:
                continue
            seen_ops.add(operation)

            triple = dict(original)  # 复制原始三元组
            triple["operation"] = operation
            triple["extraction_method"] = "overview_expand"
            # ★ v25 根因Z3 修复(2026-07-21): 根据新 operation 重新推断 expected_feedback
            #   原 bug: 复制 original 后只更新 operation，expected_feedback 保留原始值。
            #   原始 operation 是"总体说明：...查询...批量导入...单个新建...单个编辑..."
            #   含"新建"关键词 → _infer_success_hint 返回"新增成功,添加成功,创建成功,保存成功"
            #   拆解后 operation 变成"查询辅材"，但 expected_feedback 没跟着变 → 张冠李戴。
            #   根因链路(5层):
            #     L1 表层: 用例假PASS/FAIL（查询操作断言"新增成功"）
            #     L2 中层: Step3 checks 基于 expected_feedback 生成
            #     L3 根因层: expected_feedback 来自原始三元组，没随 operation 更新
            #     L4 设计层: _try_expand_overview 拆解时遗漏了 expected_feedback 同步
            #     L5 最深根因: 拆解升级时没同步更新所有依赖 operation 的字段
            #   通用性: 适用于所有总体说明拆解的需求（跨系统/跨行业/跨页面）
            #   覆盖性: 覆盖所有 overview_expand 的需求条目
            #   合规: AGENTS.md §10.8 禁止治标不治本 + §10.9 根因核查分析流程
            triple["expected_feedback"] = _infer_success_hint(operation)
            # 同步更新 description（description 含 expected_feedback 文本）
            _orig_desc = original.get("description", "")
            if _orig_desc and "系统预期反馈" in _orig_desc:
                _module = original.get("module", "")
                triple["description"] = (
                    f"在{_module}模块中，用户执行{operation}操作，"
                    f"系统预期反馈：{triple['expected_feedback']}"
                )
            triple["source_quote"] = original.get("source_quote", text)[:200]
            triple["confidence"] = max(0.6, original.get("confidence", 0.7) - 0.1)
            result.append(triple)

        if result:
            _log.info(
                "[TitleFilter] 拆解 '%s' → %d 条原子操作: %s",
                text[:50],
                len(result),
                ", ".join(t["operation"] for t in result),
            )

        return result

    @classmethod
    def _extract_actions_from_overview(cls, text: str) -> list[tuple[str, str]]:
        """从概述文本中提取操作关键词。

        如 "用户可在本模块查询辅材信息，并进行批量导入、单个新建、单个编辑操作。"
        → [("查询", "查询{obj}"), ("批量导入", "批量导入{obj}"), ("单个新建", "单个新建{obj}"), ("单个编辑", "单个编辑{obj}")]
        """
        actions = []
        matched_positions = []

        for verb in _SORTED_ACTION_VERBS:
            # 找所有出现位置
            start = 0
            while True:
                pos = text.find(verb, start)
                if pos < 0:
                    break
                # 检查是否与已匹配的词重叠
                verb_end = pos + len(verb)
                overlap = any(
                    not (verb_end <= mp_start or pos >= mp_end)
                    for mp_start, mp_end in matched_positions
                )
                if not overlap:
                    actions.append((verb, _ACTION_VERB_TO_OP[verb]))
                    matched_positions.append((pos, verb_end))
                start = pos + 1

        # 按出现位置排序
        actions.sort(key=lambda x: text.find(x[0]))

        return actions

    @classmethod
    def _infer_object(cls, text: str) -> str:
        """从概述文本中推断操作对象。

        如 "用户可在本模块查询辅材信息" → "辅材"
        """
        # 尝试提取"XX信息"中的XX（取最短匹配，避免"模块查询辅材"这种误匹配）
        matches = re.findall(r"([\u4e00-\u9fa5]{2,4})信息", text)
        if matches:
            obj = min(matches, key=len)
            # 去掉对象中的操作动词（避免"查询辅材"→"查询查询辅材"）
            for verb in _SORTED_ACTION_VERBS:
                if obj.startswith(verb):
                    obj = obj[len(verb) :]
                    break
            return obj if len(obj) >= 2 else matches[0]

        # 尝试提取"XX数据"中的XX
        matches = re.findall(r"([\u4e00-\u9fa5]{2,4})数据", text)
        if matches:
            obj = min(matches, key=len)
            for verb in _SORTED_ACTION_VERBS:
                if obj.startswith(verb):
                    obj = obj[len(verb) :]
                    break
            return obj if len(obj) >= 2 else matches[0]

        # 尝试提取"XX记录"中的XX
        matches = re.findall(r"([\u4e00-\u9fa5]{2,4})记录", text)
        if matches:
            obj = min(matches, key=len)
            for verb in _SORTED_ACTION_VERBS:
                if obj.startswith(verb):
                    obj = obj[len(verb) :]
                    break
            return obj if len(obj) >= 2 else matches[0]

        return ""  # 无法推断

    @classmethod
    def _is_pure_title(cls, text: str, source_quote: str = "") -> bool:
        """判断是否是纯标题（不可拆解，直接过滤）。

        原文若已是完整陈述句（以句末标点收尾），它就是在描述系统行为，
        不能按"无动作动词的短片段"判成标题——那会把陈述句格式的需求整类误杀。
        """
        if not text:
            return True

        if _SENTENCE_END_RE.search(source_quote or "") or _SENTENCE_END_RE.search(text):
            return False

        # 1. 纯标题前缀匹配
        if any(text.startswith(prefix) for prefix in cls._PURE_TITLE_PREFIXES):
            return True

        # 2. 功能描述前缀（但不是概述类）
        if text.startswith("功能描述") and not any(
            text.startswith(p) for p in cls._OVERVIEW_PREFIXES
        ):
            return True

        # 3. 后缀匹配（且长度较短，<=15字）
        if len(text) <= 15:
            for suffix in cls._TITLE_SUFFIXES:
                if text.endswith(suffix):
                    return True

        # 4. 超短文本（<4字）且不含动作动词
        if len(text) < 4:
            has_action = any(v in text for v in cls._ACTION_VERBS)
            if not has_action:
                return True

        # 5. 纯编号标题
        if re.match(r"^(\d+\.\d*|[一二三四五六七八九十]+[、.])\s*$", text):
            return True

        # 6. 不含动作动词的描述性文本（长度 <=25 且无动作动词）
        #    如 "排列顺序按创建时间从近到远" —— "创建时间"是名词不是操作
        if len(text) <= 25:
            # ★ Fix55b: 排除"XX时间"、"XX顺序"等名词化动词
            cleaned = re.sub(
                r"(创建|更新|修改|删除|新增|编辑|导入|导出)(时间|顺序|日期|人|方式|规则)",
                "",
                text,
            )
            has_action = any(v in cleaned for v in cls._ACTION_VERBS)
            is_sentence = bool(re.search(r"[。！？；]$", text)) or "，" in text
            if not has_action and not is_sentence:
                return True

        # 7. "字段如下"、"列表如下" 等列表标题模式
        if re.search(r"(字段|列表|内容|项|参数).{0,3}(如下|说明|描述|介绍)", text):
            return True

        # ★ Fix58: 业务规则/技术描述过滤
        #   特征：不含用户操作动词，而是描述系统行为/业务规则/技术实现
        #   如 "牌号列表：与叶组左侧的列表保持一致" / "筛选逻辑，取ERP烟叶接口中..."
        if len(text) <= 50:
            # 用户操作动词（更强的判定集合）
            user_action_verbs = {
                "点击",
                "输入",
                "填写",
                "选择",
                "勾选",
                "下拉",
                "上传",
                "导入",
                "下载",
                "导出",
                "打开",
                "关闭",
                "查看",
                "浏览",
                "跳转",
                "复制",
                "粘贴",
                "清空",
                "重置",
                "刷新",
                "启用",
                "停用",
                "保存",
                "提交",
                "确认",
                "取消",
                "删除",
                "新增",
                "新建",
                "添加",
                "编辑",
                "修改",
                "查询",
                "搜索",
                "查找",
                "检索",
                "审核",
                "审批",
            }
            # ★ Fix58b: "筛选"需要区分"筛选逻辑"(名词)和"筛选数据"(动词)
            #   只在后面不跟"逻辑/规则/条件"时才算操作
            has_user_action = False
            for v in user_action_verbs:
                if v in text:
                    has_user_action = True
                    break
            if "筛选" in text and not re.search(
                r"筛选.{0,2}(逻辑|规则|条件|策略)", text
            ):
                has_user_action = True

            # 系统行为/技术描述关键词
            system_desc_patterns = [
                r"(列表|数据|信息).{0,4}(保持一致|同步|对应|匹配)",
                r"(逻辑|规则|算法|策略)",
                r"(接口|API|ERP|数据库|DB)",
                r"(形态|编码|代码|类型).{0,4}(为|是|属于)",
            ]
            is_system_desc = any(re.search(p, text) for p in system_desc_patterns)

            if not has_user_action and is_system_desc:
                return True

        return False
