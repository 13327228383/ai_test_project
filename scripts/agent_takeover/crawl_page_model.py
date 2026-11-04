# -*- coding: utf-8 -*-
"""页面事实采集器（生成侧探索，非运行期）。

职责：登录靶子 → 逐页采集可访问性签名（tag/name/id/testid/placeholder/label/text/role），
产出机器可读的「页面模型」JSON，供 Agent 生成用例时作为**输入事实**（skill §三 输入约定）。

为什么需要：`nav_graph_nodes` 实测仅 2 行 → 生成侧几乎没有页面事实 →
模板只能吐占位符 target_hint（"主输入框"），动作与页面无关 = 用例退化（skill §五.2）。

运行方式：独立进程执行（Playwright teardown 在本机必死锁 → with 块末尾 os._exit）。
"""
from __future__ import annotations

import json
import os
import sys

PROJECT_DIR = r"D:\AI\AI_Test_Preject"
sys.path.insert(0, PROJECT_DIR)

BASE = "http://127.0.0.1:8050"
USER = "admin"
PWD = "admin123"

# 页面清单：URL → 语义名（靶子路由的静态声明，不依赖运行期发现）
PAGES = [
    ("/", "首页"),
    ("/login", "登录页"),
    ("/assets/", "资产列表"),
    ("/assets/new", "新增资产表单"),
    ("/assets/1", "资产详情"),
    ("/assets/1/edit", "编辑资产表单"),
    ("/audit/", "审计日志列表"),
]

JS_COLLECT = r"""
() => {
  const out = [];
  const seen = new Set();
  const sel = 'input,select,textarea,button,a[href],[role=button],[role=link],[role=alert],.flash';
  document.querySelectorAll(sel).forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value || '').trim().slice(0, 60);
    const rec = {
      tag,
      type: el.getAttribute('type') || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      testid: el.getAttribute('data-testid') || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria: el.getAttribute('aria-label') || '',
      role: el.getAttribute('role') || el.getAttribute('tagName') ? (el.getAttribute('role') || '') : '',
      cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 80) : '',
      text: text,
      label: '',
      required: el.hasAttribute('required'),
      options: tag === 'select' ? Array.from(el.options).map(o => o.value).slice(0, 12) : [],
    };
    // 关联 label：包裹式 <label>X<input></label> 或 for=
    const wrap = el.closest('label');
    if (wrap) {
      const t = (wrap.innerText || '').trim();
      rec.label = t.replace(text, '').trim().slice(0, 30);
    } else if (el.id) {
      const l = document.querySelector('label[for="' + el.id + '"]');
      if (l) rec.label = (l.innerText || '').trim().slice(0, 30);
    }
    // 稳定 selector 提示（优先 testid/name/id）
    let hint = '';
    if (rec.testid) hint = "[data-testid='" + rec.testid + "']";
    else if (rec.id) hint = '#' + rec.id;
    else if (rec.name && (tag === 'input' || tag === 'select' || tag === 'textarea')) hint = tag + "[name='" + rec.name + "']";
    rec.sel = hint;
    const key = [tag, rec.type, rec.name, rec.testid, rec.text, hint].join('|');
    if (seen.has(key)) return;
    seen.add(key);
    out.push(rec);
  });
  return {title: document.title, url: location.href, count: out.length, elements: out};
}
"""


def main() -> int:
    out_path = os.path.join(PROJECT_DIR, "temp", "agent_gen_input", "page_model.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    from playwright.sync_api import sync_playwright

    model = {"base_url": BASE, "pages": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        # 登录（靶子 /login 为表单 POST）
        page.goto(BASE + "/login", wait_until="domcontentloaded")
        page.fill("[name='username']", USER)
        page.fill("[name='password']", PWD)
        page.click("[data-testid='btn-login'], button[type='submit']")
        page.wait_for_load_state("domcontentloaded")
        logged_in = "assets" in page.url or "login" not in page.url

        # 详情/编辑页的 id 从列表页真实行链接发现（通用：不硬编码主键）
        real_id = "7"
        try:
            page.goto(BASE + "/assets/", wait_until="domcontentloaded")
            href = page.get_attribute("[data-testid='asset-name']", "href") or ""
            import re as _re

            m = _re.search(r"/assets/(\d+)", href)
            if m:
                real_id = m.group(1)
        except Exception as e:
            print("[crawl] discover detail id failed:", str(e)[:100])
        pages = list(PAGES) + [
            (f"/assets/{real_id}", f"资产详情#{real_id}"),
            (f"/assets/{real_id}/edit", f"编辑资产表单#{real_id}"),
        ]
        print("[crawl] discovered asset id =", real_id)

        for path, name in pages:
            try:
                page.goto(BASE + path, wait_until="domcontentloaded")
                page.wait_for_timeout(300)
                data = page.evaluate(JS_COLLECT)
                data["path"] = path
                data["name"] = name
                model["pages"].append(data)
                print(f"[crawl] {path:22s} {name:14s} elements={data['count']:3d} url={data['url'][:60]}")
            except Exception as e:
                print(f"[crawl] {path} FAILED: {str(e)[:120]}")
                model["pages"].append({"path": path, "name": name, "error": str(e)[:200]})

        model["logged_in"] = bool(logged_in)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=2)
        print("\n saved:", out_path, "logged_in=", logged_in)

    sys.stdout.flush()
    os._exit(0)  # Playwright teardown 在本机必死锁


if __name__ == "__main__":
    raise SystemExit(main())
