'use client';

import { type AnchorHTMLAttributes } from 'react';
import { useArtifacts } from '@/hooks/useArtifacts';

const ARTIFACT_SCHEME = 'artifact://';

type ArtifactLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { node?: unknown };

/**
 * Custom <a> renderer for ReactMarkdown.
 *
 * Intercepts links with the `artifact://` scheme and opens the corresponding
 * artifact in the side panel via useArtifacts().selectArtifact. All other
 * links fall through to a normal anchor that opens in a new tab.
 *
 * Pair with markdownUrlTransform — the default react-markdown URL sanitizer
 * strips non-http(s) schemes to "", which would defeat this interception.
 *
 * `node` is dropped: react-markdown passes the hast node as an extra prop,
 * and spreading it into a real <a> produces an `node="[object Object]"` DOM
 * attribute plus a React unknown-prop warning.
 */
export default function ArtifactLink({ node: _node, ...props }: ArtifactLinkProps) {
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
