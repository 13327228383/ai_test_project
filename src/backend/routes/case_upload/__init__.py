# -*- coding: utf-8 -*-
"""
Step3 用例上传路由 — Blueprint「case_upload」
============================================
用例绑定专项(2026-08)：把"用例上传执行"升级为绑定被测系统+版本、带鉴权的受控入口。

★ 三接口（与 file_upload 蓝图同构）：
  - GET  /api/step3/case/template  下载预填版本模板（权限 case.template）
  - POST /api/step3/case/validate  仅校验不上传，返回 VersionMatchResult（登录即可）
  - POST /api/step3/case/upload    上传+执行（权限 case.upload → run_uploaded_cases → 写历史）
"""
from flask import Blueprint

case_upload_bp = Blueprint("case_upload", __name__)

from . import _template
from . import _validate
from . import _upload
from . import _history
