// Client-side mirror of backend caps. Keep in sync with src/config.py.

// Max characters for a single user message / inject. Mirrors
// config.MAX_MESSAGE_CHARS (backend enforces with a 422; this is UX only).
// A paste larger than this is diverted to a staged .txt attachment instead
// of being inlined into the message.
export const MAX_MESSAGE_CHARS = 20000;
