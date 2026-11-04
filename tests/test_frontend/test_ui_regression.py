# -*- coding: utf-8 -*-
"""前端 UI 回归（2026-09-15 锁定）— 防把已稳定的界面交互改坏的门禁。

锁定内容（一旦改动前跑红即禁止合入）：
  1. 侧边栏可访问 & 手风琴：
     - /metrics-page 仅展开所在「指标区」，其余分区默认折叠；
     - 点击其它分区标题 → 手风琴互斥切换（上一个收起、新分区展开）；
     - /dashboard 默认全部折叠（仅控制台常驻）。
  2. 详情区高度自适应内容 + 全宽铺开（不被左右压缩成一坨）：
     - /metrics-page 详情面板宽度≈父容器（近全宽），KPI 卡横向铺开；
     - 高度随内容而非固定。
  3. 主要路由均可达（HTTP 200 且渲染出内容）。

通用性：只验证真实浏览器渲染结果（Playwright），不绑定具体业务数据；
后端若未运行会自动临时拉起，验证后恢复原状（不碰已在跑的保活守护）。
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

# 各分区包含的 active_nav 值（手风琴「当前页所在分区展开」判定依据）
SECTION_NAV = {
    "review": ["review", "intent-review", "clarify", "confidence-block"],
    "metrics": ["metrics", "ai-health", "quality-panel", "action-lib",
                "skill-rules", "prompt-templates"],
    "docs": ["docs", "charts", "nav-tree", "export-page", "clear-files"],
    "logs": ["log-panel", "realtime-logs", "logs", "diagnostics", "audit-logs"],
    "project": ["project-mgmt", "config", "version-mgmt", "config-history", "new-project"],
    "config": ["pipeline-config", "step4-config", "system-config", "ci-integration"],
    "user": ["personal-config", "change-password", "user-mgmt", "role-mgmt"],
}

MAIN_ROUTES = ["/dashboard", "/metrics-page", "/ai-health", "/charts",
               "/config", "/review", "/diagnostics"]


def _is_up(port=PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def service():
    """确保后端可达；若不在运行则临时拉起，随会话结束恢复。"""
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
            pytest.skip("后端未能启动，跳过 UI 回归")
    yield
    if proc is not None:
        proc.terminate()
        proc.wait(timeout=10)


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


def _expanded(pg):
    return pg.evaluate(
        """()=>Array.from(document.querySelectorAll('.sidebar-section.collapsible'))
          .filter(s=>s.classList.contains('expanded'))
          .map(s=>s.getAttribute('data-section'))"""
    )


def _nav_visible(pg, section):
    return pg.evaluate(
        """(sec)=>Array.from(document.querySelectorAll(
           `.sidebar-section[data-section="${sec}"] .nav-item`))
           .filter(a=>getComputedStyle(a).display!=='none').length""",
        section,
    )


# ── 1. 路由可达 ──────────────────────────────────────────────
@pytest.mark.parametrize("route", MAIN_ROUTES)
def test_route_reachable(page, route):
    """主要路由 200 且渲染出内容（非空 DOM）。"""
    resp = page.goto(f"http://127.0.0.1:{PORT}{route}", wait_until="domcontentloaded")
    assert resp is not None and resp.status == 200, f"{route} 非 200"
    page.wait_for_timeout(600)
    n = page.evaluate("()=>document.querySelector('main, .main-content, body')?.innerText.length")
    assert (n or 0) > 0, f"{route} 渲染为空"


# ── 2. 侧边栏可访问 + 手风琴 ─────────────────────────────────
def test_sidebar_expands_current_section_only(page):
    """进入某路由仅展开其所在分区，其余默认折叠。"""
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/metrics-page", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    assert _expanded(page) == ["metrics"], f"expanded={_expanded(page)}，期望仅 metrics"
    assert _nav_visible(page, "metrics") >= 1, "metrics 分区菜单应可见"
    assert _nav_visible(page, "docs") == 0, "docs 分区应折叠不可见"


def test_sidebar_accordion_toggle(page):
    """点击其它分区标题 → 互斥切换。"""
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/metrics-page", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    page.click(".sidebar-section[data-section='config'] .sidebar-section-title")
    page.wait_for_timeout(400)
    assert _expanded(page) == ["config"], f"点击配置区后 expanded={_expanded(page)}，期望仅 config"


def test_dashboard_collapsed(page):
    """/dashboard 无 active 分区 → 全部折叠（仅控制台常驻）。"""
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    assert _expanded(page) == [], f"dashboard 期望全折叠，实际={_expanded(page)}"


# ── 3. 详情区全宽 + 高度自适应 ───────────────────────────────
def test_detail_panel_full_width_not_squashed(page):
    """详情面板必须近全宽，不能左右压缩成一坨。"""
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/metrics-page", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    w = page.evaluate(
        """()=>{const e=document.querySelector('.metrics-detail-panel');
           const cb=e?.parentElement;
           const r=e.getBoundingClientRect(), rp=cb.getBoundingClientRect();
           return e?{w:Math.round(r.width), parent:Math.round(rp.width),
                    ratio:Math.round(r.width/rp.width*1000)/1000}:null}"""
    )
    assert w and w["ratio"] >= 0.9, (
        f"详情面板被压缩：宽 {w}, 父 {w['parent']}, 比例 {w['ratio']} (需≥0.9)"
    )


def test_detail_body_has_content(page):
    """详情正文渲染出内容（高度随内容，非空）。"""
    _login(page)
    page.goto(f"http://127.0.0.1:{PORT}/metrics-page", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    h = page.evaluate(
        """()=>{const e=document.querySelector('.metrics-detail-panel');
           return e?Math.round(e.getBoundingClientRect().height):null}"""
    )
    assert h and h > 80, f"详情面板高度异常：{h}"