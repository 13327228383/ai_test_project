# -*- coding: utf-8 -*-
"""元素动画完成断言 — animation_complete_check。

场景：弹窗淡入完成后才能断言文本；侧边栏滑出后才能断言位置。
缺失后果：读到动画中间态（opacity=0.5、transform 未完成），flaky。

实现：
  1. 通过 getComputedStyle 检查元素是否处于动画/过渡中。
  2. animationName !== 'none' → 检查 animationPlayState === 'paused' 或无活跃动画。
  3. transitionProperty !== 'all' && !== 'none' → 检查无活跃过渡。
  4. 轮询直到稳定或超时。
"""

import asyncio
import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_animation_complete(page, check: dict[str, Any]) -> dict[str, Any]:
    """元素动画完成断言。

    check 格式：
      {"type": "animation_complete_check",
       "selector": ".modal",                          # 元素选择器
       "animation_type": "all",                       # all/animation/transition
       "timeout_ms": 5000}                            # 超时毫秒
    """
    try:
        selector = check.get("selector", "")
        animation_type = (check.get("animation_type") or "all").strip().lower()
        timeout_ms = int(check.get("timeout_ms") or 5000)

        if not selector:
            return {"passed": False, "evidence": "[AnimCheck] 缺少 selector 参数"}
        if animation_type not in ("all", "animation", "transition"):
            return {
                "passed": False,
                "evidence": "[AnimCheck] 未知 animation_type: %s" % animation_type,
            }

        # JS 检查元素是否处于动画/过渡中
        # 返回: {animating: bool, detail: string}
        js_check = """
        (sel, type) => {
            const el = document.querySelector(sel);
            if (!el) return {animating: false, detail: 'element not found'};
            const cs = getComputedStyle(el);
            let animActive = false;
            let transActive = false;
            const detail = [];
            if (type === 'all' || type === 'animation') {
                const name = cs.animationName;
                const dur = cs.animationDuration;
                if (name && name !== 'none' && dur && dur !== '0s') {
                    // 检查 getAnimations() 是否有运行中的动画
                    try {
                        const anims = el.getAnimations ? el.getAnimations() : [];
                        const running = anims.filter(a => a.playState === 'running' && a.animationName !== name || true);
                        // 简化：animationName !== none 即认为可能活跃
                        animActive = anims.some(a => a.playState === 'running');
                        detail.push('animation=' + name + ' running=' + animActive);
                    } catch(e) {
                        animActive = true;
                        detail.push('animation=' + name + ' (assume active)');
                    }
                }
            }
            if (type === 'all' || type === 'transition') {
                const prop = cs.transitionProperty;
                const dur = cs.transitionDuration;
                if (prop && prop !== 'none' && prop !== 'all' && dur && dur !== '0s') {
                    transActive = true;
                    detail.push('transition=' + prop);
                } else if (prop === 'all' && dur && dur !== '0s') {
                    transActive = true;
                    detail.push('transition=all');
                }
            }
            return {animating: animActive || transActive, detail: detail.join('; ')};
        }
        """

        # 轮询检查，直到稳定或超时
        start = asyncio.get_running_loop().time()
        last_detail = ""
        stable_count = 0
        while True:
            try:
                result = await page.evaluate(
                    js_check, {"sel": selector, "type": animation_type}
                )
            except Exception as e:
                return {
                    "passed": False,
                    "evidence": "[AnimCheck] evaluate 失败: %s" % str(e)[:60],
                }

            if not result.get("animating"):
                stable_count += 1
                if stable_count >= 2:  # 连续 2 次稳定才认为完成
                    return {
                        "passed": True,
                        "evidence": "[AnimCheck] %s 动画完成（连续稳定）detail=%s"
                        % (selector, result.get("detail", "")),
                    }
            else:
                stable_count = 0
                last_detail = result.get("detail", "")

            elapsed = (asyncio.get_running_loop().time() - start) * 1000
            if elapsed >= timeout_ms:
                return {
                    "passed": False,
                    "evidence": "[AnimCheck] %s 动画未完成（超时 %dms）detail=%s"
                    % (selector, timeout_ms, last_detail),
                }
            await asyncio.sleep(0.1)
    except Exception as e:
        _log.error("[AnimCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] animation_complete_check 崩溃: %s" % str(e)[:80],
        }
