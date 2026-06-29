'use client';

import { useCallback, useEffect, useState } from 'react';
import * as api from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import {
  BUTTON_DANGER_OUTLINE,
  BUTTON_PRIMARY,
  INPUT_ON_PANEL,
  LABEL_CLASS,
} from '@/lib/styles';
import PanelShell from '@/components/layout/PanelShell';
import DangerConfirmModal from '@/components/layout/DangerConfirmModal';
import ToolUnitEditor, {
  draftToRequest,
  unitResponseToDraft,
  SELECT_CHEVRON,
  type UnitDraft,
} from '@/components/forms/ToolUnitEditor';
import { SourceBadge, StateBadge } from '@/components/forms/ToolUnitBadges';
import type {
  AgentSummaryResponse,
  CredentialStatusResponse,
  MountedAgentResponse,
  ToolUnitResponse,
} from '@/types';

interface ToolUnitDetailFormProps {
  unitName: string;
}

export default function ToolUnitDetailForm({ unitName }: ToolUnitDetailFormProps) {
  const setRightView = useUIStore((s) => s.setToolUnitRightView);
  const bumpListVersion = useUIStore((s) => s.bumpToolUnitListVersion);

  const [unit, setUnit] = useState<ToolUnitResponse | null>(null);
  const [agents, setAgents] = useState<AgentSummaryResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // 编辑器 draft + 基线(最近一次已保存态),用于 dirty 判定
  const [baseline, setBaseline] = useState<UnitDraft | null>(null);
  const [draft, setDraft] = useState<UnitDraft | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const isDynamic = unit?.source === 'dynamic';

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [u, agentsRes] = await Promise.all([
        api.getToolUnit(unitName),
        api.listToolAgents(),
      ]);
      setUnit(u);
      const d = unitResponseToDraft(u);
      setBaseline(d);
      setDraft(d);
      setAgents(agentsRes.agents);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : '加载工具 unit 失败');
    } finally {
      setLoading(false);
    }
  }, [unitName]);

  useEffect(() => {
    load();
    setSaveError(null);
    setConfirmDelete(false);
  }, [load]);

  // 挂载/凭证是即时生效的独立端点 — 操作后只刷新 unit 的挂载/凭证展示,
  // 不动编辑器 draft/baseline(避免冲掉未保存的核心/成员编辑)。
  const refreshLiveState = useCallback(async () => {
    try {
      const u = await api.getToolUnit(unitName);
      setUnit(u);
      bumpListVersion();
    } catch {
      // 刷新失败不阻断 — 下次操作会再拉
    }
  }, [unitName, bumpListVersion]);

  const dirty =
    isDynamic && baseline !== null && draft !== null &&
    JSON.stringify(baseline) !== JSON.stringify(draft);

  const handleSave = async () => {
    if (!draft || !dirty || saving) return;
    setSaveError(null);
    let body;
    try {
      body = draftToRequest(draft);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '表单校验失败');
      return;
    }
    setSaving(true);
    try {
      const updated = await api.updateToolUnit(unitName, body);
      setUnit(updated);
      const d = unitResponseToDraft(updated);
      setBaseline(d);
      setDraft(d);
      bumpListVersion();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    await api.deleteToolUnit(unitName);
    bumpListVersion();
    setConfirmDelete(false);
    setRightView({ type: 'empty' });
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-chat dark:bg-chat-dark p-6">
        <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark">加载中...</div>
      </div>
    );
  }

  if (loadError || !unit || !draft) {
    return (
      <div className="flex-1 flex flex-col gap-3 items-center justify-center bg-chat dark:bg-chat-dark p-6">
        <div className="text-sm text-status-error">{loadError ?? '工具 unit 不存在'}</div>
        <button
          onClick={load}
          className="px-4 py-1.5 rounded-lg border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark font-medium bg-surface dark:bg-surface-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
        >
          重试
        </button>
      </div>
    );
  }

  return (
    <PanelShell
      header={
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-base font-semibold text-text-primary dark:text-text-primary-dark truncate font-mono">
                {unit.name}
              </span>
              <SourceBadge source={unit.source} />
            </div>
            <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark mt-0.5">
              {unit.kind === 'tool' ? '单工具' : '工具集'} · {unit.provider} · 可见性 {unit.visibility}
            </div>
          </div>
          <button
            onClick={() => setRightView({ type: 'empty' })}
            className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
            aria-label="关闭"
            title="关闭"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      }
      footer={
        isDynamic ? (
          <>
            <button
              onClick={() => setConfirmDelete(true)}
              disabled={saving}
              title="删除该动态 unit(连带其动态挂载与凭证)"
              className={`${BUTTON_DANGER_OUTLINE} rounded-lg px-5 py-2`}
            >
              删除
            </button>
            <button
              onClick={handleSave}
              disabled={!dirty || saving}
              className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
            >
              {saving ? '保存中...' : '保存定义'}
            </button>
          </>
        ) : (
          <p className="flex-1 text-center text-sm text-text-secondary dark:text-text-secondary-dark">
            种子 unit:定义只读。改 config/tools 后重跑 reconcile。挂载可在下方调整。
          </p>
        )
      }
    >
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
        {/* 定义编辑器 — seeded 只读 */}
        <ToolUnitEditor
          value={draft}
          onChange={setDraft}
          readOnly={!isDynamic}
          lockIdentity
          disabled={saving}
        />

        {saveError && <div className="text-status-error text-sm">{saveError}</div>}

        <div className="border-t border-border dark:border-border-dark" />

        {/* 挂载管理 — 对所有 unit 可用(创建 dynamic 绑定);seeded 绑定只读 */}
        <MountSection
          unitName={unit.name}
          mounted={unit.mounted_agents}
          agents={agents}
          onChanged={refreshLiveState}
        />

        <div className="border-t border-border dark:border-border-dark" />

        {/* 凭证 — 写-only;dynamic 可配,seeded 仅看状态(由 reconcile/env 提供) */}
        <CredentialSection
          unitName={unit.name}
          credentials={unit.credentials}
          isDynamic={isDynamic}
          onChanged={refreshLiveState}
        />
      </div>

      {confirmDelete && (
        <DangerConfirmModal
          title="删除工具 unit"
          message={
            `unit：${unit.name}\n` +
            `将删除该 unit 的定义、动态 agent 挂载与已配置凭证。\n` +
            `操作不可恢复。`
          }
          confirmLabel="确认删除"
          onCancel={() => setConfirmDelete(false)}
          onConfirm={handleDelete}
        />
      )}
    </PanelShell>
  );
}

// ---------------------------------------------------------------------------
// 挂载区
// ---------------------------------------------------------------------------

function MountSection({
  unitName,
  mounted,
  agents,
  onChanged,
}: {
  unitName: string;
  mounted: MountedAgentResponse[];
  agents: AgentSummaryResponse[];
  onChanged: () => Promise<void> | void;
}) {
  const [addAgent, setAddAgent] = useState('');
  const [addState, setAddState] = useState<'enabled' | 'disabled'>('enabled');
  const [busy, setBusy] = useState<string | null>(null); // agent_name 或 '__add__'
  const [error, setError] = useState<string | null>(null);

  const mountedNames = new Set(mounted.map((m) => m.agent_name));
  const available = agents.filter((a) => !mountedNames.has(a.name));

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusy(null);
    }
  };

  const handleAdd = () => {
    if (!addAgent) return;
    void run('__add__', async () => {
      await api.mountToolUnit(unitName, addAgent, { member_state: addState });
      setAddAgent('');
    });
  };

  return (
    <div className="space-y-3">
      <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">
        挂载到 agent
      </div>

      {mounted.length === 0 ? (
        <p className="text-xs text-text-tertiary dark:text-text-tertiary-dark">尚未挂载到任何 agent</p>
      ) : (
        <div className="space-y-2">
          {mounted.map((m) => {
            const seeded = m.source === 'seeded';
            const rowBusy = busy === m.agent_name;
            return (
              <div
                key={m.agent_name}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border dark:border-border-dark"
              >
                <span className="font-mono text-sm text-text-primary dark:text-text-primary-dark truncate flex-1">
                  {m.agent_name}
                </span>
                <StateBadge state={m.member_state} />
                <SourceBadge source={m.source} />
                {seeded ? (
                  <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark">MD 绑定,只读</span>
                ) : (
                  <>
                    <button
                      onClick={() =>
                        run(m.agent_name, () =>
                          api.mountToolUnit(unitName, m.agent_name, {
                            member_state: m.member_state === 'enabled' ? 'disabled' : 'enabled',
                          }),
                        )
                      }
                      disabled={rowBusy}
                      className="px-2 py-1 text-xs rounded-md border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
                    >
                      {m.member_state === 'enabled' ? '停用' : '启用'}
                    </button>
                    <button
                      onClick={() => run(m.agent_name, () => api.unmountToolUnit(unitName, m.agent_name))}
                      disabled={rowBusy}
                      className="p-1.5 text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error disabled:opacity-40 transition-colors"
                      aria-label="卸载"
                      title="卸载"
                    >
                      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                        <path d="M3 3l8 8M11 3l-8 8" />
                      </svg>
                    </button>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 新增挂载 */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <select
            value={addAgent}
            onChange={(e) => setAddAgent(e.target.value)}
            disabled={available.length === 0 || busy === '__add__'}
            className={`${INPUT_ON_PANEL} appearance-none pr-9`}
          >
            <option value="">{available.length === 0 ? '无可挂载 agent' : '选择 agent...'}</option>
            {available.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}{a.internal ? '（内部）' : ''}
              </option>
            ))}
          </select>
          {SELECT_CHEVRON}
        </div>
        <div className="relative w-28 flex-shrink-0">
          <select
            value={addState}
            onChange={(e) => setAddState(e.target.value as 'enabled' | 'disabled')}
            disabled={busy === '__add__'}
            className={`${INPUT_ON_PANEL} appearance-none pr-9`}
          >
            <option value="enabled">启用</option>
            <option value="disabled">停用</option>
          </select>
          {SELECT_CHEVRON}
        </div>
        <button
          onClick={handleAdd}
          disabled={!addAgent || busy === '__add__'}
          className="flex-shrink-0 px-3 py-2 text-sm rounded-lg border border-border dark:border-border-dark text-accent hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
        >
          挂载
        </button>
      </div>

      {error && <div className="text-status-error text-sm">{error}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 凭证区
// ---------------------------------------------------------------------------

function CredentialSection({
  unitName,
  credentials,
  isDynamic,
  onChanged,
}: {
  unitName: string;
  credentials: CredentialStatusResponse[];
  isDynamic: boolean;
  onChanged: () => Promise<void> | void;
}) {
  return (
    <div className="space-y-3">
      <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">
        凭证
      </div>
      <p className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
        占位符取自各成员 endpoint / 请求头里的 <code className="font-mono">{'{{...}}'}</code> 引用。
        {isDynamic
          ? '值加密落库、永不回读 — 留空提交即不改。新增引用请先保存定义。'
          : '种子 unit 凭证由 reconcile / env 提供,此处只读。'}
      </p>

      {credentials.length === 0 ? (
        <p className="text-xs text-text-tertiary dark:text-text-tertiary-dark">无凭证占位符</p>
      ) : (
        <div className="space-y-2">
          {credentials.map((c) => (
            <CredentialRow
              key={c.placeholder}
              unitName={unitName}
              cred={c}
              isDynamic={isDynamic}
              onChanged={onChanged}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function CredentialRow({
  unitName,
  cred,
  isDynamic,
  onChanged,
}: {
  unitName: string;
  cred: CredentialStatusResponse;
  isDynamic: boolean;
  onChanged: () => Promise<void> | void;
}) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>, clear: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (clear) setValue('');
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="px-3 py-2 rounded-lg border border-border dark:border-border-dark space-y-2">
      <div className="flex items-center gap-2">
        <code className="font-mono text-sm text-text-primary dark:text-text-primary-dark truncate flex-1">
          {`{{${cred.placeholder}}}`}
        </code>
        {cred.configured ? (
          <span className="inline-flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
            已配置{cred.source ? `（${cred.source}）` : ''}
          </span>
        ) : (
          <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark">未配置</span>
        )}
      </div>

      {isDynamic && (
        <div className="flex items-center gap-2">
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            disabled={busy}
            placeholder={cred.configured ? '输入新值以覆盖' : '输入凭证值'}
            autoComplete="new-password"
            className={`${INPUT_ON_PANEL} flex-1`}
          />
          <button
            onClick={() => run(() => api.setToolCredential(unitName, cred.placeholder, { value }), true)}
            disabled={busy || value.length === 0}
            className="flex-shrink-0 px-3 py-2 text-sm rounded-lg border border-border dark:border-border-dark text-accent hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
          >
            保存
          </button>
          {cred.configured && (
            <button
              onClick={() => run(() => api.deleteToolCredential(unitName, cred.placeholder), false)}
              disabled={busy}
              className="flex-shrink-0 p-2 text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error disabled:opacity-40 transition-colors"
              aria-label="删除凭证"
              title="删除凭证"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M3 3l8 8M11 3l-8 8" />
              </svg>
            </button>
          )}
        </div>
      )}

      {error && <div className="text-status-error text-xs">{error}</div>}
    </div>
  );
}
