'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getSkills,
  setSkillEnabled,
  importSkill,
  downloadSkillBundle,
  deleteSkill,
  adminDeleteSkill,
  adminListSkills,
  adminUpdateSkill,
  ApiError,
} from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { useConfigStore } from '@/stores/configStore';
import { useUIStore } from '@/stores/uiStore';
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from '@/lib/styles';
import { triggerBlobDownload } from '@/lib/download';
import { PillBadge } from '@/components/ui/PillBadge';
import { SwitchTrack } from '@/components/ui/SwitchTrack';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import { StatusNotice } from '@/components/ui/StatusNotice';
import DangerConfirmModal, { DangerConfirmTarget } from '@/components/layout/DangerConfirmModal';
import PanelSearchBar from './PanelSearchBar';
import { resolvePrivateSkillAllowance } from '@/lib/privateSkillLimit';
import type {
  AdminSkillItem,
  AdminSkillUpdateRequest,
  SkillItem,
  SkillFindingItem,
  SkillImportResponse,
} from '@/types';

type SharedVisibility = 'public' | 'department';
type SkillRow = SkillItem & {
  adminShared?: AdminSkillItem;
  adminOnly?: boolean;
};
type SkillImportNoticeData = SkillImportResponse;

const VISIBILITY_OPTIONS = [
  { value: 'public', label: '公开' },
  { value: 'department', label: '部门' },
] as const;

export function mergeSkillRows(
  visibleSkills: SkillItem[],
  adminSharedSkills: AdminSkillItem[] = [],
): SkillRow[] {
  const adminById = new Map(adminSharedSkills.map((s) => [s.id, s]));
  const visibleIds = new Set(visibleSkills.map((s) => s.id));
  const rows: SkillRow[] = visibleSkills.map((skill) => ({
    ...skill,
    adminShared: adminById.get(skill.id),
  }));

  for (const adminSkill of adminSharedSkills) {
    if (visibleIds.has(adminSkill.id)) continue;
    rows.push({
      id: adminSkill.id,
      slug: adminSkill.slug,
      name: adminSkill.name,
      description: adminSkill.description,
      enabled: adminSkill.default_enabled,
      default_enabled: adminSkill.default_enabled,
      is_overridden: false,
      source: adminSkill.source,
      has_extra_files: adminSkill.has_extra_files,
      visibility: adminSkill.visibility,
      is_owner: false,
      shadowed_by_private: false,
      adminShared: adminSkill,
      adminOnly: true,
    });
  }

  return rows;
}

export function skillRowBorderClass(skill: SkillItem): string {
  if (skill.shadowed_by_private) return 'border-status-error';
  return skill.enabled
    ? 'border-accent/60'
    : 'border-border dark:border-border-dark';
}

// 用户侧技能管理(C-3 列举/toggle + E-2 导入/导出/删除)。中间面板接管(同
// ConversationBrowser),点击条目时按需在右栏预览正文。个人开关写 user_skill 覆盖,控 `enabled`
// (进不进模型 L1 索引 + 对话内激活选择器),不碰 `visible`(系统定)。
// 导入:user 私有(仅自己可见、立即启用)/ admin 可选共享(public/department、默认开关);
// 硬门拒收 → 422 结构化 findings 逐条渲染。seeded skill 归 config 只读。
export default function SkillManagementPanel() {
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [importNotice, setImportNotice] = useState<SkillImportNoticeData | null>(null);
  // 正在写覆盖/删除的 skill id 集(同名行可独立操作)。
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<SkillRow | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const maxPrivateSkills = useConfigStore((s) => s.maxPrivateSkills);
  const fetchConfig = useConfigStore((s) => s.fetchConfig);
  const setActiveMode = useUIStore((s) => s.setActiveMode);
  const skillRightView = useUIStore((s) => s.skillRightView);
  const setSkillRightView = useUIStore((s) => s.setSkillRightView);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const fetchSkills = useCallback(async () => {
    try {
      const [visibleData, adminData] = await Promise.all([
        getSkills(),
        isAdmin ? adminListSkills() : Promise.resolve(null),
      ]);
      setSkills(mergeSkillRows(visibleData.skills, adminData?.skills ?? []));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载技能失败');
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleToggle = useCallback(async (skillId: string, next: boolean) => {
    // 乐观更新 + 失败回滚。pending 期禁开关避免连点。
    setPending((p) => new Set(p).add(skillId));
    setSkills((list) =>
      list.map((s) => (s.id === skillId ? { ...s, enabled: next } : s)),
    );
    try {
      const updated = await setSkillEnabled(skillId, next);
      setSkills((list) =>
        list.map((s) => (s.id === skillId ? { ...s, ...updated } : s)),
      );
    } catch (err) {
      // 回滚
      setSkills((list) =>
        list.map((s) => (s.id === skillId ? { ...s, enabled: !next } : s)),
      );
      console.error(`Failed to toggle skill ${skillId}:`, err);
    } finally {
      setPending((p) => {
        const n = new Set(p);
        n.delete(skillId);
        return n;
      });
    }
  }, []);

  const handleExport = useCallback(async (skill: SkillRow) => {
    setRowError(null);
    try {
      const blob = await downloadSkillBundle(skill.id, {
        admin: Boolean(skill.adminShared),
      });
      triggerBlobDownload(`${skill.slug}.zip`, blob);
    } catch (err) {
      setRowError(err instanceof Error ? err.message : '导出失败');
    }
  }, []);

  const handleOpen = useCallback((skill: SkillRow) => {
    setSkillRightView({
      type: 'detail',
      skillId: skill.id,
      // Shared catalog rows use the admin channel so department-only management
      // items remain previewable even when the admin has no matching department.
      admin: Boolean(skill.adminShared),
    });
    setArtifactPanelVisible(true);
  }, [setArtifactPanelVisible, setSkillRightView]);

  const handleAdminUpdate = useCallback(
    async (skill: SkillRow, patch: AdminSkillUpdateRequest) => {
      if (!skill.adminShared?.can_edit) return;
      setRowError(null);
      setPending((p) => new Set(p).add(skill.id));
      try {
        await adminUpdateSkill(skill.id, patch);
        await fetchSkills();
        const preview = useUIStore.getState().skillRightView;
        if (preview.type === 'detail' && preview.skillId === skill.id) {
          // Re-read the detail metadata so its visibility badge cannot lag the
          // just-completed admin update. The body itself remains on-demand.
          setSkillRightView({ ...preview });
        }
      } catch (err) {
        setRowError(err instanceof Error ? err.message : '更新共享技能失败');
      } finally {
        setPending((p) => {
          const n = new Set(p);
          n.delete(skill.id);
          return n;
        });
      }
    },
    [fetchSkills, setSkillRightView],
  );

  const handleConfirmDelete = useCallback(
    async () => {
      const skill = deleteTarget;
      if (!skill) return;
      setRowError(null);
      setPending((p) => new Set(p).add(skill.id));
      try {
        // 非本人的 dynamic skill 只有 admin 通道能删(user 通道 403)
        if (skill.is_owner) {
          await deleteSkill(skill.id);
        } else {
          await adminDeleteSkill(skill.id);
        }
        // 删除私人赢家后，共享同名项需要立即解除“被覆盖”状态；重新解析服务端
        // effective set，避免只删本地行后留下红框和禁用开关。
        await fetchSkills();
        const preview = useUIStore.getState().skillRightView;
        if (preview.type === 'detail' && preview.skillId === skill.id) {
          setSkillRightView({ type: 'empty' });
          setArtifactPanelVisible(false);
        }
        setDeleteTarget(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : '删除失败';
        setRowError(message);
        if (err instanceof Error) throw err;
        throw new Error(message);
      } finally {
        setPending((p) => {
          const n = new Set(p);
          n.delete(skill.id);
          return n;
        });
      }
    },
    [
      deleteTarget,
      fetchSkills,
      setArtifactPanelVisible,
      setSkillRightView,
    ],
  );

  const q = query.trim().toLowerCase();
  const filtered = q
    ? skills.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.slug.toLowerCase().includes(q) ||
          (s.description ?? '').toLowerCase().includes(q),
      )
    : skills;
  const privateSkillCount = skills.filter((skill) => skill.is_owner).length;
  const privateAllowance = resolvePrivateSkillAllowance(
    privateSkillCount,
    maxPrivateSkills,
  );
  const privateAllowanceText = privateAllowance.kind === 'disabled'
    ? '个人技能导入已关闭'
    : privateAllowance.kind === 'unlimited'
      ? `个人技能容量 ${privateAllowance.used}/不限`
      : privateAllowance.kind === 'limited'
        ? privateAllowance.canImport
          ? `个人技能容量 ${privateAllowance.used}/${privateAllowance.limit}，剩余 ${privateAllowance.remaining} 个`
          : `个人技能容量 ${privateAllowance.used}/${privateAllowance.limit}，额度已用完`
        : `个人技能 ${privateAllowance.used}`;
  const personalEntryDisabled = !isAdmin && !privateAllowance.canImport;

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={setQuery}
        placeholder="搜索技能名 / 描述…"
        countLabel={`${skills.length} 技能`}
        onClose={() => setActiveMode('none')}
      />

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        <div className="max-w-3xl mx-auto space-y-2">
          <p className="px-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
            关闭的技能不会自动进入对话，也不会出现在输入框的激活选择器里；随时可以重新开启。
            {!loading && !error && (
              <span className="inline-block whitespace-nowrap text-accent">
                {privateAllowanceText}
              </span>
            )}
          </p>
          {/* 导入入口 + 内联导入卡片 */}
          <button
            type="button"
            disabled={personalEntryDisabled}
            onClick={() => {
              setImportNotice(null);
              setImportOpen((v) => !v);
            }}
            className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              importOpen
                ? 'text-accent border-accent bg-bg dark:bg-bg-dark'
                : 'text-accent border-border dark:border-border-dark bg-surface dark:bg-surface-dark hover:bg-bg dark:hover:bg-bg-dark'
            }`}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M7 2v10M2 7h10" />
            </svg>
            导入技能
          </button>

          {importOpen && (
            <SkillImportCard
              isAdmin={isAdmin}
              personalImportAvailable={privateAllowance.canImport}
              onImported={(data) => {
                setImportNotice(data);
                setImportOpen(false);
                fetchSkills();
              }}
              onClose={() => setImportOpen(false)}
            />
          )}

          {importNotice && (
            <SkillImportNotice
              data={importNotice}
              onDismiss={() => setImportNotice(null)}
            />
          )}

          {rowError && (
            <StatusNotice tone="error" onDismiss={() => setRowError(null)}>
              {rowError}
            </StatusNotice>
          )}

          {loading && (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              加载技能中...
            </div>
          )}

          {!loading && error && (
            <div className="py-12 text-center text-sm text-status-error">{error}</div>
          )}

          {!loading && !error && filtered.length === 0 && (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              {query ? '没有找到匹配的技能' : '暂无可用技能。'}
            </div>
          )}

          {!loading && !error && filtered.map((skill) => {
            const busy = pending.has(skill.id);
            const deletable =
              skill.source === 'dynamic' && (skill.is_owner || isAdmin);
            const adminShared = skill.adminShared;
            const canAdminEdit = Boolean(adminShared?.can_edit);
            const canUsePersonalToggle = !skill.adminOnly;
            const exportable = !skill.adminOnly || Boolean(adminShared);
            const selected =
              skillRightView.type === 'detail'
              && skillRightView.skillId === skill.id;
            return (
              <div
                key={skill.id}
                className={`flex items-start gap-3 px-4 py-3 rounded-xl bg-surface dark:bg-surface-dark border transition-colors ${skillRowBorderClass(skill)} ${selected ? 'ring-1 ring-accent' : ''}`}
              >
                <div className="min-w-0 flex-1">
                  <button
                    type="button"
                    onClick={() => handleOpen(skill)}
                    aria-pressed={selected}
                    title="点击查看 SKILL.md 说明"
                    className="block w-full rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  >
                    <div className="flex min-h-6 items-center gap-2">
                      <span className="text-sm font-medium text-text-primary dark:text-text-primary-dark truncate">
                        {skill.name}
                      </span>
                      {skill.adminOnly && (
                        <PillBadge
                          tone="warning"
                          title="当前账号不可见，但管理员仍可在共享目录中管理它"
                        >
                          管理项
                        </PillBadge>
                      )}
                      {skill.shadowed_by_private && (
                        <PillBadge
                          tone="error"
                          title="当前运行时会优先使用你的同名私人技能"
                        >
                          已被私人技能覆盖
                        </PillBadge>
                      )}
                      {skill.source === 'dynamic' && (
                        <PillBadge
                          tone="accent"
                          title={
                            skill.visibility === 'private'
                              ? '你导入的私有技能，仅自己可见'
                              : '通过界面导入的共享技能'
                          }
                        >
                          {skill.visibility === 'private' ? '私有导入' : '导入'}
                        </PillBadge>
                      )}
                      {skill.visibility !== 'private' && (
                        <PillBadge
                          tone={skill.visibility === 'department' ? 'warning' : 'neutral'}
                          title={
                            skill.visibility === 'department'
                              ? '部门可见：默认不可用，需要部门授权'
                              : '公开可见：默认全员可用，可被部门排除'
                          }
                        >
                          {skill.visibility === 'department' ? '部门' : '公开'}
                        </PillBadge>
                      )}
                    </div>
                    {skill.description && (
                      <p
                        className="mt-0.5 text-xs text-text-secondary dark:text-text-secondary-dark line-clamp-2"
                      >
                        {skill.description}
                      </p>
                    )}
                    {skill.shadowed_by_private && (
                      <p className="mt-1 text-xs text-status-error">
                        你有一个同名私人技能；对话中使用该 slug 时，共享技能不会生效。
                      </p>
                    )}
                  </button>
                  {adminShared && (
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <SegmentedTabs<SharedVisibility>
                        value={adminShared.visibility}
                        options={VISIBILITY_OPTIONS.map((option) => ({
                          ...option,
                          disabled: busy || !canAdminEdit,
                          title: canAdminEdit
                            ? undefined
                            : 'seeded skill 由 config 管理，不能在界面编辑',
                        }))}
                        onChange={(visibility) =>
                          handleAdminUpdate(skill, { visibility })
                        }
                        ariaLabel={`设置共享技能 ${skill.name} 的可见度`}
                        className="shrink-0"
                      />
                      <button
                        type="button"
                        role="switch"
                        aria-checked={adminShared.default_enabled}
                        aria-label={`${adminShared.default_enabled ? '关闭' : '开启'}共享技能 ${skill.name} 的默认启用`}
                        disabled={busy || !canAdminEdit}
                        onClick={() =>
                          handleAdminUpdate(skill, {
                            default_enabled: !adminShared.default_enabled,
                          })
                        }
                        title={
                          canAdminEdit
                            ? '控制该共享技能是否默认进入用户的可激活技能列表'
                            : 'seeded skill 由 config 管理，不能在界面编辑'
                        }
                        className="inline-flex items-center gap-2 rounded-lg bg-panel-accent dark:bg-surface-dark px-2 py-1 text-xs text-text-secondary dark:text-text-secondary-dark transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <span>{adminShared.default_enabled ? '默认开' : '默认关'}</span>
                        <SwitchTrack checked={adminShared.default_enabled} />
                      </button>
                    </div>
                  )}
                </div>

                <div className="flex h-6 flex-shrink-0 items-center gap-3">
                  {/* Row actions */}
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleOpen(skill)}
                      className="flex h-6 w-6 items-center justify-center rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-accent hover:bg-bg dark:hover:bg-bg-dark transition-colors"
                      aria-label={`查看技能 ${skill.name} 的说明`}
                      title="查看 SKILL.md 说明"
                    >
                      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 2.5h7.5a1 1 0 0 1 1 1V6M3 2.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1V10" />
                        <path d="M5 6h4M5 9h2M10 8l4-4M11 4h3v3" />
                      </svg>
                    </button>
                    {exportable && (
                      <button
                        onClick={() => handleExport(skill)}
                        disabled={busy}
                        className="flex h-6 w-6 items-center justify-center rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors disabled:opacity-40"
                        aria-label={`导出技能 ${skill.name}`}
                        title={skill.has_extra_files ? '导出技能包' : '导出单文件技能'}
                      >
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M8 2v8M4.5 6.5L8 10l3.5-3.5M2.5 13h11" />
                        </svg>
                      </button>
                    )}
                    {deletable && (
                      <button
                        onClick={() => setDeleteTarget(skill)}
                        disabled={busy}
                        className="flex h-6 w-6 items-center justify-center rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error hover:bg-status-error/10 transition-colors disabled:opacity-40"
                        aria-label={`删除技能 ${skill.name}`}
                        title="删除该技能"
                      >
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                          <path d="M2.5 4h11M6.5 4V2.5h3V4M4 4l.8 9.5h6.4L12 4M6.5 7v4M9.5 7v4" />
                        </svg>
                      </button>
                    )}
                  </div>

                  {/* Enable switch */}
                  {canUsePersonalToggle && !skill.shadowed_by_private ? (
                    <button
                      type="button"
                      role="switch"
                      aria-checked={skill.enabled}
                      aria-label={`${skill.enabled ? '关闭' : '开启'}技能 ${skill.name}`}
                      disabled={busy}
                      onClick={() => handleToggle(skill.id, !skill.enabled)}
                      className="inline-flex h-6 items-center disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <SwitchTrack checked={skill.enabled} />
                    </button>
                  ) : skill.shadowed_by_private ? (
                    <span
                      className="inline-flex h-6 items-center text-[11px] text-status-error"
                      title="删除或重命名同名私人技能后，这个共享技能会恢复"
                    >
                      已覆盖
                    </span>
                  ) : (
                    <span
                      className="inline-flex h-6 items-center text-[11px] text-text-tertiary dark:text-text-tertiary-dark"
                      title="当前账号不在该 skill 的可见范围内"
                    >
                      不可见
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {deleteTarget && (
        <DangerConfirmModal
          title="删除技能"
          message="将删除该动态技能，并清理关联的个人启用状态与部门规则。\n操作不可恢复。"
          confirmLabel="确认删除"
          onCancel={() => setDeleteTarget(null)}
          onConfirm={handleConfirmDelete}
        >
          <DangerConfirmTarget
            name={deleteTarget.name}
            description={deleteTarget.description}
          />
        </DangerConfirmModal>
      )}
    </div>
  );
}

// ------------------------------------------------------------
// 内联导入卡片(仿 BulkImportForm 的 Stage 流,压缩进中间面板)
// ------------------------------------------------------------

type ImportStage =
  | { kind: 'pick' }
  | { kind: 'submitting' };

function SkillImportCard({
  isAdmin,
  personalImportAvailable,
  onImported,
  onClose,
}: {
  isAdmin: boolean;
  personalImportAvailable: boolean;
  onImported: (data: SkillImportResponse) => void;
  onClose: () => void;
}) {
  const [stage, setStage] = useState<ImportStage>({ kind: 'pick' });
  const [file, setFile] = useState<File | null>(null);
  const [marketplace, setMarketplace] = useState(
    isAdmin && !personalImportAvailable,
  );
  const [sharedVisibility, setSharedVisibility] = useState<SharedVisibility>('public');
  const [sharedDefaultEnabled, setSharedDefaultEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** 硬门拒收的结构化 findings(422 detail),按 severity 渲染 */
  const [rejectFindings, setRejectFindings] = useState<SkillFindingItem[] | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // 清掉 <input type="file"> 的 DOM 值 —— 否则重新选同名文件不触发 onChange
  const clearNativeInput = useCallback(() => {
    if (inputRef.current) inputRef.current.value = '';
  }, []);

  useEffect(() => {
    if (isAdmin && !personalImportAvailable) setMarketplace(true);
  }, [isAdmin, personalImportAvailable]);

  const handleFile = useCallback((f: File | null) => {
    setError(null);
    setRejectFindings(null);
    if (!f) {
      setFile(null);
      clearNativeInput();
      return;
    }
    // Soft client-side hint — backend is authoritative
    if (!/\.zip$/i.test(f.name)) {
      setError('请选择 .zip 文件（技能目录打包，内含 SKILL.md）');
      return;
    }
    setFile(f);
  }, [clearNativeInput]);

  const submit = useCallback(async () => {
    if (!file || (!marketplace && !personalImportAvailable)) return;
    setStage({ kind: 'submitting' });
    setError(null);
    setRejectFindings(null);
    try {
      const data = await importSkill(file, {
        marketplace,
        ...(marketplace
          ? {
              visibility: sharedVisibility,
              defaultEnabled: sharedDefaultEnabled,
            }
          : {}),
      });
      onImported(data);
    } catch (err) {
      // 错误终态:清 file + native input,强制重挑文件(修包重传流程可预测)
      setStage({ kind: 'pick' });
      setFile(null);
      clearNativeInput();
      if (err instanceof ApiError && err.body && typeof err.body === 'object') {
        const detail = (err.body as { detail?: unknown }).detail;
        if (
          detail &&
          typeof detail === 'object' &&
          Array.isArray((detail as { findings?: unknown }).findings)
        ) {
          setRejectFindings(
            (detail as { findings: SkillFindingItem[] }).findings,
          );
          return;
        }
      }
      setError(err instanceof Error ? err.message : '导入失败');
    }
  }, [
    file,
    marketplace,
    sharedDefaultEnabled,
    sharedVisibility,
    personalImportAvailable,
    onImported,
    clearNativeInput,
  ]);

  return (
    <div className="rounded-xl bg-surface dark:bg-surface-dark border border-border dark:border-border-dark p-4 space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          handleFile(e.dataTransfer.files?.[0] ?? null);
        }}
        className={`rounded-xl border-2 border-dashed p-5 text-center transition-colors ${
          dragActive
            ? 'border-accent bg-panel/50 dark:bg-panel-accent-dark/50'
            : 'border-border dark:border-border-dark'
        }`}
      >
        {file ? (
          <div className="flex flex-col items-center gap-1">
            <div className="text-sm text-text-primary dark:text-text-primary-dark font-medium">
              {file.name}
            </div>
            <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
              {(file.size / 1024).toFixed(1)} KB
            </div>
            <button
              onClick={() => handleFile(null)}
              type="button"
              className="mt-1 text-xs text-accent hover:underline"
            >
              换一个文件
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-1.5">
            <div className="text-sm text-text-secondary dark:text-text-secondary-dark">
              拖拽技能 zip 到此处
            </div>
            <button
              onClick={() => inputRef.current?.click()}
              type="button"
              className="px-4 py-1.5 rounded-lg border border-border dark:border-border-dark text-sm font-medium text-text-secondary dark:text-text-secondary-dark bg-surface dark:bg-surface-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              选择文件
            </button>
          </div>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".zip,application/zip"
          onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
          className="hidden"
        />
      </div>

      {/* Keep this format card aligned with BulkImportForm's upload guidance. */}
      <div className="rounded-lg bg-panel/40 dark:bg-panel-accent-dark/40 p-4 text-xs space-y-2">
        <div className="font-medium text-text-secondary dark:text-text-secondary-dark">
          技能 ZIP 格式说明
        </div>
        <ul className="text-text-tertiary dark:text-text-tertiary-dark space-y-1 list-disc pl-4">
          <li>
            <span className="font-mono">SKILL.md</span>{' '}
            <span className="text-status-error">*</span>（必需，ZIP 内只能有一个）
          </li>
          <li>
            推荐目录：<span className="font-mono">my-skill/SKILL.md</span>
            （直接放在 ZIP 根目录也兼容）
          </li>
          <li>
            使用 UTF-8；在文件开头两行{' '}
            <span className="font-mono">---</span> 之间的 YAML 配置区填写{' '}
            <span className="font-mono">name</span>、{' '}
            <span className="font-mono">description</span>，Markdown 正文不能为空
          </li>
          <li>
            可选 <span className="font-mono">scripts/</span>、{' '}
            <span className="font-mono">references/</span>、{' '}
            <span className="font-mono">assets/</span>，与 SKILL.md 放在同一技能目录内
          </li>
        </ul>
      </div>

      {isAdmin && (
        <div className="space-y-2">
          <button
            type="button"
            role="switch"
            aria-checked={marketplace}
            aria-label="导入为共享技能"
            disabled={!personalImportAvailable}
            onClick={() => setMarketplace((v) => !v)}
            className={`flex w-full items-center justify-between gap-3 rounded-lg border bg-chat dark:bg-chat-dark px-3 py-2 text-left transition-colors disabled:cursor-not-allowed ${
              marketplace
                ? 'border-accent/60'
                : 'border-border dark:border-border-dark hover:border-accent/40 dark:hover:border-accent/50'
            }`}
          >
            <span className="min-w-0">
              <span className="block text-sm font-medium text-text-primary dark:text-text-primary-dark">
                导入为共享技能
              </span>
              <span className="block text-xs text-text-tertiary dark:text-text-tertiary-dark">
                {personalImportAvailable
                  ? '管理员可配置公开或部门可见'
                  : '个人导入不可用，本次将发布为共享技能'}
              </span>
            </span>
            <SwitchTrack checked={marketplace} />
          </button>
          {marketplace && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border dark:border-border-dark bg-chat dark:bg-chat-dark px-3 py-2">
              <SegmentedTabs<SharedVisibility>
                value={sharedVisibility}
                options={VISIBILITY_OPTIONS}
                onChange={setSharedVisibility}
                ariaLabel="导入共享技能可见度"
              />
              <button
                type="button"
                role="switch"
                aria-checked={sharedDefaultEnabled}
                aria-label="导入后默认启用"
                onClick={() => setSharedDefaultEnabled((v) => !v)}
                className="inline-flex items-center gap-2 rounded-lg bg-panel-accent dark:bg-surface-dark px-2 py-1 text-xs text-text-secondary dark:text-text-secondary-dark"
              >
                <span>{sharedDefaultEnabled ? '默认开' : '默认关'}</span>
                <SwitchTrack checked={sharedDefaultEnabled} />
              </button>
            </div>
          )}
        </div>
      )}

      {rejectFindings ? (
        <SkillValidationNotices findings={rejectFindings} />
      ) : error ? (
        <StatusNotice tone="error" title="导入失败">
          <div className="break-words">{error}</div>
        </StatusNotice>
      ) : null}

      <div className="flex justify-end gap-2">
        <button
          onClick={onClose}
          type="button"
          disabled={stage.kind === 'submitting'}
          className={`${BUTTON_SECONDARY} rounded-lg px-4 py-1.5 text-sm`}
        >
          取消
        </button>
        <button
          onClick={submit}
          disabled={
            !file ||
            stage.kind === 'submitting' ||
            (!marketplace && !personalImportAvailable)
          }
          type="button"
          className={`${BUTTON_PRIMARY} rounded-lg px-4 py-1.5 text-sm`}
        >
          {stage.kind === 'submitting' ? '导入中…' : '导入'}
        </button>
      </div>
    </div>
  );
}

export function SkillValidationNotices({
  findings,
}: {
  findings: SkillFindingItem[];
}) {
  const errors = findings.filter((finding) => finding.severity === 'error');
  const warnings = findings.filter((finding) => finding.severity === 'warning');

  return (
    <>
      {errors.length > 0 && (
        <StatusNotice tone="error" title="技能包还不能导入">
          <div className="space-y-3">
            <div>
              发现 {errors.length} 个必须修改的问题。修好后，请重新打包为 ZIP 并上传。
            </div>
            <SkillFindingList findings={errors} />
            <div className="border-t border-status-error/20 pt-3 text-xs leading-5">
              <span className="font-medium text-text-primary dark:text-text-primary-dark">
                不确定怎么修改？
              </span>{' '}
              回到对话，把刚才的 ZIP 作为附件发给 Agent，并说明：
              <span className="mt-1 block rounded-md bg-surface/70 px-2 py-1.5 text-text-secondary dark:bg-surface-dark/70 dark:text-text-secondary-dark">
                请检查这个 Skill 为什么无法导入 ArtifactFlow，修复问题后重新打包。
              </span>
            </div>
          </div>
        </StatusNotice>
      )}
      {warnings.length > 0 && (
        <StatusNotice tone="warning" title="其他校验提示">
          <div className="space-y-3">
            <div>以下问题不会阻止导入，但建议一并检查。</div>
            <SkillFindingList findings={warnings} />
          </div>
        </StatusNotice>
      )}
    </>
  );
}

type SkillFindingCopy = {
  title: string;
  guidance: string;
};

const SKILL_FINDING_COPY: Record<string, SkillFindingCopy> = {
  'zip.invalid': {
    title: 'ZIP 文件无法读取',
    guidance: '请确认文件没有损坏，并使用标准 ZIP 格式重新压缩。',
  },
  'zip.too_many_members': {
    title: 'ZIP 内文件太多',
    guidance: '删除不需要的文件后重新打包。',
  },
  'zip.uncompressed_too_large': {
    title: '解压后的内容太大',
    guidance: '删除不必要的大文件，或缩小 assets、references 等目录中的文件。',
  },
  'zip.path_traversal': {
    title: 'ZIP 中包含不安全的文件路径',
    guidance: '移除绝对路径、包含“..”的路径或符号链接后重新打包。',
  },
  'zip.skill_md_count': {
    title: '没有找到唯一的 SKILL.md',
    guidance: '技能包中必须且只能有一个 SKILL.md，例如 my-skill/SKILL.md。',
  },
  'zip.stray_files': {
    title: '部分文件放在技能目录之外',
    guidance: '请把 SKILL.md 和其他文件都放进同一个顶层目录，再压缩这个目录。',
  },
  'zip.orphan_files': {
    title: '有些文件可能没有被使用',
    guidance: '如果这些文件不是由脚本读取的，请删除它们；否则可以忽略这条提示。',
  },
  'zip.bundle_too_large': {
    title: '技能包文件太大',
    guidance: '删除不必要的文件或压缩大文件，使 ZIP 小于系统限制。',
  },
  'md.member_too_large': {
    title: 'SKILL.md 文件太大',
    guidance: '精简 SKILL.md，把较长的说明移到 references/ 目录。',
  },
  'md.not_utf8': {
    title: 'SKILL.md 的文本编码不正确',
    guidance: '请用编辑器将 SKILL.md 重新保存为 UTF-8 编码。',
  },
  'md.frontmatter_invalid': {
    title: 'SKILL.md 开头的配置格式有误',
    guidance: '检查两行“---”之间的 YAML，尤其是缩进、冒号和引号。',
  },
  'md.body_empty': {
    title: 'SKILL.md 缺少正文说明',
    guidance: '请在第二行“---”之后写明 Agent 应该如何使用这个技能。',
  },
  'md.unclosed_fence': {
    title: '代码块可能没有闭合',
    guidance: '检查 SKILL.md 中的 ``` 或 ~~~，确保每个代码块都有结束标记。',
  },
  'md.too_long': {
    title: 'SKILL.md 正文较长',
    guidance: '建议把详细资料移到 references/，让主说明保持简洁。',
  },
  'md.link_unresolved': {
    title: 'SKILL.md 引用的文件不存在',
    guidance: '检查相对链接是否写对，并确认对应文件已经放进 ZIP。',
  },
  'fm.name_invalid': {
    title: '技能名称格式不正确',
    guidance: '把 SKILL.md 开头配置区里的 name 设置为非空文本，例如 name: document-review。',
  },
  'fm.description_invalid': {
    title: '技能描述格式不正确',
    guidance: '把 SKILL.md 开头配置区里的 description 设置为非空文本。',
  },
  'fm.allowed_tools_invalid': {
    title: '允许使用的工具配置不正确',
    guidance: '检查 SKILL.md 开头配置区里 allowed-tools 的写法和缩进。',
  },
  'fm.compatibility_invalid': {
    title: '兼容性配置格式异常',
    guidance: '检查 compatibility 的值；不需要时可以删除这个字段。',
  },
  'fm.license_invalid': {
    title: '许可证配置格式异常',
    guidance: 'license 应填写为一段文本；不需要时可以删除这个字段。',
  },
  'fm.metadata_invalid': {
    title: '附加信息格式异常',
    guidance: 'metadata 应使用 YAML 键值结构；不需要时可以删除这个字段。',
  },
  'fm.cc_extension': {
    title: '包含 ArtifactFlow 不使用的配置',
    guidance: '这些 Claude Code 专用字段会被忽略；确认无影响后也可以保留。',
  },
  'fm.unknown_keys': {
    title: '包含无法识别的配置项',
    guidance: '这些字段会被保留但不会生效；请确认字段名是否拼写正确。',
  },
  'fm.name_dir_mismatch': {
    title: '技能名称与目录名不一致',
    guidance: '建议让顶层目录名与 SKILL.md 配置区里的 name 保持一致。',
  },
  'fm.import_ignored_keys': {
    title: '包内的可见范围设置不会生效',
    guidance: '技能的公开范围和默认开关由导入页面决定，ZIP 内对应字段会被忽略。',
  },
  'tools.unknown_entry': {
    title: '引用的工具当前不存在',
    guidance: '检查 allowed-tools 中的名称，或先让管理员安装并配置对应工具。',
  },
  'slug.invalid': {
    title: '无法生成有效的技能标识',
    guidance: '请使用以英文字母或数字开头的 name，只包含小写字母、数字、“-”或“_”。',
  },
};

function SkillFindingList({ findings }: { findings: SkillFindingItem[] }) {
  return (
    <ul className="space-y-2">
      {findings.map((finding, index) => {
        const copy = SKILL_FINDING_COPY[finding.rule];
        return (
          <li
            key={`${finding.rule}-${index}`}
            className="rounded-lg bg-surface/70 px-3 py-2 dark:bg-surface-dark/70"
          >
            <div className="font-medium text-text-primary dark:text-text-primary-dark">
              {copy?.title ?? '技能包中有一项内容需要检查'}
            </div>
            <div className="mt-0.5 text-xs leading-5">
              {copy?.guidance ?? '请展开技术详情，根据具体信息修改后重试。'}
            </div>
            <details className="mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              <summary className="cursor-pointer select-none hover:text-text-secondary dark:hover:text-text-secondary-dark">
                查看技术详情
              </summary>
              <div className="mt-1 break-words whitespace-pre-wrap font-mono text-[11px] leading-4">
                {finding.message}
              </div>
            </details>
          </li>
        );
      })}
    </ul>
  );
}

function SkillImportNotice({
  data,
  onDismiss,
}: {
  data: SkillImportNoticeData;
  onDismiss: () => void;
}) {
  const { skill, findings } = data;
  const isPrivate = skill.visibility === 'private';
  const visibilityLabel = isPrivate
    ? '私有'
    : skill.visibility === 'department'
      ? '部门'
      : '公开';

  return (
    <>
      <StatusNotice
        tone="success"
        title="已导入"
        onDismiss={onDismiss}
        dismissLabel="关闭导入成功提示"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-text-primary dark:text-text-primary-dark">
            {skill.name}
          </span>
          <PillBadge tone={isPrivate ? 'accent' : 'neutral'}>
            {visibilityLabel}
          </PillBadge>
          {isPrivate ? (
            <PillBadge tone="success">已启用</PillBadge>
          ) : (
            <PillBadge tone={skill.default_enabled ? 'success' : 'neutral'}>
              {skill.default_enabled ? '默认开' : '默认关'}
            </PillBadge>
          )}
        </div>
      </StatusNotice>
      {findings.length > 0 && (
        <StatusNotice tone="warning" title="校验提示">
          <div className="space-y-3">
            <div>技能已经导入；以下问题不会阻止使用，但建议检查。</div>
            <SkillFindingList findings={findings} />
          </div>
        </StatusNotice>
      )}
    </>
  );
}
