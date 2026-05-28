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
        accent: {
          DEFAULT: '#c96442',
          hover: '#b5573a',
          bg: '#fdf4f0',
        },
        border: {
          DEFAULT: '#d6d3cb',
          dark: '#3a3a3a',
        },
        status: {
          success: '#4a8c6f',
          error: '#c25d4e',
          warning: '#c49a3c',
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
      },
      keyframes: {
        'slide-in-right': {
          '0%': { transform: 'translateX(100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        'slide-out-left': {
          '0%': { transform: 'translateX(0)', opacity: '1' },
          '100%': { transform: 'translateX(-100%)', opacity: '0' },
        },
      },
      animation: {
        'spin-once': 'spin 0.6s ease-in-out',
        'slide-in-right': 'slide-in-right 500ms ease-out forwards',
        'slide-out-left': 'slide-out-left 500ms ease-in forwards',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
    require('@tailwindcss/container-queries'),
  ],
};

export default config;
