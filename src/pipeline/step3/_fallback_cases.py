# -*- coding: utf-8 -*-
"""兜底意图生成（v10）—— AI 不可用时使用，输出含基础 actions 的可执行用例。

★ Fix21 (2026-07-07): 复用 rule_extractor 的控件/操作/断言推断函数
  原bug: 独立硬编码，用原始 operation 文本当 target_hint（如"用户按辅材类型查询辅材信息 入口"），
         Step4 根本找不到这个元素。check 也是描述性文字"页面展示{operation}功能"。
  修复: 复用 rule_extractor 的 _infer_control_type_from_operation + _infer_submit_button_text
        + _build_ui_check_by_operation，按 operation 类型生成固定的 actions + checks 模板。
  契约: §4.1 "规则优先 + AI 补漏" — 即使是兜底，也应该用规则的逻辑生成确定性用例。
        §4.2 "Playwright 选择器只能用通用语义" — target_hint 禁止用业务功能名。
"""

import logging
from src.core.guarded import guard
import re
import uuid
from typing import Any

# ★ RC-AE(2026-09-25): 反馈条选择器统一从全引擎单一事实源导入（Step3/Step4/Agent 同源）。
from src.pipeline.utils.feedback_selectors import ERROR_SELECTOR_CSV as _ERROR_SELECTOR_CSV

# ★ 根因修复(2026-09-27)：变更型操作「触发→录入→提交」状态机的唯一实现（SSOT）。
#   与 `_rule_extractor_actions._build_actions` 共用同一编排，消除双口径漂移。
#   模块级导入（非循环依赖：`_rule_extractor_buttons` 不 import 任何兄弟模块）。
from src.pipeline.step3._rule_extractor_buttons import (
    build_interaction_actions,
    infer_confirm_hint,
    classify_operation_category,
    mutation_flow_kind,
)

_log = logging.getLogger(__name__)

# ── ★ 深断言通用选择器/常量（2026-08-21 推翻宽松断言层）──
#   设计目标：断言必须可证伪、对任一靶子通用、不依赖 SUT DB schema。
#   列表行数提取约定：统计 table/tbody tr 或带 data-testid=list-row 的行数量。
_LIST_ROW_SELECTOR = (
    "table tbody tr, .ant-table-tbody tr, .el-table__body-wrapper tr, "
    "[data-testid='list-row'], .list-row, .table-row, .ant-list-item, "
    ".el-list__item, li[data-row], .row"
)
_LIST_CONTAINER_SELECTOR = (
    "table, .ant-table, .el-table, [role='grid'], .list, .ant-list, .el-list, "
    "[data-testid='list-container']"
)
# 字段级错误提示选择器
# ★ RC-AE(2026-09-25): 改引全引擎单一事实源（原本地词表漏 `.flash-danger` /
#   `[data-testid*='error']` 等框架无关形态，仅靠 Step4 侧并集兜底；现同源）。
_FIELD_ERROR_SELECTOR = _ERROR_SELECTOR_CSV
# 表单/弹窗容器（验证"新增表单打开"的业务信号）
_FORM_CONTAINER_SELECTOR = (
    "form, [role='form'], [role='dialog'], [role='alertdialog'], "
    ".ant-modal, .ant-drawer, .ant-form, .el-dialog, .el-drawer, .el-form"
)
# ★ E2 根因修复(2026-09-26)：需求语义中"数据录入/字段校验"的通用词根。
#   命中即判定该需求以**表单**为被测对象（V01/V02/UX02 才按表单形态断言）。
#   ★ E2d 根因修复(2026-09-26)：词表曾混入**业务实体名**（名称/价格/库存/编号/
#   编码/工号/账号）与**裸行为动词**（验证/校验）→ 两类误判同时发生（实测 86 需求）：
#     · 误判为表单：REQ-005「验证内置账号凭证」(登录页)、REQ-010「展示资产列表字段」
#       (只读列表页)、REQ-049/050/051（访问控制）—— 成因是"账号/字段/验证"命中，
#       于是这些只读/登录/权限页也被要求"进入录入表单 + 有必填标记" → 必然假 FAIL；
#     · 且业务实体名是**靶子专属**的（换到医疗/制造靶子就全失效），违反 §1「通用」。
#   修复：只保留**框架无关**的语义，并分三层（单一事实源，供 E2/E2c 共用）：
#     A 控件级（单独命中即成立）：被测对象就是一个录入控件/表单容器；
#     B 校验级 + C 字段级（须**同时**命中）：字段级校验语义，只有配"字段"才成立
#       —— 这样"验证登录凭证"（无字段）不成立、"展示列表字段"（无校验语义）不成立。
_FIELD_FORM_CONTROL_KEYS = (
    "表单", "输入框", "录入", "填写", "输入", "下拉", "勾选", "复选", "选择",
)
_FIELD_FIELD_KEYS = ("字段",)
_FIELD_VALIDATION_KEYS = (
    "必填", "唯一", "格式", "数据类型", "数值", "长度", "范围", "边界", "校验",
    "验证",
)
# ★ 兼容别名：历史调用方/自证脚本按此名导入（语义即"录入型的判定词表"）。
_FIELD_SEM_KEYS = (
    _FIELD_FORM_CONTROL_KEYS + _FIELD_FIELD_KEYS + _FIELD_VALIDATION_KEYS
)
# ★ E2 根因修复(2026-09-26)：页面内容锚点（跨框架/跨页面类型通用）。
#   用于"非表单需求"的 UI 布局用例——断言被测页面**成功渲染出内容承载元素**
#   （非白屏/非错误页），替代此前对只读页面强行断言的"创建表单 + 必填红星"。
#   覆盖：原生语义标签（main/table/form/h1）、ARIA 语义（role=main）、
#   常见容器类名（container/content）——任一命中即视为页面已渲染。
_PAGE_CONTENT_SELECTOR = (
    "main, [role='main'], #content, .container, .content, .wrapper, "
    "form, table, [role='grid'], h1, h2"
)

# ── ★ 声明支持矩阵──
#   根因（accept_sut_0923 实测）：交叉维度兜底模板对 viewport_rendering_check
#   硬编码 375/768/1024/1920 四档（含 mobile 375px），而靶子的需求往往从未声明
#   移动端——实测 fullcov_sut 的 requirements.md 通篇无"响应式/移动端/viewport/
#   WCAG"字样，其 CSS 明确"朴素静态样式"且无任何 media query → 375px 必然横向溢出
#   → 必然假 FAIL（引擎自造需求）。
#   通用规则：兼容性测试只覆盖**系统声明支持的环境矩阵**。默认桌面档；
#   声明支持移动端/平板的靶子通过 config 覆盖 `support_viewports` 即可纳入。
#   单一事实源：本常量同时被 _modeling_intents 的页面级用例归一化引用。
DECLARED_SUPPORT_VIEWPORTS: list[dict[str, Any]] = [
    {"w": 1366, "h": 768, "label": "desktop"},
    {"w": 1920, "h": 1080, "label": "desktop-fhd"},
]


def declared_support_viewports() -> list[dict[str, Any]]:
    """本项目的声明支持矩阵（config 可覆盖 `support_viewports`）。"""
    with guard("declared_support_viewports: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config as _cfg

        _ov = getattr(_cfg, "support_viewports", None)
        if isinstance(_ov, list) and _ov:
            _vps = [dict(v) for v in _ov if isinstance(v, dict) and v.get("w")]
            if _vps:
                return _vps
    return [dict(v) for v in DECLARED_SUPPORT_VIEWPORTS]


def matrix_supports_mobile(viewports: list[dict[str, Any]] | None = None) -> bool:
    """声明矩阵是否覆盖移动端（宽度 < 768 视为移动端）。"""
    _vps = viewports if viewports is not None else declared_support_viewports()
    return any(int(v.get("w") or 0) < 768 for v in _vps)


# ── ★ type → required_tracks 映射（2026-09-16 同步 3 轨一致性）──
# 规则：每种测试类型按其覆盖范围映射合理轨道，不是全单轨 ["ui"]
# 理由：
#   功能  → UI 提交 + API 处理 + DB 验证 = 3 轨端到端
#   安全  → UI 展示 + API 拦截 + DB 防脏 = 3 轨联动
#   性能  → UI 加载(LCP) + API 响应时间 + DB 查询耗时 = 3 轨
#   UI    → 纯视觉/交互，单轨
#   易用  → 纯操作友好度，单轨
#   兼容  → 纯视觉/分辨率，单轨
_TYPE_TRACK_MAP: dict[str, list[str]] = {
    "功能": ["ui", "api", "db"],
    "安全": ["ui", "api", "db"],
    "性能": ["ui", "api", "db"],
    "UI": ["ui"],
    "易用": ["ui"],
    "兼容": ["ui"],
}


def _tracks_for_type(case_type: str) -> list[str]:
    """按 type 返回合理的 required_tracks，兜底给 ["ui"]。"""
    return list(_TYPE_TRACK_MAP.get(str(case_type or "").strip(), ["ui"]))


# ── ★ 通用轨道探针生成器（2026-09-16 同步 3 轨一致性）──
# fallback 模板的 checks 默认只有 ui 轨道断言 → _normalize_required_tracks
# 会把 api/db 从 required_tracks 裁掉。这两个函数生成**通用但真实**的
# api/db 轨道探针 check，让 normalize 后保留多轨声明。
def _gen_api_probe_check() -> dict | None:
    """API 轨道通用探针。

    ★ 2026-09-23 根治（D30，消除 146 例 PF 性能用例假 SKIP）：
      原实现发射 `api_call_count_check{url_pattern:".*"}`，语义="随便调用一次就行"。
      这是**实现方式**（SSR vs SPA）的性质，**不是任何需求**的性质（详见 _api.py
      D20 论证）。执行侧会把 `.*` 物化为 `http://<target>/.*` 并 HTTP 抓取 → 404 →
      软性失败 → 性能用例的唯一客观断言全软失败 → 被判 SKIP（防假绿也不记 PASS）。
      而性能用例真正的客观断言是 `performance_lcp_check`（FCP/LCP 真实测量，
      由 _UI_CHECK_DISPATCH 处理），却被这条假 API 探针压制成 SKIP。

      修复：不再发射无意义的"通配计数"探针。若用例确实没有具体 API 端点断言，
      则 api 轨本就不该被声称（交由 _normalize_required_tracks 裁掉），绝不用
      一条永远不可评估的通配探针来"保轨道"。这与 D20 同源纪律一致：
      **词表/探针漂移只准补执行器或剔除，不准用假断言顶替**。
    """
    return None


def _gen_db_probe_check(req: dict = None) -> dict | None:
    """DB 轨道通用探针 —— ★ 2026-09-27 已停用（与 `_gen_api_probe_check` 同源纪律）。

    根因（5层why）:
      L1 现象: 若干用例恒 PASS 且 expected/actual/details 全空（实测 conf=0.98 假绿），
         典型代表 TC-REQ-005-E01 等 9 条 `checks=['db_table_accessed_check']`。
      L2 直接: 这些用例的唯一断言就是本函数发射的探针；探针语义=「目标库可访问 +
         有业务表可读」，只要库能连上即 passed=True → 无证据恒绿。
      L3 为何: 生成端把探针当作"保住 db 轨道声明"的手段（见
         `_normalize_required_tracks` 第 1 轮 / `_enrich_checks_for_tracks`），
         **用一条与环境相关的探针顶替了需求断言**。
      L4 为何有害: 探针占住了 db 轨道的坑位 → `verification_plan_builder` 认为
         db 轨已被覆盖（`covered_tracks` 含 db）→ 设计好的 RAG/Schema 兜底
         (`_rag_supplement` → `_build_db_checks`) **不再产出真实 db 断言**，
         真正可判定的持久层断言被一条恒真探针挡在门外（"劣币驱逐良币"）。
      L5 根本: 与 api 轨探针同源的纪律问题 ——
         **词表/探针漂移只准补执行器或剔除，不准用假断言顶替**；
         而且"环境探针的结果不是判定"（判定语义 SSOT 见
         `step4/verification/_track_protocol.is_probe_check`）。

    修复: 不再发射。若用例确实没有可判定的 db 断言，db 轨本就不该被探针顶替——
      `_normalize_required_tracks` 第 2 轮会诚实裁剪该声明；若需求确实主张 db 维度，
      则交由 Step4 `_rag_supplement` 依 RAG/Schema 产出**真实可判定**的 db 断言
      （或诚实地落为"无判定"），而非用探针构造假绿。
    通用性: 跨系统/行业 —— 纯纪律约束，零业务硬编码。
    """
    return None


def _enrich_checks_for_tracks(checks: list[dict], required_tracks: list[str],
                              req: dict = None) -> list[dict]:
    """★ 按 required_tracks 自动补齐缺失轨道的探针 check（2026-09-16）。

    在模板声明了 ["ui","api","db"] 但 checks 只有 ui 轨道断言时，
    自动注入 api/db 通用探针 → _normalize 后保留多轨。
    这解决了"type→track 映射正确但 normalize 把 api/db 裁掉"的一致性问题。
    """
    if not checks or not required_tracks:
        return checks
    _present_tracks = {c.get("track") for c in checks if c.get("track") in ("ui", "api", "db")}
    extra = []
    if "api" in required_tracks and "api" not in _present_tracks:
        api_probe = _gen_api_probe_check()
        if api_probe:
            extra.append(api_probe)
    if "db" in required_tracks and "db" not in _present_tracks:
        db_probe = _gen_db_probe_check(req)
        if db_probe:
            extra.append(db_probe)
    return checks + extra


def _gen_unique_value(
    req: dict[str, Any], field_hint: str = "name", scope: str = ""
) -> str:
    """★ 深断言依赖：生成唯一业务记录名模板占位（用于列表可见性断言）。

    ★ 权威生产（2026-09-27 起）：唯一值真值只在执行端运行时物化
      （step4_executor._materialize_run_unique_values，run 级单一随机值、fill/check 同源、
      跨 run 不撞库）；本函数仅产形态占位（确定性后缀，跨 run 同值会撞约束，不得再当权威）。
      why: 原确定性值跨 run 复用撞唯一约束、且同 req 各用例 scope 不同导致创建值与
      编辑/列表校验期望值分裂（假 FAIL/假 PASS 温床）。

    ``scope`` 用于区分**同需求下不同用例**（如 P01 与 UX02 各建一条记录）；
    同一用例内的 fill 与 check 必须用同一 scope（同一值），跨用例不同 scope
    （彼此独立唯一，消除"用例 A 建了记录、用例 B 再建同名被拒"的执行序依赖）。

    通用性：跨系统/跨行业——唯一值只用于"断言列表出现该记录"，不绑定业务。
    """
    rid = str(req.get("req_id", "") or "REQ").strip().replace(" ", "_")
    # 用 req_id 的稳定哈希派生一个短后缀，保证同需求同值、不同需求不同值
    _seed = f"{rid}:{field_hint}" + (f":{scope}" if scope else "")
    _suffix = uuid.uuid5(uuid.NAMESPACE_DNS, _seed).hex[:8]
    if field_hint in ("name", "姓名", "名称"):
        # ★ 通用性修复(2026-09-23): "测试患者"是 hospital_sut 业务词泄漏——
        #   资产/商品等任何系统的唯一记录名必须业务无关
        return f"自动化记录{_suffix[-6:]}"
    if field_hint in ("phone", "手机", "电话"):
        # 数字型唯一值必须只含数字：hex 后缀含 a-f 会造出非法号码（SUT 正确拒绝 → 假 FAIL）
        return "138" + str(int(_suffix[:8], 16)).rjust(8, "0")[-8:]
    if field_hint in ("code", "编号", "编码"):
        return f"T{_suffix[:7].upper()}"
    return f"自动化值{_suffix[-6:]}"


# ── ★ 创建语义门控(2026-09-22 needle 污染根修) ──
#   深断言族（唯一值列表可见 + 行数+1）只对"创建记录"型需求有意义。
#   对登录失败提示/查询/展示类需求注入"列表出现记录 TEE4AF94"是类别错误 →
#   机械断言必败 → 假 FAIL。通用动词表，零业务硬编码。
#   ★ 单一事实源(2026-09-24): 判定收敛到 _rule_extractor_keywords.is_record_creation，
#     由「创建动词 + 否定/只读/审计/拦截 语境否决」判定主意图（见该函数 docstring）。
def _is_record_creation_semantics(
    operation: str, desc: str, expected_feedback: str = ""
) -> bool:
    """需求主意图是否为"创建记录"型（决定深断言形态）。

    ★ 单一事实源(2026-09-24): 委托 _rule_extractor_keywords.is_record_creation。
      此前仅扫 operation+description 的创建动词，会把
      「403 权限不足 / 审计日志 / GET 请求表单页」等否定、只读、审计语境误判为创建型。
    """
    from src.pipeline.step3._rule_extractor_keywords import is_record_creation

    return is_record_creation(operation, desc, expected_feedback)


def _is_edit_operation(
    operation: str, description: str = "", expected_feedback: str = ""
) -> bool:
    """需求主意图是否为"编辑已有记录"型（决定入口动作与断言形态）。

    ★ RC-AP(2026-10-14) 单一事实源委托：判定与否定语境排除都在
      `_rule_extractor_keywords.is_edit_operation`，此处只做转发，避免第二处实现。
    """
    from src.pipeline.step3._rule_extractor_keywords import is_edit_operation

    return is_edit_operation(operation, description, expected_feedback)


def _infer_unique_field(req: dict[str, Any]) -> str:
    """★ 从 operation/description 推断"唯一业务字段"语义（name/phone/code/...）。

    用于决定 _gen_unique_value 的 field_hint，使列表可见性断言更贴合需求。
    通用性：零业务硬编码，仅按通用词根推断。
    """
    blob = " ".join(
        str(req.get(k, "") or "")
        for k in ("operation", "description", "module", "feature_label")
    )
    if any(k in blob for k in ["姓名", "名字", "患者名", "名称", "标题"]):
        return "name"
    if any(k in blob for k in ["手机", "电话", "phone", "联系电话"]):
        return "phone"
    if any(k in blob for k in ["编号", "代码", "code", "编码", "工号", "账号"]):
        return "code"
    # 默认按 name（最常见业务唯一标识，如患者姓名）
    return "name"


def _has_sut_db_target() -> bool:
    """★ 判断 config.TARGET_DB 是否明确指向 SUT 库。

    若指向 SUT 自有库，才额外加 db_record_exists_check（深断言构造器见
    src/pipeline/step3/case_rules/base.py 的 _build_check，DB 轨字段：table/operator）。
    hospital_sut 的 DB 是独立 SQLite，引擎 TARGET_DB 默认指向引擎自身 MySQL，
    因此默认返回 False —— 但 UI 功能性深断言仍成立。

    通用性：读项目配置（config，含控制台落库的 project_configs）+ env 兜底，
    不绑定 hospital_sut。
    """
    import os

    # ★ 2026-09-23 根治（SUT DB 判定看不见项目配置）：
    #   根因：原实现只读 os.environ。控制台新建项目的 TARGET_DB_* 落在
    #   project_configs.config_json 并经 config.reload() 生效，**不写进程 env**，
    #   于是"已配置 SUT 库"的项目被判为未配置 → 退化为发射 db_table_accessed_check
    #   （Step4 未注册该类型）→ check_guard 剔除 → db 轨彻底空转。
    #   通用性：项目配置优先、env 兜底，任何配置来源都能被识别。
    def _cfg(key: str, default: str = "") -> str:
        with guard("_cfg: from src.core.config_loader import c", log=_log):
            from src.core.config_loader import config

            _v = config._get(key, default)  # noqa: SLF001 — 复用统一取值口
            if _v not in (None, ""):
                return str(_v).strip()
        return str(os.environ.get(key) or default).strip()

    _db_type = _cfg("TARGET_DB_TYPE").lower()
    # 引擎约定：SUT 库名/文件 ≠ 引擎自身库 ai_test_engine 即视为指向 SUT
    _db_name = _cfg("TARGET_DB_NAME")
    _db_file = _cfg("TARGET_DB_FILE")
    _engine_db = _cfg("STORAGE_MYSQL_DATABASE", "ai_test_engine")
    if _db_name and _db_name != _engine_db:
        return True
    # sqlite：库就是文件，配置了 TARGET_DB_FILE 即指向 SUT 库
    if _db_type == "sqlite" and _db_file:
        return True
    # 显式开关：TARGET_DB_POINTS_TO_SUT=1 也可强制开启
    if _cfg("TARGET_DB_POINTS_TO_SUT") in ("1", "true", "True"):
        return True
    return False


def _normalize_required_tracks(required_tracks: list[str], checks: list[dict]) -> list[str]:
    """★ 诚实化 required_tracks（2026-08-21；2026-09-27 停用自动探针补齐）。

    单轮逻辑：
    诚实化裁剪：若 checks 无某轨道断言，从 required_tracks 去掉该声明。

    ★ 历史与纪律(2026-09-27)：原"第 1 轮自动补齐探针"已停用——探针
      （api_call_count_check 通配计数 / db_table_accessed_check 环境访问痕迹）
      的通过只证明"环境就绪"，与需求是否被满足无关，属**非判定**；用它"保轨道"
      会（a）制造无证据假 PASS，（b）占住坑位令 RAG/Schema 兜底不再产出真实断言。
      纪律：**生成端只能发射执行端能判定的断言**，绝不用假断言顶替
      （一致于 `_gen_api_probe_check` / `_gen_db_probe_check` 的停用说明）。
      真实多轨断言应由模板/RAG 依需求产出；产不出则该声明诚实裁剪。
    """
    _out: list[str] = []
    _present: set[str] = {
        ch.get("track") for ch in checks
        if ch.get("track") in ("ui", "api", "db")
    }
    for t in required_tracks:
        if t not in ("ui", "api", "db"):
            if t not in _out:
                _out.append(t)
            continue
        if t in _present:
            if t not in _out:
                _out.append(t)
        else:
            _log.info(
                "[fallback] required_tracks 含 %r 但 checks 无该轨断言 → 移除该声明", t
            )
    if not _out:
        _out = ["ui"]
    return _out


# ── ★ 断言语义对齐（2026-09-23）—— 声明的期望必须被至少一条 check 承载 ──
#   根因（ModSystem round3 实测）：交叉维度兜底模板把「维度通用 check」挂在所有
#   同类用例上 —— TC-<RID>-UX01 标题/描述声明「空状态有引导文案」，实际 checks
#   只有 click_target_size_check + viewport_overflow_check，**没有任何断言在验证
#   "引导文案"**。后果：用例不可能证伪它声明的东西，其 FAIL 被归因到错误维度
#   （点击目标尺寸），报告把「这条用例没测」伪装成「测了且失败」。
#   通用规则（零业务硬编码）：从用例**自身的** description 里抽取被声明为
#   文案/提示断言的引号样例（如 (如'暂无数据，点击新增')），若用例没有任何
#   text_contains_check 承载它，则按该样例补一条 text_contains_check。
#   注意：这是"补齐缺失断言"，不删除任何既有 check（既有 FAIL 证据仍需保留）。
_TEXT_DECL_RES = (
    re.compile(r"断言[^。；;]{0,24}(?:文案|文本|提示|字样|显示|出现)"),
    re.compile(r"(?:文案|提示|文本|字样)[^。；;]{0,12}(?:出现|存在|可见|有)"),
)
_QUOTED_SAMPLE_RE = re.compile(r"[\u2018\u2019\u201c\u201d'\"]([^\u2018\u2019\u201c\u201d'\"]{2,40})[\u2018\u2019\u201c\u201d'\"]")


def _declares_text_assertion(desc: str) -> bool:
    """用例描述是否声明了"某段文案/提示应当出现"这类断言。"""
    return any(rx.search(desc or "") for rx in _TEXT_DECL_RES)


def _first_short_needle(desc: str) -> str:
    """从描述里的引号样例中取第一个短针（供 text_contains_check 使用）。

    text_contains_check 的 expected 约定是"具体短词"（见 _prompts.py 生成约束），
    因此按中英文逗号切分后取首段，避免把整句当针导致必然 0 匹配。
    """
    for _raw in _QUOTED_SAMPLE_RE.findall(desc or ""):
        _seg = re.split(r"[,，、;；]", _raw.strip())[0].strip()
        if 2 <= len(_seg) <= 16:
            return _seg
    return ""


def _align_checks_with_declared_expectation(cases: list[dict]) -> list[dict]:
    """统一收口：为"声明了文案断言却无对应断言"的兜底用例补齐 text_contains_check。

    原地修改并返回 cases。通用性：不依赖任何业务字段/模块名，只读用例自身描述。
    """
    _added = 0
    for _case in cases or []:
        if not isinstance(_case, dict):
            continue
        _desc = str(_case.get("description") or "")
        if not _declares_text_assertion(_desc):
            continue
        _checks = _case.setdefault("checks", [])
        if any(
            (ch or {}).get("check", {}).get("type") == "text_contains_check"
            for ch in _checks if isinstance(ch, dict)
        ):
            continue
        _needle = _first_short_needle(_desc)
        if not _needle:
            _log.warning(
                "[fallback] 断言语义对齐缺口：用例 %s 声明了文案断言，"
                "但描述未给出可验证样例 → 无法自动补齐（需 AI 派生断言针）",
                _case.get("case_id"),
            )
            continue
        _checks.append({
            "track": "ui",
            "precision": "fuzzy",
            "check": {
                "type": "text_contains_check",
                "expected": _needle,
                "operator": "contains",
            },
            "expect": f"页面出现用例声明的文案（{_needle}）",
            "_alignment_source": "declared_expectation",
        })
        _added += 1
        _log.info(
            "[fallback] 断言语义对齐：用例 %s 声明文案断言但无承载 check → 按自身声明补 text_contains_check(%r)",
            _case.get("case_id"), _needle,
        )
    if _added:
        _log.info("[fallback] 断言语义对齐合计补齐 %d 条缺失断言", _added)
    return cases


def _count_list_rows_action() -> dict[str, Any]:
    """★ 深断言基线动作：提交前统计列表行数。

    供 list_row_count_check 的 baseline_action_index 引用（索引 1，因本 action 排在首位）。
    Step4 执行时记录此刻行数，提交后对比 delta。通用 list-row selector 见 _LIST_ROW_SELECTOR。
    """
    return {
        "type": "count_list_rows",
        "target_hint": "列表/表格",
        "selector": _LIST_ROW_SELECTOR,
        "store_as": "_baseline_row_count",
    }


def _inject_unique_value(
    actions: list[dict[str, Any]], unique_val: str, operation: str
) -> None:
    """★ 把确定性唯一值注入 P01 的 fill action（actions 与断言一致，§13.3）。

    优先注入名称/姓名类输入框；无则注入第一个 fill 动作。不修改其他 action。
    通用性：基于 target_hint 关键词匹配，不绑定业务字段名。
    """
    _name_hints = (
        "名称",
        "姓名",
        "名字",
        "标题",
        # ★ Fix E(2026-09-27)：多字段录入的 target_hint 统一为 `name=<key>` 形态，
        #   若不识别则唯一值会落到"第一个 fill"（可能是 price/stock 数字框）→ 数据被污染。
        #   此处按字段 key 语义识别名称类，避免误改非名称字段。
        "name=name",
        "name=title",
        "name=姓名",
        "name=名称",
        "主输入框",
        "input",
    )
    _target_action = None
    for a in actions:
        if a.get("type") != "fill":
            continue
        _hint = str(a.get("target_hint", ""))
        if any(h in _hint for h in _name_hints):
            _target_action = a
            break
    if _target_action is None:
        for a in actions:
            if a.get("type") == "fill":
                _target_action = a
                break
    if _target_action is not None:
        _target_action["value"] = unique_val
        _log.info("[fallback] 注入唯一值到 fill action: %s=%r", _target_action.get("target_hint"), unique_val)


_TABLE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# SUT schema 缓存（进程内一次枚举）：{"tables": {表名: [列名]}}
_SUT_SCHEMA_CACHE: dict[str, Any] = {}


def _sut_schema() -> dict[str, list[str]]:
    """只读枚举 SUT 目标库结构，返回 ``{表名: [列名]}``；失败返回 ``{}``。

    ★ F3 根因修复(2026-09-26)：DB 深断言必须落在**真实存在的表/列**上。
      与 Step4 ``db_table_accessed_check`` 同源能力（DatabaseAgent 四类库），
      使 Step3 不可能再"从需求文本臆造表名"。枚举失败即返回空 → 调用方跳过
      DB 深断言（诚实跳过 > 造出假 FAIL）。
    """
    if "tables" in _SUT_SCHEMA_CACHE:
        return _SUT_SCHEMA_CACHE["tables"]
    out: dict[str, list[str]] = {}
    try:
        from src.extensions.db.db_agent import DatabaseAgent

        agent = DatabaseAgent()
        agent.connect()
        _dt = str(getattr(agent, "_db_type", "") or "").strip().lower()

        def _cell(row: Any, key: str, idx: int) -> str:
            if isinstance(row, dict):
                return str(row.get(key) or "")
            try:
                return str(row[idx])
            except Exception:  # noqa: BLE001
                return ""

        if _dt == "sqlite":
            _r = agent.query("SELECT name FROM sqlite_master WHERE type='table'")
            for _row in (_r or {}).get("data") or []:
                _t = _cell(_row, "name", 0)
                if _t and not _t.startswith("sqlite_"):
                    out[_t] = []
            for _t in list(out):
                try:
                    _r2 = agent.query('PRAGMA table_info("%s")' % _t)
                except Exception:  # noqa: BLE001
                    continue
                for _row in (_r2 or {}).get("data") or []:
                    _c = _cell(_row, "name", 1)
                    if _c:
                        out[_t].append(_c)
        else:
            # mysql / postgresql / oracle / yasdb：information_schema 同形态
            _sql = {
                "mysql": (
                    "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE()"
                ),
                "postgresql": (
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema='public'"
                ),
                "oracle": (
                    "SELECT table_name, column_name FROM user_tab_columns"
                ),
                "yasdb": "SELECT table_name, column_name FROM user_tab_columns",
            }.get(_dt)
            if _sql:
                _r = agent.query(_sql)
                for _row in (_r or {}).get("data") or []:
                    _t = _cell(_row, "TABLE_NAME", 0) or _cell(_row, "table_name", 0)
                    _c = _cell(_row, "COLUMN_NAME", 1) or _cell(_row, "column_name", 1)
                    if _t:
                        out.setdefault(_t, [])
                        if _c:
                            out[_t].append(_c)
    except Exception as _e:  # noqa: BLE001
        _log.info("[fallback] SUT schema 枚举失败（跳过 DB 深断言）: %s", str(_e)[:100])
        return {}
    if out:
        _SUT_SCHEMA_CACHE["tables"] = out
    return out


def _infer_entity_table(req: dict[str, Any]) -> str:
    """★ 推断业务实体表名（仅用于可选 DB 深断言，TARGET_DB 指向 SUT 时）。

    通用启发式：需求语义体里的实体词根（中文映射 + ASCII 词根）与 SUT 库
    **真实表名**求匹配。零业务硬编码（不写死 patients），跨系统/行业通用。

    ★ F3 根因修复(2026-09-26)：
      现象：实测 ``DB[exists] 非法表名: 资产管理系统（fullc`` → 用例被判 FAIL，
        同 run 的 AI 根因分析亦判 ENVIRONMENT_ERROR（配置错误）。
      根因：兜底分支取 module 首词 ``_first[:12]`` 当表名 —— 需求文本是中文
        自然语言，取出的短语**不是 SQL 标识符**，也不存在于任何库。
      修复：① 只在 SUT 库真实存在的表中选（`_sut_schema`）；
            ② 返回值必须是合法 SQL 标识符，否则返回 ""。
      契约：找不到合理表名时**返回空串**（调用方据此跳过 DB 断言）——这是本函数
        文档早已声明的契约，原实现违反之。
    """
    blob_keys = ("description", "expected_feedback", "condition_expr", "source_quote")
    # 常见实体词根 → 表名片段（跨行业通用映射，非靶子专属）
    _entity_map = [
        ("患者", "patient"), ("病人", "patient"), ("用户", "user"),
        ("医生", "doctor"), ("药品", "medicine"), ("药物", "medicine"),
        ("订单", "order"), ("商品", "product"), ("客户", "customer"),
        ("员工", "employee"), ("部门", "department"), ("角色", "role"),
        ("权限", "permission"), ("账单", "bill"), ("账单", "invoice"),
        ("排班", "schedule"), ("病历", "medical_record"), ("资产", "asset"),
        ("库存", "stock"), ("日志", "log"),
    ]

    def _roots_of(text: str) -> list:
        _r = [en for cn, en in _entity_map if cn in text]
        # ASCII 词根（需求里常带英文实体名/路径，如 "/assets/new"）
        _r += [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text)]
        return _r

    _tables = _sut_schema()
    if not _tables:
        return ""
    _tbl_lower = {t.lower(): t for t in _tables}
    # ★ 优先级：需求**动作**(operation) > 模块 > 其余语义体。
    #   单纯按词表顺序匹配会串味：实测"用户填写资产信息"这类需求里
    #   词表先命中"用户"→ 选到 users 表，而该需求真正的实体是资产（assets）。
    #   动作文本是"创建什么"的最强信号，故先看 operation。
    for _src in (
        req.get("operation"),
        req.get("module"),
        " ".join(str(req.get(k) or "") for k in blob_keys),
    ):
        for _r in _roots_of(str(_src or "")):
            if not _r or not _TABLE_IDENT_RE.match(_r):
                continue
            for _tl, _t in _tbl_lower.items():
                if _r == _tl or _r in _tl or _tl in _r:
                    return _t
    _log.info("[fallback] SUT 表 %s 与需求实体无匹配 → 跳过 DB 深断言: op=%r",
              sorted(_tables)[:8], str(req.get("operation") or "")[:40])
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# ★ 根因修复(2026-09-25) RC-AH/RC-AI/AJ: 规则兜底模板必须随**需求场景**分化
# ═══════════════════════════════════════════════════════════════════════════════
# 根因（5 层 why）:
#   L1 现象: 规则兜底（占全量用例 57%）对**任何**需求都发射同一套「新增表单」模板
#     —— V01/V02 硬编码 click「新增/新建/添加/创建」、N01/B01 硬编码
#     「提交非法输入 → 字段级错误提示可见 + 列表行数不变」。
#     实测: 检索类 REQ-067（搜索资产）的 B01/N01 断言"列表行数不变"，靶子按需求
#       正确返回 0 条匹配 → 行数变化 → **假 FAIL**；
#     访问控制类 REQ-022（viewer 无新增权）的 V01 点「新增」→ 控件不可达 →
#       表单容器 0 命中 → 引擎自造 HARNESS_ERROR；
#     登录类 REQ-024/058/059 的 V01 同样点「新增」→ 登录页无该控件 → ERROR。
#   L2 直接: 模板的 actions 与 checks 都假定"该需求进入一个可提交的新增表单"。
#   L3 为何: 需求语义（创建/检索/登录/访问控制）从未传导到 UI/异常/边界模板 ——
#     只有探针分支（_probe_actions）做过三态分化，主路径没做。
#   L4 为何反复: "哪种需求用哪套交互与断言"没有单一决策点，于是每处模板各自
#     硬编码一套"新增表单"假设，改一处漏三处。
#   L5 根本: 缺「需求场景 → 交互 + 断言模板」的**单一分类事实源**。
# 修复: 在规则兜底入口一次性分类需求场景（login / access_control / creation /
#   search / readonly），UI/异常/边界模板据此选择交互与断言；创建型保持原行为
#   （零行为变更），非创建型不再伪装成新增表单。
# 通用性: 分类只依据需求语义体（operation/description/expected_feedback）与既有
#   单一事实源词表（is_login_operation / _is_security_behavior），零业务硬编码。
def _classify_req_scenario(operation: str, desc: str, expected: str) -> str:
    """需求场景分类（规则兜底模板分化的**唯一决策点**）。

    返回 ``login`` | ``access_control`` | ``creation`` | ``search`` | ``readonly``。
    判定顺序（先特殊后一般）:
      1) 登录动作（提交凭证 / 请求登录页）→ ``login``
      2) 行为型访问控制（403/权限/角色/未登录/越权）→ ``access_control``
      3) 记录创建语义 → ``creation``
      4) 检索语义（搜索/查询/筛选/检索）→ ``search``
      5) 其余 → ``readonly``

    只扫 operation/description/expected_feedback（需求**语义体**），不扫
    module/source_quote —— 后者是文档章节标题/锚点（如 REQ-067 的
    source_quote="搜索与权限控制"），按构造不可靠，会把纯检索需求误判为访问控制。
    """
    _op = operation or ""
    try:
        from src.pipeline.step3._rule_extractor_keywords import is_login_operation

        _is_login = is_login_operation(_op)
    except Exception:  # noqa: BLE001
        _is_login = any(
            k in _op for k in ("登录", "登陆", "凭证", "鉴权", "认证", "口令")
        )
    if _is_login:
        return "login"
    try:
        from src.pipeline.step3.case_rules.base import _is_access_restriction_req

        _is_ac = _is_access_restriction_req(_op, desc, expected)
    except Exception:  # noqa: BLE001
        _is_ac = any(
            k in (_op + desc + expected)
            for k in ("403", "401", "权限", "越权", "未登录", "角色", "forbidden")
        )
    if _is_ac:
        return "access_control"
    if _is_record_creation_semantics(_op, desc, expected):
        return "creation"
    # ★ RC-AP(2026-10-14): 编辑/校验此前**不在**本决策点内，各模板遂各自再做一轮
    #   关键词子串扫描，与本函数结论互相矛盾（详见 is_edit_operation 注释）。
    #   统一由此处一次性判定，下游只读不推。
    try:
        from src.pipeline.step3._rule_extractor_keywords import (
            is_edit_operation as _is_edit_op,
        )

        if _is_edit_op(operation, desc, expected):
            return "edit"
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.pipeline.step3._rule_extractor_keywords import (
            is_field_constraint_requirement as _is_fc,
        )

        if _is_fc(operation, desc, expected):
            return "validation"
    except Exception:  # noqa: BLE001
        pass
    if any(k in _op for k in ("搜索", "查询", "筛选", "检索")):
        return "search"
    return "readonly"


# 受限操作动词 → 通用控件文本候选（跨系统/行业；只描述"动作语义"）
_RESTRICTED_ACTION_TEXTS: dict[str, str] = {
    "新增": "新增,新建,添加,创建",
    "创建": "新增,新建,添加,创建",
    "编辑": "编辑,修改,更新",
    "修改": "编辑,修改,更新",
    "删除": "删除,移除,清除",
    "导入": "导入,上传",
    "导出": "导出,下载",
    "查看": "查看,详情",
}


def _restricted_actions(*texts: str) -> list[str]:
    """从需求语义体解析「受限操作动词」（访问控制型需求的核心业务对象）。

    只认**动作语义词**（新增/编辑/删除…），不认角色名/权限词 —— 后者是约束
    而非被约束的动作。返回按出现顺序去重；无命中返回空列表（调用方退化处理）。
    """
    blob = " ".join(t for t in texts if t)
    hit: list[str] = []
    for verb in _RESTRICTED_ACTION_TEXTS:
        if verb in blob and verb not in hit:
            hit.append(verb)
    return hit


# 拒绝语义的通用可观察形态（服务端错误页 / 前端提示 / HTTP 状态）
_DENIAL_TEXT_KEYWORDS = (
    "403", "401", "权限不足", "无权限", "没有权限", "无权", "禁止访问",
    "拒绝访问", "访问被拒", "未授权", "越权", "forbidden", "unauthorized",
    "access denied", "permission denied", "not allowed",
)


def _access_control_checks(verbs: list[str]) -> list[dict[str, Any]]:
    """访问控制型用例的**行为断言**（替代"设计陈述文本匹配"）。

    ★ 根因修复(2026-09-25) FIX4/AJ: 访问控制需求原先被生成 `text_contains` /
      `semantic_check`，断言页面出现需求里的**设计陈述**（"不同角色获得对应的
      数据访问与操作权限"）。设计陈述是规格文字，页面上不存在 → Agent/AI 恒判
      FAIL（实测 TC-REQ-068-E01）—— 验证手段与期望性质错配，属引擎缺陷。
      修复: 断言改为**可观察行为** —— 低权身份执行受限操作后，系统给出拒绝
      （403/权限不足/重定向登录）；若受限操作仍成功则判失败（真越权）。
      通用性: 拒绝形态与业务/框架无关，零硬编码。

    `control_hints` 由 `_RESTRICTED_ACTION_TEXTS`（受限动词 → 通用控件文本候选）
    单点派生，执行端只消费候选、不自持第二份映射（防漂移）。
    """
    _verbs = [v for v in (verbs or []) if v]
    _hints = [
        _RESTRICTED_ACTION_TEXTS[v] for v in _verbs if v in _RESTRICTED_ACTION_TEXTS
    ]
    return [
        {
            "track": "ui",
            "precision": "exact",
            "check": {
                "type": "access_control_check",
                "expected": "低权身份执行受限操作被系统拒绝（403/权限不足/重定向登录）",
                "restricted_actions": _verbs,
                "control_hints": _hints,
                "require": "denied",
            },
            "expect": "受限操作被系统拒绝（访问控制行为符合需求）",
        },
    ]


def _access_control_actions(nav_target: str, verbs: list[str]) -> list[dict[str, Any]]:
    """访问控制型用例的交互：导航到模块页 → 尝试执行受限操作。

    受限操作以**控件文本候选**表达（点击后由系统给出拒绝），动词无对应控件时
    退化为直接导航（拒绝可能由页面级重定向/错误页体现）。
    """
    acts: list[dict[str, Any]] = [
        {"type": "navigate", "target_hint": nav_target or "/"}
    ]
    for verb in verbs[:2]:
        hint = _RESTRICTED_ACTION_TEXTS.get(verb)
        if hint:
            acts.append({"type": "click", "target_hint": hint})
    acts.append({"type": "screenshot"})
    return acts


def _verb_at(verbs: list[str], idx: int) -> list[str]:
    """取第 idx 个受限动词（不足时按序循环；无动词 → 空列表）。"""
    if not verbs:
        return []
    return [verbs[idx % len(verbs)]]


# 访问控制模板改写的目标用例（行为/UI 五条）与逐条语义
_AC_CASE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    # (case_id 尾缀, 场景, 标题后缀, 描述模板)
    ("P01", "happy_path", "访问控制-低权执行受限操作被拒",
     "以低权身份对{desc}执行受限操作「{verb}」，断言系统拒绝该操作"
     "（403/权限不足/重定向登录）"),
    ("N01", "error_path", "访问控制-另一受限操作同样被拒",
     "以低权身份对{desc}执行受限操作「{verb}」，断言访问控制对所有受限操作一致生效"),
    ("B01", "error_path", "访问控制-删除类受限操作被拒",
     "以低权身份对{desc}执行受限操作「{verb}」，断言高危操作同样被权限拦截"),
    ("V01", "happy_path", "UI-受限操作被拒反馈可见",
     "以低权身份对{desc}执行受限操作「{verb}」，断言界面给出可观察的拒绝反馈"
     "（拒绝页/无权限文案/登录墙）"),
    ("V02", "error_path", "UI-受限操作未越权成功",
     "以低权身份对{desc}执行受限操作「{verb}」，断言受限操作未被系统放行"
     "（未进入操作页/未出现成功反馈）"),
)

# ★ FIX4/AJ(2026-09-26): 探针分支（`fallback_functional_probe`）的访问控制 spec。
#   探针分支是**另一条独立生成路径**（静态 DOM 找不到入口按钮时触发），其
#   E01/V01/V02 同样带着"新增表单/字段校验"的通用断言 —— 与主分支是同一根因的
#   第二处表现。实测 accept_sut_final REQ-068：E01 由探针分支产出，断言
#   `semantic_check('不同角色获得对应的数据访问与操作权限')`（需求设计陈述）
#   → 页面上不存在该文字 → 恒 FAIL。故两条路径共用同一改写器，spec 各自声明。
_AC_PROBE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("E01", "happy_path", "访问控制-低权执行受限操作被拒",
     "以低权身份对{desc}执行受限操作「{verb}」，断言系统拒绝该操作"
     "（403/权限不足/重定向登录）"),
    ("V01", "happy_path", "UI-受限操作被拒反馈可见",
     "以低权身份对{desc}执行受限操作「{verb}」，断言界面给出可观察的拒绝反馈"
     "（拒绝页/权限不足/登录墙）"),
    ("V02", "error_path", "UI-受限操作未越权成功",
     "以低权身份对{desc}执行受限操作「{verb}」，断言受限操作未被系统放行"
     "（未进入操作页/未出现成功反馈）"),
)


def _apply_access_control_template(
    cases: list[dict[str, Any]],
    nav_target: str,
    verbs: list[str],
    module: str,
    desc: str,
    specs: tuple[tuple[str, str, str, str], ...] = _AC_CASE_SPECS,
    preserve_control_type: bool = False,
) -> None:
    """把「新增表单」形状的规则兜底模板改写为「访问控制行为」模板（原地修改）。

    ★ 根因修复(2026-09-25) FIX4/AJ: 需求场景分类（`_classify_req_scenario`）已把
      访问控制型需求识别出来，但 P01/N01/B01/V01/V02 五条模板仍按「进入新增表单→
      填值→提交→字段级错误」生成 —— 访问控制需求没有"新增表单"这条路径，
      断言又退化为需求设计陈述的文本匹配 → 恒 FAIL（实测 TC-REQ-068-E01）。
      修复: 这五条改写为**行为断言**（`access_control_check`）：导航到模块页 →
      尝试执行受限操作 → 断言系统拒绝；三条功能/安全用例分别覆盖不同的受限动词
      （新增/编辑/删除），两条 UI 用例覆盖"拒绝反馈可见"与"未越权成功"两个侧面。
      通用性: 受限动词 → 控件候选的映射来自 `_RESTRICTED_ACTION_TEXTS` 单点；
        拒绝形态判定由 Step4 执行器在活动页面上取证，零业务硬编码。
    其余（性能/易用/兼容）与需求语义无关，保持通用页面模板不动（零行为变更）。
    """
    # ★ 根因修复(2026-10-14) RC-AR: 需求**没点名**受限动作时，不得凭空造一个。
    #   根因（实测 fullcov_sut REQ-005「审计日志仅 admin 可访问」）: `_restricted_actions`
    #     无命中 → 曾退化成 `["新增"]` → 用例在**资产页**点击"新增资产"并断言"应被拒绝"；
    #     而 admin 本就允许新增 → 判"越权未被拒绝/越权成功" → **引擎自造的假 Bug**。
    #   正解: 未点名动作时，受限对象是**页面本身**（访问即应被拒绝）→ 只导航、不点击。
    _verbs = [v for v in (verbs or []) if v]
    _by_suffix = {
        str(c.get("case_id") or "").rsplit("-", 1)[-1]: c for c in cases
    }
    for idx, (suffix, scenario, title_suffix, desc_tpl) in enumerate(specs):
        case = _by_suffix.get(suffix)
        if not case:
            continue
        _v = _verb_at(_verbs, idx)
        _verb_cn = _v[0] if _v else "受限操作"
        # ★ 类型归属: V 系保留「UI」类目 —— 改写不得把 6 类型覆盖里的 UI 维度吃掉
        #   （P01/N01/B01/E01 归「安全」）。方法码仍取权威码: 行为型权限=AU，UI 反馈=EG。
        _is_ui = suffix.startswith("V")
        case["type"] = "UI" if _is_ui else "安全"
        case["test_method"] = "EG" if _is_ui else "AU"  # catalog.py 权威码
        case["scenario_type"] = scenario
        case["scenario_label"] = title_suffix
        if not preserve_control_type:
            case["control_type"] = "button"
        case["title"] = f"{module}-{case.get('req_id') or ''}-{title_suffix}"
        case["description"] = desc_tpl.format(desc=desc or "该需求", verb=_verb_cn)
        case["actions"] = _access_control_actions(nav_target, _v)
        case["checks"] = _access_control_checks(_v)
        case["required_tracks"] = _normalize_required_tracks(["ui"], case["checks"])


# ── ★ 横切维度模板单一事实源──
#   根因：`_build_rule_fallback_cases` 的**探针分支**（静态 DOM 找不到入口按钮时
#   提前 return）曾自建一套 `_ext_case` 模板（V01/V02/PF01/UX01/CP01/SEC01），
#   与主路径**不同源**：它无条件点"新增,新建,添加,创建"、无条件断言"必填红星/
#   空提交校验"、CP01 硬编码 375/768/1024/1920 四档（含靶子从未声明的移动端）。
#   后果（accept_sut_0923 实测）：86 条需求中 57 条 operation 无录入语义
#   （查询/只读/分页/审计…），只要走探针分支就必然点不存在的创建入口、断言只读
#   页面上不存在的必填标记 → 引擎自造 ERROR / 假 FAIL。这与"生成端模板与需求
#   语义脱钩"是同一缺陷类别的第二处表现，也是本项目反复复发的**模板漂移**
#   （主路径修了、探针分支没修）。
#   修复：把 UI/性能/易用/兼容 这组横切维度模板收敛为单一事实源 —— 主路径与探针
#   分支共用本函数；入口动作与断言语义一律由 `form_scope`/`entry_hint` 派生
#   （决策点在上游 `_build_rule_fallback_cases`，两路同源），杜绝口径漂移。
#   同时删除原探针分支的 SEC01：其断言 `text_contains_check{expected:'<script>',
#   operator:'not_contains'}` **无注入动作**（payload 从未进入页面）→ 执行器恒判
#   `found_count == 0` = PASS，是**不可证伪的假 PASS 生成器**；且若补上注入动作，
#   极性反而会反转（被正确转义的 payload 仍以字面文本出现在 inner_text 中）。
#   安全维度已由 run 级横切门禁 `cross_cut_gates.run_security_gate`（真实 payload
#   注入，含判真/判伪实证）与 step4 `oracle_results(security)` 一等公民覆盖，
#   故删除不降低真实安全覆盖。
def build_cross_cut_cases(
    *,
    rid: str,
    safe_rid: str,
    module: str,
    desc: str,
    nav_target: str,
    reason_note: str,
    entry_hint: str,
    form_scope: bool,
    unique_val: str,
    req: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """UI/性能/易用/兼容 横切维度模板（主路径与探针分支共用的单一事实源）。

    入口动作 (`entry_hint`) 与断言形态 (`form_scope`) 由调用方的需求语义判定派生，
    本函数不含任何业务/框架硬编码。
    """
    _entry_hint = entry_hint
    _form_scope = form_scope
    _unique_val = unique_val
    # ★ 字段规格按 project 隔离查询：不传时 `build_form_data_actions` 回落到空的
    #   `config.project_name`（工作线程里常为空）→ 规格恒空 → 降级"只填名称"三件套
    #   → 漏掉必填复选框 → 提交被客户端拦截 → 用例假 FAIL。
    _proj = str((req or {}).get("project") or "").strip()
    if not _proj:
        try:
            from src.core.config_loader import config as _cfg

            _proj = str(getattr(_cfg, "project_name", "") or "").strip()
        except Exception:  # noqa: BLE001
            _proj = ""

    # ── ★ UI 类：视觉/交互/提示（2 条模板）──
    # V01 视觉对齐与控件可见性
    # ★ E2(2026-09-26)：按需求语义二分——录入型需求断言"录入表单 + 必填标记"；
    #   其余需求断言"页面布局对齐 + 内容承载元素可见"（不点击创建类入口）。
    _v01_actions: list[dict[str, Any]] = [
        {"type": "navigate", "target_hint": nav_target or "/"},
    ]
    # 入口动作只在该模板的**观察面**需要它时发射：V01 非录入型的断言是
    #   "布局/内容已渲染"，页面加载即可观察。此前后无条件继承需求主操作
    #   （如"删除"），把破坏性动作塞进只读布局用例，同一 SUT 缺陷被重复计为
    #   多条 FAIL，且用例间数据互相污染。
    if _form_scope and _entry_hint:
        _v01_actions.append(
            {"type": "click", "target_hint": _entry_hint, "in_dialog": False}
        )
    _v01_actions.append({"type": "screenshot"})
    if _form_scope:
        _v01_checks: list[dict[str, Any]] = [
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "element_exists",
                    "selector": _FORM_CONTAINER_SELECTOR,
                    "expected": "存在",
                },
                "expect": "表单容器已渲染（布局检查前置条件）",
            },
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "element_exists",
                    # ★ D21 根因修复（2026-09-23）—— 伪元素选择器必然执行异常：
                    #   原选择器写作 `.ant-form-item-label > label::before, ...,
                    #   [aria-required='true']::after`（用 `::before`/`::after` 想定位
                    #   "红星必填标记"）。但 **伪元素不由 CSS 选择器 API 匹配**：
                    #   `querySelectorAll('.x::before')` 直接抛 SyntaxError，
                    #   Playwright `locator().count()` 报
                    #   `Unexpected token ":" while parsing css select`（round5 实测
                    #   TC-REQ-006/007-V01）。该检查在**任何页面**上都必抛异常，
                    #   用例被判 FAIL 却与产品行为毫无关系 —— 纯引擎侧假 FAIL。
                    #   修复：改为断言必填标记的**宿主元素**存在（伪元素只能随宿主
                    #   渲染，故宿主存在是"红星可见"的充分必要条件）。
                    # ★ E2a 根因修复（2026-09-26）—— 词表漂移补齐**原生 HTML5 标准**：
                    #   原三选一（antd label / element label / aria-required）全是框架私有
                    #   或 ARIA 机制，实测 fullcov_sut 的新增表单用的是**规范机制**
                    #   `input[required]`（form.html 三处 required 属性），三者皆不命中
                    #   → 录入型需求也必然假 FAIL。`required` 是 W3C HTML 标准属性，
                    #   比任何框架类名都更通用，必须纳入同一取或。
                    # ★ 根因修复(2026-09-28) —— 补齐**原生 label 星号**形态：服务端校验
                    #   型系统（如 SmartShop）表单 input 无 required 属性、无框架类名，
                    #   必填标记是 `<label>名称 *</label>` 的 label 宿主 + 星号文本。
                    #   `label` 是标准 CSS 选择器（不抛异常），为必填标记的通用宿主；
                    #   与 input[required]/aria-required 取或后，任意框架/规范形态均可达。
                    "selector": (
                        "input[required], select[required], textarea[required], "
                        "label, "
                        ".ant-form-item-label > label, "
                        ".el-form-item__label, "
                        "[aria-required='true']"
                    ),
                    "expected": "至少一个必填标记可见",
                },
                "expect": "必填字段有必填标记（视觉规范）",
            },
        ]
    else:
        _v01_checks = [
            {
                "track": "ui",
                "precision": "strict",
                "check": {
                    "type": "viewport_overflow_check",
                    "expected": "默认 viewport 下无水平滚动条、无元素超出视口",
                },
                "expect": "页面布局对齐、无横向溢出（控件可见的客观前置）",
            },
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "element_exists",
                    "selector": _PAGE_CONTENT_SELECTOR,
                    "expected": "存在",
                },
                "expect": "被测页面已渲染出内容承载元素（非白屏/非错误页）",
            },
        ]
    _v01_case = {
        "case_id": f"TC-{safe_rid}-V01",
        "req_id": rid,
        "title": (
            f"{module}-{rid}-UI-表单布局对齐与控件可见"
            if _form_scope
            else f"{module}-{rid}-UI-页面布局对齐与内容可见"
        ),
        "description": (
            (
                f"对{desc}进入表单页，断言: (1) 所有 label 左对齐且不遮挡输入框; "
                f"(2) 必填项有必填标记; (3) 提交按钮可见且可点击{reason_note}"
            )
            if _form_scope
            else (
                f"对{desc}目标页面，断言: (1) 默认分辨率下无横向溢出、无元素超出视口; "
                f"(2) 页面成功渲染出内容承载元素（非白屏/非错误页）{reason_note}"
            )
        ),
        "risk_level": "P3",
        "type": "UI",
        "scenario_type": "happy_path",
        "test_method": "FD",
        "control_type": "form" if _form_scope else "view",
        "required_tracks": ["ui"],
        "actions": _v01_actions,
        "checks": _v01_checks,
        "_generation_source": "local_rule_fallback",
    }
    if not _form_scope:
        # 只读观察面由本模板一次性声明，下游（_check_guard）不得再补变更激励
        from src.pipeline.step3._trigger_actions import (
            INTERACTION_SCOPE_OBSERVE_ONLY as _OBSERVE_ONLY,
        )

        _v01_case["interaction_scope"] = _OBSERVE_ONLY
    _v01_case["required_tracks"] = _normalize_required_tracks(
        _v01_case["required_tracks"], _v01_case["checks"]
    )

    # V02 错误提示可见可恢复
    # ★ E2(2026-09-26)：本模板前提是"存在可提交的空表单"。非录入型需求（查询/只读/
    #   分页/审计…）页面上没有这种表单，强行执行只会得到"找不到提交按钮/无错误提示"
    #   的引擎自造 FAIL；此时**不发射**该用例（宁少勿假），而非留一条必然失败的模板。
    _v02_case: dict[str, Any] | None = None
    if _form_scope:
        _v02_actions: list[dict[str, Any]] = [
            {"type": "navigate", "target_hint": nav_target or "/"},
        ]
        if _entry_hint:
            _v02_actions.append(
                {"type": "click", "target_hint": _entry_hint, "in_dialog": False}
            )
        # 空提交触发校验：提交类控件的**通用语义词**（跨框架/跨系统一致）
        # ★ 根因修复(2026-09-28): 提交/保存按钮位于**主表单页**而非对话框内——
        #   原 `in_dialog=True` 会让执行端只在 dialog 上下文里找"保存"，主表单页
        #   的保存按钮不可达 → 空提交从未发生 → 校验错误永不出现 → field_error
        #   恒假 FAIL（实测 REQ-002/006/009/014-V02）。in_dialog 只适用于
        #   "先点触发按钮、再在弹窗内操作"的场景，V02 直接提交主表单不属于此类。
        _v02_actions.append(
            {"type": "click", "target_hint": "保存,提交,确定,确认"}
        )
        _v02_actions.append({"type": "screenshot"})
        _v02_case = {
            "case_id": f"TC-{safe_rid}-V02",
            "req_id": rid,
            "title": f"{module}-{rid}-UI-错误提示文案可见且可恢复",
            "description": (
                f"对{desc}提交空表单触发校验，断言字段级错误提示可见且文本非空，"
                f"修改输入后错误消失（交互反馈闭环）{reason_note}"
            ),
            "risk_level": "P3",
            "type": "UI",
            "scenario_type": "error_path",
            "test_method": "EG",
            "control_type": "form",
            "required_tracks": ["ui"],
            "actions": _v02_actions,
            "checks": [
                {
                    "track": "ui",
                    "precision": "exact",
                    "check": {
                        "type": "field_error_visible_check",
                        "selector": _FIELD_ERROR_SELECTOR,
                        "expected": "错误提示文本非空",
                        "operator": "not_empty",
                    },
                    "expect": "空提交后字段级错误提示可见（校验反馈）",
                },
            ],
            "_generation_source": "local_rule_fallback",
        }
        _v02_case["required_tracks"] = _normalize_required_tracks(
            _v02_case["required_tracks"], _v02_case["checks"]
        )

    # ── ★ 性能类：响应时间/规模（2 条模板）──
    # PF01 页面加载时间
    _pf01_case = {
        "case_id": f"TC-{safe_rid}-PF01",
        "req_id": rid,
        "title": f"{module}-{rid}-性能-页面加载≤3s",
        "description": (
            f"对{desc}目标页面，断言首屏加载时间 ≤ 3000ms（LCP），"
            f"避免白屏或长时间等待{reason_note}"
        ),
        "risk_level": "P3",
        "type": "性能",
        "scenario_type": "happy_path",
        "test_method": "FD",
        "control_type": "form",
        "required_tracks": _tracks_for_type("性能"),
        "actions": [
            {"type": "navigate", "target_hint": nav_target or "/"},
            {"type": "screenshot"},
        ],
        "checks": [
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "performance_lcp_check",
                    "selector": "LCP",
                    "expected": 3000,
                    "operator": "less_than_equal",
                },
                "expect": "LCP ≤ 3s（页面加载性能基线）",
            },
        ],
        "_generation_source": "local_rule_fallback",
    }
    # 只读观察面：性能度量必须在"无任何交互"的页面上取，下游不得补变更激励
    from src.pipeline.step3._trigger_actions import (
        INTERACTION_SCOPE_OBSERVE_ONLY as _OBSERVE_ONLY_PF,
    )

    _pf01_case["interaction_scope"] = _OBSERVE_ONLY_PF
    _pf01_case["required_tracks"] = _normalize_required_tracks(
        _pf01_case["required_tracks"], _pf01_case["checks"]
    )

    # PF02 列表规模流畅性
    _pf02_case = {
        "case_id": f"TC-{safe_rid}-PF02",
        "req_id": rid,
        "title": f"{module}-{rid}-性能-500条列表渲染流畅",
        "description": (
            f"对{desc}列表页，当数据量达500条时断言: (1) 列表可见且无空白行; "
            f"(2) 滚动无明显卡顿（首屏渲染≤2s）{reason_note}"
        ),
        "risk_level": "P3",
        "type": "性能",
        "scenario_type": "boundary",
        "test_method": "BV",
        "control_type": "list",
        "required_tracks": _tracks_for_type("性能"),
        "actions": [
            {"type": "navigate", "target_hint": nav_target or "/"},
            _count_list_rows_action(),
            {"type": "screenshot"},
        ],
        "checks": [
            # ★ 根因修复(2026-09-28): PF02 改用**可判定断言**——原 list_row_count_check
            #   无 expected_count/delta（结构性不可判定，执行端恒报"⊘"→ ERROR），
            #   且被 _check_guard 剔除后 checks 变空壳（实测 TC-REQ-001~024-PF02 全
            #   ERROR）。"500条列表渲染流畅"的可观察证据 = 列表有数据渲染 + 页面无
            #   致命错误；数据量级与渲染耗时的真实测量由横切 perf 门禁承担。
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "element_exists",
                    "selector": _LIST_ROW_SELECTOR,
                    "expected": "存在",
                },
                "expect": "列表有数据行渲染（500条场景下不空白）",
                "description": "列表行数据已渲染",
            },
            {
                "track": "ui",
                "precision": "exact",
                "check": {
                    "type": "no_error",
                    "expected": "无错误",
                    "mode": "defense",
                },
                "expect": "页面无致命错误",
                "description": "列表渲染无致命错误",
            },
        ],
        "_generation_source": "local_rule_fallback",
        "interaction_scope": _OBSERVE_ONLY_PF,
    }
    _pf02_case["required_tracks"] = _normalize_required_tracks(
        _pf02_case["required_tracks"], _pf02_case["checks"]
    )

    # ── ★ 易用类：操作友好度（2 条模板）──
    # ★ 页面级横切维度收口：
    #   根因（accept_sut_0923 实测）：UX01/CP01/CP02 断言的是**整页**度量
    #   （整页空状态/整页多分辨率破版/整页触控尺寸），却被按**每条需求**各造一份
    #   → 172 条兼容/易用用例全部指向同一页面 `/`，同一页面事实被计 172 次；
    #   其中 123 条已执行且 100% FAIL（62 条挂 375px 移动端溢出=引擎自造需求，
    #   61 条挂整页链接尺寸=同一事实重复计数）。
    #   通用规则：页面级横切维度**每页判一次**，由 `_scope="page"` 标记，
    #   建模层按 (页面, 类型, 模板) 折叠为单条；需求级用例只保留自身声明的断言。
    #   跨系统成立：任何靶子的页面级度量都不该随需求条数线性膨胀。
    # UX01 新用户引导/空状态提示
    _ux01_case = {
        "case_id": f"TC-{safe_rid}-UX01",
        "req_id": rid,
        "title": f"{module}-{rid}-易用-空状态有引导文案",
        "description": (
            f"对{desc}页面用无匹配关键词触发空结果，断言空状态区域展示"
            f"无数据/无匹配的提示文案，帮助用户理解当前状态与下一步操作{reason_note}"
        ),
        "risk_level": "P4",
        "type": "易用",
        "scenario_type": "happy_path",
        "test_method": "FD",
        "control_type": "form",
        "required_tracks": ["ui"],
        "actions": [
            {"type": "navigate", "target_hint": nav_target or "/"},
            {"type": "fill", "target_hint": "查询输入框",
             "value": "ZZ_NOMATCH_%s" % uuid.uuid4().hex[:8]},
            {"type": "click", "target_hint": "查询按钮"},
            {"type": "screenshot"},
        ],
        "checks": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "semantic_check",
                    "expected": (
                        "空结果状态下页面展示无数据/无匹配的提示文案"
                        "（引导用户理解当前状态或下一步操作）"
                    ),
                },
                "expect": "空状态区域有可读的无数据/引导提示",
            },
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "click_target_size_check",
                    "min_size": 32,
                    "expected": "桌面 viewport 下按钮/链接尺寸 ≥ 32×32px（WCAG 2.5.5 Target Size）",
                },
                "expect": "桌面端点击区域达标",
            },
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "form_label_association_check",
                    "expected": "表单控件均有 label 关联或 aria/name 标注（WCAG 3.3.2）",
                },
                "expect": "表单控件都有 label/name/aria 标注",
            },
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "form_autocomplete_check",
                    "expected": "文本输入框有合理的 autocomplete 属性（WCAG 1.3.5）",
                },
                "expect": "输入框有 autocomplete 自动填充支持",
            },
        ],
        "_generation_source": "local_rule_fallback",
        "_scope": "page",
        "_scope_key": "UX01",
    }
    _ux01_case["required_tracks"] = _normalize_required_tracks(
        _ux01_case["required_tracks"], _ux01_case["checks"]
    )

    # UX02 操作反馈即时性
    # ★ E2(2026-09-26)：录入型需求 → 断言"提交后列表出现唯一值"（业务成功反馈）；
    #   其余需求（查询/只读/分页…）→ 断言"操作后页面加载完成反馈及时"——只读页面
    #   没有"提交"语义，套用创建反馈断言属无依据构造，且必然假 FAIL。
    # ★ 用例级唯一值(2026-09-27)：UX02 是横切用例，须用**自己的**唯一值派生，
    #   否则与主创建用例（P01，scope 空）共享同一值 → P01 先建、UX02 再建同名
    #   被靶子按业务拒绝（"名称已存在"）→ 假 FAIL 或执行序依赖（实测 TC-REQ-002）。
    _ux02_unique = (
        _gen_unique_value(req or {}, "name", "UX02") if form_scope else unique_val
    )
    _ux02_actions: list[dict[str, Any]] = [
        {"type": "navigate", "target_hint": nav_target or "/"},
    ]
    if _entry_hint:
        _ux02_actions.append(
            {"type": "click", "target_hint": _entry_hint, "in_dialog": False}
        )
    # ★ RC-AP(2026-10-14): UX02 断言的是**提交成功反馈**，属正向期望；
    #   需求极性为"拒否"时不得发射，否则断言与需求自相矛盾（要求"拒绝"却断言
    #   "创建成功"）→ 恒假 FAIL。表单可达性与期望极性是两个独立概念：前者决定
    #   "能不能进表单"，后者决定"提交后该成功还是该被拒"——各自由唯一判据裁决，
    #   不可合并成一个开关（合并会让 V02「错误提示」族被连坐掉）。
    try:
        from src.pipeline.step3._rule_extractor_keywords import (
            expects_error_feedback as _ux02_expects_reject,
        )

        # 需求文本只从上游结构化字段取；该作用域内没有裸名 operation/expected。
        _req_txt = req or {}
        _rejects = bool(
            _ux02_expects_reject(
                desc,
                str(_req_txt.get("operation") or ""),
                str(_req_txt.get("expected_feedback") or ""),
            )
        )
    except Exception:  # noqa: BLE001  # 判据不可用时按"不拒否"处理，不改变既有行为
        _rejects = False
        _log.warning(
            "RC-AP 判据不可用，UX02 按非拒否处理 rid=%s", rid, exc_info=True
        )
    _ux02_form_scope = _form_scope and not _rejects
    if _ux02_form_scope:
        # ★ 用例自洽修复(2026-10-14)：**所有录入动作必须排在提交动作之前**。
        #   根因：原实现把"价格/库存"两条 fill 追加在 `click(保存)` **之后**，
        #   于是提交发生时目标表单仍是空的（必填未填）→ 客户端校验拦截 → 页面无变化
        #   → 结构化重放失败 + Agent 8 轮不收敛 → 恒 ERROR（实测 18 条
        #   TC-REQ-0xx-UX02「click(新增/保存) 但页面没有变化」）。
        #   通用性：这是"先录入、后提交"这一交互契约，与业务字段无关，跨系统成立。
        #   目标形态由 SSOT `build_interaction_actions` 之外的两步显式编排承担：
        #   fill(名称/价格/库存) → click(保存,提交,确定,确认)。
        #   价格/库存为通用数值候选值：字段不存在时执行端 fill 容错跳过，不影响单字段系统。
        # ★ 根因修复(2026-10-14) RC-AJ: 录入动作改用**需求字段规格** SSOT
        #   （`_rule_extractor_fields.build_form_data_actions`），不再硬编码
        #   "名称/价格/库存"三条固定 fill。
        #   根因: 真实表单的**必填控件不止文本/数值**——本靶子需求明文规定
        #     "信息确认（confirmed）：复选框，必须勾选，否则客户端拦截"；
        #     硬编码三件套漏掉该复选框 → 客户端 JS 拦截提交 → 页面停在表单
        #     → 断言"列表出现唯一值"恒不满足（实测 TC-REQ-022/024/025/026-UX02
        #     均 FAIL，理由"页面为新增资产表单，未找到唯一值"）。
        #   通用性: 字段规格取自需求原文（跨系统/行业）；关联不到规格时诚实降级为
        #     原有通用三件套（与 _data_action 分支同一降级约定，不臆造字段）。
        _ux02_fill_actions: list[dict[str, Any]] = []
        try:
            from src.pipeline.step3._rule_extractor_fields import (
                build_form_data_actions as _build_ux02_form_data,
            )

            _ux02_fill_actions = _build_ux02_form_data(
                str((req or {}).get("operation") or ""),
                module=module,
                unique_value=_ux02_unique,
                project=_proj,
            )
        except Exception:  # noqa: BLE001
            _ux02_fill_actions = []
        if not _ux02_fill_actions:
            _ux02_fill_actions = [
                {"type": "fill", "target_hint": "名称/姓名输入框", "value": _ux02_unique},
                {"type": "fill", "target_hint": "价格/单价输入框", "value": "1"},
                {"type": "fill", "target_hint": "库存/数量输入框", "value": "1"},
            ]
        _ux02_actions += _ux02_fill_actions
        # ★ 根因修复(2026-09-28): 提交保存按钮位于主表单页，非对话框——
        #   原 in_dialog=True 使保存按钮不可达 → 提交从未发生 → 列表无唯一值
        #   → UX02 假 FAIL / Agent 探索 8 轮超时（实测 TC-REQ-001-UX02）。
        #   同 V02 根因，一并通用修复。
        _ux02_actions.append({"type": "click", "target_hint": "保存,提交,确定,确认"})
    _ux02_actions.append({"type": "screenshot"})
    if _ux02_form_scope:
        _ux02_checks: list[dict[str, Any]] = [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "text_contains_check",
                    "expected": _ux02_unique,
                    "operator": "contains",
                    "selector": _LIST_CONTAINER_SELECTOR,
                },
                "expect": f"列表出现唯一值 '{_ux02_unique}'（操作成功反馈）",
            },
        ]
    else:
        # 只读对应物：操作后页面**加载完成**（无 loading 悬挂 / 无异步等待超时）。
        # ★ 复用注册表内的 loading_complete（词表漂移高发项，已在 _track_protocol 注册）。
        _ux02_checks = [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {
                    "type": "loading_complete",
                    "wait_network_idle": True,
                    "timeout_ms": 10000,
                    "expected": "操作后页面加载完成（无加载悬挂）",
                },
                "expect": "操作反馈及时：页面在超时内达到加载完成状态，不让用户等待无反馈",
            },
        ]
    _ux02_case = {
        "case_id": f"TC-{safe_rid}-UX02",
        "req_id": rid,
        "title": (
            f"{module}-{rid}-易用-提交后有成功反馈"
            if _ux02_form_scope
            else f"{module}-{rid}-易用-操作后反馈及时"
        ),
        "description": (
            (
                f"对{desc}正常提交后，断言 ≤ 2s 内出现成功反馈"
                f"(Toast/Alert/页面跳转/按钮loading→完成)，不让用户猜操作结果{reason_note}"
            )
            if _ux02_form_scope
            else (
                f"对{desc}目标页面执行操作后，断言页面在超时内达到**加载完成**状态"
                f"(无 loading 悬挂、无异步等待超时)，操作结果无需用户猜测{reason_note}"
            )
        ),
        "risk_level": "P3",
        "type": "易用",
        "scenario_type": "happy_path",
        "test_method": "FD",
        "control_type": "form" if _ux02_form_scope else "view",
        "required_tracks": ["ui"],
        "actions": _ux02_actions,
        "checks": _ux02_checks,
        "_generation_source": "local_rule_fallback",
    }
    if not _ux02_form_scope:
        # 非提交型 UX02 只观察"操作后加载是否完成"，属只读观察面
        from src.pipeline.step3._trigger_actions import (
            INTERACTION_SCOPE_OBSERVE_ONLY as _OBSERVE_ONLY_UX,
        )

        _ux02_case["interaction_scope"] = _OBSERVE_ONLY_UX
    # ★  根因修复：UX02 原携带 form_label_association_check +
    #   form_autocomplete_check 两条**整页**度量——它们不属于本需求（创建反馈），
    #   且被 86 条需求各复制一份 → 同一页面事实重复计数。已移至页面级用例
    #   （CP02）与 run 级横切门禁（cross_cut_gates.run_empirical_usability_gate）。
    #   UX02 只保留自身声明的"提交后有成功反馈"断言。
    _ux02_case["required_tracks"] = _normalize_required_tracks(
        _ux02_case["required_tracks"], _ux02_case["checks"]
    )

    # ── ★ 兼容类：多环境差异（页面级，1 条模板）──
    # CP01 声明支持矩阵内无破版（+ 仅在声明移动端时补触控目标）
    _cp01_checks: list[dict] = [
        {
            "track": "ui",
            "precision": "strict",
            "check": {
                "type": "viewport_rendering_check",
                "viewports": declared_support_viewports(),
                "expected": "声明支持矩阵内各分辨率下均无水平溢出、无关键元素重叠",
            },
            "expect": "多分辨率下页面布局不破版",
        },
        {
            "track": "ui",
            "precision": "strict",
            "check": {
                "type": "viewport_overflow_check",
                "expected": "默认 viewport 下无水平滚动条、无元素超出视口",
            },
            "expect": "默认 viewport 无溢出",
        },
    ]
    # ★  根因修复：移动端触控目标**仅在声明支持移动端时**纳入。
    #   根因（accept_sut_0923 实测）：原 CP02 对 touch_target_mobile_check 硬编码
    #   375px，而靶子需求通篇未声明移动端 → 375px 必然横向溢出 → 必然假 FAIL
    #   （引擎自造需求）。通用规则：只测**声明支持的环境矩阵**。
    _cp_viewports = declared_support_viewports()
    if matrix_supports_mobile(_cp_viewports):
        _cp01_checks.append({
            "track": "ui",
            "precision": "strict",
            "check": {
                "type": "touch_target_mobile_check",
                "min_size": 44,
                "mobile_width": min(int(v["w"]) for v in _cp_viewports),
                "expected": "声明支持的移动端 viewport 下所有可交互元素尺寸 ≥ 44×44px（Apple HIG / WCAG 2.5.5）",
            },
            "expect": "移动端触控目标达标",
        })
    _cp01_case = {
        "case_id": f"TC-{safe_rid}-CP01",
        "req_id": rid,
        "title": f"{module}-{rid}-兼容-声明矩阵内无破版",
        "description": (
            f"对{desc}目标页面，在**声明支持的分辨率矩阵**内断言: (1) 无横向滚动条; "
            f"(2) 关键元素不重叠破版; (3) 移动端触控目标达标（仅当声明支持移动端）{reason_note}"
        ),
        "risk_level": "P4",
        "type": "兼容",
        "scenario_type": "happy_path",
        "test_method": "FD",
        "control_type": "form",
        "required_tracks": ["ui"],
        "actions": [
            {"type": "navigate", "target_hint": nav_target or "/"},
            {"type": "screenshot"},
        ],
        "checks": _cp01_checks,
        "_generation_source": "local_rule_fallback",
        "_scope": "page",
        "_scope_key": "CP01",
        # 页面级只读度量：下游不得补任何变更激励
        "interaction_scope": _OBSERVE_ONLY_PF,
    }
    _cp01_case["required_tracks"] = _normalize_required_tracks(
        _cp01_case["required_tracks"], _cp01_case["checks"]
    )

    # ★  根因修复：原 CP02（"深色模式/主题不破坏可读性"）被删除——
    #   其两条 check 是**整页**度量（触控尺寸/点击尺寸），已分别归入 CP01（触控，
    #   声明矩阵门控）与 UX01（点击尺寸）；"深色模式"本身是引擎自造需求
    #   （靶子从未声明暗色主题），保留只会制造假 FAIL。删除模板而非留空断言，
    #   符合"不打补丁"约束。
    return [
        _v01_case,
        *([_v02_case] if _v02_case else []),
        _pf01_case,
        _pf02_case,
        _ux01_case,
        _ux02_case,
        _cp01_case,
    ]



def _build_rule_fallback_cases(
    req: dict[str, Any], reason: str = ""
) -> list[dict[str, Any]]:
    """AI 不可用时生成意图驱动兜底测试用例，保证本地流水线可继续验证。

    ★ Fix21 (2026-07-07): 复用 rule_extractor 的控件/操作/断言推断函数，
      按 operation 类型生成固定的 actions + checks 模板（与 rule_extract 同源逻辑）。
    """
    rid = str(req.get("req_id", "") or "REQ").strip()
    module = str(req.get("module", "") or "通用模块").strip()
    desc = str(req.get("description", "") or "需求").strip()
    operation = str(req.get("operation", "") or "").strip()
    expected = str(req.get("expected_feedback", "") or "").strip()
    # ★ 字段规格按 project 隔离查询：不传 project 时 `build_form_data_actions` 会回落到
    #   空的 `config.project_name`（Step3 工作线程里常为空）→ 规格恒空 → 降级成"只填名称"
    #   的硬编码三件套 → 漏掉必填复选框 → 提交被客户端拦截 → 用例假 FAIL。
    _proj = str(req.get("project") or "").strip()
    if not _proj:
        try:
            from src.core.config_loader import config as _cfg

            _proj = str(getattr(_cfg, "project_name", "") or "").strip()
        except Exception:  # noqa: BLE001
            _proj = ""
    safe_rid = rid.replace(" ", "_").replace("/", "_")
    reason_note = ("；AI不可用原因：" + reason[:80]) if reason else ""

    # ── 接口操作型需求：浏览器轨模板（P01/N01/B01/E01 + UI/性能/易用/兼容）全不适用 ──
    #   根因: 为 API 需求（operation="请求GET /assets接口"）生成浏览器表单/文本断言模板
    #   → 例如把 expected_feedback 里的 "返回HTTP 200" 当成"页面应出现的文案"去断言 →
    #   Agent 在无对应页面上空转/断言错配 → 结构性假 ERROR/假 FAIL（实测 REQ-007..018）。
    #   该需求的功能验证由 API 轨意图用例（_build_intent_case 的 I01）承担；此处不再产出
    #   浏览器轨兜底，避免"引擎自造"的不可执行用例。判据单一事实源见 is_api_operation。
    from src.pipeline.step3._rule_extractor_keywords import is_api_operation

    if is_api_operation(operation):
        _log.info("[fallback] 接口操作型需求 %s：跳过浏览器轨模板生成", rid)
        return []

    # ── 1. navigate target_hint 优先用 RAG 真实路径（复用 rule_extractor）──
    nav_target = "/"
    nav_url = ""  # ★ 通用修复(2026-07-20): 同时提取URL，让fallback用例也能URL直接导航
    page_button_texts = (
        []
    )  # ★ 通用修复(2026-07-20): 目标页面的按钮文本列表，用于精确匹配submit_text
    try:
        from src.pipeline.step3._rule_extractor import (
            _extract_page_paths_from_rag,
            _match_page_for_requirement,
        )

        _rag_text = str(req.get("_rag", "") or "")
        if _rag_text:
            _paths = _extract_page_paths_from_rag(_rag_text, "")
            if _paths:
                _matched = _match_page_for_requirement(req, _paths)
                if _matched:
                    nav_target = _matched
    except Exception as _e:
        _log.debug("RAG 页面路径提取失败: %s", _e, exc_info=True)

    # ★ 通用修复(2026-07-20): 从导航图查找匹配页面的URL
    #   即使AI被cross_check阻断，fallback用例也能URL直接导航（Step4优先URL导航）
    #   通用性：通过 module/description 关键词匹配导航页面名，不依赖特定系统
    # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps.nav_json → nav_graph（read_menu_groups）
    #   根因: system_nav_maps 无 nav_json 列且 0 行，原查询恒抛 Unknown column，
    #         被下方 except 吞掉 → fallback 用例永远拿不到真实 URL/按钮。
    try:
        from src.pipeline.utils.nav_graph_read import read_menu_groups

        _proj = str(req.get("project", "") or req.get("_project", "") or "").strip()
        if _proj:
            _menu_groups = read_menu_groups(_proj)
            if not _menu_groups:
                _log.warning(
                    "[fallback] nav_graph 无导航数据 project=%s，fallback 用例无法定位真实URL",
                    _proj,
                )
            else:
                _nav = {"menu_groups": _menu_groups}

                # ★ 通用URL提取函数（与 _contexts.py / write.py 保持一致）
                def _extract_url(it: dict) -> str:
                    if not it:
                        return ""
                    _p = (it.get("path", "") or "").strip()
                    if _p and (
                        _p.startswith("/") or _p.startswith("http") or "?" in _p
                    ):
                        return _p
                    _u = (it.get("url", "") or "").strip()
                    if _u and (
                        _u.startswith("/") or _u.startswith("http") or "?" in _u
                    ):
                        return _u
                    return ""

                # ★ 关键词匹配：module + description + operation 中的关键词与 nav_map 页面名匹配
                # ★ 通用修复(2026-07-20): 双向子串匹配 + 动词过滤
                #   "查询辅材" 中的 "辅材" 能匹配 nav_map 中的 "辅材配方"
                #   过滤掉通用动词（查询/搜索/新建/编辑/删除等），只保留名词关键词
                # ★ v19 增强(2026-07-21): 新增通用名词过滤（数据/系统/信息等）
                #   原bug: "系统从办公协同获取叶组配方化学成份数据" 中的"数据"
                #          匹配到 nav_map 中的"数据看板"，导致 fallback 用例
                #          导航到错误的 dashboard 页面。
                #   修复: 过滤掉"数据/系统/信息"等通用名词，只保留业务名词关键词。
                _VERBS = {
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
                }
                # ★ v19 新增: 通用名词黑名单（在所有页面都可能出现，没有业务意义）
                _GENERIC_NOUNS_V19 = {
                    "数据",
                    "系统",
                    "信息",
                    "记录",
                    "列表",
                    "详情",
                    "内容",
                    "结果",
                    "状态",
                    "用户",
                    "功能",
                    "模块",
                    "页面",
                    "界面",
                    "视图",
                    "面板",
                    "区域",
                    "部分",
                }
                _keywords = []
                for _field in ("module", "operation", "description", "feature_label"):
                    _v = str(req.get(_field, "") or "").strip()
                    if not _v or len(_v) < 2:
                        continue
                    _keywords.append(_v)
                    # ★ 提取名词关键词：去掉动词后剩余的部分
                    #   如 "查询辅材" → "辅材"，"批量导入辅材" → "辅材"
                    _words = []
                    _cur_word = ""
                    for _ch in _v:
                        _cur_word += _ch
                        for _verb in _VERBS:
                            if _cur_word.endswith(_verb):
                                _cleaned = _cur_word[: -len(_verb)]
                                if (
                                    _cleaned
                                    and len(_cleaned) >= 2
                                    and _cleaned not in _words
                                ):
                                    _words.append(_cleaned)
                                _cur_word = ""
                                break
                    if _cur_word and len(_cur_word) >= 2 and _cur_word not in _words:
                        _words.append(_cur_word)
                    _keywords.extend(_words)
                # 同时把 nav_target（RAG匹配的页面名）作为关键词
                if nav_target and nav_target != "/":
                    _keywords.append(nav_target)

                # ★ 去重 + 过滤过短的关键词 + 过滤通用名词（v19）
                _seen_kw = set()
                _keywords = [
                    _kw
                    for _kw in _keywords
                    if len(_kw) >= 2
                    and _kw not in _GENERIC_NOUNS_V19
                    and not (_kw in _seen_kw or _seen_kw.add(_kw))
                ]

                _best_url = ""
                _best_score = 0
                _best_item = (
                    None  # ★ 通用修复(2026-07-20): 记录最佳匹配项，用于提取按钮列表
                )
                for mg in _nav.get("menu_groups", []):
                    # 检查 items
                    for item in mg.get("items", []):
                        _text = (item.get("text", "") or "").strip()
                        _url = _extract_url(item)
                        if not _url or not _text:
                            continue
                        _score = 0
                        for _kw in _keywords:
                            # ★ 双向子串匹配：关键词在页面名中 或 页面名在关键词中
                            if _kw in _text or _text in _kw:
                                _score += max(len(_kw), len(_text))
                        if _score > _best_score:
                            _best_score = _score
                            _best_url = _url
                            _best_item = item
                            if _text:
                                nav_target = (
                                    _text  # 同时更新 target_hint 为页面真实名称
                                )
                    # 检查 tab_items
                    for tab_item in mg.get("tab_items", []):
                        _text = (tab_item.get("text", "") or "").strip()
                        _url = _extract_url(tab_item)
                        if not _url or not _text:
                            continue
                        _score = 0
                        for _kw in _keywords:
                            if _kw in _text or _text in _kw:
                                _score += max(len(_kw), len(_text))
                        if _score > _best_score:
                            _best_score = _score
                            _best_url = _url
                            _best_item = tab_item
                            if _text:
                                nav_target = _text
                if _best_url:
                    nav_url = _best_url
                    # ★ 通用修复(2026-07-20): 提取目标页面的按钮文本列表
                    #   通用性：从 page_elements.buttons 提取 text 字段，组成 list[str]
                    #   用途：传给 _infer_submit_button_text 让 _match_button_by_keywords 优先匹配真实按钮
                    #   覆盖性：兼容 list[dict] 和 list[str] 两种格式
                    if _best_item:
                        _pe = _best_item.get("page_elements") or {}
                        _btns = _pe.get("buttons") or []
                        _btn_texts = []
                        for _btn in _btns:
                            if isinstance(_btn, dict):
                                _bt = (_btn.get("text", "") or "").strip()
                                if _bt:
                                    _btn_texts.append(_bt)
                            elif isinstance(_btn, str):
                                if _btn.strip():
                                    _btn_texts.append(_btn.strip())
                        if _btn_texts:
                            page_button_texts.extend(_btn_texts)
                            _log.info(
                                "[fallback] 提取页面按钮列表: %s (target=%s)",
                                _btn_texts[:5],
                                nav_target,
                            )
                    _log.info(
                        "[fallback] 从nav_map匹配到URL: %s (target=%s, score=%d, keywords=%s)",
                        nav_url[:80],
                        nav_target,
                        _best_score,
                        _keywords[:5],
                    )
                else:
                    _log.info(
                        "[fallback] nav_map未匹配到URL (keywords=%s)", _keywords[:5]
                    )
    except Exception as _e:
        _log.warning("[fallback] nav_map URL提取失败(不阻塞): %s", _e, exc_info=True)

    # ── 2. 复用 rule_extractor 的控件/操作/断言推断函数 ──
    # ★ Fix21: 不再用 operation 文本当 target_hint，而是用通用语义
    # ★ 通用修复(2026-07-20): 传入 page_button_texts 让 _match_button_by_keywords 优先匹配真实按钮
    #   根因：原代码不传 dom_buttons，_infer_submit_button_text 回退到硬编码规则返回泛化词（如"导入"），
    #         但目标页面可能根本没有该按钮（如辅材配方页面只有"批量导出/新建配方"），导致 Step4 元素定位失败。
    #   通用性：基于 nav_map 真实按钮列表匹配，适用于所有系统/行业/页面。
    #   合理性：追根溯源到 Step3 fallback 生成用例时未校验按钮真实性的缺陷。
    #   覆盖性：兼容 page_button_texts 为空（回退原逻辑）和非空（精确匹配）两种场景。
    control_type = "button"
    submit_text = "提交按钮"
    ui_check = None
    button_not_found = (
        False  # ★ 标记：目标按钮在页面中不存在（降级=失败，不生成display用例）
    )
    try:
        from src.pipeline.step3._rule_extractor import (
            _infer_control_type_from_operation,
            _infer_submit_button_text,
            _build_ui_check_by_operation,
            _match_button_by_keywords,
        )
        control_type = _infer_control_type_from_operation(operation)
        # ★ 通用修复(2026-07-20): 优先用 nav_map 真实按钮列表匹配
        # ★ 通用修复(2026-07-20): page_button_texts 为空时也标记button_not_found（校验前置）
        #   原bug: page_button_texts 为空时（nav_map 未爬取到按钮），
        #          回退到 _infer_submit_button_text 硬编码规则返回泛化词（如"导入"），
        #          但目标页面可能根本没有该按钮，导致 Step4 元素定位失败。
        #   修复: page_button_texts 为空时也标记 button_not_found=True，生成 Agent 探索意图。
        #   通用性: 基于nav_map数据完整性判断，不依赖业务逻辑。
        #   合理性: nav_map 无按钮数据时无法保证按钮存在性，Agent 探索是最安全的。
        if page_button_texts:
            _matched_btn = _match_button_by_keywords(operation, page_button_texts)
            if _matched_btn:
                submit_text = _matched_btn
                _log.info(
                    "[fallback] 按钮匹配命中: operation=%r → submit_text=%r (from nav_map buttons)",
                    operation[:50],
                    submit_text,
                )
            else:
                # ★ P0-fix(2026-07-25): 回滚 ADR-017 intent: 逻辑，符合 §12.2规则2
                #   按钮无匹配 → 标记 button_not_found=True，生成 Agent 探索意图
                button_not_found = True
                _log.info(
                    "[fallback] 按钮无匹配: operation=%r → button_not_found=True (§12.2规则2)",
                    operation[:50],
                )
        else:
            # ★ P0-fix(2026-07-25): 回滚 ADR-017 intent: 逻辑，符合 §12.2规则2
            #   nav_map 无按钮数据 → 标记 button_not_found=True，生成 Agent 探索意图
            button_not_found = True
            _log.info(
                "[fallback] nav_map无按钮数据: operation=%r → button_not_found=True (§12.2规则2)",
                operation[:50],
            )
        ui_check = _build_ui_check_by_operation(operation, expected, [], {})
        _log.info(
            "[fallback DEBUG] operation=%r → control_type=%r, submit_text=%r, button_not_found=%s",
            operation[:50],
            control_type,
            submit_text,
            button_not_found,
        )
    except Exception as _e:
        _log.warning("rule_extractor 推断失败: %s", _e, exc_info=True)

    # ── 3. 根据 control_type 生成基础 actions（通用语义，禁业务功能名）──
    # ★ Fix21: 与 rule_extractor._build_actions 同源逻辑
    # ★ P0-fix(2026-07-25): 删除 ADR-017 intent: 语义前缀，符合 §12.2规则1
    #   原bug: navigate target_hint 用 "intent:首页" 语义前缀，让 Step4 重新做菜单导航决策
    #   根因(§10.9): ADR-017 让 Step4 实时菜单导航 → 违反 §12.1"Step4 只执行不决策"
    #   修复: navigate target_hint 用 nav_url(URL直接导航) 优先 + nav_target(页面名) 次之 + "/" 兜底
    #   通用性: 跨系统/跨行业/跨页面 — 所有系统都有 nav_map，匹配失败则降级到首页
    #   合规: §12.2规则1"navigate target_hint 必须在 system_nav_maps 中存在" + §12.1"Step4 只执行不决策"
    #   全局观: 删除 intent: 前缀后，Step4 不再做菜单导航决策，统一走 URL/页面名导航
    _fb_nav_hint = nav_url or nav_target or "/"

    def _build_base_actions(
        error_case: bool = False, boundary_case: bool = False
    ) -> list[dict]:
        # ★ §13.3 覆盖性修复(2026-07-24 续): fill 值走 value_generator 统一管道(operation 语义推断)
        # ★ 根因修复(2026-10-15) D49: 传参语义必须正确 —— 生成器的第二参数是**字段名**
        #   （用于按字段语义选值：价格→数字、邮箱→邮件、名称→文本），而原实现传的是
        #   **整条 operation 文本**（"提交含非法价格的资产表单"）→ 函数内按字段名匹配
        #   email/phone/date/url 全部落空 → 恒落到通用兜底 `!@#`。
        #   实测：102 条 I01/I02/I03 中 48 条填 `!@#`、值分布与 title 声称的输入
        #   （"重复名称"/"非法价格"/"非法库存"）完全脱钩 → 断言"应拦截非法价格"时
        #   实际填的是非法字符 → 结构性假 FAIL / 假 PASS 混杂。
        #   修法: 走**同一上游字段规格** `extract_field_specs`（与
        #     `_rule_extractor_fields.build_form_data_actions` 同一事实源），
        #     取出真正的字段 key 与类型再交给生成器；取不到才降级为原行为。
        #   （注：完整字段规格的组装已在同函数下方走 `build_form_data_actions`，
        #     本处仅为"单 fill 降级路径"补上正确的字段名，不另造语义。）
        def _gen_fill(error_case: bool) -> str:
            _fkey = ""
            _ftype = ""
            try:
                from src.pipeline.step3._rule_extractor_fields import (
                    extract_field_specs as _efs,
                    _classify as _cls,
                )

                _specs = _efs(operation, module, _proj) or []
                _first = next(
                    (s for s in _specs if _cls(s).get("required")), None
                ) or (next(iter(_specs), None) if _specs else None)
                if _first:
                    _fkey = str(_first.get("key") or "")
                    _ftype = str(_cls(_first).get("vtype") or "")
            except Exception as _fe:  # noqa: BLE001
                _log.debug("[fallback] 字段规格不可用(D49 降级): %s", str(_fe)[:60])
            try:
                from src.pipeline.step3.value_generator import (
                    generate_value_from_type,
                    generate_invalid_value_from_type,
                )

                # 有字段规格 → 用真实字段名+类型（与 build_form_data_actions 同源）；
                # 无 → 退回按 operation 猜测的旧行为（诚实降级，不臆造字段）。
                _t = _ftype or (
                    "integer"
                    if any(k in operation for k in ["数量", "金额", "价格"])
                    else "string"
                )
                _n = _fkey or operation
                return (
                    generate_invalid_value_from_type(_t, _n)
                    if error_case
                    else generate_value_from_type(_t, _n)
                )
            except Exception as _ve:
                _log.debug(
                    "[fallback] value_generator不可用(用兜底): %s", str(_ve)[:50]
                )
                return "!@#" if error_case else "测试数据"

        # ★ 根因修复(2026-09-27)：动作编排收敛到 SSOT（与 `_rule_extractor_actions`
        #   及本文件探针分支同一实现）。原实现与主规则路径同源同错——变更型操作
        #   产出 `fill(先) → click(触发按钮)`：弹窗未打开即录入 → 结构化执行必败。
        _nav_action = {"type": "navigate", "target_hint": _fb_nav_hint}

        _data_action = None
        # ★ Fix E 根因修复(2026-09-27)：变更型**正向**用例按需求字段规格完整录入。
        #   根因：原先只产出单个 fill（"主输入框"），而真实新增表单有多个必填字段
        #     （名称/分类/价格/库存/状态 + 勾选确认）→ 客户端 JS 校验拦截提交 →
        #     结构化执行必败 → 降级 AI 探索 8 轮不收敛（TC-REQ-002-P01 假 FAIL，
        #     单条 6.4 分钟）。字段规格来自需求原文（SSOT `_rule_extractor_fields`）；
        #     关联不到字段规格 → 返回空 → 维持原有单 fill（诚实降级，不臆造字段）。
        #   通用性(§1)：只依赖需求书写范式「标签（key）：约束」+ 操作对象词重叠。
        if not error_case and not boundary_case:
            try:
                from src.pipeline.step3._rule_extractor_fields import (
                    build_form_data_actions,
                )

                _field_actions = build_form_data_actions(
                    operation, module=module, unique_value=_unique_val, project=_proj
                )
            except Exception as _fe:  # noqa: BLE001
                _log.debug(
                    "[fallback] 字段规格组装失败(降级单fill): %s", str(_fe)[:80]
                )
                _field_actions = []
            # 仅变更型（新增/编辑/导入…）多字段录入有意义；检索型/负向仍走下方单 fill
            if _field_actions and mutation_flow_kind(operation, True) == "dialog_form":
                _data_action = _field_actions

        if _data_action is None:
            if control_type in ("input", "textarea"):
                _data_action = {
                    "type": "fill",
                    "target_hint": "主输入框",
                    "value": "" if boundary_case else _gen_fill(error_case),
                }
            elif control_type == "select":
                _data_action = {
                    "type": "select_option",
                    "target_hint": "下拉选择框",
                    "option_text": "无效选项" if error_case else "第一项",
                }
            elif control_type == "upload":
                # ★ 通用修复(2026-07-20): 区分 P01/N01/B01 的文件名，避免 actions 同质化
                _file = (
                    "invalid_format.exe"
                    if error_case
                    else ("empty.csv" if boundary_case else "test_sample.csv")
                )
                _data_action = {
                    "type": "upload_file",
                    "target_hint": "文件上传控件",
                    "file": _file,
                }
            elif classify_operation_category(operation) == "query":
                _data_action = {
                    "type": "fill",
                    "target_hint": "查询输入框",
                    "value": "" if boundary_case else _gen_fill(error_case),
                }

        return [_nav_action] + build_interaction_actions(
            operation,
            _data_action,
            submit_text,
            infer_confirm_hint(operation, page_button_texts),
        )

    # ── 4. 深断言生成（2026-08-21 推翻宽松断言层）──
    # ★ 设计原则（用户诉求）：验证层必须"可证伪、对任一靶子通用、不依赖 SUT DB schema"。
    #   1. 不再用 toast 关键词 OR 匹配（成功/完成/操作成功）——坏页面也能蒙混。
    #   2. 改为 UI 功能性深断言：进入模块列表页 → 记录基线（列表行数）→ 执行创建
    #      → 断言"列表出现新建记录（含填入的唯一值）"或"列表行数 +1"。
    #   3. 若 config.TARGET_DB 明确指向 SUT 库，额外加 db_record_exists_check（深断言）。
    #   4. error/boundary：字段级错误断言（精确 selector，非大杂烩 OR）+ 负向不变断言。
    # ★ 通用性：列表行数提取约定见 _LIST_ROW_SELECTOR（table/tbody tr 或 data-testid 行），
    #   唯一值见 _gen_unique_value（基于 req_id 派生，跨系统/行业通用）。
    # ★ 保持契约：check 仍用 {track, precision, check:{type,expected,selector}, expect} 新格式，
    #   case_id 仍 TC-<req>-P01/N01/B01/E01，_generation_source 保留，不破坏下游消费。

    # 唯一业务值（用于列表可见性断言）—— 如"新增患者"→患者姓名
    _unique_field = _infer_unique_field(req)
    _unique_val = _gen_unique_value(req, _unique_field)
    # ★ 创建语义门控(2026-09-22): 深断言（唯一值列表可见+行数+1）仅对创建型需求注入
    #   ★ 2026-09-24: 传入 expected_feedback —— 拒绝/只读语义常落在期望反馈里。
    _is_creation = _is_record_creation_semantics(operation, desc, expected)
    # ★ 根因修复(2026-09-25) RC-AH/RC-AI/AJ: 需求场景分类（模板分化唯一决策点）
    _scenario = _classify_req_scenario(operation, desc, expected)
    _restricted_verbs = _restricted_actions(operation, desc, expected)
    _log.info(
        "[fallback] 需求 %s 场景分类=%s 受限操作=%s",
        rid, _scenario, _restricted_verbs,
    )

    # ── ★ E2 根因修复(2026-09-26)：UI/易用模板的「被测入口动作」与「断言语义」 ──
    #   必须由**需求语义**派生，禁止无条件点"新增,新建,添加,创建"。
    #   根因（accept_sut_0923 实测）：86 条需求中 57 条 operation 无创建/录入语义
    #   （"查询资产"/"分页展示资产数据"/"展示空列表"/"记录审计日志"…），但 V01/V02/UX02
    #   三条模板硬编码注入 `click "新增,新建,添加,创建"` 且断言"必填项有红星"——
    #   动作与断言**均无需求依据**（过度构造），且在只读页面上必然定位不到创建表单/
    #   必填标记 → 引擎自造 ERROR（实测 TC-REQ-001-V01: element_exists 1/2 →
    #   ai_root_cause=HARNESS_ERROR）。属"生成端模板与需求语义脱钩"缺陷类别的复发。
    #   通用规则（与 submit_text 同源，见上文 _match_button_by_keywords）：
    #     · 入口按钮 = 需求 operation 语义 ∩ **页面真实按钮**（submit_text 即该结果）；
    #       无真实匹配且需求确为录入型时，才回退到"打开录入表单"的通用入口提示。
    #     · 表单形态断言仅当需求语义指向**数据录入/字段校验**时才成立。
    _entry_hint = (submit_text or "").strip()
    if _entry_hint == "提交按钮":  # 真实按钮未匹配到（_infer_submit_button_text 的占位值）
        _entry_hint = ""
    _edit_verb = _is_edit_operation(operation)
    # ★ RC-AP(2026-10-14)：表单形态不再由本处**另起一轮关键词扫描**推导。
    #   根因：此前 `_form_scope` 用 `any(k in operation+expected ...)` 自行判定，
    #   与唯一决策点 `_classify_req_scenario` 并列且判据不同 → 二者互相矛盾。
    #   实测 REQ-022「查看资产列表」是纯只读需求，其 expected_feedback 里出现
    #   "操作（编辑/删除）字段的列表"字样 → 子串扫描命中"编辑" → 误判为录入型
    #   → 发射 UX02「提交后列表出现唯一值」断言，而该页面根本没有提交动作
    #   → 恒假 FAIL（实测 TC-REQ-022/025/045-UX02 等 12 条）。
    #   修法：表单形态 = 场景分类的**只读投影**，下游只读不推。
    #   ★ 注意：此处**只判"表单是否可达"，不掺极性**。期望极性由期望反馈族
    #     （UX02「成功反馈」/ V02「错误提示」）各自在发射点裁决——若把极性并进
    #     表单形态，V02 会因"需求期望拒否"而被连坐掉，13 条负向用例凭空消失。
    _form_scope = _scenario in ("creation", "edit", "validation")
    if _form_scope and not _entry_hint:
        # 入口按钮按**需求自身的动作**派生（动词级，非业务名）：
        #   编辑型需求 → 进入编辑表单的入口；录入型 → 进入新增表单的入口。
        _entry_hint = (
            "编辑,修改,更新" if _edit_verb else "新增,新建,添加,创建"
        )
    _log.info(
        "[fallback] 需求 %s 入口提示=%r 表单形态=%s",
        rid, _entry_hint, _form_scope,
    )
    # 把唯一值注入 P01 的 fill action（确保 actions 与断言一致，§13.3）
    _p01_actions = _build_base_actions(error_case=False, boundary_case=False)
    _inject_unique_value(_p01_actions, _unique_val, operation)

    # 深断言：列表出现包含唯一值的记录（可证伪，不依赖 toast）
    _list_contains_check = {
        "track": "ui",
        "precision": "fuzzy",
        "check": {
            "type": "text_contains_check",
            "expected": _unique_val,
            "operator": "contains",
            "selector": _LIST_CONTAINER_SELECTOR,
        },
        "expect": f"列表/表格中出现新建记录（含唯一值 '{_unique_val}'），证明业务真正落库并显示",
    }
    # 深断言：列表行数 +1（与基线对比，可证伪）
    _list_count_check = {
        "track": "ui",
        "precision": "exact",
        "check": {
            "type": "list_row_count_check",
            "selector": _LIST_ROW_SELECTOR,
            "expected_delta": 1,
            "operator": "gte",
            "baseline_action_index": 1,  # 指向 actions 中的 count_list_rows（索引见下）
        },
        "expect": "提交后列表行数比基线（提交前）增加至少 1 行，证明新增成功",
    }

    # P01 的 checks（深断言，UI 功能性）
    if _is_creation:
        base_checks = [_list_contains_check, _list_count_check]
    else:
        # ★ 非创建型需求（登录/查询/展示/反馈类）: "列表出现记录/行数+1"是类别错误，
        #   改用需求反馈断言（引号段原文 needle，占位符模板自动降级为 no_error）
        from src.pipeline.step3.case_rules._intent_checks import IntentChecksMixin

        _fb_needle = IntentChecksMixin._extract_short_keywords(expected)
        # ★ 极性一致（判据委托唯一词表 expects_error_feedback）：需求自身期望"错误/拒绝"反馈时，
        #   追加 no_error 与需求期望直接矛盾——SUT 正确渲染错误提示反被判 FAIL（实测 REQ-003-P01）。
        from src.pipeline.step3._rule_extractor_keywords import (
            expects_error_feedback as _expects_err_fb,
        )

        _fb_expects_err = _expects_err_fb(expected)
        base_checks = []
        if _fb_needle:
            base_checks.append(
                {
                    "track": "ui",
                    "precision": "fuzzy",
                    "check": {
                        "type": "text_contains_check",
                        "expected": _fb_needle,
                        "operator": "contains",
                        "selector": "body",
                    },
                    "expect": f"页面出现需求期望反馈（含 '{_fb_needle}'）",
                }
            )
        if not _fb_expects_err:
            base_checks.append(
                {
                    "track": "ui",
                    "precision": "exact",
                    "check": {"type": "no_error", "selector": "", "expected": "无错误"},
                    "expect": "系统无报错",
                }
            )
        elif not base_checks:
            base_checks.append(
                {
                    "track": "ui",
                    "precision": "exact",
                    "check": {
                        "type": "no_error",
                        "selector": "",
                        "expected": "无错误",
                        "mode": "defense",
                    },
                    "expect": "页面无致命错误",
                    "description": "防御型：需求期望错误反馈，仅判系统级致命错误/崩溃/泄露",
                }
            )
    # 可选 DB 深断言（仅当 TARGET_DB 明确指向 SUT 库、且**表/列经真实 schema 确认**时）
    #   ★ F3 根因修复(2026-09-26)：表必须存在于 SUT 库，且 where 用到的列也必须存在，
    #     否则跳过（不发射必然报"非法表名/列不存在"的假 FAIL 断言）。
    if _is_creation and _has_sut_db_target():
        _tbl = _infer_entity_table(req)
        _cols = [c.lower() for c in (_sut_schema().get(_tbl) or [])] if _tbl else []
        _uniq_col = "name" if "name" in _cols else ""
        if _tbl and _uniq_col:
            base_checks.append(
                {
                    "track": "db",
                    "precision": "exact",
                    "check": {
                        "type": "db_record_exists_check",
                        "table": _tbl,
                        "expected_count": 1,
                        "operator": "gte",
                        "where": f"{_uniq_col} LIKE '%{_unique_val}%'",
                    },
                    "expect": f"DB 表中存在含唯一值 '{_unique_val}' 的记录（业务真正落库）",
                }
            )
        else:
            _log.info(
                "[fallback] 跳过 DB 深断言：表/列未经 schema 确认 table=%r cols=%s",
                _tbl, _cols[:8],
            )

    # 异常/边界用例的字段级错误断言（精确 selector，非大杂烩 OR 关键词）
    field_error_check = {
        "track": "ui",
        "precision": "exact",
        "check": {
            "type": "field_error_visible_check",
            "selector": _FIELD_ERROR_SELECTOR,
            "expected": "非空错误提示文本",
            "operator": "not_empty",
        },
        "expect": "提交非法输入后，对应字段级错误提示可见且文本非空（精确校验，非模糊关键词）",
    }
    # 负向不变断言：列表不含该记录 / 行数不变（后端拒绝）
    negative_invariant_check = {
        "track": "ui",
        "precision": "exact",
        "check": {
            "type": "list_row_count_check",
            "selector": _LIST_ROW_SELECTOR,
            "expected_delta": 0,
            "operator": "eq",
            "baseline_action_index": 1,
        },
        "expect": "提交非法输入后，列表行数未增加（后端拒绝非法数据，未产生脏记录）",
    }

    # ★ 数据缺失退化分支（button_not_found）：严禁再生成"element_exists main + no_error"假 PASS。
    #   改为真实功能探针：navigate → 探索可交互控件 → 良性交互 → 断言具体业务信号。
    #   若确实无法定位任何控件，则标记 low_confidence + require_manual_review + status:"blocked"。
    if button_not_found:
        _log.warning(
            "[_build_rule_fallback_cases] ★功能探针★ %s 按钮未找到 → 生成真实功能探针意图"
            "(非假PASS降级): operation=%r, module=%s, nav_target=%s",
            rid, operation[:50], module, nav_target,
        )
        # ★ 通用修复(2026-09-24): 探针交互模板必须随需求语义变化，禁止"新增表单"一套打天下。
        #   现象: REQ-024(登录拒绝)/REQ-021(未登录重定向)/REQ-025(列表展示) 等非创建型
        #     需求的 E01 用例，actions 一律是
        #     click"新增/新建/添加/创建" → fill"名称/姓名输入框" → click"保存/提交/确定"。
        #   根因: 探针分支把"新增流程"交互硬编码进模板，只有 checks 被 _is_creation 门控，
        #     actions 未门控 → 需求语义从未传导到动作层。
        #   后果: 登录页/列表页无这些控件 → 元素定位必失败 → 引擎自造 ERROR（非 SUT 缺陷）。
        #   通用性: 按「提交凭证 / 创建记录 / 其余只读」三态生成交互，跨系统/行业/页面成立。
        from src.pipeline.step3._rule_extractor_keywords import is_login_reject

        # ★ 三态场景判定(2026-09-24): 动作层(_probe_actions)与探索计划层
        #   (_explore_task.test_method)共用同一决策 —— 此前 test_method 恒为
        #   "新增表单"形状，非创建型需求（登录拒绝/403/只读）被误导 → 同一根因的
        #   第二处表现。单一决策点，杜绝口径漂移。
        # ★ 单一决策点(2026-10-14)：探针场景必须与主分支**同源** —— 复用主分支
        #   已由需求语义算出的 `_form_scope`（字段×校验 / 录入控件语义）。
        #   根因: 探针分支此前自建三态（只认 `_is_creation`），主分支的
        #     `_form_scope`（含"字段+校验词共现"）从未传导到动作层 → 校验型需求
        #     （"验证价格字段大于0"）被判 readonly → 只发 navigate+screenshot，
        #     而断言要求"校验/拦截提示可见" → 结构上不可能满足（实测 REQ-026/028/
        #     049-E01：页面为空白新增表单，未出现校验提示 → 假 FAIL）。
        #   修复: 校验型（field×validation 语义）单列 `validation` 场景，动作必须
        #     "进表单 → 提交非法值"，使断言所需的系统反应真实可发生。
        # ★ 根因修复(2026-10-14) RC-AK: `validation` 场景（填**非法值**）只对
        #   **期望被拒绝**的需求生效 —— 场景判定与极性同源（全仓唯一判据
        #   `expects_error_feedback`），杜绝"同一语义在此处再推一次"。
        #   根因: 原判据只用 `_form_scope`（字段×校验语义共现），而"字段+校验"
        #     同样出现在**期望成功**的需求里（如"勾选确认后提交 → 操作成功"）。
        #     于是正向需求被套用"填非法值"动作 → 提交被客户端拦截 → 页面停在表单
        #     → 断言"操作成功"恒不满足（实测 TC-REQ-049-E01 FAIL）。
        try:
            from src.pipeline.step3._rule_extractor_keywords import (
                expects_error_feedback as _probe_expects_err,
            )

            # ★ 极性只取**期望反馈**（需求读取端已收口，`expected` 即权威极性）；
            #   operation 仅在无期望反馈时兜底 —— 否则 operation 里描述防护机制的
            #   措辞（"否则客户端拦截"）会把**期望成功**的需求误判成期望被拒绝
            #   （实测 REQ-049 期望"操作成功"却被套用"填非法值"动作 → 假 FAIL）。
            _probe_expects_reject = _probe_expects_err(expected or operation)
        except Exception:  # noqa: BLE001
            _probe_expects_reject = False
        _scenario = (
            "login_reject"
            if is_login_reject(operation, expected)
            else "access_control" if _scenario == "access_control"
            else "validation" if (_form_scope and _probe_expects_reject)
            else "creation" if (_is_creation or _form_scope)
            else "readonly"
        )
        _probe_actions = [{"type": "navigate", "target_hint": nav_target or "/"}]
        if _scenario == "login_reject":
            # 提交凭证场景：填入错误凭证并提交（拒绝反馈由 checks 断言）
            _probe_actions += [
                {"type": "fill", "target_hint": "用户名,账号,登录名",
                 "value": "wrong_user_9347"},
                {"type": "fill", "target_hint": "密码,口令",
                 "value": "wrong_pass_9347"},
                {"type": "click", "target_hint": "登录,提交,确定"},
            ]
        elif _scenario == "access_control":
            # ★ FIX4/AJ(2026-09-25): 访问控制场景 → 导航 + 尝试受限操作，
            #   拒绝由 access_control_check 在活动页面上取证（与主分支同源）。
            _probe_actions = _access_control_actions(
                nav_target, _restricted_verbs or ["新增"]
            )[:-1]  # 去掉末尾 screenshot，由下方统一追加
        elif _scenario == "creation":
            # 创建记录场景：进入新增表单 → **按需求字段规格完整录入** → 提交良性唯一值
            # ★ RC-AJ(2026-10-14): 与 UX02 分支同因——只填"名称"会漏掉必填复选框
            #   等控件 → 客户端校验拦截 → 提交从未发生。字段规格关联不到时降级单 fill。
            try:
                from src.pipeline.step3._rule_extractor_fields import (
                    build_form_data_actions as _build_creation_form_data,
                )

                _creation_fills = _build_creation_form_data(
                    operation, module=module, unique_value=_unique_val, project=_proj
                )
            except Exception:  # noqa: BLE001
                _creation_fills = []
            if not _creation_fills:
                _creation_fills = [
                    {"type": "fill", "target_hint": "名称/姓名输入框", "value": _unique_val},
                ]
            _probe_actions += [
                {"type": "click", "target_hint": "新增,新建,添加,创建", "in_dialog": False},
            ]
            _probe_actions += _creation_fills
            _probe_actions += [
                {"type": "click", "target_hint": "保存,提交,确定", "in_dialog": True},
            ]
        elif _scenario == "validation":
            # 字段校验/拒绝反馈场景：进入录入表单 → 填入**非法值** → 提交。
            #   触发序列由**单一事实源** `_trigger_actions.build_field_validation_trigger`
            #   提供（与定稿守卫 `_check_guard.ensure_post_action_triggers` 共用同一构造器，
            #   杜绝"同一语义在多处各拼一套"）。
            from src.pipeline.step3._trigger_actions import (
                build_field_validation_trigger as _build_validation_trigger,
            )

            _probe_actions += _build_validation_trigger(_entry_hint, operation, expected)
        # 其余（查看/查询/拒绝/展示类）：只读探针，导航 + 截图，断言承担验证
        _probe_actions.append({"type": "screenshot"})

        # ★ 场景化探索计划(2026-09-24): _explore_task.test_method 与 _probe_actions
        #   同源（同一 _scenario），供 Step4 AgentExplorer 引导实时探索使用。
        #   根因: 此前 test_method 恒为"新增表单→校验提示→列表记录"形状，
        #     非创建型需求（登录拒绝 / 403 / 只读）被引导去找不存在的表单控件 →
        #     引擎自造 ERROR。与动作层是同一根因的第二处表现。
        _nav_plan = f"侧边栏→{module}" if module else "侧边栏菜单"
        # ★ 通用修复(2026-09-24): 探针探索计划必须携带 op_type —— Step4 guided 执行
        #   用 `_enter_form = bool(inputs) or op_type == "form"` 决定是否进入表单/提交。
        #   根因: 探针 test_method 从不写 op_type，且 creation 之外的场景 inputs 为空
        #     （readonly 探针 inputs={}）→ 门控恒不开 → 零动作 → Agent 停在列表页
        #     对需求反馈做验证 → 引擎自造 ERROR（实测 (False,False) 门控组 401 例）。
        #   通用性: op_type 是三态场景的语义标签（登录/创建=form 录入型，其余=view
        #     只读型），跨系统/行业/页面成立；readonly 标 view 可确保门控不开（只读探针
        #     本就不该点"新增"），与 _probe_actions 完全同源。
        # validation 与 login_reject/creation 同为**录入型**（Step4 guided 门控
        # `_enter_form` 据此允许进表单/提交），readonly 标 view 保证只读探针不点创建入口。
        _probe_op_type = (
            "security_behavior" if _scenario == "access_control"
            else "form" if _scenario in ("login_reject", "creation", "validation")
            else "view"
        )
        if _scenario == "access_control":
            # 行为型访问控制：判据由 checks 层的结构化 `access_control_check` 权威表达
            # （含 require=denied / control_hints / restricted_actions，Step4 有确定性
            # 执行器 _handle_access_control_check 直接驱动受限操作并取证）。此处若再发射
            # 一条自然语言 verify，同一语义会被 Step4 guided 路径当"引导断言"二次判定：
            # 判失败会提前终结用例，令 checks 层的确定性执行器根本无从运行
            # （实测 TC-REQ-017/020-E01：checks 完备却报"验证'低权身份…被拒绝'"未通过）。
            # 故此处只保留探索计划的可观察前置，语义判据单一归属于 checks 层。
            _probe_test_method = {
                "op_type": _probe_op_type,
                "navigation": _nav_plan,
                "inputs": {},
                "verifications": [],
            }
        elif _scenario == "login_reject":
            _probe_test_method = {
                "op_type": _probe_op_type,
                "navigation": _nav_plan,
                "inputs": {"用户名": "wrong_user_9347", "密码": "wrong_pass_9347"},
                "verifications": [
                    {"step": 1, "action": "fill", "target": "用户名,账号,登录名",
                     "value": "wrong_user_9347", "expect": "输入错误用户名"},
                    {"step": 2, "action": "fill", "target": "密码,口令",
                     "value": "wrong_pass_9347", "expect": "输入错误密码"},
                    {"step": 3, "action": "click", "target": "登录,提交,确定",
                     "value": "", "expect": "提交错误凭证"},
                    {"step": 4, "action": "verify", "target": "操作结果",
                     "value": _fb_needle,
                     "expect": "错误凭证被拒绝，提示错误信息/登录失败"},
                ],
            }
        elif _scenario == "creation":
            _probe_test_method = {
                "op_type": _probe_op_type,
                "navigation": _nav_plan,
                "inputs": {"名称/姓名": _unique_val},
                "verifications": [
                    {"step": 1, "action": "verify", "target": "新增表单",
                     "value": "", "expect": "点击新增按钮后表单/弹窗打开"},
                    {"step": 2, "action": "verify", "target": "校验提示",
                     "value": "", "expect": "提交后出现字段级错误提示或成功落地记录"},
                    {"step": 3, "action": "verify", "target": "列表记录",
                     "value": _unique_val,
                     "expect": f"列表含唯一值 '{_unique_val}' 的记录"},
                ],
            }
        elif _scenario == "validation":
            # 字段校验场景：探索计划与 _probe_actions 同源（同一 _scenario）
            #   —— 进表单 → 提交非法值 → 断言需求期望的拒绝/校验反馈可见。
            _probe_test_method = {
                "op_type": _probe_op_type,
                "navigation": _nav_plan,
                "inputs": {"名称/姓名": "!@#"},
                "verifications": [
                    {"step": 1, "action": "verify", "target": "录入表单",
                     "value": "", "expect": "点击录入入口后表单/弹窗打开"},
                    {"step": 2, "action": "verify", "target": "需求期望反馈",
                     "value": _fb_needle,
                     "expect": (
                         f"提交非法值后页面出现需求期望的拒绝/校验反馈（含 '{_fb_needle}'）"
                         if _fb_needle
                         else f"提交非法值后系统按需求拒绝：{desc[:40]}"
                     )},
                ],
            }
        else:
            # 只读/权限/审计/查询类：仅验证需求期望反馈，禁止假设存在新增表单
            _probe_test_method = {
                "op_type": _probe_op_type,
                "navigation": _nav_plan,
                "inputs": {},
                "verifications": [
                    {"step": 1, "action": "verify", "target": "需求期望反馈",
                     "value": _fb_needle,
                     "expect": (
                         f"页面出现需求期望反馈（含 '{_fb_needle}'）"
                         if _fb_needle
                         else f"页面行为符合需求：{desc[:40]}"
                     )},
                ],
            }

        _probe_case = {
            "case_id": f"TC-{safe_rid}-E01",
            "req_id": rid,
            "title": f"{module}-{rid}-{operation}-功能探针验证（探索）",
            "description": (
                f"对{desc}做真实功能探针：进入模块页→探索可交互控件→执行良性交互"
                f"→断言具体业务信号（表单出现/校验提示/列表状态变化）"
            ),
            "risk_level": "P2",
            "type": "功能",
            "scenario_type": "exploratory",
            "scenario_label": f"{operation}-功能探针验证（探索）",
            "test_method": "FD",
            "control_type": "exploratory",
            "required_tracks": _tracks_for_type("功能"),
            "actions": _probe_actions,
            # 具体业务信号断言（可证伪，绝非"页面没崩"）
            # ★ 创建语义门控(2026-09-22): 探针分支原样假设"新增流程"——非创建型需求
            #   （登录失败提示/查询/展示类）被注入"新增表单+唯一值列表记录"的荒谬断言
            #   → 假 FAIL。非创建型探针断言 = 需求反馈 needle + 无报错。
            "checks": (
                [
                    {
                        "track": "ui",
                        "precision": "fuzzy",
                        "check": {
                            "type": "element_exists",
                            "selector": _FORM_CONTAINER_SELECTOR,
                            "expected": "存在",
                        },
                        "expect": "新增表单/弹窗已打开（证明模块可进入新增流程）",
                    },
                    {
                        "track": "ui",
                        "precision": "exact",
                        "check": {
                            "type": "field_error_visible_check",
                            "selector": _FIELD_ERROR_SELECTOR,
                            "expected": "非空错误提示文本",
                            "operator": "not_empty",
                        },
                        "expect": "提交后出现校验提示或字段级错误（证明交互确被系统响应）",
                    },
                    {
                        "track": "ui",
                        "precision": "fuzzy",
                        "check": {
                            "type": "text_contains_check",
                            "expected": _unique_val,
                            "operator": "contains",
                            "selector": _LIST_CONTAINER_SELECTOR,
                        },
                        "expect": f"列表中出现含唯一值 '{_unique_val}' 的记录（证明新增成功）",
                    },
                ]
                if _is_creation
                else (
                    [
                        {
                            "track": "ui",
                            "precision": "fuzzy",
                            "check": {
                                "type": "text_contains_check",
                                "expected": _fb_needle,
                                "operator": "contains",
                                "selector": "body",
                            },
                            "expect": f"页面出现需求期望反馈（含 '{_fb_needle}'）",
                        }
                    ]
                    if _fb_needle
                    else []
                )
                + [
                    {
                        "track": "ui",
                        "precision": "exact",
                        "check": {
                            "type": "no_error",
                            "selector": "",
                            "expected": "无错误",
                            "mode": "defense",
                        },
                        "expect": "系统无报错（Agent 引导断言承担语义验证）",
                    }
                ]
            ),
            "_generation_source": "fallback_functional_probe",
            "_exploratory": True,
            "low_confidence": True,
            # 严禁作为"业务通过"绿用例流出：明确待人工核验项
            "require_manual_review": True,
            "status": "blocked",
            "explore_reason": "按钮未在静态DOM中找到→真实功能探针(替代假PASS降级)",
            "_explore_task": {
                "operation": operation,
                "target_page": nav_target or "/",
                "description": desc,
                "expected_feedback": expected,
                "module": module,
                # ★ 场景化探索计划(2026-09-24): 与 _probe_actions 同源（_scenario），
                #   禁止非创建型需求沿用"新增表单→校验提示→列表记录"三步计划。
                "test_method": _probe_test_method,
            },
        }
        # 诚实化 required_tracks
        _probe_case["required_tracks"] = _normalize_required_tracks(
            _probe_case["required_tracks"], _probe_case["checks"]
        )

        # ── ★ E2c(2026-09-26)：横切维度模板改由 `build_cross_cut_cases` 单一事实源产出 ──
        #   原探针分支自建的 `_ext_case`（V01/V02/PF01/UX01/CP01/SEC01）已删除，
        #   根因见该函数头部说明（模板漂移 + 不可证伪的 SEC01 假 PASS）。
        _probe_all = [_probe_case] + build_cross_cut_cases(
            rid=rid,
            safe_rid=safe_rid,
            module=module,
            desc=desc,
            nav_target=nav_target or "/",
            reason_note=reason_note,
            entry_hint=_entry_hint,
            form_scope=_form_scope,
            unique_val=_unique_val,
            req=req,
        )
        if _scenario == "access_control":
            _apply_access_control_template(
                _probe_all, nav_target, _restricted_verbs, module, desc,
                specs=_AC_PROBE_SPECS, preserve_control_type=True,
            )
        return _probe_all

    # ★ 通用修复: 修复 control_type 张冠李戴
    #   原bug: N01/B01 强制改为 input，但 actions 是 upload_file + click（control_type=upload）
    #   修复: N01/B01 保持与 P01 一致的 control_type，因为 actions 模板相同
    #   通用性：control_type 应反映实际 actions，而非强行套用 input

    # P01 actions：在 fill 前加 count_list_rows（基线），便于 list_row_count_check 对比
    _p01_actions = [_count_list_rows_action()] + _p01_actions

    _p01_case = {
        "case_id": f"TC-{safe_rid}-P01",
        "req_id": rid,
        "title": f"{module}-{rid}-正向流程验证",
        "description": (
            f"执行{desc}的核心主流程，"
            + (
                "断言列表出现新建记录且行数+1"
                if _is_creation
                else "断言页面出现需求期望反馈且系统无报错"
            )
            + reason_note
        ),
        "risk_level": "P1",
        "type": "功能",
        "scenario_type": "happy_path",
        "test_method": "FD",
        "control_type": control_type,
        "required_tracks": _tracks_for_type("功能"),
        "actions": _p01_actions,
        "checks": base_checks,
        "_generation_source": "local_rule_fallback",
    }
    _p01_case["required_tracks"] = _normalize_required_tracks(
        _p01_case["required_tracks"], _p01_case["checks"]
    )

    # N01 actions：基线 + 非法填值 + 提交
    _n01_actions = [_count_list_rows_action()] + _build_base_actions(
        error_case=True, boundary_case=False
    )
    _n01_case = {
        "case_id": f"TC-{safe_rid}-N01",
        "req_id": rid,
        "title": f"{module}-{rid}-异常输入校验",
        "description": f"对{desc}输入非法数据，断言字段级错误可见且列表行数不变（后端拒绝）{reason_note}",
        "risk_level": "P2",
        "type": "安全",
        "scenario_type": "error_path",
        "test_method": "EG",
        "control_type": control_type,
        "required_tracks": ["ui", "api", "db"],
        "actions": _n01_actions,
        "checks": [field_error_check, negative_invariant_check],
        "_generation_source": "local_rule_fallback",
    }
    _n01_case["required_tracks"] = _normalize_required_tracks(
        _n01_case["required_tracks"], _n01_case["checks"]
    )

    # B01 actions：基线 + 边界值（空/超长）+ 提交
    _b01_actions = [_count_list_rows_action()] + _build_base_actions(
        error_case=False, boundary_case=True
    )
    _b01_case = {
        "case_id": f"TC-{safe_rid}-B01",
        "req_id": rid,
        "title": f"{module}-{rid}-边界值与异常验证",
        "description": f"对{desc}输入边界值与异常数据，断言字段级校验提示可见且列表行数不变{reason_note}",
        "risk_level": "P2",
        "type": "安全",
        "scenario_type": "boundary",
        "test_method": "BV",
        "control_type": control_type,
        "required_tracks": _tracks_for_type("安全"),
        "actions": _b01_actions,
        "checks": [field_error_check, negative_invariant_check],
        "_generation_source": "local_rule_fallback",
    }
    _b01_case["required_tracks"] = _normalize_required_tracks(
        _b01_case["required_tracks"], _b01_case["checks"]
    )

    # ── ★ E2c(2026-09-26)：横切维度模板改由 `build_cross_cut_cases` 单一事实源产出 ──
    _ext_cases = build_cross_cut_cases(
        rid=rid,
        safe_rid=safe_rid,
        module=module,
        desc=desc,
        nav_target=nav_target or "/",
        reason_note=reason_note,
        entry_hint=_entry_hint,
        form_scope=_form_scope,
        unique_val=_unique_val,
        req=req,
    )
    # ★ 统一收口：断言语义对齐（声明了文案断言却无承载 check → 按自身声明补齐）
    _all_cases = [_p01_case, _n01_case, _b01_case] + _ext_cases

    _aligned = _align_checks_with_declared_expectation(_all_cases)
    # ★ 根因修复(2026-09-25) FIX4/AJ: 访问控制型需求 → 五条模板整体改写为**行为断言**。
    #   必须放在文案对齐收口**之后**: 对齐收口会从描述里的引号样例补
    #   text_contains_check，把需求文本里的业务名词/设计陈述当作"页面应出现的文案"
    #   —— 访问控制需求恰恰没有这种页面文案（其期望是**行为**: 低权被拒），
    #   若先改写再对齐，等于把刚拆掉的"文本匹配"假 FAIL 又装回去。
    #   改写整体替换 actions/checks（语义自洽），非访问控制需求零行为变更。
    if _scenario == "access_control":
        _apply_access_control_template(
            _aligned, nav_target, _restricted_verbs, module, desc
        )
    return _aligned
