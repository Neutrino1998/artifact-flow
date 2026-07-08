// Client-side pre-gate for uploads. Since the upload-route flip (2026-06-11)
// the backend accepts ANY file format — text decodes to content, png/jpeg take
// the vision route, everything else lands as a binary blob the model can mount
// into the sandbox. There is no extension blacklist to mirror anymore; the only
// thing knowable client-side is the per-file size cap. Content-level failures
// (a corrupt .png claiming to be an image) still surface as a backend 422.

export interface StageRejection {
  name: string;
  reason: string;
}

/** Human-readable MB, one decimal — for the oversize message. */
function mb(bytes: number): string {
  return (bytes / 1024 / 1024).toFixed(1).replace(/\.0$/, '');
}

/** Split files into those the backend would accept and those it rejects on
 *  sight. The only client-side gate left is per-file byte size when `maxBytes`
 *  is given (mirrors backend MAX_UPLOAD_SIZE, surfaced via /api/v1/meta). The
 *  size gate is UX only — it spares the user a staged-then-422 round-trip; the
 *  backend stays the authoritative limit, and the batch TOTAL is enforced
 *  separately at the proxy (413). `maxBytes` omitted (limit not yet fetched)
 *  → size gate skipped, never blocks on a missing value. */
export function partitionStageable(
  files: File[],
  maxBytes?: number,
): {
  accepted: File[];
  rejected: StageRejection[];
} {
  const accepted: File[] = [];
  const rejected: StageRejection[] = [];
  for (const file of files) {
    if (maxBytes && file.size > maxBytes) {
      rejected.push({
        name: file.name,
        reason: `文件过大：${mb(file.size)}MB（单文件上限 ${mb(maxBytes)}MB）。`,
      });
    } else {
      accepted.push(file);
    }
  }
  return { accepted, rejected };
}
