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

  it('renders multiline descriptions and string defaults with lossless controls', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'multiline';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: '说明第一行\n说明第二行',
          default: '默认第一行\n默认第二行',
        },
      },
    }, null, 2);

    await act(async () => root.render(<Harness initial={draft} />));

    expect(container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="参数 query 说明"]',
    )?.value).toBe('说明第一行\n说明第二行');
    expect(container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="参数 query 默认值"]',
    )?.value).toBe('默认第一行\n默认第二行');
  });

  it('keeps unsafe parameter names exclusively in lossless JSON mode', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'unsafe-name';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: { ' user_id ': { type: 'string' } },
    }, null, 2);

    await act(async () => root.render(<Harness initial={draft} />));

    expect(container.querySelector('textarea[aria-label="输入参数 JSON Schema"]')).not.toBeNull();
    expect(container.textContent).toContain('参数名首尾不能包含空白字符');

    await act(async () => buttonByText(container, '参数表单')?.click());
    expect(container.textContent).toContain('当前 Schema 包含高级约束');
    expect(container.querySelector('input[aria-label="参数名  user_id "]')).toBeNull();
  });

  it('never creates an enum that excludes the current default', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'default-enum';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: { query: { type: 'string', default: '' } },
    }, null, 2);

    await act(async () => root.render(<Harness initial={draft} />));
    const enumToggle = container.querySelector<HTMLInputElement>(
      'input[aria-label="限制可选值"]',
    );
    await act(async () => enumToggle?.click());

    expect(enumToggle?.checked).toBe(false);
    expect(container.textContent).toContain('空字符串或含换行 enum 需要在高级 JSON Schema 中编辑');
  });

  it('initializes a newly enabled default from the first enum value', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'enum-default';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: { query: { type: 'string', enum: ['allowed', 'other'] } },
    }, null, 2);

    await act(async () => root.render(<Harness initial={draft} />));
    await act(async () => container.querySelector<HTMLInputElement>(
      'input[aria-label="设置默认值"]',
    )?.click());

    expect(container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="参数 query 默认值"]',
    )?.value).toBe('allowed');
  });

  it('routes CR strings to JSON mode before textarea normalization', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'cr-description';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: {
        query: { type: 'string', description: '第一行\r\n第二行' },
      },
    }, null, 2);

    await act(async () => root.render(<Harness initial={draft} />));

    expect(container.querySelector('textarea[aria-label="输入参数 JSON Schema"]')).not.toBeNull();
    expect(container.textContent).toContain('description 含有 CR 换行');
    expect(container.querySelector('textarea[aria-label="参数 query 说明"]')).toBeNull();
  });

  it('does not hide falsey or prototype-named required constraints', async () => {
    const draft = emptyUnitDraft();
    draft.name = 'special-required';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: {},
      required: ['constructor'],
    }, null, 2);

    await act(async () => root.render(<Harness initial={draft} />));

    expect(container.querySelector('textarea[aria-label="输入参数 JSON Schema"]')).not.toBeNull();
    expect(container.textContent).toContain('required 中的 "constructor" 未在 properties 声明');
    expect(container.textContent).not.toContain('这个工具暂时没有输入参数');
  });
});
