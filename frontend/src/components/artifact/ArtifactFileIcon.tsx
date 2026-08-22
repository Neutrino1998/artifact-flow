import { IconFolder } from '@tabler/icons-react';
import { Folders } from 'lucide-react';
import { FileTypeIcon, fileTypeLabel } from '@/components/ui/FileTypeIcon';

export { fileTypeLabel as artifactFileTypeLabel };

export function ArtifactFileIcon({
  contentType,
  filename,
  compact = false,
}: {
  contentType: string;
  filename?: string | null;
  compact?: boolean;
}) {
  return (
    <FileTypeIcon
      contentType={contentType}
      filename={filename}
      size={compact ? 16 : 20}
    />
  );
}

export function ArtifactFolderIcon() {
  return (
    <IconFolder
      aria-hidden="true"
      className="h-5 w-5 shrink-0 text-accent"
      stroke={1.65}
    />
  );
}

export function ArtifactBrowserIcon({ size = 14 }: { size?: number }) {
  return (
    <Folders
      aria-hidden="true"
      absoluteStrokeWidth
      className="shrink-0"
      size={size}
      strokeWidth={1.5}
    />
  );
}
