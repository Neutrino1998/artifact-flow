import type { SSEEvent } from '@/types/events';
import { useAuthStore } from '@/stores/authStore';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export interface SSEHandlers {
  onEvent: (event: SSEEvent) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

export interface SSEConnection {
  /** Last received SSE event ID (for reconnection) */
  lastEventId: string | null;
}

export function connectSSE(
  streamUrl: string,
  handlers: SSEHandlers,
  signal?: AbortSignal,
  lastEventId?: string | null,
): SSEConnection {
  const connection: SSEConnection = { lastEventId: null };
  const url = streamUrl.startsWith('http')
    ? streamUrl
    : `${BASE_URL}${streamUrl}`;

  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const token = useAuthStore.getState().token;
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  if (lastEventId) {
    headers['Last-Event-ID'] = lastEventId;
  }

  fetch(url, { headers, signal })
    .then(async (res) => {
      if (res.status === 401) {
        useAuthStore.getState().logout();
        const err = new Error('Session expired') as Error & { status?: number };
        err.status = 401;
        handlers.onError?.(err);
        return;
      }

      if (!res.ok || !res.body) {
        const err = new Error(`SSE connection failed: ${res.status}`) as Error & { status?: number };
        err.status = res.status;
        handlers.onError?.(err);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let currentId: string | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (signal?.aborted) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith('id:')) {
            currentId = line.slice(3).trim();
          } else if (line.startsWith('data:')) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;
            try {
              const parsed = JSON.parse(dataStr) as SSEEvent;
              if (!parsed.type && currentEvent) {
                parsed.type = currentEvent as SSEEvent['type'];
              }
              handlers.onEvent(parsed);
              if (currentId) {
                connection.lastEventId = currentId;
              }
            } catch {
              // skip malformed JSON
            }
            currentEvent = '';
            currentId = null;
          }
          // ignore comments (lines starting with ':')
        }
      }

      handlers.onClose?.();
    })
    .catch((err) => {
      if ((err as Error).name === 'AbortError') {
        handlers.onClose?.();
        return;
      }
      handlers.onError?.(err as Error);
    });

  return connection;
}
