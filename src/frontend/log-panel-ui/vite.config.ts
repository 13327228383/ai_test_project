import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tsconfigPaths from "vite-tsconfig-paths";

// Flask 后端地址（开发期通过 Vite 代理转发 /api/* 到 Flask）
// 默认 127.0.0.1:8001，可通过环境变量 FLASK_PORT 覆盖
const FLASK_PORT = process.env.FLASK_PORT || '8001';
const FLASK_TARGET = `http://127.0.0.1:${FLASK_PORT}`;

// https://vite.dev/config/
// ★ 条件配置：
//   - dev 模式：base='/'，资源从 vite dev server 根加载
//   - build 模式：base='/static/log-panel/'，产物部署到 Flask static 目录后资源路径正确
//   - build outDir 直接输出到 Flask static/log-panel/，集成时无需手动复制
//
// ★ 合规约束（P0-3 / P0-5 / P0-9 / Q5，改动前请先读 docs/frontend-adaptation-design-2026-08-06.md §2.3）：
//   1. sourcemap 必须为 false —— 'hidden' 仍会产出 .map 文件，等于把 TS 源码发到生产。
//   2. assetsInlineLimit 必须为 0 —— 禁止把任何资源内联成 base64 塞进 index.html。
//   3. minify 必须开启 —— 未压缩产物体积翻倍且等同于源码泄漏。
//   4. 禁止引入任何会注入外链/徽标的插件（traeBadgePlugin 的 prodOnly:true 只在生产出现，极易漏检）。
//   5. 禁止在生产构建启用 react-dev-locator（开发定位插件会把源码路径写进产物）。
export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/log-panel/' : '/',
  build: {
    // P0-3：'hidden' 会照常生成 .map 文件，只是不写 sourceMappingURL —— 必须 false
    sourcemap: false,
    outDir: '../static/log-panel',
    emptyOutDir: true,
    // P0-9：0 = 任何资源都不内联为 base64，全部输出为独立静态文件
    assetsInlineLimit: 0,
    // 生产必须压缩（原 minify:false 为调试期临时设置，已随本次合规改造收敛）
    minify: 'esbuild',
  },
  plugins: [
    // Q5：移除 babel plugins: ['react-dev-locator']，避免开发定位信息进生产
    react(),
    tsconfigPaths()
  ],
  server: {
    proxy: {
      // 把 /api/* 转发到 Flask 后端（SSE 流也走这个代理）
      '/api': {
        target: FLASK_TARGET,
        changeOrigin: true,
        // SSE 需要关闭缓冲，保持长连接
        ws: false,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            // 禁用代理缓冲，保证 SSE 实时推送
            proxyReq.setHeader('X-Accel-Buffering', 'no');
          });
        },
      },
      // /metrics 也转发（健康看板用到）
      '/metrics': {
        target: FLASK_TARGET,
        changeOrigin: true,
      },
    },
  },
}))
