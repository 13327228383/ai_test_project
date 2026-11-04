# -*- coding: utf-8 -*-
"""HTTP 拉取（单页 / 多页 / Markdown 输出）— 从 confluence_client.py 拆出，零业务逻辑变更。"""

import requests
from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.core.config_loader import config


class _FetchMixin:
    """fetch_confluence_page / fetch_confluence_raw / fetch_confluence_all。"""

    def _cf_get(self, url: str, params: dict = None):
        """带认证的 Confluence GET 请求。

        ★ 修复（2026-06-23）：Confluence Server 的 PAT（Personal Access Token）
        必须用 ``Authorization: Bearer <token>`` 头，旧式账号密码才用 HTTP Basic Auth。
        原实现仅 basic auth → 用 PAT（文档默认凭据）时永远 401。
        现改为 Bearer 优先（兼容 PAT），401 回退 basic auth（兼容真实密码）。

        ★ 修复（2026-07-08）：PAT 返回 404/403 时也回退 basic auth。
        PAT 污染或过期时 Confluence 返回 404/403 而非 401，导致 basic auth 回退不触发。
        """
        timeout = self._timeout
        user, pwd = self._cf_username, self._cf_password
        # 1) Bearer (PAT) —— 文档默认凭据形式
        bearer_failed = False
        if pwd:
            resp = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"Authorization": "Bearer %s" % pwd},
            )
            if resp.status_code not in (401, 403, 404):
                return resp
            bearer_failed = True
        # 2) 回退 basic auth —— 真实账号密码
        #    PAT 返回 401/403/404 时触发（PAT 污染/过期/非 PAT 格式）
        if user and pwd:
            resp = requests.get(
                url,
                params=params,
                timeout=timeout,
                auth=(user, pwd),
            )
            return resp
        # 无凭据或 Bearer 已失败且无 basic auth 凭据：返回 Bearer 结果
        if bearer_failed:
            return resp
        # 无凭据：匿名访问
        return requests.get(url, params=params, timeout=timeout)

    def fetch_confluence_page(self, page_id: str = None) -> dict[str, Any]:
        """拉取 Confluence 指定页面的完整内容（标题 + HTML 正文）。"""
        target_id = page_id or config.confluence_page_id
        if not target_id:
            return self._error("Confluence page_id 未配置且未传入")

        api_url = "%s/rest/api/content/%s" % (self._cf_base_url.rstrip("/"), target_id)
        params = {"expand": "title,body.storage,version"}

        try:
            resp = self._cf_get(api_url, params)
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("title", "")
                body_html = data.get("body", {}).get("storage", {}).get("value", "")
                version = data.get("version", {}).get("number", 0)
                return self._ok(
                    page_id=target_id,
                    title=title,
                    body_html=body_html,
                    version=version,
                    type=data.get("type", "page"),
                    space_key=data.get("space", {}).get("key", ""),
                )
            elif resp.status_code == 401:
                return self._error("Confluence 认证失败 (401)", 401)
            elif resp.status_code == 404:
                return self._error(
                    "Confluence 页面不存在 (404): page_id=%s" % target_id, 404
                )
            else:
                return self._error(
                    "Confluence 返回非预期状态码 %d: %s"
                    % (resp.status_code, resp.text[:200]),
                    resp.status_code,
                )
        except requests.exceptions.Timeout:
            return self._error("Confluence 请求超时（%ds）" % self._timeout)
        except requests.exceptions.ConnectionError as e:
            return self._error("Confluence 连接失败: %s" % e)
        except Exception as e:
            return self._error("Confluence 未知错误: %s" % e)

    def fetch_confluence_raw(self, page_id: str = None) -> dict[str, Any]:
        """拉取 Confluence 页面的 Markdown 内容（保留表格/图片/链接结构）。

        ★ 2026-07-13: 同时提取图片 URL 列表（供视觉识别模块使用）。
        """
        result = self.fetch_confluence_page(page_id)
        if not result["success"]:
            return result

        html = result["data"].get("body_html", "")
        text = self._html_to_markdown(html)
        result["data"]["text"] = text
        # ★ 提取图片 URL（在 HTML 阶段提取，此时标签尚未被去除）
        result["data"]["image_urls"] = self._extract_image_urls(html)
        return result

    def fetch_descendant_page_ids(self, page_id: str) -> list:
        """★ H3: 拉取指定页面的所有子孙页面 ID+标题（一次性 REST 调用）。

        Confluence REST API: /rest/api/content/{id}/descendant/page
        返回扁平列表（服务端已递归），无需客户端递归。
        """
        if not page_id:
            return []
        api_url = "%s/rest/api/content/%s/descendant/page" % (
            self._cf_base_url.rstrip("/"),
            page_id,
        )
        try:
            resp = self._cf_get(api_url)
            if resp.status_code != 200:
                _log.warning(
                    "[Confluence] 子页面拉取失败 (HTTP %d): page_id=%s",
                    resp.status_code,
                    page_id,
                )
                return []
            data = resp.json()
            results = data.get("results") or []
            return [
                {"page_id": r.get("id", ""), "title": r.get("title", "")}
                for r in results
                if r.get("id")
            ]
        except Exception as e:
            _log.warning("[Confluence] 子页面拉取异常: %s", str(e)[:120])
            return []

    def fetch_confluence_all(self, include_descendants: bool = True) -> dict[str, Any]:
        """
        拉取所有配置的 Confluence 页面，合并为一份需求文档。
        支持 CONFLUENCE_URL 逗号分隔多页。
        ★ H3 (2026-07-10): include_descendants=True 时递归拉取每个页面的子孙页面。
        ★ 2026-07-13: 收集所有页面的图片 URL（供视觉识别模块使用）。
        """
        from src.core.config_loader import config

        page_ids = config.confluence_page_ids
        if not page_ids:
            # 回退单页
            return self.fetch_confluence_raw()

        all_text = []
        all_titles = []
        all_page_ids = list(page_ids)
        all_image_urls = []  # ★ 收集所有页面的图片 URL
        errors = []
        _MAX_DESCENDANT_PAGES = 50  # 防止子孙页面过多导致拉取超时

        for i, pid in enumerate(page_ids):
            result = self.fetch_confluence_raw(page_id=pid)
            if result.get("success"):
                title = result["data"].get("title", "页面%d" % (i + 1))
                text = result["data"].get("text", "")
                all_titles.append(title)
                all_text.append("## %s\n\n%s" % (title, text))
                # ★ 收集图片 URL
                page_images = result["data"].get("image_urls", [])
                if page_images:
                    all_image_urls.extend(page_images)
                _log.info(
                    "  Confluence [%d/%d]: %s (pageId=%s) ✓ (图片 %d 张)"
                    % (i + 1, len(page_ids), title, pid, len(page_images))
                )

                # ★ H3: 递归拉取子孙页面
                if include_descendants:
                    descendants = self.fetch_descendant_page_ids(pid)
                    if descendants:
                        _log.info(
                            "  Confluence 子页面: %s 有 %d 个子孙页面",
                            pid,
                            len(descendants),
                        )
                        for j, d in enumerate(descendants[:_MAX_DESCENDANT_PAGES]):
                            d_result = self.fetch_confluence_raw(page_id=d["page_id"])
                            if d_result.get("success"):
                                d_title = d_result["data"].get(
                                    "title", d.get("title", "")
                                )
                                d_text = d_result["data"].get("text", "")
                                all_titles.append(d_title)
                                all_text.append("## %s\n\n%s" % (d_title, d_text))
                                all_page_ids.append(d["page_id"])
                                # ★ 收集子页面图片 URL
                                d_images = d_result["data"].get("image_urls", [])
                                if d_images:
                                    all_image_urls.extend(d_images)
                            else:
                                errors.append(
                                    "pageId=%s: %s"
                                    % (d["page_id"], d_result.get("error", "未知"))
                                )
                        if len(descendants) > _MAX_DESCENDANT_PAGES:
                            _log.warning(
                                "  Confluence 子页面超过上限 %d，截断",
                                _MAX_DESCENDANT_PAGES,
                            )
            else:
                errors.append("pageId=%s: %s" % (pid, result.get("error", "未知")))
                _log.error(
                    "  Confluence [%d/%d]: pageId=%s ✗ — %s"
                    % (i + 1, len(page_ids), pid, result.get("error", "?"))
                )

        if not all_text:
            return self._error("所有 Confluence 页面拉取失败: %s" % "; ".join(errors))

        merged = "\n\n".join(all_text)
        return self._ok(
            text=merged,
            titles=all_titles,
            page_ids=all_page_ids,
            image_urls=all_image_urls,  # ★ 返回所有图片 URL
            errors=errors if errors else None,
        )
