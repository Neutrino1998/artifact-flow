export type ComposerTriggerKind = 'file' | 'skill';

export interface ComposerTrigger {
  kind: ComposerTriggerKind;
  marker: '@' | '/';
  start: number;
  end: number;
  query: string;
}

/** Find an @ or / token immediately before the caret.
 *
 * Triggers are recognized only at the start of the input or after whitespace.
 * This keeps email addresses, URLs, and paths from opening the menu. The query
 * ends at whitespace, matching the lightweight token semantics of a textarea.
 */
export function findComposerTrigger(
  text: string,
  caret: number,
): ComposerTrigger | null {
  if (caret < 1 || caret > text.length) return null;

  let start = caret - 1;
  while (start >= 0 && !/\s/.test(text[start])) start -= 1;
  const tokenStart = start + 1;
  const marker = text[tokenStart];
  if (marker !== '@' && marker !== '/') return null;

  return {
    kind: marker === '@' ? 'file' : 'skill',
    marker,
    start: tokenStart,
    end: caret,
    query: text.slice(tokenStart + 1, caret),
  };
}

export function composerTriggerKey(trigger: ComposerTrigger): string {
  return `${trigger.kind}:${trigger.start}:${trigger.end}:${trigger.query}`;
}

export function removeComposerTrigger(
  text: string,
  trigger: ComposerTrigger,
): { text: string; caret: number } {
  return {
    text: text.slice(0, trigger.start) + text.slice(trigger.end),
    caret: trigger.start,
  };
}

export function matchesComposerQuery(
  query: string,
  ...values: Array<string | null | undefined>
): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  return values.some((value) => value?.toLocaleLowerCase().includes(normalized));
}
