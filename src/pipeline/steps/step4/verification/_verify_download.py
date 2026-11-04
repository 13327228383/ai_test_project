# -*- coding: utf-8 -*-
"""文件下载结果断言 — download_check。

场景：导出 Excel 后文件应保存且非空；下载附件后文件名应匹配。
缺失后果：下载失败假通过，或下载了空文件。

实现：
  1. 从 engine._last_download_path 取最近一次下载的文件路径。
  2. 检查文件是否存在、大小是否满足阈值。
  3. 可选校验文件名包含 expected_filename。
"""

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


async def verify_download(engine, check: dict[str, Any]) -> dict[str, Any]:
    """文件下载结果断言。

    check 格式：
      {"type": "download_check",
       "expected_filename": "report",                 # 可选，文件名包含匹配
       "expected_min_size": 1}                         # 可选，最小字节数（默认 1）
    """
    try:
        expected_filename = check.get("expected_filename", "")
        expected_min_size = int(check.get("expected_min_size") or 1)

        download_path = getattr(engine, "_last_download_path", "")
        if not download_path:
            return {
                "passed": False,
                "evidence": "[DownloadCheck] 无下载记录（engine._last_download_path 为空）",
            }

        if not Path(download_path).exists():
            return {
                "passed": False,
                "evidence": "[DownloadCheck] 文件不存在: %s" % download_path,
            }

        actual_size = Path(download_path).stat().st_size
        actual_name = Path(download_path).name

        # 大小检查
        size_ok = actual_size >= expected_min_size
        # 文件名检查
        name_ok = True
        if expected_filename:
            name_ok = expected_filename in actual_name

        passed = size_ok and name_ok
        evidence = "[DownloadCheck] %s size=%d (期望≥%d) name_match=%s → %s" % (
            actual_name,
            actual_size,
            expected_min_size,
            (
                "ok"
                if name_ok
                else "FAIL('%s' 不在 '%s')" % (expected_filename, actual_name)
            ),
            "PASS" if passed else "FAIL",
        )

        return {"passed": passed, "evidence": evidence}
    except Exception as e:
        _log.error("[DownloadCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] download_check 崩溃: %s" % str(e)[:80],
        }
