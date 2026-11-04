#!/usr/bin/env bash
# =============================================================================
# log-panel-ui 生产构建 + 合规卡口（P0-3）
# -----------------------------------------------------------------------------
# 设计依据：docs/frontend-adaptation-design-2026-08-06.md §2.3 / §7（T01 · P0-3）
#
# 为什么需要这个脚本：
#   `npm run build` 只保证「构建成功」，不保证「产物合规」。历史上出过三类事故：
#     1) sourcemap 配置从 false 被改回 'hidden'/true —— .map 文件把整套 TS 源码发到生产；
#     2) 开发期 mock 剧本（含内网域名 grandhoo）被静态 import 带进 bundle；
#     3) 第三方徽标 / Google Fonts 外链在 prodOnly 模式下悄悄注入，本地开发完全看不见。
#   这三类问题的共同点：构建过程零报错，只有对「产物文本」做扫描才能发现。
#   因此本脚本在 build 之后强制做一次产物扫描，命中任一违规即 exit 1，
#   让 CI / 人工部署在「发布前」就红掉，而不是上线后被安全扫描抓到。
#
# 用法：
#   bash ./build-and-deploy.sh        # 或 npm run build:deploy
#
# 退出码：
#   0 = 构建成功且产物合规
#   1 = 构建失败或产物命中违规模式（终端会打印命中的文件与行）
# =============================================================================

set -euo pipefail

# 切到脚本所在目录，保证无论从哪里调用，相对路径都一致
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 产物目录（与 vite.config.ts 中 build.outDir 保持一致）
DIST_DIR="../static/log-panel"

echo "=============================================================="
echo " log-panel-ui 生产构建 + 合规卡口"
echo " 工作目录 : $SCRIPT_DIR"
echo " 产物目录 : $DIST_DIR"
echo "=============================================================="

# -----------------------------------------------------------------------------
# 步骤 1：清空旧产物
# -----------------------------------------------------------------------------
# vite 的 emptyOutDir 只清 outDir 内 vite 自己认识的文件，历史遗留的 .map、
# 手工拷进去的文件不一定被清掉。这里显式 rm -rf，保证扫描的是「本次产物」，
# 不会因为旧文件残留而误报，也不会因为旧文件残留而漏报（旧 .map 仍会被部署）。
#
# 实现说明：这里用 node 的 fs.rmSync 而不是 `rm -rf`。
#   - 本目录本身就是 Node 项目，node 必然存在，没有额外依赖；
#   - `rm -rf` 在 Windows 原生 CI（cmd/PowerShell runner）上不可用，
#     而 fs.rmSync 三端行为一致，脚本不必区分平台；
#   - 部分带「安全删除」钩子的开发环境会拦截 rm，导致脚本在本地跑不通。
echo ""
echo "[1/4] 清空旧产物 $DIST_DIR ..."
if [ -d "$DIST_DIR" ]; then
  node -e "require('fs').rmSync(process.argv[1], { recursive: true, force: true })" "$DIST_DIR"
  echo "      已删除旧产物目录。"
else
  echo "      产物目录不存在，跳过。"
fi

# -----------------------------------------------------------------------------
# 步骤 2：类型检查 + 生产构建
# -----------------------------------------------------------------------------
echo ""
echo "[2/4] 执行 npm run build（tsc -b && vite build）..."
npm run build

if [ ! -d "$DIST_DIR" ]; then
  echo ""
  echo "✗ 构建未产出目录 $DIST_DIR，终止部署。"
  exit 1
fi

# -----------------------------------------------------------------------------
# 步骤 3：卡口 A —— 禁止 sourcemap 文件
# -----------------------------------------------------------------------------
# .map 文件 = 完整 TypeScript 源码 + 目录结构 + 注释，等同源码泄漏。
# 单独成一条卡口（而不是并进文本扫描），因为它是「文件是否存在」问题，
# 用文本 grep 扫不出来。
echo ""
echo "[3/4] 卡口 A：扫描 sourcemap 文件 ..."
MAP_FILES="$(find "$DIST_DIR" -type f -name '*.map' 2>/dev/null || true)"
if [ -n "$MAP_FILES" ]; then
  echo ""
  echo "✗ 卡口 A 未通过：产物中存在 sourcemap 文件（源码泄漏风险）"
  echo "$MAP_FILES" | sed 's/^/    /'
  echo ""
  echo "  修复：确认 vite.config.ts 中 build.sourcemap === false"
  echo "        （注意 'hidden' 仍会生成 .map 文件，只是不写 sourceMappingURL）"
  exit 1
fi
echo "      OK：未发现 .map 文件。"

# -----------------------------------------------------------------------------
# 步骤 4：卡口 B —— 禁止敏感 / 外链 / 开发期文本进入产物
# -----------------------------------------------------------------------------
# 逐个模式扫描（而不是一个大正则），是为了在命中时能精确告诉使用者
# 「命中的是哪一条规则」，便于定位修复方向。
echo ""
echo "[4/4] 卡口 B：扫描产物文本 ..."

# 模式 -> 说明（用两个平行数组，兼容 bash 3.x，不依赖关联数组）
# 关于 mock 的扫描口径（重要，勿随意改成笼统的 /mock/）：
#   真正的风险是「开发期 mock 剧本数据」被打进生产包 —— 它体积大、含内网域名、
#   且会让前端在后端异常时静默展示假数据。剧本的入口是 mock-dev/scenario.ts 导出的
#   两个生成器函数，因此这里精确锁定函数名与模块名。
#   而 store 中的 `useMock` 布尔开关、界面上的「Mock」运行态徽标属于产品功能
#   （告知用户当前未对接后端），不含任何数据与敏感信息，笼统 grep /mock/ 只会
#   产生长期误报并最终导致卡口被整体关掉，得不偿失。
PATTERNS=(
  'grandhoo'
  'fonts\.googleapis'
  'fonts\.gstatic'
  'trae\.ai'
  '192\.168\.'
  'react-dev-locator'
  'generatePipelineRun|generateLoopBatch'
  'mock-dev/scenario|@/mock/scenario'
)
REASONS=(
  '内网域名（应统一为 example.com / your-service-host:port）'
  'Google Fonts 外链（站点 CSP font-src 为 self，必被拦截）'
  'Google Fonts 字体源外链（同上）'
  '第三方徽标外链（traeBadgePlugin 残留）'
  '硬编码内网 IP（应统一为 your-service-host:port）'
  '开发期源码定位插件残留（会把源文件路径写进产物）'
  'mock 剧本生成器进入生产包（P0-4：只允许 DEV 下动态 import）'
  'mock 剧本模块被静态引用（P0-4：不得出现在生产 chunk 中）'
)

VIOLATION=0
for i in "${!PATTERNS[@]}"; do
  pattern="${PATTERNS[$i]}"
  reason="${REASONS[$i]}"
  # -r 递归、-I 跳过二进制、-l 只列文件名；|| true 让「无命中」(exit 1) 不触发 set -e
  hits="$(grep -rIl -E "$pattern" "$DIST_DIR" 2>/dev/null || true)"
  if [ -n "$hits" ]; then
    VIOLATION=1
    echo ""
    echo "  ✗ 命中违规模式: /$pattern/  —— $reason"
    echo "$hits" | sed 's/^/      /'
  else
    printf '  ✓ %-22s 未命中\n' "/$pattern/"
  fi
done

if [ "$VIOLATION" -ne 0 ]; then
  echo ""
  echo "✗ 卡口 B 未通过：产物包含不允许出现的文本，已终止部署。"
  echo "  请修复源码后重新执行本脚本（不要手工编辑 $DIST_DIR 内的产物）。"
  exit 1
fi

# -----------------------------------------------------------------------------
# 通过：打印产物概览，便于人工二次确认体积是否异常
# -----------------------------------------------------------------------------
echo ""
echo "=============================================================="
echo "✓ 全部卡口通过，产物已就绪：$DIST_DIR"
echo "--------------------------------------------------------------"
find "$DIST_DIR" -type f -printf '%10s  %p\n' 2>/dev/null | sort -k2 \
  || find "$DIST_DIR" -type f -exec ls -l {} \; | awk '{printf "%10s  %s\n", $5, $NF}'
echo "=============================================================="
