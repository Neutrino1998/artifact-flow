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

  return {
    id: n.id,
    title: n.title,
    body: n.body,
    severity: n.severity,
    dismissible: typeof n.dismissible === 'boolean' ? n.dismissible : undefined,
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

  // critical > warn > info；同 severity 保持文件顺序
  visible.sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]);
  return visible.map(toNotification);
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
