'use client';

import { useEffect, useState } from 'react';
import { FileTypeIcon } from '@/components/ui/FileTypeIcon';
import type { StagedFile } from '@/stores/stagedFilesStore';

/** One staged-attachment chip. Image files show a local thumbnail rendered from
 *  the File the user just picked (objectURL — no upload/round-trip needed); all
 *  other states use the shared file-format icon. The objectURL is revoked on
 *  unmount / file change. */
export default function StagedFileChip({
  sf,
  onRemove,
}: {
  sf: StagedFile;
  onRemove: () => void;
}) {
  const isImage = sf.file.type.startsWith('image/');
  const [thumb, setThumb] = useState<string | null>(null);

  useEffect(() => {
    if (!isImage) return;
    const u = URL.createObjectURL(sf.file);
    setThumb(u);
    return () => URL.revokeObjectURL(u);
  }, [isImage, sf.file]);

  return (
    <span
      className="inline-flex items-center gap-1 max-w-[200px] pl-1 pr-1 py-1 rounded-lg bg-bg dark:bg-bg-dark border border-border dark:border-border-dark text-xs text-text-secondary dark:text-text-secondary-dark"
      title={sf.file.name}
    >
      {thumb ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={thumb} alt={sf.file.name} className="w-5 h-5 rounded object-cover shrink-0" />
      ) : (
        <FileTypeIcon contentType={sf.file.type} filename={sf.file.name} size={16} />
      )}
      <span className="truncate">{sf.file.name}</span>
      <button
        onClick={onRemove}
        className="shrink-0 p-0.5 rounded hover:bg-surface dark:hover:bg-surface-dark text-text-tertiary dark:text-text-tertiary-dark"
        aria-label={`Remove ${sf.file.name}`}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>
    </span>
  );
}
