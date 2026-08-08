import { getNavGen } from '@/lib/navGen';
import { useStreamStore } from '@/stores/streamStore';

/**
 * Whether a terminal reconciliation still owns conversation-scoped UI writes.
 * The message id is the existing same-conversation turn generation; nav-gen
 * covers switching conversations or starting a fresh chat.
 */
export function isTerminalRefreshOwner(
  capturedNavGen: number,
  terminalMessageId: string | null,
): boolean {
  return (
    capturedNavGen === getNavGen() &&
    useStreamStore.getState().messageId === terminalMessageId
  );
}
