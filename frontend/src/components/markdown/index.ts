import type { Components } from 'react-markdown';
import { defaultUrlTransform } from 'react-markdown';
import CodeBlock from './CodeBlock';
import ArtifactLink from './ArtifactLink';

export const markdownComponents: Partial<Components> = {
  pre: CodeBlock as Components['pre'],
  a: ArtifactLink as Components['a'],
};

/**
 * URL transform that preserves the `artifact://` scheme so ArtifactLink can
 * intercept clicks. The default transform replaces non-http(s) schemes with
 * "", which yields href="" — a click then reloads the current page.
 */
export function markdownUrlTransform(url: string): string {
  if (url.startsWith('artifact://')) return url;
  return defaultUrlTransform(url);
}
