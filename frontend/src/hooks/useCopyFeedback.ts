'use client';

import { useCallback, useRef, useState, useEffect } from 'react';

/**
 * Clipboard write + transient "copied" flag. The flag flips true on success
 * and resets after `timeoutMs`. Failures (e.g. clipboard blocked in non-HTTPS
 * context, sandbox iframe) are swallowed — the user can simply retry.
 */
export function useCopyFeedback(timeoutMs = 1500) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), timeoutMs);
    } catch {
      // Clipboard API unavailable / permission denied — silently ignore.
    }
  }, [timeoutMs]);

  return { copied, copy };
}
