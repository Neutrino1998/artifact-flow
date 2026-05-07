import { describe, test, expect, beforeEach } from 'vitest';
import { useUIStore } from './uiStore';

function reset() {
  useUIStore.setState({
    sidebarCollapsed: false,
    artifactPanelVisible: false,
    conversationBrowserVisible: false,
    userManagementVisible: false,
    userManagementRightView: { type: 'empty' },
    observabilityVisible: false,
    observabilitySelectedConvId: null,
    observabilityBrowseVisible: false,
    observabilityRefreshTick: 0,
  });
}

describe('uiStore panel mutual exclusion', () => {
  beforeEach(() => reset());

  test('opening conversationBrowser closes userManagement and observability', () => {
    useUIStore.setState({ userManagementVisible: true, observabilityVisible: true });
    useUIStore.getState().setConversationBrowserVisible(true);

    const s = useUIStore.getState();
    expect(s.conversationBrowserVisible).toBe(true);
    expect(s.userManagementVisible).toBe(false);
    expect(s.observabilityVisible).toBe(false);
  });

  test('opening userManagement closes conversationBrowser and observability', () => {
    useUIStore.setState({ conversationBrowserVisible: true, observabilityVisible: true });
    useUIStore.getState().setUserManagementVisible(true);

    const s = useUIStore.getState();
    expect(s.userManagementVisible).toBe(true);
    expect(s.conversationBrowserVisible).toBe(false);
    expect(s.observabilityVisible).toBe(false);
  });

  test('opening observability closes conversationBrowser, userManagement, and artifactPanel', () => {
    useUIStore.setState({
      conversationBrowserVisible: true,
      userManagementVisible: true,
      artifactPanelVisible: true,
    });
    useUIStore.getState().setObservabilityVisible(true);

    const s = useUIStore.getState();
    expect(s.observabilityVisible).toBe(true);
    expect(s.conversationBrowserVisible).toBe(false);
    expect(s.userManagementVisible).toBe(false);
    expect(s.artifactPanelVisible).toBe(false);
  });

  test('closing observability also clears observabilitySelectedConvId and BrowseVisible', () => {
    useUIStore.setState({
      observabilityVisible: true,
      observabilitySelectedConvId: 'conv-1',
      observabilityBrowseVisible: true,
    });
    useUIStore.getState().setObservabilityVisible(false);

    const s = useUIStore.getState();
    expect(s.observabilityVisible).toBe(false);
    expect(s.observabilitySelectedConvId).toBeNull();
    expect(s.observabilityBrowseVisible).toBe(false);
  });

  test('setting setConversationBrowserVisible(false) does NOT touch other panels', () => {
    useUIStore.setState({ userManagementVisible: true, observabilityVisible: true });
    useUIStore.getState().setConversationBrowserVisible(false);

    const s = useUIStore.getState();
    expect(s.conversationBrowserVisible).toBe(false);
    // Other panels untouched (mutual-exclusion only fires on open)
    expect(s.userManagementVisible).toBe(true);
    expect(s.observabilityVisible).toBe(true);
  });
});

describe('uiStore observability sub-state', () => {
  beforeEach(() => reset());

  test('setObservabilitySelectedConvId clears observabilityBrowseVisible', () => {
    useUIStore.setState({ observabilityBrowseVisible: true });
    useUIStore.getState().setObservabilitySelectedConvId('conv-42');

    const s = useUIStore.getState();
    expect(s.observabilitySelectedConvId).toBe('conv-42');
    expect(s.observabilityBrowseVisible).toBe(false);
  });

  test('triggerObservabilityRefresh increments tick', () => {
    expect(useUIStore.getState().observabilityRefreshTick).toBe(0);
    useUIStore.getState().triggerObservabilityRefresh();
    useUIStore.getState().triggerObservabilityRefresh();
    expect(useUIStore.getState().observabilityRefreshTick).toBe(2);
  });
});

describe('uiStore userManagementRightView', () => {
  beforeEach(() => reset());

  test('setUserManagementRightView updates view payload', () => {
    useUIStore.getState().setUserManagementRightView({ type: 'edit-user', userId: 'u-1' });

    const view = useUIStore.getState().userManagementRightView;
    expect(view).toEqual({ type: 'edit-user', userId: 'u-1' });
  });

  test('closing user management resets RightView to empty', () => {
    useUIStore.setState({
      userManagementVisible: true,
      userManagementRightView: { type: 'edit-user', userId: 'u-1' },
    });
    useUIStore.getState().setUserManagementVisible(false);

    const s = useUIStore.getState();
    expect(s.userManagementVisible).toBe(false);
    expect(s.userManagementRightView).toEqual({ type: 'empty' });
  });

  test('opening user management does NOT touch RightView (caller controls)', () => {
    // simulate stale state from a prior session (shouldn't happen in practice, but verifies opener doesn't overwrite)
    useUIStore.setState({ userManagementRightView: { type: 'create-user' } });
    useUIStore.getState().setUserManagementVisible(true);

    expect(useUIStore.getState().userManagementRightView).toEqual({ type: 'create-user' });
  });

  test('opening conversationBrowser also resets RightView (sibling-panel path)', () => {
    useUIStore.setState({
      userManagementVisible: true,
      userManagementRightView: { type: 'edit-user', userId: 'u-1' },
    });
    useUIStore.getState().setConversationBrowserVisible(true);

    const s = useUIStore.getState();
    expect(s.userManagementVisible).toBe(false);
    expect(s.userManagementRightView).toEqual({ type: 'empty' });
  });

  test('opening observability also resets RightView (sibling-panel path)', () => {
    useUIStore.setState({
      userManagementVisible: true,
      userManagementRightView: { type: 'edit-user', userId: 'u-1' },
    });
    useUIStore.getState().setObservabilityVisible(true);

    const s = useUIStore.getState();
    expect(s.userManagementVisible).toBe(false);
    expect(s.userManagementRightView).toEqual({ type: 'empty' });
  });
});
