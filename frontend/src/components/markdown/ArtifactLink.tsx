'use client';

import { type AnchorHTMLAttributes } from 'react';
import { useArtifacts } from '@/hooks/useArtifacts';

const ARTIFACT_SCHEME = 'artifact://';

/**
 * Custom <a> renderer for ReactMarkdown.
 *
 * Intercepts links with the `artifact://` scheme and opens the corresponding
 * artifact in the side panel via useArtifacts().selectArtifact. All other
 * links fall through to a normal anchor that opens in a new tab.
 *
 * Pair with markdownUrlTransform — the default react-markdown URL sanitizer
 * strips non-http(s) schemes to "", which would defeat this interception.
 */
export default function ArtifactLink(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const { selectArtifact } = useArtifacts();
  const href = props.href ?? '';

  if (href.startsWith(ARTIFACT_SCHEME)) {
    const id = href.slice(ARTIFACT_SCHEME.length);
    return (
      <a
        {...props}
        onClick={(e) => {
          e.preventDefault();
          if (id) selectArtifact(id);
        }}
      />
    );
  }

  return <a {...props} target="_blank" rel="noopener noreferrer" />;
}
