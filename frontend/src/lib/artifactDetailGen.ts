/**
 * Monotonic counter for the artifact-detail-view selection scope.
 *
 * Both `useArtifacts.selectArtifact` and `useArtifacts.selectVersion` bump
 * this on entry. Late responses (fast user clicking A→B in the artifact
 * list, or v5→v3 in the version dropdown) compare their captured value
 * against `getArtifactDetailGen()` after the await — mismatch drops the
 * response so the slow A/v5 cannot overwrite the latest B/v3 selection
 * (or poison the diff base of an unrelated artifact).
 *
 * Also bumped by `useChat.switchConversation` and `useChat.startNewChat`
 * so an in-flight manual artifact-detail fetch from the previous
 * conversation cannot leak into the new conversation's panel.
 *
 * Distinct from `artifactFetchGen` (which protects auto-open fetches
 * driven by SSE TOOL_COMPLETE) — different bump sites, different
 * protected code paths, kept separate to avoid cross-coupling.
 *
 * Generation is process-lifetime monotonic; never reset outside tests.
 */

let _artifactDetailGen = 0;

export function getArtifactDetailGen(): number {
  return _artifactDetailGen;
}

export function bumpArtifactDetailGen(): number {
  return ++_artifactDetailGen;
}

/** Test-only: reset counter for deterministic test setup. */
export function _resetArtifactDetailGenForTests(): void {
  _artifactDetailGen = 0;
}
