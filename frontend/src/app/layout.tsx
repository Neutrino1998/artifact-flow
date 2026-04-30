import type { Metadata } from 'next';
import './globals.css';
import ThemeInitializer from '@/components/ThemeInitializer';

const themeScript = `(function(){var t=localStorage.getItem('theme');var d=t==='dark'||(t!=='light'&&window.matchMedia('(prefers-color-scheme:dark)').matches);if(d)document.documentElement.classList.add('dark')})()`;

export const metadata: Metadata = {
  title: '银清Claw测试版',
  description: 'powered by deepseek-v4-flash',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen bg-chat dark:bg-chat-dark text-text-primary dark:text-text-primary-dark">
        <ThemeInitializer />
        {children}
      </body>
    </html>
  );
}
