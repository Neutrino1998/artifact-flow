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
 * stale snapshot. This helper centralizes two guards:
 *
 *   (1) Generation counter — only the most recently fired request's response
 *       is allowed to update store. Earlier in-flight responses are dropped.
 *   (2) Session check — if the user switched conversations while a request
 *       was in flight, dropping it prevents data from another session from
 *       leaking into the current view.
 *
 * The counter is module-level so all three call sites share the same
 * generation namespace.
 */
import * as api from './api';
import type { ArtifactSummary } from '@/types';

let _generation = 0;

export async function refreshArtifactList(
  sessionId: string,
  setArtifacts: (artifacts: ArtifactSummary[]) => void,
  getCurrentSessionId: () => string | null,
): Promise<void> {
  const myGen = ++_generation;
  try {
    const data = await api.listArtifacts(sessionId);
    // (1) A newer call has been fired since we awaited — drop our stale response.
    if (myGen !== _generation) return;
    // (2) User switched session while we were in-flight — drop to avoid
    //     leaking data across sessions. If current is null (e.g. new-convo
    //     flow), pass through.
    const cur = getCurrentSessionId();
    if (cur && cur !== sessionId) return;
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
