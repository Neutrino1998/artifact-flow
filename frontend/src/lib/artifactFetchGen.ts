/**
 * Module-level monotonic counter for artifact auto-open fetches.
 *
 * Each TOOL_COMPLETE in useSSE that fires `getArtifact()` captures the
 * current value via `bumpArtifactFetchGen()` BEFORE the await. On
 * resolution the callback compares its captured value against
 * `getArtifactFetchGen()`: if they differ, a newer fetch (or a
 * stream-end revert) has fired in the meantime and the late response
 * must be dropped — otherwise:
 *
 *   - Cross-artifact lateness: with A's fetch slow and B's fast, A
 *     resolves AFTER B and writes A on top of B. The id-compare guard
 *     alone cannot catch this: by the time A resolves, store.current
 *     is B (autoSelected=true), so "cross-artifact switch allowed
 *     when autoSelected=true" lets A through. The counter check
 *     drops A.
 *   - Post-revert resurrection: an auto-open fetch fired mid-stream
 *     can resolve AFTER `refreshAfterComplete()` already cleared the
 *     panel to list view. Without bumping the counter in the revert
 *     path, that late fetch reopens the panel.
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
