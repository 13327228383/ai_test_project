# -*- coding: utf-8 -*-
"""
执行控制 — 配置开关端点
========================
P1/P2: 防幻觉功能开关 API (flags / toggle)。
从 execution.py 拆分出来。

★ 2026-08-06 D2 扩展（docs/frontend-adaptation-design-2026-08-06.md §3.3）：
  - 新增模块级 `TOGGLEABLE_FLAGS`，作为 flags / toggle 的**单一事实源**
    （原先 toggle 里硬编码 `valid_keys` 字面量，与 flags 返回的字段各说各话，
     新增开关要改两处且容易漏）。
  - `/api/config/flags` 在保留原 3 个 snake_case 顶层字段的前提下新增 `flags` 明细，
    带 `source` / `locked` —— 这是为了解决"点了开关没反应还不知道为什么"：
    配置有五层优先级（§8.6），toggle 只写第三层，被上面两层压制时必须如实告知。
  - `/api/config/toggle` 新增白名单校验、bool 类型校验、config_history 留痕、locked 警告。
"""

import json
from src.core.guarded import guard
import logging
import os

from flask import jsonify, request

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 可切换开关白名单 —— flags / toggle 的单一事实源
# ══════════════════════════════════════════════════════════════════════════
# ★ 默认值一律沿用各消费模块 _cfg(key, default) 的默认值，全部 False → 零行为变更。
#   `owner` 记录该开关的真实读取点，便于排查"改了没生效"时直接跳到消费代码。
#
# ⚠️ 为什么不能写成 `TOGGLEABLE_FLAGS = W3_FEATURE_FLAGS`（§8.7 已核实）：
#   1) 有 5 个布尔开关（FAKE_PASS_AUDIT_ENABLED / ENGINE_HEALTH_ENABLED /
#      PRIVILEGE_MATRIX_ENABLED / PER_CASE_IDENTITY_ENABLED / ADI_GATE_ENABLED）
#      **不在** _taxonomy.py 的 W3_FEATURE_FLAGS 注册表里，是各模块直接 _cfg 读取的；
#   2) W3_FEATURE_FLAGS 里有 4 个**非布尔**项（IDENTITY_POOL_KEY / IDENTITY_DEFAULT /
#      CONTRACT_SCHEMA_SOURCE 为 str，PERF_BASELINE_WINDOW 为 int），
#      它们不是开关，纳入 toggle 会被 bool 化而破坏配置。
#   因此这里必须显式列举，不能图省事去引用注册表。
TOGGLEABLE_FLAGS: dict[str, dict] = {
    # ── 既有 3 项（保持不变，向后兼容） ──
    "FLAKY_DETECTION_ENABLED": {
        "label": "Flaky 检测",
        "group": "防幻觉",
        "default": False,
        "owner": "src/core/config_loader",
    },
    "SCHEMA_VALIDATION_ENABLED": {
        "label": "Schema 校验",
        "group": "防幻觉",
        "default": False,
        "owner": "src/core/config_loader",
    },
    "REQUIREMENT_REVIEW_GATE": {
        "label": "需求评审门禁",
        "group": "防幻觉",
        "default": False,
        "owner": "src/core/config_loader",
    },
    # ── W3 注册表内（_taxonomy.py:614 W3_FEATURE_FLAGS，布尔项） ──
    "ATTRIBUTION_ENABLED": {
        "label": "归因报告区块",
        "group": "W3",
        "default": False,
        "owner": "_taxonomy.W3_FEATURE_FLAGS",
    },
    "ATTRIBUTION_STRICT_TPR": {
        "label": "严格通过率口径",
        "group": "W3",
        "default": False,
        "owner": "_taxonomy.W3_FEATURE_FLAGS",
        "danger": True,
    },
    "IDENTITY_POOL_ENABLED": {
        "label": "身份池",
        "group": "W3",
        "default": False,
        "owner": "_taxonomy.W3_FEATURE_FLAGS",
    },
    "CONTRACT_ENABLED": {
        "label": "契约断言",
        "group": "W3",
        "default": False,
        "owner": "_taxonomy.W3_FEATURE_FLAGS",
    },
    "NETWORK_FAULT_ENABLED": {
        "label": "网络故障注入",
        "group": "W3",
        "default": False,
        "owner": "_taxonomy.W3_FEATURE_FLAGS",
        "danger": True,
    },
    "IDEMPOTENCY_ENABLED": {
        "label": "幂等比对区块",
        "group": "W3",
        "default": False,
        "owner": "_taxonomy.W3_FEATURE_FLAGS",
    },
    "PERF_BASELINE_ENABLED": {
        "label": "性能基线",
        "group": "W3",
        "default": False,
        "owner": "_taxonomy.W3_FEATURE_FLAGS",
    },
    # ── W3 注册表外（各模块直接 _cfg 读取，已逐一核实读取点） ──
    "FAKE_PASS_AUDIT_ENABLED": {
        "label": "假 PASS 审计",
        "group": "W3",
        "default": False,
        "owner": "step5/report_generator.py:760",
    },
    "ENGINE_HEALTH_ENABLED": {
        "label": "引擎健康治理",
        "group": "W3",
        "default": False,
        "owner": "step5/report_generator.py:767",
    },
    "PRIVILEGE_MATRIX_ENABLED": {
        "label": "越权矩阵",
        "group": "W3",
        "default": False,
        "owner": "utils/privilege_matrix.py:42",
    },
    "PER_CASE_IDENTITY_ENABLED": {
        "label": "逐用例身份切换",
        "group": "W3",
        "default": False,
        "owner": "utils/identity_pool.py:136",
    },
    "ADI_GATE_ENABLED": {
        "label": "ADI 断言深度门禁",
        "group": "W3",
        "default": False,
        "owner": "step3/oracle/guard.py:39",
        "danger": True,
    },
    # ── ★ v6.1(2026-08-12) 安全/混沌工程开关 ──
    "SECURITY_SCAN_ENABLED": {
        "label": "安全扫描（OWASP Top 10）",
        "group": "安全",
        "default": True,
        "owner": "extensions/security_scanner/__init__.py",
    },
    "SECURITY_INVERSE_ENABLED": {
        "label": "安全反向用例生成（越权/注入/CSRF）",
        "group": "安全",
        "default": False,
        "owner": "pipeline/steps/step3_inverse/security.py",
    },
    "NETWORK_FAULT_AUTO_ENABLED": {
        "label": "混沌工程: 网络故障自动注入",
        "group": "混沌",
        "default": False,
        "owner": "pipeline/steps/step4/browser_tools/_network_fault.py",
        "danger": True,
    },
    # ── ★ v6.2(2026-08-26) 报告门禁开关 —— 纳入单一事实源，前端 quality-panel 聚合展示 ──
    #   根因：PERF_GATE_ENABLED / A11Y_REPORT_ENABLED 原先散落 config.yaml，
    #       未进 TOGGLEABLE_FLAGS → 前端 flags 面板看不到、切换不了，形成「门禁暗开关」。
    #   默认 False（零行为变更），消费方 report_generator.py:1086/1097。
    "PERF_GATE_ENABLED": {
        "label": "性能门禁报告区块",
        "group": "报告门禁",
        "default": False,
        "owner": "step5/report_generator.py:1086",
    },
    "A11Y_REPORT_ENABLED": {
        "label": "可访问性(a11y)报告区块",
        "group": "报告门禁",
        "default": False,
        "owner": "step5/report_generator.py:1097",
    },
    # ── ★ v6.2(2026-08-26) 变更影响→回归范围 知识库（低成本，默认开） ──
    #   默认 True（只读 executions 历史 + project_configs，无副作用、零行为变更风险）。
    #   关闭只需一步：toggle 置 false 即可。
    "CHANGE_IMPACT_KB_ENABLED": {
        "label": "变更影响→回归范围知识库",
        "group": "报告门禁",
        "default": True,
        "owner": "extensions/change_impact_kb/report_section.py:build_change_impact_section",
    },
}

# config_history 的配置节名（所有开关变更归入同一节，便于按节筛选审计）
_HISTORY_SECTION = "feature_flags"


# ══════════════════════════════════════════════════════════════════════════
# 配置来源解析
# ══════════════════════════════════════════════════════════════════════════


def _resolve_source(key: str) -> str:
    """判定某个开关当前生效值来自哪一层（§8.6 的五层优先级）。

    返回: user_db / env / runtime / project_config / system_config / default

    ⚠️ 这里的判定顺序必须与 `pipeline_config._read_config_value` 的实际读取顺序
      严格一致，否则前端展示的 source 会骗人。任何一方改动都要同步核对。
    """
    try:
        from src.core.pipeline_config import _read_system_config, _read_user_config
    except Exception as e:  # noqa: BLE001 —— 配置模块不可用时不阻断 flags 展示
        _log.debug("[Config] 来源解析导入失败: %s", e)
        return "default"

    # ① 用户级 DB 配置（最高）
    with guard("_resolve_source: if _read_user_config(key) is not Non", log=_log):
        if _read_user_config(key) is not None:
            return "user_db"

    # ② 环境变量（直接同名）
    if os.environ.get(key) is not None:
        return "env"

    # ③ config._get 内部又分四小层
    with guard("_resolve_source: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        # ③a 环境变量别名映射
        env_map = getattr(config, "_ENV_KEY_MAP", None) or {}
        if key in env_map and os.environ.get(env_map[key]) not in (None, ""):
            return "env"
        # ③c toggle 写入的运行时覆盖（③b 与 ② 重复，已在上面命中）
        overrides = getattr(config, "_runtime_overrides", None) or {}
        if key in overrides:
            return "runtime"
        # ③d 项目级配置（project_configs / YAML）
        data = getattr(config, "_config_data", None) or {}
        if data.get(key) is not None:
            return "project_config"
        yaml_map = getattr(config, "_YAML_KEY_MAP", None) or {}
        alt = yaml_map.get(key)
        if alt and alt != key and data.get(alt) is not None:
            return "project_config"

    # ④ 系统级 DB 配置（最低）
    with guard("_resolve_source: if _read_system_config(key) is not N", log=_log):
        if _read_system_config(key) is not None:
            return "system_config"

    return "default"


def _effective_value(key: str, default: bool) -> bool:
    """读取开关的**生效值**（穿过全部五层优先级）。"""
    try:
        from src.core.pipeline_config import _read_config_value

        return bool(_read_config_value(key, default))
    except Exception as e:  # noqa: BLE001 —— 读不到一律按默认值，不让面板崩
        _log.warning("[Config] 读取 %s 生效值失败，回落默认值: %s", key, str(e)[:160])
        return bool(default)


def _build_flags_view() -> dict[str, dict]:
    """构造 `/api/config/flags` 的 `flags` 明细视图。

    `locked=true` 表示该开关被环境变量锁定 —— 前端应置灰并提示，
    因为此时 toggle 写入的 runtime 层永远压不过 env 层，点了不会生效。
    """
    view: dict[str, dict] = {}
    for key, meta in TOGGLEABLE_FLAGS.items():
        default = bool(meta.get("default", False))
        source = _resolve_source(key)
        view[key] = {
            "value": _effective_value(key, default),
            "default": default,
            "label": meta.get("label", key),
            "group": meta.get("group", ""),
            "source": source,
            "locked": source == "env",
            "danger": bool(meta.get("danger", False)),
        }
    return view


# ══════════════════════════════════════════════════════════════════════════
# 写侧辅助（持久化 / 留痕）
# ══════════════════════════════════════════════════════════════════════════


def _persist_flag(key: str, value: bool, project: str) -> None:
    """持久化开关到 `project_configs` 表，确保重启 / reload 后不丢失。

    逻辑与改造前完全一致（仅从 api_config_toggle 内联抽出为独立函数，
    以便主函数保持在可读长度内）。

    ⚠️ 本函数**照常向上抛异常**，由调用方 `_persist_flag_or_warning()` 统一
      转成用户可见的 warning —— 不在这里吞掉（BUG#2 的成因就是被吞在这一层）。
    """
    from src.users.manager import _conn

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT config_json FROM project_configs WHERE project=%s", (project,)
        )
        row = cur.fetchone()
        cur.close()

        db_config = {}
        if row and row.get("config_json"):
            raw = row["config_json"]
            db_config = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(db_config, dict):
            db_config = {}

        db_config[key] = bool(value)

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO project_configs(project, config_json) VALUES(%s,%s) "
            "ON DUPLICATE KEY UPDATE config_json=VALUES(config_json)",
            (project, json.dumps(db_config)),
        )
        conn.commit()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("execute + commit + close(): %s", _e, exc_info=True)


# 持久化失败时回给前端的提示语。抽成常量是为了让单测能直接断言前缀，
# 不必把中文文案硬编码在测试里（改文案时不会连带改崩测试）。
_PERSIST_FAIL_WARNING = (
    "配置已在内存生效，但持久化失败 —— 仅在本次运行期有效，重启后将丢失：%s"
)


def _persist_flag_or_warning(key: str, value: bool, project: str) -> str:
    """持久化开关，成功返回空串，失败返回给用户看的 warning 文案。

    🟠 BUG#2 修复（QA_T01_T02_VERIFY_2026-08-06.md §三 BUG#2，2026-08-07）

    原实现把 `_persist_flag()` 的异常就地 `except` 掉、只打一条 `_log.warning`，
    响应体照样是 `{"ok": true, ...}`。真库实测（QA 的 `qa_b9_persist.py`）：
    `project_configs` 有 `fk_configs_project` 外键，项目名不存在时 INSERT 直接
    1452 报错，配置只落在内存 `_runtime_overrides` 里 —— **进程一重启就没了**，
    而用户看到的是一个干干净净的 `ok:true`，毫不知情。

    这与本接口既有的设计意图直接矛盾：`_resolve_source` 的存在就是为了
    「点了开关没反应必须让用户知道为什么」（见模块 docstring），env 锁定场景
    也确实正确地返回了 `warning`，唯独持久化失败这条路径在静默。

    修复：失败时**不再吞异常**，而是把失败原因转成 `warning` 字段带回响应体。
    仍然不阻断 toggle —— 内存值确实已经生效了，回滚它反而更糟。

    Returns:
        str: 成功为 `""`；失败为提示文案（非空即表示需要在响应体里加 warning）。
    """
    try:
        _persist_flag(key, bool(value), project)
    except Exception as db_err:  # noqa: BLE001 —— 转成 warning 上抛给用户，绝不静默
        detail = str(db_err)[:160]
        _log.warning(
            "[Config] %s → %s 内存已生效，但 project_configs 持久化失败: %s",
            key,
            value,
            detail,
        )
        return _PERSIST_FAIL_WARNING % detail

    _log.info("[Config] %s → %s (已持久化到 project_configs)", key, value)
    return ""


def _merge_warning(resp: dict, warning: str) -> None:
    """把一条 warning 并入响应体，已有 warning 时用 `；` 串联而不是覆盖。

    持久化失败与 env 锁定是两件独立的事，可能同时发生
    （例如 env 锁着、项目名又是脏数据）。谁也不该把对方的提示挤掉。
    """
    if not warning:
        return
    existing = resp.get("warning", "")
    resp["warning"] = ("%s；%s" % (existing, warning)) if existing else warning


def _record_flag_history(
    key: str, old_value: bool, new_value: bool, project: str
) -> None:
    """把开关变更写入 `config_history` 表（审计留痕）。

    复用既有的 `record_config_change()` 通道而不是自己拼 INSERT —— 它已处理
    scope 归一化、连接管理，并且 `/config-history` 页面正是读同一张表展示的。
    其第二个参数写入的是 `section` 列，故此处传 `feature_flags`。

    ★ 留痕失败绝不阻断 toggle：开关本身已经生效，因为审计写失败就回滚用户操作
      属于本末倒置（且 config_history.user_id 有外键约束，脏数据场景下会失败）。
    """
    with guard("_record_flag_history: from src.core.config_loader import g", log=_log, level=logging.WARNING):
        from src.core.config_loader import get_current_user
        from src.users.audit_logs import record_config_change

        user_id = get_current_user() or ""
        if not user_id:
            _log.debug("[Config] 无当前用户，跳过 config_history 留痕: %s", key)
            return
        record_config_change(
            user_id,
            _HISTORY_SECTION,
            json.dumps({key: bool(old_value)}, ensure_ascii=False),
            json.dumps({key: bool(new_value)}, ensure_ascii=False),
            scope_type="project",
            scope_id=project,
        )


def _register_config_routes():
    """注册配置开关路由到 exec_bp。延迟导入避免循环依赖。"""
    from src.backend.routes.execution import exec_bp

    @exec_bp.route("/api/config/flags", methods=["GET"])
    def api_config_flags():
        """获取所有防幻觉功能开关状态。

        ★ 向后兼容：原有 3 个 snake_case 顶层字段**原样保留**，
          现有前端零改动继续工作；新增的 `flags` 是增量字段。
        """
        try:
            from src.core.config_loader import config

            return jsonify(
                {
                    "flaky_detection_enabled": config.flaky_detection_enabled,
                    "schema_validation_enabled": config.schema_validation_enabled,
                    "requirement_review_gate": config.requirement_review_gate,
                    # ★ 开关明细数量以 TOGGLEABLE_FLAGS 为单一事实源，注释不写死数字，
                    #   避免再次与代码漂移（历史：14→15→18→20 三次注释漂移）。
                    "flags": _build_flags_view(),
                }
            )
        except Exception as e:
            _log.error("[api_config_flags] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取配置开关失败"}), 500

    @exec_bp.route("/api/config/toggle", methods=["POST"])
    def api_config_toggle():
        """切换单个功能开关。POST body: {"key": "FLAKY_DETECTION_ENABLED", "value": false}"""
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project

        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        data = request.get_json(silent=True) or {}
        key = data.get("key", "")
        value = data.get("value")

        # ① 白名单校验（单一事实源 TOGGLEABLE_FLAGS）
        if key not in TOGGLEABLE_FLAGS:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "无效的配置键: %s，有效键: %s"
                        % (key, ", ".join(sorted(TOGGLEABLE_FLAGS))),
                    }
                ),
                400,
            )

        # ② 类型校验 —— 必须在 bool() 转换之前做。
        #    JSON 里传字符串 "false" 时 bool("false") == True，会把用户的"关闭"
        #    静默变成"开启"，这是最难排查的一类 bug，宁可直接 400。
        if not isinstance(value, bool):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "value 必须为布尔类型 true/false，收到 %s: %r"
                        % (type(value).__name__, value),
                    }
                ),
                400,
            )

        from src.core.config_loader import config

        try:
            meta = TOGGLEABLE_FLAGS[key]
            default = bool(meta.get("default", False))
            old_value = _effective_value(key, default)
            proj = _current_project() or getattr(config, "_project_name", "default")

            # ③ 写内存（立即热加载生效）
            config._set_runtime(key, bool(value))

            # ④ 持久化到 DB（project_configs 表）
            #    ★ BUG#2：失败不再静默 —— 转成 warning 带回响应体，
            #      让用户知道「这次改了，但重启会丢」。
            persist_warning = _persist_flag_or_warning(key, bool(value), proj)

            # ⑤ 审计留痕
            _record_flag_history(key, old_value, bool(value), proj)

            # ⑥ 回读生效值：若被更高优先级层压制，如实告知，不静默欺骗用户
            resp = {"ok": True, "key": key, "value": bool(value)}
            effective = _effective_value(key, default)
            if effective != bool(value):
                resp["effective"] = effective
                if _resolve_source(key) == "env":
                    _merge_warning(resp, "该开关被环境变量锁定，本次设置不会生效")
                else:
                    _merge_warning(
                        resp,
                        "该开关被更高优先级配置覆盖，本次设置未生效（实际值 %s）"
                        % effective,
                    )

            # ⑦ 持久化告警（与 ⑥ 的锁定告警互相独立，同时发生时串联而非覆盖）
            #    ★ 只在**失败时**追加 `persisted:false` + warning。成功路径必须保持
            #      返回体精简为 {ok,key,value} —— 这是 test_config_toggle_whitelist
            #      ::test_toggle_has_no_warning_when_effective 守卫的既有契约
            #      （"正常生效时不应出现 warning / effective 噪音字段"）。恒发
            #      `persisted:true` 会让每次成功响应都多一个前端用不上的字段。
            if persist_warning:
                resp["persisted"] = False
                _merge_warning(resp, persist_warning)
            return jsonify(resp)
        except Exception as e:
            return jsonify({"ok": False, "error": "配置切换失败: %s" % str(e)}), 500


# ★ 模块加载时自动注册路由
_register_config_routes()
