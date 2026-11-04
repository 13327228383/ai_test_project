# -*- coding: utf-8 -*-
"""通用 UI 同义词字典（与具体业务无关）。

每个 group 是一个语义簇——簇内任一词都视为命中该语义。
关键词提取时取意图描述里出现的某个词，扩展到该词所在的整个簇做匹配。

★ 字典维护原则
1. 只放通用 UI 词，不放业务术语（业务术语去 _synonyms_project.py）
2. 中英混排（按钮文本经常是英文，意图描述是中文）
3. 全部小写存储，匹配时把元素文本和意图都转小写比较
4. 不做语义模糊匹配——同一簇内的词都得是"几乎等价"的
"""

# 每个簇的第一个词作为"代表词"，仅用于日志/调试展示
COMMON_SYNONYMS = [
    # ── 写入/确认类操作 ──
    {"保存", "save", "存储", "保留"},
    {"提交", "submit", "send", "发送", "递交"},
    {"确定", "ok", "确认", "确认操作", "yes", "✓", "对", "好的"},
    {"应用", "apply"},
    {"完成", "done", "finish", "结束", "完毕"},
    {"创建", "create", "新建", "new", "添加", "add", "新增", "+"},
    # ── 取消/否定类操作 ──
    {"取消", "cancel", "no", "✗", "不", "返回上级"},
    {"关闭", "close", "×", "x", "退出", "dismiss"},
    {"重置", "reset", "清空", "clear", "清除"},
    {"删除", "delete", "del", "移除", "remove", "🗑"},
    # ── 修改类操作 ──
    {"编辑", "edit", "修改", "update", "modify", "✏", "改"},
    {"复制", "copy", "duplicate"},
    {"粘贴", "paste"},
    {"剪切", "cut"},
    # ── 导航类 ──
    {"返回", "back", "go back", "←"},
    {"前进", "forward", "next", "→"},
    {"下一步", "next step", "下一页", "next page"},
    {"上一步", "prev step", "previous", "上一页"},
    {"首页", "home", "主页", "homepage"},
    {"跳过", "skip"},
    # ── 查询类 ──
    {"搜索", "search", "查询", "query", "查找", "find", "🔍"},
    {"筛选", "filter", "过滤"},
    {"排序", "sort", "order"},
    {"刷新", "refresh", "reload", "重新加载", "🔄"},
    # ── 数据流转 ──
    {"导入", "import", "上传", "upload", "选择文件", "choose file"},
    {"导出", "export", "download", "下载"},
    {"打印", "print"},
    {"分享", "share"},
    # ── 用户态 ──
    {"登录", "login", "log in", "sign in", "登入"},
    {"登出", "logout", "log out", "sign out", "退出登录"},
    {"注册", "register", "sign up", "create account", "注册账号"},
    # ── 详情/展开 ──
    {"详情", "detail", "details", "更多", "more", "view"},
    {"展开", "expand", "▼", "▽"},
    {"折叠", "collapse", "▲", "△"},
    {"显示", "show", "display"},
    {"隐藏", "hide"},
    # ── 表单常见字段（这些词常出现在 placeholder/label 上）──
    {"用户名", "username", "user name", "账号", "account", "user id"},
    {"密码", "password", "passwd", "pwd"},
    {"邮箱", "email", "e-mail", "mail"},
    {"手机号", "phone", "mobile", "手机", "电话"},
    {"验证码", "captcha", "verify code", "code"},
    {"姓名", "name", "全名", "full name"},
    # ── 状态/反馈类（出现在断言场景）──
    {"成功", "success", "succeeded", "ok"},
    {"失败", "failed", "failure", "error", "错误"},
    {"警告", "warning", "warn", "注意"},
    {"提示", "tip", "tips", "hint", "notice"},
]


def build_lookup_table(groups):
    """把 List[Set[str]] 倒排成 word -> set(同簇词) 的查找表。
    全部小写。"""
    table = {}
    for group in groups:
        if not group:
            continue
        normalized = {w.strip().lower() for w in group if w and w.strip()}
        for word in normalized:
            # 同一个词在多个簇里时，合并所有簇（罕见但可能发生）
            if word in table:
                table[word] = table[word] | normalized
            else:
                table[word] = set(normalized)
    return table


# 模块加载时构建一次倒排表
COMMON_LOOKUP = build_lookup_table(COMMON_SYNONYMS)
