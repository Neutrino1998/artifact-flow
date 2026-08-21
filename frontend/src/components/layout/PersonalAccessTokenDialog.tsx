'use client';

import { useEffect, useMemo, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import type {
  PersonalAccessTokenCreateResponse,
  PersonalAccessTokenResponse,
  PersonalAccessTokenScope,
} from '@/types';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { CopyIcon } from '@/components/ui/CopyIcon';
import { parseUtcIso } from '@/lib/time';
import {
  BUTTON_DANGER_OUTLINE,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  INPUT_ON_PANEL,
  LABEL_CLASS,
} from '@/lib/styles';
import ConfirmModal from './ConfirmModal';
import DialogShell from './DialogShell';

interface PersonalAccessTokenDialogProps {
  onClose: () => void;
}

const SCOPE_OPTIONS: Array<{
  value: PersonalAccessTokenScope;
  label: string;
  description: string;
}> = [
  {
    value: 'conversations:read',
    label: '读取对话',
    description: '查看完整对话、事件和 SSE，可能包含 Artifact 与工具内容',
  },
  { value: 'conversations:write', label: '发送对话', description: '发送消息、上传附件和引用文件' },
  { value: 'conversations:control', label: '控制执行', description: '向运行中的任务注入消息或取消任务' },
  { value: 'conversations:delete', label: '删除对话', description: '删除单个或批量删除自己的对话' },
  {
    value: 'artifacts:read',
    label: '读取文件',
    description: '通过 Artifact API 查看、下载文件和历史版本',
  },
  { value: 'skills:read', label: '读取技能', description: '查看和导出用户可见的技能' },
  { value: 'skills:write', label: '管理技能', description: '导入、启停和删除自己的技能' },
  {
    value: 'tools:approve',
    label: '批准工具',
    description: '批准单次调用，或按工具在当前对话分支持续允许',
  },
];

const DEFAULT_SCOPES = new Set<PersonalAccessTokenScope>([
  'conversations:read',
  'conversations:write',
  'artifacts:read',
]);

function formatDate(value: string): string {
  return parseUtcIso(value).toLocaleString('zh-CN');
}

export default function PersonalAccessTokenDialog({
  onClose,
}: PersonalAccessTokenDialogProps) {
  const [tokens, setTokens] = useState<PersonalAccessTokenResponse[]>([]);
  const [name, setName] = useState('');
  const [expiresInDays, setExpiresInDays] = useState('90');
  const [scopes, setScopes] = useState<Set<PersonalAccessTokenScope>>(
    () => new Set(DEFAULT_SCOPES),
  );
  const [created, setCreated] = useState<PersonalAccessTokenCreateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<PersonalAccessTokenResponse | null>(null);
  const [revoking, setRevoking] = useState(false);
  const { copied, copy } = useCopyFeedback();

  useEffect(() => {
    let active = true;
    void api.listPersonalAccessTokens()
      .then((result) => {
        if (active) setTokens(result.tokens);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof ApiError ? err.message : '加载 API 密钥失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  const selectedScopes = useMemo(
    () => SCOPE_OPTIONS.map((option) => option.value).filter((scope) => scopes.has(scope)),
    [scopes],
  );
  const parsedExpiresInDays = Number(expiresInDays);
  const expiryIsValid = /^\d+$/.test(expiresInDays)
    && Number.isInteger(parsedExpiresInDays)
    && parsedExpiresInDays >= 1
    && parsedExpiresInDays <= 365;

  const toggleScope = (scope: PersonalAccessTokenScope) => {
    setScopes((current) => {
      const next = new Set(current);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  };

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (
      loading
      || !name.trim()
      || selectedScopes.length === 0
      || !expiryIsValid
      || submitting
    ) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.createPersonalAccessToken({
        name: name.trim(),
        scopes: selectedScopes,
        expires_in_days: parsedExpiresInDays,
      });
      setCreated(result);
      setTokens((current) => [result, ...current]);
      setName('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '创建 API 密钥失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget || revoking) return;
    setRevoking(true);
    setError(null);
    try {
      await api.revokePersonalAccessToken(revokeTarget.id);
      setTokens((current) => current.filter((token) => token.id !== revokeTarget.id));
      setRevokeTarget(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '撤销 API 密钥失败');
    } finally {
      setRevoking(false);
    }
  };

  return (
    <>
      <DialogShell
        title="个人访问令牌（PAT）"
        description="PAT 代表你的用户身份调用普通 API。密钥只在创建成功时显示一次。"
        size="lg"
        onClose={onClose}
        closeOnBackdrop={!submitting && !created}
        closeOnEscape={!submitting && !created}
        surfaceClassName="bg-chat dark:bg-chat-dark"
      >
        <div className="space-y-6">
          <section
            aria-label="PAT API 调用方式"
            className="rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark p-3 text-xs text-text-secondary dark:text-text-secondary-dark"
          >
            <div className="font-medium text-text-primary dark:text-text-primary-dark">API 调用方式</div>
            <code className="mt-1 block break-all text-accent">Authorization: Bearer &lt;PAT&gt;</code>
            <p className="mt-1">
              只可调用普通用户 API；创建时选择的权限范围（scope）决定它能执行哪些操作。
            </p>
          </section>

          {created && (
            <section className="rounded-lg border border-status-warning/50 bg-status-warning/10 p-4">
              <div className="font-medium text-text-primary dark:text-text-primary-dark">
                立即复制并妥善保存
              </div>
              <p className="mt-1 text-sm text-text-secondary dark:text-text-secondary-dark">
                关闭后无法再次查看这个密钥；遗失时请撤销并重新创建。
              </p>
              <div className="mt-3 flex items-center gap-2 rounded-lg bg-bg dark:bg-bg-dark p-3">
                <code className="min-w-0 flex-1 break-all text-xs text-text-primary dark:text-text-primary-dark">
                  {created.token}
                </code>
                <button
                  type="button"
                  aria-label="复制 API 密钥"
                  title={copied ? '已复制' : '复制'}
                  onClick={() => void copy(created.token)}
                  className="shrink-0 rounded-md p-2 text-text-secondary hover:text-text-primary dark:text-text-secondary-dark dark:hover:text-text-primary-dark"
                >
                  <CopyIcon copied={copied} />
                </button>
              </div>
              <div className="mt-3 flex justify-end">
                <button
                  type="button"
                  onClick={() => setCreated(null)}
                  className={`${BUTTON_PRIMARY} rounded-lg px-5 py-2`}
                >
                  我已保存
                </button>
              </div>
            </section>
          )}

          {!created && (
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_160px]">
                <div>
                  <label className={LABEL_CLASS}>名称</label>
                  <input
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    maxLength={128}
                    placeholder="例如：数据分析脚本"
                    className={INPUT_ON_PANEL}
                    disabled={loading || submitting}
                  />
                </div>
                <div>
                  <div className="flex items-baseline justify-between gap-2">
                    <label htmlFor="pat-expiry-days" className={LABEL_CLASS}>有效期（天）</label>
                    <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
                      最长 365 天
                    </span>
                  </div>
                  <input
                    id="pat-expiry-days"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    maxLength={3}
                    value={expiresInDays}
                    onChange={(event) => {
                      if (/^\d*$/.test(event.target.value)) {
                        setExpiresInDays(event.target.value);
                      }
                    }}
                    placeholder="1–365"
                    aria-invalid={!expiryIsValid}
                    className={INPUT_ON_PANEL}
                    disabled={loading || submitting}
                  />
                </div>
              </div>

              <fieldset>
                <legend className={LABEL_CLASS}>权限范围</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {SCOPE_OPTIONS.map((option) => (
                    <label
                      key={option.value}
                      className="flex cursor-pointer items-start gap-3 rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark p-3"
                    >
                      <input
                        type="checkbox"
                        checked={scopes.has(option.value)}
                        onChange={() => toggleScope(option.value)}
                        disabled={loading || submitting}
                        className="mt-1 accent-accent"
                      />
                      <span>
                        <span className="block text-sm font-medium text-text-primary dark:text-text-primary-dark">
                          {option.label}
                        </span>
                        <span className="block text-xs text-text-secondary dark:text-text-secondary-dark">
                          {option.description}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="flex justify-end gap-3">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={submitting}
                  className={`${BUTTON_SECONDARY} rounded-lg px-6 py-2`}
                >
                  关闭
                </button>
                <button
                  type="submit"
                  disabled={
                    loading
                    || !name.trim()
                    || selectedScopes.length === 0
                    || !expiryIsValid
                    || submitting
                  }
                  className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
                >
                  {submitting ? '创建中…' : '创建密钥'}
                </button>
              </div>
            </form>
          )}

          {error && <div className="text-sm text-status-error">{error}</div>}

          <section>
            <h3 className="mb-2 text-sm font-medium text-text-primary dark:text-text-primary-dark">
              有效密钥（最多 50 个）
            </h3>
            {loading ? (
              <div className="text-sm text-text-secondary dark:text-text-secondary-dark">加载中…</div>
            ) : tokens.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border dark:border-border-dark p-4 text-sm text-text-secondary dark:text-text-secondary-dark">
                暂无 API 密钥
              </div>
            ) : (
              <div className="space-y-2">
                {tokens.map((token) => (
                  <div
                    key={token.id}
                    className="flex flex-col gap-3 rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark p-3 sm:flex-row sm:items-center"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-text-primary dark:text-text-primary-dark">
                        {token.name}
                      </div>
                      <div className="mt-1 font-mono text-xs text-text-secondary dark:text-text-secondary-dark">
                        {token.prefix}
                      </div>
                      <div className="mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                        到期：{formatDate(token.expires_at)}
                        {token.last_used_at ? ` · 最近使用：${formatDate(token.last_used_at)}` : ' · 尚未使用'}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setRevokeTarget(token)}
                      className={`${BUTTON_DANGER_OUTLINE} rounded-lg px-4 py-2 text-sm`}
                    >
                      撤销
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </DialogShell>

      {revokeTarget && (
        <ConfirmModal
          title="撤销 API 密钥"
          message={`撤销“${revokeTarget.name}”后，使用它的程序会立即失去访问权限。`}
          confirmLabel={revoking ? '撤销中…' : '撤销'}
          destructive
          onConfirm={() => void handleRevoke()}
          onCancel={() => { if (!revoking) setRevokeTarget(null); }}
        />
      )}
    </>
  );
}
