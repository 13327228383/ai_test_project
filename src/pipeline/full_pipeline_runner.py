# -*- coding: utf-8 -*-
"""完整 Step1→Step5 流水线非交互驱动（人机协同，审核闸门自动放行 green lane）。

★ 由临时脚本 logs/_run_full_pipeline.py 提升为可维护的 src 模块：
  —— 根因归位：原先非交互全流水线驱动逻辑散落在 logs/ 临时脚本，且以
     sys.path hack 方式自我导包，无法被通用入口（main.py --project）干净复用。
  —— 本模块把该驱动固化为 `run_full_pipeline(project, from_step)`，供
     main.py 与任何调用方 import 使用，消除重复实现。

策略（严守引擎质量闸门，不击穿）：
  - Step1 以 skip_sources=['figma'] 绕过外部 Figma MCP 阻塞；
  - Step2 需求审核：全批 approved（需求层无 lane 分流）；
  - Step3 用例审核：仅 green lane 自动放行；yellow/red 保留 pending，
    下一步若仍在同一步重暂停 → 停下来交人审（打印待审清单）。
  - 审核恢复后一律用 from_step=暂停+1 + resume=False（禁用 checkpoint 跳过）。

返回：进程退出码（0=正常完成 / 2=门禁阻断 / 3=存在待人工审核用例）。
"""

import logging
from src.core.guarded import guard
import sys
from typing import Any

from src.pipeline.scheduler import PipelineScheduler

_log = logging.getLogger(__name__)


def _approve_requirements(project: str) -> int:
    """需求全批 approved（等价 review.py /api/review/approve）。"""
    from src.core.excel_handler import excel_handler

    n = 0
    for r in excel_handler.read_requirements():
        if r.get("review_status", "pending") not in ("approved", "modified"):
            rid = r.get("req_id", "")
            if rid and excel_handler.update_review_status(rid, "approved"):
                n += 1
    _log.info("[gate] 需求已批准 %d 条", n)
    return n


def _approve_green_cases(project: str) -> dict:
    """仅 green lane 用例放行；yellow/red 保留 pending。返回 {approved, yellow, red}。"""
    from src.core.versioning import current_version_id
    from src.pipeline.utils.review_lane_store import read_case_lanes
    from src.pipeline.utils.review_store import review_store

    vid = int(current_version_id(project) or 0)
    lanes = read_case_lanes(project, vid) or []
    lane_map = {
        str(r.get("case_id") or "").strip(): r.get("lane", "yellow")
        for r in lanes
        if r.get("case_id")
    }
    try:
        intents = review_store.list_case_meta() or []
    except Exception as e:  # noqa: BLE001 —— 只影响该轮放行判定
        _log.warning("[gate] 读取 case_meta 失败 %s", str(e)[:80])
        intents = []
    approved = yellow = red = 0
    for c in intents:
        cid = str(c.get("case_id") or c.get("id") or "").strip()
        st = str(c.get("_review_status") or c.get("review_status") or "pending")
        if not cid or st in ("approved", "modified"):
            continue
        lane = lane_map.get(cid, "yellow")
        if lane == "green":
            with guard("_approve_green_cases: review_store.update_case_review_stat", log=_log, level=logging.WARNING):
                review_store.update_case_review_status(
                    cid, "approved", reviewer="system", project=project
                )
                approved += 1
        elif lane == "red":
            red += 1
        else:
            yellow += 1
    _log.info("[gate] 用例放行 green=%d yellow=%d red=%d", approved, yellow, red)
    return {"approved": approved, "yellow": yellow, "red": red}


def _clear_gate_state(project: str) -> None:
    from src.backend.state import _set_paused_step
    from src.core.project_purge import purge_specs
    from src.pipeline.utils.pause_controller import pause_controller

    pause_controller.resume()
    _set_paused_step(0, project)
    # 闸门恢复后必须丢弃 checkpoint，否则会按已完成步跳过（清空范围交给统一清空模块）
    purge_specs(project, ["mysql://pipeline_checkpoints"])


def run_full_pipeline(project: str, from_step: int = 1) -> int:
    """非交互执行完整 Step1→Step5 流水线（审核闸门自动放行 green lane）。

    Returns:
        int 退出码：0=正常完成 / 2=前置准入门禁阻断 / 3=存在待人工审核用例。
    """
    s = PipelineScheduler(project_name=project)
    from src.core.versioning import current_run_id

    s.run_id = current_run_id(project)
    _log.info("=== 完整流水线启动 project=%s run_id=%s ===", project, s.run_id)

    last_paused: int | None = None
    max_iter = 20
    it = 0
    cur = from_step
    while it < max_iter:
        it += 1
        _log.info(">>>>>> 运行 Step%s→5 (iter=%d) <<<<<<", cur, it)
        res: dict[str, Any] = s.run(from_step=cur, end_step=5, skip_sources=["figma"])
        if not isinstance(res, dict):
            _log.error("run 返回异常: %r", res)
            return 2
        if res.get("gate_blocked"):
            _log.error("前置准入阻断: %s", res.get("gate_blocked_reason"))
            return 2
        if res.get("paused"):
            paused_at = int(res.get("paused_at_step", cur))
            _log.info(">> 暂停点 Step%s <<", paused_at)
            if paused_at == last_paused:
                _log.warning(
                    "!! 同一暂停点 Step%s 未推进，yellow/red 待人工审核，停止。",
                    paused_at,
                )
                return 3
            if paused_at >= 2:
                _approve_requirements(project)
            if paused_at >= 3:
                r = _approve_green_cases(project)
                if r["yellow"] or r["red"]:
                    _log.warning(
                        "!! 存在 yellow=%d red=%d 待人工审核的用例，再跑一轮看是否仍暂停",
                        r["yellow"],
                        r["red"],
                    )
            _clear_gate_state(project)
            cur = paused_at + 1
            if cur > 5:
                break
            last_paused = paused_at
            continue
        _log.info(
            "== 流水线本轮结束，status=%s steps=%s",
            res.get("status"),
            list((res.get("steps") or {}).keys()),
        )
        break

    _log.info("=== 完整流水线驱动结束 project=%s ===", project)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    project = sys.argv[1] if len(sys.argv) > 1 else ""
    from_step = 1
    if "--from" in sys.argv:
        from_step = int(sys.argv[sys.argv.index("--from") + 1])
    if not project:
        print("用法: python -m src.pipeline.full_pipeline_runner <project> [--from N]")
        sys.exit(1)
    sys.exit(run_full_pipeline(project, from_step))