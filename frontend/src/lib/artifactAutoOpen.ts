import * as api from './api';
import type { ArtifactDetail, VersionSummary } from '@/types';
import { bumpArtifactFetchGen, getArtifactFetchGen } from './artifactFetchGen';

/**
 * Dependencies for `autoOpenArtifact`. All accessors / setters injected so
 * the helper stays pure and unit-testable without spinning up the real
 * Zustand store or React context.
 */
export interface ArtifactAutoOpenDeps {
  getCurrent: () => ArtifactDetail | null;
  getAutoSelected: () => boolean;
  setCurrentAuto: (artifact: ArtifactDetail) => void;
  refreshCurrent: (artifact: ArtifactDetail) => void;
  setVersions: (versions: VersionSummary[]) => void;
  setSelectedVersion: (version: null) => void;
}

/**
 * Stream-event handler for auto-opening the artifact panel when an agent
 * artifact tool completes. Encapsulates all the race / ownership logic
 * that used to be an inline closure inside `useSSE.ts`:
 *
 *   - Per-fetch generation counter — drops out-of-order fetch resolutions
 *     (A slow, B fast: A's late callback would otherwise overwrite B).
 *     Also drops fetches that survive past stream-end revert.
 *   - Same-id branch — preserves the user's manual ownership
 *     (`autoSelected=false`) and their chosen view mode by writing
 *     through `refreshCurrent` instead of `setCurrentAuto`. Version-
 *     ordering still enforced for same-id out-of-order edge.
 *   - Cross-id branch — only allowed when no manual pick is in effect
 *     (autoSelected=true or current=null). Sets `autoSelected=true` so
 *     subsequent agent edits can keep following the latest update.
 *
 * Errors from the fetch are swallowed silently, mirroring the original
 * `.catch(() => {})` behavior — auto-open is best-effort and shouldn't
 * surface to the user.
 */
export async function autoOpenArtifact(
  sessionId: string,
  artifactId: string,
  deps: ArtifactAutoOpenDeps,
  fetchFn: (sid: string, aid: string) => Promise<ArtifactDetail> = api.getArtifact,
): Promise<void> {
  const myGen = bumpArtifactFetchGen();
  let detail: ArtifactDetail;
  try {
    detail = await fetchFn(sessionId, artifactId);
  } catch {
    return;
  }

  // A newer fetch fired (or `refreshAfterComplete` bumped the counter to
  // invalidate everything in flight before reverting to list). Drop.
  if (myGen !== getArtifactFetchGen()) return;

  const cur = deps.getCurrent();
  const autoSelected = deps.getAutoSelected();

  if (cur && cur.id === detail.id) {
    // Same-artifact refresh. Preserve ownership + viewMode.
    // Same-id out-of-order: discard if the displayed version is newer.
    if (cur.current_version > detail.current_version) return;
    deps.refreshCurrent(detail);
  } else {
    // Cross-artifact switch. Refuse if the user has actively selected
    // something — don't yank them away.
    if (cur && !autoSelected) return;
    deps.setCurrentAuto(detail);
  }
  deps.setVersions(detail.versions);
  deps.setSelectedVersion(null);
}
