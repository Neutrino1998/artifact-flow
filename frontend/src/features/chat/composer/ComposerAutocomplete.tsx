'use client';

import { useEffect, useRef } from 'react';
import { FileTypeIcon } from '@/components/ui/FileTypeIcon';
import type { ComposerTriggerKind } from './composerTrigger';

export interface ComposerSuggestion {
  key: string;
  title: string;
  description?: string | null;
  badge?: string;
  selected?: boolean;
  contentType?: string | null;
}

interface ComposerAutocompleteProps {
  kind: ComposerTriggerKind;
  suggestions: ComposerSuggestion[];
  activeIndex: number;
  loading: boolean;
  error: boolean;
  hasConversation: boolean;
  hint?: string;
  emptyText?: string;
  multiSelect?: boolean;
  onActiveIndexChange: (index: number) => void;
  onSelect: (suggestion: ComposerSuggestion) => void;
}

export default function ComposerAutocomplete({
  kind,
  suggestions,
  activeIndex,
  loading,
  error,
  hasConversation,
  hint = '↑↓ 选择 · Enter 确认 · Esc 关闭',
  emptyText: emptyTextOverride,
  multiSelect = false,
  onActiveIndexChange,
  onSelect,
}: ComposerAutocompleteProps) {
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const activeSuggestionKey = suggestions[activeIndex]?.key;
  const heading = kind === 'file' ? '引用会话文件' : '选择技能';
  let emptyText = emptyTextOverride ?? (kind === 'file'
    ? hasConversation
      ? '当前会话没有匹配的已上传文件。'
      : '新对话中还没有可引用的文件。'
    : '没有匹配的技能。');
  if (loading) emptyText = '加载中…';
  if (error) emptyText = kind === 'file' ? '会话文件加载失败，请稍后重试。' : '技能加载失败，请稍后重试。';

  // Arrow-key navigation is owned by the textarea, while this component owns
  // the scroll viewport. Keep the highlighted option visible as the parent
  // advances activeIndex beyond the initially rendered rows.
  useEffect(() => {
    optionRefs.current[activeIndex]?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex, activeSuggestionKey]);

  return (
    <div
      id="composer-autocomplete-list"
      role="listbox"
      aria-label={heading}
      aria-multiselectable={multiSelect || undefined}
      className="fixed inset-x-4 bottom-[calc(7rem+env(safe-area-inset-bottom))] z-30 flex max-h-72 flex-col overflow-hidden rounded-xl bg-surface dark:bg-surface-dark border border-border dark:border-border-dark shadow-float sm:absolute sm:inset-x-0 sm:bottom-full sm:mb-2"
    >
      <div className="shrink-0 px-3 py-2 bg-surface dark:bg-surface-dark border-b border-border dark:border-border-dark text-xs font-medium text-text-secondary dark:text-text-secondary-dark">
        {heading}
        <span className="ml-2 font-normal text-text-tertiary dark:text-text-tertiary-dark">
          {hint}
        </span>
      </div>
      <div className="min-h-0 overflow-y-auto overscroll-contain py-1">
        {suggestions.length > 0 ? (
          suggestions.map((suggestion, index) => (
            <button
              ref={(element) => {
                optionRefs.current[index] = element;
              }}
              key={suggestion.key}
              type="button"
              role="option"
              aria-selected={multiSelect ? Boolean(suggestion.selected) : index === activeIndex}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => onActiveIndexChange(index)}
              onClick={() => onSelect(suggestion)}
              className={`mx-1 my-0.5 flex min-h-11 w-[calc(100%-0.5rem)] items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors ${
                suggestion.selected
                  ? index === activeIndex
                    ? 'border-accent bg-accent/20'
                    : 'border-accent bg-accent/10 hover:bg-accent/15'
                  : index === activeIndex
                    ? 'border-transparent bg-bg dark:bg-bg-dark'
                    : 'border-transparent hover:bg-bg dark:hover:bg-bg-dark'
              }`}
            >
              <span className="flex h-5 w-5 shrink-0 items-center justify-center text-text-tertiary dark:text-text-tertiary-dark">
                {kind === 'file' ? (
                  <FileTypeIcon
                    contentType={suggestion.contentType}
                    filename={suggestion.title}
                    size={16}
                  />
                ) : (
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6.5 2l1 2.7 2.7 1-2.7 1-1 2.7-1-2.7-2.7-1 2.7-1z" />
                    <path d="M11.5 9.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" />
                  </svg>
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-medium text-text-primary dark:text-text-primary-dark">
                    {suggestion.title}
                  </span>
                  {suggestion.badge && (
                    <span className="shrink-0 rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                      {suggestion.badge}
                    </span>
                  )}
                </span>
                {suggestion.description && (
                  <span className="block truncate text-xs text-text-tertiary dark:text-text-tertiary-dark">
                    {suggestion.description}
                  </span>
                )}
              </span>
              {suggestion.selected && (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-accent">
                  <path d="M3 8l3.5 3.5L13 5" />
                </svg>
              )}
            </button>
          ))
        ) : (
          <div className={`px-3 py-4 text-xs ${error ? 'text-status-error' : 'text-text-tertiary dark:text-text-tertiary-dark'}`}>
            {emptyText}
          </div>
        )}
      </div>
    </div>
  );
}
