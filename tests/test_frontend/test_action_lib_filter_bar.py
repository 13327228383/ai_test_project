# -*- coding: utf-8 -*-
"""指令治理面板（/action-lib）分类筛选条回归（2026-10-28 锁定）。

锁定契约：分类筛选按钮必须**换行铺开**、按内容取宽、不被裁切。
缺陷史：`.al-cat-tabs` 单行 `nowrap` + `overflow-x:auto` + 右缘 mask 渐隐，
实测 1440px 下容器 1144 / 内容 1663 → 后半排按钮（计数/文案越长的越靠右）
被裁在可视区外，用户表现为「筛选按钮被压缩」。
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


def _is_up(port=PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
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


def _open_action_lib(pg, width=1024):
    pg.set_viewport_size({"width": width, "height": 1000})
    pg.goto(f"http://127.0.0.1:{PORT}/login", wait_until="domcontentloaded")
    pg.wait_for_timeout(300)
    pg.select_option("select.form-select", "admin")
    pg.fill("#loginPassword", "admin123")
    pg.keyboard.press("Enter")
    pg.wait_for_load_state("domcontentloaded")
    pg.wait_for_timeout(800)
    pg.goto(f"http://127.0.0.1:{PORT}/action-lib", wait_until="domcontentloaded")
    pg.wait_for_selector(".al-cat-tab", timeout=20000)
    pg.wait_for_timeout(600)


def _bar_metrics(pg):
    return pg.evaluate(
        """()=>{
          const bar=document.querySelector('#catTabs');
          const cs=getComputedStyle(bar);
          const items=Array.from(bar.querySelectorAll('.al-cat-tab'));
          const br=bar.getBoundingClientRect();
          const tops=new Set();
          const clipped=[];
          let narrowest=null;
          items.forEach(function(t){
            const r=t.getBoundingClientRect();
            tops.add(Math.round(r.top));
            if (r.right > br.right + 1 || r.left < br.left - 1 ||
                t.scrollWidth > Math.ceil(r.width) + 1) {
              clipped.push({text:t.textContent.trim().slice(0,20),
                            w:Math.round(r.width), sw:t.scrollWidth});
            }
            if (!narrowest || t.scrollWidth > narrowest.sw) {
              narrowest={text:t.textContent.trim().slice(0,20),
                         w:Math.round(r.width), sw:t.scrollWidth};
            }
          });
          return {flexWrap:cs.flexWrap, rows:tops.size, count:items.length,
                  clipped:clipped, widest:narrowest,
                  barW:Math.round(br.width), barSW:bar.scrollWidth};
        }"""
    )


def test_filter_chips_wrap_and_are_not_clipped(page):
    """窄屏（1024）下 12 个分类按钮必须换行铺开、全部完整可见。"""
    _open_action_lib(page, width=1024)
    m = _bar_metrics(page)
    assert m["count"] >= 3, f"分类按钮过少，无法验证: {m}"
    assert m["flexWrap"] == "wrap", f"筛选条未换行，仍会单行裁切: flexWrap={m['flexWrap']}"
    assert m["rows"] >= 2, f"未发生换行（rows={m['rows']}）: {m}"
    assert not m["clipped"], f"存在被裁切/压扁的筛选按钮: {m['clipped']}"


def test_widest_chip_keeps_intrinsic_width(page):
    """计数/文案最长的按钮宽度必须 >= 内容宽度（不被 flex 收缩）。"""
    _open_action_lib(page, width=1024)
    m = _bar_metrics(page)
    widest = m["widest"]
    assert widest and widest["w"] >= widest["sw"], f"最长按钮被压缩: {widest}"
