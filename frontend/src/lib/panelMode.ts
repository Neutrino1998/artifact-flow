import type { ActiveMode } from '@/stores/uiStore';

export function artifactVisibilityOverride(
  activeMode: ActiveMode,
  isAdmin: boolean,
  isMd: boolean,
): boolean | undefined {
  // Skill management uses the ordinary visibility flag for an explicit,
  // click-opened skill preview. Background artifact auto-open remains blocked
  // by uiStore.RIGHT_PANEL_MODES while this mode is active.
  if (activeMode === 'skills') return undefined;

  if (!isAdmin) return undefined;

  if (
    activeMode === 'departmentAccess' ||
    activeMode === 'observability' ||
    activeMode === 'instances' ||
    activeMode === 'notificationConfig'
  ) {
    return false;
  }

  if (activeMode === 'userManagement' || activeMode === 'toolUnit') {
    return isMd;
  }

  return undefined;
}
