'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import * as api from '@/lib/api';
import ChangePasswordDialog from '@/components/layout/ChangePasswordDialog';

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isHydrated, hydrate, setUser, user } = useAuthStore();
  const router = useRouter();

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (isHydrated && !isAuthenticated) {
      router.replace('/login');
    }
  }, [isHydrated, isAuthenticated, router]);

  // hydrate() 只从 localStorage 还原，新增字段（如 department_path）对已登录
  // 会话是缺的。这里跑一次 /me 拉最新 UserInfo 覆盖缓存；token 失效时
  // request() 内部会触发 logout，无需额外处理。
  useEffect(() => {
    if (!isHydrated || !isAuthenticated) return;
    let cancelled = false;
    api.getMe()
      .then((u) => { if (!cancelled) setUser(u); })
      .catch(() => { /* 静默：网络/401 已由 request() 兜底 */ });
    return () => { cancelled = true; };
  }, [isHydrated, isAuthenticated, setUser]);

  // Show minimal loading state while hydrating or redirecting to login
  // Using null causes a blank flash when logging out before the login page loads
  if (!isHydrated || !isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg dark:bg-bg-dark">
        <div className="text-text-tertiary dark:text-text-tertiary-dark">Loading...</div>
      </div>
    );
  }

  // 强制改密(门类三):must_change_password 为 True 时,挡住整个应用,只渲染
  // 不可关闭的改密框。后端闸门已对受保护端点 403 兜底;这里是 UX 引导。
  // user 经 hydrate(localStorage)初值可能缺该字段,getMe() 刷新后会补上 → 重渲染。
  if (user?.must_change_password) {
    return (
      <div className="min-h-screen bg-bg dark:bg-bg-dark">
        <ChangePasswordDialog forced onClose={() => { /* forced: 无关闭 */ }} />
      </div>
    );
  }

  return <>{children}</>;
}
