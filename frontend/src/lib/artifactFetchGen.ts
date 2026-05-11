/**
 * Module-level monotonic counter for artifact auto-open fetches.
 *
 * Each TOOL_COMPLETE in useSSE that fires `getArtifact()` captures the
 * current value via `bumpArtifactFetchGen()` BEFORE the await. On
 * resolution the callback compares its captured value against
 * `getArtifactFetchGen()`: if they differ, a newer auto-open OR an
 * external invalidation has fired in the meantime and the late
 * response must be dropped — otherwise:
 *
 *   - Cross-artifact lateness: with A's fetch slow and B's fast, A
 *     resolves AFTER B and writes A on top of B. The id-compare guard
 *     alone cannot catch this: by the time A resolves, store.current
 *     is B (autoSelected=true), so "cross-artifact switch allowed
 *     when autoSelected=true" lets A through. The counter check
 *     drops A.
 *   - Post-complete resurrection: an auto-open fetch fired mid-stream
 *     can resolve AFTER stream end. Without an invalidation point,
 *     that late fetch reopens the panel (or — worse — pops one open
 *     when the auto-open hadn't yet resolved when complete arrived,
 *     so current was still null and no per-revert bump would have
 *     fired either).
 *   - Cross-conversation leak: switching conversation or starting a
 *     new chat clears `artifactStore.current` to null. A late
 *     auto-open fetch from the abandoned conversation would otherwise
 *     fall through autoOpenArtifact's cur==null branch and inject the
 *     stale artifact into the new conversation's panel.
 *
 * Bump sites (every point at which any in-flight auto-open must be
 * invalidated):
 *   - useSSE.refreshAfterComplete entry (stream end)
 *   - useChat.switchConversation entry
 *   - useChat.startNewChat
 *   - autoOpenArtifact itself (each new fetch supersedes older ones)
 *
 * Generation is process-lifetime monotonic; never reset outside tests.
 */

let _artifactFetchGen = 0;

export function getArtifactFetchGen(): number {
  return _artifactFetchGen;
}

export function bumpArtifactFetchGen(): number {
  return ++_artifactFetchGen;
}

/** Test-only: reset counter for deterministic test setup. */
export function _resetArtifactFetchGenForTests(): void {
  _artifactFetchGen = 0;
}
