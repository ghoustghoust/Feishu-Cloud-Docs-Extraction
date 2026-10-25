/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 语义保持不变, 值换为浅色系: 950=页面底 900=卡片 850=次级底 800=悬停 700=描边
        ink: {
          950: "#f5f7fb",
          900: "#ffffff",
          850: "#f8fafc",
          800: "#eef2f7",
          700: "#e2e8f0",
          600: "#cbd5e1",
        },
        lark: {
          DEFAULT: "#3370ff",
          soft: "#2563eb",
          dim: "#93b4ff",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "PingFang SC", "Microsoft YaHei", "sans-serif"],
        mono: ["JetBrains Mono", "Cascadia Code", "Consolas", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,.04), 0 8px 24px -12px rgba(15,23,42,.08)",
      },
      animation: {
        "pulse-soft": "pulse 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [],
};
