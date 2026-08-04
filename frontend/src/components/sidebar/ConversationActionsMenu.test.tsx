import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import ConversationActionsMenu from './ConversationActionsMenu';


(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe('ConversationActionsMenu', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.replaceChildren();
  });

  it('disables deletion while an execution is active', async () => {
    const onDelete = vi.fn();

    await act(async () => {
      root.render(
        <ConversationActionsMenu
          conversationId="conv-1"
          title="Running conversation"
          visible
          open
          onOpenChange={vi.fn()}
          onDelete={onDelete}
          deleteDisabled
          wrapperClassName="absolute"
        />,
      );
    });

    const deleteButton = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('删除对话'),
    );
    expect(deleteButton).toBeDefined();
    expect(deleteButton?.disabled).toBe(true);

    await act(async () => deleteButton?.click());
    expect(onDelete).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain('确认删除');
  });

  it('keeps the confirmation open and shows a visible delete error', async () => {
    const onDelete = vi.fn().mockRejectedValue(
      new ApiError(409, 'Conversation has an active execution.'),
    );

    await act(async () => {
      root.render(
        <ConversationActionsMenu
          conversationId="conv-1"
          title="Race conversation"
          visible
          open
          onOpenChange={vi.fn()}
          onDelete={onDelete}
          wrapperClassName="absolute"
        />,
      );
    });

    const deleteButton = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('删除对话'),
    );
    await act(async () => deleteButton?.click());

    const confirmButton = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('确认删除'),
    );
    await act(async () => confirmButton?.click());

    expect(onDelete).toHaveBeenCalledWith('conv-1');
    expect(document.body.textContent).toContain('Conversation has an active execution.');
    expect(document.body.textContent).toContain('确认删除');
  });
});
