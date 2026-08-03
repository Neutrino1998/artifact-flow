'use client';

import { useEffect, useState } from 'react';
import { INPUT_ON_PANEL, LABEL_CLASS } from '@/lib/styles';
import { SELECT_CHEVRON } from '@/components/ui/SelectChevron';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import Checkbox from '@/components/forms/Checkbox';
import {
  addSimpleParameter,
  defaultValueForType,
  formatInputSchemaJson,
  inferHttpParameterLocation,
  inspectInputSchema,
  parseTypedValue,
  removeSimpleParameter,
  renameSimpleParameter,
  setRejectUnknownParameters,
  setSimpleArrayItemType,
  setSimpleParameterDefault,
  setSimpleParameterDescription,
  setSimpleParameterEnum,
  setSimpleParameterRequired,
  setSimpleParameterType,
  typedValueToText,
  type SimpleArrayItemType,
  type SimpleParameterType,
  type SimpleSchemaParameter,
} from '@/lib/inputSchemaForm';

// ---------------------------------------------------------------------------
// 输入参数：JSON Schema 是唯一权威状态；表单只是可无损往返的基础子集投影。
// ---------------------------------------------------------------------------

type SchemaEditorMode = 'form' | 'json';

export default function InputSchemaEditor({
  value,
  endpoint,
  method,
  readOnly,
  onChange,
}: {
  value: string;
  endpoint: string;
  method: string;
  readOnly: boolean;
  onChange: (next: string) => void;
}) {
  const [mode, setMode] = useState<SchemaEditorMode>(() => (
    inspectInputSchema(value).kind === 'simple' ? 'form' : 'json'
  ));
  const [formatError, setFormatError] = useState<string | null>(null);
  const inspection = inspectInputSchema(value);

  const formatJson = () => {
    try {
      onChange(formatInputSchemaJson(value));
      setFormatError(null);
    } catch (error) {
      setFormatError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <label className={`${LABEL_CLASS} mb-0`}>输入参数</label>
        <SegmentedTabs
          value={mode}
          onChange={setMode}
          ariaLabel="输入参数编辑模式"
          options={[
            { value: 'form', label: '参数表单' },
            { value: 'json', label: '高级 JSON Schema' },
          ]}
        />
      </div>

      {mode === 'form' ? (
        inspection.kind === 'simple' ? (
          <SimpleSchemaForm
            schemaText={value}
            inspection={inspection}
            endpoint={endpoint}
            method={method}
            readOnly={readOnly}
            onChange={onChange}
          />
        ) : (
          <div className={`rounded-lg border p-3 text-xs ${
            inspection.kind === 'invalid'
              ? 'border-status-error/30 bg-status-error/5 text-status-error'
              : 'border-status-warning/30 bg-status-warning/5 text-status-warning'
          }`}>
            <div className="font-medium">
              {inspection.kind === 'invalid' ? '当前 Schema 无法解析' : '当前 Schema 包含高级约束'}
            </div>
            <div className="mt-1">{inspection.reason}</div>
            <button
              type="button"
              onClick={() => setMode('json')}
              className="mt-2 text-accent hover:underline"
            >
              转到高级 JSON Schema 编辑
            </button>
          </div>
        )
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
              Draft 2020-12，根 type 必须是 object；这里保存的内容就是模型披露与运行时校验使用的 Schema。
            </p>
            {!readOnly ? (
              <button
                type="button"
                onClick={formatJson}
                className="flex-shrink-0 text-xs text-accent hover:underline"
              >
                格式化 JSON
              </button>
            ) : null}
          </div>
          <textarea
            value={value}
            onChange={(event) => {
              onChange(event.target.value);
              setFormatError(null);
            }}
            disabled={readOnly}
            rows={14}
            spellCheck={false}
            aria-label="输入参数 JSON Schema"
            className={`${INPUT_ON_PANEL} font-mono resize-y`}
          />
          {formatError ? (
            <p className="text-xs text-status-error">{formatError}</p>
          ) : inspection.kind === 'invalid' ? (
            <p className="text-xs text-status-error">{inspection.reason}</p>
          ) : inspection.kind === 'advanced' ? (
            <p className="text-xs text-status-warning">
              {inspection.reason}；参数表单不会修改或简化这些字段。
            </p>
          ) : (
            <p className="text-xs text-status-success">此 Schema 可与参数表单无损切换。</p>
          )}
        </div>
      )}
    </div>
  );
}

function SimpleSchemaForm({
  schemaText,
  inspection,
  endpoint,
  method,
  readOnly,
  onChange,
}: {
  schemaText: string;
  inspection: Extract<ReturnType<typeof inspectInputSchema>, { kind: 'simple' }>;
  endpoint: string;
  method: string;
  readOnly: boolean;
  onChange: (next: string) => void;
}) {
  return (
    <div className="space-y-3">
      {inspection.parameters.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border dark:border-border-dark px-3 py-4 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
          这个工具暂时没有输入参数。
        </div>
      ) : (
        inspection.parameters.map((parameter) => (
          <SimpleParameterCard
            key={parameter.name}
            schemaText={schemaText}
            parameter={parameter}
            endpoint={endpoint}
            method={method}
            readOnly={readOnly}
            onChange={onChange}
          />
        ))
      )}

      <div className="space-y-3">
        {!readOnly ? (
          <button
            type="button"
            onClick={() => onChange(addSimpleParameter(schemaText))}
            className="px-3 py-1.5 text-xs rounded-md border border-border dark:border-border-dark text-accent hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          >
            + 添加参数
          </button>
        ) : null}
        <div>
          <label className="flex items-center gap-2 text-xs text-text-secondary dark:text-text-secondary-dark select-none">
            <Checkbox
              checked={inspection.rejectUnknown}
              onChange={(checked) => onChange(setRejectUnknownParameters(schemaText, checked))}
              disabled={readOnly}
              ariaLabel="只允许已声明参数"
            />
            只允许上面声明的参数（推荐）
          </label>
          <p className="ml-6 mt-1 text-[10px] text-text-tertiary dark:text-text-tertiary-dark">
            对应 additionalProperties: false；模型多传字段时会在工具执行前校验失败。
          </p>
        </div>
      </div>
      <p className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
        参数表单会实时生成 JSON Schema。复杂嵌套、组合约束和条件约束请使用“高级 JSON Schema”。
      </p>
    </div>
  );
}

function SimpleParameterCard({
  schemaText,
  parameter,
  endpoint,
  method,
  readOnly,
  onChange,
}: {
  schemaText: string;
  parameter: SimpleSchemaParameter;
  endpoint: string;
  method: string;
  readOnly: boolean;
  onChange: (next: string) => void;
}) {
  const location = inferHttpParameterLocation(endpoint, method, parameter.name);
  const locationTone = location === 'URL Path'
    ? 'bg-accent/10 text-accent'
    : 'bg-panel-accent dark:bg-bg-dark text-text-tertiary dark:text-text-tertiary-dark';

  return (
    <div className="rounded-lg border border-border dark:border-border-dark bg-bg/40 dark:bg-bg-dark/30 p-3 space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_10rem_auto] gap-2 items-start">
        <ParameterNameEditor
          name={parameter.name}
          readOnly={readOnly}
          onCommit={(nextName) => onChange(renameSimpleParameter(schemaText, parameter.name, nextName))}
        />
        <div>
          <label className={LABEL_CLASS}>类型</label>
          <div className="relative">
            <select
              value={parameter.type}
              onChange={(event) => onChange(setSimpleParameterType(
                schemaText,
                parameter.name,
                event.target.value as SimpleParameterType,
              ))}
              disabled={readOnly}
              aria-label={`参数 ${parameter.name} 类型`}
              className={`${INPUT_ON_PANEL} appearance-none pr-9`}
            >
              <option value="string">文本</option>
              <option value="integer">整数</option>
              <option value="number">数字</option>
              <option value="boolean">布尔值</option>
              <option value="object">对象</option>
              <option value="array">数组</option>
            </select>
            {SELECT_CHEVRON}
          </div>
        </div>
        {!readOnly ? (
          <button
            type="button"
            onClick={() => onChange(removeSimpleParameter(schemaText, parameter.name))}
            className="px-2 mt-6 py-2 text-xs text-status-error hover:underline"
          >
            删除
          </button>
        ) : null}
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-2 text-xs text-text-secondary dark:text-text-secondary-dark select-none">
          <Checkbox
            checked={parameter.required}
            onChange={(checked) => onChange(setSimpleParameterRequired(
              schemaText,
              parameter.name,
              checked,
            ))}
            disabled={readOnly}
            ariaLabel={`参数 ${parameter.name} 必填`}
          />
          必填
        </label>
        <span className={`px-2 py-0.5 rounded-full text-[10px] font-mono ${locationTone}`}>
          {location}
        </span>
        <span className="text-[10px] text-text-tertiary dark:text-text-tertiary-dark">
          根据 endpoint 与 HTTP 方法自动推导
        </span>
      </div>

      <div>
        <label className={LABEL_CLASS}>参数说明</label>
        <input
          type="text"
          value={parameter.description}
          onChange={(event) => onChange(setSimpleParameterDescription(
            schemaText,
            parameter.name,
            event.target.value,
          ))}
          disabled={readOnly}
          placeholder="告诉模型这个参数的含义和填写方式"
          className={INPUT_ON_PANEL}
        />
      </div>

      {parameter.type === 'array' ? (
        <div className="max-w-xs">
          <label className={LABEL_CLASS}>数组元素类型</label>
          <div className="relative">
            <select
              value={parameter.itemType}
              onChange={(event) => onChange(setSimpleArrayItemType(
                schemaText,
                parameter.name,
                event.target.value as SimpleArrayItemType,
              ))}
              disabled={readOnly}
              className={`${INPUT_ON_PANEL} appearance-none pr-9`}
            >
              <option value="any">任意 JSON 值</option>
              <option value="string">文本</option>
              <option value="integer">整数</option>
              <option value="number">数字</option>
              <option value="boolean">布尔值</option>
              <option value="object">对象</option>
            </select>
            {SELECT_CHEVRON}
          </div>
        </div>
      ) : null}

      <div className="space-y-3">
        <OptionalTypedValueEditor
          label="默认值"
          enabled={parameter.hasDefault}
          value={parameter.defaultValue}
          type={parameter.type}
          readOnly={readOnly}
          onToggle={(enabled) => onChange(setSimpleParameterDefault(
            schemaText,
            parameter.name,
            enabled,
            enabled ? defaultValueForType(parameter.type) : undefined,
          ))}
          onCommit={(nextValue) => onChange(setSimpleParameterDefault(
            schemaText,
            parameter.name,
            true,
            nextValue,
          ))}
        />
        <OptionalEnumEditor
          values={parameter.enumValues}
          type={parameter.type}
          readOnly={readOnly}
          onToggle={(enabled) => onChange(setSimpleParameterEnum(
            schemaText,
            parameter.name,
            enabled ? [parameter.type === 'string' ? 'value' : defaultValueForType(parameter.type)] : null,
          ))}
          onCommit={(values) => onChange(setSimpleParameterEnum(
            schemaText,
            parameter.name,
            values,
          ))}
        />
      </div>
    </div>
  );
}

function ParameterNameEditor({
  name,
  readOnly,
  onCommit,
}: {
  name: string;
  readOnly: boolean;
  onCommit: (name: string) => void;
}) {
  const [draft, setDraft] = useState(name);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(name);
    setError(null);
  }, [name]);

  const commit = () => {
    if (draft.trim() === name) {
      setDraft(name);
      return;
    }
    try {
      onCommit(draft);
      setError(null);
    } catch (commitError) {
      setError(commitError instanceof Error ? commitError.message : String(commitError));
    }
  };

  return (
    <div>
      <label className={LABEL_CLASS}>参数名</label>
      <input
        type="text"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur();
        }}
        disabled={readOnly}
        aria-label={`参数名 ${name}`}
        className={`${INPUT_ON_PANEL} font-mono`}
      />
      {error ? <p className="text-[10px] text-status-error mt-1">{error}</p> : null}
    </div>
  );
}

function OptionalTypedValueEditor({
  label,
  enabled,
  value,
  type,
  readOnly,
  onToggle,
  onCommit,
}: {
  label: string;
  enabled: boolean;
  value: unknown;
  type: SimpleParameterType;
  readOnly: boolean;
  onToggle: (enabled: boolean) => void;
  onCommit: (value: unknown) => void;
}) {
  return (
    <div className="rounded-md border border-border/70 dark:border-border-dark/70 p-2 space-y-2">
      <label className="flex items-center gap-2 text-xs text-text-secondary dark:text-text-secondary-dark select-none">
        <Checkbox
          checked={enabled}
          onChange={onToggle}
          disabled={readOnly}
          ariaLabel={`设置${label}`}
        />
        设置{label}
      </label>
      {enabled ? (
        <TypedValueEditor value={value} type={type} readOnly={readOnly} onCommit={onCommit} />
      ) : (
        <p className="text-[10px] text-text-tertiary dark:text-text-tertiary-dark">未设置</p>
      )}
    </div>
  );
}

function TypedValueEditor({
  value,
  type,
  readOnly,
  onCommit,
}: {
  value: unknown;
  type: SimpleParameterType;
  readOnly: boolean;
  onCommit: (value: unknown) => void;
}) {
  const serializedValue = typedValueToText(value, type);
  const [draft, setDraft] = useState(serializedValue);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(serializedValue);
    setError(null);
  }, [serializedValue]);

  if (type === 'boolean') {
    return (
      <div className="relative">
        <select
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            onCommit(event.target.value === 'true');
          }}
          disabled={readOnly}
          className={`${INPUT_ON_PANEL} appearance-none pr-9`}
        >
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
        {SELECT_CHEVRON}
      </div>
    );
  }

  const commit = () => {
    try {
      onCommit(parseTypedValue(draft, type));
      setError(null);
    } catch (commitError) {
      setError(commitError instanceof Error ? commitError.message : String(commitError));
    }
  };
  const isStructured = type === 'array' || type === 'object';

  return (
    <div>
      {isStructured ? (
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          disabled={readOnly}
          rows={2}
          spellCheck={false}
          className={`${INPUT_ON_PANEL} font-mono resize-y`}
        />
      ) : (
        <input
          type={type === 'integer' || type === 'number' ? 'number' : 'text'}
          step={type === 'integer' ? 1 : type === 'number' ? 'any' : undefined}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          disabled={readOnly}
          className={INPUT_ON_PANEL}
        />
      )}
      {error ? <p className="text-[10px] text-status-error mt-1">{error}</p> : null}
    </div>
  );
}

function OptionalEnumEditor({
  values,
  type,
  readOnly,
  onToggle,
  onCommit,
}: {
  values: unknown[] | null;
  type: SimpleParameterType;
  readOnly: boolean;
  onToggle: (enabled: boolean) => void;
  onCommit: (values: unknown[]) => void;
}) {
  return (
    <div className="rounded-md border border-border/70 dark:border-border-dark/70 p-2 space-y-2">
      <label className="flex items-center gap-2 text-xs text-text-secondary dark:text-text-secondary-dark select-none">
        <Checkbox
          checked={values !== null}
          onChange={onToggle}
          disabled={readOnly}
          ariaLabel="限制可选值"
        />
        限制可选值
      </label>
      {values !== null ? (
        <EnumValuesEditor values={values} type={type} readOnly={readOnly} onCommit={onCommit} />
      ) : (
        <p className="text-[10px] text-text-tertiary dark:text-text-tertiary-dark">不限制</p>
      )}
    </div>
  );
}

function EnumValuesEditor({
  values,
  type,
  readOnly,
  onCommit,
}: {
  values: unknown[];
  type: SimpleParameterType;
  readOnly: boolean;
  onCommit: (values: unknown[]) => void;
}) {
  const serializedValues = values.map((value) => typedValueToText(value, type)).join('\n');
  const [draft, setDraft] = useState(serializedValues);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(serializedValues);
    setError(null);
  }, [serializedValues]);

  const commit = () => {
    try {
      const lines = draft.split('\n').filter((line) => line.length > 0);
      if (lines.length === 0) throw new Error('可选值至少需要一项');
      onCommit(lines.map((line) => parseTypedValue(line, type)));
      setError(null);
    } catch (commitError) {
      setError(commitError instanceof Error ? commitError.message : String(commitError));
    }
  };

  return (
    <div>
      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        disabled={readOnly}
        rows={2}
        spellCheck={false}
        placeholder="每行一个可选值"
        className={`${INPUT_ON_PANEL} font-mono resize-y`}
      />
      <p className="text-[10px] text-text-tertiary dark:text-text-tertiary-dark mt-1">
        每行一个；对象或数组请填写单行 JSON。
      </p>
      {error ? <p className="text-[10px] text-status-error mt-1">{error}</p> : null}
    </div>
  );
}
