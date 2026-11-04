import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)
# -*- coding: utf-8 -*-
"""
智能失败原因分析模块
====================
从 ExcelHandler 类中提取的静态方法。
优先使用 RAG + AI 做根因分析；若不可用，回退到静态关键词匹配。
"""


def _rag_failure_analysis(case_id, title, failure_detail=""):
    """★ v6: 使用 RAG + AI 进行智能失败原因分析。

    流程：
    1. 用失败测试用例信息检索 RAG 索引（相关 API/DB/Page）
    2. 将检索结果 + 失败信息发送给 AI 做根因分析
    3. 返回结构化分析报告
    4. 若 RAG 或 AI 不可用，返回空字符串（触发回退到静态分析）
    """
    try:
        from src.extensions.rag_engine import rag_engine
    except Exception as _e:
        _log.debug("[FailureAnalyzer] RAG 引擎导入失败: %s", _e, exc_info=True)
        return ""

    # 1. 检查 RAG 是否可用
    try:
        if rag_engine.chunk_count == 0:
            return ""
    except Exception as _e:
        _log.debug("[FailureAnalyzer] RAG 可用性检查失败: %s", _e, exc_info=True)
        return ""

    # 2. 构建检索查询
    title_str = str(title) if title else ""
    query = "%s %s %s" % (case_id, title_str, failure_detail)

    # 3. RAG 检索相关 API/DB/Page
    try:
        retrieved = rag_engine.retrieve(
            {"req_id": str(case_id), "module": "", "description": query},
            top_k=3,
        )
        # ★ S3-012: 记录 RAG 使用日志
        try:
            from src.extensions.rag_engine.rag_usage_logger import log_rag_retrieval

            log_rag_retrieval(
                retrieved, case_id=str(case_id), req_id="failure_analysis"
            )
        except Exception as _e:
            _log.debug("[failure_analyzer] RAG 使用日志记录异常: %s", _e, exc_info=True)
    except Exception as e:
        _log.info("  [RAG] 检索失败: %s", str(e)[:60])
        return ""

    # 4. 检查是否有检索结果
    has_results = any(
        retrieved.get(k) for k in ("api_endpoints", "db_tables", "figma_pages")
    )
    if not has_results:
        return ""  # 无检索结果，回退到静态分析

    # 5. 格式化 RAG 上下文
    rag_ctx = rag_engine.format_retrieval_context(retrieved)
    if not rag_ctx:
        return ""

    # 6. 调用 AI 分析
    try:
        from src.ai.ai_client import ai_brain

        system_prompt = (
            "你是一名资深测试自动化工程师和故障诊断专家。\n"
            "请根据以下信息，分析测试用例失败的根本原因。\n\n"
            "## 分析要求\n"
            "1. 结合失败测试用例的相关 API/DB/Page 信息，推断可能的失败原因\n"
            "2. 区分前端问题（UI元素变更、文案不匹配、页面结构变化）和后端问题（API返回变更、数据库状态异常）\n"
            "3. 给出具体可操作的修复建议\n"
            "4. 用中文输出，简洁专业\n\n"
            "## 输出格式（严格遵循）\n"
            "【失败现象】简述失败表现\n"
            "【可能原因】\n"
            "1. 原因一（结合检索到的具体 API/DB/Page 信息）\n"
            "2. 原因二\n"
            "【建议修复】\n"
            "• 修复方案一\n"
            "• 修复方案二\n"
        )

        user_message = (
            "## 失败测试用例信息\n"
            "- 测试用例ID: %s\n"
            "- 测试用例标题: %s\n"
            "- 失败详情: %s\n\n"
            "%s\n\n"
            "请分析该测试用例失败的原因。"
        ) % (case_id, title_str, failure_detail or "（无额外详情）", rag_ctx)

        result = ai_brain._call_api(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.3,
            max_tokens=1024,
            caller_name="RAG失败分析",
            task_type="failure_analysis",
            force_cloud=True,
        )

        if result.get("success"):
            analysis = result.get("data", "").strip()
            if analysis:
                return analysis
        _log.info("  [RAG] AI 分析返回空")
        return ""

    except Exception as e:
        _log.info("  [RAG] AI 分析异常: %s", str(e)[:80])
        return ""


def _auto_failure_analysis(case_id, title, failure_detail=""):
    """失败原因自动分析入口：优先 RAG+AI → 回退静态关键词匹配。"""
    # ★ v6: 优先使用 RAG + AI 智能分析
    with guard("_auto_failure_analysis: rag_result = _rag_failure_analysis(c", log=_log, level=logging.WARNING):
        rag_result = _rag_failure_analysis(case_id, title, failure_detail)
        if rag_result:
            _log.info("  [RAG] ✓ AI 智能失败分析已生成")
            return rag_result

    # 回退：静态关键词匹配
    title_str = str(title) if title else ""
    if "XSS" in title_str.upper():
        return (
            "【失败现象】XSS脚本注入后系统未能正确过滤，浏览器执行了注入的恶意脚本。\n\n"
            "【可能原因】\n1. 后端未对用户输入进行HTML实体编码转义\n"
            "2. 前端使用 innerHTML 直接渲染搜索关键词\n"
            "3. 缺少 Content-Security-Policy 响应头\n\n"
            "【建议修复】\n• 对所有用户输入进行HTML实体编码转义\n"
            "• 替换 innerHTML → textContent 渲染用户内容\n"
            "• 添加 CSP 头"
        )
    if "SQL" in title_str.upper() or "注入" in title_str:
        return (
            "【失败现象】SQL注入 payload 被数据库执行，可能存在SQL注入漏洞。\n\n"
            "【可能原因】\n1. 未使用参数化查询\n2. 输入过滤不完整\n\n"
            "【建议修复】\n• 全面使用参数化查询/预编译语句\n• 后端增加输入白名单校验"
        )
    if "登录" in title_str or "权限" in title_str:
        return (
            "【失败现象】登录或权限校验异常。\n\n"
            "【可能原因】\n1. 权限校验逻辑存在缺陷\n2. 会话管理异常\n"
            "3. 角色配置未正确生效\n\n"
            "【建议修复】\n• 检查角色-权限映射表配置\n• 验证会话Token生成与校验逻辑"
        )
    return "执行失败，需人工排查。建议检查操作日志和截图确认失败步骤。"


def mark_flaky(case_id: str):
    """
    ★ P1-4: 标记测试用例为 Flaky（写入 MySQL）。
    """
    with guard("mark_flaky: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config, get_current_user

        uid = get_current_user() or ""
        _log.error("[failure_analyzer] Flaky标记: %s (用户=%s)", case_id, uid)
        from src.backend.state import _thread_run_id

        _run_id = getattr(_thread_run_id, "value", "") or ""
        from src.users.manager import record_execution

        record_execution(
            uid,
            config.project_name,
            case_id,
            "FLAKY",
            case_asset_file="",
            case_row=0,
            screenshot="",
            failure_reason="Flaky: 首次FAIL, 重跑PASS",
            run_id=_run_id,
        )
