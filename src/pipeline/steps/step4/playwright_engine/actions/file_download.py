# -*- coding: utf-8 -*-
"""文件下载动作 — download_file_element。

用于导出功能验证：点击触发下载的元素，等待下载完成并保存到指定路径。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import logging
from src.core.guarded import guard
import os
from pathlib import Path

_log = logging.getLogger(__name__)


class FileDownloadMixin:
    """文件下载动作。"""

    async def download_file_element(
        self,
        ai_id: str = "",
        save_dir: str = "",
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """点击触发下载的元素，等待下载完成并保存到 save_dir。

        2 级兜底链：ai_id 选择器 → 语义选择器。
        返回下载文件路径保存在 self._last_download_path。
        """
        if not self._page:
            return False
        if not ai_id and not target_hint:
            return False

        if not save_dir:
            save_dir = str(Path(os.environ.get("TEMP", "/tmp")) / "e2e_downloads")
        Path(save_dir).mkdir(parents=True, exist_ok=True)

        selector = '[data-ai-id="%s"]' % ai_id if ai_id else None

        async def _try_download(locator) -> bool:
            try:
                async with self._page.expect_download(
                    timeout=self._timeout_ms
                ) as dl_info:
                    await locator.click(timeout=5000)
                download = await dl_info.value
                suggested = download.suggested_filename or "download.bin"
                save_path = str(Path(save_dir) / suggested)
                await download.save_as(save_path)
                self._last_download_path = save_path
                _log.debug("[download] 文件已保存: %s", save_path)
                return True
            except Exception as e:
                _log.debug("[download] 下载失败: %s", e)
                return False

        # ── 级别0: ai_id 选择器 ──
        if selector:
            with guard("download_file_element: await self._page.wait_for_selector( ", log=_log):
                await self._page.wait_for_selector(
                    selector, state="attached", timeout=3000
                )
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    if await _try_download(el):
                        return True

        # ── 级别1: 语义选择器 ──
        semantic_sel = self._build_semantic_selector(
            target_hint, fallback_selectors or []
        )
        if semantic_sel:
            with guard("download_file_element: el = self._page.locator(semantic_sel", log=_log):
                el = self._page.locator(semantic_sel).first
                if await el.count() > 0:
                    if await _try_download(el):
                        return True

        return False
