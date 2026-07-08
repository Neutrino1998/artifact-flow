import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        chat: {
          DEFAULT: '#FAF9F6',
          dark: '#1e1e1e',
        },
        panel: {
          DEFAULT: '#ffffff',
          dark: '#222222',
          accent: '#F0EEE7',
          'accent-dark': '#1a1a1a',
        },
        action: {
          DEFAULT: '#1f1e1b',
          hover: '#151412',
          contrast: '#f3eee3',
        },
        bg: {
          DEFAULT: '#FAF9F6',
          dark: '#1a1a1a',
        },
        surface: {
          DEFAULT: '#ffffff',
          dark: '#2a2a2a',
        },
        text: {
          primary: '#1f0909',
          secondary: '#656565',
          tertiary: '#999999',
          'primary-dark': '#e8e4dc',
          'secondary-dark': '#9b9590',
          'tertiary-dark': '#6b6560',
        },
        // 「赭墨」— values live as RGB-triplet CSS vars in globals.css so
        // hover/bg can differ per mode without dark: variants at call sites.
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          hover: 'rgb(var(--accent-hover) / <alpha-value>)',
          bg: 'rgb(var(--accent-bg) / <alpha-value>)',
        },
        // 边框/分割线:统一一个实色令牌(描边 + 实色填充:滚动条/分栏条/开关轨道/
        // 细线),全站同一值。浅色 #dddbd4(暖米灰,较原 #d6d3cb 提亮一档、更贴浅底);
        // 深色 #333333(中性,较原 #3a3a3a 略深一档、更贴深色卡面 #2a2a2a)。
        // 必须用实色:opacity 修饰(border/60、ring-border/40 等)会缩放它;若填半透明
        // 色值,/NN 会改写其 alpha、暴露底色 → 描边突然变重(头像/通知环那个 bug)。
        border: {
          DEFAULT: '#dddbd4',
          dark: '#333333',
        },
        // Muted categorical hues for the observability event trace (scoped
        // to that panel; agent_* events use accent directly).
        trace: {
          tool: '#52657F',
          'tool-dark': '#7E92AC',
          llm: '#6A5670',
          'llm-dark': '#9A85A0',
        },
        status: {
          success: '#4a8c6f',
          // 危险红:从 #c25d4e(HSL 8°/49%/53%,为贴合赭墨被压得过温吞)提饱和到
          // #d0432f(7°/63%/50%)—— 色相仍锁暖家族,但明显读作「警示」而非陈旧砖色;
          // 刻意比 success/warning 更冲(危险色该抢注意力,音量梯度 error>warning>success)。
          error: '#d0432f',
          warning: '#c49a3c',
          // running deliberately shares the accent hue (live activity = brand)
          running: 'rgb(var(--accent) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'sans-serif',
        ],
        serif: [
          'PT Serif',
          'Times New Roman',
          'Times',
          'serif',
        ],
        mono: ['SF Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        card: '12px',
        bubble: '16px',
      },
      boxShadow: {
        hover: 'var(--shadow-hover)',
        modal: 'var(--shadow-modal)',
        float: 'var(--shadow-float)',
        sidebar: 'var(--shadow-sidebar)',
        'sidebar-card': 'var(--shadow-sidebar-card)',
      },
      keyframes: {
        // 欢迎页 hint 切换:淡入 + 轻微上浮 + 微模糊。由 WelcomeTips 顺序驱动
        // (先 tip-out 淡出旧的、再 tip-in 淡入新的),故不重叠;替换原先整宽
        // translateX 横滑(观感偏「糙」)。无障碍:reduced-motion 退化为纯淡入,见 globals.css。
        'tip-in': {
          '0%': { opacity: '0', transform: 'translateY(6px)', filter: 'blur(2.5px)' },
          '100%': { opacity: '1', transform: 'translateY(0)', filter: 'blur(0)' },
        },
        'tip-out': {
          '0%': { opacity: '1', transform: 'translateY(0)', filter: 'blur(0)' },
          '100%': { opacity: '0', transform: 'translateY(-6px)', filter: 'blur(2.5px)' },
        },
      },
      animation: {
        'spin-once': 'spin 0.6s ease-in-out',
        'tip-in': 'tip-in 300ms ease-out forwards',
        'tip-out': 'tip-out 300ms ease-in forwards',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
    require('@tailwindcss/container-queries'),
  ],
};

export default config;
