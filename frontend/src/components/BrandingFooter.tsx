'use client';

import { useEffect, useState } from 'react';
import { fetchBranding, type Branding } from '@/lib/siteConfig';

/**
 * 版权 / 业务联系页脚 —— 侧栏底部 + 登录页底部共用。
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
  sidebar: 'px-3 pb-2 -mt-1 text-[11px] text-text-secondary dark:text-text-secondary-dark text-center truncate',
  // login: 登录卡片下方一行，居中、稍微留白
  login: 'mt-6 text-xs text-text-secondary dark:text-text-secondary-dark text-center',
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

  const { developer, contact_email } = branding;

  return (
    <div className={WRAPPER_CLASS[variant]}>
      <span>由 {developer} 开发</span>
      {contact_email && (
        <>
          <span className="mx-1.5 opacity-60">·</span>
          <a
            href={`mailto:${contact_email}`}
            className="hover:text-accent hover:underline"
          >
            {contact_email}
          </a>
        </>
      )}
    </div>
  );
}
