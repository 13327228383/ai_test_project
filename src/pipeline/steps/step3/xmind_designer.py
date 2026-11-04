# -*- coding: utf-8 -*-
"""
XMind 测试设计生成器（MySQL 版 + 三元组驱动）
==============================================
数据源：requirements 表三元组（operation + condition_expr + expected_feedback + control_type）。
按控件类型区分测试方向：按钮不测安全，输入框必须测边界，等等。

返回值与原函数一致：成功时返回 doc_type='xmind' 的 version_label；失败时返回空字符串。
"""

import base64
import logging

_log = logging.getLogger(__name__)


def _xmind_bytes_from_path(xmind_path):
    """读取 build_xmind 产生的临时 .xmind 文件，返回字节；读取后删除。"""
    from pathlib import Path

    p = Path(str(xmind_path))
    try:
        if not p.is_file():
            return b""
        data = p.read_bytes()
        try:
            p.unlink()
        except Exception as _pass_e:
            _log.warning("xmind_designer.py:37 异常被吞: %s", _pass_e, exc_info=True)
        return data
    except Exception as _e:
        _log.warning("  [xmind] 读取临时 xmind 文件失败: %s", _e)
        return b""


def _build_user_prompt(project_name, version, triples_by_module, module_test_types):
    """构建 user message，包含三元组和控件→测试类型映射。"""
    lines = [
        "## 项目: %s (版本: %s)" % (project_name, version),
        "",
        "## 控件类型 → 适用测试方向（只生成对应方向的测试点）",
        "",
    ]
    for ct, types in sorted(module_test_types.items()):
        lines.append("- %s: %s" % (ct or "未分类", "、".join(types)))
    lines.append("")
    lines.append("## 原子需求点（三元组）")
    lines.append("")

    total_chars = sum(len(l) for l in lines)
    for mod_name, triples in sorted(triples_by_module.items()):
        header = "### 模块: %s (%d条三元组)" % (mod_name, len(triples))
        if total_chars + len(header) > 15000:
            break
        lines.append(header)
        total_chars += len(header)
        for t in triples[:10]:
            parts = []
            if t.get("operation"):
                parts.append("操作: %s" % t["operation"])
            if t.get("condition_expr"):
                parts.append("条件: %s" % t["condition_expr"])
            if t.get("expected_feedback"):
                parts.append("预期: %s" % t["expected_feedback"])
            if t.get("control_type"):
                parts.append("控件: %s" % t["control_type"])
            line = "  - [%s] %s" % (t["req_id"], " | ".join(parts))
            if total_chars + len(line) > 15000:
                break
            lines.append(line)
            total_chars += len(line)
    return "\n".join(lines)


def generate_xmind_design() -> str:
    """根据三元组生成 XMind 测试设计（base64 写入 MySQL），返回 version_label。"""
    from src.ai.ai_client import ai_brain
    from src.core.config_loader import config
    from src.pipeline.steps.step3.triple_reader import (
        read_triples,
        group_triples_by_module,
        get_test_types_for_control,
    )

    project_name = getattr(config, "project_name", "") or ""
    from src.users.project_auth import get_active_version_label

    version_label = get_active_version_label(project_name)
    if not version_label:
        version_label = (getattr(config, "project_version", None) or "") or "v1"

    from src.core.versioning import get_version_id

    version_id = get_version_id(project_name, version_label)

    triples = read_triples(project_name, version_id, exclude_implicit=True)
    if not triples:
        _log.info("    暂无三元组数据，跳过 XMind 生成")
        return ""

    # 按模块分组，同时收集所有出现过的控件类型
    triples_by_module = group_triples_by_module(triples)
    all_control_types = set()
    for t in triples:
        ct = (t.get("control_type") or "").strip()
        if ct:
            all_control_types.add(ct)

    # 构建控件→测试类型映射
    module_test_types = {}
    for ct in sorted(all_control_types):
        module_test_types[ct] = get_test_types_for_control(ct)

    _log.info(
        "    [XMind] %d 条三元组 → %d 个模块，%d 种控件类型",
        len(triples),
        len(triples_by_module),
        len(module_test_types),
    )

    user_msg = _build_user_prompt(
        project_name, version_label, triples_by_module, module_test_types
    )

    system_prompt = (
        "你是资深测试架构师。输入是原子需求点（三元组=操作+条件+预期+控件）和各控件类型适用的测试方向。\n"
        "请根据三元组和控件类型，生成一份测试设计思维导图 JSON。\n\n"
        "## 测试方向选择规则（根据控件类型，只生成适用的方向）\n"
        "- button/nav/display: 只测功能+UI，不测安全/边界/数据\n"
        "- input/upload: 测功能+边界+安全+数据\n"
        "- select: 测功能+边界+数据\n"
        "- interactive/composite: 测功能+UI+边界(+数据)\n\n"
        "## 输出 JSON 格式（严格遵守，不要额外文字）\n"
        "{\n"
        '  "project": "项目名",\n'
        '  "version": "版本号",\n'
        '  "types": [{\n'
        '    "name": "功能测试",\n'
        '    "modules": [{\n'
        '      "name": "模块名",\n'
        '      "points": [{\n'
        '        "desc": "测试点描述（一句话说清测什么，20字以内）",\n'
        '        "method": "EP",\n'
        '        "technique": "等价类：正常值1/正常值2/异常值",\n'
        '        "suggested_intent": {"title":"测试用例标题","type":"正向或异常","risk_level":"P1","steps":"意图动作1,证据采集动作2,人工可读动作3"}\n'
        "      }]\n"
        "    }]\n"
        "  }]\n"
        "}\n\n"
        "## 编写规则\n"
        "1. 每个模块在每个适用测试方向下至少 2 个测试点\n"
        "2. 不适用该模块的测试方向不要生成（如按钮模块不生成安全测试）\n"
        "3. desc 简洁精准（≤20字），从三元组的 operation/expected 提取灵感\n"
        "4. method 可选值：EP/BV/DT/CE/ST/OE/ET/SC\n"
        "5. technique 写出具体测试数据和场景\n"
        "6. suggested_intent 中的 type 用中文：正向、异常、边界\n"
        "7. steps 字段用于 XMind/人工阅读导出，写 3-6 个候选意图动作或证据采集动作\n"
        "8. JSON 字符串内部不要出现未转义的换行符、双引号\n"
        "9. 只输出纯 JSON，不要 markdown 代码块包裹\n"
    )

    # 估算最大 tokens
    try:
        from src.ai.token_estimator import compute_max_tokens
        from src.core.config_loader import config as _xd_cfg

        xmind_max_tokens, _ = compute_max_tokens(
            system_prompt, user_msg, model=_xd_cfg.ai_model
        )
        xmind_max_tokens = max(int(xmind_max_tokens), 16384)
    except Exception as _e:
        _log.debug("[xmind_designer] 非关键异常: %s", _e, exc_info=True)
        xmind_max_tokens = 16384

    design = None
    last_error = ""
    for attempt in range(1, 4):
        result = ai_brain._call_api(
            system_prompt=system_prompt,
            user_message=user_msg,
            temperature=0.3,
            max_tokens=xmind_max_tokens,
            caller_name="XMind测试设计",
            task_type="test_design",
            force_cloud=True,
        )
        if not result.get("success"):
            last_error = "AI 调用失败: %s" % (str(result.get("error", "未知"))[:120])
            _log.error("    [WARN] XMind %d/3: %s", attempt, last_error)
            continue

        parsed = ai_brain._parse_json_response(result["data"])
        if parsed.get("success"):
            candidate = parsed["data"]
            # ★ AI 偶发返回 list 而非 dict，需类型校验后才能 .get()，
            #   否则 AttributeError 会直接中断 3 次重试机制。
            if not isinstance(candidate, dict):
                last_error = "AI 返回非 dict 格式: %s" % type(candidate).__name__
                _log.error("    [WARN] XMind %d/3: %s", attempt, last_error)
                continue
            if (
                candidate.get("types")
                and isinstance(candidate["types"], list)
                and len(candidate["types"]) > 0
            ):
                design = candidate
                break
            else:
                last_error = "JSON 缺少 types 数组或为空"
                _log.error("    [WARN] XMind %d/3: %s", attempt, last_error)
        else:
            last_error = str(parsed.get("error", "JSON 解析失败"))[:120]
            _log.error("    [WARN] XMind %d/3: %s", attempt, last_error)
    else:
        if design is None:
            _log.error("    [WARN] XMind 生成失败（已重试 3 次）: %s", last_error)
            return ""

    if not design.get("project"):
        design["project"] = project_name

    try:
        from src.pipeline.steps.step3.xmind_generator import build_xmind

        xmind_result = build_xmind(design, project_name)
    except Exception as _e:
        _log.error("    [WARN] build_xmind 异常: %s", _e)
        return ""

    if not xmind_result or not xmind_result.get("success"):
        _log.error(
            "    [WARN] XMind 文件生成失败: %s",
            xmind_result.get("error") if xmind_result else "",
        )
        return ""

    # 从临时文件读取字节，base64 编码后写入 MySQL
    xmind_bytes = _xmind_bytes_from_path(xmind_result.get("path"))
    if not xmind_bytes:
        _log.error("    [WARN] 未能从 build_xmind 读到有效的 xmind 二进制")
        return ""

    encoded = base64.b64encode(xmind_bytes).decode("ascii")
    content = (
        "## XMind 测试设计（二进制 Base64）\n\n"
        "- project: %s\n"
        "- version_label: %s\n"
        "- byte_size: %d\n"
        "- base64_size: %d\n\n"
        "```xmind-b64\n%s\n```\n"
    ) % (project_name, version_label, len(xmind_bytes), len(encoded), encoded)

    conn = None
    try:
        import hashlib
        from src.core.db_manager import get_connection

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn = get_connection()
        conn.execute(
            "INSERT INTO source_documents "
            "(project, doc_type, version_label, content, content_hash, char_count, "
            " created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW()) "
            "ON DUPLICATE KEY UPDATE content = VALUES(content), "
            "content_hash = VALUES(content_hash), char_count = VALUES(char_count), "
            "updated_at = NOW()",
            (project_name, "xmind", version_label, content, content_hash, len(content)),
        )
        conn.commit()
        _log.info(
            "    ✅ XMind 已写入 MySQL (doc_type=xmind, version=%s, bytes=%d)",
            version_label,
            len(xmind_bytes),
        )
        return version_label
    except Exception as _e:
        _log.error("    [WARN] XMind MySQL 写入失败: %s", _e)
        return ""
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning(
                    "xmind_designer.py:206 异常被吞: %s", _pass_e, exc_info=True
                )
