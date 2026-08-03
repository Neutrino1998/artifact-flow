import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import ToolUnitEditor, { emptyUnitDraft, type UnitDraft } from './ToolUnitEditor';


function Harness({ initial }: { initial: UnitDraft }) {
  const [draft, setDraft] = useState(initial);
  return <ToolUnitEditor value={draft} onChange={setDraft} />;
}

function buttonByText(container: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button'))
    .find((button) => button.textContent?.trim() === text);
}

describe('ToolUnitEditor input schema modes', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('defaults to the parameter form and generates canonical JSON from it', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'search';
    draft.members[0].method = 'POST';
    draft.members[0].endpoint = '/search';

    await act(async () => root.render(<Harness initial={draft} />));

    expect(container.textContent).toContain('这个工具暂时没有输入参数');
    await act(async () => buttonByText(container, '+ 添加参数')?.click());

    expect(container.querySelector('input[aria-label="参数名 param_1"]')).not.toBeNull();
    expect(container.textContent).toContain('JSON Body');

    await act(async () => buttonByText(container, '高级 JSON Schema')?.click());
    const json = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="输入参数 JSON Schema"]',
    )?.value;
    expect(JSON.parse(json ?? '{}')).toEqual({
      type: 'object',
      properties: { param_1: { type: 'string' } },
      additionalProperties: false,
    });
  });

  it('keeps advanced constraints in JSON mode and refuses a lossy form projection', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'advanced';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: {},
      minProperties: 1,
    }, null, 2);

    await act(async () => root.render(<Harness initial={draft} />));

    expect(container.querySelector('textarea[aria-label="输入参数 JSON Schema"]')).not.toBeNull();
    expect(container.textContent).toContain('根级字段 minProperties');

    await act(async () => buttonByText(container, '参数表单')?.click());
    expect(container.textContent).toContain('当前 Schema 包含高级约束');
    expect(container.textContent).toContain('根级字段 minProperties');
  });
});
