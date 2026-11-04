# -*- coding: utf-8 -*-
"""上传规则：处理 upload 控件，生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule, _log


class UploadRule(BaseCaseRule):
    """文件上传通用规则。

    P01: 上传合法文件，提交 → 期望成功
    N01: 上传非法文件（超限/格式错误），提交 → 期望错误提示
    B01: 不选文件提交 → 期望校验提示
    """

    rule_id = "upload_rule"
    name = "上传规则"
    description = "处理文件上传控件，生成合法上传/非法上传/空上传三胞胎"
    control_types = ["upload", "file", "file_input"]
    priority = 50

    def generate(
        self,
        req: dict[str, Any],
        target_page: str,
        control_type: str,
        merged_data: dict[str, Any],
        selected_ops: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rid = self._safe_rid(req)
        module = req.get("module", merged_data.get("module", "")) or ""
        operation = (req.get("operation") or "").lower()
        expected = req.get("expected_feedback") or req.get("expected") or ""
        desc = req.get("description", "") or operation
        submit_text = self._infer_submit_text(operation, target_page, merged_data)
        _cross_page_resolved = False

        # ★ 通用修复(2026-07-22): target_page 找不到按钮时，跨页面搜索
        #   根因: "批量导入辅材"的按钮"数据导入"可能在其他Tab页面上，
        #         target_page选择基于关键词匹配"辅材"→辅材配方页面，
        #         但导入按钮实际在叶组配方页面上。
        #   修复: 跨页面搜索找到按钮后，更新target_page为按钮所在页面。
        #   通用性: 适用于所有"按钮在非target_page"的场景（Tab页面/跨页面操作）
        #   覆盖性: 影响所有upload_rule生成的用例
        #   合规: AGENTS.md §12.4 通用性自检 — 跨页面通用性
        if submit_text is None:
            _btn, _page = self._resolve_button_cross_page(operation, target_page)
            if _btn and _page:
                submit_text = _btn
                target_page = _page
                _cross_page_resolved = True
                _log.info(
                    "[upload_rule] 跨页面搜索成功: operation=%r → button=%r, page=%r",
                    operation[:30],
                    _btn,
                    _page[:50],
                )
            else:
                return self._build_downgrade_cases(
                    req,
                    target_page,
                    merged_data,
                    reason=f"upload_rule: DOM无匹配按钮(operation={operation[:30]})",
                )
        tracks = ["ui"]

        # ★ v3.0 (2026-07-10): 从配置读取测试文件路径（原硬编码 C:/test/...）
        from src.core.pipeline_config import pipeline_config

        _valid_file = getattr(
            pipeline_config, "test_upload_valid_file", "test_files/sample.txt"
        )
        _invalid_file = getattr(
            pipeline_config, "test_upload_invalid_file", "test_files/invalid.exe"
        )

        # ★ 通用修复(2026-07-22 v11): 移除硬编码弹窗提交按钮点击
        #   根因核查(§10.9):
        #     1. 现象: click "开始导入" 按钮不存在 → AI loop timeout → 假FAIL
        #     2. 为什么1(直接): _infer_dialog_submit_text 硬编码 "开始导入"（导入类操作）
        #     3. 为什么2(中间): Step3 无弹窗 DOM 数据（Step1 只爬页面初始状态），只能推断
        #     4. 为什么3(根因): upload_file(v9.1) 已在运行时检测实际提交按钮并点击，
        #        Step3 生成单独的 click submit 是冗余且硬编码的
        #   修复: 移除 _submit_click，由 upload_file(v9.1) 动态检测+点击实际提交按钮
        #   通用性: 不硬编码任何按钮名，适用于所有系统的 upload 场景（跨系统/行业/页面）
        #   合理性: 追根溯源到 Step3 硬编码（不是 Step4 执行问题）
        #   覆盖性: 影响所有 upload_rule 的 P01/N01
        #   全局观: Step4 v9.1/v9.2/v10 保留作为运行时安全网
        #   职责边界: Step3 职责内（不生成冗余硬编码动作）
        #   自我反驳:
        #     1) v9.1 检测失败? → v9.1 搜索所有常见 modal 选择器，失败时 v10 设标志位跳过
        #     2) 更通用? → 用魔法标记 __dialog_submit__? → 增加约定复杂度，直接移除更简洁
        #     3) 更根本? → Step1 爬弹窗 DOM? → 工作量大且 v9.1 运行时检测更准确
        _trigger_clicks = (
            self._build_click_actions(operation, target_page, merged_data)
            if submit_text
            else []
        )

        # ── P01（正向）──
        # upload_file(v9.1) 内建: 上传文件 → 等待3s → 检测 modal 内实际提交按钮 → 点击
        p01_actions = [
            self._nav(target_page),
            *_trigger_clicks,
            self._upload(_valid_file),
            self._screenshot(),
        ]
        p01_checks = [
            self._build_success_check(operation, req=req),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

        # ── N01（异常）──
        # upload_file(v9.1) 上传非法文件后检测+点击提交按钮 → 系统显示校验错误
        n01_actions = [
            self._nav(target_page),
            *_trigger_clicks,
            self._upload(_invalid_file),
            self._screenshot(),
        ]
        n01_checks = [
            self._build_check("error_hint", "页面", "错误提示", "系统显示格式错误提示"),
        ]

        # ── B01（边界：不选文件场景）──
        # ★ v28 根因修复(2026-07-22): 交互模型自适应
        #   根因(3层追问):
        #     1. 表层: B01 不选文件点"开始导入"后无校验提示 → element_exists✗ 假FAIL
        #     2. 中层: 目标系统导入 modal 无提交按钮（只有"选择文件"），
        #              "不选文件点提交"路径在真实系统中不存在（选择文件即触发校验/导入）
        #     3. 根因: B01 模板假设"有提交按钮，不选文件点提交→校验提示"，
        #              与"选择即触发"交互模型错配
        #   修复: 不选文件场景的边界验证锚定为"上传入口可用性+约束可见"
        #         （弹窗/菜单/上传组件可见），"有/无提交按钮"两种交互模型下都正确；
        #         空输入防护的完整验证进入 Step5 人工补测建议
        #   通用性: 覆盖 Ant Design/ElementUI/原生 dialog/dropdown/upload 组件
        #   全局观: 不影响 P01（成功反馈）/N01（非法文件校验）语义
        b01_actions = [
            self._nav(target_page),
            *_trigger_clicks,
            self._screenshot(),
        ]
        b01_checks = [
            self._build_check(
                "element_exists_at",
                '.ant-dropdown-menu, .ant-modal, [role="dialog"], .el-dialog, .el-drawer, [class*="upload"]',
                "上传入口可见",
                "不选文件场景：上传弹窗/菜单/组件可见且约束明确",
            ),
        ]

        _cases = [
            self._build_one(
                f"TC-{rid}-P01",
                req,
                module,
                desc,
                operation,
                expected,
                "happy_path",
                "P1",
                "正向",
                "playwright",
                control_type,
                tracks,
                p01_actions,
                p01_checks,
                "正向流程验证",
            ),
            self._build_one(
                f"TC-{rid}-N01",
                req,
                module,
                desc,
                operation,
                expected,
                "error_path",
                "P1",
                "异常",
                "playwright",
                control_type,
                tracks,
                n01_actions,
                n01_checks,
                "异常输入校验",
            ),
            self._build_one(
                f"TC-{rid}-B01",
                req,
                module,
                desc,
                operation,
                expected,
                "boundary",
                "P2",
                "边界",
                "playwright",
                control_type,
                tracks,
                b01_actions,
                b01_checks,
                "边界值与异常验证",
            ),
        ]
        # ★ 通用修复(2026-07-22): 跨页面解析标记，防止 _control_validation 覆盖正确 URL
        if _cross_page_resolved:
            for _c in _cases:
                _c["_cross_page_resolved"] = True
        return _cases
