import type { ActiveMode } from '@/stores/uiStore';

export function artifactVisibilityOverride(
  activeMode: ActiveMode,
  isAdmin: boolean,
  isMd: boolean,
): boolean | undefined {
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
