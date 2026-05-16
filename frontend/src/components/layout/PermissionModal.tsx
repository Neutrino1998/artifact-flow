'use client';

import { useState, useCallback } from 'react';
import { useStreamStore } from '@/stores/streamStore';
import * as api from '@/lib/api';
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from '@/lib/styles';
import DialogShell from './DialogShell';

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

  // Deny on ESC / backdrop — symmetric with explicit "拒绝" button.
  // Disabled while a response is in-flight to avoid double-dispatch.
  const handleClose = () => {
    if (!loading) handleResponse(false);
  };

  return (
    <DialogShell
      title="需要授权"
      description="智能体请求执行以下工具，需要您的确认。"
      size="md"
      onClose={handleClose}
      closeOnBackdrop={!loading}
      closeOnEscape={!loading}
      footer={
        <>
          <button
            onClick={() => handleResponse(false)}
            disabled={loading}
            className={`${BUTTON_SECONDARY} rounded-lg px-8 py-2`}
          >
            拒绝
          </button>
          <button
            onClick={() => handleResponse(true, true)}
            disabled={loading}
            className={`${BUTTON_SECONDARY} rounded-lg px-8 py-2`}
          >
            始终允许
          </button>
          <button
            onClick={() => handleResponse(true)}
            disabled={loading}
            className={`${BUTTON_PRIMARY} rounded-lg px-8 py-2`}
          >
            {loading ? '允许中...' : '允许一次'}
          </button>
        </>
      }
    >
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
    </DialogShell>
  );
}
