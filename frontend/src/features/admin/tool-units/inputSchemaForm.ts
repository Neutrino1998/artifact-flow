export const SIMPLE_PARAMETER_TYPES = [
  'string',
  'integer',
  'number',
  'boolean',
  'object',
  'array',
] as const;

export type SimpleParameterType = typeof SIMPLE_PARAMETER_TYPES[number];
export type SimpleArrayItemType = 'any' | Exclude<SimpleParameterType, 'array'>;

type JsonObject = Record<string, unknown>;

export interface SimpleSchemaParameter {
  name: string;
  type: SimpleParameterType;
  itemType: SimpleArrayItemType;
  description: string;
  required: boolean;
  hasDefault: boolean;
  defaultValue: unknown;
  enumValues: unknown[] | null;
}

export type InputSchemaInspection =
  | {
      kind: 'simple';
      schema: JsonObject;
      parameters: SimpleSchemaParameter[];
      rejectUnknown: boolean;
    }
  | { kind: 'advanced'; reason: string }
  | { kind: 'invalid'; reason: string };

const SIMPLE_ROOT_KEYS = new Set([
  'type',
  'properties',
  'required',
  'additionalProperties',
]);
const SIMPLE_PROPERTY_KEYS = new Set([
  'type',
  'description',
  'default',
  'enum',
  'items',
]);
const SIMPLE_ITEM_KEYS = new Set(['type']);
const CONTROL_CHARACTER_RE = /[\u0000-\u001f\u007f]/u;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasOwn(object: object, key: PropertyKey): boolean {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function isSimpleParameterType(value: unknown): value is SimpleParameterType {
  return typeof value === 'string'
    && SIMPLE_PARAMETER_TYPES.includes(value as SimpleParameterType);
}

function simpleParameterNameError(name: string): string | null {
  if (!name) return '参数名不能为空';
  if (name.trim() !== name) return '参数名首尾不能包含空白字符';
  if (CONTROL_CHARACTER_RE.test(name)) return '参数名不能包含换行或控制字符';
  return null;
}

function isTextareaRoundTripSafe(value: string): boolean {
  return !value.includes('\r');
}

function isLineEnumRoundTripSafe(value: string): boolean {
  return value !== '' && !/[\r\n]/u.test(value);
}

function jsonValuesEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => jsonValuesEqual(value, right[index]));
  }
  if (!isObject(left) || !isObject(right)) return false;
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key) => (
      hasOwn(right, key)
      && jsonValuesEqual(left[key], right[key])
    ));
}

function enumContains(enumValues: unknown[], value: unknown): boolean {
  return enumValues.some((candidate) => jsonValuesEqual(candidate, value));
}

function valueMatchesType(
  value: unknown,
  type: SimpleParameterType | Exclude<SimpleParameterType, 'array'>,
): boolean {
  switch (type) {
    case 'string':
      return typeof value === 'string';
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value);
    case 'number':
      return typeof value === 'number' && Number.isFinite(value);
    case 'boolean':
      return typeof value === 'boolean';
    case 'object':
      return isObject(value);
    case 'array':
      return Array.isArray(value);
  }
}

function valueMatchesParameter(value: unknown, parameter: JsonObject): boolean {
  const type = parameter.type;
  if (!isSimpleParameterType(type) || !valueMatchesType(value, type)) return false;
  if (type !== 'array' || !Array.isArray(value)) return true;
  const items = parameter.items;
  if (items === undefined) return true;
  if (!isObject(items) || !isSimpleParameterType(items.type) || items.type === 'array') {
    return false;
  }
  return value.every((item) => valueMatchesType(item, items.type as Exclude<SimpleParameterType, 'array'>));
}

export function inspectInputSchema(schemaText: string): InputSchemaInspection {
  let parsed: unknown;
  try {
    parsed = JSON.parse(schemaText);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { kind: 'invalid', reason: `JSON 解析失败：${detail}` };
  }
  if (!isObject(parsed)) {
    return { kind: 'invalid', reason: 'Schema 必须是 JSON 对象' };
  }
  if (parsed.type !== 'object') {
    return { kind: 'invalid', reason: 'Schema 根 type 必须是 object' };
  }

  const rootAdvanced = Object.keys(parsed).find((key) => !SIMPLE_ROOT_KEYS.has(key));
  if (rootAdvanced) {
    return { kind: 'advanced', reason: `根级字段 ${rootAdvanced} 需要在高级 JSON Schema 中编辑` };
  }
  if (
    parsed.additionalProperties !== undefined
    && typeof parsed.additionalProperties !== 'boolean'
  ) {
    return { kind: 'advanced', reason: '对象形式的 additionalProperties 需要在高级 JSON Schema 中编辑' };
  }

  const properties = parsed.properties ?? {};
  if (!isObject(properties)) {
    return { kind: 'invalid', reason: 'Schema properties 必须是对象' };
  }
  const required = parsed.required ?? [];
  if (!Array.isArray(required) || required.some((name) => typeof name !== 'string')) {
    return { kind: 'invalid', reason: 'Schema required 必须是字符串数组' };
  }
  if (new Set(required).size !== required.length) {
    return { kind: 'invalid', reason: 'Schema required 不能包含重复参数名' };
  }
  const unknownRequiredIndex = required.findIndex((name) => !hasOwn(properties, name));
  if (unknownRequiredIndex >= 0) {
    const unknownRequired = required[unknownRequiredIndex];
    return {
      kind: 'advanced',
      reason: `required 中的 ${JSON.stringify(unknownRequired)} 未在 properties 声明，无法用参数配置无损表达`,
    };
  }

  const parameters: SimpleSchemaParameter[] = [];
  for (const [name, rawProperty] of Object.entries(properties)) {
    const nameError = simpleParameterNameError(name);
    if (nameError) {
      return {
        kind: 'advanced',
        reason: `${nameError}；参数 ${JSON.stringify(name)} 需要在高级 JSON Schema 中编辑`,
      };
    }
    if (hasOwn(Object.prototype, name)) {
      return { kind: 'advanced', reason: `参数名 ${name} 需要在高级 JSON Schema 中编辑` };
    }
    if (!isObject(rawProperty)) {
      return { kind: 'advanced', reason: `参数 ${name} 的 Schema 不是普通对象` };
    }
    const advancedKey = Object.keys(rawProperty).find((key) => !SIMPLE_PROPERTY_KEYS.has(key));
    if (advancedKey) {
      return { kind: 'advanced', reason: `参数 ${name} 使用了高级字段 ${advancedKey}` };
    }
    if (!isSimpleParameterType(rawProperty.type)) {
      return { kind: 'advanced', reason: `参数 ${name} 的 type 需要在高级 JSON Schema 中编辑` };
    }
    if (
      rawProperty.description !== undefined
      && typeof rawProperty.description !== 'string'
    ) {
      return { kind: 'invalid', reason: `参数 ${name} 的 description 必须是字符串` };
    }
    if (
      typeof rawProperty.description === 'string'
      && !isTextareaRoundTripSafe(rawProperty.description)
    ) {
      return { kind: 'advanced', reason: `参数 ${name} 的 description 含有 CR 换行，需要在高级 JSON Schema 中编辑` };
    }

    let itemType: SimpleArrayItemType = 'any';
    if (rawProperty.type === 'array' && rawProperty.items !== undefined) {
      if (!isObject(rawProperty.items)) {
        return { kind: 'advanced', reason: `参数 ${name} 的数组元素约束需要在高级 JSON Schema 中编辑` };
      }
      const itemAdvancedKey = Object.keys(rawProperty.items).find(
        (key) => !SIMPLE_ITEM_KEYS.has(key),
      );
      if (
        itemAdvancedKey
        || !isSimpleParameterType(rawProperty.items.type)
        || rawProperty.items.type === 'array'
      ) {
        return { kind: 'advanced', reason: `参数 ${name} 的数组元素约束需要在高级 JSON Schema 中编辑` };
      }
      itemType = rawProperty.items.type;
    } else if (rawProperty.type !== 'array' && rawProperty.items !== undefined) {
      return { kind: 'invalid', reason: `非数组参数 ${name} 不能声明 items` };
    }

    const hasDefault = hasOwn(rawProperty, 'default');
    if (hasDefault && !valueMatchesParameter(rawProperty.default, rawProperty)) {
      return { kind: 'invalid', reason: `参数 ${name} 的默认值与类型不匹配` };
    }
    if (
      hasDefault
      && rawProperty.type === 'string'
      && typeof rawProperty.default === 'string'
      && !isTextareaRoundTripSafe(rawProperty.default)
    ) {
      return { kind: 'advanced', reason: `参数 ${name} 的字符串默认值含有 CR 换行，需要在高级 JSON Schema 中编辑` };
    }
    if (rawProperty.enum !== undefined && !Array.isArray(rawProperty.enum)) {
      return { kind: 'invalid', reason: `参数 ${name} 的 enum 必须是数组` };
    }
    if (Array.isArray(rawProperty.enum) && rawProperty.enum.length === 0) {
      return { kind: 'invalid', reason: `参数 ${name} 的 enum 至少需要一个值` };
    }
    if (
      Array.isArray(rawProperty.enum)
      && rawProperty.enum.some((value) => !valueMatchesParameter(value, rawProperty))
    ) {
      return { kind: 'advanced', reason: `参数 ${name} 的 enum 含有与当前类型不同的值` };
    }
    if (
      rawProperty.type === 'string'
      && Array.isArray(rawProperty.enum)
      && rawProperty.enum.some((value) => (
        typeof value === 'string' && !isLineEnumRoundTripSafe(value)
      ))
    ) {
      return { kind: 'advanced', reason: `参数 ${name} 的空字符串或含换行 enum 需要在高级 JSON Schema 中编辑` };
    }
    if (
      hasDefault
      && Array.isArray(rawProperty.enum)
      && !enumContains(rawProperty.enum, rawProperty.default)
    ) {
      return { kind: 'invalid', reason: `参数 ${name} 的默认值必须是可选值之一` };
    }

    parameters.push({
      name,
      type: rawProperty.type,
      itemType,
      description: typeof rawProperty.description === 'string' ? rawProperty.description : '',
      required: required.includes(name),
      hasDefault,
      defaultValue: rawProperty.default,
      enumValues: Array.isArray(rawProperty.enum) ? rawProperty.enum : null,
    });
  }

  return {
    kind: 'simple',
    schema: parsed,
    parameters,
    rejectUnknown: parsed.additionalProperties === false,
  };
}

function mutateSimpleSchema(
  schemaText: string,
  mutate: (schema: JsonObject, properties: JsonObject, required: string[]) => void,
): string {
  const inspected = inspectInputSchema(schemaText);
  if (inspected.kind !== 'simple') {
    throw new Error(inspected.reason);
  }
  const schema = inspected.schema;
  const properties = (schema.properties ?? {}) as JsonObject;
  const required = [...((schema.required ?? []) as string[])];
  schema.properties = properties;
  schema.required = required;
  mutate(schema, properties, required);
  if (required.length === 0) delete schema.required;
  const nextSchemaText = JSON.stringify(schema, null, 2);
  const nextInspection = inspectInputSchema(nextSchemaText);
  if (nextInspection.kind !== 'simple') {
    throw new Error(nextInspection.reason);
  }
  return nextSchemaText;
}

function propertyFor(properties: JsonObject, name: string): JsonObject {
  if (!hasOwn(properties, name)) throw new Error(`参数 ${name} 不存在`);
  const property = properties[name];
  if (!isObject(property)) throw new Error(`参数 ${name} 不存在`);
  return property;
}

export function addSimpleParameter(schemaText: string): string {
  return mutateSimpleSchema(schemaText, (_schema, properties) => {
    let index = Object.keys(properties).length + 1;
    let name = `param_${index}`;
    while (hasOwn(properties, name)) {
      index += 1;
      name = `param_${index}`;
    }
    properties[name] = { type: 'string' };
  });
}

export function removeSimpleParameter(schemaText: string, name: string): string {
  return mutateSimpleSchema(schemaText, (_schema, properties, required) => {
    delete properties[name];
    const index = required.indexOf(name);
    if (index >= 0) required.splice(index, 1);
  });
}

export function renameSimpleParameter(
  schemaText: string,
  oldName: string,
  nextName: string,
): string {
  const nameError = simpleParameterNameError(nextName);
  if (nameError) throw new Error(nameError);
  return mutateSimpleSchema(schemaText, (_schema, properties, required) => {
    if (nextName !== oldName && hasOwn(properties, nextName)) {
      throw new Error(`参数名 ${nextName} 已存在`);
    }
    const entries = Object.entries(properties);
    for (const key of Object.keys(properties)) delete properties[key];
    for (const [name, property] of entries) {
      properties[name === oldName ? nextName : name] = property;
    }
    const requiredIndex = required.indexOf(oldName);
    if (requiredIndex >= 0) required[requiredIndex] = nextName;
  });
}

export function setSimpleParameterRequired(
  schemaText: string,
  name: string,
  isRequired: boolean,
): string {
  return mutateSimpleSchema(schemaText, (_schema, _properties, required) => {
    const index = required.indexOf(name);
    if (isRequired && index < 0) required.push(name);
    if (!isRequired && index >= 0) required.splice(index, 1);
  });
}

export function setSimpleParameterDescription(
  schemaText: string,
  name: string,
  description: string,
): string {
  return mutateSimpleSchema(schemaText, (_schema, properties) => {
    const property = propertyFor(properties, name);
    if (description) property.description = description;
    else delete property.description;
  });
}

export function setSimpleParameterType(
  schemaText: string,
  name: string,
  type: SimpleParameterType,
): string {
  return mutateSimpleSchema(schemaText, (_schema, properties) => {
    const property = propertyFor(properties, name);
    property.type = type;
    if (type === 'array') property.items = { type: 'string' };
    else delete property.items;
    if (
      hasOwn(property, 'default')
      && !valueMatchesParameter(property.default, property)
    ) {
      delete property.default;
    }
    if (Array.isArray(property.enum)) {
      const enumValues = property.enum.filter((value) => valueMatchesParameter(value, property));
      if (enumValues.length === 0) delete property.enum;
      else property.enum = enumValues;
    }
  });
}

export function setSimpleArrayItemType(
  schemaText: string,
  name: string,
  itemType: SimpleArrayItemType,
): string {
  return mutateSimpleSchema(schemaText, (_schema, properties) => {
    const property = propertyFor(properties, name);
    if (property.type !== 'array') throw new Error(`参数 ${name} 不是数组`);
    if (itemType === 'any') delete property.items;
    else property.items = { type: itemType };
    if (
      hasOwn(property, 'default')
      && !valueMatchesParameter(property.default, property)
    ) {
      delete property.default;
    }
    if (Array.isArray(property.enum)) {
      const enumValues = property.enum.filter((value) => valueMatchesParameter(value, property));
      if (enumValues.length === 0) delete property.enum;
      else property.enum = enumValues;
    }
  });
}

export function setSimpleParameterDefault(
  schemaText: string,
  name: string,
  present: boolean,
  value?: unknown,
): string {
  return mutateSimpleSchema(schemaText, (_schema, properties) => {
    const property = propertyFor(properties, name);
    if (!present) {
      delete property.default;
      return;
    }
    if (!valueMatchesParameter(value, property)) {
      throw new Error('默认值与参数类型不匹配');
    }
    if (Array.isArray(property.enum) && !enumContains(property.enum, value)) {
      throw new Error('默认值必须是可选值之一');
    }
    property.default = value;
  });
}

export function setSimpleParameterEnum(
  schemaText: string,
  name: string,
  values: unknown[] | null,
): string {
  return mutateSimpleSchema(schemaText, (_schema, properties) => {
    const property = propertyFor(properties, name);
    if (values === null) {
      delete property.enum;
      return;
    }
    if (values.length === 0) throw new Error('可选值至少需要一项');
    if (values.some((value) => !valueMatchesParameter(value, property))) {
      throw new Error('可选值与参数类型不匹配');
    }
    if (
      hasOwn(property, 'default')
      && !enumContains(values, property.default)
    ) {
      throw new Error('可选值必须包含当前默认值');
    }
    property.enum = values;
  });
}

export function setRejectUnknownParameters(schemaText: string, reject: boolean): string {
  return mutateSimpleSchema(schemaText, (schema) => {
    schema.additionalProperties = !reject;
  });
}

export function defaultValueForType(type: SimpleParameterType): unknown {
  switch (type) {
    case 'string':
      return '';
    case 'integer':
    case 'number':
      return 0;
    case 'boolean':
      return false;
    case 'object':
      return {};
    case 'array':
      return [];
  }
}

export function typedValueToText(value: unknown, type: SimpleParameterType): string {
  if (type === 'string') return typeof value === 'string' ? value : '';
  if (type === 'boolean') return value === true ? 'true' : 'false';
  if (type === 'integer' || type === 'number') return typeof value === 'number' ? String(value) : '';
  return JSON.stringify(value);
}

export function parseTypedValue(text: string, type: SimpleParameterType): unknown {
  if (type === 'string') return text;
  if (type === 'boolean') {
    if (text === 'true') return true;
    if (text === 'false') return false;
    throw new Error('布尔值必须是 true 或 false');
  }
  if (type === 'integer' || type === 'number') {
    if (!text.trim()) throw new Error('数值不能为空');
    const value = Number(text);
    if (!Number.isFinite(value) || (type === 'integer' && !Number.isInteger(value))) {
      throw new Error(type === 'integer' ? '请输入整数' : '请输入有效数字');
    }
    return value;
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(type === 'array' ? '请输入合法 JSON 数组' : '请输入合法 JSON 对象');
  }
  if (!valueMatchesType(value, type)) {
    throw new Error(type === 'array' ? '请输入 JSON 数组' : '请输入 JSON 对象');
  }
  return value;
}

export function inferHttpParameterLocation(
  endpoint: string,
  method: string,
  parameterName: string,
): 'URL Path' | 'JSON Body' | 'Query String' {
  if (endpoint.includes(`{${parameterName}}`)) return 'URL Path';
  return ['POST', 'PUT', 'PATCH'].includes(method.toUpperCase())
    ? 'JSON Body'
    : 'Query String';
}

export function formatInputSchemaJson(schemaText: string): string {
  const parsed: unknown = JSON.parse(schemaText);
  if (!isObject(parsed)) throw new Error('Schema 必须是 JSON 对象');
  return JSON.stringify(parsed, null, 2);
}
