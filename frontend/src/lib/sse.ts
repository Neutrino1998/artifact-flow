import type { SSEEvent } from '@/types/events';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface SSEHandlers {
  onEvent: (event: SSEEvent) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

export function connectSSE(
  streamUrl: string,
  handlers: SSEHandlers,
  signal?: AbortSignal
): void {
  const url = streamUrl.startsWith('http')
    ? streamUrl
    : `${BASE_URL}${streamUrl}`;

  fetch(url, {
    headers: { Accept: 'text/event-stream' },
    signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        handlers.onError?.(new Error(`SSE connection failed: ${res.status}`));
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';

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
          } else if (line.startsWith('data:')) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;
            try {
              const parsed = JSON.parse(dataStr) as SSEEvent;
              if (!parsed.type && currentEvent) {
                parsed.type = currentEvent as SSEEvent['type'];
              }
              handlers.onEvent(parsed);
            } catch {
              // skip malformed JSON
            }
            currentEvent = '';
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
}
