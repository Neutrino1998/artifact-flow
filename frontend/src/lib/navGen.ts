/**
 * Module-level monotonic navigation generation counter.
 *
 * Both `switchConversation` and `startNewChat` bump this on entry, providing
 * a single authoritative "did the user navigate away?" signal that is
 * synchronously set BEFORE any await. Used by:
 *
 *   - useChat.switchConversation: capture-at-entry, drop the late
 *     getConversation response if a newer navigation has fired.
 *   - useSSE.refreshAfterComplete: capture-before-await, drop the entire
 *     post-stream refresh if the user navigated mid-flight. Critical because
 *     `current?.id` alone cannot distinguish "first-message new conv"
 *     (legit null) from "user clicked into another conv whose detail hasn't
 *     resolved yet" (current still null) from "user clicked new chat"
 *     (current explicitly nulled).
 *
 * Generation is process-lifetime monotonic; never reset outside tests.
 */

let _navGen = 0;

export function getNavGen(): number {
  return _navGen;
}

export function bumpNavGen(): number {
  return ++_navGen;
}

/** Test-only: reset counter for deterministic test setup. */
export function _resetNavGenForTests(): void {
  _navGen = 0;
}
