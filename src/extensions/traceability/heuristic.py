# -*- coding: utf-8 -*-
"""
追溯链路引擎 — 启发式回退
========================
当 AI 不可用时，使用关键词启发式匹配为需求构建追溯链路。
"""

import logging

from .models import TraceLink

_log = logging.getLogger("traceability_engine")

# ★ 中英文通用业务关键词映射（不限定行业，用于启发式回退阶段）
#   格式: 中文词 → [英文等价词列表]
_CN_EN_KEYWORD_MAP = {
    "用户": ["user", "users", "account"],
    "登录": ["login", "auth", "signin"],
    "注册": ["register", "signup"],
    "密码": ["password", "pwd"],
    "角色": ["role", "roles"],
    "权限": ["permission", "permissions", "role", "acl"],
    "项目": ["project", "projects"],
    "成员": ["member", "members"],
    "任务": ["task", "tasks", "job"],
    "订单": ["order", "orders"],
    "商品": ["product", "products", "item", "goods"],
    "配置": ["config", "setting", "settings", "configuration"],
    "日志": ["log", "logs", "audit"],
    "文件": ["file", "files", "document", "attachment"],
    "消息": ["message", "notification", "msg"],
    "评论": ["comment", "review"],
    "标签": ["tag", "label", "category"],
    "搜索": ["search", "query", "filter"],
    "导出": ["export", "download"],
    "导入": ["import", "upload"],
    "删除": ["delete", "remove"],
    "编辑": ["edit", "update", "modify"],
    "创建": ["create", "add", "new"],
    "列表": ["list", "index", "table"],
    "详情": ["detail", "view", "info"],
    "统计": ["stats", "report", "dashboard", "analytics"],
    "审批": ["approval", "review", "workflow"],
    "通知": ["notify", "notification", "alert"],
    "支付": ["pay", "payment", "billing"],
    "管理": ["manage", "admin", "management"],
    "组织": ["org", "organization", "department"],
    "团队": ["team", "group"],
    "系统": ["system", "platform"],
}


class HeuristicBuilder:
    """启发式链路构建器。"""

    # 启发式匹配最小关键词长度（低于此长度的子串不参与匹配，防止误报）
    HEURISTIC_MIN_KEYWORD_LEN = 4

    def build_links(
        self,
        requirements: list[dict],
        table_list: list[dict],
        page_list: list[dict],
        api_list: list[dict] = None,
    ) -> dict[str, TraceLink]:
        """当 AI 不可用时，使用关键词启发式匹配。"""
        links = {}
        for req in requirements:
            links[req.get("req_id", "")] = self._heuristic_single_link(
                req, table_list, page_list, api_list or []
            )
        return links

    @staticmethod
    def _to_en_keywords(chinese_text: str) -> set:
        """将中文文本转换为英文关键词集合（用于匹配英文表名/API路径）。"""
        en_keywords = set()
        text = chinese_text.lower()
        for cn_word, en_words in _CN_EN_KEYWORD_MAP.items():
            if cn_word in text:
                en_keywords.update(en_words)
        return en_keywords

    def _heuristic_single_link(
        self,
        req: dict,
        table_list: list[dict],
        page_list: list[dict],
        api_list: list[dict] = None,
    ) -> TraceLink:
        """对单条需求做关键词启发式匹配。支持中英文跨语言匹配。"""
        rid = req.get("req_id", "")
        module = req.get("module", "")
        desc = f"{module} {req.get('description', '')}".lower()
        min_len = self.HEURISTIC_MIN_KEYWORD_LEN

        # ★ 中→英 关键词映射（弥补英文表名/API路径在中文描述中匹配不到的缺陷）
        en_keywords = self._to_en_keywords(desc)

        # ★ 2026-09-15 FIX: 从数据源本身补充关键词
        #   DB 里的需求描述常是操作视角（"点击新建按钮..."），缺业务实体名（"商品"），
        #   导致 en_keywords 里没 "product"。扫描 api_list 路径、table_list 表名、
        #   page_list 页面名，把路径段/表名作为 fallback 关键词。
        min_len = self.HEURISTIC_MIN_KEYWORD_LEN
        for api_item in api_list or []:
            path = api_item.get("path", "") if isinstance(api_item, dict) else ""
            for seg in path.strip("/").split("/"):
                if len(seg) >= min_len and seg != "api":
                    en_keywords.add(seg)
        for t in table_list:
            tname = t.get("name", "") if isinstance(t, dict) else str(t)
            if len(tname) >= min_len:
                en_keywords.add(tname.lower())
        for p in page_list:
            pname = p.get("name", "") if isinstance(p, dict) else str(p)
            for seg in pname.strip("/").split("/"):
                if len(seg) >= min_len and seg != "api":
                    en_keywords.add(seg)

        # 匹配 DB 表
        matched_tables = {}
        for t in table_list:
            tname = t.get("name", "").lower()
            if not tname or len(tname) < min_len:
                continue
            # 1) 直接子串匹配（英文描述场景）
            if tname in desc:
                matched_tables[t.get("name", "")] = t.get("columns", [])[:5]
                continue
            # 2) 中→英 关键词匹配
            if tname in en_keywords:
                matched_tables[t.get("name", "")] = t.get("columns", [])[:5]
                continue
            # 3) 表名关键词拆分匹配（如 project_members → project + members）
            parts = tname.replace("_", " ").split()
            if any(p in en_keywords for p in parts if len(p) >= 2):
                matched_tables[t.get("name", "")] = t.get("columns", [])[:5]

        # 匹配 API 接口（★ 中→英 关键词 + HTTP方法语义）
        matched_apis = []
        for api_item in api_list or []:
            if not isinstance(api_item, dict):
                continue
            api_path = api_item.get("path", "").lower()
            api_method = api_item.get("method", "").lower()
            api_parts = api_path.strip("/").split("/")
            # 路径末段关键词（如 /api/login → login）
            path_keyword = api_parts[-1] if api_parts else ""
            # 直接匹配
            if path_keyword and (path_keyword in desc or path_keyword in en_keywords):
                matched_apis.append(api_item)
                continue
            # HTTP 方法 + 中文语义匹配
            has_create = any(kw in desc for kw in ["创建", "新增", "注册", "添加"])
            has_delete = any(kw in desc for kw in ["删除", "移除", "清空"])
            has_update = any(kw in desc for kw in ["编辑", "修改", "更新", "配置"])
            has_query = any(
                kw in desc for kw in ["查看", "列表", "查询", "搜索", "详情", "统计"]
            )
            if (
                (api_method == "post" and has_create)
                or (api_method == "delete" and has_delete)
                or (api_method in ("put", "patch") and has_update)
                or (api_method == "get" and has_query)
            ):
                if path_keyword in en_keywords:
                    matched_apis.append(api_item)

        # 匹配页面
        matched_pages = []
        for p in page_list:
            pname = p.get("name", "").lower()
            # ★ 2026-09-15 FIX: 给 page 匹配也加中英文关键词映射
            #   否则 scan_target_code 提取的英文路径（/products）匹配不到中文需求描述（"商品新建"）
            page_keywords = set()
            page_keywords.add(pname)
            # 从路径 /products/new 拆出 products, new
            for seg in pname.strip("/").split("/"):
                if len(seg) >= min_len:
                    page_keywords.add(seg)
                    page_keywords.update(seg.split("-"))
            if any(kw and len(kw) >= min_len for kw in page_keywords) and (
                pname in desc
                or any(kw in desc for kw in page_keywords if kw and len(kw) >= min_len)
                or any(kw in en_keywords for kw in page_keywords if kw and len(kw) >= min_len)
            ):
                matched_pages.append(p.get("name", ""))

        return TraceLink(
            req_id=rid,
            module=module,
            description=req.get("description", "")[:100],
            api_endpoints=matched_apis,
            db_tables=matched_tables,
            figma_pages=matched_pages,
            confidence="low",
            feature_id=int(req.get("feature_id") or 0),
        )
