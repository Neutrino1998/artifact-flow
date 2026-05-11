/**
 * Latest-only guarded refresh for the artifact list.
 *
 * Three call sites need to refresh the artifact list:
 *   1. useSSE.refreshAfterComplete  — stream completion
 *   2. useSSE TOOL_COMPLETE handler — mid-stream artifact tool / auto-persist
 *   3. useArtifacts.loadArtifacts   — manual triggers (panel mount, post-upload)
 *
 * Without coordination, concurrent calls produce a race: an older response
 * arrives last and overwrites a newer one, briefly reverting the list to a
 * stale snapshot. This helper coordinates them with **claim-before-await**:
 *
 *   1. Bump a module-level generation counter and stamp the target session
 *      onto the artifact store immediately — claim "the list will belong to
 *      this session".
 *   2. Await the API response.
 *   3. Verify the claim still stands:
 *        - Generation must still be ours (no newer refresh has fired).
 *        - Current session must still match our target (no reset / switch
 *          has invalidated our claim).
 *      Otherwise drop the response silently.
 *
 * This pattern handles the tricky case where `useChat.switchConversation`
 * (or newConversation) calls `resetArtifacts()` mid-flight, setting the
 * store's sessionId to null. A stale response that returns after the reset
 * sees `cur === null !== sessionId` and is dropped, preventing cross-session
 * leakage. Generation counter alone wouldn't catch this when no follow-up
 * refresh is fired to bump the counter.
 */
import * as api from './api';
import type { ArtifactSummary } from '@/types';

let _generation = 0;

export async function refreshArtifactList(
  sessionId: string,
  setArtifacts: (artifacts: ArtifactSummary[]) => void,
  setSessionId: (sessionId: string | null) => void,
  getCurrentSessionId: () => string | null,
): Promise<void> {
  const myGen = ++_generation;
  // Claim: stamp our target session so a later reset() sets cur back to null
  // and we can detect "claim invalidated" after the await resolves.
  setSessionId(sessionId);
  try {
    const data = await api.listArtifacts(sessionId);
    // (1) Newer refresh fired during our await → drop.
    if (myGen !== _generation) return;
    // (2) Reset / switch invalidated our claim → drop. Strict equality
    //     means a null cur (post-reset) blocks us even when no replacement
    //     refresh has been fired.
    if (getCurrentSessionId() !== sessionId) return;
    setArtifacts(data.artifacts);
  } catch {
    // Silent: callers decide whether/how to surface errors. This refresh is
    // a best-effort secondary update — the next refresh will retry.
  }
}

/**
 * Test-only: reset the generation counter so tests start from a known state.
 * Production code must never call this.
 */
export function _resetGenerationForTests(): void {
  _generation = 0;
}
