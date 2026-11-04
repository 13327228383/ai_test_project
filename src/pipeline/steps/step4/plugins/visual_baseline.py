# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
视觉基线对比插件
=================
对关键页面截图建立基线，后续执行时对比像素级差异。
防止 UI 回归（布局错位、样式变化、元素缺失等）。

介入点：AFTER_STEP（步骤完成后截图对比）
存储：MySQL / 本地文件系统
"""

import logging
import hashlib
from typing import Any

_log = logging.getLogger(__name__)


# ★ 确保 visual_baselines 存在 version_id 列（存量库运行时 ALTER 兜底，参考 browser_evidence）
_VB_TABLE_ENSURED = False


def ensure_visual_baseline_table():
    """确保 visual_baselines 表存在 version_id 列（幂等，缺失才 ALTER）。"""
    global _VB_TABLE_ENSURED
    if _VB_TABLE_ENSURED:
        return
    with guard("ensure_visual_baseline_table: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            _cols = {
                r["COLUMN_NAME"]
                for r in conn.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'visual_baselines'"
                ).fetchall()
            }
            if "version_id" not in _cols:
                conn.execute(
                    "ALTER TABLE visual_baselines "
                    "ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
                )
                conn.commit()
            _VB_TABLE_ENSURED = True
        finally:
            with guard("ensure_visual_baseline_table: conn.close()", log=_log):
                conn.close()


class VisualBaselinePlugin:
    """视觉基线对比插件。"""

    name = "visual_baseline"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self._threshold = self.config.get(
            "pixel_diff_threshold", 0.01
        )  # 1% 像素差异阈值
        # ★ 忽略区域（mask region）：CSS selector 列表，匹配的元素区域在像素对比时排除
        self._mask_regions: list[str] = self.config.get("mask_regions", []) or []
        # ★ 全 MySQL：基线截图存 visual_baselines 表，不再落磁盘 visual_baselines/*.png

    async def on_after_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        步骤执行后截图并与基线对比。

        ctx 包含:
            step: dict — 步骤定义
            result: bool — 步骤执行结果
            engine: PlaywrightEngine — 浏览器引擎
            case_id: str — 测试用例 ID
            project: str — 项目名
            step_index: int — 步骤序号
        """
        if not self.enabled:
            return None

        engine = ctx.get("engine")
        if engine is None or not hasattr(engine, "_page"):
            return None

        page = engine._page
        if page is None:
            return None

        case_id = ctx.get("case_id", "unknown")
        # ★ 修复：兼容 step_index / step_idx 两种 key（_base.py 现两者都传）
        step_idx = ctx.get("step_index", ctx.get("step_idx", 0))
        project = ctx.get("project") or self._get_project()

        try:
            # 截图（★ async：Playwright Page.screenshot 是协程，必须 await）
            screenshot_bytes = await page.screenshot(full_page=False)
            current_hash = hashlib.md5(screenshot_bytes).hexdigest()

            # ★ 版本并入 baseline_key：保证同项目跨版本基线各自独立
            active_version = self._active_version(project)
            baseline_key = (
                f"v{active_version}_{project}_{case_id}_step{step_idx}"
            )

            # ★ 忽略区域：收集 mask region 的 bounding boxes（在像素对比时排除）
            mask_boxes = await self._collect_mask_boxes(page)

            # 查询基线
            baseline = self._get_baseline(baseline_key)

            if baseline is None:
                # 首次执行 → 建立基线
                self._save_baseline(baseline_key, screenshot_bytes, current_hash)
                _log.info("[VisualBaseline] 建立基线: %s", baseline_key)
                self._record_vb_metric("created")
                return {
                    "visual_baseline_status": "created",
                    "visual_baseline_key": baseline_key,
                }

            # 对比哈希（快速路径）
            if baseline.get("hash") == current_hash:
                _log.debug("[VisualBaseline] ✓ 与基线一致: %s", baseline_key)
                self._record_vb_metric("match")
                return {
                    "visual_baseline_status": "match",
                    "visual_baseline_key": baseline_key,
                }

            # 哈希不同 → 像素级对比
            diff_ratio = self._compare_pixels(
                baseline.get("image_bytes"), screenshot_bytes, mask_boxes
            )
            # ★ B4: 若启用 SSIM，把 ssim 分数附加到 ctx（不改变主返回值）
            try:
                _ssim_val = getattr(self, "_last_ssim", None)
                if _ssim_val is not None and isinstance(ctx, dict):
                    ctx["visual_baseline_ssim"] = float(_ssim_val)
            except Exception as _pass_e:
                _log.warning(
                    "visual_baseline.py:89 异常被吞: %s", _pass_e, exc_info=True
                )
            if diff_ratio <= self._threshold:
                _log.info(
                    "[VisualBaseline] ✓ 差异在阈值内 (%.2f%%): %s",
                    diff_ratio * 100,
                    baseline_key,
                )
                self._record_vb_metric("match_within_threshold", diff_ratio)
                return {
                    "visual_baseline_status": "match_within_threshold",
                    "visual_baseline_diff_ratio": diff_ratio,
                    "visual_baseline_key": baseline_key,
                }
            else:
                _log.warning(
                    "[VisualBaseline] ⚠ 视觉差异超出阈值 (%.2f%% > %.2f%%): %s",
                    diff_ratio * 100,
                    self._threshold * 100,
                    baseline_key,
                )
                # 保存差异截图（入 MySQL visual_baselines.diff_data）
                self._save_diff(
                    baseline_key, baseline.get("image_bytes"), screenshot_bytes
                )
                self._record_vb_metric("mismatch", diff_ratio)
                return {
                    "visual_baseline_status": "mismatch",
                    "visual_baseline_diff_ratio": diff_ratio,
                    "visual_baseline_key": baseline_key,
                }

        except Exception as e:
            _log.warning("[VisualBaseline] 截图对比异常: %s", str(e)[:120])
            return None

    def _record_vb_metric(self, status: str, diff_ratio: float = 0.0):
        """记录视觉基线对比结果到 ai_health_metrics。"""
        with guard("_record_vb_metric: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            run_id = getattr(_thread_run_id, "value", "") or ""
            if run_id:
                is_mismatch = status == "mismatch"
                accepted = 0
                rejected = 0
                if status == "mismatch":
                    rejected = 1
                metrics_collector.record_visual_baseline(
                    project=self._get_project(),
                    run_id=run_id,
                    snapshots_taken=1,
                    diffs_detected=(
                        1 if status in ("mismatch", "match_within_threshold") else 0
                    ),
                    accepted_diffs=accepted,
                    rejected_diffs=rejected,
                )

    def _get_project(self) -> str:
        """获取当前项目名。"""
        try:
            from src.core.config_loader import config

            return config.project_name
        except Exception:
            return self.config.get("project", "default")

    @staticmethod
    def _active_version(project: str) -> int:
        """当前项目 active version_id（0=legacy）。"""
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)

    def _get_baseline(self, key: str) -> dict[str, Any] | None:
        """从 MySQL visual_baselines 表查询基线。"""
        project = self._get_project()
        if not project:
            _log.debug("[VisualBaseline] 无活动项目，跳过基线读取")
            return None
        try:
            from src.core.db_manager import get_connection

            ensure_visual_baseline_table()
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT image_data, image_hash FROM visual_baselines "
                    "WHERE project=%s AND baseline_key=%s AND version_id=%s",
                    (project, key, self._active_version(project)),
                ).fetchone()
            finally:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning(
                        "visual_baseline.py:130 异常被吞: %s", _pass_e, exc_info=True
                    )
            if not row:
                return None
            content = bytes(row["image_data"])
            return {
                "image_bytes": content,
                "hash": row["image_hash"] or hashlib.md5(content).hexdigest(),
            }
        except Exception as e:
            _log.warning("[VisualBaseline] 读取基线失败: %s", str(e)[:120])
            return None

    def _save_baseline(self, key: str, screenshot_bytes: bytes, img_hash: str) -> None:
        """保存基线截图到 MySQL visual_baselines 表。"""
        project = self._get_project()
        if not project:
            _log.debug("[VisualBaseline] 无活动项目，跳过基线保存")
            return
        with guard("_save_baseline: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection

            ensure_visual_baseline_table()
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO visual_baselines (project, baseline_key, version_id, image_data, image_hash) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE image_data=%s, image_hash=%s, updated_at=NOW()",
                    (
                        project,
                        key,
                        self._active_version(project),
                        screenshot_bytes,
                        img_hash,
                        screenshot_bytes,
                        img_hash,
                    ),
                )
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning(
                        "visual_baseline.py:158 异常被吞: %s", _pass_e, exc_info=True
                    )

    async def _collect_mask_boxes(self, page) -> list[tuple[int, int, int, int]]:
        """★ 忽略区域：收集 mask_regions 配置中各 CSS selector 的 bounding box。

        返回 [(x1, y1, x2, y2), ...] 像素坐标列表，用于在 _compare_pixels 中
        将对应区域排除（设为统一颜色，使其不产生差异）。

        通用性：任何系统的动态内容（广告位/时间戳/随机验证码）只需在配置中
        指定 CSS selector 即可排除，无需修改对比逻辑。
        """
        if not self._mask_regions:
            return []
        boxes: list[tuple[int, int, int, int]] = []
        for selector in self._mask_regions:
            with guard("_collect_mask_boxes: locator = page.locator(selector)", log=_log):
                locator = page.locator(selector)
                count = await locator.count()
                for i in range(count):
                    with guard("_collect_mask_boxes: box = await locator.nth(i).bounding_", log=_log):
                        box = await locator.nth(i).bounding_box()
                        if box:
                            boxes.append(
                                (
                                    int(box["x"]),
                                    int(box["y"]),
                                    int(box["x"] + box["width"]),
                                    int(box["y"] + box["height"]),
                                )
                            )
        if boxes:
            _log.debug(
                "[VisualBaseline] 收集到 %d 个 mask region（来自 %d 个 selector）",
                len(boxes),
                len(self._mask_regions),
            )
        return boxes

    def _apply_mask(self, img, mask_boxes: list[tuple[int, int, int, int]]):
        """将 mask_boxes 指定区域填充为纯黑（RGB 0,0,0），使其不参与像素差异。

        使用 PIL ImageDraw 绘制矩形，同时兼容 numpy 路径（调用方在 numpy 化前调用）。
        """
        if not mask_boxes:
            return img
        with guard("_apply_mask: from PIL import ImageDraw", log=_log):
            from PIL import ImageDraw

            draw = ImageDraw.Draw(img)
            for x1, y1, x2, y2 in mask_boxes:
                # 裁剪到图片边界内
                x1 = max(0, min(x1, img.width - 1))
                y1 = max(0, min(y1, img.height - 1))
                x2 = max(0, min(x2, img.width - 1))
                y2 = max(0, min(y2, img.height - 1))
                if x2 > x1 and y2 > y1:
                    draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0))
        return img

    def _compare_pixels(
        self,
        baseline_bytes: bytes,
        current_bytes: bytes,
        mask_boxes: list[tuple[int, int, int, int]] | None = None,
    ) -> float:
        """
        像素级对比。返回差异比例（0.0 ~ 1.0）。

        ★ B4: 优先用 numpy 向量化（O(n) 但常数极小）；不可用时回退到旧的双层循环。
        ★ 全 MySQL：基线从 bytes 读取（Image.open(BytesIO)），不再读磁盘路径。
        """
        try:
            from PIL import Image
            import io

            baseline_img = Image.open(io.BytesIO(baseline_bytes)).convert("RGB")
            current_img = Image.open(io.BytesIO(current_bytes)).convert("RGB")

            # ★ 忽略区域：在对比前将 mask_boxes 指定区域填充为纯黑
            if mask_boxes:
                baseline_img = self._apply_mask(baseline_img, mask_boxes)
                current_img = self._apply_mask(current_img, mask_boxes)

            # 尺寸不同 → 全差异
            if baseline_img.size != current_img.size:
                return 1.0

            width, height = baseline_img.size
            total_pixels = width * height
            if total_pixels <= 0:
                return 0.0

            # ── 优先：numpy 向量化 ──
            with guard("_compare_pixels: import numpy as _np", log=_log):
                import numpy as _np  # type: ignore

                arr_a = _np.asarray(baseline_img)
                arr_b = _np.asarray(current_img)
                if arr_a.shape != arr_b.shape:
                    return 1.0
                diff_pixels = int(_np.any(arr_a != arr_b, axis=-1).sum())
                ratio = diff_pixels / total_pixels if total_pixels > 0 else 0.0
                # ── 可选 SSIM（不影响主返回值，结果挂到 ctx 由调用方处理） ──
                try:
                    from src.core.pipeline_config import pipeline_config as _pc

                    if bool(getattr(_pc, "visual_use_ssim", False)):
                        try:
                            from skimage.metrics import structural_similarity as _ssim  # type: ignore

                            # 转灰度计算 SSIM（单通道更稳定）
                            gray_a = _np.mean(arr_a, axis=-1).astype("float32")
                            gray_b = _np.mean(arr_b, axis=-1).astype("float32")
                            score = float(_ssim(gray_a, gray_b, data_range=255.0))
                            try:
                                self._last_ssim = score  # 暴露给调用方读取
                            except Exception as _pass_e:
                                _log.warning(
                                    "visual_baseline.py:206 异常被吞: %s",
                                    _pass_e,
                                    exc_info=True,
                                )
                        except Exception as _pass_e:
                            _log.warning(
                                "visual_baseline.py:208 异常被吞: %s",
                                _pass_e,
                                exc_info=True,
                            )
                except Exception as _pass_e:
                    _log.warning(
                        "visual_baseline.py:210 异常被吞: %s", _pass_e, exc_info=True
                    )
                return ratio

            # ── Fallback: 旧的双层 Python 循环 ──
            diff_pixels = 0
            baseline_data = baseline_img.load()
            current_data = current_img.load()

            for y in range(height):
                for x in range(width):
                    if baseline_data[x, y] != current_data[x, y]:
                        diff_pixels += 1

            return diff_pixels / total_pixels if total_pixels > 0 else 0.0

        except ImportError:
            _log.warning("[VisualBaseline] PIL 未安装，跳过像素对比")
            return 0.0
        except Exception as e:
            _log.warning("[VisualBaseline] 像素对比异常: %s", str(e)[:120])
            return 0.0

    def _save_diff(self, key: str, baseline_bytes: bytes, current_bytes: bytes) -> None:
        """生成差异高亮图并写入 MySQL visual_baselines.diff_data。"""
        project = self._get_project()
        if not project:
            _log.debug("[VisualBaseline] 无活动项目，跳过差异图保存")
            return
        with guard("_save_diff: from PIL import Image", log=_log, level=logging.WARNING):
            from PIL import Image
            import io

            baseline_img = Image.open(io.BytesIO(baseline_bytes)).convert("RGB")
            current_img = Image.open(io.BytesIO(current_bytes)).convert("RGB")

            if baseline_img.size != current_img.size:
                current_img = current_img.resize(baseline_img.size)

            width, height = baseline_img.size
            diff_img = Image.new("RGB", (width, height), (255, 255, 255))

            baseline_data = baseline_img.load()
            current_data = current_img.load()
            diff_data = diff_img.load()

            for y in range(height):
                for x in range(width):
                    if baseline_data[x, y] != current_data[x, y]:
                        diff_data[x, y] = (255, 0, 0)  # 红色标记差异
                    else:
                        diff_data[x, y] = baseline_data[x, y]

            buf = io.BytesIO()
            diff_img.save(buf, format="PNG")
            diff_bytes = buf.getvalue()

            from src.core.db_manager import get_connection

            ensure_visual_baseline_table()
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE visual_baselines SET diff_data=%s, updated_at=NOW() "
                    "WHERE project=%s AND baseline_key=%s AND version_id=%s",
                    (diff_bytes, project, key, self._active_version(project)),
                )
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning(
                        "visual_baseline.py:278 异常被吞: %s", _pass_e, exc_info=True
                    )
