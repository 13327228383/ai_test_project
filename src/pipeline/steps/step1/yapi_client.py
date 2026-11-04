# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Step 1: YApi 接口文档拉取器 (YApiClient)
=========================================
从 YApi 平台拉取接口文档，支持 LDAP 登录。

使用方式：
    from src.pipeline.steps.step1.yapi_client import yapi_client
    result = yapi_client.fetch_all()

返回结构：
    {"success": True,
     "data": {
       # ★ 某SPA系统业务名已统一到 config.demo.sample_project
       "project": {"name": DEMO_PROJECT, "basepath": "/api"},
       "interfaces": [
         {"method": "POST", "path": "/login", "title": "登录接口",
          "req_body": {...}, "res_body": {...}},
         ...],
       "summary": {"total": 201, "methods": {"GET": 108, "POST": 78, ...}}
     }}
"""

import json
import re
import time
from typing import Any
import logging

_log = logging.getLogger(__name__)

import requests

from src.core.config_loader import config
from src.core.pipeline_config import pipeline_config


class YApiClient:
    """YApi REST API 封装（LDAP 登录）。

    ★ 修复（2026-06-22）：之前在 __init__ 把 config.api_docs_* 快照到实例字段，
    导致用户在 UI 重新保存账号/URL 后，模块级单例仍读到旧值。
    现在改成 @property，每次访问都重新读 config。
    """

    def __init__(self) -> None:
        # 仅缓存进程级稳定的依赖（session、timeout）；凭据相关一律走 property。
        self._session = requests.Session()
        self._logged_in = False
        # 标记当前已登录所用的 (base, username) 组合，凭据变更时重置 session
        self._logged_with: tuple = ("", "")
        self._timeout = pipeline_config.yapi_timeout

    # ── 配置属性：每次访问都重新读，不再快照 ──
    @property
    def _base(self) -> str:
        return config.api_docs_base_url

    @property
    def _username(self) -> str:
        return config.api_docs_username

    @property
    def _password(self) -> str:
        return config.api_docs_password

    @property
    def _project_id(self) -> str:
        return config.api_docs_project_id

    @staticmethod
    def _ok(data: Any = None, **extra) -> dict[str, Any]:
        result = {"success": True, "data": data}
        result.update(extra)
        return result

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"success": False, "error": message}

    def _login(self) -> tuple:
        """LDAP 登录 YApi。
        返回: (成功标志, 失败原因码)
        原因码: None=成功, 'not_configured'=URL未配, 'no_credentials'=账号未配,
                'connection_error'=网络不通, 'auth_failed'=密码/认证错误
        """
        # ★ 凭据变更检测：若 (base, username, password) 与上次登录所用的不一致，
        #   说明用户改了配置，旧 session/cookie 已无效，必须重新登录。
        current_id = (self._base, self._username, self._password)
        if self._logged_in and current_id != self._logged_with:
            self._session = requests.Session()
            self._logged_in = False
        if self._logged_in:
            return (True, None)
        if not self._base:
            return (False, "not_configured")
        if not self._username:
            return (False, "no_credentials")
        try:
            r = self._session.post(
                "%s/api/user/login_by_ldap" % self._base,
                json={"email": self._username, "password": self._password},
                timeout=self._timeout,
            )
            if r.status_code == 200:
                d = r.json()
                if d.get("errcode") == 0:
                    self._logged_in = True
                    self._logged_with = current_id
                    return (True, None)
            _log.info("  [YApi] 登录失败: %s", r.text[:100])
            return (False, "auth_failed")
        except requests.exceptions.ConnectionError as e:
            _log.info("  [YApi] 登录连接失败: %s", e)
            return (False, "connection_error")
        except requests.exceptions.Timeout as e:
            _log.info("  [YApi] 登录超时: %s", e)
            return (False, "connection_error")
        except Exception as e:
            _log.info("  [YApi] 登录异常: %s", e)
            return (False, "connection_error")

    def _call(self, path: str, params: dict = None) -> tuple:
        """调用 YApi API。
        返回: (响应数据或None, 失败原因码或None)
        原因码: None=成功, 'not_configured'/'no_credentials'/'auth_failed'/'connection_error'
                'api_error'=API调用返回异常
        """
        login_ok, login_reason = self._login()
        if not login_ok:
            return (None, login_reason)

        # ★ 对网络瞬断/超时增加指数退避重试（最多3次），不重试业务错误
        _max_retries = 3
        _delay_base = 0.5
        for _attempt in range(_max_retries):
            try:
                r = self._session.get(
                    "%s%s" % (self._base, path),
                    params=params or {},
                    timeout=self._timeout,
                )
                if r.status_code == 200:
                    d = r.json()
                    if d.get("errcode") == 0:
                        return (d.get("data"), None)
                # HTTP 200 但业务码非 0 属于业务错误，不重试
                return (None, "api_error")
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                _log.info(
                    "  [YApi] API 调用网络异常 (第%d次/%d): %s",
                    _attempt + 1,
                    _max_retries,
                    e,
                )
                if _attempt < _max_retries - 1:
                    time.sleep(_delay_base * (2**_attempt))
                else:
                    return (None, "connection_error")
            except Exception as e:
                _log.info("  [YApi] API 调用失败: %s", e)
                return (None, "connection_error")

    # ── 失败原因码 → 用户友好提示 ──
    _REASON_MESSAGES = {
        "not_configured": "接口文档地址未配置 (API_DOCS_URL)",
        "no_credentials": "接口文档账号未配置 (API_DOCS_USERNAME / API_DOCS_PASSWORD)",
        "connection_error": "YApi 服务无法连接 — 请检查 API_DOCS_URL 地址和网络",
        "auth_failed": "YApi 登录认证失败 — 请检查接口文档账号密码",
        "api_error": "YApi 接口返回异常 — 请检查项目 ID 是否正确",
    }

    @classmethod
    def reason_message(cls, reason: str, default: str = "无法获取接口列表") -> str:
        """将原因码翻译为用户可读的错误提示。"""
        return cls._REASON_MESSAGES.get(reason, default)

    def _reason_error(self, reason: str) -> dict[str, Any]:
        """将原因码转换为标准错误返回。"""
        return self._error(self.reason_message(reason))

    # ── 连接状态查询（供外部检测，无需实际调用 API） ──
    def check_configured(self) -> dict[str, Any]:
        """快速检查 YApi 配置状态（不发起网络请求）。
        返回: {"configured": bool, "reason": str|None, "message": str}
        """
        if not self._base:
            return {
                "configured": False,
                "reason": "not_configured",
                "message": self.reason_message("not_configured"),
            }
        if not self._username:
            return {
                "configured": False,
                "reason": "no_credentials",
                "message": self.reason_message("no_credentials"),
            }
        return {"configured": True, "reason": None, "message": ""}

    def fetch_project(self) -> dict[str, Any]:
        """获取项目基本信息。"""
        data, reason = self._call("/api/project/get", {"project_id": self._project_id})
        if data is not None:
            return self._ok(data)
        return self._reason_error(reason or "api_error")

    def fetch_interface_list(self) -> dict[str, Any]:
        """获取接口列表（不含详情）。"""
        data, reason = self._call(
            "/api/interface/list", {"project_id": self._project_id, "limit": 500}
        )
        if data is not None:
            items = data.get("list", [])
            total = data.get("total", len(items))
            return self._ok({"total": total, "items": items})
        return self._reason_error(reason or "api_error")

    def fetch_stats(self) -> dict[str, Any]:
        """轻量统计：方法分布、状态分布、分类数量（不拉详情，秒级返回）。"""
        if not config.api_docs_enabled:
            return self._reason_error("not_configured")

        # 项目信息
        proj_data, proj_reason = self._call(
            "/api/project/get", {"project_id": self._project_id}
        )
        proj_name = proj_data.get("name", "?") if proj_data else "?"

        # 接口列表
        list_data, list_reason = self._call(
            "/api/interface/list", {"project_id": self._project_id, "limit": 500}
        )
        if list_data is None:
            return self._reason_error(list_reason or "api_error")

        items = list_data.get("list", [])
        methods = {}
        statuses = {}
        categories = set()
        for item in items:
            m = item.get("method", "?")
            methods[m] = methods.get(m, 0) + 1
            s = item.get("status", "?")
            statuses[s] = statuses.get(s, 0) + 1
            cat = item.get("catid")
            if cat is not None:
                categories.add(cat)

        return self._ok(
            {
                "project_name": proj_name,
                "total": len(items),
                "methods": methods,
                "statuses": statuses,
                "categories": len(categories),
            }
        )

    def fetch_interface_detail(self, interface_id: int) -> dict[str, Any]:
        """获取单个接口的完整详情（含请求参数、返回体 Schema）。"""
        data, reason = self._call("/api/interface/get", {"id": interface_id})
        if data is not None:
            return self._ok(data)
        return self._reason_error(reason or "api_error")

    def fetch_all(self) -> dict[str, Any]:
        """
        拉取全部接口文档并格式化为 AI 可用的结构化描述。

        返回:
            {"success": True,
             "data": {
               "project": {...},
               "interfaces": [...],
               "summary": {"total": N, "methods": {...}, "categories": N},
               "text": "Markdown 格式的接口文档摘要，可直接注入 AI Prompt"
             }}
        """
        if not config.api_docs_enabled:
            return self._reason_error("not_configured")

        # 获取项目信息
        proj_data, _ = self._call("/api/project/get", {"project_id": self._project_id})
        proj_name = proj_data.get("name", "?") if proj_data else "?"
        basepath = proj_data.get("basepath", "") if proj_data else ""

        # 获取接口列表
        list_data, list_reason = self._call(
            "/api/interface/list", {"project_id": self._project_id, "limit": 500}
        )
        if list_data is None:
            return self._reason_error(list_reason or "api_error")

        items = list_data.get("list", [])
        total = max(
            list_data.get("total", 0), len(items)
        )  # ★ API 的 total 字段可能不准，以实际 list 长度兜底
        _log.info("  [YApi] 项目: %s, 共 %d 个接口", proj_name, total)

        # 统计
        methods = {}
        categories = {}
        for item in items:
            m = item.get("method", "?")
            methods[m] = methods.get(m, 0) + 1
            cid = item.get("catid", 0)
            categories[cid] = categories.get(cid, 0) + 1

        # 拉取详情（全量拉取，分批以避免单次请求过多）
        BATCH_SIZE = 50
        detailed = []
        total_items = len(items)
        for batch_start in range(0, total_items, BATCH_SIZE):
            batch = items[batch_start : batch_start + BATCH_SIZE]
            for item in batch:
                iid = item.get("_id")
                if not iid:
                    continue
                detail, _ = self._call("/api/interface/get", {"id": iid})
                if detail:
                    detailed.append(
                        {
                            "_id": iid,
                            "method": detail.get("method", item.get("method", "")),
                            "path": (basepath or "")
                            + detail.get("path", item.get("path", "")),
                            "title": detail.get("title", item.get("title", "")),
                            "catid": detail.get("catid", item.get("catid", 0)),
                            "status": detail.get("status", "undone"),
                            "req_query": detail.get("req_query", []),
                            "req_body": detail.get("req_body_form", [])
                            or detail.get("req_body_other", ""),
                            "res_body": detail.get("res_body", ""),
                            "desc": detail.get("desc", ""),
                        }
                    )
                else:
                    detailed.append(
                        {
                            "_id": item.get("_id"),
                            "method": item.get("method", ""),
                            "path": item.get("path", ""),
                            "title": item.get("title", ""),
                            "catid": item.get("catid", 0),
                            "status": item.get("status", "undone"),
                        }
                    )
            if total_items > BATCH_SIZE:
                _log.info(
                    "  [YApi] 接口详情拉取进度: %d/%d"
                    % (min(batch_start + BATCH_SIZE, total_items), total_items)
                )

        summary = {
            "total": total,
            "detailed": len(detailed),
            "methods": methods,
            "categories": len(categories),
        }

        # 生成 AI 可用的 Markdown 摘要
        text = self._format_for_ai(detailed, proj_name, basepath, summary)

        return self._ok(
            {
                "project": {"name": proj_name, "basepath": basepath},
                "interfaces": detailed,
                "summary": summary,
                "text": text,
            }
        )

    @staticmethod
    def _format_for_ai(
        interfaces: list[dict], proj: str, basepath: str, summary: dict
    ) -> str:
        """将接口文档格式化为紧凑的 Markdown，适合注入 AI Prompt。"""
        lines = [
            "## 系统接口文档（来自 YApi）",
            "",
            "**项目**: %s | **接口总数**: %d | **方法分布**: %s"
            % (
                proj,
                summary["total"],
                ", ".join(
                    "%s:%d" % (k, v)
                    for k, v in sorted(summary.get("methods", {}).items())
                ),
            ),
            "",
            "### 接口列表",
            "",
            "| 方法 | 路径 | 说明 | 参数 |",
            "|------|------|------|------|",
        ]

        for iface in interfaces[:200]:
            method = iface.get("method", "?")
            path = iface.get("path", "?")
            title = iface.get("title", "")[:30]
            # 提取请求参数摘要
            params = []
            for q in (iface.get("req_query") or [])[:5]:
                if isinstance(q, dict):
                    params.append(q.get("name", "?"))
            body = iface.get("req_body", "")
            if isinstance(body, str) and body:
                try:
                    schema = json.loads(body)
                    props = schema.get("properties", {})
                    params.extend(list(props.keys())[:3])
                except Exception as _e:
                    _log.debug("[yapi_client] 非关键异常: %s", _e, exc_info=True)
            elif isinstance(body, list):
                for b in body[:3]:
                    if isinstance(b, dict):
                        params.append(b.get("name", "?"))
            param_str = ", ".join(params[:4]) if params else "—"
            lines.append(
                "| %s | %s | %s | %s |" % (method, path[:50], title, param_str[:40])
            )

        lines.append("")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════
    # ★ P1-3: 响应 Schema 提取（供 Step 4 API 校验使用）
    # ══════════════════════════════════════════════════

    def extract_response_schemas(self) -> dict[str, Any]:
        """
        从缓存的 YApi 数据中提取每个接口的响应 Schema。
        返回: {"/api/brand/list": {"code_field": "code", "required_fields": ["data"], "status_ok": [0]}}
        """
        deserialized = self._load_from_cache()
        if not deserialized:
            return {}
        from src.core.utils.yapi_utils import extract_response_schemas

        return extract_response_schemas(deserialized)


# 单例
# ★ 方案C 改造：yapi_client 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

yapi_client = PerProjectRegistry(YApiClient)
