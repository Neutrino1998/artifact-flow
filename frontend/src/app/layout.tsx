import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'ArtifactFlow',
  description: 'Multi-agent research system',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="min-h-screen bg-bg dark:bg-bg-dark text-text-primary dark:text-text-primary-dark">
        {children}
      </body>
    </html>
  );
}
