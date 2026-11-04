# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
SelfHealingVision 插件 — 视觉自愈元素定位
===========================================
解决 P0-2: 当 UI 元素属性变更时，通过视觉特征自动修复定位。

核心能力：
- 视觉特征提取：记录元素的颜色、大小、位置、形状特征
- 相似度匹配：属性变化时通过视觉特征找到最相似的替代元素
- 学习沉淀：成功的修复记录到缓存，下次直接命中

介入点：BEFORE_ACTION
依赖：PIL（ Pillow ）用于图像处理
"""

import logging
import hashlib
import contextvars
from typing import Any

from src.pipeline.steps.step4.plugins.plugin_manager import BasePlugin

_log = logging.getLogger(__name__)

# ★ 存量库运行时 ALTER 补列（幂等，参考 browser_evidence.ensure_screenshot_table 模式）
_VISION_HEAL_VERSION_COLUMN_ENSURED = False


def _active_version_id(project: str = "") -> int:
    """当前项目活跃版本号；无 active 版本/取不到时返回 0（0=跨版本复用 legacy 缓存）。"""
    try:
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)
    except Exception as _e:
        _log.debug("[VisionHeal] _active_version_id 异常: %s", _e, exc_info=True)
        return 0


def _ensure_vision_heal_table():
    """确保 vision_heal_cache 含 version_id 列（幂等；缺失才 ALTER）。失败不阻断（降级容错）。"""
    global _VISION_HEAL_VERSION_COLUMN_ENSURED
    if _VISION_HEAL_VERSION_COLUMN_ENSURED:
        return
    with guard("_ensure_vision_heal_table: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            _rows = conn.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'vision_heal_cache'"
            ).fetchall()
            if "version_id" not in {r["COLUMN_NAME"] for r in (_rows or [])}:
                conn.execute(
                    "ALTER TABLE `vision_heal_cache` ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
                )
            conn.commit()
            _VISION_HEAL_VERSION_COLUMN_ENSURED = True
        finally:
            release_connection(conn)

# ★ contextvars：BEFORE_ACTION → AFTER_ACTION 状态传递，asyncio Task 间隔离
_last_heal_info: contextvars.ContextVar = contextvars.ContextVar(
    "shv_last_heal_info", default=None
)


class SelfHealingVisionPlugin(BasePlugin):
    """视觉自愈元素定位插件。"""

    name = "self_healing_vision"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # ★ 视觉自愈缓存已迁 MySQL vision_heal_cache 表（取代磁盘 vision_healing_cache/）
        self._similarity_threshold = self.config.get("similarity_threshold", 0.7)
        self._max_candidates = self.config.get("max_candidates", 5)

        # 统计
        self._stats = {
            "vision_heal_attempts": 0,
            "vision_heal_success": 0,
            "vision_heal_cache_hits": 0,
            "vision_heal_fallback": 0,
            "vision_heal_verify_success": 0,  # ★ 验证闭环
            "vision_heal_verify_fail": 0,  # ★ 验证闭环
        }
        # ★ contextvars 已提至模块级 _last_heal_info ContextVar

    async def on_before_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        动作执行前：如果元素定位失败，尝试视觉自愈。

        ctx 包含:
            action: dict — 动作定义
            engine: PlaywrightEngine
            element_found: bool — 是否已找到目标元素
            page_state: dict — 当前页面状态
        """
        if not self.enabled:
            return None

        if ctx.get("element_found", True):
            _last_heal_info.set(None)  # ★ 清空
            return None  # 元素已找到，不需要自愈

        action = ctx.get("action", {})
        target_hint = action.get("target_hint", "")
        ai_id = action.get("ai_id", "")
        engine = ctx.get("engine")

        # ★ 通用 CSS 选择器（css=.xxx）不进入视觉自愈（含缓存命中）：
        #    SelfHealing css_class 策略会把 hint 降级为 css=.button 等通用 class，
        #    这种选择器会匹配页面上多个元素，缓存和视觉匹配都会随机选到错误元素
        #    （如 css=.button → text=批量打标 的错误映射）。
        #    在入口统一拦截，不查缓存、不做视觉匹配，避免错误映射污染执行。
        hint_stripped = (target_hint or "").strip()
        if hint_stripped.startswith("css=."):
            _log.info(
                "[VisionHeal] ✗ 跳过通用 CSS 选择器的视觉自愈（含缓存）: %s",
                hint_stripped[:40],
            )
            _last_heal_info.set(None)
            return None

        if engine is None or not hasattr(engine, "_page"):
            return None

        page = engine._page
        if page is None:
            return None

        self._stats["vision_heal_attempts"] += 1

        # 1. 尝试缓存命中
        cached = self._get_cached_heal(ai_id, target_hint)
        if cached:
            self._stats["vision_heal_cache_hits"] += 1
            _log.info(
                "[VisionHeal] ✓ 缓存命中: %s → %s", target_hint, cached.get("new_hint")
            )
            _last_heal_info.set(
                {  # ★ 状态传递
                    "ai_id": ai_id,
                    "original_hint": target_hint,
                    "new_hint": cached.get("new_hint"),
                    "strategy": "vision_cache",
                    "action": action,
                }
            )
            return {
                "self_healed": True,
                "self_heal_strategy": "vision_cache",
                "self_heal_original_hint": target_hint,
                "self_heal_new_hint": cached.get("new_hint"),
                "action": {**action, "target_hint": cached.get("new_hint")},
            }

        # 2. 视觉特征匹配
        with guard("on_before_action: healed = await self._try_visual_matc", log=_log):
            healed = await self._try_visual_match(page, target_hint, ai_id)
            if healed:
                self._stats["vision_heal_success"] += 1
                # 缓存成功的修复
                self._cache_heal(ai_id, target_hint, healed.get("new_hint"))
                _last_heal_info.set(
                    {  # ★ 状态传递
                        "ai_id": ai_id,
                        "original_hint": target_hint,
                        "new_hint": healed.get("new_hint"),
                        "strategy": "vision_match",
                        "action": action,
                    }
                )
                return {
                    "self_healed": True,
                    "self_heal_strategy": "vision_match",
                    "self_heal_original_hint": target_hint,
                    "self_heal_new_hint": healed.get("new_hint"),
                    "action": {**action, "target_hint": healed.get("new_hint")},
                }

        # 3. 降级到文本策略（已有 SelfHealingPlugin 处理）
        self._stats["vision_heal_fallback"] += 1
        _last_heal_info.set(None)  # ★ 清空
        return None

    async def _try_visual_match(
        self, page, target_hint: str, ai_id: str
    ) -> dict[str, Any] | None:
        """尝试通过视觉特征匹配找到替代元素。"""
        try:
            # ★ 通用 CSS 选择器（css=.xxx）不进行视觉匹配：
            #    通用 class 会匹配页面上多个元素，视觉匹配会随机选择"最相似"的，
            #    但这不是用户想要的特定元素，几乎总是错误匹配。
            hint_stripped = (target_hint or "").strip()
            if hint_stripped.startswith("css=."):
                _log.info(
                    "[VisionHeal] ✗ 跳过通用 CSS 选择器的视觉匹配: %s",
                    hint_stripped[:40],
                )
                return None

            # 获取页面所有可见元素的位置和尺寸
            elements = await page.evaluate(
                """
                () => {
                    const all = document.querySelectorAll('*');
                    const visible = [];
                    for (const el of all) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.left >= 0) {
                            const style = window.getComputedStyle(el);
                            visible.push({
                                tag: el.tagName.toLowerCase(),
                                text: el.textContent.trim().substring(0, 50),
                                x: Math.round(rect.left),
                                y: Math.round(rect.top),
                                w: Math.round(rect.width),
                                h: Math.round(rect.height),
                                bg: style.backgroundColor,
                                color: style.color,
                            });
                        }
                    }
                    return visible;
                }
            """
            )

            if not elements:
                return None

            # 从 target_hint 推断元素特征
            inferred_features = self._infer_features_from_hint(target_hint)

            # 计算相似度
            best_match = None
            best_score = 0

            for elem in elements:
                score = self._calculate_similarity(inferred_features, elem)
                if score > best_score and score >= self._similarity_threshold:
                    best_score = score
                    best_match = elem

            if best_match:
                # ★ 通用修复：视觉匹配无文本时，不返回弱 CSS fallback
                #    css={tag} 会导致按钮错误定位到 input 等无关元素
                _best_text = best_match.get("text", "")
                _best_tag = best_match.get("tag", "")
                if not _best_text:
                    # 无文本匹配 → 视觉匹配可信度低，返回 None 让上层继续 fallback
                    _log.info(
                        "[VisionHeal] ✗ 视觉匹配无文本，放弃弱 CSS fallback: tag=%s, score=%.2f",
                        _best_tag,
                        best_score,
                    )
                    return None
                new_hint = f"text={_best_text}"
                _log.info(
                    "[VisionHeal] ✓ 视觉匹配成功: score=%.2f, hint=%s",
                    best_score,
                    new_hint,
                )
                return {
                    "new_hint": new_hint,
                    "score": best_score,
                    "matched_element": best_match,
                }

            return None

        except Exception as e:
            _log.debug("[VisionHeal] 视觉匹配异常: %s", str(e)[:80])
            return None

    def _infer_features_from_hint(self, target_hint: str) -> dict[str, Any]:
        """从 target_hint 推断元素特征。"""
        features = {"text": "", "tag": "", "type_hints": []}

        import re

        # 提取文本
        text_match = re.search(r"text=([\u4e00-\u9fff\w]+)", target_hint)
        if text_match:
            features["text"] = text_match.group(1)

        # 推断标签类型
        hint_lower = target_hint.lower()
        if any(kw in hint_lower for kw in ["button", "btn", "提交", "确认", "登录"]):
            features["tag"] = "button"
            features["type_hints"].append("button")
        elif any(kw in hint_lower for kw in ["input", "text", "填写", "输入"]):
            features["tag"] = "input"
            features["type_hints"].append("input")
        elif any(kw in hint_lower for kw in ["select", "下拉", "选择"]):
            features["tag"] = "select"
            features["type_hints"].append("select")

        return features

    def _calculate_similarity(self, inferred: dict, elem: dict) -> float:
        """计算推断特征与元素特征的相似度（0.0 ~ 1.0）。"""
        scores = []

        # 文本相似度
        if inferred.get("text") and elem.get("text"):
            if inferred["text"] in elem["text"] or elem["text"] in inferred["text"]:
                scores.append(1.0)
            else:
                scores.append(0.3)

        # 标签类型匹配
        if inferred.get("tag") and elem.get("tag"):
            scores.append(1.0 if inferred["tag"] == elem["tag"] else 0.0)

        # 尺寸合理性（按钮通常有固定尺寸范围）
        w, h = elem.get("w", 0), elem.get("h", 0)
        if w > 0 and h > 0:
            if "button" in inferred.get("type_hints", []):
                # 按钮通常宽度 60-300，高度 24-60
                if 60 <= w <= 300 and 24 <= h <= 60:
                    scores.append(0.8)
                else:
                    scores.append(0.3)
            elif "input" in inferred.get("type_hints", []):
                # 输入框通常宽度 100-500，高度 24-50
                if 100 <= w <= 500 and 24 <= h <= 50:
                    scores.append(0.8)
                else:
                    scores.append(0.3)

        if not scores:
            return 0.0

        return sum(scores) / len(scores)

    def _get_cached_heal(self, ai_id: str, target_hint: str) -> dict | None:
        """从 MySQL vision_heal_cache 获取成功的修复记录。"""
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        key = hashlib.md5(f"{project}:{ai_id}:{target_hint}".encode()).hexdigest()
        with guard("_get_cached_heal: from src.core.db_manager import get_", log=_log):
            from src.core.db_manager import get_connection, release_connection

            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT ai_id, original_hint, new_hint, created_at "
                    "FROM vision_heal_cache WHERE cache_key=%s AND project=%s "
                    "AND (version_id=%s OR version_id=0)",
                    (key, project, _active_version_id(project)),
                ).fetchone()
                if row:
                    return {
                        "ai_id": row["ai_id"],
                        "original_hint": row["original_hint"],
                        "new_hint": row["new_hint"],
                        "timestamp": (
                            str(row["created_at"]) if row["created_at"] else ""
                        ),
                    }
            finally:
                release_connection(conn)
        return None

    def _cache_heal(self, ai_id: str, target_hint: str, new_hint: str):
        """缓存成功的修复记录到 MySQL vision_heal_cache。"""
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        key = hashlib.md5(f"{project}:{ai_id}:{target_hint}".encode()).hexdigest()
        with guard("_cache_heal: from src.core.db_manager import get_", log=_log):
            from src.core.db_manager import get_connection, release_connection

            _ensure_vision_heal_table()
            vid = _active_version_id(project)
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO vision_heal_cache (cache_key, project, version_id, ai_id, original_hint, new_hint) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE new_hint=VALUES(new_hint), created_at=NOW()",
                    (
                        key,
                        project,
                        vid,
                        (ai_id or "")[:256],
                        target_hint or "",
                        new_hint or "",
                    ),
                )
                conn.commit()
            finally:
                release_connection(conn)

    def _uncache_heal(self, ai_id: str, target_hint: str):
        """★ 验证失败时删除错误缓存，防止错误映射持续污染后续执行。"""
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        key = hashlib.md5(f"{project}:{ai_id}:{target_hint}".encode()).hexdigest()
        with guard("_uncache_heal: from src.core.db_manager import get_", log=_log):
            from src.core.db_manager import get_connection, release_connection

            conn = get_connection()
            try:
                conn.execute(
                    "DELETE FROM vision_heal_cache WHERE cache_key=%s AND project=%s",
                    (key, project),
                )
                conn.commit()
            finally:
                release_connection(conn)

    def on_after_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        动作执行后验证：验证视觉自愈修复是否真正生效。

        ctx 包含:
            action: dict — 动作定义
            result: bool — 动作执行结果
            before_state: dict — 动作前 DOM 签名
            after_state: dict — 动作后 DOM 签名
            engine: PlaywrightEngine
        """
        heal_info = _last_heal_info.get()
        _last_heal_info.set(None)  # ★ 消耗一次

        if not heal_info:
            return None

        result_ok = ctx.get("result", False)
        if result_ok:
            self._stats["vision_heal_verify_success"] += 1
            _log.info(
                "[VisionHeal] ✓ 视觉自愈验证通过: %s → %s",
                heal_info["original_hint"][:40],
                heal_info["new_hint"][:40],
            )
        else:
            self._stats["vision_heal_verify_fail"] += 1
            _log.warning(
                "[VisionHeal] ✗ 视觉自愈验证失败: %s → %s",
                heal_info["original_hint"][:40],
                heal_info["new_hint"][:40],
            )
            # ★ 验证失败 → 删除错误缓存，防止错误映射持续污染后续执行
            self._uncache_heal(
                heal_info.get("ai_id", ""),
                heal_info.get("original_hint", ""),
            )
            _log.info(
                "[VisionHeal] 已删除验证失败的错误缓存: %s",
                heal_info.get("original_hint", "")[:40],
            )

        return None  # 不干预主流程

    def get_stats(self) -> dict[str, Any]:
        """获取自愈统计。"""
        return self._stats
