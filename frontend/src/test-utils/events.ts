import type { MessageEventItem } from '@/lib/api';

let _seq = 0;

/** Reset the auto-incrementing timestamp counter. Call in `beforeEach` if a
 *  test needs deterministic IDs. */
export function resetEventSeq() {
  _seq = 0;
}

/** Build a MessageEventItem fixture with sane defaults. Each call advances an
 *  internal counter so `created_at` values are unique and ordered, mirroring
 *  the real backend behavior where events arrive in time order. */
export function makeEvent(
  event_type: string,
  data: Record<string, unknown> | null = {},
  agent_name: string | null = null,
  created_at?: string,
): MessageEventItem {
  _seq += 1;
  return {
    id: `evt-${_seq}`,
    event_type,
    agent_name,
    data,
    created_at: created_at ?? `2026-01-01T00:00:${String(_seq).padStart(2, '0')}Z`,
  };
}
