import type { ArtifactDetail } from '@/types';
import { ApiError, getArtifact, getVersion } from '@/lib/api';
import { findPrevVersion } from '@/lib/artifactVersions';

interface TerminalArtifactReconciliation {
  sessionId: string;
  artifactId: string;
  isOwner: () => boolean;
  commitPresent: (artifact: ArtifactDetail, diffBaseContent: string | undefined) => void;
  commitMissing: (artifactId: string) => void;
}

/**
 * Resolve one terminal artifact against DB truth.
 *
 * Outcomes are intentionally tri-state:
 * - present: atomically commit detail + persisted Diff base;
 * - missing (detail 404): atomically remove the artifact and its tabs;
 * - unknown (network/5xx): preserve state for a later retry.
 */
export async function reconcileTerminalArtifact({
  sessionId,
  artifactId,
  isOwner,
  commitPresent,
  commitMissing,
}: TerminalArtifactReconciliation): Promise<void> {
  try {
    const artifact = await getArtifact(sessionId, artifactId);
    if (!isOwner()) return;

    const prevVersion = findPrevVersion(artifact.versions, artifact.current_version);
    let diffBaseContent: string | undefined;
    if (prevVersion === null) {
      // First persisted version: Diff is correctly empty → current.
      diffBaseContent = '';
    } else {
      try {
        const base = await getVersion(sessionId, artifact.id, prevVersion);
        if (!isOwner()) return;
        diffBaseContent = base.content;
      } catch {
        if (!isOwner()) return;
        // Detail/versions are still authoritative. Undefined tells the store
        // to exit Diff because the previous-version side is unknown.
        diffBaseContent = undefined;
      }
    }

    if (!isOwner()) return;
    commitPresent(artifact, diffBaseContent);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404 && isOwner()) {
      commitMissing(artifactId);
    }
    // Other errors are "unknown": preserve state for a later retry.
  }
}
