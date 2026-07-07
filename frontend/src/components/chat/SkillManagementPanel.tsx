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
import { useUIStore } from '@/stores/uiStore';
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from '@/lib/styles';
import { triggerBlobDownload } from '@/lib/download';
import { PillBadge } from '@/components/ui/PillBadge';
import { SwitchTrack } from '@/components/ui/SwitchTrack';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import { StatusNotice } from '@/components/ui/StatusNotice';
import DangerConfirmModal, { DangerConfirmTarget } from '@/components/layout/DangerConfirmModal';
import PanelSearchBar from './PanelSearchBar';
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

function mergeSkillRows(
  visibleSkills: SkillItem[],
  adminSharedSkills: AdminSkillItem[] = [],
): SkillRow[] {
  const adminBySlug = new Map(adminSharedSkills.map((s) => [s.slug, s]));
  const visibleSlugs = new Set(visibleSkills.map((s) => s.slug));
  const rows: SkillRow[] = visibleSkills.map((skill) => ({
    ...skill,
    adminShared: adminBySlug.get(skill.slug),
  }));

  for (const adminSkill of adminSharedSkills) {
    if (visibleSlugs.has(adminSkill.slug)) continue;
    rows.push({
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
      adminShared: adminSkill,
      adminOnly: true,
    });
  }

  return rows;
}

// 用户侧技能管理(C-3 列举/toggle + E-2 导入/导出/删除)。中间面板接管(同
// ConversationBrowser),全用户可见。个人开关写 user_skill 覆盖,控 `enabled`
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
  // 正在写覆盖/删除的 slug 集(禁用其控件防抖动)。
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<SkillRow | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const setActiveMode = useUIStore((s) => s.setActiveMode);

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

  const handleToggle = useCallback(async (slug: string, next: boolean) => {
    // 乐观更新 + 失败回滚。pending 期禁开关避免连点。
    setPending((p) => new Set(p).add(slug));
    setSkills((list) =>
      list.map((s) => (s.slug === slug ? { ...s, enabled: next } : s)),
    );
    try {
      const updated = await setSkillEnabled(slug, next);
      setSkills((list) =>
        list.map((s) => (s.slug === slug ? { ...s, ...updated } : s)),
      );
    } catch (err) {
      // 回滚
      setSkills((list) =>
        list.map((s) => (s.slug === slug ? { ...s, enabled: !next } : s)),
      );
      console.error(`Failed to toggle skill ${slug}:`, err);
    } finally {
      setPending((p) => {
        const n = new Set(p);
        n.delete(slug);
        return n;
      });
    }
  }, []);

  const handleExport = useCallback(async (skill: SkillRow) => {
    setRowError(null);
    try {
      const blob = await downloadSkillBundle(skill.slug, {
        admin: Boolean(skill.adminShared),
      });
      triggerBlobDownload(`${skill.slug}.zip`, blob);
    } catch (err) {
      setRowError(err instanceof Error ? err.message : '导出失败');
    }
  }, []);

  const handleAdminUpdate = useCallback(
    async (skill: SkillRow, patch: AdminSkillUpdateRequest) => {
      if (!skill.adminShared?.can_edit) return;
      setRowError(null);
      setPending((p) => new Set(p).add(skill.slug));
      try {
        await adminUpdateSkill(skill.slug, patch);
        await fetchSkills();
      } catch (err) {
        setRowError(err instanceof Error ? err.message : '更新共享技能失败');
      } finally {
        setPending((p) => {
          const n = new Set(p);
          n.delete(skill.slug);
          return n;
        });
      }
    },
    [fetchSkills],
  );

  const handleConfirmDelete = useCallback(
    async () => {
      const skill = deleteTarget;
      if (!skill) return;
      setRowError(null);
      setPending((p) => new Set(p).add(skill.slug));
      try {
        // 非本人的 dynamic skill 只有 admin 通道能删(user 通道 403)
        if (skill.is_owner) {
          await deleteSkill(skill.slug);
        } else {
          await adminDeleteSkill(skill.slug);
        }
        setSkills((list) => list.filter((s) => s.slug !== skill.slug));
        setDeleteTarget(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : '删除失败';
        setRowError(message);
        if (err instanceof Error) throw err;
        throw new Error(message);
      } finally {
        setPending((p) => {
          const n = new Set(p);
          n.delete(skill.slug);
          return n;
        });
      }
    },
    [deleteTarget],
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
          </p>

          {/* 导入入口 + 内联导入卡片(中间面板接管,不动右面板) */}
          <button
            onClick={() => {
              setImportNotice(null);
              setImportOpen((v) => !v);
            }}
            className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border font-medium transition-colors ${
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
            <div className="px-3 py-2 text-xs text-status-error bg-status-error/10 rounded-lg">
              {rowError}
            </div>
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
            const busy = pending.has(skill.slug);
            const deletable =
              skill.source === 'dynamic' && (skill.is_owner || isAdmin);
            const adminShared = skill.adminShared;
            const canAdminEdit = Boolean(adminShared?.can_edit);
            const canUsePersonalToggle = !skill.adminOnly;
            const exportable = !skill.adminOnly || Boolean(adminShared);
            return (
              <div
                key={skill.slug}
                className={`flex items-start gap-3 px-4 py-3 rounded-xl bg-surface dark:bg-surface-dark border transition-colors ${
                  skill.enabled
                    ? 'border-accent/60'
                    : 'border-border dark:border-border-dark'
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
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
                    <p className="mt-0.5 text-xs text-text-secondary dark:text-text-secondary-dark line-clamp-2">
                      {skill.description}
                    </p>
                  )}
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

                {/* Row actions */}
                <div className="flex items-center gap-1 flex-shrink-0 mt-0.5">
                  {exportable && (
                    <button
                      onClick={() => handleExport(skill)}
                      disabled={busy}
                      className="h-6 w-6 flex items-center justify-center rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors disabled:opacity-40"
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
                      className="h-6 w-6 flex items-center justify-center rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error hover:bg-status-error/10 transition-colors disabled:opacity-40"
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
                {canUsePersonalToggle ? (
                  <button
                    type="button"
                    role="switch"
                    aria-checked={skill.enabled}
                    aria-label={`${skill.enabled ? '关闭' : '开启'}技能 ${skill.name}`}
                    disabled={busy}
                    onClick={() => handleToggle(skill.slug, !skill.enabled)}
                    className="flex-shrink-0 mt-0.5 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <SwitchTrack checked={skill.enabled} />
                  </button>
                ) : (
                  <span
                    className="mt-1 flex-shrink-0 text-[11px] text-text-tertiary dark:text-text-tertiary-dark"
                    title="当前账号不在该 skill 的可见范围内"
                  >
                    不可见
                  </span>
                )}
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
  onImported,
  onClose,
}: {
  isAdmin: boolean;
  onImported: (data: SkillImportResponse) => void;
  onClose: () => void;
}) {
  const [stage, setStage] = useState<ImportStage>({ kind: 'pick' });
  const [file, setFile] = useState<File | null>(null);
  const [marketplace, setMarketplace] = useState(false);
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
    if (!file) return;
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
          setError('技能包未通过校验，请修复后重新打包上传：');
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
            <div className="text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
              zip 内含一个 SKILL.md（可带 scripts / references / assets）
            </div>
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

      {isAdmin && (
        <div className="space-y-2">
          <button
            type="button"
            role="switch"
            aria-checked={marketplace}
            aria-label="导入为共享技能"
            onClick={() => setMarketplace((v) => !v)}
            className={`flex w-full items-center justify-between gap-3 rounded-lg border bg-chat dark:bg-chat-dark px-3 py-2 text-left transition-colors ${
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
                管理员可配置公开或部门可见
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

      {error && <div className="text-status-error text-xs">{error}</div>}
      {rejectFindings && <FindingList findings={rejectFindings} />}

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
          disabled={!file || stage.kind === 'submitting'}
          type="button"
          className={`${BUTTON_PRIMARY} rounded-lg px-4 py-1.5 text-sm`}
        >
          {stage.kind === 'submitting' ? '导入中…' : '导入'}
        </button>
      </div>
    </div>
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
    <StatusNotice
      tone="success"
      title={
        <>
          <span>已导入</span>
          <span>{skill.name}</span>
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
        </>
      }
      onDismiss={onDismiss}
      dismissLabel="关闭导入成功提示"
    >
      {findings.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
            校验提示（不阻断，仅供修包参考）
          </div>
          <FindingList findings={findings} />
        </div>
      )}
    </StatusNotice>
  );
}
function FindingList({ findings }: { findings: SkillFindingItem[] }) {
  return (
    <div className="rounded-lg border border-border dark:border-border-dark divide-y divide-border dark:divide-border-dark max-h-48 overflow-y-auto">
      {findings.map((f, i) => (
        <div key={i} className="px-3 py-2 text-xs flex items-start gap-2">
          <PillBadge
            tone={f.severity === 'error' ? 'error' : 'warning'}
            className="mt-px"
          >
            {f.severity === 'error' ? '错误' : '提示'}
          </PillBadge>
          <div className="min-w-0">
            <span className="font-mono text-text-tertiary dark:text-text-tertiary-dark">
              {f.rule}
            </span>
            <div className="text-text-secondary dark:text-text-secondary-dark mt-0.5 break-words">
              {f.message}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
