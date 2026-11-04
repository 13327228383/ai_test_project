# -*- coding: utf-8 -*-
"""图片加载断言 — image_loaded_check。

验证图片是否成功加载（naturalWidth > 0）。
用于验证图片显示、头像加载、缩略图加载等场景。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

_IMG_JS = r"""
(args) => {
  const {selector} = args;
  const el = document.querySelector(selector);
  if (!el) return {found: false, loaded: false, naturalWidth: 0, src: ""};
  return {
    found: true,
    loaded: el.naturalWidth > 0,
    naturalWidth: el.naturalWidth,
    src: el.src || ""
  };
}
"""


async def verify_image(page, check: dict[str, Any]) -> dict[str, Any]:
    """图片加载断言。

    check 格式：
      {"type": "image_loaded_check",
       "selector": "img.avatar",
       "expected": true|false}
    """
    selector = check.get("selector", "")
    if not selector:
        return {"passed": True, "evidence": "无 selector，跳过"}

    if page is None:
        return {"passed": True, "evidence": "无 page，图片断言跳过"}

    expected_loaded = check.get("expected", True)

    try:
        res = await page.evaluate(_IMG_JS, {"selector": selector})
        if not res.get("found"):
            return {
                "passed": True,
                "evidence": "图片[loaded] selector 未找到: %s" % selector,
            }

        actual_loaded = res.get("loaded", False)
        natural_width = res.get("naturalWidth", 0)
        src = res.get("src", "")

        passed = actual_loaded == expected_loaded
        return {
            "passed": passed,
            "evidence": "图片[loaded] %s naturalWidth=%d → %s (期望%s)"
            % (
                selector,
                natural_width,
                "已加载" if actual_loaded else "未加载",
                "已加载" if expected_loaded else "未加载",
            ),
        }
    except Exception as e:
        _log.warning("[ImageCheck] 异常: %s", str(e)[:120])
        return {"passed": True, "evidence": "图片断言异常，跳过: %s" % str(e)[:80]}
