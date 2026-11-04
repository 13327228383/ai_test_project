/** @type {import('tailwindcss').Config} */

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    container: {
      center: true,
    },
    extend: {
      colors: {
        primary: {
          DEFAULT: "var(--color-primary)",
          soft: "var(--color-primary-soft)",
          border: "var(--color-primary-border)",
          hover: "var(--color-primary-hover)",
        },
        bg: {
          base: "var(--bg-base)",
          surface: "var(--bg-surface)",
          elevated: "var(--bg-elevated)",
          overlay: "var(--bg-overlay)",
          log: "var(--bg-log)",
        },
        sidebar: "var(--sidebar-bg)",
        text: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          tertiary: "var(--text-tertiary)",
          inverse: "var(--text-inverse)",
        },
        border: {
          DEFAULT: "var(--border-default)",
          hover: "var(--border-hover)",
          focus: "var(--border-focus)",
        },
        state: {
          success: "var(--state-success)",
          "success-soft": "var(--state-success-soft)",
          "success-text": "var(--state-success-text)",
          warning: "var(--state-warning)",
          "warning-soft": "var(--state-warning-soft)",
          "warning-text": "var(--state-warning-text)",
          error: "var(--state-error)",
          "error-soft": "var(--state-error-soft)",
          "error-text": "var(--state-error-text)",
          info: "var(--state-info)",
          "info-soft": "var(--state-info-soft)",
          "info-text": "var(--state-info-text)",
        },
        step: {
          1: "var(--step1-color)",
          2: "var(--step2-color)",
          3: "var(--step3-color)",
          4: "var(--step4-color)",
          5: "var(--step5-color)",
        },
      },
      fontFamily: {
        sans: ["Inter", "Microsoft YaHei", "PingFang SC", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "Cascadia Code", "Microsoft YaHei", "monospace"],
      },
      fontSize: {
        hero: ["28px", "1.25"],
        h1: ["20px", "1.25"],
        h2: ["16px", "1.25"],
        h3: ["14px", "1.4"],
        body: ["13px", "1.5"],
        small: ["12px", "1.5"],
        xs: ["11px", "1.5"],
        micro: ["10px", "1.5"],
      },
      borderRadius: {
        xs: "var(--radius-xs)",
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
        full: "var(--radius-full)",
      },
      spacing: {
        1: "var(--space-1)",
        2: "var(--space-2)",
        3: "var(--space-3)",
        4: "var(--space-4)",
        5: "var(--space-5)",
        6: "var(--space-6)",
        8: "var(--space-8)",
      },
      boxShadow: {
        card: "var(--surface-shadow-card)",
        float: "var(--surface-shadow-float)",
      },
    },
  },
  plugins: [],
};
