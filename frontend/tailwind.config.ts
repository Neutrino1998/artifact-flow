import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        chat: {
          DEFAULT: '#f5f0e8',
          dark: '#1a1915',
        },
        panel: {
          DEFAULT: '#ddd5c8',
          dark: '#21201c',
        },
        action: {
          DEFAULT: '#1f1e1b',
          hover: '#151412',
          contrast: '#f3eee3',
        },
        bg: {
          DEFAULT: '#f5f0e8',
          dark: '#1a1915',
        },
        surface: {
          DEFAULT: '#ffffff',
          dark: '#2c2b28',
        },
        text: {
          primary: '#1a1a1a',
          secondary: '#6b6560',
          tertiary: '#9b9590',
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
          DEFAULT: '#e5ddd3',
          dark: '#3a3530',
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
    },
  },
  plugins: [require('@tailwindcss/typography')],
};

export default config;
