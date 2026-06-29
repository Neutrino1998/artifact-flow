import { describe, test, expect, beforeEach } from 'vitest';
import { useUIStore, INITIAL_UI_STATE } from './uiStore';

// 整体复位到导出的初始数据态 —— 新增字段无需改这里,不会有"漏复位 → 泄漏到下个用例"。
function reset() {
  useUIStore.setState(INITIAL_UI_STATE);
}

describe('uiStore activeMode mutual exclusion', () => {
  beforeEach(() => reset());

  // 「同一时刻最多一个接管面板」现在按构造成立:activeMode 是单一枚举,
  // 不可能同时取两个值。下面验证切换即排他(旧 mode 自动消失)。
  test('setActiveMode replaces the previous mode (exclusion by construction)', () => {
    const set = useUIStore.getState().setActiveMode;
    set('userManagement');
    expect(useUIStore.getState().activeMode).toBe('userManagement');
    set('toolUnit');
    expect(useUIStore.getState().activeMode).toBe('toolUnit'); // userManagement gone, no leftover flag
    set('observability');
    expect(useUIStore.getState().activeMode).toBe('observability');
    set('conversationBrowser');
    expect(useUIStore.getState().activeMode).toBe('conversationBrowser');
  });

  test('setActiveMode("none") returns to plain chat (closes any panel)', () => {
    useUIStore.getState().setActiveMode('toolUnit');
    useUIStore.getState().setActiveMode('none');
    expect(useUIStore.getState().activeMode).toBe('none');
  });

  test('entering observability hides the default artifact panel', () => {
    useUIStore.setState({ artifactPanelVisible: true });
    useUIStore.getState().setActiveMode('observability');
    expect(useUIStore.getState().artifactPanelVisible).toBe(false);
  });

  test('switching mode resets all per-mode transient sub-state', () => {
    // userManagement 里攒了选择 + rightView,观测里选了一条会话 —— 切到 toolUnit 应全清。
    useUIStore.setState({
      activeMode: 'userManagement',
      userManagementRightView: { type: 'edit-user', userId: 'u-1' },
      selectionMode: true,
      userManagementSelection: ['u-1', 'u-2'],
      observabilitySelectedConvId: 'conv-1',
      observabilityBrowseVisible: true,
    });
    useUIStore.getState().setActiveMode('toolUnit');

    const s = useUIStore.getState();
    expect(s.activeMode).toBe('toolUnit');
    expect(s.userManagementRightView).toEqual({ type: 'empty' });
    expect(s.selectionMode).toBe(false);
    expect(s.userManagementSelection).toEqual([]);
    expect(s.toolUnitRightView).toEqual({ type: 'empty' });
    expect(s.observabilitySelectedConvId).toBeNull();
    expect(s.observabilityBrowseVisible).toBe(false);
  });

  test('leaving a mode (→ none) clears its sub-state too', () => {
    useUIStore.setState({
      activeMode: 'observability',
      observabilitySelectedConvId: 'conv-1',
      observabilityBrowseVisible: true,
    });
    useUIStore.getState().setActiveMode('none');

    const s = useUIStore.getState();
    expect(s.observabilitySelectedConvId).toBeNull();
    expect(s.observabilityBrowseVisible).toBe(false);
  });

  test('re-entering the current mode is a no-op (does not wipe sub-state)', () => {
    useUIStore.setState({
      activeMode: 'userManagement',
      userManagementRightView: { type: 'edit-user', userId: 'u-1' },
    });
    useUIStore.getState().setActiveMode('userManagement');
    // 重复进同一 mode 不应清掉刚选中的用户(避免重点菜单项把详情重置)
    expect(useUIStore.getState().userManagementRightView).toEqual({ type: 'edit-user', userId: 'u-1' });
  });
});

describe('uiStore rightPanelIntentEpoch', () => {
  beforeEach(() => reset());

  test('toggleArtifactPanel bumps epoch', () => {
    const before = useUIStore.getState().rightPanelIntentEpoch;
    useUIStore.getState().toggleArtifactPanel();
    useUIStore.getState().toggleArtifactPanel();
    expect(useUIStore.getState().rightPanelIntentEpoch).toBe(before + 2);
  });

  test('setArtifactPanelVisible bumps epoch even when value is unchanged', () => {
    const before = useUIStore.getState().rightPanelIntentEpoch;
    useUIStore.getState().setArtifactPanelVisible(false);
    expect(useUIStore.getState().rightPanelIntentEpoch).toBe(before + 1);
  });

  test('entering user management bumps epoch (right panel re-targets to detail)', () => {
    const before = useUIStore.getState().rightPanelIntentEpoch;
    useUIStore.getState().setActiveMode('userManagement');
    expect(useUIStore.getState().rightPanelIntentEpoch).toBe(before + 1);
  });

  test('leaving user management (→ none) bumps epoch (right panel releases back)', () => {
    useUIStore.getState().setActiveMode('userManagement');
    const before = useUIStore.getState().rightPanelIntentEpoch;
    useUIStore.getState().setActiveMode('none');
    expect(useUIStore.getState().rightPanelIntentEpoch).toBe(before + 1);
  });

  test('entering tool-unit bumps epoch (master-detail re-target)', () => {
    const before = useUIStore.getState().rightPanelIntentEpoch;
    useUIStore.getState().setActiveMode('toolUnit');
    expect(useUIStore.getState().rightPanelIntentEpoch).toBe(before + 1);
  });

  test('entering observability bumps epoch (full-screen takeover)', () => {
    const before = useUIStore.getState().rightPanelIntentEpoch;
    useUIStore.getState().setActiveMode('observability');
    expect(useUIStore.getState().rightPanelIntentEpoch).toBe(before + 1);
  });

  test('conversationBrowser does NOT bump (middle-panel takeover, not right)', () => {
    const before = useUIStore.getState().rightPanelIntentEpoch;
    useUIStore.getState().setActiveMode('conversationBrowser');
    useUIStore.getState().setActiveMode('none');
    expect(useUIStore.getState().rightPanelIntentEpoch).toBe(before);
  });
});

describe('uiStore list versions', () => {
  beforeEach(() => reset());

  test('bumpUserMgmtListVersion / bumpToolUnitListVersion increment independently', () => {
    useUIStore.getState().bumpUserMgmtListVersion();
    useUIStore.getState().bumpToolUnitListVersion();
    useUIStore.getState().bumpToolUnitListVersion();
    const s = useUIStore.getState();
    expect(s.userMgmtListVersion).toBe(1);
    expect(s.toolUnitListVersion).toBe(2);
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

describe('uiStore rightView payloads', () => {
  beforeEach(() => reset());

  test('setUserManagementRightView updates view payload', () => {
    useUIStore.getState().setUserManagementRightView({ type: 'edit-user', userId: 'u-1' });
    expect(useUIStore.getState().userManagementRightView).toEqual({ type: 'edit-user', userId: 'u-1' });
  });

  test('setToolUnitRightView updates view payload', () => {
    useUIStore.getState().setToolUnitRightView({ type: 'edit-unit', unitName: 'weather_api' });
    expect(useUIStore.getState().toolUnitRightView).toEqual({ type: 'edit-unit', unitName: 'weather_api' });
  });
});
