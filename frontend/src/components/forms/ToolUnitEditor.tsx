'use client';

import { INPUT_ON_PANEL, LABEL_CLASS } from '@/lib/styles';
import { SELECT_CHEVRON } from '@/components/ui/SelectChevron';
import Checkbox from '@/components/forms/Checkbox';
import InputSchemaEditor from '@/components/forms/InputSchemaEditor';
import type { CreateToolUnitRequest, ToolUnitResponse } from '@/types';

// ---------------------------------------------------------------------------
// Draft 模型
//
// 编辑器持「draft」而非直接持请求体：headers 用有序数组（字典在 UI 里没法稳定
// 编辑空键/重复键），input_schema 用格式化 JSON 文本；draftToRequest 解析并做
// 根结构检查，后端继续负责完整 Draft 2020-12 校验。
// ---------------------------------------------------------------------------

export type UnitKind = 'tool' | 'toolset' | 'mcp';
export type ArtifactOutputMode = 'text' | 'binary';
export type PermissionLevel = 'auto' | 'confirm';

export interface MemberDraft {
  member_name: string;
  permission: PermissionLevel;
  description: string;
  endpoint: string;
  method: string;
  headers: Array<{ key: string; value: string }>;
  input_schema: string;
  response_extract: string; // '' → null
  artifact_output: {
    enabled: boolean;
    mode: ArtifactOutputMode;
    content_type: string;
    filename: string;
    title: string;
  };
  timeout: number;
}

export interface McpProviderConfigDraft {
  transport: 'streamable_http';
  url: string;
  headers: Array<{ key: string; value: string }>;
  timeout: number;
  default_permission: PermissionLevel;
}

export interface UnitDraft {
  name: string;
  kind: UnitKind;
  description: string;
  visibility: 'public' | 'department';
  defer: boolean;
  members: MemberDraft[];
  provider_config: McpProviderConfigDraft;
}

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
const ARTIFACT_OUTPUT_MODES: ArtifactOutputMode[] = ['text', 'binary'];
const TEXT_CONTENT_TYPES = [
  { label: 'Plain text', value: 'text/plain' },
  { label: 'CSV', value: 'text/csv' },
  { label: 'JSON', value: 'application/json' },
  { label: 'Markdown', value: 'text/markdown' },
  { label: 'HTML', value: 'text/html' },
];
const BINARY_CONTENT_TYPES = [
  { label: 'PDF', value: 'application/pdf' },
  { label: 'PNG', value: 'image/png' },
  { label: 'JPEG', value: 'image/jpeg' },
  { label: 'Excel .xlsx', value: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
  { label: 'Excel .xls', value: 'application/vnd.ms-excel' },
  { label: 'Word .docx', value: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
  { label: 'Word .doc', value: 'application/msword' },
  { label: 'PowerPoint .pptx', value: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' },
  { label: 'ZIP', value: 'application/zip' },
  { label: 'Binary', value: 'application/octet-stream' },
];
const CUSTOM_CONTENT_TYPE_OPTION = '__custom__';
const AUTO_CONTENT_TYPE_OPTION = '__auto__';
const EXTENSION_CONTENT_TYPES: Record<string, string> = {
  '.txt': 'text/plain',
  '.csv': 'text/csv',
  '.json': 'application/json',
  '.md': 'text/markdown',
  '.markdown': 'text/markdown',
  '.html': 'text/html',
  '.htm': 'text/html',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xls': 'application/vnd.ms-excel',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.doc': 'application/msword',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.zip': 'application/zip',
};

function emptyMcpProviderConfig(): McpProviderConfigDraft {
  return {
    transport: 'streamable_http',
    url: '',
    headers: [],
    timeout: 60,
    default_permission: 'confirm',
  };
}

function emptyMember(): MemberDraft {
  return {
    member_name: '',
    permission: 'confirm',
    description: '',
    endpoint: '',
    method: 'GET',
    headers: [],
    input_schema: JSON.stringify({
      type: 'object',
      properties: {},
      additionalProperties: false,
    }, null, 2),
    response_extract: '',
    artifact_output: {
      enabled: false,
      mode: 'text',
      content_type: '',
      filename: '',
      title: '',
    },
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
    provider_config: emptyMcpProviderConfig(),
  };
}

function scalarToText(v: unknown, { prettyObjects = true }: { prettyObjects?: boolean } = {}): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'object') {
    try {
      return prettyObjects ? JSON.stringify(v, null, 2) : JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

export function unitResponseToDraft(u: ToolUnitResponse): UnitDraft {
  const providerConfig = (u.provider_config ?? {}) as Record<string, unknown>;
  const providerHeaders = (providerConfig.headers ?? {}) as Record<string, unknown>;
  return {
    name: u.name,
    kind: (u.kind === 'mcp' ? 'mcp' : u.kind === 'toolset' ? 'toolset' : 'tool'),
    description: u.description ?? '',
    visibility: (u.visibility === 'department' ? 'department' : 'public'),
    defer: u.defer,
    provider_config: {
      transport: 'streamable_http',
      url: typeof providerConfig.url === 'string' ? providerConfig.url : '',
      headers: Object.entries(providerHeaders).map(([key, value]) => ({
        key,
        value: scalarToText(value),
      })),
      timeout: typeof providerConfig.timeout === 'number' ? providerConfig.timeout : 60,
      default_permission: providerConfig.default_permission === 'auto' ? 'auto' : 'confirm',
    },
    members: u.members.map((m) => {
      const def = (m.definition ?? {}) as Record<string, unknown>;
      const headersObj = (def.headers ?? {}) as Record<string, unknown>;
      const inputSchema = def.input_schema && typeof def.input_schema === 'object'
        ? def.input_schema
        : { type: 'object', properties: {}, additionalProperties: false };
      const artifactOutput = (def.artifact_output ?? {}) as Record<string, unknown>;
      return {
        member_name: m.member_name,
        permission: (m.permission === 'auto' ? 'auto' : 'confirm'),
        description: typeof def.description === 'string' ? def.description : '',
        endpoint: typeof def.endpoint === 'string' ? def.endpoint : '',
        method: typeof def.method === 'string' ? def.method : 'GET',
        headers: Object.entries(headersObj).map(([key, value]) => ({ key, value: scalarToText(value) })),
        input_schema: JSON.stringify(inputSchema, null, 2),
        response_extract: typeof def.response_extract === 'string' ? def.response_extract : '',
        artifact_output: {
          enabled: artifactOutput.enabled === true,
          mode: ARTIFACT_OUTPUT_MODES.includes(artifactOutput.mode as ArtifactOutputMode)
            ? (artifactOutput.mode as ArtifactOutputMode)
            : 'text',
          content_type: typeof artifactOutput.content_type === 'string' ? artifactOutput.content_type : '',
          filename: typeof artifactOutput.filename === 'string' ? artifactOutput.filename : '',
          title: typeof artifactOutput.title === 'string' ? artifactOutput.title : '',
        },
        timeout: typeof def.timeout === 'number' ? def.timeout : 60,
      };
    }),
  };
}

/** draft → 请求体;校验失败抛 Error(中文),由调用方 catch 显示。后端仍是权威校验。 */
export function draftToRequest(d: UnitDraft): CreateToolUnitRequest {
  const name = d.name.trim();
  if (!name) throw new Error('unit 名称不能为空');
  if (name.includes('__')) throw new Error("unit 名称不能包含 '__'(前缀分隔符)");

  if (d.kind === 'mcp') {
    const url = d.provider_config.url.trim();
    if (!url) throw new Error('MCP server URL 不能为空');
    if (d.provider_config.timeout < 1 || d.provider_config.timeout > 600) {
      throw new Error('MCP server 超时必须在 1~600 秒之间');
    }
    const headers: Record<string, string> = {};
    for (const h of d.provider_config.headers) {
      const k = h.key.trim();
      if (!k) continue;
      headers[k] = h.value;
    }
    return {
      name,
      kind: 'mcp',
      description: d.description,
      visibility: d.visibility,
      defer: d.defer,
      members: [],
      provider_config: {
        transport: 'streamable_http',
        url,
        headers,
        timeout: d.provider_config.timeout,
        default_permission: d.provider_config.default_permission,
      },
    };
  }

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

    let inputSchema: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(m.input_schema);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('必须是 JSON 对象');
      }
      inputSchema = parsed as Record<string, unknown>;
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      throw new Error(`成员「${memberName}」的 input_schema 无效：${detail}`);
    }
    if (inputSchema.type !== 'object') {
      throw new Error(`成员「${memberName}」的 input_schema 根 type 必须是 object`);
    }
    let properties = inputSchema.properties;
    if (properties === undefined) {
      properties = {};
      inputSchema.properties = properties;
    }
    if (properties === null || typeof properties !== 'object' || Array.isArray(properties)) {
      throw new Error(`成员「${memberName}」的 input_schema.properties 必须是对象`);
    }

    return {
      member_name: memberName,
      permission: m.permission,
      description: m.description,
      endpoint: m.endpoint.trim(),
      method: m.method,
      headers,
      input_schema: inputSchema,
      response_extract: m.response_extract.trim() || null,
      artifact_output: m.artifact_output.enabled
        ? {
            enabled: true,
            mode: m.artifact_output.mode,
            content_type: m.artifact_output.content_type.trim() || null,
            filename: m.artifact_output.filename.trim() || null,
            title: m.artifact_output.title.trim() || null,
          }
        : null,
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
    provider_config: null,
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
  const patchMcpConfig = (p: Partial<McpProviderConfigDraft>) =>
    onChange({
      ...value,
      provider_config: { ...value.provider_config, ...p },
    });

  const handleKindChange = (kind: UnitKind) => {
    if (kind === 'mcp') {
      onChange({ ...value, kind, defer: true, members: [] });
      return;
    }
    // tool 必须恰好 1 个成员;toolset → tool 时截断到第一个
    const seedMembers = value.members.length ? value.members : [emptyMember()];
    const members = kind === 'tool' ? seedMembers.slice(0, 1) : seedMembers;
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
                全局唯一，禁含 &apos;__&apos;（工具全名前缀分隔符）
              </p>
            </>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL_CLASS}>类型</label>
            {lockIdentity ? (
              <div className="text-sm text-text-secondary dark:text-text-secondary-dark py-2">
                {value.kind === 'mcp' ? 'MCP server' : value.kind === 'tool' ? '单工具' : '工具集'}
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
                  <option value="mcp">MCP server</option>
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
            默认不进目录，经 search_tools 检索后才暴露
          </span>
        </label>
      </div>

      {value.kind === 'mcp' ? (
        <McpServerEditor
          value={value.provider_config}
          readOnly={ro}
          onChange={patchMcpConfig}
        />
      ) : (
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
      )}
    </div>
  );
}

function McpServerEditor({
  value,
  readOnly,
  onChange,
}: {
  value: McpProviderConfigDraft;
  readOnly: boolean;
  onChange: (p: Partial<McpProviderConfigDraft>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={LABEL_CLASS}>传输</label>
          <div className="relative">
            <select
              value={value.transport}
              disabled
              className={`${INPUT_ON_PANEL} appearance-none pr-9`}
            >
              <option value="streamable_http">streamable_http</option>
            </select>
            {SELECT_CHEVRON}
          </div>
        </div>
        <div>
          <label className={LABEL_CLASS}>默认权限</label>
          <div className="relative">
            <select
              value={value.default_permission}
              onChange={(e) => onChange({ default_permission: e.target.value as PermissionLevel })}
              disabled={readOnly}
              className={`${INPUT_ON_PANEL} appearance-none pr-9`}
            >
              <option value="confirm">每次执行需授权（confirm）</option>
              <option value="auto">自动执行（auto）</option>
            </select>
            {SELECT_CHEVRON}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-[1fr_8rem] gap-3">
        <div>
          <label className={LABEL_CLASS}>
            Server URL <span className="text-status-error">*</span>
          </label>
          <input
            type="text"
            value={value.url}
            onChange={(e) => onChange({ url: e.target.value })}
            disabled={readOnly}
            placeholder="https://mcp.example.com/mcp"
            className={`${INPUT_ON_PANEL} font-mono`}
          />
        </div>
        <div>
          <label className={LABEL_CLASS}>超时（秒）</label>
          <input
            type="number"
            min={1}
            max={600}
            value={value.timeout}
            onChange={(e) => onChange({ timeout: Number(e.target.value) })}
            disabled={readOnly}
            className={INPUT_ON_PANEL}
          />
        </div>
      </div>
      <p className="text-text-tertiary dark:text-text-tertiary-dark text-xs -mt-2">
        URL / 请求头可用 <code className="font-mono">{'{{TOOL_SECRET_*}}'}</code> 占位符引用凭证，运行期替换
      </p>

      <HeaderEditor
        headers={value.headers}
        readOnly={readOnly}
        onChange={(headers) => onChange({ headers })}
      />
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
            placeholder="裸名；全名 = unit名__成员名"
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
        endpoint path 可用 <code className="font-mono">{'{param_name}'}</code> 引用参数；endpoint / 请求头可用 <code className="font-mono">{'{{TOOL_SECRET_*}}'}</code> 引用凭证
      </p>

      <HeaderEditor
        headers={member.headers}
        readOnly={readOnly}
        onChange={(headers) => onChange({ headers })}
      />

      <InputSchemaEditor
        value={member.input_schema}
        endpoint={member.endpoint}
        method={member.method}
        readOnly={readOnly}
        onChange={(input_schema) => onChange({ input_schema })}
      />

      <div>
        <label className={LABEL_CLASS}>响应提取（response_extract，可选）</label>
        <input
          type="text"
          value={member.response_extract}
          onChange={(e) => onChange({ response_extract: e.target.value })}
          disabled={readOnly}
          placeholder="JMESPath 表达式（如 data.price），留空返回原始响应"
          className={`${INPUT_ON_PANEL} font-mono`}
        />
      </div>

      <ArtifactOutputEditor
        value={member.artifact_output}
        readOnly={readOnly}
        onChange={(artifact_output) => onChange({ artifact_output })}
      />
    </div>
  );
}

function ArtifactOutputEditor({
  value,
  readOnly,
  onChange,
}: {
  value: MemberDraft['artifact_output'];
  readOnly: boolean;
  onChange: (next: MemberDraft['artifact_output']) => void;
}) {
  const patch = (p: Partial<MemberDraft['artifact_output']>) => onChange({ ...value, ...p });
  const contentTypeOptions = value.mode === 'text' ? TEXT_CONTENT_TYPES : BINARY_CONTENT_TYPES;
  const selectedPreset = value.content_type.trim()
    ? contentTypeOptions.some((o) => o.value === value.content_type)
      ? value.content_type
      : CUSTOM_CONTENT_TYPE_OPTION
    : value.mode === 'binary'
      ? AUTO_CONTENT_TYPE_OPTION
      : CUSTOM_CONTENT_TYPE_OPTION;

  const inferContentType = (filename: string) => {
    const lower = filename.trim().toLowerCase();
    const dot = lower.lastIndexOf('.');
    if (dot < 0) return null;
    return EXTENSION_CONTENT_TYPES[lower.slice(dot)] ?? null;
  };

  const handleModeChange = (mode: ArtifactOutputMode) => {
    const nextOptions = mode === 'text' ? TEXT_CONTENT_TYPES : BINARY_CONTENT_TYPES;
    const current = value.content_type.trim();
    const currentFitsNextMode = nextOptions.some((o) => o.value === current);
    const nextContentType = currentFitsNextMode ? current : mode === 'text' ? 'text/plain' : '';
    patch({ mode, content_type: nextContentType });
  };

  const handleFilenameChange = (filename: string) => {
    if (value.mode === 'binary') {
      patch({ filename });
      return;
    }
    if (value.content_type.trim()) {
      patch({ filename });
      return;
    }
    const inferred = inferContentType(filename);
    patch({ filename, ...(inferred ? { content_type: inferred } : {}) });
  };

  return (
    <div className="space-y-3 pt-1">
      <label className="flex items-center gap-3 select-none cursor-pointer">
        <Checkbox
          checked={value.enabled}
          onChange={(enabled) => patch({ enabled })}
          disabled={readOnly}
          ariaLabel="保存响应为 artifact"
        />
        <span className="text-sm text-text-primary dark:text-text-primary-dark">
          保存响应为 artifact
        </span>
      </label>

      {value.enabled && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL_CLASS}>模式</label>
              <div className="relative">
                <select
                  value={value.mode}
                  onChange={(e) => handleModeChange(e.target.value as ArtifactOutputMode)}
                  disabled={readOnly}
                  className={`${INPUT_ON_PANEL} appearance-none pr-9`}
                >
                  <option value="text">文本（text）</option>
                  <option value="binary">二进制（binary）</option>
                </select>
                {SELECT_CHEVRON}
              </div>
            </div>
            <div>
              <label className={LABEL_CLASS}>常用类型</label>
              <div className="relative">
                <select
                  value={selectedPreset}
                  onChange={(e) => {
                    const next = e.target.value;
                    if (next === AUTO_CONTENT_TYPE_OPTION) {
                      patch({ content_type: '' });
                    } else if (next !== CUSTOM_CONTENT_TYPE_OPTION) {
                      patch({ content_type: next });
                    }
                  }}
                  disabled={readOnly}
                  className={`${INPUT_ON_PANEL} appearance-none pr-9`}
                >
                  {value.mode === 'binary' && (
                    <option value={AUTO_CONTENT_TYPE_OPTION}>自动读取响应头</option>
                  )}
                  <option value={CUSTOM_CONTENT_TYPE_OPTION}>自定义</option>
                  {contentTypeOptions.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
                {SELECT_CHEVRON}
              </div>
            </div>
          </div>
          <div>
            <label className={LABEL_CLASS}>content_type</label>
            <input
              type="text"
              value={value.content_type}
              onChange={(e) => {
                patch({ content_type: e.target.value });
              }}
              disabled={readOnly}
              placeholder={value.mode === 'text' ? 'text/csv' : '自动读取响应 Content-Type'}
              className={`${INPUT_ON_PANEL} font-mono`}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL_CLASS}>文件名</label>
              <input
                type="text"
                value={value.filename}
                onChange={(e) => handleFilenameChange(e.target.value)}
                disabled={readOnly}
                placeholder="report.csv"
                className={`${INPUT_ON_PANEL} font-mono`}
              />
            </div>
            <div>
              <label className={LABEL_CLASS}>标题</label>
              <input
                type="text"
                value={value.title}
                onChange={(e) => patch({ title: e.target.value })}
                disabled={readOnly}
                placeholder="报表"
                className={INPUT_ON_PANEL}
              />
            </div>
          </div>
        </div>
      )}
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
