# -*- coding: utf-8 -*-
"""项目内上传文档的唯一读取入口。

"这个项目里有哪些上传文档、各自的正文是什么"原本被 6 处各自拼 SQL
（RAG 切片、覆盖审计、Step1 需求合并、Step2 增量哈希、Step2 正文拼接、
Step1 构建门控），过滤条件/分类集合/排序各不相同 —— 于是同一次运行里
索引链、审计链、抽取链会对"同一批文档"给出不同答案，表现为"上传了却
没进索引""审计说有缺""Step2 说没变化"这类互相矛盾的结论。

此处收敛为单点：所有调用方读同一份行集合（含正文），额外维度（分类过滤、
排序）由调用方在参数/内存里叠加，不再各自写 SQL。

不变量：
- 只返回正文非空的文档行。空正文文档不可索引，也不应成为覆盖审计的分母；
  空正文上传在入口即判失败（FileParser 文本契约），不会落库。
- project 为空时返回空列表，绝不跨项目兜底读取。
"""

import logging

_log = logging.getLogger(__name__)

UPLOAD_DOC_TYPE_PREFIX = "upload_"

# 上传文档分类 → RAG chunk 类型。单一事实源：切片、审计、抽取共用同一分类集合。
UPLOAD_CATEGORY_CHUNK_TYPES = {
    "requirement": "upload_req",
    "data_dict": "upload_dict",
    "api_supplement": "upload_api",
    "design_supplement": "upload_design",
    "test_data": "upload_data",
    "ddl_schema": "upload_ddl",
}

_SELECT_SQL = (
    "SELECT doc_type, filename, file_format, category, weight, content_hash, content "
    "FROM source_documents "
    "WHERE project = %s AND version_label = %s AND doc_type LIKE 'upload_%%' "
    "AND category IN ({placeholders}) "
    "AND CHAR_LENGTH(content) > 0"
)


def fetch_uploaded_documents(
    project: str, version_label: str, categories=None
) -> list[dict]:
    """返回项目 + 版本内的上传文档行（均含正文）。

    版本是必填维度：上传行与 canonical 需求行都按版本分区存储，索引/审计/抽取
    若不带版本就会把别的版本的内容混进来。version_label 为空时返回空列表
    （与 project 为空同语义：没有版本上下文就无法确定分区）。

    Args:
        project: 项目名（构建/审计/抽取必须是同一个值）。
        version_label: 版本标签（与 archived/active 版本一致）。
        categories: 可选分类子集；默认全部已知分类。未知分类（NULL/
            历史脏值）不参与，避免"库里有一行、索引里没有"的假缺口。

    Returns:
        [{"doc_type", "filename", "file_format", "category", "weight",
          "content_hash", "content"}, ...]；查询失败返回空列表并告警。
    """
    project = (project or "").strip()
    version_label = (version_label or "").strip()
    if not project or not version_label:
        return []
    cats = tuple(categories or UPLOAD_CATEGORY_CHUNK_TYPES)
    if not cats:
        return []

    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        placeholders = ",".join(["%s"] * len(cats))
        rows = conn.execute(
            _SELECT_SQL.format(placeholders=placeholders),
            (project, version_label, *cats),
        ).fetchall()
        return [dict(r) for r in (rows or [])]
    except Exception as e:
        _log.warning("[UploadDocs] 读取上传文档失败 project=%s: %s", project, str(e)[:120])
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[UploadDocs] 连接关闭异常: %s", _e)
