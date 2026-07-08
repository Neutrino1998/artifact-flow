'use client';

import { useEffect, useState } from 'react';
import { fetchBranding, type Branding } from '@/lib/siteConfig';

/**
 * 版权 / 问题反馈页脚 —— 侧栏底部 + 登录页底部共用。
 *
 * 数据来自 public/site/branding.json（fetchBranding 已经做了 fail-closed
 * 校验：404 / 解析失败 / schema 错位 → null）。null 时整个组件渲染 null,
 * 让运维通过删文件就能彻底隐藏。
 *
 * variant 只影响外层 margin / 字体微调，不影响内容结构。
 */

type Variant = 'sidebar' | 'login';

const WRAPPER_CLASS: Record<Variant, string> = {
  // sidebar: 紧贴 UserMenu 下方，左右对齐 padding 与上方按钮一致
  sidebar: 'px-3 pb-2 -mt-1 flex items-center justify-center gap-2 text-[11px] text-text-secondary dark:text-text-secondary-dark text-center truncate',
  // login: 登录卡片下方一行，居中、稍微留白
  login: 'mt-6 flex items-center justify-center gap-2 text-xs text-text-secondary dark:text-text-secondary-dark text-center',
};

export default function BrandingFooter({ variant }: { variant: Variant }) {
  const [branding, setBranding] = useState<Branding | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchBranding().then((b) => {
      if (!cancelled) setBranding(b);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!branding) return null;

  const { developer, feedback } = branding;
  const opensNewTab = /^https?:\/\//i.test(feedback?.href ?? '');

  return (
    <div className={WRAPPER_CLASS[variant]}>
      <span className="leading-none">由 {developer} 开发</span>
      {feedback && (
        <a
          href={feedback.href}
          target={opensNewTab ? '_blank' : undefined}
          rel={opensNewTab ? 'noopener noreferrer' : undefined}
          className="inline-flex items-center gap-1 leading-none hover:text-accent hover:underline"
        >
          <svg
            aria-hidden="true"
            className="h-[0.95em] w-[0.95em] shrink-0 translate-y-px"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="8" cy="8" r="6" />
            <path d="M6.35 6.25a1.75 1.75 0 1 1 2.8 1.4c-.65.48-1.15.85-1.15 1.6" />
            <path d="M8 11.75h.01" />
          </svg>
          {feedback.label}
        </a>
      )}
    </div>
  );
}
