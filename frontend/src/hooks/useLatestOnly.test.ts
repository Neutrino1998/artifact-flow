import { describe, it, expect } from 'vitest';
import { createLatestOnlyClaim } from './useLatestOnly';

describe('createLatestOnlyClaim', () => {
  it('first claim is latest until a second claim fires', () => {
    const claim = createLatestOnlyClaim();
    const isA = claim();
    expect(isA()).toBe(true);

    const isB = claim();
    // Second claim supersedes the first immediately.
    expect(isA()).toBe(false);
    expect(isB()).toBe(true);
  });

  it('only the most recent claim is latest after many bumps', () => {
    const claim = createLatestOnlyClaim();
    const a = claim();
    const b = claim();
    const c = claim();
    expect(a()).toBe(false);
    expect(b()).toBe(false);
    expect(c()).toBe(true);
  });

  it('isLatest is sticky-false: once superseded, never returns true again', () => {
    // Simulates the race shape: A captured first, B fires while A is
    // in flight, A's late response checks isLatest and must see false.
    const claim = createLatestOnlyClaim();
    const a = claim();
    claim(); // B fires while A is "awaiting"
    expect(a()).toBe(false);
    claim(); // C fires later
    expect(a()).toBe(false);
  });

  it('separate factory instances do not share state', () => {
    // Mirrors the per-component-instance contract of useLatestOnly:
    // two sibling components must not invalidate each other.
    const f1 = createLatestOnlyClaim();
    const f2 = createLatestOnlyClaim();
    const a = f1();
    const b = f2();
    expect(a()).toBe(true);
    expect(b()).toBe(true);

    f1(); // bumps f1 only
    expect(a()).toBe(false);
    expect(b()).toBe(true);
  });

  it('models the search → load-more flow: latest replace wins, stale page drops', () => {
    // claim1: search "a" fires
    // claim2: search "ab" fires (supersedes "a")
    // "a" response arrives late → must drop
    // "ab" response arrives → applies
    // claim3: load-more for "ab" page 2
    // user retypes to "abc": claim4
    // load-more "ab" page 2 arrives → must drop (would pollute "abc")
    const claim = createLatestOnlyClaim();
    const searchA = claim();
    const searchAB = claim();
    expect(searchA()).toBe(false); // "a" stale
    expect(searchAB()).toBe(true); // "ab" applies

    const loadMoreAB = claim();
    const searchABC = claim();
    expect(loadMoreAB()).toBe(false); // late page-2 of "ab" drops
    expect(searchABC()).toBe(true);
  });
});
