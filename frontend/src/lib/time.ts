/**
 * 时间工具:把后端发来的 naive ISO 字符串显式当 UTC 解析。
 *
 * 后端约定(utils/time.utc_now):所有写入 DB / API 响应 / 事件 payload 的
 * datetime 都是 naive UTC,序列化为 ISO 字符串时**没有 Z 后缀也没有时区
 * 偏移**(例:`"2026-05-18T08:30:00.123456"`)。
 *
 * JS `new Date(<naive ISO>)` 会把字符串当**本地时间**解析——浏览器在
 * Shanghai 时就当 UTC+8 处理,显示时再"转回本地"就出现 8h 偏差。修法:
 * 显式追加 `Z` 把它锚定为 UTC,后续 `toLocaleString` 等 API 才能按
 * client TZ 正确换算。
 *
 * 历史数据兜底:已经带时区(`Z` / `+08:00` 等)的字符串保持原样;只有
 * 朴素串才补 `Z`。
 *
 * 背景:`docs/_archive/ops/incident-2026-05-14-fix-plan.md` PR-tz-unify。
 */

const HAS_TZ_SUFFIX = /(Z|[+\-]\d{2}:?\d{2})$/;

/** Append `Z` to naive ISO strings so `new Date()` treats them as UTC. */
export function parseUtcIso(iso: string): Date {
  const normalized = HAS_TZ_SUFFIX.test(iso) ? iso : `${iso}Z`;
  return new Date(normalized);
}
