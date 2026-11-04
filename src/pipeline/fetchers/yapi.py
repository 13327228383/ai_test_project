# -*- coding: utf-8 -*-
"""
Step1DataFetcher YApi 拉取 mixin

职责：
- _fetch_yapi — YApi 接口文档拉取（缓存检查、API 拉取、MySQL 写入）
"""

import hashlib
import logging
import time

from src.core.config_loader import config

_log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 7776000  # 90天
_TRUNC_DETAIL = 80
_TRUNC_STATUS = 100


class _Step1YapiMixin:
    """YApi 拉取能力。"""

    def _fetch_yapi(self, _version_label, figma_force_refresh):
        """拉取 YApi，返回 (yapi_ok, api_docs_context, yapi_summary_text, sources_status_yapi)。

        ★ MySQL 唯一事实源：缓存判新鲜度/读/API失败兜底一律查 source_documents(yapi)，
          不再有磁盘缓存概念。
        """
        api_docs_context = ""
        yapi_ok = False
        yapi_summary_text = ""
        yapi_from_cache = False
        yapi_status = {"status": "skipped", "detail": "未配置 API_DOCS_URL"}

        if not config.api_docs_enabled:
            _log.info("YApi: 未配置，跳过")
            _log.info("  ○ YApi: 未配置，跳过")
            return yapi_ok, api_docs_context, yapi_summary_text, yapi_status

        _project_name = getattr(config, "project_name", "") or ""

        # ★ MySQL 缓存新鲜度判断 + 读取（取代磁盘 raw_yapi_docs_md）
        yapi_cache_fresh = False
        if not figma_force_refresh:
            try:
                from src.core.db_manager import get_connection

                _conn = get_connection()
                try:
                    _row = _conn.execute(
                        "SELECT content, updated_at FROM source_documents "
                        "WHERE project=%s AND doc_type='yapi' AND version_label=%s LIMIT 1",
                        (_project_name, _version_label),
                    ).fetchone()
                finally:
                    try:
                        _conn.close()
                    except Exception as _pass_e:
                        _log.warning("yapi.py:59 异常被吞: %s", _pass_e, exc_info=True)
                if _row and _row["content"] and _row["updated_at"]:
                    _ut = _row["updated_at"]
                    _ts = (
                        _ut.timestamp()
                        if hasattr(_ut, "timestamp")
                        else time.mktime(_ut.timetuple())
                    )
                    if time.time() - _ts < _CACHE_TTL_SECONDS:
                        api_docs_context = _row["content"]
                        if isinstance(api_docs_context, (bytes, bytearray)):
                            api_docs_context = api_docs_context.decode(
                                "utf-8", "ignore"
                            )
                        if api_docs_context.strip():
                            yapi_cache_fresh = True
                            yapi_from_cache = True
                            yapi_ok = True
                            yapi_summary_text = "来自 MySQL 缓存"
                            yapi_status = {
                                "status": "ok",
                                "detail": "缓存 %d 字符" % len(api_docs_context),
                            }
                            _log.info(
                                "YApi: MySQL 缓存新鲜(%.0fmin前)，跳过 API 调用 (%d 字符)",
                                (time.time() - _ts) / 60,
                                len(api_docs_context),
                            )
            except Exception as _e:
                _log.warning("YApi: MySQL 缓存读取失败: %s", _e)

        if not yapi_from_cache:
            _log.info("  ⏳ YApi: 正在拉取接口文档...")
            _t0 = time.time()
            try:
                from src.pipeline.steps.step1.yapi_client import yapi_client

                yapi_result = yapi_client.fetch_all()
                _elapsed = time.time() - _t0
                if yapi_result.get("success"):
                    api_docs_context = yapi_result["data"].get("text", "")
                    if api_docs_context:
                        # ★ S1-003: 内容标准化（5步确定性规则）— 哈希计算前统一文本格式，避免换行符差异误判变更
                        from src.core.utils.text_utils import normalize_content

                        api_docs_context = normalize_content(api_docs_context)
                        self._write_yapi_to_mysql(
                            _version_label, api_docs_context, yapi_result
                        )
                        yapi_ok = True
                        summary = yapi_result["data"].get("summary", {})
                        _interface_count = summary.get("total", 0)
                        # 分组数：从 project.cat 或 interfaces 中按 catid 去重统计
                        _group_count = 0
                        try:
                            _project_data = yapi_result["data"].get("project") or {}
                            _cats = _project_data.get("cat") or []
                            _group_count = len(_cats) if isinstance(_cats, list) else 0
                        except Exception:
                            _group_count = 0
                        yapi_summary_text = "项目: %s, 接口总数: %d" % (
                            (yapi_result["data"].get("project") or {}).get("name", "?"),
                            _interface_count,
                        )
                        yapi_status = {
                            "status": "ok",
                            "detail": "%d 个接口" % _interface_count,
                            "interface_count": _interface_count,
                            "group_count": _group_count,
                            "char_count": len(api_docs_context),
                        }
                        _log.info(
                            "YApi: %d 个接口 (%d 字符, %.1fs)",
                            _interface_count,
                            len(api_docs_context),
                            _elapsed,
                        )
                    else:
                        _log.warning("YApi: API 返回成功但内容为空，将尝试 MySQL 兜底")
                else:
                    err_msg = yapi_result.get("error", "未知")
                    yapi_status = {
                        "status": "failed",
                        "detail": str(err_msg)[:_TRUNC_STATUS],
                    }
                    _log.error("YApi: 拉取失败 — %s (%.1fs)", err_msg, _elapsed)
            except ImportError:
                _log.error(
                    "yapi_client 模块缺失，无法拉取接口文档。请检查部署是否完整。"
                )
                yapi_status = {
                    "status": "failed",
                    "detail": "yapi_client 模块缺失，请检查部署完整性",
                }
            except Exception as e:
                _log.error("YApi 拉取异常: %s", e)
                yapi_status = {"status": "failed", "detail": str(e)[:_TRUNC_STATUS]}

        # ★ 兜底：API 失败时读 MySQL 最新缓存（不限 version，取最近一条）
        if not yapi_ok:
            try:
                from src.core.db_manager import get_connection

                _conn = get_connection()
                try:
                    _row = _conn.execute(
                        "SELECT content FROM source_documents "
                        "WHERE project=%s AND doc_type='yapi' ORDER BY updated_at DESC LIMIT 1",
                        (_project_name,),
                    ).fetchone()
                finally:
                    try:
                        _conn.close()
                    except Exception as _pass_e:
                        _log.warning("yapi.py:122 异常被吞: %s", _pass_e, exc_info=True)
                if _row and _row["content"]:
                    api_docs_context = _row["content"]
                    if isinstance(api_docs_context, (bytes, bytearray)):
                        api_docs_context = api_docs_context.decode("utf-8", "ignore")
                    yapi_ok = True
                    yapi_from_cache = True
                    yapi_status = {
                        "status": "ok",
                        "detail": "MySQL 缓存 %d 字符" % len(api_docs_context),
                    }
                    _log.info(
                        "YApi: API 失败，使用 MySQL 缓存 (%d 字符)",
                        len(api_docs_context),
                    )
            except Exception as _e:
                _log.warning("YApi: MySQL 兜底读取失败: %s", _e)
        if not yapi_ok:
            yapi_status = {"status": "failed", "detail": "API 拉取失败且无缓存"}
            _log.error("YApi 拉取失败且无 MySQL 缓存")

        return yapi_ok, api_docs_context, yapi_summary_text, yapi_status

    def _write_yapi_to_mysql(self, _version_label, api_docs_context, yapi_result):
        from src.core.db_manager import get_connection

        _project_name = getattr(config, "project_name", "") or ""
        new_yapi_hash = hashlib.sha256(api_docs_context.encode()).hexdigest()

        old_yapi_hash = ""
        _conn_yh = None
        try:
            _conn_yh = get_connection()
            _cur_yh = _conn_yh.execute(
                "SELECT content_hash FROM source_documents "
                "WHERE project = %s AND doc_type = 'yapi' AND version_label = %s LIMIT 1",
                (_project_name, _version_label),
            )
            _row_yh = _cur_yh.fetchone() if _cur_yh else None
            if _row_yh and _row_yh.get("content_hash"):
                old_yapi_hash = _row_yh["content_hash"]
        except Exception as _e:
            _log.warning("  [step1] 读取 YApi 历史哈希失败: %s", _e)
        finally:
            if _conn_yh:
                try:
                    _conn_yh.close()
                except Exception as _pass_e:
                    _log.warning("yapi.py:161 异常被吞: %s", _pass_e, exc_info=True)
        if old_yapi_hash == new_yapi_hash:
            _log.info("YApi: 未变更 (哈希=%s)，跳过写入", new_yapi_hash[:8])
            return

        # ★ BUGFIX: 单连接原子写入 source_documents + source_hashes，失败 rollback
        _conn_y = None
        try:
            _conn_y = get_connection()
            # 1. source_documents — Markdown 文本（AI prompt 注入用）
            # ★ 保存 source_url（原始 YApi URL）
            _yapi_url = (config.API_DOCS_URL or "").strip()
            _conn_y.execute(
                "INSERT INTO source_documents (project, doc_type, version_label, content, content_hash, char_count, "
                " source_url, created_at, updated_at) VALUES (%s, 'yapi', %s, %s, %s, %s, %s, NOW(), NOW()) "
                "ON DUPLICATE KEY UPDATE content=VALUES(content), content_hash=VALUES(content_hash), "
                "char_count=VALUES(char_count), source_url=VALUES(source_url), updated_at=NOW()",
                (
                    _project_name,
                    _version_label,
                    api_docs_context,
                    new_yapi_hash,
                    len(api_docs_context),
                    _yapi_url,
                ),
            )
            # 2. source_documents — 结构化 JSON（RAG 索引 / 解析器用，含完整 interfaces 列表）
            try:
                structured = {
                    "project": (yapi_result.get("data", {}) or {}).get("project", {}),
                    "interfaces": (yapi_result.get("data", {}) or {}).get(
                        "interfaces", []
                    ),
                    "summary": (yapi_result.get("data", {}) or {}).get("summary", {}),
                }
                import json as _json

                structured_json = _json.dumps(structured, ensure_ascii=False)
                structured_hash = hashlib.sha256(structured_json.encode()).hexdigest()
                _conn_y.execute(
                    "INSERT INTO source_documents (project, doc_type, version_label, content, content_hash, char_count, "
                    " source_url, created_at, updated_at) VALUES (%s, 'yapi_structured', %s, %s, %s, %s, %s, NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content), content_hash=VALUES(content_hash), "
                    "char_count=VALUES(char_count), source_url=VALUES(source_url), updated_at=NOW()",
                    (
                        _project_name,
                        _version_label,
                        structured_json,
                        structured_hash,
                        len(structured_json),
                        _yapi_url,
                    ),
                )
                _log.info(
                    "  YApi: 结构化数据 %d 接口已写入 (yapi_structured)",
                    len(structured.get("interfaces", [])),
                )
            except Exception as _se:
                _log.warning("  [step1] YApi 结构化数据写入失败（非阻断）: %s", _se)
            # 3. source_hashes 统一表（同一连接，原子性保障）
            _conn_y.execute(
                "INSERT INTO source_hashes (project, doc_type, version_label, content_hash, created_at, updated_at) "
                "VALUES (%s, 'yapi', %s, %s, NOW(), NOW()) "
                "ON DUPLICATE KEY UPDATE content_hash=VALUES(content_hash), updated_at=NOW()",
                (_project_name, _version_label, new_yapi_hash),
            )
            _conn_y.commit()
            _log.info(
                "  YApi: 已原子写入 MySQL source_documents + source_hashes (%d 字符)",
                len(api_docs_context),
            )
        except Exception as _e:
            _log.error("  [step1] YApi 原子写入失败: %s", _e)
            if _conn_y:
                try:
                    _conn_y.rollback()
                except Exception as _pass_e:
                    _log.warning("yapi.py:192 异常被吞: %s", _pass_e, exc_info=True)
        finally:
            if _conn_y:
                try:
                    _conn_y.close()
                except Exception as _pass_e:
                    _log.warning("yapi.py:196 异常被吞: %s", _pass_e, exc_info=True)
        # 写入 source_yapi_hashes 细表（Step2 增量对比使用）
        try:
            from src.pipeline.utils.source_hashes_store import source_hashes_store

            _ok = source_hashes_store.save_yapi_hash(
                overall_hash=new_yapi_hash,
                version_label=_version_label,
            )
            if _ok:
                _log.info("  [step1] YApi 细表哈希已写入")
            else:
                _log.error(
                    "  [step1] YApi source_yapi_hashes 写入失败（详见上游错误日志）"
                )
        except Exception as _e:
            _log.warning("  [step1] YApi source_yapi_hashes 写入失败: %s", _e)

        _log.info(
            "YApi 缓存已持久化: MySQL (doc_type=yapi, %d 字符)", len(api_docs_context)
        )
