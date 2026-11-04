# -*- coding: utf-8 -*-
from __future__ import annotations

"""规则提取 - 关键词提取与 nav_map 匹配模块
============================================
★ 架构债清理(2026-07-25): 从 _rule_extractor.py 拆出（§4.3 单文件≤800行）。

职责：
  1. 判断需求是否为系统集成类（不适合 UI E2E 测试）
  2. 从 operation/module/description 提取业务关键词（用于降级用例 checks）
  3. 从 nav_map 匹配页面名/反查页面名
通用性（§4.2）：nav_map 数据驱动 + 删除法通用动词过滤，无业务关键词硬编码。
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


# ============================================================
# 4. 控件类型推断
# ============================================================


def _is_system_integration_requirement(operation: str, description: str = "") -> bool:
    """★ 通用修复(2026-07-21 v19): 判断需求是否为系统集成类（不适合 UI E2E 测试）。

    根因: REQ-061 等需求 operation="系统从办公协同获取叶组配方化学成份数据"
          本质是后端系统集成/数据同步，没有用户操作的 UI 页面。
          为它生成 UI 降级用例（navigate to dashboard + click 数据看板）是错误的，
          降级用例的 operation 关键词验证必然失败（dashboard 不包含业务关键词）。

    通用性:
      - 跨系统: 适用于所有系统的需求文档（不依赖特定系统）
      - 跨行业: 适用于所有行业的系统集成类需求
      - 跨页面: 不依赖页面类型，只判断 operation 语义
    合理性: 追根溯源到"为系统集成类需求生成 UI 用例"的根因，从源头跳过
    覆盖性: 被 extract_cases_by_rules / _build_rule_fallback_cases /
            _parallel_model_all_reqs 三处调用，覆盖所有用例生成路径

    判断规则（满足任一即判定为系统集成类）:
      1) operation 含"系统从" + 系统集成类动词（"系统从X获取Y"模式，最严格）
      2) operation 含"通过系统对接"等明确系统集成语义
      3) description 含"系统从X获取Y数据"模式

    自我反驳:
      1) 反驳: "系统"可能在用户操作中出现（如"系统设置"）？
         → "系统设置"不含"系统从"+动词，不会误判
      2) 反驳: "获取"可能在用户操作中出现（如"用户获取系统验证码"）？
         → "用户获取系统验证码"不含"系统从"，不会误判
         → 只有"系统从X获取Y"模式才是系统集成类需求
      3) 反驳: 跳过会不会漏测？
         → 系统集成类需求应由 API 测试/集成测试覆盖，不是 UI E2E 测试
    """
    if not operation:
        return False
    op = str(operation)
    desc = str(description or "")

    # 系统集成类动词（后端数据同步/接口调用语义）
    _SYS_INTEGRATION_VERBS = (
        "获取",
        "同步",
        "对接",
        "集成",
        "调用",
        "推送",
        "拉取",
        "传输",
        "交互",
    )

    # 规则1: operation 含"系统从" + 系统集成类动词（"系统从X获取Y"模式）
    #   ★ v19 改进: 用"系统从"替代"系统"，避免误判"用户获取系统验证码"等用户操作
    #   通用性: "系统从X获取Y"是系统集成类需求的标准表述模式
    #   合理性: 只有"系统从"开头的操作才是系统集成类，用户操作不会这样表述
    if "系统从" in op and any(v in op for v in _SYS_INTEGRATION_VERBS):
        return True

    # 规则2: operation 含"通过系统对接"等明确系统集成语义
    if "系统对接" in op or "系统间" in op or "跨系统" in op:
        return True

    # 规则3: description 含"系统从X获取Y数据"模式
    if "系统从" in desc and any(v in desc for v in _SYS_INTEGRATION_VERBS):
        return True

    return False


def _extract_operation_keywords_v16(
    operation: str, module: str = "", description: str = ""
) -> list:
    """★ 彻底重构(2026-07-21 v20): 删除滑动窗口垃圾代码，改用 nav_map优先 + 删除法。

    用于降级用例的text_contains_check，验证页面与需求相关，禁止假PASS。

    ★ v20 根因分析（追根溯源3层）:
      1. 表层: text_contains_check 期望文本是滑动窗口产生的子串（如"公协同获"），
               无意义子串必然导致 check 失败。
      2. 中层: 滑动窗口方法本身就是垃圾代码——它不区分"业务名词"和"虚词/动词的组合"，
               对"系统从办公协同获取叶组配方化学成份数据"产生 5 个无意义子串。
      3. 根因: 关键词提取思路错误——应该提取**完整的业务名词**（如"辅材配方"、
               "叶组配方"），而不是用滑动窗口切割所有可能的 2-4 字子串。

    ★ v20 新策略（三优先级，删除滑动窗口）:
      1) 优先级1 — nav_map 业务名词匹配:
         从 system_nav_maps 加载所有页面名（flat_list + menu_groups items + tab_items），
         从 operation/module/description 中提取出现在页面名中的完整词组。
         这是最可靠的方式，因为这些是真实业务名词。
      2) 优先级2 — 删除法:
         从 operation 中删除动词/虚词/通用名词/系统集成类词，剩余部分作为业务名词。
         例: "查询辅材配方" → 删除"查询" → "辅材配方"
      3) 优先级3 — 返回空列表:
         如果以上都未提取到关键词，返回空列表，调用方跳过此项 check（不强求）。

    ★ v20 自我反驳三次（取最优解）:
      1) 反驳: 为什么不用 jieba 分词器？
         → 项目未安装 jieba，且分词器对中文业务术语的准确率不保证；
           nav_map 已有真实业务名词，更可靠。
      2) 反驳: 删除法会不会误删业务名词（如"系统管理"中的"系统"）？
         → "系统管理"是通用词组（任何系统都有），删除后剩余空，
           走优先级3返回空列表，调用方跳过 check（比假PASS更诚实）。
      3) 反驳: nav_map 匹配会不会漏掉 operation 中没有页面名的需求？
         → 会漏，但这种需求走优先级2（删除法）补充；
           如果删除法也提取不到，说明 operation 纯动词无业务名词，
           跳过 check 是合理的（强断言无意义）。

    通用性:
      - 跨系统: nav_map 是 Step1 爬取的真实页面名，适用于所有系统
      - 跨行业: 删除法只过滤通用动词/虚词，不硬编码业务术语
      - 跨页面: 不依赖页面类型，只提取业务名词

    合理性: 追根溯源到"滑动窗口方法本身是垃圾代码"的根因，彻底删除

    覆盖性: 被 _rule_extractor.py 和 case_rules/base.py 两处降级用例 checks 生成调用
    """
    if not operation and not module and not description:
        return []

    # ── 通用词集（删除法用，不硬编码业务术语）──
    _VERBS_V20 = {
        # UI 操作动词
        "查询",
        "搜索",
        "筛选",
        "新建",
        "编辑",
        "删除",
        "查看",
        "导入",
        "导出",
        "上传",
        "下载",
        "保存",
        "提交",
        "确认",
        "取消",
        "刷新",
        "新增",
        "修改",
        "批量",
        "单个",
        "操作",
        "管理",
        "功能",
        "模块",
        "用户",
        "系统",
        "执行",
        "进行",
        "使用",
        "设置",
        "配置",
        "复制",
        "启用",
        "禁用",
        "停用",
        "开关",
        "支持",
        "能够",
        "可以",
        "应该",
        "需要",
        "必须",
        "实现",
        "完成",
        "成功",
        "失败",
        "返回",
        "进入",
        "回到",
        "跳转",
        "切换",
        "选择",
        "勾选",
        "填写",
        "输入",
        "点击",
        "按键",
        "回车",
        "确定",
        "功能",
        "清单",
        "需求",
        # 系统集成类动词
        "获取",
        "同步",
        "对接",
        "集成",
        "调用",
        "推送",
        "拉取",
        "传输",
        "交互",
        "提供",
        "接收",
        "发送",
        "处理",
        "计算",
        "生成",
    }
    _GENERIC_NOUNS_V20 = {
        "数据",
        "信息",
        "记录",
        "列表",
        "详情",
        "内容",
        "结果",
        "状态",
        "页面",
        "界面",
        "视图",
        "面板",
        "区域",
        "部分",
        "目标",
        "对象",
        "化学",
        "成份",
        "成分",
        "类型",
        "种类",
        "类别",
        "分类",
    }
    _STOPWORDS_V20 = {
        "从",
        "的",
        "了",
        "在",
        "是",
        "有",
        "和",
        "与",
        "或",
        "及",
        "把",
        "被",
        "让",
        "使",
        "给",
        "对",
        "向",
        "为",
        "由",
        "于",
        "等",
        "之",
        "其",
        "此",
        "那",
        "这",
        "某",
        "每",
        "各",
        "任",
        "中",
        "内",
        "外",
        "上",
        "下",
        "前",
        "后",
        "左",
        "右",
    }

    # ── 优先级1: nav_map 业务名词匹配 ──
    nav_keywords = _match_nav_page_names_in_text(operation, module, description)
    if nav_keywords:
        _log.debug(
            "[v20] nav_map 匹配关键词: %s (operation=%r)",
            nav_keywords[:5],
            (operation or "")[:40],
        )
        return nav_keywords[:5]

    # ── 优先级2: 删除法 ──
    import re as _re_v20

    combined = f"{operation} {module} {description}".strip()
    if not combined:
        return []
    # 提取连续中文字符段
    segments = _re_v20.findall(r"[\u4e00-\u9fff]+", combined)
    delete_keywords = []
    for seg in segments:
        # 从 segment 中删除动词/通用名词/虚词，剩余部分作为业务名词
        remaining = seg
        # 按长度倒序删除（先删长的，避免"系统从"删除"系统"后留下"从"被"从"删除再误删业务词）
        for word in sorted(_VERBS_V20 | _GENERIC_NOUNS_V20, key=len, reverse=True):
            if len(word) >= 2 and word in remaining:
                remaining = remaining.replace(word, " ")
        # 按单字删除虚词（只删单字虚词，避免误删业务名词中的字）
        for sw in _STOPWORDS_V20:
            if len(sw) == 1:
                remaining = remaining.replace(sw, " ")
        # 切分剩余部分，保留长度>=2的子串
        parts = [p.strip() for p in remaining.split() if p.strip()]
        for p in parts:
            # ★ v20 改进: 过滤掉以单字虚词开头/结尾的关键词（如"按辅材"→"辅材"）
            #   原因: 删除法可能留下"按辅材"这种半截词组，"按"是虚词但不在_STOPWORDS_V20
            #   策略: 反复删除首尾单字虚词，直到关键词以2字以上词组开头/结尾或长度<2
            _all_stopwords = _STOPWORDS_V20 | {
                "按",
                "将",
                "会",
                "能",
                "可",
                "已",
                "又",
                "也",
                "还",
                "才",
                "再",
            }
            _changed = True
            while _changed and len(p) >= 2:
                _changed = False
                if len(p) >= 2 and p[0] in _all_stopwords:
                    p = p[1:]
                    _changed = True
                if len(p) >= 2 and p[-1] in _all_stopwords:
                    p = p[:-1]
                    _changed = True
            if len(p) >= 2 and p not in delete_keywords:
                delete_keywords.append(p)
    if delete_keywords:
        _log.debug(
            "[v20] 删除法关键词: %s (operation=%r)",
            delete_keywords[:5],
            (operation or "")[:40],
        )
        return delete_keywords[:5]

    # ── 优先级3: 返回空列表，调用方跳过此项 check ──
    _log.debug(
        "[v20] 未提取到业务关键词，跳过 operation 关键词 check (operation=%r)",
        (operation or "")[:40],
    )
    return []


def _match_nav_page_names_in_text(
    operation: str, module: str = "", description: str = ""
) -> list:
    """★ v20 新增: 从 nav_map 加载所有页面名，匹配出现在 operation/module/description 中的页面名。

    通用性:
      - 跨系统: nav_map 是 Step1 爬取的真实页面名，适用于所有系统
      - 跨行业: 不硬编码业务术语，纯数据驱动
      - 跨页面: 不依赖页面类型

    合理性: nav_map 页面名是真实业务名词，比滑动窗口产生的子串更可靠

    覆盖性: 被 _extract_operation_keywords_v16 调用，覆盖所有降级用例 checks 生成

    Returns:
        list[str]: 匹配到的页面名列表（按出现顺序，最多5个）；无匹配返回空列表
    """
    try:
        from src.core.config_loader import config

        # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps.nav_json → nav_graph
        #   根因: system_nav_maps 无 nav_json 列且 0 行，原查询恒抛 Unknown column
        #         被下方 except 吞掉 → 页面名匹配永远返回空。
        from src.pipeline.utils.nav_graph_read import read_nav_map

        project = getattr(config, "project_name", "") or ""
        if not project:
            return []
        nav_data = read_nav_map(project)

        # 收集所有页面名（flat_list + menu_groups items + tab_items）
        page_names = set()
        for item in nav_data.get("flat_list", []) or []:
            _t = str(item.get("text", "") or "").strip()
            if _t and len(_t) >= 2:
                page_names.add(_t)
        for mg in nav_data.get("menu_groups", []) or []:
            for item in mg.get("items", []) or []:
                _t = str(item.get("text", "") or "").strip()
                if _t and len(_t) >= 2:
                    page_names.add(_t)
            for tab_item in mg.get("tab_items", []) or []:
                _t = str(tab_item.get("text", "") or "").strip()
                if _t and len(_t) >= 2:
                    page_names.add(_t)
        if not page_names:
            return []

        # 从 operation/module/description 中匹配页面名
        combined = f"{operation} {module} {description}".strip()
        if not combined:
            return []
        matched = []
        # 按页面名长度倒序匹配（先匹配长的，避免"辅材"先匹配到"辅材配方"的子串）
        for name in sorted(page_names, key=len, reverse=True):
            if name in combined and name not in matched:
                matched.append(name)
        return matched[:5]
    except Exception as _e:
        _log.debug("[v20] nav_map 页面名匹配失败(不阻塞): %s", str(_e)[:80])
        return []


# ============================================================
# 5. 场景语义分类（跨模块单一事实源）
# ============================================================
# ★ 通用修复(2026-09-24): 登录/拒绝场景判定收敛为唯一入口。
#   根因: 同一语义（登录+拒绝）此前在 _navigation_resolver(导航清空)、
#     case_generator/_explore_task_builder(测试方法)、_fallback_cases(用例动作)
#     各自硬编码关键词表 → 三套口径漂移，改了 A 惠及不到 B。
#   通用性: 跨系统/行业/页面 — 登录凭证与拒绝语义是 Web 系统通用概念，非业务词。

# 登录/认证类需求（无对应菜单，Agent 停留登录场景自主处理）。
AUTH_KEYWORDS = ("登录", "认证", "密码", "用户名", "登出", "注销", "账号")

# 「提交凭证」语义词（聚焦登录动作本身，不含登出/账号管理等旁支）。
_LOGIN_SUBMIT_KEYWORDS = ("登录", "登陆", "凭证", "鉴权", "认证", "口令")

# 「期望被拒绝」语义词（区别于"成功/通过"）。
_REJECT_KEYWORDS = (
    "错误", "失败", "无效", "非法", "拒绝", "被拒", "不允许", "无权限",
    "401", "403", "用户名或密码", "密码错误", "用户名不存在", "口令错误",
    # ★ 通用修复(2026-09-24): "期望出现校验/错误提示"同样是"期望非成功态"。
    #   根因: 边界/异常用例的 expected_feedback 常写作"显示校验提示或正确处理"
    #     （不含"错误/失败"字样）→ 极性判定漏报 → 矛盾断言 no_error 被保留 →
    #     SUT 按需求正确渲染校验提示时 no_error 判 FAIL（实测 TC-REQ-031-I03/
    #     TC-REQ-036-I03：页面出现 .flash-danger"价格必须是大于 0 的数字"却判无错失败）。
    #   通用性: 只匹配"提示"的**类别词**（校验提示/错误提示/拦截/阻止提交），
    #     不匹配裸"校验"——否则会把"通过双重校验后提交成功"（REQ-040 正向用例）误判。
    "校验提示", "错误提示", "提示错误", "校验失败", "校验错误",
    "拦截", "阻止提交", "不提交",
    # ★ 通用修复(2026-09-24): 「拒绝执行 / 拒绝持久化」同样是"期望非成功态"。
    #   根因: 删除拦截类需求的 expected_feedback 写作"系统不执行删除操作，列表保持不变"
    #     （REQ-015/017），不含"错误/失败/提示"字样 → 极性判定漏报 → 矛盾断言 no_error
    #     被保留 → SUT 按需求正确渲染"维护中的资产不允许删除"拦截提示时 no_error 判 FAIL
    #     （实测 TC-REQ-017-I01：semantic_check 已 PASS"符合系统不执行删除操作的语义"，
    #      仅因 no_error 发现 .flash-danger 而整体 FAIL）。
    #   通用性: 「不执行 / 不写库 / 不落库 / 未写入 / 不改变」是"操作被拒绝"的通用表述，
    #     跨系统/行业/页面成立，零业务硬编码。
    #   ⚠ 刻意不含裸"保持不变": "编辑价格，其他字段保持不变"是**正向**需求
    #     （系统应当不报错），保留 no_error 才正确；只有"不执行/不写库"这类
    #     明确的拒绝语义才与 no_error 矛盾。
    "不执行", "不写库", "不落库", "不写入", "未写入", "不改变", "未变更",
    # 否定动词「禁止」本身即声明拒绝语义（"禁止删除并提示该分类下仍有商品"）；
    #   刻意不收裸"提示"，避免只描述反馈样式的中性需求被误判为拒绝型。
    "禁止",
)

# 「否定防御」片段: "无…错误/不崩溃/未…泄露" 是系统
#   **正确防御**的措辞（预期没有错误），不是"期望错误反馈"。判定前剥离之，
#   防裸词"错误/崩溃"把防御成功误判为拒绝型。通用性: 否定防御措辞
#   （无/不/未/不会/未导致…错误/异常/崩溃/报错/致命错误）跨系统通用。
#   族词刻意不含"泄露"：它可作"不泄露"的独立被否定对象（防御成功），
#   含之会把"不泄露数据库错误信息"错剥成"不泄露"残留"错误"（实测）。
#   「不存在」不再单列：它会让"删除不存在的商品返回错误提示"被当成防御片段剥离
#     （否定作用在"存在"上，而"返回错误提示"才是主谓）。同理「的」是相对小句标记，
#     跨「的」的距离不算否定防御 —— 否定必须与其后错误族词同属一个短语。
_NEGATED_DEFENSE_RE = re.compile(
    r"(?:无|没有|未|不|不会|不能|未导致|未出现|未发生)\s*"
    r"(?:任何|出现|弹出|展示|显示|发生)?\s*"
    r"[^，。；、:：的]{0,6}?(?:错误|异常|崩溃|报错|致命错误)",
    re.IGNORECASE,
)


# ============================================================
# 5.0 字段约束语义（DOM 约束型需求的单一事实源）
# ============================================================
# ★ 通用修复(2026-09-24): 字段约束型需求改走 DOM 约束校验（field_constraint_check）。
#   根因（系统性假 FAIL + 假 PASS 双向）:
#     需求文档的字段规则是**控件级约束**（"名称：必填，最长 40 字符"／
#     "价格：必填，数字，必须大于 0"／"库存：必填，非负整数"），它们由
#     `<input maxlength/min/step/type/required>` 等 DOM 属性承载，
#     **页面上不会有任何文字说明这些规则**。
#     原生成端把 expected_feedback（"系统允许输入最多40字符的名称"）当语义期望 →
#     语义断言只能看页面文本（_handle_semantic_check 仅取 inner_text）→
#     AI 如实回答"页面无任何关于'非负整数'的验证说明" → 判 FAIL（实测
#     TC-REQ-001-I01/004-I01/005-I01）。这是**验证手段与期望性质错配**，
#     属引擎缺陷（用错工具），不是 SUT 缺陷。
#   通用判据（跨系统/行业/页面，零业务硬编码）:
#     需求文本出现可机械判定的控件约束语汇（最多/最长N字符、非负整数、
#     大于0、必填、下拉选项枚举）→ 期望性质是"DOM 约束存在"，
#     只能用属性读取验证，不能交给文本/语义判定。
#   ⚠ 与"行为描述守卫"的分工: 行为描述（系统做什么）→ 交语义判定；
#     字段约束（控件属性是什么）→ 交 DOM 属性校验。二者互补，都不造假断言。

# 约束模式 → 规范化约束键（正则全部为通用语汇，无业务术语）
_MAXLEN_RE = re.compile(r"(?:最多|最长|最大长度|不超过|至多)\s*(\d{1,4})\s*(?:个)?(?:字符|字|位)")
# 长度区间写法（"1-50 字"／"2~20个字符"）：上界即长度上限
_MAXLEN_RANGE_RE = re.compile(r"(\d{1,4})\s*[-~～至]\s*(\d{1,4})\s*(?:个)?(?:字符|字|位)")
_INTEGER_RE = re.compile(r"非负整数|正整数|整数")
_NONNEG_RE = re.compile(r"非负|不小于\s*0|大于等于\s*0|>=\s*0")
_POSITIVE_RE = re.compile(r"大于\s*0|大于零|正数|必须为正|>\s*0")
_REQUIRED_RE = re.compile(r"必填|非空|不能为空|不允许为空|不可为空|必录")
# 下拉枚举：形如 "active/maintenance/retired"（≥2 个以 / 分隔的 ASCII 标识符）
#   ★ 边界用 ASCII 邻接断言而非 \b：Python 的 \b 以 Unicode \w 为基准，
#     中文属 \w，"提供active/..." 中 "供|a" 之间**不存在**词边界 → \b 恒不匹配
#     （实测 REQ-006 "系统提供active/maintenance/retired下拉选项" 漏判）。
_OPTIONS_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z][A-Za-z0-9_-]{1,20}(?:/[A-Za-z][A-Za-z0-9_-]{1,20}){1,11})"
    r"(?![A-Za-z0-9])"
)
# 枚举语境的必要前置词：只把「下拉/选项/单选」语境的斜杠串当字段枚举。
#   ★ 防误判: 接口清单需求常写 "GET/POST /assets/new"、审计需求常写
#     "create/edit/delete"，这些斜杠串是**方法名/动作名**而非字段取值枚举，
#     若无此语境约束会被误判成字段约束型 → 生成错误的 DOM 校验。
_OPTIONS_CONTEXT_RE = re.compile(r"下拉|选项|单选|select", re.IGNORECASE)

# 「仅声明必填」不足以触发字段约束路由：必填是几乎所有表单字段的公共属性，
# 单独出现时交由常规路径处理；只有与数值/长度/枚举约束同现才走 DOM 约束校验。
_CONSTRAINT_TRIGGER_KEYS = ("maxlength", "min", "integer", "positive", "options")

# API 操作型需求的**单一权威判据**：operation 主体是 HTTP 调用（如
#   "请求GET /assets接口"），而非页面操作。HTTP method + path 是跨系统接口语义
#   （RFC 7231），与业务无关。
#   用途：接口类需求无对应页面 → 不得生成浏览器轨（UI/性能/易用/兼容）用例，
#   否则 Agent 在无匹配页面上空转耗尽预算 → 结构性假 ERROR（引擎缺陷）。
_API_OP_RE = re.compile(
    r"(?<![A-Za-z])(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/\S*",
    re.IGNORECASE,
)


def is_api_operation(*texts: str) -> bool:
    """需求是否为接口操作型（operation 含 HTTP method + path）。单一事实源。"""
    return any(_API_OP_RE.search(t or "") for t in texts)


def parse_api_operation(text: str) -> tuple[str, str] | None:
    """从需求 operation 解析 (method, path)；非接口操作返回 None。单一事实源。

    path 截断到首个非 ASCII 字符（如 "请求GET /assets接口" → ("GET", "/assets")）。
    """
    m = _API_OP_RE.search(text or "")
    if not m:
        return None
    method = m.group(1).upper()
    path = m.group(0)[len(m.group(1)):].strip()
    path = re.split(r"[^\x21-\x7e]", path, 1)[0]
    return (method, path or "/")


def parse_field_constraints(*texts: str) -> dict:
    """从需求文本解析控件级约束（返回规范化约束字典；无约束返回 {}）。

    返回键（全部可选，出现即代表需求声明了该约束）::

        {"maxlength": 40, "min": 0, "integer": True,
         "positive": True, "required": True, "options": ["a","b","c"]}

    通用性: 纯通用语汇正则，跨系统/行业/页面成立，零业务硬编码。
    自我反驳:
      1) "最多 3 个"（数量）会被误判为 maxlength？→ 正则要求量词后必须跟
         "字符/字/位"，"最多3个"不匹配，不会误判。
      2) 枚举误判？→ 要求 ≥2 项且以 / 分隔的 ASCII 标识符，"和/或"这类
         中文连接词不匹配。
    """
    blob = " ".join(t for t in texts if t)
    if not blob:
        return {}
    out: dict = {}
    _m = _MAXLEN_RE.search(blob) or _MAXLEN_RANGE_RE.search(blob)
    if _m:
        out["maxlength"] = int(_m.group(_m.lastindex or 1))
    if _INTEGER_RE.search(blob):
        out["integer"] = True
    if _NONNEG_RE.search(blob) or out.get("integer"):
        out["min"] = 0
    if _POSITIVE_RE.search(blob):
        out["positive"] = True
        out["min"] = max(int(out.get("min", 0) or 0), 0)
    if _REQUIRED_RE.search(blob):
        out["required"] = True
    _om = _OPTIONS_RE.search(blob)
    if _om and _OPTIONS_CONTEXT_RE.search(blob):
        _opts = [p for p in _om.group(1).split("/") if p]
        if len(_opts) >= 2:
            out["options"] = _opts
    return out


# ★ RC-AP(2026-10-14): 「编辑型」需求的唯一判定源。
#   此前 `_fallback_cases` 用 `any(k in operation+expected ...)` 子串扫描，
#   会把「查看资产列表」这类**只读**需求误判为编辑型——因为其 expected_feedback
#   提到列表含"编辑/删除"操作入口字样。后果：发射「提交后列表出现唯一值」这类
#   页面上根本不存在提交动作的断言 → 恒假 FAIL。
#   判据收紧为 operation **以编辑/修改/更新/变更领起**：需求的主意图写在
#   operation 开头；出现在结果描述里的同类词只是**列举操作入口**，不是主意图。
_EDIT_VERB_RE = re.compile(r"^(编辑|修改|更新|变更)")


def is_edit_operation(*texts: str) -> bool:
    """需求主意图是否为"编辑已有记录"型（编辑/修改/更新/变更 领起）。

    否定/权限语境下的"编辑"是**受限动作**而非录入意图，故一并排除
    （与 is_record_creation 的否定优先约定同源）。
    """
    _op = str(texts[0] if texts else "" or "").strip()
    if not _EDIT_VERB_RE.match(_op):
        return False
    return not expects_error_feedback(*texts)


def is_field_constraint_requirement(*texts: str) -> bool:
    """需求是否为"字段约束型"（期望性质=控件 DOM 约束，须走属性校验）。

    与 is_record_creation / is_login_reject 同层：语义分类的单一事实源。
    通用性: 判定完全由 parse_field_constraints 的可机械判定约束驱动；
      仅声明"必填"（required）不单独触发——必填是表单字段公共属性，
      不足以说明期望性质是"控件约束"，避免把普通表单需求误路由。

    ★ 根因修复(2026-09-28): **服务端数值校验语义不许路由到 DOM 断言**。
      根因（实测 SmartShop TC-REQ-003/010-I01）: 需求写"价格必须大于0…
      服务端校验失败…拒绝写入数据库" → parse_field_constraints 解析出
      positive/min → 走到 field_constraint_check 要求 `<input min>` 存在于 DOM。
      但 SUT 的校验写明在**服务端**（客户端 input 无 min 属性是**合法**设计）
      → DOM 断言恒假 FAIL。这是验证手段与期望性质错配：服务端校验型需求的
      被测路径 = "提交非法值 → 被拒绝并报错"，已由非法载荷 + 错误提示语义
      验证承担，DOM 属性断言冗余且必然错配。
      判据: 约束集合 ⊆ {min, positive, required}（纯数值下限类，无 maxlength/
      options 客户端可承载约束）**且**文本含"服务端"校验语境 → 非 DOM 约束需求。
      maxlength/options/integer 是纯粹的客户端控件能力，不受本节影响。
      通用性: "服务端校验失败/拒绝写入"是跨系统的常见架构声明，零业务硬编码。
    """
    _c = parse_field_constraints(*texts)
    if not any(k in _c for k in _CONSTRAINT_TRIGGER_KEYS):
        return False
    _blob = " ".join(str(t) for t in texts if t)
    if "服务端" in _blob and set(_c.keys()) <= {"min", "positive", "required"}:
        return False
    return True


# 「字段名」通用语汇：中文技术写作中字段名几乎总以「X字段 / X输入框 / X下拉框 /
#   X选项 / X栏」的形式被点名（"价格字段为必填项且必须大于0"、"库存字段…"）。
#   ★ 根因修复(2026-09-25) RC-Y: field_constraint_check 的**字段定位源**必须是
#     这句「约束声明原文」，而不是 expected_feedback。
#   根因: expected_feedback 描述的是**系统行为**（"客户端拦截表单并提交，页面顶部
#     显示红色错误条"／"系统拒绝并返回错误提示"），其中**不含字段名** →
#     `_FIELD_LOCATE_JS` 拿它去匹配页面 label 必然 0 命中 →
#     恒 FIELD_NOT_FOUND 假 FAIL（实测 TC-REQ-041-I01/042-I01：候选标签明明
#     列出 ['名称','分类','价格','库存','状态','备注']，却因期望文本无字段名而失败）。
#   通用性: 纯中文技术写作的通用句式，零业务硬编码，跨系统/行业成立。
_FIELD_NAME_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_]{0,7})"
    r"(?:字段|输入框|下拉框|选择框|输入项|栏)"
)

# 「X字段模块/管理/页面」是**模块名**的一部分（"在表单字段模块中…"），不是字段名；
#   介词开头的候选（"在表单"）同理是散文片段。二者都会让 field_hint 指向不存在的
#   控件，定位端只能退回整段文本 → 约束被套到任意一个命中的 label 上。
_MODULE_SUFFIX_RE = re.compile(r"^\s*(?:模块|管理|页面|功能|需求|中)")
_PREP_PREFIX_RE = re.compile(r"^(?:在|于|对|从|向|给|把|将)")
# 字段名的前边界：谓词/冒号/引号/括号左侧的残留必须截断（"提示'价格" → "价格"）
_FIELD_CUT_RE = re.compile(r"必须|须|需|应|不得|要|不能|不可|不允许|为|是|[：:（(“\"'「『]")
_FIELD_TOKEN_RE = re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_]{1,9})\s*$")
# 约束算子：字段名紧邻其左侧（"价格必须大于0"/"库存不得超过…"/"最多50字符"）
_CONSTRAINT_OP_RE = re.compile(
    r"最多|最长|最大长度|不超过|至多|必须|须|需|应|不得|大于|小于|非负|非空|不能为空|必填"
)
# 非字段名的通用谓词/主语词（要求文本里的动词短语，不是控件名）
_FIELD_STOPWORDS = frozenset({
    "系统", "页面", "服务端", "客户端", "前端", "后端", "用户", "操作", "功能",
    "模块", "表单", "控件", "输入", "字段", "选项", "提示", "校验", "数据",
    "所有", "任何", "该", "此", "上述", "对应",
})
# 约束声明的分句符（中英文列举/句读），用于把多字段约束逐一拆开
_CLAUSE_SPLIT_RE = re.compile(r"[、，,；;。\n]+")
# 显式以控件属性语汇声明约束（"type=number"/"maxlength"/"控件属性"/"输入框"）
_CONTROL_ATTR_HINT_RE = re.compile(
    r"(?:type|min|max|maxlength|minlength|required|step|pattern)\s*=|"
    r"属性|控件|输入框|下拉框|选择框|HTML|DOM",
    re.IGNORECASE,
)
# 只能由控件自身表达的约束（长度上限/取值枚举/整数输入）；min/positive/required
#   是客户端与服务端都能承担的取值域规则，DOM 无属性不等于违反需求。
_CONTROL_ONLY_KEYS = ("maxlength", "options", "integer")


def parse_field_names(*texts: str) -> list:
    """从需求文本提取「约束所作用字段」的候选名（通用句式，无业务硬编码）。

    返回去重保序的候选列表；提取不到返回 []（调用方退回整段需求文本定位）。
    自我反驳: "该字段"/"此字段" 这类指代会被提取为 "该"/"此" —— 但页面 label 不会
      只有 1 个字，定位端要求 label 与候选**互相包含**，1 字候选不会误命中。
    """
    blob = " ".join(t for t in texts if t)
    if not blob:
        return []
    out: list = []
    for m in _FIELD_NAME_RE.finditer(blob):
        name = (m.group(1) or "").strip()
        # 剔除指代词/无义前缀（"该"/"此"/"上述"/"对应"）
        for _p in ("上述", "对应", "该", "此"):
            if name.startswith(_p) and len(name) > len(_p):
                name = name[len(_p):]
        # 模块名/散文片段不是字段名（"在表单字段模块中" → "在表单"）
        if _PREP_PREFIX_RE.match(name) or _MODULE_SUFFIX_RE.match(blob[m.end():]):
            continue
        # 「表单字段/控件」这类**类别词**不是具体字段名
        if name in _FIELD_STOPWORDS:
            continue
        if name and name not in out:
            out.append(name)
    return out


def field_name_from_clause(clause: str) -> str:
    """从单个约束分句提取「约束所作用字段名」；提取不到返回 ""。

    通用句式（跨系统/行业，零业务硬编码）:
      1) "X字段/输入框/下拉框…" 显式句式；
      2) 约束算子左侧紧邻的标识符（"价格必须大于0"/"库存不得超过…"），
         取**最靠近**约束算子的候选（"上架商品必须库存大于0" → "库存"）；
      3) "字段名（约束…）" 写法（"价格（大于 0）"）。
    """
    c = (clause or "").strip()
    if not c:
        return ""
    names = parse_field_names(c)
    if names:
        return names[0]
    cand = ""
    for m in _CONSTRAINT_OP_RE.finditer(c):
        left = _FIELD_CUT_RE.split(c[: m.start()])[-1]
        tm = _FIELD_TOKEN_RE.search(left)
        if not tm:
            continue
        tok = tm.group(1).strip()
        if len(tok) < 2 or tok in _FIELD_STOPWORDS or _PREP_PREFIX_RE.match(tok):
            continue
        cand = tok
    if cand:
        return cand
    pm = re.search(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_]{1,9})\s*[（(]", c)
    if pm:
        tok = pm.group(1).strip()
        if tok not in _FIELD_STOPWORDS and not _PREP_PREFIX_RE.match(tok):
            return tok
    return ""


def control_constraint_items(*texts: str) -> list[dict]:
    """把字段约束声明拆成「每字段一项」，只保留**控件能力型**约束。

    返回 ``[{"field": 字段名, "constraints": {...}, "clause": 该约束分句}, ...]``。

    根因（实测 TC-REQ-029-I01）: 需求"必填：商品名称（1-50 字）、分类、价格（大于 0）、
      库存（非负整数）"的四个约束分别作用于四个控件，原实现用 parse_field_constraints
      把整段合并成**一个**字典，再套到定位到的**任意一个** label（实测套到了"分类"的
      文本输入框，按 positive/integer 判定 → 假 FAIL）。约束与字段名同句共现是
      中文技术写作的通用句式，按分句拆解即可一一对应。
    性质过滤: 只有长度上限/取值枚举/整数输入是控件自身能力；min/positive/required
      是客户端与服务端都能承担的取值域规则，DOM 无属性**不等于**违反需求
      （实测 REQ-032 "提示'价格必须大于 0'" 被套 DOM min 断言 → 用错工具）。
      取值域规则由 validation 场景用例（填非法值→拒绝/提示）承担。
    """
    items: list[dict] = []
    _seen: set = set()
    for t in texts:
        for clause in _CLAUSE_SPLIT_RE.split(str(t or "")):
            clause = clause.strip()
            if not clause:
                continue
            cons = parse_field_constraints(clause)
            if not any(k in cons for k in _CONSTRAINT_TRIGGER_KEYS):
                continue
            if not _CONTROL_ATTR_HINT_RE.search(clause):
                cons = {k: v for k, v in cons.items() if k in _CONTROL_ONLY_KEYS}
            if not any(k in cons for k in _CONSTRAINT_TRIGGER_KEYS):
                continue
            name = field_name_from_clause(clause)
            _key = (name, repr(sorted(cons.items())))
            if _key in _seen:
                continue
            _seen.add(_key)
            items.append({"field": name, "constraints": cons, "clause": clause})
    return items


def is_auth_requirement(text: str) -> bool:
    """需求文本是否属于登录/认证类（无对应菜单，Agent 停留登录场景自主处理）。"""
    return any(k in (text or "") for k in AUTH_KEYWORDS)


# ★ 根因修复(2026-10-14) RC-AQ: 认证词的**否定/终止语境**不是「登录动作」。
#   根因（实测 fullcov_sut REQ-016/019）: operation="验证未登录访问业务页面" 含"登录"，
#     纯子串匹配判为 login → 套用登录模板生成"提交错误凭证→提示登录失败"验证点，
#     而该需求的真实期望是"未认证访问被重定向到登录页（302/401）"——
#     验证点与期望自相矛盾 → 引擎自造假 FAIL。
#   why: "未登录/未认证/退出登录" 表达的是**状态约束**（有权才放行 = 访问控制），
#     不是**登录动作**；子串匹配不辨语境，与 is_edit_operation 的否定排除属同一类根因。
#   通用性: 「否定/终止词 + 认证词」是语言通用组合，跨系统/行业成立，零业务硬编码。
_LOGIN_NEGATED_STATE_RE = re.compile(
    r"(?:未|无|非|尚未|没有)\s*(?:登录|登陆|认证|鉴权|授权|凭证|口令)"
)
_LOGIN_TERMINATED_STATE_RE = re.compile(
    r"(?:退出|登出|注销)\s*(?:登录|登陆|系统|账号|账户)?"
    r"|(?:登录|登陆)\s*(?:超时|失效|过期)"
)


def is_login_operation(operation: str) -> bool:
    """``operation`` 是否为「登录动作」（提交登录请求 / 请求登录页 / 登录校验）。

    ★ 根因修复(2026-09-25) RC-AG: 登录动作的被测前提是**未认证** —— 已登录会话下
      登录表单不可达（多数系统会把已认证用户从 /login 重定向到受保护首页）。故登录
      动作类用例必须以**匿名身份**执行，并由执行层**显式导航到登录页**；否则用例带着
      默认（admin）会话停在受保护列表页 → "登录按钮未找到" → 引擎自造 HARNESS_ERROR
      （实测 TC-REQ-024-I01 / 058-I01 / 059-I01）。

    通用性: 只扫 ``operation``（动作短语），**不扫 description** —— 避免
      "用户登录后访问资产列表"（REQ-033）/ "viewer 登录后…"（REQ-030）/
      "执行登出操作"（REQ-060）等**认证后**场景被误判为登录动作。
      词表复用 ``_LOGIN_SUBMIT_KEYWORDS``（登录/凭证/鉴权语义的单一事实源），
      与 ``is_login_reject`` 同源，零业务硬编码。
      否定形态（"未登录访问"/"登录超时"）由 RC-AQ 先剥离，落到访问控制分支。
    """
    _op = operation or ""
    if not _op:
        return False
    if _LOGIN_NEGATED_STATE_RE.search(_op) or _LOGIN_TERMINATED_STATE_RE.search(_op):
        return False
    return any(k in _op for k in _LOGIN_SUBMIT_KEYWORDS)


def is_login_reject(operation: str, expected_feedback: str = "") -> bool:
    """需求是否属于「提交登录凭证且期望被拒绝」场景。

    根因: "提交登录请求" 类 operation 无"错误/失败"字样，拒绝语义落在
      expected_feedback（如"用户名或密码错误"）→ 只扫 operation 会漏报，
      用例落入通用表单模板（fill"名称/姓名输入框"），登录页无该控件 →
      元素定位失败，引擎自造 ERROR（非 SUT 缺陷）。

    ★ 与 `is_login_operation` 共用「登录动作」前提（同一否定态排除）：
      "提交凭证" 是被测动作，前提是**当前处于未认证态、可提交**。
      "未登录访问业务页面被重定向"（REQ-016）描述的是**未认证状态下的访问被拒**，
      并不存在"提交凭证"这一步 —— 若按提交凭证执行（要求填「密码,口令」），
      登录页/门禁页没有该控件 → 填充失败 → 引擎自造 ERROR（实测 TC-REQ-016-E01）。
      该语义属访问控制（未认证 → 被拒绝），归 `access_control` 场景处理。

    通用性: 「提交凭证词」+「拒绝语义词」+「非否定态」三者并列，跨系统/行业成立。
    """
    _op = operation or ""
    _fb = expected_feedback or ""
    if _LOGIN_NEGATED_STATE_RE.search(_op) or _LOGIN_TERMINATED_STATE_RE.search(_op):
        return False
    return any(k in _op for k in _LOGIN_SUBMIT_KEYWORDS) and any(
        (k in _op) or (k in _fb) for k in _REJECT_KEYWORDS
    )


# 引擎自撰模板文案的分隔标记 —— 探针用例 description 尾部的**引擎协议**：
#   "对{需求描述}做真实功能探针：进入模块页→探索可交互控件→执行良性交互
#    →断言具体业务信号（表单出现/校验提示/列表状态变化）"（见 _fallback_cases._probe_case）
#
# ★ 根因修复(2026-09-27)：该模板**枚举了可能的 UI 信号**（含"校验提示"），而"校验提示"
#   本身是 _REJECT_KEYWORDS 的一员 —— 于是**每一个探针用例**都被判为"期望错误反馈"，
#   与需求真实期望无关。实测 TC-REQ-010-E01（正向："资产列表需完整呈现核心业务字段"，
#   期望反馈="列表展示 ID、名称…"）被误判 expects_error=True → no_error 断言被误反转/误剔除。
#
#   通用原则（结构性边界，非业务词表）：**引擎自撰文案不得参与需求语义/极性推断**。
#   需求派生文本与引擎模板文本必须分离；该原则对任何引擎模板文案成立。
_ENGINE_TEMPLATE_MARKERS = ("做真实功能探针：",)

# 「系统预期反馈：」是引擎**结构化自撰**的槽位（见 step2 `_fd_extraction` 的
#   `…用户执行{op}操作，系统预期反馈：{fb}`）。槽位内容 `fb` 有两种来源：
#     ① 需求自述 → 属需求派生文本，参与极性推断；
#     ② 引擎按 operation 关键词**推断**（∈ INFERRED_FEEDBACKS）→ 非需求义务，
#        不得参与极性推断。
#   根因(2026-10-15)：推断值被拼进 description 后，负向需求文本里同时出现
#     "成功"（接受族，来自推断值）与"错误"（拒绝族，来自需求）→ 被
#     `is_disjunctive_text` 误判为析取式 → `_check_guard` 的极性反转被豁免
#     → no_error 存活 → 恒假 FAIL（实测 040 族：SUT 正确返回"用户名或密码错误"
#     仍判 FAIL）。实测全库 81 条含该推断槽位，属系统性污染。
_SYS_FEEDBACK_MARKER = "系统预期反馈："


def strip_engine_template(text: str) -> str:
    """剥离引擎自撰模板文案，仅保留**需求派生文本**（供语义/极性推断使用）。"""
    _s = str(text or "")
    for _m in _ENGINE_TEMPLATE_MARKERS:
        _i = _s.find(_m)
        if _i >= 0:
            _s = _s[:_i]
    return _s


def strip_inferred_feedback_clause(text: str) -> str:
    """移除「系统预期反馈：<引擎推断值>」子句（保留需求自述值）。

    判据复用唯一事实源 `INFERRED_FEEDBACKS`（非新词表）：只有槽位值**整体**
      以某个推断产物开头时，才判定该槽位是引擎推断的、与需求无关，予以移除；
      需求自述的槽位值（如"服务端拒绝非法值"）不属于推断集，原样保留。
    通用性: 结构性边界（引擎自撰槽位）+ 既有 SSOT 判据，零业务硬编码。
    """
    _s = str(text or "")
    _pos = 0
    while True:
        _i = _s.find(_SYS_FEEDBACK_MARKER, _pos)
        if _i < 0:
            return _s
        _val_at = _i + len(_SYS_FEEDBACK_MARKER)
        _tail = _s[_val_at:]
        _hit = next(
            (_k for _k in _INFERRED_FEEDBACKS_BY_LEN if _tail.startswith(_k)),
            "",
        )
        if _hit:
            # 槽位值为引擎推断 → **标记与值一并移除**，保留前后文
            _s = _s[:_i] + " " + _s[_val_at + len(_hit):]
            _pos = _i + 1
        else:
            _pos = _val_at


def is_disjunctive_text(text: str) -> bool:
    """文案是否为**析取式**（同时陈述"或/或者/or"两种可接受结果）。

    析取式（如"显示错误提示或正确处理"、"出现字段级错误提示或成功落地记录"）
    同时含正/负语义，不构成单一极性信号；**容错型**判定（如 Step3 N01/B01 的
    "错误提示必须存在吗"）必须据此放宽——系统"正确处理"也是符合期望的结果。
    本函数是该校验的唯一实现，调用方不得各自复制。
    """
    _s = str(text or "")
    return "或" in _s or " or " in _s.lower()


# 容错型结果语义词（**结果面**层面的容忍，不是"无极性"）。
#   根因(2026-10-27): 判定"期望是否容错"曾用"既非拒绝也非接受"的补集——
#     这把两类**非容错**文案误纳入：① 布局前置条件（"表单容器已渲染…必填标记可见"）；
#     ② 防御型探针（"系统无报错/无错误"）。二者被改写成 any_of(错误条|成功条|列表容器)
#     后，在**表单页/详情页**三个分支必然全不成立 → 结构性假 FAIL。
#   判据: 只有"允许多个合法结果面之一"才是容错语义 ——
#     析取式（或/ or）或显式结果面语汇（正确处理/合理结果/正常响应/正常渲染/不崩溃）。
#   通用性: 纯通用结果语汇，与业务无关；与 `is_disjunctive_text` 同层且互补。
_TOLERANT_OUTCOME_MARKERS = (
    "正确处理", "合理结果", "合理提示", "正常响应", "正常渲染", "不崩溃", "不中断",
)


def has_tolerant_outcome_marker(text: str) -> bool:
    """期望文本是否显式声明「中性/正确结果」语汇（正确处理/合理结果/正常响应…）。

    与 `is_tolerant_outcome` 的分工（两者回答**不同问题**）：
      - `is_tolerant_outcome`: 是否允许多个合法结果面（**析取式也算**）；
      - 本函数: 是否出现**非错误极**的结果语汇 —— 供断言分支构造使用。
    只有出现该语汇时，"成功反馈/结果承载面"才是需求允许的合法结局；
    纯错误/确认的析取（如"显示错误提示或确认拦截"）不得挂结果承载面分支，
    否则"页面只要还开着"就满足了"应被拒绝"，等于把断言退化为假 PASS。
    """
    _stem = " ".join(strip_engine_template(t) for t in (text,) if t)
    return any(k in _stem for k in _TOLERANT_OUTCOME_MARKERS)


def is_tolerant_outcome(text: str) -> bool:
    """期望是否为**容错型结果**（多个合法结果面之一成立即符合）。

    与 `expects_error_feedback`/`expects_accept_feedback` 的分工:
      极性判据回答"期望落在拒绝极还是接受极"；本判据回答"期望本身是否允许
      多个结果面"。只有后者成立时，把期望承载为 `any_of_check`（错误条/成功条/
      结果承载面任一可见）才与需求等价；前置条件与"无报错"防御不属于容错。
    """
    _s = str(text or "")
    if not _s.strip():
        return False
    if is_disjunctive_text(_s):
        return True
    return has_tolerant_outcome_marker(_s)


def has_opposite_outcome_disjunction(text: str) -> bool:
    """文案是否把**对立两极**（被拒绝 / 被接受）并列为可选结局。

    与 `is_disjunctive_text` 的区别（两者回答**不同问题**，非重复实现）：
      - `is_disjunctive_text`: "文本是否含替代结果"（含中性替代，如"正确处理"）
        → 供**容错型**判定使用（Step3 N01/B01 的"错误提示必须存在吗"）。
      - 本函数: "文本是否同时主张**互斥的成败两极**"
        → 供**极性判定**使用（`_check_guard` 的 no_error 反转）——只有并列
          正/负两极时极性才真正不可判；含中性替代时极性仍然明确。

    判据 = 某连接词（'或'/' or '）两侧**分属对立的两个族**：
      一侧含错误族词（_REJECT_KEYWORDS），另一侧含接受族词（_ACCEPT_KEYWORDS）。

    为什么极性判定不能直接用 `is_disjunctive_text`（裸子串判据）：
      `用户名或密码错误` 是**单一固定短语**（意为"凭证不正确"），`新增、编辑或删除`
      是**枚举**，`显示校验提示或正确处理` 的替代项是**中性**结果 —— 三者的极性
      都明确，却都会被裸 `或` 判据判成"析取"，使 `_check_guard` 的极性反转被错误
      豁免 → 负向断言 no_error 存活 → SUT 正确返回错误提示仍被判 FAIL
      （实测 2026-10-15，fullcov_sut v9 共 11 条被误豁免）。
    """
    _s = str(text or "")
    _low = _s.lower()
    if "或" not in _s and " or " not in _low:
        return False

    def _has(seg: str, words) -> bool:
        _l = seg.lower()
        return any(k.lower() in _l for k in words)

    for _m in re.finditer(r"或| or ", _low):
        _l_seg = _s[: _m.start()]
        _r_seg = _s[_m.end() :]
        if (
            _has(_l_seg, _REJECT_KEYWORDS) and _has(_r_seg, _ACCEPT_KEYWORDS)
        ) or (
            _has(_l_seg, _ACCEPT_KEYWORDS) and _has(_r_seg, _REJECT_KEYWORDS)
        ):
            return True
    return False


# ── 变更型操作 → 成功反馈候选（逗号分隔，任一命中即真 PASS）──
#   单一事实源：推断文案全集由此表派生（`INFERRED_FEEDBACKS`），供
#   `is_inferred_feedback` 判定"库里的期望是需求自述还是引擎推断产物"。
_MUTATION_OP_FEEDBACK: tuple[tuple[tuple[str, ...], str], ...] = (
    (("新增", "新建", "添加", "创建"), "新增成功,添加成功,创建成功,保存成功"),
    (("修改", "编辑", "更新"), "保存成功,修改成功,更新成功"),
    (("删除", "移除"), "删除成功,移除成功"),
    (("导入", "上传"), "导入成功,上传成功"),
    (("导出", "下载"), "导出成功,下载成功"),
    (("启用", "禁用", "停用"), "操作成功,启用成功,禁用成功"),
    (("登录",), "登录成功,欢迎"),
    (("退出", "注销"), "退出成功,已退出"),
    (("查询", "搜索", "筛选", "检索", "查找"), "页面显示查询结果"),
    (("审批", "审核", "审查", "批准", "驳回"), "审批成功,审核成功,操作成功"),
    (("配置", "设置", "分配"), "配置成功,设置成功,保存成功"),
    (("绑定", "解绑"), "绑定成功,解绑成功,操作成功"),
)
_MUTATION_OP_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(k for keys, _ in _MUTATION_OP_FEEDBACK for k in keys)
)
_DEFAULT_FEEDBACK = "操作成功,成功"
_REJECT_FALLBACK = "系统拒绝该操作并给出错误提示"

# 只读语义词（跨系统/行业通用）：系统只渲染结果，不弹"操作成功"。
_READ_ONLY_OP_KEYS: tuple[str, ...] = (
    "分页", "统计", "展示", "显示", "列表", "过滤", "排序", "查看",
    "内置", "高亮", "导航", "菜单", "布局", "样式", "标注",
)

INFERRED_FEEDBACKS: frozenset[str] = frozenset(
    [fb for _, fb in _MUTATION_OP_FEEDBACK] + [_DEFAULT_FEEDBACK, _REJECT_FALLBACK]
)
# 按长度降序的前缀匹配序（供 `strip_inferred_feedback_clause` 使用）：
#   长值优先，避免 "成功" 抢先匹配 "操作成功,成功" 这类更长推断值。
_INFERRED_FEEDBACKS_BY_LEN: tuple[str, ...] = tuple(
    sorted(INFERRED_FEEDBACKS, key=len, reverse=True)
)


def infer_expected_feedback(operation: str, declared: str = "") -> str:
    """按需求语义推断「默认期望反馈」—— 跨 step2/step3 的唯一实现。

    ★ 为什么需要它（实测根因，2026-10-14）：
      本函数的前身是两处**逐字相同**的私有词表（`step2/breakdown_rules/
      numbered_list.py::_infer_success_hint` 与 `step3/case_rules/base.py::_infer_success_hint`），
      二者都**只看 operation 关键词就返回"成功"文案**，完全不看需求的极性。
      实测 REQ-040「登录失败（用户名或密码错误）需提示"用户名或密码错误"，不允许区分
      具体哪个错误」因含"登录"二字被判成 `登录成功,欢迎` —— 期望与需求**正好相反**，
      下游据此生成的断言（以及极性收口 `_check_guard` 的 `expects_error_feedback`
      判据）全部失效：收口逻辑本来会把矛盾的 `no_error` 反转成负向断言，却因拿到
      "成功"文案而认为需求是正向 ⇒ 给一条"登录失败"用例配上"无错误"断言 ⇒ 假 FAIL。

    ★ 极性优先（唯一判据 = `expects_error_feedback`，不另立词表）：
      需求语义体自述"期望被拒绝/报错"时**绝不返回成功文案** —— 返回需求自身表达
      拒绝语义的那段原文，让下游的负向断言与极性收口都能正常工作。

    :param operation: 需求的操作意图文本
    :param declared: 需求已声明的期望反馈（若与极性冲突，以极性为准）
    """
    _op = operation or ""
    _dec = (declared or "").strip()
    if expects_error_feedback(f"{_op} {_dec}"):
        # 负向需求：优先取"自身即表达拒绝语义"的那段原文，保证断言语义与极性自洽
        for _cand in (_dec, _op):
            if _cand and expects_error_feedback(_cand):
                return _cand
        return _op or _dec or _REJECT_FALLBACK

    op = _op.lower()
    # ★ 只读型操作不产生成功反馈：系统对"只看不写"的响应就是**把结果渲染出来**，
    #   不存在"操作成功"提示文案。此前它与变更型共用一个兜底 "操作成功,成功" →
    #   断言"页面文本含'操作成功'" → 只读页永远不出现 → 结构性假 FAIL
    #   （实测 fullcov_sut REQ-039 内置账号 / 042 分页 / 043 统计 / 045 过滤）。
    #   判据（跨系统/行业通用）：**无变更动词** 且 **有只读语义词**。
    if not any(k in op for k in _MUTATION_OP_KEYS) and any(
        k in op for k in _READ_ONLY_OP_KEYS
    ):
        return ""
    for _keys, _fb in _MUTATION_OP_FEEDBACK:
        if any(k in op for k in _keys):
            return _fb
    return _DEFAULT_FEEDBACK


def is_inferred_feedback(text: str) -> bool:
    """该期望文案是否为**推断产物**（而非需求自述）。

    用于区分"需求真的声明了这个反馈"与"引擎按 operation 关键词猜的"：后者
    不携带超出 operation 的信息，因而不具备需求效力，可被更准确的规则覆盖。
    """
    return str(text or "").strip() in INFERRED_FEEDBACKS


def resolve_expected_feedback(
    operation: str, declared: str = "", description: str = ""
) -> str:
    """需求「期望反馈」的唯一解析入口（极性自洽 → 需求自述 → 推断）。

    ★ 本函数是期望反馈进入任何下游（断言/极性收口/场景分类）前的**唯一收口**，
      需求读取端（requirement_store）必须经它落地。此前消费端直接取库里的
      `expected_feedback` 就当需求义务用 → 旧版本推断出的错误文案被固化在库里
      （REQ-040「登录失败…」→"登录成功,欢迎"；REQ-041「不允许新增…403」→
      "新增成功…"）→ 断言与需求**正好相反** → 系统性假 FAIL。

    判定次序（每条都是通用语义，零业务硬编码）:
      1. 极性为负 → 取**自身即表达拒绝语义**的原文（declared 优先于 operation）；
      2. declared 非空且非推断产物 → declared（需求自述为权威）；
      3. 其余 → `infer_expected_feedback`（含只读型返回空 = 不产文本型反馈）。
    """
    _op = str(operation or "")
    _dec = str(declared or "").strip()
    _desc = str(description or "")
    if expects_error_feedback(f"{_op} {_dec} {_desc}"):
        for _cand in (_dec, _op, _desc):
            if _cand and expects_error_feedback(_cand):
                return _cand
        return _op or _dec or "系统拒绝该操作并给出错误提示"
    if _dec and not is_inferred_feedback(_dec):
        return _dec
    return infer_expected_feedback(_op, _dec)


def expects_error_feedback(*texts: str) -> bool:
    """需求期望反馈是否为「错误 / 拒绝 / 无权限」提示（断言极性单一事实源）。

    根因(2026-09-24): 生成端在所有用例上无差别追加负向断言 `no_error`
      （期望文案"系统无报错"）。对"期望被拒绝"类需求（如"服务端校验失败返回
      错误提示且不写库""维护中的资产不允许删除"），负向断言与需求期望直接矛盾
      —— SUT 按需求正确渲染错误提示时，no_error 判 FAIL → 系统性假 FAIL。

    根因(2026-09-27): 判据文本必须先剥离**引擎自撰模板文案**（见
      `strip_engine_template`），否则模板里枚举的"校验提示"会命中 _REJECT_KEYWORDS，
      把正向需求也判成"期望错误反馈"。

    根因(2026-09-28): 裸词"错误"误命中**否定防御**表述——"无数据库错误泄露"/
      "系统无致命错误"/"未导致崩溃或泄露"是**防御成功**的措辞（系统正确地没有
      出错），却被判成"期望错误" → SQLi/XSS 用例的 no_error 被 _check_guard 反转、
      执行端又按 error 极性走 DOM 反馈条通道 → 防御成功用例假 PASS/断言错位。
      修复: 判定前先剥离"否定前缀 + 错误族词"片段（无/不/未…错误/崩溃/泄露），
      剥离后剩余文本才参与 _REJECT_KEYWORDS 匹配；"显示错误提示/拒绝保存"等
      肯定形态不受影响。通用性: 否定防御措辞跨系统/行业通用，零业务硬编码。
    """
    _blob = " ".join(strip_engine_template(t) for t in texts if t)
    if not _blob:
        return False
    # 否定防御片段（"无…错误/不崩溃/未…泄露"）先剥离，避免裸词"错误"误命中。
    _blob = _NEGATED_DEFENSE_RE.sub("", _blob)
    return any(k in _blob for k in _REJECT_KEYWORDS)


# 「操作不得生效」的不变量语义词（取消/拒绝后目标不被删除或改变）。
#   与 expects_error_feedback 同层同源（断言极性单一事实源）：拒绝**提示**是"有反馈"，
#   不变量是"无副作用"，两者都要求断言取反——不得断言操作已完成。
#   根因: "取消则不删除"只含否定后缀，未进 _REJECT_KEYWORDS → 删除类断言仍落
#     delete_done（行数减少）→ SUT 按需求正确未删除反而判 FAIL。
#   判据刻意要求"取消/不执行/未/保留原"等明确不生效标记，不收裸"保持不变"
#     （"编辑价格，其他字段保持不变"是正向需求）。
_NOOP_INVARIANT_RE = re.compile(
    r"取消(?:后|则)?不?(?:删除|执行|生效|提交|保存)|"
    r"不(?:执行|产生|进行|发生)(?:该|此)?(?:删除|操作|变更|修改)|"
    r"未(?:删除|执行|生效|变更)|"
    r"保留原(?:记录|数据|条目|资产|商品|内容)|"
    r"(?:记录|数据|条目)(?:仍|依然)存在",
    re.IGNORECASE,
)


def expects_noop_invariant(*texts: str) -> bool:
    """需求期望是否为「操作不得生效」的不变量（取消/拒绝后目标未被删除或改变）。

    用途: 生成端据此为删除/变更类需求落反极性断言证据（如 delete_aborted），
      而非断言操作已完成。通用性: 纯语义词 + 同层剥离引擎模板，零业务硬编码。
    """
    _blob = " ".join(strip_engine_template(t) for t in texts if t)
    if not _blob:
        return False
    return bool(_NOOP_INVARIANT_RE.search(_blob))


# 「要求用户确认某个决定」的期望语义词 —— 观察对象是**确认弹窗是否存在**。
#   刻意要求"确认 + 弹窗/对话框/框"共现：裸"确认"多为提交按钮文案
#   （"点击确认提交"是正向流程），不是"须弹出确认框"这一可观察事实。
_CONFIRM_DIALOG_MARKERS = (
    "确认框", "确认弹窗", "确认对话框", "确认提示框", "二次确认", "弹出确认",
)
_CONFIRM_DIALOG_EN_RE = re.compile(
    r"confirm(?:ation)?\s*(?:dialog|prompt|box)", re.IGNORECASE
)


def expects_confirm_dialog(*texts: str) -> bool:
    """需求期望是否为「须出现确认弹窗」（assertion 手段 = 弹窗观察）。

    用途: 生成端据此把"弹出确认框"从语义断言映射为可机械判定的弹窗断言，
      运行期零 AI 才能对它给出真判定。通用性: 纯语义词，跨系统/框架成立。
    """
    _blob = " ".join(strip_engine_template(t) for t in texts if t)
    if not _blob:
        return False
    if any(k in _blob for k in _CONFIRM_DIALOG_MARKERS):
        return True
    return bool(_CONFIRM_DIALOG_EN_RE.search(_blob))


# 「期望被接受 / 正向反馈」语义词（与 _REJECT_KEYWORDS 对称，同为断言极性单一事实源）。
#   ★ 根因修复(2026-09-25) RC-X: 执行端 `_guided_verify` 的「成功条判定」触发条件
#     只认"成功"族词，而需求期望常写作"系统接受X"/"系统允许X"——
#     语义同属**接受**，却无"成功"字样 → 触发条件不成立 → 不做成功条 DOM 判定 →
#     落到 LLM 语义兜底（该路径对抽象措辞不稳定）→ 断言恒 FAIL
#     （实测 TC-REQ-007-I01：SUT 实际已"创建成功"并渲染 .flash-success，
#     页面快照含"创建成功"，却判 FAIL —— 典型假 FAIL）。
#   通用性: 跨系统/行业/页面 —— "接受/生效/落库"是 Web 系统正向反馈的通用表达，
#     与业务无关；词表与 _REJECT_KEYWORDS 同源同层，避免执行端再复制一份造成漂移。
_ACCEPT_KEYWORDS = (
    "成功", "已保存", "已提交", "已创建", "已新增", "已更新", "已删除", "已完成",
    "保存成功", "提交成功", "创建成功", "操作成功", "删除成功", "更新成功",
    "接受", "生效", "已生效", "落库", "写入成功", "持久化成功",
    "success", "saved", "created", "accepted",
)

# 接受语义的**否定/抑制**形态：命中即说明该期望不是"接受"（而是拒绝/拦截）。
#   根因: "接受"是高频动词，若不加否定护栏，"系统不接受非法输入"会被判为接受语义
#     → 叠加可见成功条即假 PASS。此处与 _REJECT_KEYWORDS 的拒绝语境重叠，互为护栏。
_ACCEPT_NEG_RE = re.compile(
    r"不(接受|允许|支持|生效|可用|保存|提交|创建)|"
    r"未(接受|保存|提交|创建|更新|删除)|"
    r"无法(接受|保存|提交|创建)|不能(接受|保存|提交|创建|允许)|"
    r"拒绝|拦截|阻止|不允许",
    re.IGNORECASE,
)


def expects_accept_feedback(*texts: str) -> bool:
    """需求期望反馈是否为「接受 / 正向成功」语义（与 expects_error_feedback 对称）。

    用途: 执行端据此启用「活动 DOM 成功条」判定 —— 断言措辞为正向时，
      以页面可见的成功反馈（.flash-success / toast / role=status 等）作为
      "系统已接受该输入"的确定性证据，不再依赖 LLM 对抽象措辞的语义猜测。
    通用性: 纯通用语义词 + 否定护栏，零业务硬编码。
    """
    _blob = " ".join(t for t in texts if t)
    if not _blob:
        return False
    if _ACCEPT_NEG_RE.search(_blob):
        return False
    _low = _blob.lower()
    return any(k.lower() in _low for k in _ACCEPT_KEYWORDS)


# ============================================================
# 5.1 创建记录语义判定（意图级，非关键词命中）
# ============================================================
# ★ 通用修复(2026-09-24): 创建语义判定从"关键词命中"升级为"意图判定"。
#   根因: 原判定只要 operation/description 出现"新增/创建/添加"即认定创建型，
#     但需求描述中"新增"常出现在**否定/权限/审计/只读**语境：
#       REQ-022 "viewer...不允许新增、编辑或删除资产...返回403权限不足"
#       REQ-031 "尝试执行新增资产操作时，系统应拒绝该操作并返回403权限不足"
#       REQ-018 "对所有资产操作（新增、编辑、删除）自动生成审计日志条目"
#       REQ-047 "...同时审计日志表新增一条 action=delete 的日志记录"
#       REQ-053 "用户通过GET /assets/new访问资产新增入口，系统展示资产新建表单页面"
#     这些被误判为创建型 → E01 探针动作被填成
#       click"新增" → fill"名称/姓名输入框" → click"保存"
#     → 403/只读/审计场景页面没有这些控件 → 元素定位失败 → 引擎自造 ERROR
#       （非 SUT 缺陷），深断言"列表出现唯一值"亦属类别错误 → 假 FAIL。
#   通用性: 跨系统/行业/页面 — 「创建动词 + 否定/只读/审计/拦截 语境否决」判定主意图，
#     词表全为通用语义词，零业务硬编码。

_CREATION_VERB_RE = re.compile(
    r"新增|新建|添加|创建|导入|上传|注册|录入|登记|发布|开通|"
    r"\bcreate\b|\badd\b|\bimport\b|\bupload\b|\bregister\b",
    re.IGNORECASE,
)

# 否定/权限语境：命中即说明"创建"是被禁止或被检验的对象，而非主意图。
# 同时是"需求是否访问控制型"的判据（`_identity_assign.pick_identity` 复用本常量）。
_DENY_RE = re.compile(
    r"不允许|禁止|拒绝|无权|无权限|权限不足|越权|不能|不可|"
    r"权限控制|权限策略|按角色控制|"
    r"\b403\b|\b401\b|forbidden|unauthorized|access\s+denied",
    re.IGNORECASE,
)

# 审计/日志侧写语境：创建/新增的对象是"日志记录"而非业务实体。
_LOG_OBJECT_RE = re.compile(
    r"审计日志|日志表|日志记录|操作日志|audit_log|audit\s*log|写入记录|记录一条",
    re.IGNORECASE,
)

# 只读页面加载语境：GET/请求/展示某页面，且操作本身无提交动作。
_READONLY_PAGE_RE = re.compile(r"\bGET\b|请求|展示|访问|打开", re.IGNORECASE)
_PAGE_NOUN_RE = re.compile(r"页面|表单页|入口")
_SUBMIT_VERB_RE = re.compile(r"提交|保存|填写|录入|输入|点击")
# ★ 根因修复(2026-09-28): 刷新/重定向**结果反馈**语境否决"创建记录"判定。
#   根因（实测 TC-REQ-008-I01）: op="变更数据后刷新页面"、expected="自动刷新页面
#   或重定向回列表页"，desc 举例"新增/编辑/删除"含创建动词 → is_record_creation=True
#   → 注入"列表出现唯一值 自动化记录xxx"深断言——但该用例不创建任何记录，列表里
#   不可能出现该值 → 系统性假 FAIL（跨用例值串扰）。
#   刷新/重定向是"操作完成后的 UI 反馈语义"，与创建动作无关；主意图是刷新时
#   不得按创建要求深断言。通用性：刷新/重定向/返回列表 是跨系统通用 UI 反馈词。
_REFRESH_RESULT_RE = re.compile(
    r"变更数据|数据变更|刷新页面|自动刷新|重定向回|重定向到|返回列表|跳转回列表|回到列表页",
    re.IGNORECASE,
)
# ★ 根因修复(2026-09-28): 必填/缺失/非空校验语境否决"创建记录"判定。
#   根因（实测 TC-REQ-002-I01）: desc="新增或编辑商品时，名称、价格、库存、分类
#   均为必填字段，任一字段缺失时系统应进行表单校验，提示相应错误信息且数据不落库"
#   —— 举例子句含"新增"创建动词 → is_record_creation=True → E01 探针注入
#   "列表含唯一值"深断言，但该用例不创建记录 → 系统性假 FAIL。
#   必填校验的主意图是"提交被拒/不落库"，与创建动作（提交成功落库）相反；
#   一旦出现必填/缺失语义即否决创建。通用性：必填/非空/缺失校验跨系统通用。
_REQUIRED_CHECK_RE = re.compile(
    r"必填|缺失|不能为空|不允许为空|不可为空|为空时|非空校验|必填字段",
    re.IGNORECASE,
)

# 「重定向/跳转至登录页」行为语义（登录墙/会话失效引导）——单一事实源：
# `_identity_assign._UNAUTH_STATE_RE` 与执行端 `_guided_verify` 均委托本常量，
# 不再各自表达该子语义。
_REDIRECT_LOGIN_RE = re.compile(
    r"重定向[^。；;]{0,10}登录|跳转[^。；;]{0,10}登录|强制登录|登录墙",
    re.IGNORECASE,
)


def is_redirect_to_login(text: str) -> bool:
    """需求/断言文本是否为「重定向或跳转至登录页」行为语义。"""
    return bool(_REDIRECT_LOGIN_RE.search(str(text or "")))

# 客户端校验拦截：明确"不提交/被拦截"。
_BLOCKED_SUBMIT_RE = re.compile(
    r"拦截|阻止.{0,4}提交|不提交|禁止提交", re.IGNORECASE
)


def is_record_creation(
    operation: str, description: str = "", expected_feedback: str = ""
) -> bool:
    """需求主意图是否为"创建记录"型（决定深断言形态与探针动作）。

    判定顺序（否定语境优先，避免"提到即算"）：
      1) 无创建动词 → 否
      2) 否定/权限语境（403/401/不允许/拒绝/权限不足/越权/权限控制）→ 否
      3) 客户端校验拦截（拦截/不提交/阻止提交）→ 否
      4) 只读页面加载（GET/请求/展示 页面，且 operation 无提交动作）→ 否
      5) 审计/日志侧写：创建对象是日志记录 → 否
         （例外：operation 本身是"业务实体创建"时保留，如"创建资产成功"
           描述里附带审计日志属次要副作用）

    通用性: 跨系统/行业/页面，零业务硬编码。
    """
    op = str(operation or "")
    text = f"{op} {description or ''} {expected_feedback or ''}"

    if not _CREATION_VERB_RE.search(text):
        return False
    # ★ 根因修复(2026-09-28): 刷新/重定向**结果反馈**仅在 operation 自身是刷新/变更
    #   语义时否决创建判定。根因（实测 TC-REQ-008-I01）: op="变更数据后刷新页面"、
    #   desc 举例"新增/编辑/删除"含创建动词 → 误判创建 → 注入"列表出现唯一值"深断言
    #   恒假 FAIL。但"新增商品…重定向回列表页"（REQ-013/019）的刷新是**创建成功的
    #   结果反馈**，op 是创建动词 → 必须保留创建判定。判据: operation 含刷新/变更
    #   且不含创建动词（整体文本才含 desc 的举例动词）→ 主意图是刷新而非创建。
    _op_lower = op.lower()
    if _REFRESH_RESULT_RE.search(text) and not _CREATION_VERB_RE.search(_op_lower):
        return False
    # ★ 根因修复(2026-09-28): 必填/缺失校验 → 主意图是"提交被拒"，非创建（见
    #   _REQUIRED_CHECK_RE 根因注释；判据与 deny 同层）。
    if _REQUIRED_CHECK_RE.search(text):
        return False
    if _DENY_RE.search(text):
        return False
    if _BLOCKED_SUBMIT_RE.search(text):
        return False
    if (
        _READONLY_PAGE_RE.search(text)
        and _PAGE_NOUN_RE.search(text)
        and not _SUBMIT_VERB_RE.search(op)
    ):
        return False
    if _LOG_OBJECT_RE.search(text):
        if _LOG_OBJECT_RE.search(op) or not _CREATION_VERB_RE.search(op):
            return False
    return True


# ── 「某状态被禁止处理」的语料判定（受操作约束的行状态）——单一事实源 ─────────
#   模块内常有一条需求声明"某状态的记录禁止被某操作处理"（如"状态为 maintenance
#   的资产禁止删除"）。同模块的**正向**需求（"删除成功"）若不排除该状态，其触发会被
#   服务端拒绝 → 行数不减 → 正向主张结构性落空（假 FAIL / 假 SKIP）。
#   受限状态值从**需求原文**读取（语言事实），非业务词表。
_FORBIDDEN_MARKERS = (
    "禁止", "不允许", "不可", "不得", "无法", "拒绝",
    "forbidden", "not allowed", "denied",
)
_STATE_MARKERS = ("状态", "status")
# 状态值的书写形态："状态为 X" / "状态字段值为 X" / "status = X"（X 为状态码）
_STATE_VALUE_RE = re.compile(r"(?:为|是|=|:|：)\s*([A-Za-z][A-Za-z0-9_\-]{1,40})")
# 值名不是状态值的常见干扰（布尔/空值等）
_STATE_VALUE_STOPWORDS = frozenset(
    {"true", "false", "none", "null", "ok", "yes", "no", "and", "or", "not"}
)


def forbidden_states_in_texts(texts: list, operation_tokens: tuple = ()) -> list[str]:
    """从需求文本中提取「被禁止处理的状态值」（受操作约束的行状态）。

    判据（三条同时成立才算，避免"提到即算"）:
      ① 文本含禁止语义（禁止/不允许/不可/拒绝…）；
      ② 文本含状态语义（状态/status）且给出状态值（"状态为 X"）；
      ③ 文本提及该操作动词（operation_tokens，由调用方按操作类型传入）。
    通用性: 判据是语言事实（禁令 + 状态值 + 动作），跨系统/行业成立，零业务词表。
    """
    _tokens = tuple(t for t in (operation_tokens or ()) if t)
    _out: list[str] = []
    for _text in texts or []:
        _t = str(_text or "")
        _lower = _t.lower()
        if not _t:
            continue
        if not any(_m.lower() in _lower for _m in _FORBIDDEN_MARKERS):
            continue
        if not any(_m.lower() in _lower for _m in _STATE_MARKERS):
            continue
        if _tokens and not any(_tok in _t for _tok in _tokens):
            continue
        for _m in _STATE_VALUE_RE.finditer(_t):
            _v = _m.group(1)
            if _v and _v.lower() not in _STATE_VALUE_STOPWORDS and _v not in _out:
                _out.append(_v)
    return _out


def _lookup_page_name_from_nav_map(target_page: str, project: str = "") -> str:
    """★ v21 根源修复新增: 从 nav_map 反查 target_page 对应的页面名。

    用于降级用例 checks 的 text_contains_check(页面名)，
    替代之前不可靠的 text_contains_check(operation关键词)。

    通用性:
      - 跨系统: nav_map 是 Step1 爬取的真实页面名，适用于所有系统
      - 跨行业: 不硬编码业务术语，纯数据驱动
      - 跨页面: 兼容页面名、URL路径、URL带query参数三种格式

    合理性: nav_map 页面名是真实业务名词，比 operation 关键词更可靠
      - operation 可能是"查询辅材配方"，但页面只显示"辅材配方"
      - 页面名是"辅材配方"，页面标题/Tab名应该包含它

    覆盖性: 被 _infer_control_type_from_operation 调用，覆盖所有控件类型推断

    Args:
        target_page: 目标页面（可能是页面名、URL路径、URL带query参数）
        project: 项目名（用于查 nav_map，空则用 config.project_name）

    Returns:
        str: 页面名（如"辅材配方"）；未找到返回空字符串
    """
    if not target_page or target_page in ("/", "/login"):
        return ""
    try:
        from src.core.config_loader import config

        # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps.nav_json → nav_graph（同上）
        from src.pipeline.utils.nav_graph_read import read_nav_map

        if not project:
            project = getattr(config, "project_name", "") or ""
        if not project:
            return ""
        nav_data = read_nav_map(project)

        # 收集 path → text 映射（flat_list + menu_groups items + tab_items）
        # ★ v21 修复: 用完整path作为key，不用base_path（避免多Tab覆盖）
        path_to_text = {}
        # ★ v21 新增: tab_param → text 映射（精确匹配?tab=xxx）
        tab_param_to_text = {}
        # ★ v21 新增: base_path → [text列表] 映射（兜底模糊匹配）
        base_path_to_texts = {}

        def _add_path_text(item: dict):
            if not item:
                return
            _p = str(item.get("path", "") or item.get("url", "") or "").strip()
            _t = str(item.get("text", "") or item.get("name", "") or "").strip()
            if _p and _t:
                path_to_text[_p] = _t
                # 提取?tab=xxx参数（精确匹配Tab）
                if "?tab=" in _p:
                    _tab_part = _p.split("?tab=")[1].split("&")[0].split("#")[0]
                    if _tab_part:
                        tab_param_to_text[_tab_part] = _t
                # 提取#tab=xxx参数（hash参数也匹配）
                if "#tab=" in _p:
                    _tab_part = _p.split("#tab=")[1].split("&")[0].split("#")[0]
                    if _tab_part:
                        tab_param_to_text[_tab_part] = _t
                # base_path → [text列表]（兜底，不覆盖）
                if "?" in _p or "#" in _p:
                    _base = _p.split("?")[0].split("#")[0]
                    if _base not in base_path_to_texts:
                        base_path_to_texts[_base] = []
                    if _t not in base_path_to_texts[_base]:
                        base_path_to_texts[_base].append(_t)

        for item in nav_data.get("flat_list", []) or []:
            _add_path_text(item)
        for mg in nav_data.get("menu_groups", []) or []:
            for item in mg.get("items", []) or []:
                _add_path_text(item)
            for tab_item in mg.get("tab_items", []) or []:
                _add_path_text(tab_item)

        if not path_to_text:
            return ""

        # target_page 可能是页面名、URL路径、URL带query参数
        # 1. 如果target_page本身就是页面名（在path_to_text的values中），直接返回
        for _path, _text in path_to_text.items():
            if target_page == _text:
                return target_page

        # 2. ★ v21 优先: 如果target_page含?tab=xxx，用tab参数精确匹配
        if "?tab=" in target_page:
            _target_tab = target_page.split("?tab=")[1].split("&")[0].split("#")[0]
            if _target_tab in tab_param_to_text:
                return tab_param_to_text[_target_tab]
        if "#tab=" in target_page:
            _target_tab = target_page.split("#tab=")[1].split("&")[0].split("#")[0]
            if _target_tab in tab_param_to_text:
                return tab_param_to_text[_target_tab]

        # 3. 完整path精确匹配
        if target_page in path_to_text:
            return path_to_text[target_page]

        # 4. base_path模糊匹配（返回第一个匹配的text，不覆盖）
        _target_normalized = target_page.split("?")[0].split("#")[0].rstrip("/")
        if _target_normalized in base_path_to_texts:
            _texts = base_path_to_texts[_target_normalized]
            if _texts:
                return _texts[0]  # 返回第一个匹配（通常是主导航项的text）

        # 5. 最后兜底：子串匹配
        for _path, _text in path_to_text.items():
            _path_normalized = _path.split("?")[0].split("#")[0].rstrip("/")
            if (
                _path_normalized
                and _target_normalized
                and (
                    _path_normalized in _target_normalized
                    or _target_normalized in _path_normalized
                )
            ):
                return _text

        return ""
    except Exception as _e:
        _log.debug("[v21] nav_map 页面名反查失败(不阻塞): %s", str(_e)[:80])
        return ""
