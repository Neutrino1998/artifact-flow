import { describe, expect, it } from 'vitest';

import {
  addSimpleParameter,
  inferHttpParameterLocation,
  inspectInputSchema,
  parseTypedValue,
  removeSimpleParameter,
  renameSimpleParameter,
  setRejectUnknownParameters,
  setSimpleArrayItemType,
  setSimpleParameterDefault,
  setSimpleParameterDescription,
  setSimpleParameterEnum,
  setSimpleParameterRequired,
  setSimpleParameterType,
} from './inputSchemaForm';


function parse(text: string): Record<string, unknown> {
  return JSON.parse(text) as Record<string, unknown>;
}

describe('inputSchemaForm inspection', () => {
  it('projects the common object schema without changing its semantics', () => {
    const inspected = inspectInputSchema(JSON.stringify({
      type: 'object',
      properties: {
        question: { type: 'string', description: '检索问题' },
        dataset_ids: {
          type: 'array',
          items: { type: 'string' },
          default: ['dataset-a'],
          enum: [['dataset-a'], ['dataset-b']],
        },
      },
      required: ['question'],
      additionalProperties: false,
    }));

    expect(inspected.kind).toBe('simple');
    if (inspected.kind !== 'simple') return;
    expect(inspected.rejectUnknown).toBe(true);
    expect(inspected.parameters).toMatchObject([
      { name: 'question', type: 'string', required: true },
      {
        name: 'dataset_ids',
        type: 'array',
        itemType: 'string',
        required: false,
        hasDefault: true,
      },
    ]);
  });

  it('routes unrepresentable constraints to advanced JSON instead of dropping them', () => {
    const rootConstraint = inspectInputSchema(JSON.stringify({
      type: 'object',
      properties: {},
      minProperties: 1,
    }));
    const nestedObject = inspectInputSchema(JSON.stringify({
      type: 'object',
      properties: {
        payload: {
          type: 'object',
          properties: { id: { type: 'string' } },
        },
      },
    }));

    expect(rootConstraint).toMatchObject({ kind: 'advanced' });
    expect(nestedObject).toMatchObject({ kind: 'advanced' });
  });

  it('reports invalid JSON separately from valid advanced schemas', () => {
    expect(inspectInputSchema('{')).toMatchObject({ kind: 'invalid' });
    expect(inspectInputSchema(JSON.stringify({ type: 'array' }))).toMatchObject({
      kind: 'invalid',
    });
  });

  it('rejects a default outside enum while accepting deep-equal structured values', () => {
    expect(inspectInputSchema(JSON.stringify({
      type: 'object',
      properties: {
        query: { type: 'string', default: '', enum: ['value'] },
      },
    }))).toMatchObject({
      kind: 'invalid',
      reason: '参数 query 的默认值必须是可选值之一',
    });

    expect(inspectInputSchema(JSON.stringify({
      type: 'object',
      properties: {
        filters: {
          type: 'object',
          default: { region: 'cn', active: true },
          enum: [{ active: true, region: 'cn' }],
        },
      },
    }))).toMatchObject({ kind: 'simple' });
  });

  it('routes parameter names that a single-line form cannot preserve to JSON mode', () => {
    for (const name of [' user_id ', 'user\nid']) {
      expect(inspectInputSchema(JSON.stringify({
        type: 'object',
        properties: { [name]: { type: 'string' } },
      }))).toMatchObject({ kind: 'advanced' });
    }
  });
});

describe('inputSchemaForm mutations', () => {
  const base = JSON.stringify({
    type: 'object',
    properties: {
      query: { type: 'string', description: 'old', default: 'hello' },
    },
    required: ['query'],
    additionalProperties: false,
  });

  it('renames properties and their required reference together', () => {
    const renamed = parse(renameSimpleParameter(base, 'query', 'question'));

    expect(renamed.properties).toEqual({
      question: { type: 'string', description: 'old', default: 'hello' },
    });
    expect(renamed.required).toEqual(['question']);
  });

  it('adds and removes parameters without leaving dangling required names', () => {
    const added = addSimpleParameter(base);
    expect(Object.keys(parse(added).properties as object)).toEqual(['query', 'param_2']);

    const removed = parse(removeSimpleParameter(added, 'query'));
    expect(removed.properties).toEqual({ param_2: { type: 'string' } });
    expect(removed).not.toHaveProperty('required');
  });

  it('edits type-aware defaults, enums, arrays, required, and unknown-key policy', () => {
    let schema = setSimpleParameterType(base, 'query', 'array');
    schema = setSimpleArrayItemType(schema, 'query', 'string');
    schema = setSimpleParameterDefault(schema, 'query', true, ['a']);
    schema = setSimpleParameterEnum(schema, 'query', [['a'], ['b']]);
    schema = setSimpleParameterRequired(schema, 'query', false);
    schema = setRejectUnknownParameters(schema, false);

    expect(parse(schema)).toEqual({
      type: 'object',
      properties: {
        query: {
          type: 'array',
          description: 'old',
          items: { type: 'string' },
          default: ['a'],
          enum: [['a'], ['b']],
        },
      },
      additionalProperties: true,
    });
  });

  it('refuses defaults that do not match the selected type', () => {
    expect(() => setSimpleParameterDefault(base, 'query', true, 3)).toThrow(
      '默认值与参数类型不匹配',
    );
  });

  it('keeps default and enum compatible in both mutation directions', () => {
    const withEnum = JSON.stringify({
      type: 'object',
      properties: { query: { type: 'string', enum: ['allowed'] } },
    });
    expect(() => setSimpleParameterDefault(withEnum, 'query', true, 'other')).toThrow(
      '默认值必须是可选值之一',
    );

    const withDefault = JSON.stringify({
      type: 'object',
      properties: { query: { type: 'string', default: 'allowed' } },
    });
    expect(() => setSimpleParameterEnum(withDefault, 'query', ['other'])).toThrow(
      '可选值必须包含当前默认值',
    );
    expect(inspectInputSchema(
      setSimpleParameterEnum(withDefault, 'query', ['allowed', 'other']),
    )).toMatchObject({ kind: 'simple' });
  });

  it('preserves multiline descriptions and string defaults', () => {
    let schema = setSimpleParameterDescription(base, 'query', '第一行\n第二行');
    schema = setSimpleParameterDefault(schema, 'query', true, '默认第一行\n默认第二行');

    expect(parse(schema)).toMatchObject({
      properties: {
        query: {
          description: '第一行\n第二行',
          default: '默认第一行\n默认第二行',
        },
      },
    });
    expect(inspectInputSchema(schema)).toMatchObject({ kind: 'simple' });
  });

  it('rejects parameter names that would be silently normalized', () => {
    expect(() => renameSimpleParameter(base, 'query', ' question ')).toThrow(
      '参数名首尾不能包含空白字符',
    );
    expect(() => renameSimpleParameter(base, 'query', 'question\nnext')).toThrow(
      '参数名不能包含换行或控制字符',
    );
  });
});

describe('inputSchemaForm value and request helpers', () => {
  it('parses typed field values', () => {
    expect(parseTypedValue('42', 'integer')).toBe(42);
    expect(parseTypedValue('["a"]', 'array')).toEqual(['a']);
    expect(() => parseTypedValue('4.2', 'integer')).toThrow('请输入整数');
  });

  it('infers URL path, JSON body, and query locations', () => {
    expect(inferHttpParameterLocation('/datasets/{dataset_id}', 'GET', 'dataset_id'))
      .toBe('URL Path');
    expect(inferHttpParameterLocation('/search', 'POST', 'question')).toBe('JSON Body');
    expect(inferHttpParameterLocation('/search', 'GET', 'question')).toBe('Query String');
  });
});
