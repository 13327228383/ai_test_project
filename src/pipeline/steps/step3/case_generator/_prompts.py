# -*- coding: utf-8 -*-
"""测试用例生成 Prompt 常量（v4.0 意图驱动）—— explore_task + checks 格式。"""

# ── 输出格式（完整测试用例 = explore_task + checks）──
_CASE_OUTPUT_FORMAT = (
    "\n## 输出格式（★★★ 必须严格遵守）\n"
    '输出必须是单个 JSON 对象，顶层字段为 "cases"，其值是测试用例数组。\n'
    '格式如下：{"cases": [完整测试用例对象, ...]}。\n'
    "禁止省略外层 case 对象；禁止输出 markdown 代码块。\n\n"
    "{\n"
    '  "cases": [\n'
    "    {\n"
    '      "case_id": "TC-XXX",\n'
    '      "req_id": "REQ-XXX",\n'
    '      "title": "简洁概括测试目的（20-50字）",\n'
    '      "scenario_type": "happy_path|error_path|boundary|security",\n'
    '      "test_method": "EP|BV|CE|DT|ST|OT|EG|FD|PC|SD|SX|SI|CS|AU|SS|CT|PF|ET",\n'
    '      "control_type": "input|select|button|upload|display|nav|interactive|composite",\n'
    '      "risk_level": "P0|P1|P2",\n'
    '      "type": "功能|UI|安全|性能|易用|兼容",\n'
    '      "precondition": "前置条件（编号列表，如：1.系统已登录 2.测试数据已准备）",\n'
    '      "test_steps": "测试步骤（JSON数组，如：[{"step":1,"action":"打开页面"},{"step":2,"action":"输入查询条件","data":"TEST001"}]）",\n'
    '      "expected_result": "预期结果（精确描述，如：页面显示搜索结果为TEST001的记录）",\n'
    '      "explore_task": {\n'
    '        "operation": "从需求中提取的操作描述（如：按名称搜索辅材、批量导入辅材）",\n'
    '        "target_page": "★ 可选（推荐留空""，让Agent用module字段实时探索侧边栏菜单；仅当导航地图中有可靠URL时填写）",\n'
    '        "description": "人类可读的任务描述（如：在辅材管理页面搜索名为TEST的辅材）",\n'
    '        "module": "所属模块（如：物料管理）",\n'
    '        "input_values": {"字段名": "测试值"},\n'
    '        "test_method": {\n'
    '          "navigation": "侧边栏菜单路径或URL",\n'
    '          "inputs": {"字段名": {"value": "测试值", "source": "yapi_example|db_constraint|semantic_infer"}},\n'
    '          "verifications": [\n'
    '            {"step": 1, "action": "fill|click|verify|wait", "target": "目标元素描述", "value": "操作值", "expect": "精确期望结果"},\n'
    '            {"step": 2, "action": "verify", "target": "操作结果区域", "value": "", "expect": "创建成功"},\n'
    '            {"step": 3, "action": "verify", "target": "列表数据", "value": "", "expect": "列表包含新增记录"}\n'
    "          ]\n"
    "        }\n"
    "      },\n"
    '      "checks": [\n'
    '        {"track": "ui|api|db", "precision": "exact|fuzzy",\n'
    '         "check": "检查内容（exact写具体规则，fuzzy写自然语言）",\n'
    '         "expect": "预期结果（exact写具体值，fuzzy写期望特征）"}\n'
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "字段说明：\n"
    "- scenario_type: ★ 必填。happy_path(正向流程)、error_path(异常/错误路径)、boundary(边界值)、security(安全验证)\n"
    "- type: ★ 必填，case_type（测试类型维度，与scenario_type场景维度正交）。★★★ 必须覆盖至少 3 类以上，不要全部用功能！\n"
    "  · 功能：核心业务功能正确性——创建/编辑/删除/查询/审批等正向业务流程\n"
    "  · UI：界面视觉与交互——布局对齐、按钮状态、提示文案、滚动/点击反馈、控件可见可点（用 visual_check 断言）\n"
    "  · 安全：漏洞与权限——XSS/SQL注入/越权访问/敏感信息泄露/CSRF（用 SX/SI method，security scenario_type）\n"
    "  · 性能：响应与规模——页面加载时间≤3s、列表500条流畅、大数据量导入不超时、并发操作不崩溃\n"
    "  · 易用：操作友好度——首次操作无引导文案/错误提示模糊难恢复/关键操作需6步以上\n"
    "  · 兼容：多环境差异——不同浏览器(Chrome/Edge)、不同分辨率(1920×1080/1366×768)、深色模式/浅色模式\n"
    "  ★ 强制分布规则：每条需求至少生成 3 条不同 type 的用例——1条功能 + 1条UI或安全 + 1条性能或易用或兼容\n"
    "  ★ UI类用例必须带 visual_check selector；性能类必须带响应时间阈值；安全类必须带具体攻击向量(payload)\n"
    "- test_method: ★ 必填(07-05h)。测试方法论编码（与scenario_type正交）：\n"
    "  · EP=等价类划分 BV=边界值分析 EG=错误推测 FD=场景法\n"
    "  · CE=因果图 DT=判定表 ST=状态迁移 OT=正交实验\n"
    "  · PC=路径覆盖 SD=规范导出 SX=XSS注入 SI=SQL注入\n"
    "  · CS=CSRF跨站请求伪造 AU=越权/权限校验（IDOR）\n"
    "  · SS=会话管理（登录/登出/超时/Token/Cookie）CT=兼容/响应式 PF=性能 ET=探索式\n"
    "- control_type: ★ 必填(07-05h)。控件类型，决定适用的测试方法论：\n"
    "  · input(输入类): text/number/password/email/textarea/date 等\n"
    "  · select(选择类): select/radio/checkbox/cascader/transfer\n"
    "  · button(按钮类): button/submit/link\n"
    "  · upload(上传类): upload/dragger\n"
    "  · display(展示类): table/list/tag/badge/card\n"
    "  · nav(导航类): menu/tabs/breadcrumb/pagination\n"
    "  · interactive(交互类): modal/drawer/tooltip/popconfirm\n"
    "  · composite(复合类): form/Steps/Tree\n"
    "- explore_task: ★ 必填。意图驱动探索任务，Step4 的 AgentExplorer 在浏览器中实时执行。\n"
    '  · operation: ★ 必填。从需求原文中提取的操作描述（如"按名称搜索辅材"、"批量导入辅材"），禁止编造\n'
    '  · target_page: ★ 必填。目标页面路径（如"/dataManage/portraitData?tab=auxMaterial"），来自导航地图清单\n'
    "  · description: 人类可读的任务描述，AgentExplorer 会用此理解测试意图\n"
    '  · module: 所属模块名（如"物料管理"），用于 AgentExplorer 定位侧边栏菜单\n'
    '  · input_values: 可选。仅当需要填充输入值时填写（如 {"搜索关键词": "TEST001"}），\n'
    "    值来源优先级：yapi example > db字段约束 > 语义推断\n"
    "- checks: 检查点列表。★ 保留不变，Step4 执行 explore_task 后与 checks 比对判定 PASS/FAIL。\n"
    "  · precision=exact: 有 RAG 信息时写具体规则（SQL/API路径/DOM文本），引擎可确定性判定\n"
    "  · precision=fuzzy: 无 RAG 信息时写自然语言描述，引擎用 AI 辅助判定\n"
    '  · check: exact 写 {"sql":"SELECT...",...} / {"method":"POST","path":"/api/...",...} / {"type":"text_match","pattern":"..."}，fuzzy 写自然语言描述\n'
    '  · visual_check（视觉断言）：必须带 selector（目标元素CSS选择器）+ expectation（期望的视觉状态），\n'
    '    ★ 引擎会仅截取该控件区域做视觉特写，图更小、精度更高。selector 优先取导航地图/DOM 快照中的真实选择器，\n'
    '    否则取元素的稳定 class/id（如 .chart-bar、#main-panel）。示例：{"type":"visual_check","selector":".chart .bar","expectation":"柱子未遮挡纵坐标"}\n'
    '  · expect: ★ 必须短且具体（≤20字符）。exact写短文本如"成功"、"商品"、"保存"；fuzzy写短特征如"visible"、"正常"。禁止长句/中文逗号。\n'
    "  · track: ui=页面检查, api=接口检查, db=数据库检查\n"
    "  · ★ api track 约束：\n"
    "    1) check.type 必须是 api_response_body_check / http_status_check / api_header_check，\n"
    "       禁止用 api_call_count_check（它是页面 XHR 调用次数探测，不是接口响应验证）\n"
    "    2) expect 禁止纯数字（如 '1'/'0'）——必须写 HTTP 状态码语义（'200'/'2xx'）或字段断言语义\n"
    "       （'返回 code=0' / '响应包含 xxx' / '成功' / '正常'）。纯数字断言无法验证任何业务语义\n"
    "    3) 接口清单/数据字典类需求必须用 api_response_body_check 验证响应体字段，\n"
    "       禁止只写 status 检查（接口的核心契约是数据结构，不是状态码）\n"
)

# ── 编写规则（v4.0 意图驱动）──
_CASE_RULES = (
    "\n## 编写规则\n"
    "1. ★ 每条需求必须覆盖 4 个场景维度（按需求性质选择优先级）：\n"
    "   - happy_path（正向流程）：正常操作→预期成功，占 40-50%\n"
    "   - error_path（异常路径）：错误输入/越权/不存在资源→预期报错，占 20-30%\n"
    "   - boundary（边界值）：空值/最大值/最小值/超长/特殊字符，占 15-20%\n"
    "   - security（安全验证）：XSS/SQL注入/越权/CSRF，占 10-15%\n"
    "2. 每条需求 1-3 个测试用例，scenario_type 分别覆盖上述维度\n"
    "3. ★ RAG 上下文里有表名/接口路径 → checks 必须写具体 SQL/API 参数，标 precision=exact\n"
    "4. 没有 RAG 信息时 → 标 precision=fuzzy，check 写清楚验证什么\n"
    "5. 涉及用户输入的需求至少 1 条 security 场景 check（XSS/SQL注入），标 precision=fuzzy\n"
    "6. 不同需求间检查点禁止重复\n"
    "7. 不要输出 JSON 数组之外的解释文字\n"
    "8. ★ explore_task 编写规则（v4.0 意图驱动，替代旧 actions 数组）：\n"
    '    ★ 核心原则：不再生成具体操作步骤（navigate/click/fill），而是描述"要做什么"，\n'
    "       Step4 的 AgentExplorer 在浏览器中实时感知→决策→执行。\n"
    "    ★ explore_task 字段说明：\n"
    "    a) operation（★ 必填）：从需求原文中提取的操作描述，禁止编造。\n"
    '       示例：需求"支持按名称搜索辅材" → operation="按名称搜索辅材"\n'
    '       示例：需求"批量导入辅材" → operation="批量导入辅材"\n'
    '    b) target_page（★ 可选 — P0-3b变更）：推荐留空字符串""。\n'
    "       ★ 仅在导航地图清单中有可靠URL时填写，否则让Agent用module字段实时探索侧边栏菜单。\n"
    '       示例："/dataManage/portraitData?tab=auxMaterial"（仅当URL已确认存在时）\n'
    "       ★ 禁止编造URL！如果你不确定URL是否存在，就留空。\n"
    "    c) description：人类可读的任务描述，AgentExplorer 用此理解测试意图。\n"
    '       示例："在辅材管理页面搜索名为TEST001的辅材，验证搜索结果正确显示"\n'
    '    d) module（★ 必填 — P0-3b强化）：所属模块名（如"物料管理"、"配方管理"、"烟叶画像数据"），\n'
    "       ★ 这是Agent导航到正确页面的关键依据，必须从需求原文中提取真实模块名。\n"
    '       ★ 禁止填写文档章节标题（如"表格块：第1行"、"P3需求"等），必须是业务模块名。\n'
    "       帮助 AgentExplorer 在侧边栏菜单中定位。\n"
    "    e) input_values（可选）：仅当需要填充输入值时填写。\n"
    "       值来源优先级：yapi example > db字段约束 > 语义推断\n"
    '       示例：{"搜索关键词": "TEST001", "名称": "测试辅材"}\n'
    "    f) ★ test_method（★ 必填 — v5.4 新增）：Agent 执行的结构化测试方法，\n"
    '       替代"Agent 根据 operation 文本自由猜测"的不可靠模式。\n'
    "       格式：{\n"
    '         "navigation": "侧边栏菜单路径或相对URL",\n'
    '         "inputs": {"字段名": {"value": "测试值", "source": "yapi_example|db_constraint|semantic_infer"}},\n'
    '         "verifications": [\n'
    '           {"step": 1, "action": "fill|click|verify|wait", "target": "目标元素描述", "value": "操作值", "expect": "期望结果"}\n'
    "         ]\n"
    "       }\n"
    "    · navigation: ★ 必填。告诉Agent如何到达目标页面。\n"
    '      示例: "侧边栏→数据管理→物料管理→辅材管理" 或 "/dataManage/auxMaterial"\n'
    "    · inputs: ★ 必填(有输入时)。每个字段的测试值及来源。\n"
    "      来源优先级: yapi_example(最高) > db_constraint(字段约束生成) > semantic_infer(语义推断，兜底)\n"
    '      示例: {"搜索关键词": {"value": "TEST001", "source": "yapi_example"}, "名称": {"value": "辅材_自动测试_001", "source": "semantic_infer"}}\n'
    "    · verifications: ★ 必填。Agent按步骤执行并逐点验证。每个step含:\n"
    "      - action: fill(填充输入) / click(点击按钮) / verify(验证结果) / wait(等待)\n"
    '      - target: 目标元素描述（自然语言，如"搜索输入框"、"搜索按钮"、"搜索结果列表"）\n'
    "      - value: 操作值（fill时填输入值，click/verify时不填）\n"
    '      - expect: 期望结果（verify时填写，如"列表包含TEST001"、"显示1条记录"）\n'
    "    ★ 禁止事项：\n"
    "    - 禁止生成 actions 数组（旧格式），必须使用 explore_task\n"
    "    - 禁止编造 operation（必须从需求原文提取）\n"
    "    - 禁止编造 target_page URL（无可靠来源时留空，由Agent自主导航）\n"
    "    - 禁止硬编码输入值（如 test_xxx、@#$%），必须从 yapi/db 提取\n"
    "    - ★ 禁止省略 test_method。Agent 必须按 test_method 执行，不是自由探索。\n"
    "    · ★ 验证点必须可执行落地（v5.7，2026-08-04）：verifications 的 expect 必须能被\n"
    "      浏览器事实验证，按优先级选用：\n"
    '      (1) url_matches：导航后 URL 与功能对应（如 /pipeline 对应"流水线执行"）——最可靠\n'
    '      (2) text_contains：页面标题/关键文本包含预期词（如"流水线执行"出现在页面标题）\n'
    '      (3) element_exists：关键控件/区块存在（如"开始执行按钮"、"进度条"）\n'
    '      ★ 禁止使用"页面显示XX相关功能"式的模糊语义断言——它无法在浏览器中被事实验证，\n'
    "        必须替换为 URL 或具体文本/元素断言。\n"
    "    · ★ 导航目标映射规则（v5.8，2026-08-04）：导航地图清单给出每个页面的 URL 与页面元素时：\n"
    '      (1) 从需求描述提取功能关键词（如"流水线/执行/步骤"→流水线执行 /pipeline；"报告/图表"→可视化报告 /charts；\n'
    '          "配置/设置/参数"→项目配置 /config；"日志/运行状态"→日志面板 /log-panel；"用户/角色"→用户管理/角色管理）；\n'
    "      (2) target_page 必须填写与需求功能语义最匹配的菜单 URL；\n"
    '      (3) ★ 登录/认证类需求（需求描述含"登录/用户名/密码/认证"）无对应菜单 → target_page 留空""，\n'
    "          Agent 停留在登录场景自主处理，禁止导航到无关菜单（如文档中心）。\n"
    "    · ★ 断言绑定页面特征（v5.8）：checks 中 element_exists/text_contains 必须引用导航地图页面元素清单中\n"
    '      该页面真实存在的特征（按钮文本/输入框 placeholder/链接文本，如"重新建模"按钮、"保存配置"按钮）。\n'
    "      ★ 禁止仅用通用 element_exists(main/[role=main]) 作为唯一断言——任何页面都有 main，无法证明导航正确；\n"
    "      必须附加目标页面独有的元素断言（优先 url_matches + 页面特征元素）。\n"
    "9. ★ checks 保留不变：Step4 执行 explore_task 后，将实际结果与 checks 比对判定 PASS/FAIL。\n"
    "10. ★ S3-013 错误处理行为需求特殊规则：\n"
    '    当需求本身描述错误处理行为时（expected_feedback / expected_behavior 含"提示/错误/不允许/拒绝/失败/校验"等关键词）：\n'
    "    - 正向用例(happy_path)：触发错误条件 → 验证系统正确显示错误提示/拒绝操作\n"
    '      （例如：需求"密码长度不足时提示错误" → 正向用例=输入短密码→验证错误提示出现）\n'
    "    - 异常用例(error_path)：触发该错误的变体场景 → 验证系统仍能正确处理\n"
    "      （例如：变体=输入空密码、输入超长密码、输入特殊字符密码→验证均被正确拦截）\n"
    "    - 边界用例(boundary)：错误条件的边界值 → 验证临界点行为\n"
    "      （例如：密码最小长度=8，输入7位→报错，输入8位→通过）\n"
    '    注意：此类需求的"正向"含义是"错误处理逻辑正确执行"，而非"操作成功"\n'
    "11. ★ 场景组合断言规则（07-07）：复杂业务场景应组合多个断言覆盖完整业务流程，避免只校验单点。\n"
    "    根据需求语义识别以下场景，自动组合对应断言链：\n"
    "    - 搜索/筛选场景：loading_complete_check（等加载完成）+ list_order_check（排序正确）+ pagination_check（分页正确）\n"
    "    - 表单提交场景：api_response_body_check（API返回成功）+ db_field_value_check（DB入库正确）+ verify_toast（UI提示正确）\n"
    "    - 文件下载场景：download_check（文件存在非空）+ db_record_count_check（下载日志入库）+ api_response_body_check（API成功）\n"
    "    - 实时推送场景：websocket_check（消息内容正确）+ text_contains_check（UI显示一致）\n"
    "    - 批量删除场景：db_ui_consistency_check（UI与DB一致）+ db_record_count_check（记录数减少）+ verify_toast（提示成功）\n"
    "    - 数据展示场景：element_attribute_all_check（列表所有行属性一致）+ list_order_check（排序）+ pagination_check（分页）\n"
    "    组合原则：单条用例的 checks 数组应包含 2-4 个断言，覆盖 UI/API/DB 三个轨道中的至少 2 个；\n"
    "    简单 UI 校验（如按钮文字）仍用单断言，不必强行组合。\n"
    "12. ★ 因果图前置条件规则（S3-014）：当操作目标为 button 类控件（查询/搜索/筛选/提交/保存/确认/删除/新增/导出等）时，\n"
    "    必须分析按钮的前置条件——哪些输入字段需要填写后按钮才从 disabled 变为 enabled。\n"
    "    分析步骤：\n"
    '    a) 从需求原文和 RAG 上下文中识别该按钮关联的输入字段（如"字段名输入框"、"日期范围选择器"）。\n'
    '    b) 若数据字典中有相关字段名 → 在 fill 的 value 中使用字段名占位（如"${field_name}"），\n'
    "       后续由 Step4 执行时从 DB 自动获取真实数据填充。\n"
    "    c) 若数据字典中无相关字段 → 检查 DOM 快照中的 placeholder/text 作为参考值。\n"
    '    d) 若以上都无 → 使用通用测试数据（如"TEST001"、"测试数据"），并生成 element_enabled_check 断言验证按钮状态变化。\n'
    "    强制动作序列：fill（填充前置字段）→ click（点击按钮）→ checks（验证结果）。\n"
    "    禁止行为：直接生成只有一个 click 的查询/搜索/提交类用例（按钮必然 disabled，用例必定失败）。\n"
    "13. ★ expect 值必须短且具体（根因修复）：checks 中的 expect 字段强制规则：\n"
    "    - precision=exact 时，expect 必须是页面上真实存在的短文本（≤20字符），如'成功'、'商品'、'保存'、'正常'\n"
    "    - precision=fuzzy 时，expect 必须是可验证的简短特征描述（≤30字符），如'visible'、'正常'、'存在'\n"
    "    - ★ 禁止使用长句作为 expect（如'商品管理、订单处理、供应商管理、库存管理等核心功能正常可用'是一个句子，不是可验证的断言）\n"
    "    - ★ 禁止 expect 使用中文顿号（、）分隔多个值——每个值应单独作为一个 check\n"
    "    - ★ text_contains_check 的 expected 字段必须是一个具体的短词（如'成功'、'商品'），禁止写句子\n"
    "    - ★ 每个 check 只验证一个具体的预期，不要在一个 check 中组合多个预期\n"
    "    核心理念：一个 check = 一个可验证的事实。长句/多值/模糊断言 = 不可验证 = 测试无效。\n"
    "14. ★ 数据流验证（2026-08-09，DB 轨数据流分析验证）：对于写操作（创建/新增/添加/编辑/修改/删除等），\n"
    "    checks 必须包含三阶段数据流验证，跟踪数据从「创建→存储→展示」的完整链路：\n"
    "    - 阶段1（UI 反馈）：操作后页面显示成功提示 → check 使用 text_contains 验证「成功」关键词\n"
    "    - 阶段2（DB 持久化）：数据已写入数据库 → check 使用 db_record_exists_check 验证数据存在\n"
    "    - 阶段3（UI 回读）：页面列表页展示新数据 → check 使用 text_contains 验证创建数据的值出现在页面中\n"
    "    示例 checks 结构：\n"
    '    [\n'
    '      {"track": "ui", "precision": "exact", "check": {"type": "text_contains"}, "expect": "成功", "description": "阶段1: UI 操作成功提示验证", "data_flow_stage": "ui_feedback"},\n'
    '      {"track": "db", "precision": "exact", "check": {"type": "db_record_exists_check", "table": "product"}, "expect": "数据已写入DB", "description": "阶段2: DB 持久化验证", "data_flow_stage": "db_persistence"},\n'
    '      {"track": "ui", "precision": "exact", "check": {"type": "text_contains"}, "expect": "{created_value}", "description": "阶段3: UI 回读展示验证", "data_flow_stage": "ui_readback"}\n'
    "    ]\n"
    "    ★ 注意：data_flow_stage 字段用于标识数据流阶段，引擎按阶段顺序执行验证。\n"
    "    ★ 读操作（查询/搜索/查看/浏览）不涉及数据写入，不需要数据流验证。\n"
    "15. ★ expect 值必须精确（2026-08-09，意图驱动精细化）：verifications 的 expect 必须是精确值，禁止使用描述性文本：\n"
    '    - 正确：expect="创建成功"、expect="删除成功"、expect="列表包含TEST001"\n'
    '    - 正确：expect="保存成功"、expect="提示已删除"、expect="页面显示1条记录"\n'
    '    - 错误：expect="场景主流程操作成功"（模糊描述，不可验证）\n'
    '    - 错误：expect="页面显示XX相关功能"（模糊语义，无法在浏览器中验证）\n'
    '    - 错误：expect="有效等价类操作成功"（方法论术语，不是页面可观察结果）\n'
    "    ★ 每个 verification 必须有完整的 action/target/value/expect 四个字段，不允许缺失。\n"
    "    ★ 输入值必须有具体示例（如 TEST001、测试辅材、test@example.com），禁止使用占位符如 \"{valid_value}\" 或空字符串。\n"
    "16. ★ 测试用例标准化字段（2026-08-09，IEEE 829 标准）：每个用例必须包含以下三个字段：\n"
    "    - precondition（前置条件）：编号列表，如「1.系统已登录 2.测试数据已准备」\n"
    '    - test_steps（测试步骤）：JSON 数组，描述操作步骤序列，如 [{"step":1,"action":"打开页面"},{"step":2,"action":"输入查询条件","data":"TEST001"}]\n'
    "    - expected_result（预期结果）：精确描述操作后的预期状态，如「页面显示搜索结果为TEST001的记录」\n"
    "    ★ 这三个字段是 IEEE 829 标准测试用例的必备要素，必须生成。\n"
    "    ★ 向后兼容：如果这些字段没有显式值，从 explore_task.test_method.verifications 和 checks 自动提取生成。\n"
    "17. ★ visual_check 必须附带目标选择器（2026-09-01，控件级视觉特写）：\n"
    "    - 只有当需要校验 DOM 拿不到的视觉状态（颜色/遮挡/对齐/缺失/重叠等）时才生成 visual_check\n"
    '    - ★ 必须带字段：selector（目标元素CSS选择器）+ expectation（期望的视觉状态，如"柱子未遮挡纵坐标"、\"保存按钮为红色\"）\n'
    "    - ★ selector 优先取导航地图/DOM 快照中该页面真实元素的选择器；无真实选择器时取稳定 class/id，禁止留空\n"
    "    - ★ 引擎据此仅截取该控件区域做视觉特写（图更小→更快更准）；缺 selector 则回退整视口截图，精度下降\n"
    '    正确：{"type":"visual_check","selector":".chart .bar","expectation":"柱子未遮挡纵坐标"}\n'
    '    错误：{"type":"visual_check","expectation":"图表正常"}（缺 selector，且期望模糊不可判定）\n'
)

# ── 组合断言 Few-Shot 示例（v4.0 意图驱动）──
_COMBO_FEW_SHOT = (
    "\n## 组合断言示例（参考写法，勿直接复制）\n"
    "示例 1（搜索场景，explore_task + 3 个断言组合覆盖 UI+DB）：\n"
    "{\n"
    '  "case_id": "TC-001", "req_id": "REQ-001", "title": "按名称搜索订单并验证列表",\n'
    '  "scenario_type": "happy_path", "test_method": "FD", "control_type": "input",\n'
    '  "risk_level": "P1", "type": "功能",\n'
    '  "explore_task": {\n'
    '    "operation": "按名称搜索订单",\n'
    '    "target_page": "/order/list",\n'
    '    "description": "在订单管理页面搜索包含手机关键词的订单，验证搜索结果",\n'
    '    "module": "订单管理",\n'
    '    "input_values": {"订单名称": "手机"},\n'
    '    "test_method": {\n'
    '      "navigation": "侧边栏→订单管理",\n'
    '      "inputs": {"订单名称": {"value": "手机", "source": "semantic_infer"}},\n'
    '      "verifications": [\n'
    '        {"step": 1, "action": "fill", "target": "订单名称输入框", "value": "手机", "expect": ""},\n'
    '        {"step": 2, "action": "click", "target": "搜索按钮", "value": "", "expect": ""},\n'
    '        {"step": 3, "action": "verify", "target": "", "value": "", "expect": "列表包含手机相关订单"}\n'
    "      ]\n"
    "    }\n"
    "  },\n"
    '  "checks": [\n'
    '    {"track": "ui", "precision": "exact",\n'
    '     "check": {"type": "loading_complete_check", "loading_selector": ".ant-spin", "expected_state": "hidden", "timeout_ms": 10000},\n'
    '     "expect": "加载完成"},\n'
    '    {"track": "ui", "precision": "exact",\n'
    '     "check": {"type": "list_order_check", "selector": "table tbody tr", "value_selector": "td:nth-child(3)", "data_type": "numeric", "order": "desc"},\n'
    '     "expect": "列表按金额降序排列"},\n'
    '    {"track": "db", "precision": "exact",\n'
    '     "check": {"type": "db_ui_consistency_check", "ui_selector": "table tbody tr", "db_table": "orders", "db_where": "name LIKE \'%手机%\'", "expected_relation": "eq"},\n'
    '     "expect": "UI 显示条数 = DB 查询条数"}\n'
    "  ]\n"
    "}\n\n"
    "示例 2（表单提交场景，explore_task + 3 个断言组合覆盖 UI+API+DB）：\n"
    "{\n"
    '  "case_id": "TC-002", "req_id": "REQ-002", "title": "新增用户并验证入库",\n'
    '  "scenario_type": "happy_path", "test_method": "FD", "control_type": "composite",\n'
    '  "risk_level": "P0", "type": "功能",\n'
    '  "explore_task": {\n'
    '    "operation": "新增用户",\n'
    '    "target_page": "/user/list",\n'
    '    "description": "在用户管理页面点击新增按钮，填写用户名和邮箱后保存，验证入库成功",\n'
    '    "module": "用户管理",\n'
    '    "input_values": {"用户名": "testuser", "邮箱": "test@example.com"},\n'
    '    "test_method": {\n'
    '      "navigation": "侧边栏→用户管理",\n'
    '      "inputs": {"用户名": {"value": "testuser", "source": "semantic_infer"}, "邮箱": {"value": "test@example.com", "source": "semantic_infer"}},\n'
    '      "verifications": [\n'
    '        {"step": 1, "action": "click", "target": "新增用户按钮", "value": "", "expect": ""},\n'
    '        {"step": 2, "action": "fill", "target": "用户名输入框", "value": "testuser", "expect": ""},\n'
    '        {"step": 3, "action": "fill", "target": "邮箱输入框", "value": "test@example.com", "expect": ""},\n'
    '        {"step": 4, "action": "click", "target": "保存按钮", "value": "", "expect": ""},\n'
    '        {"step": 5, "action": "verify", "target": "", "value": "", "expect": "提示保存成功"}\n'
    "      ]\n"
    "    }\n"
    "  },\n"
    '  "checks": [\n'
    '    {"track": "api", "precision": "exact",\n'
    '     "check": {"type": "api_response_body_check", "url_pattern": "/api/user/create", "expected_field": "code", "expected_value": 0, "operator": "eq"},\n'
    '     "expect": "API 返回 code=0 表示成功"},\n'
    '    {"track": "db", "precision": "exact",\n'
    '     "check": {"type": "db_field_value_check", "table": "users", "field": "username", "where": "username=\'testuser\'", "expected": "testuser", "operator": "eq", "tolerance": 0},\n'
    '     "expect": "DB users 表中 username=testuser"},\n'
    '    {"track": "ui", "precision": "exact",\n'
    '     "check": {"type": "text_contains_check", "selector": ".toast-message", "expected": "成功,保存成功", "operator": "contains"},\n'
    '     "expect": "Toast 提示包含成功"}\n'
    "  ]\n"
    "}\n\n"
    "示例 3（视觉缺陷场景，visual_check 必须带 selector 做控件级视觉特写）：\n"
    "{\n"
    '  "case_id": "TC-003", "req_id": "REQ-003", "title": "验证柱状图柱子未遮挡纵坐标",\n'
    '  "scenario_type": "happy_path", "test_method": "SD", "control_type": "display",\n'
    '  "risk_level": "P2", "type": "UI",\n'
    '  "explore_task": {\n'
    '    "operation": "查看柱状图数据展示",\n'
    '    "target_page": "/charts",\n'
    '    "description": "导航到图表页，打开月度销售柱状图，验证柱子不被纵坐标遮挡",\n'
    '    "module": "可视化报告",\n'
    '    "input_values": {},\n'
    '    "test_method": {\n'
    '      "navigation": "/charts",\n'
    '      "inputs": {},\n'
    '      "verifications": [\n'
    '        {"step": 1, "action": "click", "target": "月度销售柱状图", "value": "", "expect": ""},\n'
    '        {"step": 2, "action": "verify", "target": "图表柱形区域", "value": "", "expect": "柱子完整显示"}\n'
    "      ]\n"
    "    }\n"
    "  },\n"
    '  "checks": [\n'
    '    {"track": "ui", "precision": "exact",\n'
    '     "check": {"type": "visual_check", "selector": ".chart-plot .bar", "expectation": "柱子未遮挡纵坐标，整体清晰完整"},\n'
    '     "expect": "柱子无遮挡"}\n'
    "  ]\n"
    "}\n"
)

# ── 测试方法指南 ──
_TEST_METHOD_GUIDE = (
    "\n## 测试覆盖维度参考\n"
    "1. 场景法(FD) — 模拟用户端到端操作流程（actions 覆盖主要路径）\n"
    "2. ★ 等价类(EP)+边界值(BV)（S3-004）— 每个输入字段必须分析：\n"
    "   - 有效等价类：正常值范围内的典型值\n"
    "   - 无效等价类：空值、超长、非法字符、类型错误\n"
    "   - 边界值：最小值-1、最小值、最小值+1、最大值-1、最大值、最大值+1\n"
    "   - 生成 boundary 场景用例覆盖上述边界值\n"
    "3. 错误推测(EG) — 空格、特殊字符、并发、超长字符串\n"
    "4. 安全覆盖(SX/SI) — 涉及用户输入的功能至少 1 条 security 场景 check（XSS/SQL注入）\n"
    "5. 因果图(CE) — 分析输入条件之间的因果关系，绘制因果图后转换为判定表\n"
    "6. 判定表(DT) — 枚举条件组合矩阵，每列对应一个动作\n"
    "7. 状态迁移(ST) — 识别状态节点+迁移事件，覆盖所有合法迁移+非法迁移\n"
    "8. 正交实验(OT) — 多因素组合用正交表筛选（L4/L8/L9等），减少用例数量\n"
    "9. 路径覆盖(PC) — 分析控制流图，覆盖所有独立路径\n"
    "10. 规范导出(SD) — 直接从需求规范推导用例，原文是事实源\n"
    "\n"
    "## ★ 控件类型→测试方法论智能匹配表（07-05h）\n"
    "★ 核心原则：不同控件类型应用不同测试方法论，不能强行对按钮做SQL注入\n"
    "\n"
    "| 控件类型 | 适用测试方法论 | 不适用方法论 |\n"
    "|:---|:---|:---|\n"
    "| input(输入类) | EP+BV+EG+SX+SI+SD | DT/ST/OT/PC |\n"
    "|   触发：text/number/password/email/textarea/date/search |\n"
    "|   重点：边界值(空值/最大值/特殊字符) + XSS + SQL注入 |\n"
    "| select(选择类) | EP+EG+SD | BV/SX/SI/DT |\n"
    "|   触发：select/radio/checkbox/cascader/transfer |\n"
    "|   重点：默认值/未选择/多选上限/互斥选项 |\n"
    "| button(按钮类) | FD+CE+SD | BV/SX/SI/EP |\n"
    "|   触发：button/submit/link |\n"
    "|   重点：场景法(点击流程) + 因果图(前置条件) |\n"
    "| upload(上传类) | EP+BV+EG+SX |\n"
    "|   触发：upload/dragger |\n"
    "|   重点：文件大小边界 + 类型边界 + 文件内容XSS |\n"
    "| display(展示类) | SD+FD | BV/SX/SI/EP |\n"
    "|   触发：table/list/tag/badge/card |\n"
    "|   重点：数据展示正确性 + 分页/排序场景 |\n"
    "| nav(导航类) | FD+ST+PC | BV/SX/SI/EP |\n"
    "|   触发：menu/tabs/breadcrumb/pagination |\n"
    "|   重点：导航流程 + 状态迁移 + 路径覆盖 |\n"
    "| interactive(交互类) | FD+ST+EG | BV/SX/SI |\n"
    "|   触发：modal/drawer/tooltip/popconfirm |\n"
    "|   重点：打开/关闭状态 + 错误推测 |\n"
    "| composite(复合类) | FD+CE+DT+ST+PC | BV/SX/SI |\n"
    "|   触发：form/Steps/Tree |\n"
    "|   重点：场景法 + 因果图 + 判定表 + 状态迁移 + 路径覆盖 |\n"
    "\n"
    "★ 控件识别规则（按 operation 关键词判断）：\n"
    '- input: 操作含"输入/填写/录入/编辑(文本)/设置(值)" + 文本类\n'
    '- select: 操作含"选择/勾选/下拉/单选/多选/切换"\n'
    '- button: 操作含"点击/提交/保存/确认/取消/删除/新增/导出/搜索"\n'
    '- upload: 操作含"上传/拖拽/导入文件"\n'
    '- display: 操作含"查看/浏览/展示/显示/列表"\n'
    '- nav: 操作含"跳转/导航/切换页/进入页面/返回"\n'
    '- interactive: 操作含"打开弹窗/关闭弹窗/悬浮提示/确认弹窗"\n'
    '- composite: 操作含"提交表单/多步骤流程/树形操作"\n'
    "\n"
    "## ★ 原文上下文使用指南（07-05g）\n"
    'user_message 中可能包含"所属切片原文(≤600字)"字段，这是需求文档的原文片段。\n'
    "使用规则：\n"
    "- 优先从原文中提取测试数据线索（如字段长度限制、格式要求、业务规则）\n"
    "- 基于原文中的业务规则设计等价类和边界值（而非凭空猜测）\n"
    '- 若原文中明确了字段约束（如"用户名3-20字符"），boundary 用例必须覆盖这些边界\n'
    '- 若原文中描述了业务流程（如"先A再B最后C"），场景法用例必须按此流程构造\n'
    '- 若原文中提到了权限要求（如"需管理员权限"），security 用例必须覆盖越权场景\n'
    "- 原文是事实源：当 operation/expected_feedback 与原文冲突时，以原文为准\n"
    "\n"
    "## ★ risk_level 判定标准（S3-005）\n"
    "- P0: 核心业务流程、涉及数据写入/删除/修改、安全漏洞、权限绕过\n"
    "- P1: 错误处理、异常输入、非核心功能流程\n"
    "- P2: 边界值验证、UI展示、非关键路径、兼容性\n"
    "\n"
    "## ★ precision 边界值判定（S3-006）\n"
    "对于数值精度场景，按以下规则判定 precision：\n"
    "- 金额/价格: 保留2位小数，边界值 ±0.01 需验证\n"
    "- 百分比: 0%~100% 闭区间，0 和 100 为合法边界\n"
    "- 数量: 非负整数，0 为合法边界（需与业务确认是否允许 0）\n"
    "- 浮点运算: 容忍 1e-9 误差，NaN/Inf 需单独拒收\n"
    '- 空值: None/null/undefined/空字符串 需区分"未设置"与"设置为空"两种语义\n'
    "- 字符串长度: 0字符(空串)、1字符、max-1、max、max+1 五个边界\n"
    "- 数组: 空数组[]、单元素、max-1、max、max+1 五个边界\n"
)
