# -*- coding: utf-8 -*-
"""单元测试：step4 Agent 执行器「需求权威性 SKIP/FAIL 判决」（2026-08-30 通用性修复）。

背景（用户质疑：需求比实际系统更权威）：
  旧的 v5.3 逻辑只要导航失败就一律 SKIP 并扣"需求文档虚构功能"帽子，掩盖了
  "需求已定义但系统未实现"的真实覆盖缺口。
  修复后改为按需求权威性判决：
    - 有可追溯 req_id 且非隐含（is_implicit=0）→ 需求未实现 → FAIL（_req_unimplemented=True）
    - 无 req_id / 隐含(is_implicit=1) / AI 虚构 → 保留 SKIP

本测试通过 mock 侧边栏导航恒失败 + target_page 无合法回退，
确定性驱动到该判决分支，验证通用性、不依赖任何靶子。
"""
import asyncio

import pytest

from src.pipeline.steps.step4.browser_core.single_mixins import _agent_executor as ae
from src.pipeline.steps.step4.browser_core.single_mixins import _exploratory


# ── 桩：侧边栏导航恒失败 + 空 target_page → 必进入权威性判决分支 ──────────
class _FakePage:
    async def wait_for_load_state(self, state=None, timeout=None):
        pass


class _FakeExplorer:
    pass


def _patch_env(monkeypatch, navigate_result: bool = False):
    """把 _agent_executor 依赖的导航/探索桩化。

    2026-09-22/24 起，"需求未实现"判决新增两道证据基础守卫：
    ① 项目已摄入 nav_graph_nodes（菜单清单）；② module 标签本身是清单内菜单项。
    两守卫都读 MySQL；单测在此桩化为 True，模拟"清单已摄入且 module 确为菜单项
    但导航失败"的场景 —— 这正是"需求未实现"有证据基础的前提。
    """
    async def _fake_navigate_by_module(*a, **kw):
        return navigate_result

    async def _fake_verify(*a, **kw):
        return True

    monkeypatch.setattr(_exploratory, "_navigate_by_module", _fake_navigate_by_module)
    monkeypatch.setattr(_exploratory, "_verify_page_content_loaded", _fake_verify)
    monkeypatch.setattr(ae, "_project_has_nav_inventory", lambda _project: True)
    monkeypatch.setattr(ae, "_module_is_known_nav_target", lambda _project, _module: True)


def _make_intent(req_id: str, is_implicit: bool) -> dict:
    return {
        "_explore_task": {
            "operation": "操作",
            "target_page": "",  # 无合法 URL 回退 → 导航必失败
            "module": "医院诊所管理系统需求规格",  # 无括号模型名 → 走侧边栏
            "req_id": req_id,
            "_req_implicit": is_implicit,
        }
    }


def _make_intent_top_level(req_id: str, is_implicit: bool) -> dict:
    """★ 2026-09-02 回归：真实生产数据形态。

    Step3 生成的 test_cases.payload_json 把 req_id 放在 intent 顶层
    （_explore_task 内只有 module/operation/description/target_page/
    test_method/expected_feedback），与 _make_intent 的形态不同。
    若执行器只读 _explore_task/case_meta 而漏读 intent 顶层 → 权威需求误判 SKIP。
    """
    return {
        "req_id": req_id,
        "is_implicit": is_implicit,
        "module": "医院诊所管理系统需求规格",
        "_explore_task": {
            "operation": "操作",
            "target_page": "",  # 无合法 URL 回退 → 导航必失败
            "module": "医院诊所管理系统需求规格",  # 无括号模型名 → 走侧边栏
        },
    }


class _Cfg:
    target_url = "http://127.0.0.1:5080"
    project_name = "hospital_sut"
    step4_case_timeout_sec = 180


def _run(intent, monkeypatch, explorer_getter):
    _patch_env(monkeypatch)
    monkeypatch.setattr("src.agent.explorer.get_explorer", explorer_getter)
    engine = type("E", (), {"_page": _FakePage()})()
    return asyncio.run(
        ae.handle_agent_intent(
            engine,
            intent,
            {"project": "hospital_sut", "req_id": intent.get("req_id")},
            case_id="TC-REQ-006-I01",
            step_results=[],
            all_passed=True,
            intent_t0=0.0,
        )
    )


def test_authoritative_requirement_unimplemented_is_fail(monkeypatch):
    """权威需求（非隐含）导航失败 → FAIL（需求未实现），不是 SKIP。"""
    _cfg = _Cfg()
    # 注入 _cfg：函数内运行时 `from src.core.config_loader import config as _cfg`
    monkeypatch.setattr("src.core.config_loader.config", _cfg, raising=False)

    r = _run(_make_intent("REQ-006", is_implicit=False), monkeypatch,
             lambda: _FakeExplorer())
    assert r is not None
    assert r["passed"] is False, r
    assert r["_req_unimplemented"] is True
    assert not r.get("_skipped")
    reason = r["error"]
    assert "REQ-006" in reason
    assert "未实现" in reason or "不可达" in reason
    assert r["step_results"][0]["passed"] is False
    assert r["step_results"][0]["_agent_driven"] is False


def test_authoritative_top_level_req_id_is_fail(monkeypatch):
    """★ 2026-09-02 回归：真实生产数据形态（req_id 在 intent 顶层）。

    之前执行器只读 _explore_task/case_meta 的 req_id，而 Step3 生成的 payload
    把 req_id 放在 intent 顶层 → hospital_sut REQ-006/007/008 权威需求被误判
    SKIP（9 条记录）。修复后顶层级 req_id 也能正确判 FAIL。
    """
    import src.core.config_loader
    _cfg = _Cfg()
    monkeypatch.setattr("src.core.config_loader.config", _cfg, raising=False)
    r = _run(_make_intent_top_level("REQ-006", is_implicit=False), monkeypatch,
             lambda: _FakeExplorer())
    assert r is not None
    assert r["passed"] is False, r
    assert r["_req_unimplemented"] is True, r
    assert not r.get("_skipped")
    reason = r["error"]
    assert "REQ-006" in reason
    assert "未实现" in reason or "不可达" in reason


def test_implicit_requirement_falls_back_to_skip(monkeypatch):
    """隐含需求（is_implicit=True）导航失败 → 保留 SKIP（非权威，不误判系统有缺陷）。"""
    import src.core.config_loader
    _cfg = _Cfg()
    monkeypatch.setattr("src.core.config_loader.config", _cfg, raising=False)
    r = _run(_make_intent("REQ-099", is_implicit=True), monkeypatch,
             lambda: _FakeExplorer())
    assert r is not None
    assert r["_skipped"] is True, r
    assert not r.get("_req_unimplemented")
    assert r["passed"] is True  # SKIP 语义为跳过，不计为失败
    assert r["skip_reason"]


def test_no_req_id_falls_back_to_skip(monkeypatch):
    """无 req_id（AI 臆造/无需求背书）导航失败 → SKIP，不产生假 FAIL。"""
    import src.core.config_loader
    _cfg = _Cfg()
    monkeypatch.setattr("src.core.config_loader.config", _cfg, raising=False)
    intent = _make_intent("", is_implicit=False)
    r = _run(intent, monkeypatch, lambda: _FakeExplorer())
    assert r is not None
    assert r["_skipped"] is True, r
    assert not r.get("_req_unimplemented")