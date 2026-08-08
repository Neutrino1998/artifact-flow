import type { ActiveMode } from '@/stores/uiStore';

export function artifactVisibilityOverride(
  activeMode: ActiveMode,
  isAdmin: boolean,
  isMd: boolean,
): boolean | undefined {
  // Skill management is a conversation-independent workspace for every user.
  // Keep the artifact panel out even if a live turn tries to auto-open it.
  if (activeMode === 'skills') return false;

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
