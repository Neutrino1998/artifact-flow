'use client';

import { useState, useCallback } from 'react';
import { useStreamStore } from '@/stores/streamStore';
import { useSSE } from '@/hooks/useSSE';
import * as api from '@/lib/api';

export default function PermissionModal() {
  const permissionRequest = useStreamStore((s) => s.permissionRequest);
  const { connect } = useSSE();
  const [loading, setLoading] = useState(false);

  const handleResponse = useCallback(
    async (approved: boolean) => {
      // Read current values from store to avoid stale closure issues
      const { permissionRequest: req, conversationId, threadId, messageId } =
        useStreamStore.getState();
      if (!req || !conversationId || !threadId || !messageId) return;
      setLoading(true);
      try {
        const res = await api.resumeExecution(conversationId, {
          thread_id: threadId,
          message_id: messageId,
          approved,
        });

        // Reconnect SSE first, then clear permission (clearing unmounts this component)
        useStreamStore.getState().resumeStream(res.stream_url);
        connect(res.stream_url, conversationId, messageId);
        useStreamStore.getState().setPermissionRequest(null);
      } catch (err) {
        console.error('Failed to resume:', err);
      } finally {
        setLoading(false);
      }
    },
    [connect]
  );

  if (!permissionRequest) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-card shadow-modal max-w-md w-full mx-4 p-6">
        {/* Header */}
        <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark mb-1">
          需要授权
        </h2>
        <p className="text-sm text-text-secondary dark:text-text-secondary-dark mb-4">
          智能体请求执行以下工具，需要您的确认。
        </p>

        {/* Tool info */}
        <div className="bg-bg dark:bg-bg-dark border border-border dark:border-border-dark rounded-lg p-3 mb-4">
          <div className="text-sm font-medium text-text-primary dark:text-text-primary-dark mb-2">
            {permissionRequest.toolName}
          </div>
          {Object.keys(permissionRequest.params).length > 0 && (
            <pre className="text-xs text-text-secondary dark:text-text-secondary-dark font-mono overflow-x-auto max-h-40 overflow-y-auto">
              {JSON.stringify(permissionRequest.params, null, 2)}
            </pre>
          )}
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-3">
          <button
            onClick={() => handleResponse(false)}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
          >
            拒绝
          </button>
          <button
            onClick={() => handleResponse(true)}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
          >
            {loading ? '授权中...' : '授权'}
          </button>
        </div>
      </div>
    </div>
  );
}
