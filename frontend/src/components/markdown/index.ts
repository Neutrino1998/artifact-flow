import type { Components, UrlTransform } from 'react-markdown';
import { defaultUrlTransform } from 'react-markdown';
import CodeBlock from './CodeBlock';
import DiagramPre from './DiagramPre';
import ArtifactLink from './ArtifactLink';

export const markdownComponents: Partial<Components> = {
  pre: CodeBlock as Components['pre'],
  a: ArtifactLink as Components['a'],
};

/**
 * Variant that renders ```mermaid blocks as diagrams (other code blocks fall
 * back to CodeBlock). Use ONLY on non-streaming surfaces — artifact preview
 * and the persisted final response — where content is complete and stable.
 * The streaming flow uses `markdownComponents` so half-written mermaid never
 * flickers a parse error.
 */
export const markdownComponentsWithDiagrams: Partial<Components> = {
  pre: DiagramPre as Components['pre'],
  a: ArtifactLink as Components['a'],
};

/**
 * URL transform that preserves the `artifact://` scheme so ArtifactLink can
 * intercept clicks. The default transform replaces non-http(s) schemes with
 * "", which yields href="" — a click then reloads the current page.
 *
 * Narrowed to <a href> only — without this, the exception would also leak
 * `artifact://` through to <img src> and other URL-bearing attributes, where
 * nothing intercepts the click and the browser just renders a broken resource.
 */
export const markdownUrlTransform: UrlTransform = (url, key, node) => {
  if (url.startsWith('artifact://') && key === 'href' && node.tagName === 'a') {
    return url;
  }
  return defaultUrlTransform(url);
};
