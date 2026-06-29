import { create } from 'zustand';

function applyTheme(theme: 'light' | 'dark') {
  if (typeof document !== 'undefined') {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }
}

export type UserMgmtRightView =
  | { type: 'empty' }
  | { type: 'create-user' }
  | { type: 'edit-user'; userId: string }
  | { type: 'dept-manager' }
  | { type: 'bulk-import' }
  | { type: 'bulk-action' };

export type ToolUnitRightView =
  | { type: 'empty' }
  | { type: 'create-unit' }
  | { type: 'edit-unit'; unitName: string };

// 顶层互斥 UI 模式。这是「同一时刻最多一个接管面板」这个不变量的**唯一真相源**:
// 一个变量不可能同时取两个值,所以「两个面板同时开」按构造不可表示 —— 不再靠每个
// setter 手动 spread `其他: false` 来维持(那是旧设计的反复漏点,如选对话不退工具管理)。
//   - none               : 普通聊天(右面板由 artifactPanelVisible 这个独立轴控制)
//   - conversationBrowser : 中间面板接管(不动右面板)
//   - userManagement      : master-detail(中列表 + 右详情)
//   - toolUnit            : master-detail(中列表 + 右详情)
//   - observability       : 全屏接管(连右面板一起隐藏)
export type ActiveMode =
  | 'none'
  | 'conversationBrowser'
  | 'userManagement'
  | 'toolUnit'
  | 'observability';

// 哪些 mode 接管/影响**右面板**(master-detail 重定向 or 全屏隐藏)。
// conversationBrowser 只接管中间面板 → 进出它不算右面板意图变更,不 bump epoch。
const RIGHT_PANEL_MODES: ReadonlySet<ActiveMode> = new Set([
  'userManagement',
  'toolUnit',
  'observability',
]);

interface UIState {
  sidebarCollapsed: boolean;
  artifactPanelVisible: boolean;
  // Monotonic counter bumped on every write that affects what occupies the
  // right panel: artifact toggle / explicit set, and any activeMode change
  // into/out of a RIGHT_PANEL_MODES member (master-detail re-target or
  // observability full-screen hide). Lets deferred callers (e.g. useChat's
  // auto-open-on-switch) snapshot the value before an await and detect ANY
  // user-driven right-panel intent change in between — a plain boolean
  // snapshot of `artifactPanelVisible` cannot distinguish "untouched" from
  // "toggled and toggled back", and ignores the admin modes that also
  // re-target the right panel.
  rightPanelIntentEpoch: number;
  // 顶层互斥模式(见 ActiveMode)。取代旧的 4 个 *Visible 布尔。
  activeMode: ActiveMode;
  userManagementRightView: UserMgmtRightView;
  // 列表刷新版本号 — 右面板表单（创建/编辑/删除）成功后 bump，
  // UserManagementPanel 订阅版本号触发 refetch，避免 prop 钻透
  userMgmtListVersion: number;
  // 工具 unit 管理（B-4）— 与 user-mgmt 同构的 master-detail：中间面板列表 +
  // 右面板详情/创建。listVersion 由挂载/凭证/CRUD 成功后 bump 触发列表刷新。
  toolUnitRightView: ToolUnitRightView;
  toolUnitListVersion: number;
  // PR5a: 中间面板的选择模式 + 选中集；与 RightView 协调（进入选择模式
  // 自动切到 'bulk-action'，退出回 'empty'）
  selectionMode: boolean;
  userManagementSelection: string[];
  observabilitySelectedConvId: string | null;
  observabilityBrowseVisible: boolean;
  observabilityRefreshTick: number;
  theme: 'light' | 'dark';

  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleArtifactPanel: () => void;
  setArtifactPanelVisible: (visible: boolean) => void;
  // 进入某个顶层模式(排他);回普通聊天用 setActiveMode('none')。
  setActiveMode: (mode: ActiveMode) => void;
  setUserManagementRightView: (view: UserMgmtRightView) => void;
  bumpUserMgmtListVersion: () => void;
  setToolUnitRightView: (view: ToolUnitRightView) => void;
  bumpToolUnitListVersion: () => void;
  enterSelectionMode: () => void;
  exitSelectionMode: () => void;
  toggleUserSelection: (userId: string) => void;
  setUserManagementSelection: (ids: string[]) => void;
  clearUserSelection: () => void;
  setObservabilitySelectedConvId: (id: string | null) => void;
  setObservabilityBrowseVisible: (visible: boolean) => void;
  triggerObservabilityRefresh: () => void;
  setTheme: (theme: 'light' | 'dark') => void;
  toggleTheme: () => void;
}

// 初始**数据**态(不含 actions)。单独导出 → 测试可 setState(INITIAL_UI_STATE) 整体复位,
// 不再手抄字段清单(漏抄会让状态泄漏到下个用例 —— reviewer #7)。新增字段只改这一处。
type UIData = Omit<UIState,
  | 'toggleSidebar' | 'setSidebarCollapsed' | 'toggleArtifactPanel' | 'setArtifactPanelVisible'
  | 'setActiveMode' | 'setUserManagementRightView' | 'bumpUserMgmtListVersion'
  | 'setToolUnitRightView' | 'bumpToolUnitListVersion' | 'enterSelectionMode' | 'exitSelectionMode'
  | 'toggleUserSelection' | 'setUserManagementSelection' | 'clearUserSelection'
  | 'setObservabilitySelectedConvId' | 'setObservabilityBrowseVisible' | 'triggerObservabilityRefresh'
  | 'setTheme' | 'toggleTheme'
>;

export const INITIAL_UI_STATE: UIData = {
  sidebarCollapsed: false,
  artifactPanelVisible: false,
  rightPanelIntentEpoch: 0,
  activeMode: 'none',
  userManagementRightView: { type: 'empty' },
  userMgmtListVersion: 0,
  toolUnitRightView: { type: 'empty' },
  toolUnitListVersion: 0,
  selectionMode: false,
  userManagementSelection: [],
  observabilitySelectedConvId: null,
  observabilityBrowseVisible: false,
  observabilityRefreshTick: 0,
  theme: 'dark',
};

export const useUIStore = create<UIState>((set) => ({
  ...INITIAL_UI_STATE,

  toggleSidebar: () =>
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

  toggleArtifactPanel: () =>
    set((s) => ({
      artifactPanelVisible: !s.artifactPanelVisible,
      rightPanelIntentEpoch: s.rightPanelIntentEpoch + 1,
    })),
  setArtifactPanelVisible: (visible) =>
    set((s) => ({
      artifactPanelVisible: visible,
      rightPanelIntentEpoch: s.rightPanelIntentEpoch + 1,
    })),
  setActiveMode: (mode) => set((s) => {
    if (s.activeMode === mode) return {}; // 重复进同一 mode = no-op,不清子状态/不 bump
    const affectsRight = RIGHT_PANEL_MODES.has(s.activeMode) || RIGHT_PANEL_MODES.has(mode);
    return {
      activeMode: mode,
      // 切 mode = 进入全新模式 → 清掉所有 per-mode 瞬时子状态。子状态本就互斥
      // (不可能同时在两个模式),进新模式一律从干净态开始;省去每个模式各自的复位逻辑。
      userManagementRightView: { type: 'empty' },
      selectionMode: false,
      userManagementSelection: [],
      toolUnitRightView: { type: 'empty' },
      observabilitySelectedConvId: null,
      observabilityBrowseVisible: false,
      // observability 全屏接管 → 关掉默认 artifact 面板(沿用旧 setObservabilityVisible 行为)
      ...(mode === 'observability' && { artifactPanelVisible: false }),
      // 仅当进/出影响右面板的模式时才 bump(conversationBrowser 只动中间面板,不算)
      ...(affectsRight && { rightPanelIntentEpoch: s.rightPanelIntentEpoch + 1 }),
    };
  }),
  setUserManagementRightView: (view) => set({ userManagementRightView: view }),
  bumpUserMgmtListVersion: () =>
    set((s) => ({ userMgmtListVersion: s.userMgmtListVersion + 1 })),
  setToolUnitRightView: (view) => set({ toolUnitRightView: view }),
  bumpToolUnitListVersion: () =>
    set((s) => ({ toolUnitListVersion: s.toolUnitListVersion + 1 })),
  enterSelectionMode: () => set({
    selectionMode: true,
    userManagementSelection: [],
    userManagementRightView: { type: 'bulk-action' },
  }),
  exitSelectionMode: () => set({
    selectionMode: false,
    userManagementSelection: [],
    userManagementRightView: { type: 'empty' },
  }),
  toggleUserSelection: (userId) => set((s) => {
    const has = s.userManagementSelection.includes(userId);
    return {
      userManagementSelection: has
        ? s.userManagementSelection.filter((id) => id !== userId)
        : [...s.userManagementSelection, userId],
    };
  }),
  setUserManagementSelection: (ids) => set({ userManagementSelection: ids }),
  clearUserSelection: () => set({ userManagementSelection: [] }),
  setObservabilitySelectedConvId: (id) => set({
    observabilitySelectedConvId: id,
    observabilityBrowseVisible: false,
  }),
  setObservabilityBrowseVisible: (visible) => set({
    observabilityBrowseVisible: visible,
  }),
  triggerObservabilityRefresh: () => set((s) => ({
    observabilityRefreshTick: s.observabilityRefreshTick + 1,
  })),

  setTheme: (theme) => {
    applyTheme(theme);
    set({ theme });
  },
  toggleTheme: () =>
    set((s) => {
      const next = s.theme === 'light' ? 'dark' : 'light';
      localStorage.setItem('theme', next);
      applyTheme(next);
      return { theme: next };
    }),
}));
