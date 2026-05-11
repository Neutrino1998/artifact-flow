'use client';

import { useRef } from 'react';

/**
 * Pure factory: returns a `claim()` function that, on each invocation,
 * bumps an internal counter and returns an `isLatest()` check. The
 * check returns true only until the next `claim()` supersedes it.
 *
 * This is the React-free core of `useLatestOnly` — extracted so the
 * monotonic-counter semantics are testable without a hook renderer.
 */
export function createLatestOnlyClaim(): () => () => boolean {
  let gen = 0;
  return function claim(): () => boolean {
    const myGen = ++gen;
    return () => myGen === gen;
  };
}

/**
 * Per-component-instance latest-only claim helper for async writes to
 * local state.
 *
 * Use when a component has one or more async fetchers that can be
 * triggered concurrently (initial load, debounced search, load-more,
 * external-bump refresh) and all write to the same local state. Without
 * coordination, a slow earlier response can overwrite a faster later
 * one — and an older "append" page can be merged into a newer query's
 * results.
 *
 * Pattern: capture before await, check after await.
 *
 *   const claim = useLatestOnly();
 *   const fetchPage = useCallback(async (...) => {
 *     const isLatest = claim();
 *     setLoading(true);
 *     try {
 *       const data = await api.fetch(...);
 *       if (!isLatest()) return;
 *       setData(data); // and other writes
 *     } finally {
 *       if (isLatest()) setLoading(false);
 *     }
 *   }, [claim, ...]);
 *
 * Distinct from `lib/navGen` / `lib/artifactFetchGen` / `lib/artifactDetailGen`,
 * which are *module-level* counters: those need cross-hook-call
 * coordination (multiple useArtifacts instances must share one counter,
 * and switchConversation / startNewChat must be able to bump from
 * outside). useLatestOnly is for *per-instance* coordination — many
 * call sites inside one component share one counter, but two sibling
 * components must not share one.
 */
export function useLatestOnly(): () => () => boolean {
  // useRef so the same factory instance lives across renders; its
  // closed-over counter is the per-instance source of truth.
  const claimRef = useRef<ReturnType<typeof createLatestOnlyClaim> | null>(null);
  if (claimRef.current === null) {
    claimRef.current = createLatestOnlyClaim();
  }
  return claimRef.current;
}
