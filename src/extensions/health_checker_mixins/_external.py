# -*- coding: utf-8 -*-
"""外部服务健康检查 mixin。"""

import time

import requests
import logging

_log = logging.getLogger(__name__)


class HealthCheckerExternalMixin:
    """提供 Confluence、Figma、YApi、MySQL 健康检查。"""

    def check_confluence(self) -> dict:
        """检查 Confluence 服务器可达性。"""
        t0 = time.time()
        try:
            from src.core.config_loader import config

            base = config.confluence_base_url
            if not base:
                return self._result("confluence", False, 0, "未配置 CONFLUENCE_URL")

            # 尝试访问 REST API 根路径（无需认证即可拿到 200/401/403）
            resp = requests.get(
                f"{base.rstrip('/')}/rest/api/space",
                timeout=self.TIMEOUT,
            )
            latency = (time.time() - t0) * 1000
            if resp.status_code in (200, 401, 403):
                return self._result("confluence", True, latency)
            return self._result(
                "confluence", False, latency, f"HTTP {resp.status_code}"
            )
        except requests.exceptions.Timeout:
            return self._result(
                "confluence", False, (time.time() - t0) * 1000, "请求超时"
            )
        except requests.exceptions.ConnectionError as e:
            return self._result(
                "confluence", False, (time.time() - t0) * 1000, f"连接失败: {e}"
            )
        except Exception as e:
            return self._result(
                "confluence", False, (time.time() - t0) * 1000, str(e)[:200]
            )

    def check_figma(self) -> dict:
        """检查 Figma API 可达性。"""
        t0 = time.time()
        try:
            from src.core.config_loader import config

            token = config.figma_key
            if not token:
                return self._result("figma", False, 0, "未配置 FIGMA_KEY")

            headers = {"X-Figma-Token": token}
            resp = requests.get(
                "https://api.figma.com/v1/me",
                headers=headers,
                timeout=self.TIMEOUT,
            )
            latency = (time.time() - t0) * 1000
            if resp.status_code == 200:
                return self._result("figma", True, latency)
            return self._result("figma", False, latency, f"HTTP {resp.status_code}")
        except requests.exceptions.Timeout:
            return self._result("figma", False, (time.time() - t0) * 1000, "请求超时")
        except requests.exceptions.ConnectionError as e:
            return self._result(
                "figma", False, (time.time() - t0) * 1000, f"连接失败: {e}"
            )
        except Exception as e:
            return self._result("figma", False, (time.time() - t0) * 1000, str(e)[:200])

    def check_yapi(self) -> dict:
        """检查 YApi 服务器可达性。"""
        t0 = time.time()
        try:
            from src.core.config_loader import config

            base = config.api_docs_base_url
            if not base:
                return self._result("yapi", False, 0, "未配置 API_DOCS_URL")

            resp = requests.get(
                f"{base.rstrip('/')}/api/project/get",
                timeout=self.TIMEOUT,
            )
            latency = (time.time() - t0) * 1000
            # 未登录时 200 但 errcode 非 0，或 401，都说明服务可达
            if resp.status_code in (200, 401, 400):
                return self._result("yapi", True, latency)
            return self._result("yapi", False, latency, f"HTTP {resp.status_code}")
        except requests.exceptions.Timeout:
            return self._result("yapi", False, (time.time() - t0) * 1000, "请求超时")
        except requests.exceptions.ConnectionError as e:
            return self._result(
                "yapi", False, (time.time() - t0) * 1000, f"连接失败: {e}"
            )
        except Exception as e:
            return self._result("yapi", False, (time.time() - t0) * 1000, str(e)[:200])

    def check_mysql(self) -> dict:
        """检查 MySQL 连接可用性。"""
        t0 = time.time()
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            conn.ping(reconnect=True)
            latency = (time.time() - t0) * 1000
            return self._result("mysql", True, latency)
        except Exception as e:
            return self._result("mysql", False, (time.time() - t0) * 1000, str(e)[:200])
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)
