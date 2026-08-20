import { describe, expect, it } from 'vitest';
import {
  findComposerTrigger,
  matchesComposerQuery,
  removeComposerTrigger,
} from './composerTrigger';

describe('composerTrigger', () => {
  it('recognizes file and skill triggers at token boundaries', () => {
    expect(findComposerTrigger('@报告', 3)).toMatchObject({ kind: 'file', query: '报告' });
    expect(findComposerTrigger('请使用 /doc', 8)).toMatchObject({ kind: 'skill', query: 'doc' });
  });

  it('does not trigger inside email addresses, URLs, or paths', () => {
    expect(findComposerTrigger('a@b.com', 7)).toBeNull();
    expect(findComposerTrigger('https://example.com', 19)).toBeNull();
    expect(findComposerTrigger('foo/bar', 7)).toBeNull();
  });

  it('removes only the active trigger token', () => {
    const text = '请比较 @report 后面的内容';
    const trigger = findComposerTrigger(text, 11);
    expect(trigger).not.toBeNull();
    expect(removeComposerTrigger(text, trigger!)).toEqual({
      text: '请比较  后面的内容',
      caret: 4,
    });
  });

  it('matches names and descriptions case-insensitively', () => {
    expect(matchesComposerQuery('DOC', 'Word 文档', 'Create docx files')).toBe(true);
    expect(matchesComposerQuery('pdf', 'Word 文档', 'Create docx files')).toBe(false);
  });
});
