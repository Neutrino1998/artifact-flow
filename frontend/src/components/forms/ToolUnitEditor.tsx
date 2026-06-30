'use client';

import { INPUT_ON_PANEL, LABEL_CLASS } from '@/lib/styles';
import Checkbox from '@/components/forms/Checkbox';
import type { CreateToolUnitRequest, ToolUnitResponse } from '@/types';

// ---------------------------------------------------------------------------
// Draft 模型
//
// 编辑器持「draft」而非直接持请求体：headers 用有序数组（字典在 UI 里没法稳定
// 编辑空键/重复键），parameters 的 default/enum 用原始文本（按 type 在提交期 coerce）。
// draftToRequest 做唯一的一次 校验 + coerce + 收口成请求体;unitResponseToDraft 反向。
// 后端 _build_definition 不按 type 强转 default(原样存)——所以 coerce 必须在这里做,
// 否则整数默认值会以字符串落库。
// ---------------------------------------------------------------------------

export type UnitKind = 'tool' | 'toolset';
export type ParamType = 'string' | 'integer' | 'number' | 'boolean';

export interface ParamDraft {
  name: string;
  type: ParamType;
  description: string;
  required: boolean;
  default: string; // 原始文本;'' → null,按 type coerce
  enum: string; // 逗号/换行分隔的原始文本;'' → null
}

export interface MemberDraft {
  member_name: string;
  permission: 'auto' | 'confirm';
  description: string;
  endpoint: string;
  method: string;
  headers: Array<{ key: string; value: string }>;
  parameters: ParamDraft[];
  response_extract: string; // '' → null
  timeout: number;
}

export interface UnitDraft {
  name: string;
  kind: UnitKind;
  description: string;
  visibility: 'public' | 'department';
  defer: boolean;
  members: MemberDraft[];
}

const PARAM_TYPES: ParamType[] = ['string', 'integer', 'number', 'boolean'];
const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];

function emptyMember(): MemberDraft {
  return {
    member_name: '',
    permission: 'confirm',
    description: '',
    endpoint: '',
    method: 'GET',
    headers: [],
    parameters: [],
    response_extract: '',
    timeout: 60,
  };
}

export function emptyUnitDraft(): UnitDraft {
  return {
    name: '',
    kind: 'tool',
    description: '',
    visibility: 'public',
    defer: false,
    members: [emptyMember()],
  };
}

function scalarToText(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return String(v);
}

export function unitResponseToDraft(u: ToolUnitResponse): UnitDraft {
  return {
    name: u.name,
    kind: (u.kind === 'toolset' ? 'toolset' : 'tool'),
    description: u.description ?? '',
    visibility: (u.visibility === 'department' ? 'department' : 'public'),
    defer: u.defer,
    members: u.members.map((m) => {
      const def = (m.definition ?? {}) as Record<string, unknown>;
      const headersObj = (def.headers ?? {}) as Record<string, unknown>;
      const params = Array.isArray(def.parameters) ? (def.parameters as Array<Record<string, unknown>>) : [];
      return {
        member_name: m.member_name,
        permission: (m.permission === 'auto' ? 'auto' : 'confirm'),
        description: typeof def.description === 'string' ? def.description : '',
        endpoint: typeof def.endpoint === 'string' ? def.endpoint : '',
        method: typeof def.method === 'string' ? def.method : 'GET',
        headers: Object.entries(headersObj).map(([key, value]) => ({ key, value: scalarToText(value) })),
        parameters: params.map((p) => ({
          name: typeof p.name === 'string' ? p.name : '',
          type: (PARAM_TYPES.includes(p.type as ParamType) ? (p.type as ParamType) : 'string'),
          description: typeof p.description === 'string' ? p.description : '',
          required: p.required !== false,
          default: scalarToText(p.default),
          enum: Array.isArray(p.enum) ? (p.enum as unknown[]).map(scalarToText).join('\n') : '',
        })),
        response_extract: typeof def.response_extract === 'string' ? def.response_extract : '',
        timeout: typeof def.timeout === 'number' ? def.timeout : 60,
      };
    }),
  };
}

// 仅在本编辑器自己的提交期 coerce/校验。后端存 default 时不按 type 强转(原样存),故"绕过本
// UI 用 REST 直建的、type 与 default 不符的脏 unit"在这里重存会抛错——但 dynamic unit 的唯一
// 创建者就是本 UI(建时即 coerce),该脏态按构造不可达,不额外兜底(reviewer #4)。
function coerceScalar(type: ParamType, raw: string): unknown {
  const t = raw.trim();
  if (t === '') return null;
  if (type === 'integer') {
    if (!/^-?\d+$/.test(t)) throw new Error(`整数参数的值「${t}」不是合法整数`);
    return parseInt(t, 10);
  }
  if (type === 'number') {
    const n = Number(t);
    // 拒非有限(NaN/Infinity):否则 Infinity 过了"合法数字"校验,却被 JSON.stringify 静默
    // 吞成 null → 默认值无声丢失。loud-fail 优于静默(reviewer #5)。
    if (!Number.isFinite(n)) throw new Error(`数值参数的值「${t}」不是合法有限数字`);
    return n;
  }
  if (type === 'boolean') {
    if (t === 'true') return true;
    if (t === 'false') return false;
    throw new Error(`布尔参数的值「${t}」必须是 true 或 false`);
  }
  return raw;
}

function parseEnum(type: ParamType, raw: string): unknown[] | null {
  // 只按换行分隔(与 unitResponseToDraft 的 \n join 对齐)→ 往返无损,枚举值本身可含逗号(reviewer #3)
  const parts = raw.split('\n').map((s) => s.trim()).filter((s) => s.length > 0);
  if (parts.length === 0) return null;
  return parts.map((p) => coerceScalar(type, p));
}

/** draft → 请求体;校验失败抛 Error(中文),由调用方 catch 显示。后端仍是权威校验。 */
export function draftToRequest(d: UnitDraft): CreateToolUnitRequest {
  const name = d.name.trim();
  if (!name) throw new Error('unit 名称不能为空');
  if (name.includes('__')) throw new Error("unit 名称不能包含 '__'(前缀分隔符)");
  if (d.members.length === 0) throw new Error('至少需要一个工具成员');
  if (d.kind === 'tool' && d.members.length !== 1) {
    throw new Error('单工具 unit 只能有一个成员');
  }

  const seen = new Set<string>();
  const members = d.members.map((m) => {
    const memberName = d.kind === 'tool' ? name : m.member_name.trim();
    if (!memberName) throw new Error('工具成员缺少名称');
    if (seen.has(memberName)) throw new Error(`成员名称「${memberName}」重复`);
    seen.add(memberName);

    if (m.timeout < 1 || m.timeout > 600) {
      throw new Error(`成员「${memberName}」的超时必须在 1~600 秒之间`);
    }

    const headers: Record<string, string> = {};
    for (const h of m.headers) {
      const k = h.key.trim();
      if (!k) continue; // 空键行直接丢弃
      headers[k] = h.value;
    }

    const parameters = m.parameters.map((p) => {
      const pname = p.name.trim();
      if (!pname) throw new Error(`成员「${memberName}」有参数缺少名称`);
      return {
        name: pname,
        type: p.type,
        description: p.description,
        required: p.required,
        default: coerceScalar(p.type, p.default),
        enum: parseEnum(p.type, p.enum),
      };
    });

    return {
      member_name: memberName,
      permission: m.permission,
      description: m.description,
      endpoint: m.endpoint.trim(),
      method: m.method,
      headers,
      parameters,
      response_extract: m.response_extract.trim() || null,
      timeout: m.timeout,
    };
  });

  return {
    name,
    kind: d.kind,
    description: d.description,
    visibility: d.visibility,
    defer: d.defer,
    members,
  };
}

// ---------------------------------------------------------------------------
// 编辑器组件（受控）。readOnly = seeded unit:全字段禁用、无增删按钮。
// lockIdentity = 编辑既有 unit:name/kind 不可变(后端 ImmutableFieldError)。
// ---------------------------------------------------------------------------

interface ToolUnitEditorProps {
  value: UnitDraft;
  onChange: (next: UnitDraft) => void;
  /** seeded unit:整体只读 */
  readOnly?: boolean;
  /** 编辑既有 unit:name/kind 锁定(创建时 false) */
  lockIdentity?: boolean;
  disabled?: boolean;
}

export const SELECT_CHEVRON = (
  <svg
    className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary dark:text-text-tertiary-dark"
    width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
  >
    <path d="M3 4.5l3 3 3-3" />
  </svg>
);

export default function ToolUnitEditor({
  value,
  onChange,
  readOnly = false,
  lockIdentity = false,
  disabled = false,
}: ToolUnitEditorProps) {
  const ro = readOnly || disabled;

  const patch = (p: Partial<UnitDraft>) => onChange({ ...value, ...p });

  const patchMember = (idx: number, p: Partial<MemberDraft>) =>
    onChange({
      ...value,
      members: value.members.map((m, i) => (i === idx ? { ...m, ...p } : m)),
    });

  const handleKindChange = (kind: UnitKind) => {
    // tool 必须恰好 1 个成员;toolset → tool 时截断到第一个
    const members = kind === 'tool' ? value.members.slice(0, 1) : value.members;
    onChange({ ...value, kind, members: members.length ? members : [emptyMember()] });
  };

  const addMember = () => onChange({ ...value, members: [...value.members, emptyMember()] });
  const removeMember = (idx: number) =>
    onChange({ ...value, members: value.members.filter((_, i) => i !== idx) });

  return (
    <div className="space-y-6">
      {/* ── 核心字段 ── */}
      <div className="space-y-4">
        <div>
          <label className={LABEL_CLASS}>
            unit 名称 {!lockIdentity && <span className="text-status-error">*</span>}
          </label>
          {lockIdentity ? (
            <div className="font-mono text-sm text-text-secondary dark:text-text-secondary-dark break-all">
              {value.name}
            </div>
          ) : (
            <>
              <input
                type="text"
                value={value.name}
                onChange={(e) => patch({ name: e.target.value })}
                disabled={ro}
                placeholder="如 weather_api"
                className={`${INPUT_ON_PANEL} font-mono`}
              />
              <p className="text-text-tertiary dark:text-text-tertiary-dark text-xs mt-1">
                全局唯一,禁含 &apos;__&apos;(工具全名前缀分隔符)
              </p>
            </>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL_CLASS}>类型</label>
            {lockIdentity ? (
              <div className="text-sm text-text-secondary dark:text-text-secondary-dark py-2">
                {value.kind === 'tool' ? '单工具' : '工具集'}
                <span className="ml-2 text-xs text-text-tertiary dark:text-text-tertiary-dark">建后不可变</span>
              </div>
            ) : (
              <div className="relative">
                <select
                  value={value.kind}
                  onChange={(e) => handleKindChange(e.target.value as UnitKind)}
                  disabled={ro}
                  className={`${INPUT_ON_PANEL} appearance-none pr-9`}
                >
                  <option value="tool">单工具（singleton）</option>
                  <option value="toolset">工具集（toolset）</option>
                </select>
                {SELECT_CHEVRON}
              </div>
            )}
          </div>
          <div>
            <label className={LABEL_CLASS}>可见性</label>
            <div className="relative">
              <select
                value={value.visibility}
                onChange={(e) => patch({ visibility: e.target.value as UnitDraft['visibility'] })}
                disabled={ro}
                className={`${INPUT_ON_PANEL} appearance-none pr-9`}
              >
                <option value="public">公开（public）</option>
                <option value="department">部门（department）</option>
              </select>
              {SELECT_CHEVRON}
            </div>
          </div>
        </div>

        <div>
          <label className={LABEL_CLASS}>描述</label>
          <input
            type="text"
            value={value.description}
            onChange={(e) => patch({ description: e.target.value })}
            disabled={ro}
            placeholder="这个 unit 的用途"
            className={INPUT_ON_PANEL}
          />
        </div>

        <label className="flex items-center gap-3 select-none cursor-pointer">
          <Checkbox
            checked={value.defer}
            onChange={(c) => patch({ defer: c })}
            disabled={ro}
            ariaLabel="渐进式披露"
          />
          <span className="text-sm text-text-primary dark:text-text-primary-dark">
            渐进式披露（defer）
          </span>
          <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
            默认不进目录,经 search_tools 检索后才暴露
          </span>
        </label>
      </div>

      {/* ── 成员 ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">
            工具成员{value.kind === 'toolset' && <span className="ml-1 text-text-tertiary dark:text-text-tertiary-dark">（{value.members.length}）</span>}
          </div>
          {!ro && value.kind === 'toolset' && (
            <button
              type="button"
              onClick={addMember}
              className="px-3 py-1 text-xs rounded-md border border-border dark:border-border-dark text-accent hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              + 添加工具
            </button>
          )}
        </div>

        {value.members.map((m, idx) => (
          <MemberCard
            key={idx}
            index={idx}
            member={m}
            kind={value.kind}
            readOnly={ro}
            canRemove={!ro && value.kind === 'toolset' && value.members.length > 1}
            onChange={(p) => patchMember(idx, p)}
            onRemove={() => removeMember(idx)}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单个成员卡片
// ---------------------------------------------------------------------------

function MemberCard({
  index,
  member,
  kind,
  readOnly,
  canRemove,
  onChange,
  onRemove,
}: {
  index: number;
  member: MemberDraft;
  kind: UnitKind;
  readOnly: boolean;
  canRemove: boolean;
  onChange: (p: Partial<MemberDraft>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-xl border border-border dark:border-border-dark p-4 space-y-4 bg-surface/40 dark:bg-surface-dark/40">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-text-tertiary dark:text-text-tertiary-dark">
          {kind === 'toolset' ? `成员 #${index + 1}` : '工具定义'}
        </div>
        {canRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="text-xs text-status-error hover:underline"
          >
            移除
          </button>
        )}
      </div>

      {kind === 'toolset' && (
        <div>
          <label className={LABEL_CLASS}>
            成员名 <span className="text-status-error">*</span>
          </label>
          <input
            type="text"
            value={member.member_name}
            onChange={(e) => onChange({ member_name: e.target.value })}
            disabled={readOnly}
            placeholder="裸名;全名 = unit名__成员名"
            className={`${INPUT_ON_PANEL} font-mono`}
          />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={LABEL_CLASS}>权限等级</label>
          <div className="relative">
            <select
              value={member.permission}
              onChange={(e) => onChange({ permission: e.target.value as MemberDraft['permission'] })}
              disabled={readOnly}
              className={`${INPUT_ON_PANEL} appearance-none pr-9`}
            >
              <option value="confirm">每次执行需授权（confirm）</option>
              <option value="auto">自动执行（auto）</option>
            </select>
            {SELECT_CHEVRON}
          </div>
        </div>
        <div>
          <label className={LABEL_CLASS}>超时（秒）</label>
          <input
            type="number"
            min={1}
            max={600}
            value={member.timeout}
            onChange={(e) => onChange({ timeout: Number(e.target.value) })}
            disabled={readOnly}
            className={INPUT_ON_PANEL}
          />
        </div>
      </div>

      <div>
        <label className={LABEL_CLASS}>工具描述</label>
        <input
          type="text"
          value={member.description}
          onChange={(e) => onChange({ description: e.target.value })}
          disabled={readOnly}
          placeholder="模型看到的工具说明"
          className={INPUT_ON_PANEL}
        />
      </div>

      <div className="grid grid-cols-[1fr_auto] gap-3">
        <div>
          <label className={LABEL_CLASS}>请求地址（endpoint）</label>
          <input
            type="text"
            value={member.endpoint}
            onChange={(e) => onChange({ endpoint: e.target.value })}
            disabled={readOnly}
            placeholder="https://api.example.com/v1/..."
            className={`${INPUT_ON_PANEL} font-mono`}
          />
        </div>
        <div>
          <label className={LABEL_CLASS}>方法</label>
          <div className="relative">
            <select
              value={member.method}
              onChange={(e) => onChange({ method: e.target.value })}
              disabled={readOnly}
              className={`${INPUT_ON_PANEL} appearance-none pr-9`}
            >
              {METHODS.map((mm) => (
                <option key={mm} value={mm}>{mm}</option>
              ))}
            </select>
            {SELECT_CHEVRON}
          </div>
        </div>
      </div>
      <p className="text-text-tertiary dark:text-text-tertiary-dark text-xs -mt-2">
        endpoint / 请求头可用 <code className="font-mono">{'{{TOOL_SECRET_*}}'}</code> 占位符引用凭证,运行期替换
      </p>

      <HeaderEditor
        headers={member.headers}
        readOnly={readOnly}
        onChange={(headers) => onChange({ headers })}
      />

      <ParamEditor
        params={member.parameters}
        readOnly={readOnly}
        onChange={(parameters) => onChange({ parameters })}
      />

      <div>
        <label className={LABEL_CLASS}>响应提取（response_extract，可选）</label>
        <input
          type="text"
          value={member.response_extract}
          onChange={(e) => onChange({ response_extract: e.target.value })}
          disabled={readOnly}
          placeholder="JMESPath 表达式(如 data.price),留空返回原始响应"
          className={`${INPUT_ON_PANEL} font-mono`}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 请求头键值编辑器
// ---------------------------------------------------------------------------

function HeaderEditor({
  headers,
  readOnly,
  onChange,
}: {
  headers: Array<{ key: string; value: string }>;
  readOnly: boolean;
  onChange: (next: Array<{ key: string; value: string }>) => void;
}) {
  const update = (idx: number, p: Partial<{ key: string; value: string }>) =>
    onChange(headers.map((h, i) => (i === idx ? { ...h, ...p } : h)));
  const add = () => onChange([...headers, { key: '', value: '' }]);
  const remove = (idx: number) => onChange(headers.filter((_, i) => i !== idx));

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className={`${LABEL_CLASS} mb-0`}>请求头</label>
        {!readOnly && (
          <button type="button" onClick={add} className="text-xs text-accent hover:underline">
            + 添加请求头
          </button>
        )}
      </div>
      {headers.length === 0 ? (
        <p className="text-xs text-text-tertiary dark:text-text-tertiary-dark">无</p>
      ) : (
        <div className="space-y-2">
          {headers.map((h, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                type="text"
                value={h.key}
                onChange={(e) => update(idx, { key: e.target.value })}
                disabled={readOnly}
                placeholder="Header 名"
                className={`${INPUT_ON_PANEL} font-mono flex-1`}
              />
              <input
                type="text"
                value={h.value}
                onChange={(e) => update(idx, { value: e.target.value })}
                disabled={readOnly}
                placeholder="值 / {{TOOL_SECRET_X}}"
                className={`${INPUT_ON_PANEL} font-mono flex-1`}
              />
              {!readOnly && (
                <button
                  type="button"
                  onClick={() => remove(idx)}
                  className="flex-shrink-0 p-1.5 text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error transition-colors"
                  aria-label="移除请求头"
                >
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                    <path d="M3 3l8 8M11 3l-8 8" />
                  </svg>
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 参数列表编辑器
// ---------------------------------------------------------------------------

function ParamEditor({
  params,
  readOnly,
  onChange,
}: {
  params: ParamDraft[];
  readOnly: boolean;
  onChange: (next: ParamDraft[]) => void;
}) {
  const update = (idx: number, p: Partial<ParamDraft>) =>
    onChange(params.map((x, i) => (i === idx ? { ...x, ...p } : x)));
  const add = () =>
    onChange([...params, { name: '', type: 'string', description: '', required: true, default: '', enum: '' }]);
  const remove = (idx: number) => onChange(params.filter((_, i) => i !== idx));

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className={`${LABEL_CLASS} mb-0`}>参数</label>
        {!readOnly && (
          <button type="button" onClick={add} className="text-xs text-accent hover:underline">
            + 添加参数
          </button>
        )}
      </div>
      {params.length === 0 ? (
        <p className="text-xs text-text-tertiary dark:text-text-tertiary-dark">无</p>
      ) : (
        <div className="space-y-3">
          {params.map((p, idx) => (
            <div key={idx} className="rounded-lg border border-border/60 dark:border-border-dark/60 p-3 space-y-2">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={p.name}
                  onChange={(e) => update(idx, { name: e.target.value })}
                  disabled={readOnly}
                  placeholder="参数名"
                  className={`${INPUT_ON_PANEL} font-mono flex-1`}
                />
                <div className="relative w-32 flex-shrink-0">
                  <select
                    value={p.type}
                    onChange={(e) => update(idx, { type: e.target.value as ParamType })}
                    disabled={readOnly}
                    className={`${INPUT_ON_PANEL} appearance-none pr-9`}
                  >
                    {PARAM_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                  {SELECT_CHEVRON}
                </div>
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => remove(idx)}
                    className="flex-shrink-0 p-1.5 text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error transition-colors"
                    aria-label="移除参数"
                  >
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                      <path d="M3 3l8 8M11 3l-8 8" />
                    </svg>
                  </button>
                )}
              </div>
              <input
                type="text"
                value={p.description}
                onChange={(e) => update(idx, { description: e.target.value })}
                disabled={readOnly}
                placeholder="参数说明"
                className={INPUT_ON_PANEL}
              />
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="text"
                  value={p.default}
                  onChange={(e) => update(idx, { default: e.target.value })}
                  disabled={readOnly}
                  placeholder="默认值（可选）"
                  className={`${INPUT_ON_PANEL} font-mono`}
                />
                <textarea
                  value={p.enum}
                  onChange={(e) => update(idx, { enum: e.target.value })}
                  disabled={readOnly}
                  rows={2}
                  placeholder="枚举值,每行一个（可选）"
                  className={`${INPUT_ON_PANEL} font-mono resize-y`}
                />
              </div>
              <label className="flex items-center gap-2 select-none cursor-pointer">
                <Checkbox
                  checked={p.required}
                  onChange={(c) => update(idx, { required: c })}
                  disabled={readOnly}
                  ariaLabel="必填参数"
                />
                <span className="text-xs text-text-secondary dark:text-text-secondary-dark">必填</span>
              </label>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
