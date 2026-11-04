/// <reference types="vite/client" />

interface ImportMetaEnv {
  // 是否使用 mock 数据源（开发期无后端时启用）
  // 设为 "true" 时加载内置剧本，不对接真实后端
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
