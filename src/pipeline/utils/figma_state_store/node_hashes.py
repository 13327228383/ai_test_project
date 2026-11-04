# -*- coding: utf-8 -*-
"""figma_node_hashes 表读写 — 从 figma_state_store.py 拆出，零逻辑变更。"""

import json
import time
from pathlib import Path
import logging

_log = logging.getLogger(__name__)


class _NodeHashesMixin:
    """save_node_hashes / load_node_hashes / delete_node_hashes_for_version / count + JSON 导入导出。"""

    # ------------------------------------------------------------------
    # figma_node_hashes 表
    # ------------------------------------------------------------------
    def save_node_hashes(self, hashes: dict[str, str], version_label: str = "") -> bool:
        """整体写入哈希字典（REPLACE）。"""
        if not isinstance(hashes, dict):
            return False
        try:
            project = self._project()
            now = time.time()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                rows = [
                    (project, version_label, str(nid), str(hv), now)
                    for nid, hv in hashes.items()
                ]
                if rows:
                    c.executemany(
                        """
                        REPLACE INTO figma_node_hashes(
                            project, version_label, node_id, hash_value, updated_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        """,
                        rows,
                    )
                c.commit()
                self._last_error = ""
                return True
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return False

    def load_node_hashes(self, version_label: str = "") -> dict[str, str]:
        """读取当前项目 / 版本的全部 ``{node_id: hash}``。"""
        try:
            project = self._project()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                rows = c.execute(
                    """
                    SELECT node_id, hash_value
                      FROM figma_node_hashes
                     WHERE project = %s AND version_label = %s
                    """,
                    (project, version_label),
                ).fetchall()
                result: dict[str, str] = {}
                for row in rows:
                    item = (
                        dict(row)
                        if isinstance(row, dict)
                        else {"node_id": row[0], "hash_value": row[1]}
                    )
                    nid = str(item.get("node_id", "") or "")
                    hv = str(item.get("hash_value", "") or "")
                    # ★ 跳过目录指纹行（__dir__ 前缀是目录指纹，非节点哈希）
                    if nid and not nid.startswith("__dir__"):
                        result[nid] = hv
                return result
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return {}

    def delete_node_hashes_for_version(self, version_label: str) -> bool:
        """清理某个版本的节点哈希（回滚 / 重置时使用）。"""
        try:
            project = self._project()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                c.execute(
                    """
                    DELETE FROM figma_node_hashes
                     WHERE project = %s AND version_label = %s
                    """,
                    (project, version_label),
                )
                c.commit()
                return True
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return False

    def count_node_hashes(self) -> int:
        try:
            project = self._project()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                row = c.execute(
                    "SELECT COUNT(*) AS cnt FROM figma_node_hashes WHERE project=%s",
                    (project,),
                ).fetchone()
                if row is None:
                    return 0
                cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
                return int(cnt or 0)
            finally:
                c.close()
        except Exception as _e:
            _log.debug("[node_hashes] count_node_hashes 异常: %s", _e, exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # 目录指纹（★ P1-5 修复：避免无条件全量拉取）
    # ------------------------------------------------------------------
    def save_directory_fingerprint(
        self, file_key: str, fingerprint: str, version_label: str = ""
    ) -> bool:
        """存储目录指纹（页面对 ID+name 的 MD5），用于判断是否需要重拉节点。

        复用 figma_node_hashes 表，node_id 使用特殊前缀 ``__dir__{file_key}``。
        """
        try:
            project = self._project()
            now = time.time()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                c.execute(
                    """
                    REPLACE INTO figma_node_hashes(
                        project, version_label, node_id, hash_value, updated_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (project, version_label, "__dir__" + file_key, fingerprint, now),
                )
                c.commit()
                self._last_error = ""
                return True
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return False

    def load_directory_fingerprint(self, file_key: str, version_label: str = "") -> str:
        """读取目录指纹，不存在则返回空字符串。"""
        try:
            project = self._project()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                row = c.execute(
                    """
                    SELECT hash_value FROM figma_node_hashes
                     WHERE project = %s AND version_label = %s AND node_id = %s
                    """,
                    (project, version_label, "__dir__" + file_key),
                ).fetchone()
                if row is None:
                    return ""
                if isinstance(row, dict):
                    return str(row.get("hash_value", "") or "")
                return str(row[0] or "") if row else ""
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return ""

    # ----- 显式 JSON 导入/导出（node_hashes） -----
    def export_node_hashes_to_json(self, path: str, version_label: str = "") -> bool:
        try:
            hashes = self.load_node_hashes(version_label=version_label)
            data = {"hashes": hashes, "updated_at": time.time()}
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception as e:
            self._last_error = str(e)
            return False

    def import_node_hashes_from_json(self, path: str, version_label: str = "") -> bool:
        try:
            source = Path(path)
            if not source.is_file():
                return False
            data = json.loads(source.read_text(encoding="utf-8"))
            hashes = data.get("hashes", {}) if isinstance(data, dict) else {}
            if not isinstance(hashes, dict):
                return False
            return self.save_node_hashes(hashes, version_label=version_label)
        except Exception as e:
            self._last_error = str(e)
            return False
