/**
 * Site-config fetchers: 从静态 public/site/*.json 读取通知与欢迎页提示。
 *
 * 设计要点：
 * - 文件 404 / 解析失败 / 字段错位 → 一律返回空数组，调用方据此隐藏组件。
 *   不会因为运维写错 JSON 把整个 UI 崩掉。
 * - 通知按 severity 排序、按时间窗过滤、被 dismiss 的剔除，都在这里做完，
 *   组件只渲染。
 */

export type Severity = 'info' | 'warn' | 'critical';

export interface Notification {
  id: string;
  severity: Severity;
  title: string;
  body: string;
  starts_at?: string;
  ends_at?: string;
  dismissible?: boolean;
}

const DISMISS_KEY = 'af.dismissed_notifications';
const SEVERITY_RANK: Record<Severity, number> = { info: 0, warn: 1, critical: 2 };

function readDismissed(): Set<string> {
  if (typeof window === 'undefined') return new Set();
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed.filter((x): x is string => typeof x === 'string')) : new Set();
  } catch {
    return new Set();
  }
}

export function dismissNotification(id: string): void {
  if (typeof window === 'undefined') return;
  const dismissed = readDismissed();
  dismissed.add(id);
  window.localStorage.setItem(DISMISS_KEY, JSON.stringify(Array.from(dismissed)));
}

function isValidNotification(x: unknown): x is Notification {
  if (!x || typeof x !== 'object') return false;
  const n = x as Record<string, unknown>;
  return (
    typeof n.id === 'string' &&
    typeof n.title === 'string' &&
    typeof n.body === 'string' &&
    (n.severity === 'info' || n.severity === 'warn' || n.severity === 'critical')
  );
}

function isActive(n: Notification, now: number): boolean {
  if (n.starts_at) {
    const start = Date.parse(n.starts_at);
    if (!Number.isNaN(start) && now < start) return false;
  }
  if (n.ends_at) {
    const end = Date.parse(n.ends_at);
    if (!Number.isNaN(end) && now > end) return false;
  }
  return true;
}

export async function fetchNotifications(): Promise<Notification[]> {
  let raw: unknown;
  try {
    const res = await fetch('/site/notifications.json', { cache: 'no-store' });
    if (!res.ok) return [];
    raw = await res.json();
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];

  const now = Date.now();
  const dismissed = readDismissed();
  const items = raw.filter(isValidNotification).filter((n) => isActive(n, now));

  // dismissible 默认 true；用户已 dismiss 的剔除
  const visible = items.filter((n) => {
    if (n.dismissible === false) return true;
    return !dismissed.has(n.id);
  });

  // critical > warn > info；同 severity 保持文件顺序
  visible.sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]);
  return visible;
}

export async function fetchWelcomeTips(): Promise<string[]> {
  let raw: unknown;
  try {
    const res = await fetch('/site/welcome_tips.json', { cache: 'no-store' });
    if (!res.ok) return [];
    raw = await res.json();
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  return raw.filter((x): x is string => typeof x === 'string' && x.trim().length > 0);
}
