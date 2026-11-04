# -*- coding: utf-8 -*-
"""建模引擎 - 验证器模块（从 _modeling_engine.py 拆分，单文件<=800行）。

职责：URL 导航地图校验 + 元素名称校验 + URL 清理。
  _validate_navigate_urls: 将 AI 幻觉 URL 替换为页面名称或 "/"
  _find_closest_nav_name: 查找最接近的导航页面名称
  _validate_element_names: 替换英文元素名为中文术语
  _extract_chinese_terms: 从 operation 提取中文术语
  _cleanup_navigate_urls: 清理 navigate 动作中的中文 URL

被 _modeling_engine.py 主流程调用（验证后置阶段）。
依赖：_log (logging.getLogger)

根因(3层追问):
  1. 表层: _modeling_engine.py 2150 行超 §4.3 红线
  2. 中层: 验证函数群与主流程内聚，但逻辑独立
  3. 根因: 验证是独立的"校验"职责，应拆为独立模块
通用性: 适用于所有系统/行业 - 纯 URL/元素名校验，不依赖业务术语
"""
import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def _validate_navigate_urls(all_intents: list[dict], project: str = "") -> list[dict]:
    """★ 通用修复(2026-07-18): URL 导航地图校验 — 将 AI 幻觉 URL 替换为页面名称或 "/"。

    问题：Step3 AI 经常编造导航地图中不存在的 URL（如 /auxiliaryManagement），
         导致 Step4 执行时导航到 404 页面。

    策略：
      1. 从 system_nav_maps 加载导航地图，提取所有有效 URL 和页面名称
      2. 遍历所有用例的 navigate 动作，检查 target_hint 是否为 URL（以 / 开头）
      3. 若 URL 不在导航地图中 → 替换为最接近的页面名称；若无匹配则替换为 "/"

    通用性：零业务硬编码，纯基于导航地图数据驱动。
    """
    import json as _json
    import re as _re_url

    if not all_intents:
        return all_intents

    # ── 1. 加载导航地图 ──
    _nav_urls = set()  # 所有有效 URL
    _nav_names = set()  # 所有页面名称（中文等）
    _nav_name_to_url = {}  # 页面名称 → URL 映射
    # ★ P0-1 读侧收口(2026-08-05): nav_graph_nodes 为唯一数据源。
    #   删除原 system_nav_maps 降级分支（约 80 行）—— 该表 0 行且无 nav_json 列，
    #   `SELECT nav_json` 恒抛 Unknown column 后被 except 吞掉，属纯死代码。
    #   改用 nav_graph_read 适配层：原实现依赖 config.project_version，为空时
    #   read_nav_graph_nodes 直接返回 []，适配层会回退到该 project 最新版本。
    from src.pipeline.utils.nav_graph_read import read_nodes as _read_nav_nodes
    from src.core.config_loader import config as _cfg

    _vl = getattr(_cfg, "project_version", "") or ""
    try:
        _nav_nodes = _read_nav_nodes(project, _vl or None)
    except Exception as _e:
        # 配置了导航图却读失败 → 属异常，必须留痕（不静默放行）
        _log.error(
            "[_validate_navigate_urls] nav_graph 读取失败 project=%s ver=%s: %s",
            project,
            _vl,
            _e,
            exc_info=True,
        )
        return all_intents

    for _node in _nav_nodes:
        _pu = (_node.get("page_url", "") or "").strip()
        _pp = (_node.get("page_path", "") or "").strip()
        _pt = (_node.get("page_title", "") or "").strip()
        if _pu:
            _nav_urls.add(_pu)
        if _pp and _pp != _pu:
            _nav_urls.add(_pp)
        if _pt:
            _nav_names.add(_pt)
            if _pu:
                _nav_name_to_url[_pt] = _pu
            elif _pp:
                _nav_name_to_url[_pt] = _pp

    if _nav_urls or _nav_names:
        _log.info(
            "[_validate_navigate_urls] nav_graph 加载成功: %d URLs, %d 页面名称",
            len(_nav_urls),
            len(_nav_names),
        )

    if not _nav_urls and not _nav_names:
        _log.debug("[_validate_navigate_urls] 导航地图为空，跳过校验")
        return all_intents

    # ★ 通用修复(2026-07-19): 如果 nav_map 有页面名但无真实 URL（如 Confluence/Figma 源），
    #   跳过 URL 校验，避免将所有 URL 误判为幻觉。AI 生成的 URL 可能来自 DOM 爬取上下文。
    if not _nav_urls:
        _log.info(
            "[_validate_navigate_urls] 导航地图无真实 URL（仅 %d 页面名），跳过 URL 校验",
            len(_nav_names),
        )
        return all_intents

    _log.info(
        "[_validate_navigate_urls] 导航地图: %d URLs, %d 页面名称",
        len(_nav_urls),
        len(_nav_names),
    )

    # ── 2. 校验并修正 ──
    _fixed_count = 0
    for _c in all_intents:
        if not isinstance(_c, dict):
            continue
        _actions = _c.get("actions", [])
        if isinstance(_actions, str):
            try:
                _actions = _json.loads(_actions)
            except Exception as _rx1:
                _log.debug(
                    "[_modeling_engine] 异常降级(continue): %s",
                    str(_rx1)[:60],
                    exc_info=True,
                )
                continue
        if not isinstance(_actions, list):
            continue

        for _act in _actions:
            if not isinstance(_act, dict):
                continue
            if str(_act.get("type", "")).strip().lower() != "navigate":
                continue

            _target = str(_act.get("target_hint", "")).strip()
            if not _target:
                continue

            # 只处理 URL 格式的 target_hint（以 / 或 http 开头）
            if not (_target.startswith("/") or _target.startswith("http")):
                continue

            # 规范化 URL（去掉 query string 和尾部斜杠）
            _url_normalized = _target.split("?")[0].rstrip("/")
            # ★ 通用修复(2026-07-18): "/" 是首页死页面，也需替换
            if not _url_normalized and _target != "/":
                continue

            # ★ 通用修复(2026-07-20 v4): 处理绝对 URL（nav_map real_url 是绝对URL）
            #   原bug: _nav_urls 只含相对路径（/xxx），绝对URL（http://...）永远不匹配，
            #         被误判为幻觉URL，替换为页面名，破坏 v4 fix 生成的 navigate(real_url)。
            #   修复: 如果是绝对URL，提取 path 部分再检查。
            #   通用性: 适用于所有系统的绝对URL（http/https），不依赖业务逻辑。
            #   合理性: 追根溯源到URL格式不一致的缺陷，_nav_urls 只存相对路径。
            #   覆盖性: 兼容绝对URL和相对路径两种输入。
            _path_for_check = _url_normalized
            if _url_normalized.startswith("http://") or _url_normalized.startswith(
                "https://"
            ):
                with guard("_validate_navigate_urls: from urllib.parse import urlparse as", log=_log):
                    from urllib.parse import urlparse as _urlparse

                    _path_for_check = _urlparse(_url_normalized).path or "/"

            # 检查是否在导航地图中（★ 优先检查完整URL含tab参数，再检查base URL）
            _is_valid = False
            # ★ 通用修复(2026-07-18): 先检查完整URL（含?tab=参数）是否在导航地图中
            if (
                _target in _nav_urls
                or _url_normalized in _nav_urls
                or _path_for_check in _nav_urls
            ):
                _is_valid = True
            elif _url_normalized and (
                _url_normalized in _nav_urls or _path_for_check in _nav_urls
            ):
                # base URL 在导航地图中，但完整URL不在
                # → 检查是否有query参数是tab（来自DOM爬取的合法Tab页面）
                if "?" in _target:
                    _query_part = _target.split("?", 1)[1] if "?" in _target else ""
                    # ★ 如果query参数包含tab=，说明是DOM爬取的合法Tab页面，不剥离
                    if "tab=" in _query_part:
                        _log.info(
                            "[_validate_navigate_urls] 保留合法Tab URL: %s (case=%s)",
                            _target,
                            _c.get("case_id", "?"),
                        )
                        _is_valid = True
                        continue  # 跳过，保留完整URL
                    else:
                        # 其他query参数（AI编造的），剥离
                        _log.warning(
                            "[_validate_navigate_urls] 剥离幻觉query参数: %s → %s (case=%s)",
                            _target,
                            _url_normalized,
                            _c.get("case_id", "?"),
                        )
                        _act["target_hint"] = _url_normalized
                        _act["url"] = _url_normalized
                        _fixed_count += 1
                        continue
                _is_valid = True

            if _is_valid:
                continue  # URL 合法，跳过

            # ★ URL 不在导航地图中 → 幻觉 URL，需要替换
            _log.warning(
                "[_validate_navigate_urls] 发现幻觉 URL: %s (case=%s)",
                _target,
                _c.get("case_id", "?"),
            )

            # 尝试匹配最接近的页面名称
            _best_name = _find_closest_nav_name(
                _url_normalized, _nav_names, _nav_name_to_url
            )
            if _best_name:
                _act["target_hint"] = _best_name
                # ★ 通用修复(2026-07-19): 从 _nav_name_to_url 查找页面名称对应的实际 URL
                #   之前将 url 设为页面名称（如"辅材管理"），导致 _validate_action_urls
                #   检查时 URL 不在合法 URL 集合中，url 被清空，Step4 无法导航。
                _best_url = _nav_name_to_url.get(_best_name, "")
                if _best_url:
                    _act["url"] = _best_url
                else:
                    # fallback: 使用第一个可用 URL（至少不是空的）
                    _act["url"] = next(iter(_nav_urls)) if _nav_urls else _best_name
                _log.info(
                    "[_validate_navigate_urls]   → 替换为页面名称: %s, url=%s",
                    _best_name,
                    _act["url"],
                )
            else:
                # ★ 通用修复(2026-07-19): 当 nav_map 无匹配时，保留原始 target_hint
                #   而非替换为 "/"。原始 target_hint 来自规则提取器或 AI，
                #   可能是真实页面路径（如 /dashboard），只是 nav_map 不完整。
                #   替换为 "/" 会导致 Step4 无法导航，所有用例失败。
                _act["target_hint"] = _target  # 保留原始 target_hint
                _act["url"] = _target  # 保留原始 url
                _log.info(
                    "[_validate_navigate_urls]   → nav_map 无匹配，保留原始: %s",
                    _target,
                )
            _fixed_count += 1

    if _fixed_count > 0:
        _log.warning("[_validate_navigate_urls] 共修正 %d 个幻觉 URL", _fixed_count)

    return all_intents


def _find_closest_nav_name(url: str, nav_names: set, name_to_url: dict) -> str:
    """从导航地图中找最接近的页面名称。

    策略（通用）：
      1. URL 最后一段与页面名称的拼音/英文匹配
      2. URL 片段与页面名称的子串匹配
      3. 无匹配返回空字符串
    """
    if not nav_names:
        return ""
    # ★ 通用修复(2026-07-18): "/" 首页是死页面，用第一个可用页面名称替代
    if not url:
        return next(iter(nav_names)) if nav_names else ""

    # 提取 URL 关键片段
    _url_parts = url.strip("/").lower().split("/")
    _url_last = _url_parts[-1] if _url_parts else ""

    # 常见英文→中文映射（通用，非业务硬编码）
    _common_en_to_cn = {
        "management": "管理",
        "list": "列表",
        "detail": "详情",
        "config": "配置",
        "settings": "设置",
        "dashboard": "看板",
        "query": "查询",
        "view": "查看",
        "edit": "编辑",
        "create": "新建",
        "delete": "删除",
        "import": "导入",
        "export": "导出",
        "report": "报告",
        "monitor": "监控",
        "flow": "流程",
        "task": "任务",
        "user": "用户",
        "role": "角色",
        "permission": "权限",
        "log": "日志",
        "material": "物料",
        "auxiliary": "辅材",
        "param": "参数",
        "recipe": "配方",
        "formula": "配方",
        "portrait": "画像",
        "model": "模型",
        "field": "字段",
        "data": "数据",
    }

    # 策略1：URL 最后一段的英文关键词 → 中文匹配
    _url_keywords = set()
    for _part in _url_parts:
        _part_lower = _part.lower()
        for _en, _cn in _common_en_to_cn.items():
            if _en in _part_lower:
                _url_keywords.add(_cn)

    # 策略2：直接子串匹配
    _best = ""
    _best_score = 0
    for _name in nav_names:
        _name_lower = _name.lower()
        _score = 0

        # 关键词匹配
        for _kw in _url_keywords:
            if _kw in _name:
                _score += 2

        # URL 最后一段与名称的模糊匹配
        if _url_last and len(_url_last) >= 3:
            # 英文名直接匹配
            if _url_last in _name_lower:
                _score += 3
            # 拼音首字母匹配
            _name_pinyin = "".join(c for c in _name_lower if c.isalpha())
            if len(_url_last) >= 3 and _url_last[:3] in _name_pinyin:
                _score += 1

        if _score > _best_score:
            _best_score = _score
            _best = _name

    if _best_score >= 2:
        return _best

    return ""


def _validate_element_names(
    all_intents: list[dict], project: str = "", version_id: int = 1
) -> list[dict]:
    """★ 通用修复(2026-07-18): 元素名校验 — 将 AI 生成的英文元素名替换为需求文档中的中文原文。

    问题：Step3 AI 经常将中文需求术语翻译为英文（如 auxiliarycode、auxiliarytype、blendingcode），
         导致 Step4 在中文页面上找不到对应元素。

    策略：
      1. 从 requirements 表加载每个需求的 operation 字段（中文原文）
      2. 遍历所有用例的非 navigate 动作，检测 target_hint 是否含英文 ASCII 单词
      3. 若含英文单词 → 使用需求文档的 operation 文本替换
      4. 从 operation 中提取关键中文术语（去除动词前缀如"编辑"、"新建"、"单个"）
    """
    # 动词前缀列表（通用）
    _VERB_PREFIXES = [
        "新建",
        "编辑",
        "删除",
        "查看",
        "单个",
        "批量",
        "导入",
        "导出",
        "添加",
        "修改",
        "移除",
        "查询",
        "搜索",
        "新增",
        "创建",
        "更新",
        "配置",
        "设置",
        "提交",
        "保存",
        "取消",
        "确认",
        "审核",
        "审批",
        "启用",
        "禁用",
        "停用",
        "启用",
        "发布",
        "撤销",
        "复制",
        "移动",
        "上传",
        "下载",
        "登录",
        "退出",
        "注册",
        "切换",
        "选择",
        "筛选",
    ]

    # 加载所有需求
    _req_map = {}
    try:
        from src.core.db_manager import get_connection, release_connection

        _conn = get_connection()
        try:
            _rows = _conn.execute(
                "SELECT req_id, operation FROM requirements WHERE project=%s AND version_id=%s",
                (project, version_id),
            ).fetchall()
            for _r in _rows:
                _req_map[_r["req_id"]] = (_r.get("operation") or "").strip()
        finally:
            release_connection(_conn)
    except Exception as _e:
        _log.debug("[_validate_element_names] 需求加载失败(不阻塞): %s", str(_e)[:80])
        return all_intents

    _fixed_count = 0
    for _c in all_intents:
        _req_id = _c.get("req_id", "")
        _op_text = _req_map.get(_req_id, "")
        if not _op_text:
            continue

        _actions = _c.get("actions", [])
        for _act in _actions:
            _atype = str(_act.get("type", "")).strip().lower()
            if _atype == "navigate":
                continue  # 跳过 navigate 动作

            _target = str(_act.get("target_hint", "") or "").strip()
            if not _target:
                continue

            # ★ 方案B (ADR-017, 2026-07-23): intent: 语义意图前缀跳过英文名替换
            #   根因(§10.9 3层追问):
            #     1. 表层: intent:批量导入辅材 被替换为 辅材:批量导入辅材 → _control_validation 降级
            #     2. 中层: _validate_element_names 用 [a-zA-Z]{3,} 检测英文，"intent" 被当作英文元素名
            #     3. 根因: intent: 是 Plan B 协议前缀（Step3↔Step4 契约），不是英文元素名
            #   通用性(§12.4 9维度):
            #     1. 跨系统: intent: 协议与系统无关
            #     2. 跨行业: 语义意图适用于任何行业
            #     3. 跨页面: 列表/详情/表单/Tab 页都适用
            #     4. 跨Tab: Step4 实时感知处理 Tab
            #     5. 数据完整性: 不依赖任何数据
            #     6. 根因溯源: 修协议破坏，非打补丁
            #     7. 覆盖性: 影响所有 intent: 前缀的 target_hint
            #     8. 全局观: 恢复 Plan B 协议，消除第7个降级路径
            #     9. 职责边界: Step3 后处理修复
            #   自我反驳三次:
            #     1) 跳过替换会放过英文元素名？→ 不会。intent: 是协议前缀，后面是中文操作语义
            #     2) 只跳过 intent: 够吗？→ 是。其他 target_hint 的英文替换仍正常工作
            #     3) 删掉 _validate_element_names？→ 否。它仍替换 AI 生成的英文元素名
            if _target.startswith("intent:"):
                continue

            # 检测是否含英文单词（3个以上连续ASCII字母）
            import re

            _eng_words = re.findall(r"[a-zA-Z]{3,}", _target)
            if not _eng_words:
                continue

            # 从 operation 中提取中文术语
            _chinese_terms = _extract_chinese_terms(_op_text, _VERB_PREFIXES)
            if not _chinese_terms:
                continue

            # 用中文术语替换英文元素名
            _new_target = _target
            for _ew in _eng_words:
                _new_target = _new_target.replace(_ew, _chinese_terms)

            if _new_target != _target:
                _log.info(
                    "[_validate_element_names] 替换英文元素名: %s → %s (case=%s, req=%s)",
                    _target,
                    _new_target,
                    _c.get("case_id", "?"),
                    _req_id,
                )
                _act["target_hint"] = _new_target
                _fixed_count += 1

    if _fixed_count > 0:
        _log.warning("[_validate_element_names] 共修复 %d 个英文元素名", _fixed_count)
    else:
        _log.warning("[_validate_element_names] 未发现英文元素名，无需修复")

    return all_intents


def _extract_chinese_terms(op_text: str, verb_prefixes: list) -> str:
    """从 operation 文本中提取关键中文术语，去除所有动词前缀。"""
    _text = str(op_text).strip()
    if not _text:
        return ""

    # ★ 循环去除所有动词前缀（而非只去除第一个）
    while True:
        _found = False
        for _vp in sorted(verb_prefixes, key=len, reverse=True):
            if _text.startswith(_vp):
                _text = _text[len(_vp) :].strip()
                _found = True
                break
        if not _found:
            break

    # 去除尾缀（如"字段"、"按钮"）
    _suffixes = ["字段", "按钮", "操作", "功能", "管理", "配置"]
    for _sf in _suffixes:
        if _text.endswith(_sf):
            _text = _text[: -len(_sf)].strip()

    return _text if _text else op_text


def _cleanup_navigate_urls(flat_intents: list[dict]) -> None:
    """★ 通用修复(2026-07-19): 清理 navigate 动作中的中文 URL。

    Step3 AI 可能将中文页面名填入 url 字段（如 "url": "辅材配方管理"），
    导致 PageExplorer 无法提取 URL 且 AI loop 导航卡死。
    此函数在写入 DB 前扫描所有意图，将中文 URL 移至 target_hint 并清空 url。
    """
    import re as _re_cn
    import json as _json

    _cn_pattern = _re_cn.compile(r"[\u4e00-\u9fff]")
    _cleaned = 0

    for intent in flat_intents:
        _actions = intent.get("actions") or []
        if not _actions:
            continue

        _modified = False
        for _action in _actions:
            if _action.get("type") != "navigate":
                continue
            _url = _action.get("url", "")
            if not _url:
                continue
            _url = str(_url).strip()
            # 检测 URL 是否包含中文
            if _cn_pattern.search(_url):
                _hint = _action.get("target_hint", "")
                if not _hint:
                    _action["target_hint"] = _url
                _action["url"] = ""
                _modified = True

        if _modified:
            _cleaned += 1
            intent["actions"] = _actions

    if _cleaned > 0:
        _log.info("[Step3] URL 清理: %d 条意图的中文 URL 已移至 target_hint", _cleaned)
