# -*- coding: utf-8 -*-
"""「测试方法论清单」端到端验证（Playwright 真实浏览器）
验证：导航 JS 注入 → 卡片渲染 → 点卡跳转筛选 → 横幅/返回闭环。
运行：python -m pytest tests/test_frontend/test_test_methods_e2e.py -v
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PORT = 8700
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "src" / "backend" / "app.py"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

METHOD_CN = {
    "EP": "等价类划分", "BV": "边界值分析", "CE": "因果图", "DT": "判定表",
    "ST": "状态迁移", "OT": "正交实验", "EG": "错误推测", "FD": "场景法",
    "PC": "路径覆盖", "SD": "规范导出", "SX": "XSS注入", "SI": "SQL注入",
    "CS": "CSRF跨站请求伪造", "AU": "越权与权限", "SS": "会话管理",
    "CT": "兼容与响应式", "PF": "性能", "ET": "探索式",
}


def _is_up(port=PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def service():
    proc = None
    if not _is_up():
        proc = subprocess.Popen([sys.executable, "-u", str(BACKEND)],
                                cwd=str(ROOT), creationflags=CREATE_NO_WINDOW)
        for _ in range(60):
            time.sleep(0.5)
            if _is_up():
                break
        else:
            proc.terminate()
            pytest.skip("后端未能启动，跳过 E2E")
    yield
    if proc is not None:
        proc.terminate()


@pytest.fixture
def page(service):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        yield pg
        b.close()


def _login(pg):
    pg.goto(f"http://127.0.0.1:{PORT}/login", wait_until="domcontentloaded")
    pg.wait_for_timeout(300)
    pg.select_option("select.form-select", "admin")
    pg.fill("#loginPassword", "admin123")
    pg.keyboard.press("Enter")
    pg.wait_for_load_state("domcontentloaded")
    pg.wait_for_timeout(800)


def _method_cards(pg):
    """返回卡片列表 [{code,name}]"""
    return pg.evaluate(
        """()=>Array.from(document.querySelectorAll('.tm-card')).map(c=>(
            {code:c.getAttribute('data-code'),
             name:(c.querySelector('.tm-name')||{}).textContent||''}))"""
    )


# ── 1. 导航 JS 注入到指标区 ─────────────────────────────
def test_nav_injection_in_metrics(page):
    """方法论清单 nav 项被 JS 注入到指标区，且当前页置为 active。"""
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/test-methods", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    exists = page.evaluate(
        """()=>{const s=document.querySelector('.sidebar-section[data-section="metrics"]');
           const a=s&&s.querySelector('#nav-test-methods');
           return a?{href:a.getAttribute('href'),active:a.classList.contains('active'),
                    text:(a.innerText||'').trim()}:null}"""
    )
    assert exists, "指标区未注入 #nav-test-methods"
    assert exists["href"] == "/test-methods", f"href={exists['href']}"
    assert exists["active"], "test-methods 页应置 active"


# ── 2. 页面渲染 18 卡片 ─────────────────────────────────
def test_page_renders_18_cards(page):
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/test-methods", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    cards = _method_cards(page)
    assert len(cards) == 18, f"卡片数={len(cards)}，期望 18"
    codes = sorted(c["code"] for c in cards)
    assert codes == sorted(METHOD_CN.keys()), f"编码集={codes}"
    # 每张卡有中文名
    assert all(c["name"] for c in cards), "存在空名卡片"


# ── 3. 点卡 → 跳转筛选 + 横幅 ───────────────────────────
def test_card_click_filters_intent_review(page):
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/test-methods", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    # 点 FD 场景法（有真实用例的编码）
    card = page.locator(".tm-card[data-code='FD']")
    assert card.count() == 1, "FD 卡片不存在"
    card.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2500)
    # URL 带 method=FD
    assert "method=FD" in page.url, f"URL={page.url}"
    # 横幅显示方法名
    banner_text = page.evaluate(
        """()=>{const b=document.getElementById('methodBanner');
           return b&&!b.hidden?b.innerText.trim():null}"""
    )
    assert banner_text and METHOD_CN["FD"] in banner_text, f"横幅={banner_text}"
    # 返回按钮可见
    back_display = page.evaluate(
        """()=>{const b=document.getElementById('btnBackToMethods');
           return b?getComputedStyle(b).display:''}"""
    )
    assert back_display != "none", "返回按钮不可见"


# ── 4. 返回清单页 ───────────────────────────────────────
def test_back_to_methods_flow(page):
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/intent-review?method=FD", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    page.click("a.ir-method-link")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2500)
    assert "/test-methods" in page.url, f"URL={page.url}"
    assert len(_method_cards(page)) == 18, "返回后未渲染 18 卡"


# ── 5. 非法 method 参数不误筛 ───────────────────────────
def test_invalid_method_param_ignored(page):
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/intent-review?method=ZZ", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    banner_hidden = page.evaluate(
        """()=>{const b=document.getElementById('methodBanner'); return !b || b.hidden}"""
    )
    assert banner_hidden, "非法编码不应显示筛选横幅"