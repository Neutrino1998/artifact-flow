/**
 * Runtime site-content fetchers.
 *
 * 设计要点：
 * - 通知从 authenticated backend API 读取（DB 共享）；欢迎提示与品牌仍来自
 *   frontend 本地静态文件。
 * - 通知按 severity 排序、按时间窗过滤、被 dismiss 的剔除，都在这里做完，
 *   组件只渲染。
 */

import { getNotifications } from '@/lib/api';

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

const NOTIFICATION_STATE_KEY_PREFIX = 'af.notification_state.';
const SEVERITY_RANK: Record<Severity, number> = { info: 0, warn: 1, critical: 2 };

interface NotificationBrowserState {
  seen: string[];
  dismissed: string[];
}

function notificationStateKey(userId: string): string {
  return `${NOTIFICATION_STATE_KEY_PREFIX}${encodeURIComponent(userId)}`;
}

function readNotificationState(userId: string): NotificationBrowserState {
  if (typeof window === 'undefined') return { seen: [], dismissed: [] };
  try {
    const raw = window.localStorage.getItem(notificationStateKey(userId));
    if (!raw) return { seen: [], dismissed: [] };
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { seen: [], dismissed: [] };
    }
    const state = parsed as Record<string, unknown>;
    return {
      seen: Array.isArray(state.seen)
        ? state.seen.filter((x): x is string => typeof x === 'string')
        : [],
      dismissed: Array.isArray(state.dismissed)
        ? state.dismissed.filter((x): x is string => typeof x === 'string')
        : [],
    };
  } catch {
    return { seen: [], dismissed: [] };
  }
}

function writeNotificationState(userId: string, state: NotificationBrowserState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(notificationStateKey(userId), JSON.stringify(state));
  } catch {
    // Browser storage is intentionally best-effort. A blocked/full store means
    // notifications may reappear on a later page load, never that they vanish.
  }
}

export function dismissNotification(userId: string, id: string): void {
  const state = readNotificationState(userId);
  if (!state.dismissed.includes(id)) state.dismissed.push(id);
  writeNotificationState(userId, state);
}

export function unseenNotificationIds(
  userId: string,
  notifications: Notification[],
): string[] {
  const seen = new Set(readNotificationState(userId).seen);
  return notifications.filter((item) => !seen.has(item.id)).map((item) => item.id);
}

export function markNotificationsSeen(userId: string, ids: string[]): void {
  if (ids.length === 0) return;
  const state = readNotificationState(userId);
  const seen = new Set(state.seen);
  for (const id of ids) seen.add(id);
  writeNotificationState(userId, { ...state, seen: Array.from(seen) });
}

// 可选时间字段在 schema 校验阶段就被解析过，到达 isActive 时一定是有效 epoch。
// fail-closed：写错日期格式 → 整条通知丢弃，不会因为 Date.parse 返回 NaN 而被
// 当成"无时间边界"提前曝光或永不过期。
type ParsedNotification = Omit<Notification, 'starts_at' | 'ends_at'> & {
  starts_at_ms?: number;
  ends_at_ms?: number;
};

function parseOptionalDate(value: unknown): number | undefined | null {
  // 返回 undefined = 字段缺失（合法）；number = 解析成功；null = 字段存在但解析失败（拒绝整条）
  if (value === undefined) return undefined;
  if (typeof value !== 'string') return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

function validateNotification(x: unknown): ParsedNotification | null {
  if (!x || typeof x !== 'object') return null;
  const n = x as Record<string, unknown>;
  if (typeof n.id !== 'string' || typeof n.title !== 'string' || typeof n.body !== 'string') return null;
  if (n.severity !== 'info' && n.severity !== 'warn' && n.severity !== 'critical') return null;

  const starts = parseOptionalDate(n.starts_at);
  if (starts === null) return null;
  const ends = parseOptionalDate(n.ends_at);
  if (ends === null) return null;

  // dismissible fail-closed 与日期字段对齐：present-but-not-boolean（例如
  // 字符串 "false"）会让运维以为 notice 强制展示，结果用户点叉就关掉了。
  // 字段缺失 -> undefined（沿用默认值 true）；字段存在但非 bool -> 整条丢。
  let dismissible: boolean | undefined;
  if (n.dismissible === undefined) {
    dismissible = undefined;
  } else if (typeof n.dismissible === 'boolean') {
    dismissible = n.dismissible;
  } else {
    return null;
  }

  return {
    id: n.id,
    title: n.title,
    body: n.body,
    severity: n.severity,
    dismissible,
    starts_at_ms: starts,
    ends_at_ms: ends,
  };
}

function isActive(n: ParsedNotification, now: number): boolean {
  if (n.starts_at_ms !== undefined && now < n.starts_at_ms) return false;
  if (n.ends_at_ms !== undefined && now > n.ends_at_ms) return false;
  return true;
}

function toNotification(p: ParsedNotification): Notification {
  // ParsedNotification 是内部表示；对外仍保留原始 ISO 字符串语义不必要 ——
  // 外部组件只看 id/title/body/severity/dismissible，时间字段已经 baked-in
  // 过滤逻辑里了，所以直接吐出去就行。
  return {
    id: p.id,
    title: p.title,
    body: p.body,
    severity: p.severity,
    dismissible: p.dismissible,
  };
}

export async function fetchNotifications(userId: string): Promise<Notification[]> {
  const response = await getNotifications();
  const raw: unknown = response.notifications;
  if (!Array.isArray(raw)) return [];

  const now = Date.now();
  const dismissed = new Set(readNotificationState(userId).dismissed);

  const parsed: ParsedNotification[] = [];
  for (const item of raw) {
    const v = validateNotification(item);
    if (v !== null) parsed.push(v);
  }

  const visible = parsed.filter((n) => {
    if (!isActive(n, now)) return false;
    // dismissible 默认 true；用户已 dismiss 的剔除
    if (n.dismissible === false) return true;
    return !dismissed.has(n.id);
  });

  // critical > warn > info；同 severity 保持配置顺序
  visible.sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]);
  return visible.map(toNotification);
}

// ============================================================
// Branding（页脚版权 / 问题反馈入口）
// ============================================================
// 与 welcome_tips 同源：静态 JSON、运维改文件即生效、
// 出错一律 null 让组件隐藏：缺文件 / 写坏 schema → 页脚消失
//（fail-closed），而不是回退到代码常量掩盖运维错误。

export interface FeedbackLink {
  label: string;
  href: string;
}

export interface Branding {
  developer: string;
  feedback?: FeedbackLink;
}

function validateFeedbackHref(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'mailto:' || url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function validateBranding(x: unknown): Branding | null {
  if (!x || typeof x !== 'object') return null;
  const b = x as Record<string, unknown>;
  if (typeof b.developer !== 'string' || b.developer.trim() === '') return null;
  if (b.contact_email !== undefined) return null;

  let feedback: FeedbackLink | undefined;
  if (b.feedback !== undefined) {
    if (!b.feedback || typeof b.feedback !== 'object' || Array.isArray(b.feedback)) return null;
    const f = b.feedback as Record<string, unknown>;
    if (typeof f.label !== 'string' || f.label.trim() === '') return null;
    if (typeof f.href !== 'string' || f.href.trim() === '') return null;

    const href = f.href.trim();
    if (!validateFeedbackHref(href)) return null;
    feedback = { label: f.label.trim(), href };
  }

  return { developer: b.developer, feedback };
}

export async function fetchBranding(): Promise<Branding | null> {
  let raw: unknown;
  try {
    const res = await fetch('/site/branding.json', { cache: 'no-store' });
    if (!res.ok) return null;
    raw = await res.json();
  } catch {
    return null;
  }
  return validateBranding(raw);
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
