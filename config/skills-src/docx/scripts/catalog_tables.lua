-- Emit a compact table catalog while Pandoc already has the document AST.
-- This deliberately avoids serializing the full AST for a second parser.

local catalog_path = os.getenv('ARTIFACTFLOW_DOCX_TABLE_CATALOG')
local max_tables = tonumber(os.getenv('ARTIFACTFLOW_DOCX_MAX_TABLES')) or 500
local max_text_codepoints = 1000

local headings = {}
local flow_order = 0
local table_count = 0
local output = nil

local function normalize_text(value)
  if value == nil then
    return ''
  end
  local text = pandoc.utils.stringify(value):gsub('%s+', ' ')
  text = text:match('^%s*(.-)%s*$') or ''
  local boundary = utf8.offset(text, max_text_codepoints + 1)
  if boundary ~= nil then
    text = text:sub(1, boundary - 1) .. '…'
  end
  return text
end

local escapes = {
  ['\\'] = '\\\\',
  ['"'] = '\\"',
  ['\b'] = '\\b',
  ['\f'] = '\\f',
  ['\n'] = '\\n',
  ['\r'] = '\\r',
  ['\t'] = '\\t',
}

local function json_string(value)
  local escaped = value:gsub('[\\"\b\f\n\r\t]', escapes)
  escaped = escaped:gsub('[%z\1-\31]', function(char)
    return string.format('\\u%04x', string.byte(char))
  end)
  return '"' .. escaped .. '"'
end

local function json_nullable_string(value)
  if value == nil or value == '' then
    return 'null'
  end
  return json_string(value)
end

local function json_string_array(values)
  local encoded = {}
  for _, value in ipairs(values) do
    encoded[#encoded + 1] = json_string(value)
  end
  return '[' .. table.concat(encoded, ',') .. ']'
end

local function table_caption(block)
  if block.caption == nil then
    return ''
  end
  if block.caption.long ~= nil and #block.caption.long > 0 then
    return normalize_text(block.caption.long)
  end
  if block.caption.short ~= nil then
    return normalize_text(block.caption.short)
  end
  return ''
end

local function emit_table(block)
  table_count = table_count + 1
  if table_count > max_tables then
    error('table limit is ' .. tostring(max_tables))
  end

  local heading_path = {}
  for level = 1, 9 do
    if headings[level] ~= nil then
      heading_path[#heading_path + 1] = headings[level]
    end
  end
  local line = '{' ..
    '"source_id":' .. json_nullable_string(normalize_text(block.identifier)) .. ',' ..
    '"label":' .. json_nullable_string(table_caption(block)) .. ',' ..
    '"order":' .. tostring(flow_order) .. ',' ..
    '"heading_path":' .. json_string_array(heading_path) ..
    '}\n'
  local ok, write_error = output:write(line)
  if not ok then
    error('cannot write table catalog: ' .. tostring(write_error))
  end
end

local function process_blocks(blocks)
  for _, block in ipairs(blocks) do
    local tag = block.tag or block.t
    if tag == 'Div' or tag == 'BlockQuote' then
      process_blocks(block.content)
    elseif tag == 'BulletList' or tag == 'OrderedList' then
      for _, item in ipairs(block.content) do
        process_blocks(item)
      end
    elseif tag == 'DefinitionList' then
      for _, entry in ipairs(block.content) do
        for _, definition in ipairs(entry[2]) do
          process_blocks(definition)
        end
      end
    else
      flow_order = flow_order + 1
      if tag == 'Header' then
        local level = math.max(1, math.min(tonumber(block.level) or 1, 9))
        local title = normalize_text(block.content)
        if title ~= '' then
          headings[level] = title
          for deeper = level + 1, 9 do
            headings[deeper] = nil
          end
        end
      elseif tag == 'Table' then
        emit_table(block)
      end
    end
  end
end

function Pandoc(document)
  if catalog_path == nil or catalog_path == '' then
    error('ARTIFACTFLOW_DOCX_TABLE_CATALOG is required')
  end
  local open_error
  output, open_error = io.open(catalog_path, 'w')
  if output == nil then
    error('cannot open table catalog: ' .. tostring(open_error))
  end
  process_blocks(document.blocks)
  local closed, close_error = output:close()
  if not closed then
    error('cannot close table catalog: ' .. tostring(close_error))
  end
end
