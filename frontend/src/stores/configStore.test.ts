import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  getClientConfig: vi.fn(),
}));

vi.mock('@/lib/api', () => apiMocks);

import { useConfigStore } from './configStore';

describe('configStore password policy', () => {
  beforeEach(() => {
    apiMocks.getClientConfig.mockReset();
    useConfigStore.setState({
      compactionThreshold: null,
      leadAgentModel: null,
      maxUploadSize: null,
      maxPrivateSkills: null,
      messageFeedbackMaxDetailChars: null,
      passwordPolicy: null,
      fetched: false,
    });
  });

  it('maps the backend runtime policy into frontend naming', async () => {
    apiMocks.getClientConfig.mockResolvedValue({
      compaction_token_threshold: 100_000,
      lead_agent_model: 'test-model',
      max_upload_size: 1024,
      max_private_skills: 3,
      message_feedback_max_detail_chars: 2000,
      password_policy: {
        min_length: 12,
        max_bytes: 72,
        require_letter: true,
        require_digit: true,
        require_symbol: false,
      },
    });

    await useConfigStore.getState().fetchConfig();

    expect(useConfigStore.getState().passwordPolicy).toEqual({
      minLength: 12,
      maxBytes: 72,
      requireLetter: true,
      requireDigit: true,
      requireSymbol: false,
    });
  });
});
