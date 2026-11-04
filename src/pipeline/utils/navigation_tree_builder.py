# -*- coding: utf-8 -*-
"""
导航树构建器
============
★ 2026-07-08 新增：合并 nav_graph_nodes（原 system_nav_maps，已于 2026-08-05 DROP）+ system_feature_list，
构建完整的导航树结构（登录→模块→功能→控件），支持模块依赖关系和 Excel 导出。

数据流：
    nav_graph_nodes（原 system_nav_maps.nav_json）→ 菜单分组结构
    system_feature_list → 每个页面的功能控件
        ↓
    build_navigation_tree → 完整树形结构
        ↓
    export_to_excel → Excel 功能清单

使用方式：
    from src.pipeline.utils.navigation_tree_builder import build_navigation_tree, export_tree_to_excel
    tree = build_navigation_tree(project, version_label)
    excel_bytes = export_tree_to_excel(tree)
"""

import json
from src.core.guarded import guard
import logging
import re
from typing import Any
from collections import defaultdict

_log = logging.getLogger(__name__)

# ── 需求兜底树模块名归一化（★ 2026-09-01，仅展示层，不写库）──
#   背景：需求兜底路径的模块名直接继承需求文档章节标题，会带章节编号前缀
#   （如"2. 用户与认证"）或整篇文档标题型伪模块（如"图书管理系统 API 需求规格"），
#   造成"跳号/标题当模块"的观感困惑。此处统一归一化，树/模块列表/依赖三处共享。
_TITLE_MODULE_RE = re.compile(
    r"(需求规格|需求文档|需求说明|规格说明|系统需求|需求概述)", re.IGNORECASE
)
# 章节编号前缀：如 "2. 用户与认证" / "3.1 鉴权" / "5、新增图书" / "7-删除图书" / "7。删除图书"
_CHAPTER_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*\s*[、.．。:：\-—]?\s*")

# ── 模块颜色映射（Excel 样式）──
_MODULE_COLORS = [
    ("用户管理", "B4C6E7"),  # 浅蓝
    ("数据源", "FFE699"),  # 浅黄
    ("任务管理", "C6EFCE"),  # 浅绿
    ("系统设置", "F4CCCC"),  # 浅红
    ("报表中心", "D9D9D9"),  # 浅灰
    ("权限管理", "E2EFDA"),  # 浅绿变体
    ("日志管理", "FFF2CC"),  # 浅黄变体
    ("其他", "B4C6E7"),  # 浅蓝（避免白底白字）
]

# ── 默认模块颜色索引 ──
_DEFAULT_MODULE_COLOR_INDEX = 0


def _get_module_color(module_name: str) -> str:
    """根据模块名获取对应颜色（十六进制 RGB）。"""
    for name_hint, color in _MODULE_COLORS:
        if name_hint in module_name or module_name in name_hint:
            return color
    # 未匹配的模块轮流分配颜色
    idx = hash(module_name) % len(_MODULE_COLORS)
    return _MODULE_COLORS[idx][1]


def normalize_module_name(name: str) -> str:
    """★ 需求兜底树模块名归一化（仅展示层，不写库）。

    处理两类"观感噪声"：
      1. 章节编号前缀：去掉 "2. 用户与认证" → "用户与认证"，
         "3.1 鉴权" → "鉴权"，消除"1/3/4/6/8 跳号"的困惑（跳号源于源文档
         描述性章节无需求，与模块无关）。
      2. 文档标题型伪模块：含"需求规格/需求文档/系统需求"等 → 归为「概述」，
         避免把整篇文档标题误当成真实功能模块。

    通用性：纯文本规则，跨系统/文档格式通用；未匹配任何规则时原样返回，
    不影响正常业务模块名。
    """
    if not name:
        return "未分类"
    n = str(name).strip()
    n = _CHAPTER_PREFIX_RE.sub("", n).strip()
    if not n:
        return "概述"
    if _TITLE_MODULE_RE.search(n):
        return "概述"
    return n


def build_navigation_tree(
    project: str,
    version_label: str,
    include_features: bool = True,
    include_dependencies: bool = True,
) -> dict[str, Any]:
    """
    构建完整的导航树结构。

    Args:
        project: 项目名
        version_label: 版本标签
        include_features: 是否包含功能控件（从 system_feature_list）
        include_dependencies: 是否包含模块依赖关系（从 requirements 或推断）

    Returns:
        {
            "success": bool,
            "tree": dict,  # 树形结构
            "modules": list[str],  # 模块列表
            "dependencies": list[dict],  # 模块依赖关系
            "statistics": dict,  # 统计信息
            "error": str,
        }
    """
    result = {
        "success": False,
        "tree": {},
        "modules": [],
        "dependencies": [],
        "statistics": {
            "total_pages": 0,
            "total_modules": 0,
            "total_features": 0,
            "total_controls": 0,
        },
        "error": "",
    }

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        if not conn:
            result["error"] = "数据库连接失败"
            return result

        try:
            # 1. 读取导航地图
            # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps.nav_json → nav_graph。
            #   根因: system_nav_maps 表**没有 nav_json / version_label 列**
            #   （实际列为 nav_map_json），原查询恒抛 Unknown column，被外层
            #   except 吞成 error 字段；且该表 0 行、全仓无写入方。
            #   read_nav_map 返回同样的 {"flat_list","menu_groups"} 形状，
            #   下游 _build_tree_from_nav_and_features 无需改动。
            from src.pipeline.utils.nav_graph_read import read_nav_map

            nav_data = read_nav_map(project, version_label or None)

            # 2. 读取功能清单
            feature_rows = (
                conn.execute(
                    "SELECT page_url, page_path, page_title, features_json, statistics_json, source "
                    "FROM system_feature_list "
                    "WHERE project = %s AND version_label = %s "
                    "ORDER BY page_path",
                    (project, version_label),
                ).fetchall()
                or []
            )

            # 3. 构建页面→功能映射
            page_features_map = {}
            for row in feature_rows:
                page_path = row.get("page_path", "") or "/"
                page_url = row.get("page_url", "")
                page_title = row.get("page_title", "")
                features_json = row.get("features_json", "") or "[]"
                try:
                    features = (
                        json.loads(features_json)
                        if isinstance(features_json, str)
                        else features_json
                    )
                except Exception:
                    features = []

                page_features_map[page_path] = {
                    "page_url": page_url,
                    "page_title": page_title,
                    "features": features,
                    "source": row.get("source", "dom"),
                }

            # 4. 构建导航树
            tree = _build_tree_from_nav_and_features(nav_data, page_features_map)

            # ★ 通用兜底(2026-08-31)：有需求必有导航树。
            #   根因：导航树此前只从 Step4 实爬数据（nav_graph / system_feature_list）构建，
            #   当 Step4 未执行（或当前版本无 DOM 采集）时，即便 requirements 已有需求，
            #   树仍然是空，触发「有需求却无导航树」的逻辑断链。
            #   通用修复：当导航/功能数据均为空时，退化为「需求 → 模块 → 操作」结构，
            #   保证任何含需求的项目都有导航树可看、可导出。
            if not tree.get("children"):
                tree = _build_tree_from_requirements(conn, project, version_label)

            # 5. 提取模块列表
            modules = _extract_modules_from_tree(tree)

            # 6. 构建模块依赖关系（可选）
            dependencies = []
            if include_dependencies:
                dependencies = _build_module_dependencies(
                    conn, project, version_label, modules
                )

            # 7. 统计信息
            stats = _compute_tree_statistics(tree)
            # ★ 依赖统计补齐：统计卡「依赖关系」此前从不在 statistics 中，
            #   前端 statDependencies 读不到该字段恒为「—」，而树图虚线用的是顶层
            #   dependencies 数组（有数据），造成「虚线有、统计卡空」的不一致。
            #   此处把模块级依赖数并入 statistics，令统计卡与树图同一数据源。
            stats["total_dependencies"] = len(
                [d for d in dependencies if d.get("level") == "module"]
            )

            result.update(
                {
                    "success": True,
                    "tree": tree,
                    "modules": modules,
                    "dependencies": dependencies,
                    "statistics": stats,
                    "error": "",
                }
            )

            _log.info(
                "[nav_tree] ✓ 导航树构建完成: %d 模块, %d 页面, %d 功能",
                len(modules),
                stats["total_pages"],
                stats["total_features"],
            )

        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)

    except Exception as e:
        result["error"] = str(e)[:200]
        _log.warning("[nav_tree] 导航树构建失败: %s", str(e)[:120])

    return result


def _build_tree_from_nav_and_features(
    nav_data: dict,
    page_features_map: dict,
) -> dict[str, Any]:
    """
    从导航地图和功能清单构建树形结构。

    树形结构：
    {
        "name": "登录",
        "type": "root",
        "children": [
            {
                "name": "模块1",
                "type": "module",
                "color": "B4C6E7",
                "children": [
                    {
                        "name": "功能1-1",
                        "type": "function",
                        "page_url": "...",
                        "children": [
                            {"name": "保存按钮", "type": "control", "control_type": "button"},
                            {"name": "用户名输入框", "type": "control", "control_type": "input"},
                        ]
                    }
                ]
            }
        ]
    }
    """
    # 根节点：登录
    root = {
        "name": "登录",
        "type": "root",
        "url": "",
        "path": "/login",
        "children": [],
        "color": "FFFFFF",
    }

    # 从导航地图提取菜单分组
    menu_groups = nav_data.get("menu_groups", [])
    flat_list = nav_data.get("flat_list", [])

    # 如果有分组信息，按分组构建模块节点
    if menu_groups:
        for group in menu_groups:
            group_name = group.get("group_name", "未分类")
            items = group.get("items", [])

            # 模块节点
            module_node = {
                "name": group_name,
                "type": "module",
                "color": _get_module_color(group_name),
                "children": [],
            }

            # 遍历分组下的菜单项
            for item in items:
                item_text = item.get("text", "")
                item_url = item.get("url", "")
                item_path = item.get("path", "")
                # 完整层级路径（如 "数据管理/用户管理"），group_name 是第一段
                parent_path = item.get("parent_path", group_name)

                # 计算中间层级（去掉第一段模块名后的路径）
                path_parts = parent_path.split("/") if parent_path else []
                if path_parts and path_parts[0] == group_name:
                    remaining_parts = path_parts[1:]
                elif path_parts:
                    remaining_parts = path_parts
                else:
                    remaining_parts = []

                # 在模块节点下按层级嵌套查找或创建中间功能节点
                current_children = module_node["children"]
                for part in remaining_parts:
                    if not part:
                        continue
                    found = None
                    for child in current_children:
                        if (
                            child.get("name") == part
                            and child.get("type") == "function"
                        ):
                            found = child
                            break
                    if not found:
                        found = {
                            "name": part,
                            "type": "function",
                            "url": "",
                            "path": "",
                            "children": [],
                        }
                        current_children.append(found)
                    current_children = found["children"]

                # 创建最终功能节点（叶子页面）
                func_node = {
                    "name": item_text,
                    "type": "function",
                    "url": item_url,
                    "path": item_path,
                    "children": [],
                }

                # 关联页面功能控件
                page_features = page_features_map.get(item_path, {})
                if page_features:
                    features = page_features.get("features", [])
                    for feat in features:
                        ctrl_type = feat.get("type", "other")
                        ctrl_name = feat.get("name", "") or feat.get("description", "")
                        func_node["children"].append(
                            {
                                "name": ctrl_name,
                                "type": "control",
                                "control_type": ctrl_type,
                                "description": feat.get("description", ""),
                            }
                        )

                current_children.append(func_node)

            root["children"].append(module_node)

    elif flat_list:
        # 无分组信息，使用 flat_list 构建
        modules_map = defaultdict(list)
        for item in flat_list:
            path = item.get("path", "")
            parts = path.split("/")
            module_name = parts[1] if len(parts) > 1 else "其他"
            modules_map[module_name].append(item)

        for module_name, items in modules_map.items():
            module_node = {
                "name": module_name,
                "type": "module",
                "color": _get_module_color(module_name),
                "children": [],
            }

            for item in items:
                func_node = {
                    "name": item.get("text", ""),
                    "type": "function",
                    "url": item.get("url", ""),
                    "path": item.get("path", ""),
                    "children": [],
                }

                page_features = page_features_map.get(item.get("path", ""), {})
                if page_features:
                    features = page_features.get("features", [])
                    for feat in features:
                        ctrl_type = feat.get("type", "other")
                        ctrl_name = feat.get("name", "") or feat.get("description", "")
                        func_node["children"].append(
                            {
                                "name": ctrl_name,
                                "type": "control",
                                "control_type": ctrl_type,
                            }
                        )

                module_node["children"].append(func_node)

            root["children"].append(module_node)

    else:
        # ★ 无导航地图（DOM 未爬取）：从 page_features_map 直接构建
        # Figma/需求等功能来源没有菜单结构，按 page_path 推断模块层级
        modules_map = defaultdict(list)
        for page_path, page_data in page_features_map.items():
            if not page_data:
                continue
            parts = page_path.strip("/").split("/")
            module_name = parts[0] if parts[0] else "未分类"
            modules_map[module_name].append((page_path, page_data))

        for module_name, pages in modules_map.items():
            module_node = {
                "name": module_name,
                "type": "module",
                "color": _get_module_color(module_name),
                "children": [],
            }

            for page_path, page_data in pages:
                func_node = {
                    "name": page_data.get("page_title", "")
                    or page_path.rsplit("/", 1)[-1]
                    or "未命名",
                    "type": "function",
                    "url": page_data.get("page_url", ""),
                    "path": page_path,
                    "children": [],
                }

                features = page_data.get("features", [])
                # 控件按类型分组（不逐个平铺，避免全是控件属性）
                ctrl_groups = defaultdict(list)
                for feat in features:
                    ctrl_type = feat.get("type", "other")
                    ctrl_groups[ctrl_type].append(feat)

                for ctrl_type, items in ctrl_groups.items():
                    # 单控件用名称，多控件用类型+数量汇总
                    if len(items) == 1:
                        ctrl_name = (
                            items[0].get("name", "")
                            or items[0].get("description", "")
                            or ctrl_type
                        )
                    else:
                        ctrl_name = f"{ctrl_type} ×{len(items)}"
                    func_node["children"].append(
                        {
                            "name": ctrl_name,
                            "type": "control",
                            "control_type": ctrl_type,
                            "description": f"共 {len(items)} 个{ctrl_type}控件",
                        }
                    )

                module_node["children"].append(func_node)

            root["children"].append(module_node)

    # 添加登录页控件（从 page_features_map）
    login_features = page_features_map.get("/login", {})
    if login_features:
        for feat in login_features.get("features", []):
            ctrl_type = feat.get("type", "other")
            ctrl_name = feat.get("name", "") or feat.get("description", "")
            root["children"].append(
                {
                    "name": ctrl_name,
                    "type": "control",
                    "control_type": ctrl_type,
                }
            )

    return root


def _resolve_version_id(conn, project: str, version_label: str) -> int:
    """version_label → version_id（requirements 表按 version_id 隔离，无 version_label 列）。"""
    if not project or not version_label:
        return 0
    with guard("_resolve_version_id: row = conn.execute( 'SELECT id FROM ", log=_log):
        row = conn.execute(
            "SELECT id FROM project_versions "
            "WHERE project=%s AND version_label=%s ORDER BY id DESC LIMIT 1",
            (project, version_label),
        ).fetchone()
        if row and row.get("id"):
            return int(row["id"])
    return 0


def _build_tree_from_requirements(
    conn, project: str, version_label: str
) -> dict[str, Any]:
    """★ 需求兜底构建导航树：『有需求必有导航树』。

    当 Step4 未执行（nav_graph / system_feature_list 均为空）时，
    从 requirements 按 module 分组构建导航树，保证任何含需求的项目都有
    导航树可看、可导出。前两类节点（登录 / 模块）与主路径一致；
    功能节点为需求的 operation/description，控件节点以需求编号标注。
    """
    root = {
        "name": "登录",
        "type": "root",
        "url": "",
        "path": "/login",
        "children": [],
        "color": "FFFFFF",
    }

    version_id = _resolve_version_id(conn, project, version_label)
    if not project or not version_id:
        return root

    try:
        rows = (
            conn.execute(
                "SELECT req_id, module, description, operation "
                "FROM requirements "
                "WHERE project = %s AND version_id = %s "
                "ORDER BY module, req_id",
                (project, version_id),
            ).fetchall()
            or []
        )
    except Exception as _e:
        _log.warning("[nav_tree] 需求兜底读取失败: %s", _e)
        return root

    modules_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        module = normalize_module_name(row.get("module") or "") or "未分类"
        modules_map[module].append(dict(row))

    for module_name, reqs in modules_map.items():
        module_node: dict[str, Any] = {
            "name": module_name,
            "type": "module",
            "color": _get_module_color(module_name),
            "children": [],
        }
        for r in reqs:
            req_id = (r.get("req_id") or "").strip()
            op = (r.get("operation") or "").strip()
            desc = (r.get("description") or "").strip()
            title = op or desc or req_id or "未命名需求"
            if len(title) > 30:
                title = title[:30] + "…"
            func_node: dict[str, Any] = {
                "name": title,
                "type": "function",
                "url": "",
                "path": "",
                "req_id": req_id,
                "description": desc[:120],
                "children": [],
            }
            if req_id:
                func_node["children"].append(
                    {
                        "name": f"REQ-{req_id}",
                        "type": "control",
                        "control_type": "requirement",
                        "description": desc[:80],
                    }
                )
            module_node["children"].append(func_node)
        # 只有真正有需求的模块才纳入树，避免空模块
        if module_node["children"]:
            root["children"].append(module_node)

    return root


def _extract_modules_from_tree(tree: dict) -> list[str]:
    """从树中提取所有模块名称。"""
    modules = []
    for child in tree.get("children", []):
        if child.get("type") == "module":
            modules.append(child.get("name", ""))
    return modules


def _build_module_dependencies(
    conn,
    project: str,
    version_label: str,
    modules: list[str],
) -> list[dict]:
    """
    构建模块依赖关系。

    来源：
    1. requirements.dependency_req_id → 模块间依赖
    2. API 调用关系推断（暂未实现，可后续扩展）

    Returns:
        [{"from_module": str, "to_module": str, "type": str, "description": str}, ...]
    """
    dependencies = []

    # requirements 表按 version_id 隔离（无 version_label 列）
    version_id = _resolve_version_id(conn, project, version_label)
    if not project or not version_id:
        return dependencies

    with guard("_build_module_dependencies: all_rows = ( conn.execute( 'SELECT r", log=_log):
        # 一次取出版本内全部需求，构建 req_id → 需求信息 索引（供依赖对象两端标注）
        all_rows = (
            conn.execute(
                "SELECT req_id, module, description, operation, dependency_req_id "
                "FROM requirements "
                "WHERE project = %s AND version_id = %s",
                (project, version_id),
            ).fetchall()
            or []
        )
        info_by_id: dict[str, dict] = {}
        for row in all_rows:
            rid = str(row.get("req_id") or "").strip()
            if not rid:
                continue
            info_by_id[rid] = {
                "module": normalize_module_name(row.get("module") or "") or "未分类",
                "description": (row.get("description") or row.get("operation") or "").strip(),
            }

        def _obj(rid: str) -> dict:
            info = info_by_id.get(rid, {})
            return {
                "type": "function",
                "req_id": rid,
                "module": info.get("module", ""),
                "name": info.get("description", "")[:40],
            }

        # 逐条带依赖的需求：产出「功能级」依赖，并可聚合出「模块级」依赖
        module_deps: dict[tuple[str, str], list[str]] = {}
        for row in all_rows:
            req_id = str(row.get("req_id") or "").strip()
            dep_req_id = str(row.get("dependency_req_id") or "").strip()
            if not req_id or not dep_req_id or dep_req_id not in info_by_id:
                continue
            from_obj = _obj(req_id)
            to_obj = _obj(dep_req_id)
            if from_obj["module"] and to_obj["module"] and from_obj["module"] == to_obj["module"]:
                # 同模块依赖 → 仅作为功能级依赖保留（模块级聚合只看跨模块）
                dependencies.append(
                    {
                        "level": "function",
                        "from": from_obj,
                        "to": to_obj,
                        "type": "requirement",
                        "description": f"{from_obj['req_id']} 依赖 {to_obj['req_id']} (同模块)",
                    }
                )
                continue
            # 跨模块 → 功能级 + 模块级聚合各一份
            dependencies.append(
                {
                    "level": "function",
                    "from": from_obj,
                    "to": to_obj,
                    "type": "requirement",
                    "description": f"{from_obj['req_id']}({from_obj['module']}) 依赖 {to_obj['req_id']}({to_obj['module']})",
                }
            )
            key = (from_obj["module"], to_obj["module"])
            module_deps.setdefault(key, []).append(f"{req_id} → {dep_req_id}")

        for (f_mod, t_mod), refs in module_deps.items():
            dependencies.append(
                {
                    "level": "module",
                    "from": {"type": "module", "module": f_mod, "name": f_mod},
                    "to": {"type": "module", "module": t_mod, "name": t_mod},
                    "from_module": f_mod,
                    "to_module": t_mod,
                    "type": "requirement",
                    "description": f"{f_mod} 依赖 {t_mod}（{len(refs)} 处：{'; '.join(refs)}）",
                }
            )


    return dependencies


def _compute_tree_statistics(tree: dict) -> dict[str, int]:
    """计算导航树统计信息。"""
    stats = {
        "total_pages": 0,
        "total_modules": 0,
        "total_features": 0,
        "total_controls": 0,
    }

    def _count_nodes(node, depth=0):
        node_type = node.get("type", "")
        if node_type == "module":
            stats["total_modules"] += 1
        elif node_type == "function":
            stats["total_pages"] += 1
            stats["total_features"] += 1
        elif node_type == "control":
            stats["total_controls"] += 1

        for child in node.get("children", []):
            _count_nodes(child, depth + 1)

    _count_nodes(tree)
    return stats


def export_tree_to_excel(
    tree: dict,
    dependencies: list[dict] = None,
    project: str = "",
) -> bytes:
    """
    将导航树导出为 Excel 格式（带模块颜色和依赖标注）。

    输出格式：
    | 层级 | 模块名称 | 功能名称 | 页面路径 | 控件类型 | 控件名称 | 颜色标注 |
    |:---|:---|:---|:---|:---|:---|:---|

    Returns:
        Excel 文件字节流
    """
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "导航功能清单"

    # 样式定义
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4472C4")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # 表头
    headers = [
        "层级",
        "模块名称",
        "功能名称",
        "页面路径",
        "控件类型",
        "控件名称",
        "备注",
    ]
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    # 数据行（递归展平树）
    rows_data = []
    _flatten_tree_to_rows(
        tree, rows_data, level=0, module_name="", module_color="FFFFFF"
    )

    # 写入数据行（带颜色）
    for row_idx, row_data in enumerate(rows_data, 2):
        level = row_data.get("level", 0)
        module = row_data.get("module", "")
        func = row_data.get("function", "")
        path = row_data.get("path", "")
        ctrl_type = row_data.get("control_type", "")
        ctrl_name = row_data.get("control_name", "")
        note = row_data.get("note", "")
        color = row_data.get("color", "FFFFFF")

        ws.cell(row=row_idx, column=1, value=level).border = thin_border
        ws.cell(row=row_idx, column=2, value=module).border = thin_border
        ws.cell(row=row_idx, column=3, value=func).border = thin_border
        ws.cell(row=row_idx, column=4, value=path).border = thin_border
        ws.cell(row=row_idx, column=5, value=ctrl_type).border = thin_border
        ws.cell(row=row_idx, column=6, value=ctrl_name).border = thin_border
        ws.cell(row=row_idx, column=7, value=note).border = thin_border

        # 模块列颜色填充
        if color and module:
            fill = PatternFill("solid", fgColor=color)
            ws.cell(row=row_idx, column=2).fill = fill

    # 添加依赖关系 Sheet
    if dependencies:
        ws2 = wb.create_sheet("模块依赖关系")
        dep_headers = ["依赖方模块", "被依赖模块", "依赖类型", "描述"]
        for col_idx, h in enumerate(dep_headers, 1):
            cell = ws2.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = thin_border

        for row_idx, dep in enumerate(dependencies, 2):
            ws2.cell(row=row_idx, column=1, value=dep.get("from_module", "")).border = (
                thin_border
            )
            ws2.cell(row=row_idx, column=2, value=dep.get("to_module", "")).border = (
                thin_border
            )
            ws2.cell(row=row_idx, column=3, value=dep.get("type", "")).border = (
                thin_border
            )
            ws2.cell(row=row_idx, column=4, value=dep.get("description", "")).border = (
                thin_border
            )

            # 被依赖模块颜色填充
            to_module = dep.get("to_module", "")
            to_color = _get_module_color(to_module)
            ws2.cell(row=row_idx, column=2).fill = PatternFill(
                "solid", fgColor=to_color
            )

    # 设置列宽
    col_widths = [8, 20, 20, 30, 15, 25, 40]
    for col_idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # 自动筛选
    ws.auto_filter.ref = ws.dimensions

    # 写入 BytesIO
    bio = io.BytesIO()
    wb.save(bio)
    wb.close()
    bio.seek(0)

    return bio.getvalue()


def _flatten_tree_to_rows(
    node: dict,
    rows: list[dict],
    level: int = 0,
    module_name: str = "",
    module_color: str = "FFFFFF",
    func_name: str = "",
):
    """递归展平树节点为 Excel 行数据。"""
    node_type = node.get("type", "")
    node_name = node.get("name", "")

    if node_type == "root":
        # 根节点（登录）
        rows.append(
            {
                "level": level,
                "module": "",
                "function": node_name,
                "path": node.get("path", "/login"),
                "control_type": "",
                "control_name": "",
                "note": "系统入口",
                "color": "FFFFFF",
            }
        )
        for child in node.get("children", []):
            _flatten_tree_to_rows(child, rows, level, "", "FFFFFF", "")

    elif node_type == "module":
        # 模块节点
        module_color = node.get("color", "FFFFFF")
        rows.append(
            {
                "level": level + 1,
                "module": node_name,
                "function": "",
                "path": "",
                "control_type": "",
                "control_name": "",
                "note": "",
                "color": module_color,
            }
        )
        for child in node.get("children", []):
            _flatten_tree_to_rows(child, rows, level + 1, node_name, module_color, "")

    elif node_type == "function":
        # 功能节点
        rows.append(
            {
                "level": level + 1,
                "module": module_name,
                "function": node_name,
                "path": node.get("path", ""),
                "control_type": "",
                "control_name": "",
                "note": "",
                "color": module_color,
            }
        )
        for child in node.get("children", []):
            _flatten_tree_to_rows(
                child, rows, level + 1, module_name, module_color, node_name
            )

    elif node_type == "control":
        # 控件节点
        rows.append(
            {
                "level": level + 1,
                "module": module_name,
                "function": func_name,
                "path": "",
                "control_type": node.get("control_type", ""),
                "control_name": node_name,
                "note": node.get("description", ""),
                "color": module_color,
            }
        )


def get_navigation_tree_json(project: str, version_label: str) -> dict:
    """
    获取导航树 JSON（供前端预览使用）。

    Returns:
        {
            "success": bool,
            "tree_json": str,  # JSON 字符串（前端可直接渲染）
            "statistics": dict,
            "error": str,
        }
    """
    result = build_navigation_tree(project, version_label)
    if result.get("success"):
        tree_json = json.dumps(result["tree"], ensure_ascii=False, indent=2)
        return {
            "success": True,
            "tree_json": tree_json,
            "statistics": result["statistics"],
            "dependencies": result["dependencies"],
            "modules": result["modules"],
            "error": "",
        }
    return {
        "success": False,
        "tree_json": "",
        "statistics": {},
        "dependencies": [],
        "modules": [],
        "error": result.get("error", ""),
    }
