import { describe, expect, it } from 'vitest';
import {
  findComposerTrigger,
  matchesComposerQuery,
  removeComposerTrigger,
  shouldCommitComposerSelection,
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

  it('removes the full token when selection happens from the middle', () => {
    const text = '比较 @report 内容';
    const trigger = findComposerTrigger(text, 7);

    expect(trigger).toMatchObject({ query: 'rep', start: 3, end: 10 });
    expect(removeComposerTrigger(text, trigger!)).toEqual({
      text: '比较  内容',
      caret: 3,
    });
  });

  it('commits suggestions only for an eligible key with a real candidate', () => {
    expect(shouldCommitComposerSelection('Enter', {
      shiftKey: false,
      isComposing: false,
      suggestionCount: 1,
    })).toBe(true);
    expect(shouldCommitComposerSelection('Tab', {
      shiftKey: false,
      isComposing: false,
      suggestionCount: 1,
    })).toBe(true);
    expect(shouldCommitComposerSelection('Enter', {
      shiftKey: false,
      isComposing: false,
      suggestionCount: 0,
    })).toBe(false);
    expect(shouldCommitComposerSelection('Enter', {
      shiftKey: true,
      isComposing: false,
      suggestionCount: 1,
    })).toBe(false);
    expect(shouldCommitComposerSelection('Enter', {
      shiftKey: false,
      isComposing: true,
      suggestionCount: 1,
    })).toBe(false);
  });

  it('matches names and descriptions case-insensitively', () => {
    expect(matchesComposerQuery('DOC', 'Word 文档', 'Create docx files')).toBe(true);
    expect(matchesComposerQuery('pdf', 'Word 文档', 'Create docx files')).toBe(false);
  });
});
