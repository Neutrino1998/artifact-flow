'use client';

import { useState, useEffect, useCallback } from 'react';
import { getSkills, setSkillEnabled } from '@/lib/api';
import type { SkillItem } from '@/types';

// 用户侧技能管理(C-3)。中间面板接管(同 ConversationBrowser),全用户可见、非 admin。
// 列出所有**可见** skill(含被禁用的 —— 要能重新开启);个人开关写 user_skill 覆盖,
// 控 `enabled`(进不进模型 L1 索引 + 对话内激活选择器),不碰 `visible`(系统定)。
export default function SkillManagementPanel() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 正在写覆盖的 slug 集(禁用其开关防抖动)。
  const [pending, setPending] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await getSkills();
        if (alive) setSkills(data.skills);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : '加载技能失败');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const handleToggle = useCallback(async (slug: string, next: boolean) => {
    // 乐观更新 + 失败回滚。pending 期禁开关避免连点。
    setPending((p) => new Set(p).add(slug));
    setSkills((list) =>
      list.map((s) => (s.slug === slug ? { ...s, enabled: next } : s)),
    );
    try {
      const updated = await setSkillEnabled(slug, next);
      setSkills((list) => list.map((s) => (s.slug === slug ? updated : s)));
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

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      {/* Header */}
      <div className="px-4 pt-4 pb-2">
        <div className="max-w-3xl mx-auto">
          <div className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-2xl shadow-float px-4 py-3">
            <h2 className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">
              技能管理
            </h2>
            <p className="mt-1 text-xs text-text-tertiary dark:text-text-tertiary-dark">
              关闭的技能不会自动进入对话,也不会出现在输入框的激活选择器里;随时可以重新开启。
            </p>
          </div>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-4 pb-4">
        <div className="max-w-3xl mx-auto space-y-2">
          {loading && (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              加载技能中...
            </div>
          )}

          {!loading && error && (
            <div className="py-12 text-center text-sm text-red-600 dark:text-red-400">{error}</div>
          )}

          {!loading && !error && skills.length === 0 && (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              暂无可用技能。
            </div>
          )}

          {!loading && !error && skills.map((skill) => {
            const overridden = skill.is_overridden && skill.enabled !== skill.default_enabled;
            const busy = pending.has(skill.slug);
            return (
              <div
                key={skill.slug}
                className="flex items-start gap-3 px-4 py-3 rounded-2xl bg-surface dark:bg-surface-dark border border-border dark:border-border-dark"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary dark:text-text-primary-dark truncate">
                      {skill.name}
                    </span>
                    {overridden && (
                      <span className="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded text-text-tertiary dark:text-text-tertiary-dark bg-bg dark:bg-bg-dark border border-border dark:border-border-dark">
                        {skill.enabled ? '已开启' : '已关闭'}
                      </span>
                    )}
                  </div>
                  {skill.description && (
                    <p className="mt-0.5 text-xs text-text-secondary dark:text-text-secondary-dark line-clamp-2">
                      {skill.description}
                    </p>
                  )}
                </div>

                {/* Enable switch */}
                <button
                  role="switch"
                  aria-checked={skill.enabled}
                  aria-label={`${skill.enabled ? '关闭' : '开启'}技能 ${skill.name}`}
                  disabled={busy}
                  onClick={() => handleToggle(skill.slug, !skill.enabled)}
                  className={`relative flex-shrink-0 mt-0.5 h-5 w-9 rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                    skill.enabled ? 'bg-accent' : 'bg-border dark:bg-border-dark'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
                      skill.enabled ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
