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
