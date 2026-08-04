import { describe, expect, it } from 'vitest';

import { draftToRequest, emptyUnitDraft } from './ToolUnitEditor';


describe('draftToRequest input_schema normalization', () => {
  it('fills omitted properties with an empty object', () => {
    const draft = emptyUnitDraft();
    draft.name = 'minimal';
    draft.members[0].input_schema = JSON.stringify({ type: 'object' });

    const request = draftToRequest(draft);

    expect(request.members?.[0].input_schema).toEqual({
      type: 'object',
      properties: {},
    });
  });

  it('still rejects an explicitly non-object properties value', () => {
    const draft = emptyUnitDraft();
    draft.name = 'invalid';
    draft.members[0].input_schema = JSON.stringify({
      type: 'object',
      properties: null,
    });

    expect(() => draftToRequest(draft)).toThrow('input_schema.properties 必须是对象');
  });
});
