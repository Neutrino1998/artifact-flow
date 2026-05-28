import type { Metadata } from 'next';
import { headers } from 'next/headers';
import './globals.css';
import ThemeInitializer from '@/components/ThemeInitializer';
import { APP_NAME, APP_TAGLINE } from '@/lib/branding';

const themeScript = `(function(){var t=localStorage.getItem('theme');var d=t==='dark'||(t!=='light'&&window.matchMedia('(prefers-color-scheme:dark)').matches);if(d)document.documentElement.classList.add('dark')})()`;

export const metadata: Metadata = {
  title: APP_NAME,
  // description = APP_TAGLINE: keep <head> meta + visible UI tagline reading
  // from one branding.ts source. Diverged historically because metadata is
  // Next server-side (build-time eval) and there was no separate constant to
  // import — now there is, so import it.
  description: APP_TAGLINE,
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // CSP nonce set by middleware — applied to the inline theme script so it runs
  // under `script-src 'nonce-…'` (no 'unsafe-inline').
  const nonce = (await headers()).get('x-nonce') ?? undefined;
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* suppressHydrationWarning: the browser blanks the nonce attribute after
            parse (CSP anti-exfiltration), so the client reads nonce="" while the
            server markup has the real nonce — an expected, benign mismatch. The
            script has already executed by then. */}
        <script
          nonce={nonce}
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: themeScript }}
        />
      </head>
      <body className="min-h-screen bg-chat dark:bg-chat-dark text-text-primary dark:text-text-primary-dark">
        <ThemeInitializer />
        {children}
      </body>
    </html>
  );
}
