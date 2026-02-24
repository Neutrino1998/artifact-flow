'use client';

import { useEffect } from 'react';
import { useUIStore } from '@/stores/uiStore';

export default function ThemeInitializer() {
  const setTheme = useUIStore((s) => s.setTheme);

  useEffect(() => {
    const saved = localStorage.getItem('theme');
    if (saved === 'light' || saved === 'dark') {
      setTheme(saved);
    } else {
      // No saved preference — follow system
      const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      setTheme(systemDark ? 'dark' : 'light');
    }
  }, [setTheme]);

  return null;
}
