'use client';

import { useState, useCallback } from 'react';
import { useStreamStore } from '@/stores/streamStore';
import * as api from '@/lib/api';

export default function PermissionModal() {
  const permissionRequest = useStreamStore((s) => s.permissionRequest);
  const [loading, setLoading] = useState(false);

  const handleResponse = useCallback(
    async (approved: boolean, alwaysAllow: boolean = false) => {
      // Read current values from store to avoid stale closure issues
      const { permissionRequest: req, conversationId, messageId } =
        useStreamStore.getState();
      if (!req || !conversationId || !messageId) return;
      setLoading(true);
      try {
        await api.resumeExecution(conversationId, {
          message_id: messageId,
          approved,
          always_allow: alwaysAllow,
        });

        // No SSE reconnection needed — the existing connection stays alive
        // while the engine blocks on asyncio.Event.wait(). Resolving the
        // interrupt (above) lets the engine continue; events flow on the
        // same SSE stream.
        useStreamStore.getState().setPermissionRequest(null);
      } catch (err) {
        console.error('Failed to resume:', err);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  if (!permissionRequest) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-card shadow-modal max-w-md w-full mx-4 p-6">
        {/* Header */}
        <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark mb-1">
          需要授权
        </h2>
        <p className="text-text-secondary dark:text-text-secondary-dark mb-4">
          智能体请求执行以下工具，需要您的确认。
        </p>

        {/* Tool info */}
        <div className="bg-bg dark:bg-bg-dark border border-border dark:border-border-dark rounded-lg p-3 mb-4">
          <div className="font-medium text-text-primary dark:text-text-primary-dark">
            {permissionRequest.toolName}
          </div>
          {Object.keys(permissionRequest.params).length > 0 && (
            <pre className="text-xs text-text-secondary dark:text-text-secondary-dark font-mono overflow-x-auto max-h-40 overflow-y-auto pt-2 mt-2 border-t border-border dark:border-border-dark">
              {JSON.stringify(permissionRequest.params, null, 2)}
            </pre>
          )}
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-3">
          <button
            onClick={() => handleResponse(false)}
            disabled={loading}
            className="px-8 py-2 rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
          >
            拒绝
          </button>
          <button
            onClick={() => handleResponse(true)}
            disabled={loading}
            className="px-8 py-2 rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
          >
            {loading ? '允许中...' : '允许'}
          </button>
          <button
            onClick={() => handleResponse(true, true)}
            disabled={loading}
            className="px-8 py-2 rounded-lg border border-accent text-accent hover:bg-accent/10 disabled:opacity-40 transition-colors"
          >
            始终允许
          </button>
        </div>
      </div>
    </div>
  );
}
