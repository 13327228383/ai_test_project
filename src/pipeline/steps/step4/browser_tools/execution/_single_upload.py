# -*- coding: utf-8 -*-
"""upload_file 执行辅助 — 从 _single.py 拆分（§4.3 单文件≤800行）。

职责：封装 upload_file 操作的全部执行期辅助逻辑：
  - 反馈等待/检测: _wait_for_upload_feedback / _check_upload_triggered
  - 模板下载与填充: _try_download_template / _fill_template_with_test_data
  - 测试值生成: _generate_test_value_for_column
  - 默认文件保障: _ensure_upload_test_file
  - accept 属性自适应: _parse_accept_attr / _generate_compatible_upload_file

依赖: asyncio / logging / pathlib / tempfile; openpyxl/PIL/docx 延迟导入
内部模块: src.core.config_loader / src.pipeline.step3.value_generator

根因(3层追问):
  1. 表层: _single.py 4101 行超 §4.3 红线 5 倍
  2. 中层: 上传辅助 8 个函数占 565 行，与 _execute_tool 主流程职责不同
  3. 根因: upload_file 辅助是独立职责（上传执行期支持），应拆为独立模块
通用性: 覆盖 Ant Design/ElementUI/Naive UI/Bootstrap/原生 HTML，零业务硬编码
覆盖性: 影响 Step4 upload_file 执行 → 上传用例 evidence 收集 → Step5 报告
"""
import asyncio
from src.core.guarded import guard
import logging
import tempfile
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


# ★ v11 通用修复(2026-07-23): upload_file 后等待业务反馈通知出现
#   根因核查(§10.9):
#     1. 现象: text_contains:✗ — upload_file 后 check 执行太快,成功通知尚未出现
#     2. 直接原因: upload_file "上传即提交"后直接返回,2秒内就执行 check
#     3. 中间原因: upload_file 语义不包含"等待反馈",系统来不及处理上传并显示通知
#     4. 根本原因: upload_file 是"提交类"操作,完成后应等待系统反馈(成功/失败通知)出现
#   全局观: 影响所有 upload_file 用例(跨系统/跨行业/跨页面),不影响其他 action 类型
#   职责边界: Step4 执行层 — "等待反馈"是 upload_file 执行语义的一部分,不是决策
#   通用性自检(§12.4):
#     1. 跨系统: 通知选择器覆盖 Ant Design/ElementUI/Naive UI/Bootstrap/原生HTML
#     2. 跨行业: 所有系统上传后都有反馈通知
#     3. 跨页面: 适用于所有上传页面
#     4. 跨Tab: 不涉及
#     5. 数据完整性: 不依赖外部数据
#     6. 根因溯源: 修upload_file语义不完整(治本),非加sleep治标
#     7. 覆盖性: 覆盖所有upload_file用例(submit button + upload=submit 两条路径)
#     8. 全局观: 不影响其他Step,只增强upload_file执行
#     9. 职责边界: Step4执行层,不越界
#   自我反驳三次:
#     1) 漏洞? → 固定sleep太短/太长 → 用wait_for_selector自适应等待
#     2) 更通用? → 在check前统一等待? → 太宽泛,非upload场景不需要
#     3) 更根本? → 统一"提交类action后等待反馈"机制 → 好,但当前先修upload_file最急迫
#   实施: 等待成功/失败通知出现(通用选择器),最多10秒,超时记录警告不阻塞
async def _wait_for_upload_feedback(page, timeout_sec: int = 10):
    """upload_file 后等待业务反馈通知出现。

    通用选择器覆盖主流 UI 框架的成功/失败通知:
    - Ant Design: .ant-message-success, .ant-notification-notice-success
    - ElementUI: .el-message--success, .el-notification--success
    - Naive UI: .n-message--success-type
    - Bootstrap: .alert-success, .toast.success
    - 通用: [role=status], .success, .toast, .notification, .message
    """
    # 成功通知选择器(覆盖主流UI框架)
    _SUCCESS_SELECTORS = (
        ".ant-message-success",
        ".ant-message-notice-content",
        ".ant-notification-notice-success",
        ".ant-notification-notice-content",
        ".el-message--success",
        ".el-notification.success",
        ".n-message--success-type",
        ".alert-success",
        ".toast.success",
        ".toast",
        "[role='status']",
        ".success",
        ".notification",
        ".message:not(:empty)",
    )
    # 失败/错误通知选择器(也终止等待)
    _ERROR_SELECTORS = (
        ".ant-message-error",
        ".ant-notification-notice-error",
        ".el-message--error",
        ".el-notification.error",
        ".n-message--error-type",
        ".alert-danger",
        ".alert-error",
        ".error",
        "[role='alert']",
    )
    _ALL_SELECTORS = _SUCCESS_SELECTORS + _ERROR_SELECTORS
    _selector_str = ", ".join(_ALL_SELECTORS)
    try:
        # 用 wait_for_selector 等待任一通知出现(最多 timeout_sec 秒)
        try:
            await page.wait_for_selector(
                _selector_str, timeout=timeout_sec * 1000, state="visible"
            )
            # 找到通知,判断是成功还是失败
            _found_text = ""
            for _sel in _ALL_SELECTORS:
                try:
                    _loc = page.locator(_sel).first
                    if await _loc.count() > 0 and await _loc.is_visible():
                        _found_text = (
                            await _loc.text_content(timeout=2000) or ""
                        ).strip()
                        if _found_text:
                            _is_error = (
                                any(
                                    _esel in _sel
                                    for _esel in ("error", "danger", "alert")
                                )
                                and "success" not in _sel.lower()
                            )
                            _log.info(
                                "[upload] v11: ✓ 业务反馈已出现 (sel=%s text=%r is_error=%s)",
                                _sel,
                                _found_text[:60],
                                _is_error,
                            )
                            break
                except Exception as _se1:
                    _log.debug(
                        "[_single_upload] 异常降级(continue): %s",
                        str(_se1)[:60],
                        exc_info=True,
                    )
                    continue
            # 额外等待1秒让通知完全渲染
            await asyncio.sleep(1)
            return _found_text
        except Exception:
            # 超时未找到通知,记录警告但不阻塞
            _log.warning(
                "[upload] v11: 等待%d秒后未发现业务反馈通知(可能系统无通知或处理时间较长)",
                timeout_sec,
            )
            return ""
    except Exception as _e:
        _log.debug("[upload] v11 等待反馈异常(不阻塞): %s", str(_e)[:80])
        return ""


async def _check_upload_triggered(page, timeout_sec: int = 2) -> str:
    """★ v18: 检测 set_input_files 后上传是否真正触发。

    检测三类反馈信号(任一出现即认为上传已触发):
    1. 业务通知: 成功/失败通知(Ant Design/ElementUI/Naive UI/Bootstrap)
    2. 文件列表: 上传组件显示已选文件(Ant Design .ant-upload-list-item 等)
    3. 加载动画: 上传中状态(.ant-upload-loading/.ant-spin 等)

    通用性: 覆盖主流 UI 框架(Ant Design/ElementUI/Naive UI/Bootstrap/原生)
    返回: 反馈类型字符串(如 "notification"/"file_list"/"loading")，无反馈返回 ""
    """
    # 文件列表选择器(上传组件显示已选文件)
    _FILE_LIST_SELECTORS = (
        ".ant-upload-list-item",  # Ant Design
        ".el-upload-list__item",  # ElementUI
        ".n-upload-file",  # Naive UI
        ".upload-list .upload-list-item",  # 通用
        "[class*='upload-list-item']",  # 通用 class 匹配
        "[class*='file-item']",  # 通用 file-item
    )
    # 加载动画选择器(上传中状态)
    _LOADING_SELECTORS = (
        ".ant-upload-loading",
        ".ant-spin-spinning",
        ".el-upload-loading",
        ".el-loading-mask",
        ".n-upload-loading",
        ".is-loading",
        "[class*='upload-loading']",
        "[class*='uploading']",
    )
    # 通知选择器(复用 _wait_for_upload_feedback 的子集)
    _NOTIFY_SELECTORS = (
        ".ant-message-success",
        ".ant-message-error",
        ".ant-notification-notice-success",
        ".ant-notification-notice-error",
        ".el-message--success",
        ".el-message--error",
        ".n-message--success-type",
        ".n-message--error-type",
        ".alert-success",
        ".alert-danger",
        ".alert-error",
        "[role='status']",
        "[role='alert']",
    )

    try:
        # 等待一小段时间让 UI 响应
        await asyncio.sleep(min(timeout_sec, 2))

        # 检测文件列表
        for sel in _FILE_LIST_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    _log.info("[upload] v18: 检测到文件列表项 (sel=%s)", sel)
                    return "file_list"
            except Exception as _se2:
                _log.debug(
                    "[_single_upload] 异常降级(continue): %s",
                    str(_se2)[:60],
                    exc_info=True,
                )
                continue

        # 检测加载动画
        for sel in _LOADING_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    _log.info("[upload] v18: 检测到加载动画 (sel=%s)", sel)
                    return "loading"
            except Exception as _se3:
                _log.debug(
                    "[_single_upload] 异常降级(continue): %s",
                    str(_se3)[:60],
                    exc_info=True,
                )
                continue

        # 检测通知
        for sel in _NOTIFY_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    # ★ v19.1 通用修复: 通知文本可能延迟渲染，重试读取
                    #   根因: Ant Design message 有淡入动画，元素已出现但文本未渲染
                    #   通用性: 适用于所有 UI 框架的延迟渲染
                    _text = ""
                    for _retry in range(3):
                        try:
                            _text = (await loc.text_content(timeout=1000) or "").strip()
                            if _text:
                                break
                        except Exception as _se4:
                            _log.debug(
                                "[_single_upload] 异常降级(pass): %s",
                                str(_se4)[:60],
                                exc_info=True,
                            )
                            pass
                        if _retry < 2:
                            await asyncio.sleep(0.5)
                    _is_error = (
                        any(_kw in sel for _kw in ("error", "danger", "alert"))
                        and "success" not in sel.lower()
                    )
                    _log.info(
                        "[upload] v18: 检测到通知 (sel=%s is_error=%s text=%r retry=%d)",
                        sel,
                        _is_error,
                        _text[:100],
                        _retry,
                    )
                    # ★ v21 通用修复(2026-07-23): 检测到通知时立即写入 page._transient_hints
                    #   根因(§10.9): _check_upload_triggered 已检测到通知(sel+text)，但只返回
                    #     字符串 → _capture_transient_hints 调用时 antd message 已淡出(~3s)，
                    #     _match_transient_hints 查空缓存 → element_exists:✗ (假FAIL)
                    #   修复: 检测到就保存完整证据(text/cls/role)，_match_transient_hints 直接命中
                    #   通用性: 覆盖所有 _NOTIFY_SELECTORS(Ant Design/ElementUI/Bootstrap/原生)
                    #   覆盖性: 唯一通知检测点，所有 upload 反馈都经此路径
                    #   全局观: 只增强 Step4 证据收集，不影响 Step1/2/3/5
                    #   职责边界: Step4 执行层内(证据捕获是执行的一部分，非决策)
                    with guard("_check_upload_triggered: _hints = getattr(page, '_transient_h", log=_log):
                        _hints = getattr(page, "_transient_hints", None)
                        if _hints is None:
                            _hints = []
                            setattr(page, "_transient_hints", _hints)
                        _cls_attr = ""
                        _role_attr = ""
                        try:
                            _cls_attr = await loc.get_attribute("class") or ""
                            _role_attr = await loc.get_attribute("role") or ""
                        except Exception as _se5:
                            _log.debug(
                                "[_single_upload] 异常降级(pass): %s",
                                str(_se5)[:60],
                                exc_info=True,
                            )
                            pass
                        if not _role_attr:
                            _role_attr = "alert" if _is_error else "status"
                        _entry = {
                            "text": _text[:200],
                            "cls": _cls_attr,
                            "role": _role_attr,
                            "ts": __import__("time").time(),
                        }
                        _key = (_entry["text"], _entry["cls"])
                        if not any(
                            (h.get("text"), h.get("cls")) == _key for h in _hints
                        ):
                            _hints.append(_entry)
                            _log.info(
                                "[Transient] upload 检测即保存: %s (cls=%s)",
                                _text[:60],
                                _cls_attr[:40],
                            )
                    return f"notification:{'error' if _is_error else 'success'}:{_text[:80]}"
            except Exception as _se6:
                _log.debug(
                    "[_single_upload] 异常降级(continue): %s",
                    str(_se6)[:60],
                    exc_info=True,
                )
                continue

        return ""
    except Exception as _e:
        _log.debug("[upload] v18 检测反馈异常: %s", str(_e)[:80])
        return ""


async def _try_download_template(page, timeout_sec: int = 5) -> Optional[str]:
    """★ v19: 尝试在当前页面/模态框下载导入模板。

    通用策略：搜索含"模板"关键词的可点击元素，点击触发下载。
    下载完成后返回文件路径，未找到或下载失败返回 None。

    通用性(§12.4):
      - 跨系统: 适用于 Ant Design/ElementUI/Native HTML 的模板下载按钮
      - 跨行业: 不依赖业务术语，只匹配"模板"通用关键词
      - 跨页面: 在 modal/dialog/页面任意位置搜索
      - 数据完整性: 有模板则用模板，无模板则降级为生成的文件
      - 根因溯源: 修复"不知道系统期望的文件格式"根因
      - 覆盖性: 覆盖所有提供模板下载的上传场景
      - 全局观: 只增强 Step4 执行，不影响其他 Step
      - 职责边界: Step4 Self-Healing 范围内（运行时获取正确格式）
    """

    # 模板下载按钮选择器（精确匹配优先，模糊匹配兜底）
    _TEMPLATE_SELECTORS = [
        # 精确匹配（含"模板"+"下载"组合）
        'a:has-text("下载模板")',
        'button:has-text("下载模板")',
        'a:has-text("导入模板")',
        'button:has-text("导入模板")',
        'a:has-text("模板下载")',
        'button:has-text("模板下载")',
        '[role="button"]:has-text("下载模板")',
        '[role="button"]:has-text("导入模板")',
        # 模糊匹配（仅含"模板"）
        'a:has-text("模板")',
        'button:has-text("模板")',
        '[role="button"]:has-text("模板")',
        # 限定在 modal 内
        '.ant-modal a:has-text("模板")',
        '.ant-modal button:has-text("模板")',
        '.el-dialog a:has-text("模板")',
        '.el-dialog button:has-text("模板")',
        '[role="dialog"] a:has-text("模板")',
        '[role="dialog"] button:has-text("模板")',
    ]

    # 排除负面关键词（不是模板下载的按钮）
    _NEGATIVE_KWS = (
        "取消",
        "关闭",
        "返回",
        "退出",
        "放弃",
        "删除",
        "清空",
        "停止",
        "导出",
    )

    try:
        from src.core.config_loader import get_current_user

        _uid = get_current_user() or "default"
    except Exception:
        _uid = "default"
    tmp_dir = Path(tempfile.gettempdir()) / f"e2e_templates_{_uid}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for sel in _TEMPLATE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            if not await loc.is_visible():
                continue
            _text = (await loc.text_content(timeout=1000) or "").strip()
            if not _text:
                continue
            if any(_neg in _text for _neg in _NEGATIVE_KWS):
                continue
            _log.info(
                "[upload] v19: 发现模板下载按钮 '%s' (sel=%s)，尝试下载",
                _text[:40],
                sel[:50],
            )

            # 使用 expect_download 等待下载
            try:
                async with page.expect_download(timeout=timeout_sec * 1000) as dl_info:
                    await loc.click(timeout=3000)
                download = await dl_info
                # 保存到临时文件（保留原始扩展名）
                _orig_name = download.suggested_filename or "template.xlsx"
                _template_path = (
                    tmp_dir
                    / f"template_{int(asyncio.get_event_loop().time())}_{_orig_name}"
                )
                await download.save_as(str(_template_path))
                _file_size = (
                    _template_path.stat().st_size if _template_path.exists() else 0
                )
                if _file_size < 10:
                    _log.warning(
                        "[upload] v19: 模板文件过小(%d bytes)，可能无效", _file_size
                    )
                    continue
                _log.info(
                    "[upload] v19: ✓ 模板下载成功: %s (%d bytes)",
                    _template_path.name,
                    _file_size,
                )
                return str(_template_path)
            except Exception as _dl_e:
                _log.info(
                    "[upload] v19: 模板按钮 '%s' 点击未触发下载: %s",
                    _text[:40],
                    str(_dl_e)[:80],
                )
                continue
        except Exception as _se7:
            _log.debug(
                "[_single_upload] 异常降级(continue): %s", str(_se7)[:60], exc_info=True
            )
            continue

    _log.info("[upload] v19: 未找到模板下载按钮，将使用生成的文件")
    return None


def _fill_template_with_test_data(template_path: str) -> str:
    """★ v19: 用最小测试数据填充下载的模板。

    保留模板的 sheet 名和列头结构，在第二行填入合规测试数据。
    这是"基于真实模板填充"而非"猜测格式"，确保系统接受文件。

    通用性: 适用于任何 Excel 模板（保留原始结构，只填数据行）
    """
    try:
        from openpyxl import load_workbook

        wb = load_workbook(template_path)
        ws = wb.active

        # 读取列头（第一行）
        headers = []
        for cell in ws[1]:
            if cell.value is not None:
                headers.append(str(cell.value).strip())
            else:
                headers.append("")

        if not any(headers):
            _log.warning("[upload] v19: 模板无列头，无法填充，使用原模板")
            return template_path

        # 填入测试数据（第二行）
        for col_idx, header in enumerate(headers, 1):
            if not header:
                continue
            test_value = _generate_test_value_for_column(header)
            ws.cell(row=2, column=col_idx, value=test_value)

        # 保存到新文件
        try:
            from src.core.config_loader import get_current_user

            _uid = get_current_user() or "default"
        except Exception:
            _uid = "default"
        filled_dir = Path(tempfile.gettempdir()) / f"e2e_uploads_filled_{_uid}"
        filled_dir.mkdir(parents=True, exist_ok=True)
        filled_path = filled_dir / f"filled_{Path(template_path).name}"
        wb.save(str(filled_path))
        _log.info(
            "[upload] v19: ✓ 模板填充完成: %s (headers=%s)",
            filled_path.name,
            headers[:5],
        )
        return str(filled_path)
    except Exception as _e:
        _log.warning("[upload] v19: 填充模板失败: %s，使用原模板", str(_e)[:100])
        return template_path


def _generate_test_value_for_column(header: str) -> str:
    """★ v19: 根据列头语义生成合规测试值（走 value_generator 统一管道）。

    ★ §13.3 通用修复(2026-07-24 续): 改用 value_generator，禁硬编码 E2E测试数据/E2E001 等。
      根因: 原 _generate_test_value_for_column 硬编码值，绕过 value_generator 管道。
      方案: header 作 field_name，类型按列头语义推断，调 generate_value_from_type。
      通用性: value_generator 内置名称/编码/日期/金额等语义，跨系统/行业通用。
    """
    if not header:
        return "测试数据"
    # 列头语义 → field_type
    if any(
        kw in header
        for kw in ("数量", "值", "value", "amount", "price", "价格", "金额")
    ):
        _ftype = "integer"
    else:
        _ftype = "string"
    try:
        from src.pipeline.step3.value_generator import generate_value_from_type

        return generate_value_from_type(_ftype, header)
    except Exception as _e:
        _log.debug("[upload] value_generator 不可用，用兜底: %s", _e, exc_info=True)
        return "测试数据"


def _ensure_upload_test_file(file_path: str) -> Optional[str]:
    """确保 upload_file 有可用测试文件。

    若调用方未提供 file_path 或文件不存在，则在系统临时目录生成一个
    通用测试文件（含中文与 ASCII 内容，覆盖常见上传场景），避免 Step3
    未生成 file_path 时直接参数校验失败。
    """
    import os

    # 优先使用调用方提供的真实路径
    if file_path:
        p = Path(file_path)
        if p.exists() and p.is_file():
            return file_path

    # 尝试在项目临时目录复用同一份默认测试文件
    # ★ 方案C 改造：按 user_id 隔离上传临时目录，防止并发文件冲突
    try:
        from src.core.config_loader import get_current_user

        _uid = get_current_user() or "default"
    except Exception:
        _uid = "default"
    tmp_dir = Path(tempfile.gettempdir()) / f"e2e_uploads_{_uid}"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except Exception as _e:
        _log.debug("[_ensure_upload_test_file] 创建临时目录失败: %s", _e)
        return None

    default_file = tmp_dir / "e2e_default_upload.txt"
    try:
        if not default_file.exists():
            default_file.write_text(
                "E2E 默认上传测试文件\nDefault upload test content for E2E engine.\n",
                encoding="utf-8",
            )
        return str(default_file)
    except Exception as _e:
        _log.warning("[_ensure_upload_test_file] 创建默认测试文件失败: %s", _e)
        return None


# ★ 通用修复(2026-07-23 v16): 上传文件格式自适应
#   根因(3层追问):
#     1. 现象: 上传 sample.txt 后系统无"导入成功"通知
#     2. 直接原因: 后端 uploadExcel API 期望 .xlsx，sample.txt 解析失败
#     3. 根本原因: Step3 用静态配置 test_upload_valid_file(默认 sample.txt)，
#        不感知目标系统 input[type=file] 的 accept 属性
#   修复: Step4 上传前读取 accept 属性，动态生成兼容格式测试文件
#   通用性(§12.4):
#     - 跨系统: accept 是 HTML 标准，所有系统都支持
#     - 跨行业: 支持 Excel/PDF/图片/CSV/TXT 等所有常见格式
#     - 跨页面: 适用于任何 upload 页面
#     - 根因溯源: 修复"不感知 accept"根因，非表层补丁
#     - 覆盖性: 覆盖所有 upload_file 调用
#     - 全局观: 只增强 Step4 执行，不影响其他 Step
#     - 职责边界: Step4 是执行层，感知 DOM 并生成兼容文件是执行职责
#   自我反驳三次:
#     1) accept 为空? → 兜底用原文件(向后兼容)
#     2) 更通用? → Step3 从 yapi 获取? → yapi 不一定有文件格式，Step4 实时感知最准
#     3) 更根本? → Step1 爬 accept 存 rag_chunks? → accept 可能动态变化，Step4 实时读最准


def _parse_accept_attr(accept: str) -> list:
    """解析 input[type=file] 的 accept 属性，返回标准化扩展名列表。

    支持:
      - 扩展名: .xlsx, .xls, .csv, .pdf, .png, .jpg, .txt
      - MIME 类型: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet → .xlsx
      - 通配符: image/* → [.png, .jpg], text/* → [.txt, .csv]
    """
    if not accept or not accept.strip():
        return []
    result = []
    for part in accept.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part.startswith("."):
            result.append(part)
        elif part == "image/*":
            result.extend([".png", ".jpg", ".jpeg"])
        elif part == "text/*":
            result.extend([".txt", ".csv"])
        elif (
            part == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ):
            result.append(".xlsx")
        elif part == "application/vnd.ms-excel":
            result.append(".xls")
        elif part == "text/csv":
            result.append(".csv")
        elif part == "application/pdf":
            result.append(".pdf")
        elif part == "image/png":
            result.append(".png")
        elif part in ("image/jpeg", "image/jpg"):
            result.append(".jpg")
        elif part == "text/plain":
            result.append(".txt")
        elif (
            part
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            result.append(".docx")
    return list(dict.fromkeys(result))  # 去重保序


def _generate_compatible_upload_file(file_path: str, accept_attr: str) -> str:
    """根据 accept 属性生成兼容格式的测试文件。

    若 file_path 的扩展名已匹配 accept，返回原路径（向后兼容）。
    若不匹配，在临时目录生成兼容文件并返回新路径。
    若 accept 为空，返回原路径（向后兼容）。
    """
    accepted_exts = _parse_accept_attr(accept_attr)
    if not accepted_exts:
        return file_path

    # 检查原文件是否已匹配
    orig_ext = Path(file_path).suffix.lower()
    if orig_ext in accepted_exts:
        return file_path

    # 需要生成兼容文件
    target_ext = accepted_exts[0]
    try:
        from src.core.config_loader import get_current_user

        _uid = get_current_user() or "default"
    except Exception:
        _uid = "default"
    tmp_dir = Path(tempfile.gettempdir()) / f"e2e_uploads_{_uid}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target_file = tmp_dir / f"e2e_compatible_upload{target_ext}"

    try:
        if target_ext == ".xlsx":
            # 生成最小有效 Excel 文件
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.title = "Sheet1"
            ws.append(["E2E测试数据", "值"])
            ws.append(["测试项1", "100"])
            wb.save(str(target_file))
        elif target_ext == ".xls":
            # 退化为 .xlsx（大多数系统同时支持）
            target_file = tmp_dir / "e2e_compatible_upload.xlsx"
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.append(["E2E测试数据", "值"])
            wb.save(str(target_file))
        elif target_ext == ".csv":
            target_file.write_text("E2E测试数据,值\n测试项1,100\n", encoding="utf-8")
        elif target_ext == ".pdf":
            # 最小有效 PDF（固定字节序列）
            _minimal_pdf = (
                b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
                b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
                b"0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\n"
                b"startxref\n149\n%%EOF"
            )
            target_file.write_bytes(_minimal_pdf)
        elif target_ext in (".png", ".jpg", ".jpeg", ".gif"):
            from PIL import Image

            _fmt_map = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF"}
            img = Image.new("RGB", (10, 10), color=(255, 255, 255))
            img.save(str(target_file), format=_fmt_map.get(target_ext, "PNG"))
        elif target_ext == ".txt":
            target_file.write_text("E2E 测试上传文件\n", encoding="utf-8")
        elif target_ext == ".docx":
            # 退化为 .txt（若无 python-docx）
            try:
                from docx import Document

                doc = Document()
                doc.add_paragraph("E2E 测试文档")
                doc.save(str(target_file))
            except ImportError:
                target_file = tmp_dir / "e2e_compatible_upload.txt"
                target_file.write_text("E2E 测试上传文件\n", encoding="utf-8")
        else:
            # 未知格式，返回原文件让系统处理
            return file_path
        _log.info(
            "[upload] v16: 文件格式自适应 %s → %s (accept=%s)",
            Path(file_path).name,
            target_file.name,
            accept_attr[:50],
        )
        return str(target_file)
    except Exception as _e:
        _log.warning(
            "[upload] v16: 生成兼容文件失败(ext=%s): %s → 用原文件",
            target_ext,
            str(_e)[:100],
        )
        return file_path
