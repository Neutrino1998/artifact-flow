import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { ArtifactDetail } from '@/types';
import { ApiError } from './api';
import { reconcileTerminalArtifact } from './reconcileTerminalArtifact';

const apiMocks = vi.hoisted(() => ({
  getArtifact: vi.fn(),
  getVersion: vi.fn(),
}));

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return { ...actual, ...apiMocks };
});

function detail(): ArtifactDetail {
  return {
    id: 'doc',
    session_id: 'session-1',
    content_type: 'text/markdown',
    title: 'Doc',
    content: 'persisted v2',
    current_version: 2,
    source: 'agent',
    original_filename: null,
    has_blob: false,
    created_at: '',
    updated_at: '',
    versions: [
      { version: 1, update_type: 'create', created_at: '' },
      { version: 2, update_type: 'update', created_at: '' },
    ],
  };
}

describe('reconcileTerminalArtifact', () => {
  beforeEach(() => vi.resetAllMocks());

  test('commits a persisted detail together with its DB diff base', async () => {
    const artifact = detail();
    apiMocks.getArtifact.mockResolvedValue(artifact);
    apiMocks.getVersion.mockResolvedValue({
      version: 1,
      content: 'persisted v1',
      update_type: 'create',
      created_at: '',
    });
    const commitPresent = vi.fn();
    const commitMissing = vi.fn();

    await reconcileTerminalArtifact({
      sessionId: 'session-1',
      artifactId: 'doc',
      isOwner: () => true,
      commitPresent,
      commitMissing,
    });

    expect(commitPresent).toHaveBeenCalledWith(artifact, 'persisted v1');
    expect(commitMissing).not.toHaveBeenCalled();
  });

  test('commits detail 404 as an authoritative missing result', async () => {
    apiMocks.getArtifact.mockRejectedValue(new ApiError(404, 'Not found'));
    const commitPresent = vi.fn();
    const commitMissing = vi.fn();

    await reconcileTerminalArtifact({
      sessionId: 'session-1',
      artifactId: 'ghost',
      isOwner: () => true,
      commitPresent,
      commitMissing,
    });

    expect(commitMissing).toHaveBeenCalledWith('ghost');
    expect(commitPresent).not.toHaveBeenCalled();
  });

  test('does not delete on an unknown server failure', async () => {
    apiMocks.getArtifact.mockRejectedValue(new ApiError(503, 'Unavailable'));
    const commitPresent = vi.fn();
    const commitMissing = vi.fn();

    await reconcileTerminalArtifact({
      sessionId: 'session-1',
      artifactId: 'doc',
      isOwner: () => true,
      commitPresent,
      commitMissing,
    });

    expect(commitPresent).not.toHaveBeenCalled();
    expect(commitMissing).not.toHaveBeenCalled();
  });

  test('drops a 404 after terminal ownership expires', async () => {
    apiMocks.getArtifact.mockRejectedValue(new ApiError(404, 'Not found'));
    const commitMissing = vi.fn();

    await reconcileTerminalArtifact({
      sessionId: 'session-1',
      artifactId: 'ghost',
      isOwner: () => false,
      commitPresent: vi.fn(),
      commitMissing,
    });

    expect(commitMissing).not.toHaveBeenCalled();
  });
});
