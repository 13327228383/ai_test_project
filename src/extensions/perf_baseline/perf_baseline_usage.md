# 性能门禁（perf_gate）使用说明

> **状态标注**：本模块由子模型实现，**[未复核-需 GLM/DeepSeek 把关]**。
> 单测已全绿，但**未经真实靶子 / 真实 Playwright 端到端验证**，不得视为"已验证通过"。

---

## 1. 这是什么

给 E2E 测试引擎补上「性能会不会越跑越慢」的自动判定能力：

```
step4 执行用例
   └─ PerfCollectorPlugin  采集 Web / API 指标
         └─ PerfContext    按 run_id 暂存（内存 + jsonl 兜底）
step5 生成报告
   └─ _perf_gate_report    取样本 → 加载规则 → 对比基线 → 门禁判定
         └─ 报告区块「性能门禁（基线对比）」+ data/perf_reports/*.json
```

**与既有两条链路的区别（三者互不干扰）**：

| 链路 | 位置 | 干什么 |
|---|---|---|
| `performance_monitor` 插件 | step4 | 实时硬编码阈值告警 → `ai_health_metrics` |
| `step5/perf_baseline.py` | step5 | MySQL `oracle_results` → 质量面板（**同名不同物，勿改**）|
| **`perf_gate`（本模块）** | step4+step5 | 规则驱动的基线对比门禁（新增） |

---

## 2. 快速开启

默认**全关**（`PERF_GATE_ENABLED: false`），开启只需一步：

```yaml
# src/core/config.yaml
PERF_GATE_ENABLED: true          # ← 改成 true

step4_plugins:
  perf_collector:
    enabled: true                # 采集插件（默认已开，仅写数据不判定）
    spill: true                  # 样本双写 data/perf_runtime/<run_id>.jsonl
```

两个开关是**分离**的：
- 只开 `perf_collector` → 只采集、不判定、报告无任何变化（可先跑几轮攒基线）
- 再开 `PERF_GATE_ENABLED` → 报告出现「性能门禁」区块

---

## 3. 判定规则（★ 唯一实现在 `gate.py::evaluate_gate`）

**严格按优先级短路，顺序不可调整**：

| # | 条件 | 终态 | 阻断 |
|---|---|---|---|
| 1 | `sample.error` 非空 或 `metrics` 为空 | `NO_DATA` | 否（但**不计入 PASS**）|
| 2 | 任一指标 > 绝对阈值 | `FAIL` | ✅ |
| 3 | 该 key 无基线（首次见到） | `BASELINE_ESTABLISHED` | 否（**不计入 PASS**）|
| 4 | 任一指标相对基线上浮 ≥ `fail_pct`(100%) | `FAIL` | ✅ |
| 5 | 任一指标上浮 ∈ [`pre_gate_pct`(80%), `fail_pct`) | `PRE_GATE` | 否（告警）|
| 6 | 其余 | `PASS` | — |

补充约定：
- `delta_pct = (current - baseline_mean) / baseline_mean * 100`
- `baseline_mean <= 0` → 跳过该指标相对判定（除零保护）
- `status_code` / `resource_count` **只做绝对校验，不做相对**（枚举/计数型无"退化"语义）
- **fail-closed**：采集失败不会变成"没问题"，一律 `NO_DATA` 并在报告里显式列出
- `evaluate_gate` 遇非法入参**返回 `NO_DATA` 而非抛异常**——这是本模块唯一的异常表达方式

---

## 4. 配置规则：`perf_rules.yaml`

**不写死靶子**。代码只认 metric key，靶子差异全部由 yaml 承担。

```yaml
version: 1

defaults:                        # 所有靶子的基准
  web:
    metrics: [ttfb_ms, dcl_ms, load_ms, resource_count, resource_bytes, longtask_ms, cls_approx]
    thresholds:
      ttfb_ms: 800
      load_ms: 5000
      # ...
  api:
    metrics: [elapsed_ms, status_code, payload_bytes]
    thresholds:
      elapsed_ms: 1500
  relative:
    pre_gate_pct: 80
    fail_pct: 100

targets:                         # 逐靶子覆盖，与 defaults **递归深合并**
  smartshop:
    web:
      thresholds: {load_ms: 4500}      # 只改这一项，其余沿用 defaults
  api_demo:
    web:
      metrics: []                      # ← 空列表整组关停（list 整体覆盖）
```

合并语义：
- **dict 递归合并**，叶子值以 `targets.<name>` 为准
- **list 整体覆盖** → `metrics: []` 即关停该组
- 未在 `targets` 中声明的靶子 → 直接用 `defaults`
- `thresholds` 会被**裁剪到 `metrics` 列表内**（未启用的指标阈值不进最终规则）

### 新增一个靶子

只改 yaml，**不动任何一行 Python**：

```yaml
targets:
  my_new_target:
    web:
      thresholds: {load_ms: 12000}     # 这个系统天生慢，放宽
    api:
      metrics: [elapsed_ms]            # 只看接口耗时
```

`load_rules()` **绝不抛异常**：文件缺失 / YAML 语法错 / 顶层不是 dict → 一律回落内置 defaults 并打 warning。

---

## 5. 基线文件

路径：`src/extensions/perf_baseline/baselines/<target>.json`

```json
{
  "schema_version": 1,
  "target": "smartshop",
  "baseline_version": 3,
  "runs_aggregated": 3,
  "entries": {
    "web::/product/{id}": {
      "kind": "web",
      "samples": 3,
      "metrics": {
        "load_ms": {"mean": 1500.0, "p50": 1480.0, "p95": 1620.0,
                    "min": 1400.0, "max": 1620.0, "samples": 3, "values": [...]}
      }
    }
  }
}
```

要点：
- **按靶子分文件**，互不干扰
- 每次成功 `update()` → `baseline_version + 1`
- **原子写**（`tmp + os.replace`），进程被杀不会留半截 JSON
- 文件损坏 → 按空基线处理并 warning，**不阻断流水线**
- 保留滚动窗口 `values`（上限 200），p50/p95 每次**精确重算**而非近似
- **只有 `is_valid()` 的样本参与聚合**，采集失败的样本绝不污染基线
- **基线更新发生在判定之后**，本轮样本不会污染自己的对比基准

### 重置基线

靶子做了大改版、旧基线失去意义时：

```python
from src.extensions.perf_baseline import BaselineManager
BaselineManager("smartshop").reset()      # 清空 entries，下轮重新建立
```

或者直接删掉 `baselines/smartshop.json`。

---

## 6. URL 归一化（key 的生成方式）

同一业务页面 / 接口在不同用例里 URL 各不相同，必须归一才能对齐基线：

```
http://localhost:3000/product/42?ref=home  →  web::/product/{id}
GET http://api.x.com/v1/users/8f3e-...-9c   →  api::GET /v1/users/{id}
```

规则（`schemas.normalize_key`）：
1. 丢弃 host、query、fragment（换端口 / 换环境不影响对齐）
2. 去尾部斜杠
3. **段级**替换：纯数字 / UUID / 32 位 hex → `{id}`
4. 输出 `web::<path>` 或 `api::<METHOD> <path>`

---

## 7. 采集口径

**Web**（`collect_web_async`）：

| 指标 | 来源 |
|---|---|
| `ttfb_ms` / `dcl_ms` / `load_ms` | W3C Navigation Timing |
| `resource_count` / `resource_bytes` | Resource Timing |
| `longtask_ms` / `cls_approx` | 注入的 `PerformanceObserver` 累加值 |

**API**（`collect_api`）：复用 step4 `ApiTracker` 的 `page.on('response')` 记录，
归一为 `elapsed_ms` / `status_code` / `payload_bytes`。同一 key 多次调用**取每个指标的最大值**
（最坏情况代表该业务点的性能上界）。

> ⚠️ **async Playwright**：本仓库用的是 `playwright.async_api`，`page.evaluate` / `page.add_init_script`
> 都返回协程。采集器因此提供**同步 + 异步双入口**：
> - `install` / `collect_web` —— 同步 Page（单测 fake page、`sync_api`）
> - `install_async` / `collect_web_async` —— async Page（**插件走这条**）
>
> 同步入口误用于 async Page 时会返回带 `error` 的样本（→ `NO_DATA`），**不会静默变成"没数据=通过"**。

---

## 8. 插件挂载点（★ 与设计文档的偏离，务必知悉）

设计文档写的是 `ON_NAVIGATE` 钩子。**经全仓库核查，`PluginHook.ON_NAVIGATE` 从未被任何代码 emit**
（enum 与 `BasePlugin` 空实现存在，`performance_monitor.on_navigate` 同样是死代码）。
照抄设计文档会导致插件永远不执行，因此改挂三个**真实触发且 await**的钩子：

| 钩子 | 触发处 | 做什么 |
|---|---|---|
| `on_before_intent` | `_base.py:181` | `install_async(page)` 注入 PerformanceObserver |
| `on_after_step` | `_base.py:681` | 页面 URL key 变化时采一次 Web 指标 |
| `on_after_intent` | `_base.py:1057` | 补采终态页 Web 指标 + 采集 ApiTracker 增量 |

`on_navigate` 仍保留实现，将来有人 emit 即可生效。

> ⚠️ 所有 hook 必须是 `async def`：`PluginManager.emit`（同步）会直接 `close()` 掉协程，
> 只有 `execute_hook`（async）会 `await`。同一 `(run_id, web_key)` 每轮只采一次，避免报告噪声。

---

## 9. run_id 一致性（最容易踩的坑）

采集侧（step4）与判定侧（step5）**必须推导出同一个 run_id**，否则 `drain()` 永远取不到样本。

两侧统一逻辑：`ctx.run_id` → `versioning.current_run_id(target)` → `"default"`。
`current_run_id` 由 `project + active version_label` 确定性推导，同一 session 必然同值。
`tests/extensions/test_perf_e2e.py::test_run_id_resolution_is_consistent_between_step4_and_step5`
是这条约束的回归护栏。

---

## 10. 产物

| 产物 | 路径 |
|---|---|
| 基线 | `src/extensions/perf_baseline/baselines/<target>.json` |
| 结构化报告 | `data/perf_reports/perf_report_<run_id>.json` |
| 样本兜底 | `data/perf_runtime/<run_id>.jsonl`（drain 后自动删除）|
| Markdown 区块 | 测试报告中的「性能门禁（基线对比）」|

Markdown 区块包含：门禁总览表 / 违规明细表 / **NO_DATA 明细表**（fail-closed 必须显式列出）/ 本轮新建基线清单。

---

## 11. 典型使用流程

```bash
# 第 1 轮：建立基线（报告里全是 BASELINE_ESTABLISHED，零 FAIL —— 不误报）
PERF_GATE_ENABLED=true 跑一次完整流水线

# 第 2..N 轮：正常对比
#   - 无劣化      → PASS
#   - 上浮 85%    → PRE_GATE（黄，只告警）
#   - 上浮 120%   → FAIL（红，blocking=true）
#   - 采集挂了    → NO_DATA（不算通过，报告里点名）
```

代码里直接调用：

```python
from src.pipeline.steps.step5._perf_gate_report import run_perf_gate

report = run_perf_gate("smartshop", run_id="")   # run_id 留空则自动推导
print(report["gate_summary"])
# {'total': 12, 'pass': 9, 'pre_gate': 1, 'fail': 1,
#  'baseline_established': 1, 'no_data': 0, 'blocking': True}
```

---

## 12. 排错

| 现象 | 排查方向 |
|---|---|
| 报告没有性能门禁区块 | `PERF_GATE_ENABLED` 是否为 true；日志搜 `[perf_gate]` |
| `无采集样本，跳过门禁` | `step4_plugins.perf_collector.enabled` 是否 true；run_id 两侧是否一致 |
| 全是 `NO_DATA` | 看样本 `error` 字段；多半是页面未完成导航或 `page.evaluate` 失败 |
| 全是 `BASELINE_ESTABLISHED` | 首轮正常；若持续如此，检查 URL 归一化后 key 是否每轮都变 |
| 误报 FAIL | 先看是 absolute 还是 relative；absolute 调 `perf_rules.yaml` 阈值，relative 说明确有退化 |

所有日志前缀统一为 `[perf_gate]`。

---

## 13. 强模型复核项（逐项已收尾，结论见 audits/full_repo_audit_2026-09-04.md §5.2）

> **状态（2026-09-05）**：下述 1-10 项已由 GLM/DeepSeek 强模型逐项复核并给出明确结论
> （等效已实证 / 可接受 / 已校准 / 保留 / 决策），非悬空待办。以下保留原列便于追溯，
> 结论以审计文档为准。

1. **插件挂载点的偏离**：`ON_NAVIGATE` → `before_intent`/`after_step`/`after_intent` 的替代方案是否语义等价、是否遗漏页面。
2. **`after_step` 采集的性能开销**：每步一次 `page.evaluate`（URL key 未变时跳过）对用例执行耗时的影响未实测。
3. **相对判定阈值 80% / 100%** 是否合理，需真实靶子多轮数据校准。
4. **`resource_bytes` 依赖 `transferSize`**：跨域资源在部分浏览器下恒为 0，可能低估。
5. **`cls_approx` 是近似值**：`buffered:true` 只能补齐同文档内条目，首屏 CLS 可能少计。
6. **基线滚动窗口 200** 的取值，以及 p95 在样本数 < 20 时的统计意义。
7. **`collect_api` 取最大值**的聚合策略是否会被单次网络抖动放大。
8. **`api_tracker.py` 新增 `resp_bytes`**：`len(body.encode('utf-8'))` 与真实传输字节（压缩后）有差异。
9. **基线文件是否应入库**（当前随仓库走），CI 干净工作区场景需确认。
10. **并发/并行执行**（`parallel.py`）下 `PerfContext` 单例与 run_id 隔离是否足够。
