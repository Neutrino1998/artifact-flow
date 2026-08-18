import { redirect } from 'next/navigation';

// Document requests are rewritten by middleware before App Router sees the
// provider query. If that boundary is bypassed, fail closed instead of rendering
// a route whose RSC payload could serialize the query string.
export default function SsoCallbackFallbackPage() {
  redirect('/login');
}
