'use client';

import { useCallback, useRef, useState, useEffect } from 'react';

/**
 * Best-effort clipboard write that works outside a secure context.
 *
 * `navigator.clipboard` only exists in a secure context (HTTPS / localhost).
 * Private deployments served over plain HTTP on an intranet leave it
 * `undefined`, so `writeText` throws and nothing gets copied. Fall back to the
 * legacy `document.execCommand('copy')` via an off-screen <textarea> there.
 */
async function writeClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the execCommand path.
    }
  }

  // Legacy fallback for insecure (HTTP) contexts.
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    // Keep it out of the layout / viewport so it doesn't flash or scroll.
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

/**
 * Clipboard write + transient "copied" flag. The flag flips true on success
 * and resets after `timeoutMs`. Failures are swallowed — the user can retry.
 */
export function useCopyFeedback(timeoutMs = 1500) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  const copy = useCallback(async (text: string) => {
    const ok = await writeClipboard(text);
    if (!ok) return;
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), timeoutMs);
  }, [timeoutMs]);

  return { copied, copy };
}
