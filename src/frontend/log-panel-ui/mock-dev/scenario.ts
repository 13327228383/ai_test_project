// ─────────────────────────────────────────────────────────────
// 开发期 mock 剧本（P0-4：已从 src/mock/ 迁出，脱离生产构建图）
//
// ★ 本文件不在 tsconfig.include（["src","api"]）内，也不被任何静态 import 引用，
//   仅由 src/store/logStore.ts 在 import.meta.env.DEV && VITE_USE_MOCK=true 时
//   通过「变量说明符 + @vite-ignore」的动态 import 加载。
// ★ 因此生产 bundle 中不会出现本文件的任何字节（验收：grep 'mock|scenario' = 0）。
// ★ 禁止在此写入任何真实内网域名 / IP / 凭证，占位符统一用 example.com。
// ─────────────────────────────────────────────────────────────
import type { LogEntry, LogSegment, LogLevel } from "../src/types/log";

// 构建消息片段的辅助函数
const seg = (text: string, type?: LogSegment["type"]): LogSegment => ({ text, type });
const segs = (...parts: (string | LogSegment)[]): LogSegment[] =>
  parts.map((p) => (typeof p === "string" ? { text: p } : p));

let idCounter = 0;
const nextId = () => `log-${++idCounter}`;

// 把 [HH:mm:ss.SSS] 字符串推进指定毫秒
function advanceTime(base: Date, ms: number): Date {
  return new Date(base.getTime() + ms);
}
function formatTs(d: Date): string {
  const pad = (n: number, l = 2) => String(n).padStart(l, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(
    d.getMilliseconds(),
    3,
  )}`;
}

// 一条流水线完整剧本：每项 = { level, source, step?, segments, dt }
// dt = 距离上一条的毫秒数
interface ScenarioBeat {
  level: LogLevel;
  source: string;
  step?: 1 | 2 | 3 | 4 | 5;
  isError?: boolean;
  segments: LogSegment[];
  dt: number;
}

const SCENARIO: ScenarioBeat[] = [
  {
    level: "INFO",
    source: "系统",
    segments: segs(
      seg("E2E 智能测试引擎", "highlight"),
      " v4.0.0 启动，环境: ",
      seg("TEST--V0.0.1", "value"),
    ),
    dt: 0,
  },
  {
    level: "INFO",
    source: "Step1",
    step: 1,
    segments: segs("开始拉取数据源: ", seg("3", "value"), " 个配置源"),
    dt: 31,
  },
  {
    level: "INFO",
    source: "API",
    segments: segs(
      "GET http://docs.example.com:3000/api/v1/documents → ",
      seg("200", "value"),
      " ",
      seg("(238ms)", "dim"),
    ),
    dt: 88,
  },
  {
    level: "INFO",
    source: "API",
    segments: segs(
      "GET http://yapi.example.com/api/interface/list → ",
      seg("200", "value"),
      " ",
      seg("(156ms)", "dim"),
    ),
    dt: 420,
  },
  {
    level: "INFO",
    source: "DB",
    segments: segs(
      "MySQL schema 读取: ",
      seg("42", "value"),
      " 张表, ",
      seg("318", "value"),
      " 个字段",
    ),
    dt: 380,
  },
  {
    level: "SUCCESS",
    source: "Step1",
    step: 1,
    segments: segs(
      "数据拉取完成: ",
      seg("5", "value"),
      " 个文档, ",
      seg("12,847", "value"),
      " 字符",
    ),
    dt: 885,
  },
  {
    level: "INFO",
    source: "Step2",
    step: 2,
    segments: segs(
      "开始需求拆解: AI 模型 ",
      seg("deepseek-v4-pro", "value"),
    ),
    dt: 16229,
  },
  {
    level: "INFO",
    source: "AI",
    segments: segs(
      "model=",
      seg("deepseek-v4-pro", "value"),
      ", input=",
      seg("14,150", "value"),
      ", output=",
      seg("8,920", "value"),
      " ",
      seg("tokens", "dim"),
    ),
    dt: 222,
  },
  {
    level: "INFO",
    source: "RAG",
    segments: segs(
      "YApi 契约检索: 命中 ",
      seg("23", "value"),
      " 接口, DB Schema 检索: 命中 ",
      seg("42", "value"),
      " 表",
    ),
    dt: 1100,
  },
  {
    level: "SUCCESS",
    source: "Step2",
    step: 2,
    segments: segs(
      "拆解完成: ",
      seg("18", "value"),
      " 条需求条目, 平均置信度 ",
      seg("0.87", "value"),
    ),
    dt: 11333,
  },
  {
    level: "INFO",
    source: "Step2",
    step: 2,
    segments: segs(
      "防幻觉核对: 全句子串匹配通过 ",
      seg("15", "value"),
      " 条, NLI 蕴含 ",
      seg("3", "value"),
      " 条",
    ),
    dt: 210,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("开始用例建模", "highlight"),
      ": 共 18 条需求条目待处理",
    ),
    dt: 7812,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(
      "开始建模: ",
      seg("REQ-001", "value"),
      " 用户登录验证",
    ),
    dt: 122,
  },
  {
    level: "INFO",
    source: "AI",
    segments: segs(
      "model=",
      seg("deepseek-v4-pro", "value"),
      ", input=",
      seg("1,415", "value"),
      ", output=",
      seg("1,024", "value"),
      " ",
      seg("tokens", "dim"),
    ),
    dt: 333,
  },
  {
    level: "SUCCESS",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("REQ-001", "value"),
      " 建模完成: ",
      seg("3", "value"),
      " actions, ",
      seg("5", "value"),
      " checks ",
      seg("(1.7s)", "dim"),
    ),
    dt: 1667,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(
      "开始建模: ",
      seg("REQ-002", "value"),
      " 商品搜索功能",
    ),
    dt: 111,
  },
  {
    level: "SUCCESS",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("REQ-002", "value"),
      " 建模完成: ",
      seg("4", "value"),
      " actions, ",
      seg("6", "value"),
      " checks ",
      seg("(2.3s)", "dim"),
    ),
    dt: 2333,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(
      "开始建模: ",
      seg("REQ-003", "value"),
      " 订单提交流程",
    ),
    dt: 222,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("REQ-003", "value"),
      " 建模中... AI推理耗时 ",
      seg("1.3s", "metric"),
    ),
    dt: 1311,
  },
  {
    level: "SUCCESS",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("REQ-003", "value"),
      " 建模完成: ",
      seg("5", "value"),
      " actions, ",
      seg("8", "value"),
      " checks ",
      seg("(3.1s)", "dim"),
    ),
    dt: 1100,
  },
  {
    level: "ERROR",
    source: "Step3",
    step: 3,
    isError: true,
    segments: segs(
      seg("REQ-011", "value"),
      " JSON解析失败: ",
      seg("expected '}'", "highlight"),
      " at line 47, col 12",
    ),
    dt: 1300,
  },
  {
    level: "ERROR",
    source: "Step3",
    step: 3,
    isError: true,
    segments: segs(
      seg("REQ-011", "value"),
      " 第 ",
      seg("1/3", "highlight"),
      " 轮重试",
    ),
    dt: 390,
  },
  {
    level: "SUCCESS",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("REQ-011", "value"),
      " 第 1 轮重试成功: ",
      seg("4", "value"),
      " actions, ",
      seg("6", "value"),
      " checks",
    ),
    dt: 1233,
  },
  {
    level: "ERROR",
    source: "Step3",
    step: 3,
    isError: true,
    segments: segs(
      seg("REQ-012", "value"),
      " JSON解析失败: ",
      seg("invalid escape sequence \\x", "highlight"),
      " at line 23",
    ),
    dt: 1111,
  },
  {
    level: "ERROR",
    source: "Step3",
    step: 3,
    isError: true,
    segments: segs(
      seg("REQ-012", "value"),
      " 第 ",
      seg("1/3", "highlight"),
      " 轮重试",
    ),
    dt: 326,
  },
  {
    level: "WARN",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("REQ-012", "value"),
      " 重试 1 仍然失败，JSON 不完整",
    ),
    dt: 2440,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(
      "第 ",
      seg("3/3", "value"),
      " 轮重试: ",
      seg("7", "metric"),
      " 条待修复",
    ),
    dt: 4345,
  },
  {
    level: "WARN",
    source: "AI",
    segments: segs(
      "Token预算接近上限: ",
      seg("85%/100%", "metric"),
    ),
    dt: 3755,
  },
  {
    level: "INFO",
    source: "Token",
    segments: segs(
      "本次消耗: input=",
      seg("57,639", "value"),
      " output=",
      seg("32,984", "value"),
      " cost=",
      seg("0.2482V", "metric"),
    ),
    dt: 3655,
  },
  {
    level: "INFO",
    source: "Token",
    segments: segs(
      "合计: input=",
      seg("220,903", "value"),
      " output=",
      seg("90,557", "value"),
      " cost=",
      seg("1.1050V", "metric"),
    ),
    dt: 100,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(
      "全部建模完成: ",
      seg("18", "value"),
      "/18 条, 成功率 ",
      seg("94.4%", "value"),
    ),
    dt: 900,
  },
  {
    level: "INFO",
    source: "Step4",
    step: 4,
    segments: segs(
      seg("开始检查执行", "highlight"),
      ": ",
      seg("56", "value"),
      " 个检查点, 并发=",
      seg("3", "value"),
    ),
    dt: 4001,
  },
  {
    level: "INFO",
    source: "API",
    segments: segs(
      "GET http://tokenapi.example.com:3000/v1/chat → ",
      seg("200", "value"),
      " ",
      seg("(142ms)", "dim"),
    ),
    dt: 199,
  },
  {
    level: "INFO",
    source: "Step4",
    step: 4,
    segments: segs(
      "CHECK-001 ",
      seg("REQ-001", "value"),
      " → ",
      seg("PASS", "value"),
      " ",
      seg("exact match", "dim"),
    ),
    dt: 1300,
  },
  {
    level: "INFO",
    source: "Step4",
    step: 4,
    segments: segs(
      "CHECK-002 ",
      seg("REQ-001", "value"),
      " → ",
      seg("PASS", "value"),
      " ",
      seg("fuzzy (0.92)", "dim"),
    ),
    dt: 600,
  },
  {
    level: "WARN",
    source: "Step4",
    step: 4,
    segments: segs(
      "CHECK-008 ",
      seg("REQ-003", "value"),
      " → ",
      seg("WARN", "metric"),
      " fuzzy score=0.68, 阈值=0.70",
    ),
    dt: 1700,
  },
  {
    level: "ERROR",
    source: "Step4",
    step: 4,
    isError: true,
    segments: segs(
      "CHECK-012 ",
      seg("REQ-005", "value"),
      " → ",
      seg("FAIL", "highlight"),
      " Playwright timeout: 30000ms exceeded",
    ),
    dt: 700,
  },
  {
    level: "ERROR",
    source: "Playwright",
    isError: true,
    segments: segs(
      "Element ",
      seg('button[type="submit"]', "highlight"),
      " not found within 30s timeout",
    ),
    dt: 700,
  },
  {
    level: "INFO",
    source: "Step4",
    step: 4,
    segments: segs(
      "CHECK-012 API直调降级: GET /api/order/status → ",
      seg("200", "value"),
      " ",
      seg("(89ms)", "dim"),
    ),
    dt: 800,
  },
  {
    level: "SUCCESS",
    source: "Step4",
    step: 4,
    segments: segs(
      "检查执行完成: ",
      seg("56", "value"),
      " 个检查点, ",
      seg("PASS", "value"),
      "=48, ",
      seg("WARN", "metric"),
      "=3, ",
      seg("FAIL", "highlight"),
      "=5",
    ),
    dt: 4100,
  },
  {
    level: "INFO",
    source: "Step5",
    step: 5,
    segments: segs(
      seg("开始生成报告", "highlight"),
      ": 格式=HTML+JSON",
    ),
    dt: 1901,
  },
  {
    level: "SUCCESS",
    source: "Step5",
    step: 5,
    segments: segs(
      "报告生成完成: ",
      seg("e2e-report-20260704.html", "value"),
      " ",
      seg("(2.5s)", "dim"),
    ),
    dt: 2499,
  },
  {
    level: "INFO",
    source: "系统",
    segments: segs(
      seg("流水线执行完成", "highlight"),
      " | 总耗时: ",
      seg("2m 05s", "metric"),
    ),
    dt: 5500,
  },
  {
    level: "DEBUG",
    source: "AI",
    segments: segs(
      "prompt_tokens=",
      seg("1357", "value"),
      ", smart_max=",
      seg("16384", "value"),
      ", final=",
      seg("1024", "value"),
    ),
    dt: 2300,
  },
];

// 生成一条完整流水线日志序列
export function generatePipelineRun(startBase: Date): LogEntry[] {
  const out: LogEntry[] = [];
  let cursor = new Date(startBase.getTime());
  for (const beat of SCENARIO) {
    cursor = advanceTime(cursor, beat.dt);
    out.push({
      id: nextId(),
      timestamp: formatTs(cursor),
      level: beat.level,
      source: beat.source,
      step: beat.step,
      isError: beat.isError,
      segments: beat.segments,
    });
  }
  return out;
}

// 持续生成的新日志轮次（循环播放，时间戳递增）
const LOOP_BEATS: ScenarioBeat[] = [
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs("开始建模: ", seg("REQ-001", "value"), " 用户登录验证"),
    dt: 0,
  },
  {
    level: "INFO",
    source: "AI",
    segments: segs(
      "model=",
      seg("deepseek-v4-pro", "value"),
      ", input=",
      seg("1,415", "value"),
      ", output=",
      seg("1,024", "value"),
      " ",
      seg("tokens", "dim"),
    ),
    dt: 222,
  },
  {
    level: "SUCCESS",
    source: "Step3",
    step: 3,
    segments: segs(
      seg("REQ-001", "value"),
      " 建模完成: ",
      seg("3", "value"),
      " actions, ",
      seg("5", "value"),
      " checks",
    ),
    dt: 667,
  },
  {
    level: "WARN",
    source: "AI",
    segments: segs("Token预算接近上限: ", seg("85%/100%", "metric")),
    dt: 333,
  },
  {
    level: "ERROR",
    source: "Step3",
    step: 3,
    isError: true,
    segments: segs(
      seg("REQ-011", "value"),
      " JSON解析失败: ",
      seg("expected '}'", "highlight"),
    ),
    dt: 1322,
  },
  {
    level: "ERROR",
    source: "Step3",
    step: 3,
    isError: true,
    segments: segs(seg("REQ-011", "value"), " 第 ", seg("1/3", "highlight"), " 轮重试"),
    dt: 101,
  },
  {
    level: "ERROR",
    source: "Step3",
    step: 3,
    isError: true,
    segments: segs(
      seg("REQ-012", "value"),
      " JSON解析失败: ",
      seg("invalid escape", "highlight"),
    ),
    dt: 1234,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs("第 ", seg("3/3", "value"), " 轮重试: ", seg("7", "metric"), " 条待修复"),
    dt: 111,
  },
  {
    level: "INFO",
    source: "Token",
    segments: segs(
      "本次消耗: input=",
      seg("57,639", "value"),
      " output=",
      seg("32,984", "value"),
      " cost=",
      seg("0.2482V", "metric"),
    ),
    dt: 766,
  },
  {
    level: "INFO",
    source: "API",
    segments: segs(
      "GET http://tokenapi.example.com:3000/v1/chat → ",
      seg("200", "value"),
      " ",
      seg("(98ms)", "dim"),
    ),
    dt: 1500,
  },
  {
    level: "DEBUG",
    source: "AI",
    segments: segs(
      "prompt_tokens=",
      seg("1357", "value"),
      ", smart_max=",
      seg("16384", "value"),
      ", final=",
      seg("1024", "value"),
    ),
    dt: 500,
  },
  {
    level: "INFO",
    source: "Step3",
    step: 3,
    segments: segs(seg("REQ-005", "value"), " 建模中... AI推理耗时 ", seg("2.3s", "metric")),
    dt: 1000,
  },
];

export function generateLoopBatch(startBase: Date): LogEntry[] {
  const out: LogEntry[] = [];
  let cursor = new Date(startBase.getTime());
  for (const beat of LOOP_BEATS) {
    cursor = advanceTime(cursor, beat.dt);
    out.push({
      id: nextId(),
      timestamp: formatTs(cursor),
      level: beat.level,
      source: beat.source,
      step: beat.step,
      isError: beat.isError,
      segments: beat.segments,
    });
  }
  return out;
}
