import { describe, expect, test } from 'vitest';
import { artifactVisibilityOverride } from './panelMode';

describe('artifactVisibilityOverride', () => {
  test('full-screen admin takeovers force-hide the right panel', () => {
    expect(artifactVisibilityOverride('departmentAccess', true, true)).toBe(false);
    expect(artifactVisibilityOverride('observability', true, true)).toBe(false);
    expect(artifactVisibilityOverride('instances', true, true)).toBe(false);
  });

  test('master-detail admin modes force-show only on desktop', () => {
    expect(artifactVisibilityOverride('userManagement', true, true)).toBe(true);
    expect(artifactVisibilityOverride('toolUnit', true, true)).toBe(true);
    expect(artifactVisibilityOverride('userManagement', true, false)).toBe(false);
    expect(artifactVisibilityOverride('toolUnit', true, false)).toBe(false);
  });

  test('ordinary modes and non-admin users defer to artifactPanelVisible', () => {
    expect(artifactVisibilityOverride('none', true, true)).toBeUndefined();
    expect(artifactVisibilityOverride('conversationBrowser', true, true)).toBeUndefined();
    expect(artifactVisibilityOverride('skills', true, true)).toBeUndefined();
    expect(artifactVisibilityOverride('departmentAccess', false, true)).toBeUndefined();
  });
});
