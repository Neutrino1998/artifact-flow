"""
XML工具调用解析器
使用标准 xml.etree.ElementTree，支持 CDATA
"""

import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """工具调用数据结构"""
    name: str
    params: Dict[str, Any]
    raw_text: str = ""
    error: Optional[str] = None  # 解析失败时的错误信息
    # 解析时触发的兜底修复提示（祈使句，回传给模型在下一轮看到）。
    # 仅在 repair 实际改写了输入时登记；正常解析路径为空列表。
    warnings: List[str] = field(default_factory=list)
    # 模型在调用前写的一句意图（<reason> 兄弟标签）。display-only：透出到 CONFIRM
    # 审批弹窗 / TOOL_START 事件，**绝不**进 params、绝不进 execute()。best-effort：
    # 缺失或在 repair 路径丢失都不影响工具执行。
    reason: Optional[str] = None


class XMLToolCallParser:
    """XML工具调用解析器"""

    @staticmethod
    def parse_tool_calls(text: str) -> List[ToolCall]:
        """
        解析所有 tool_call 块

        Args:
            text: 包含工具调用的文本

        Returns:
            解析出的工具调用列表
        """
        results = []
        for inner, raw, is_trailing in XMLToolCallParser._split_tool_calls(text):
            tool_call = XMLToolCallParser._parse_single_block(inner, is_trailing=is_trailing)
            if tool_call:
                tool_call.raw_text = raw
                results.append(tool_call)
        return results

    # tool_call 开/闭标签大小写不敏感（沿用旧行为）；CDATA 定界符按 XML 规范大小写敏感。
    _OPEN_RE = re.compile(r'<tool_call>', re.IGNORECASE)
    _CLOSE_RE = re.compile(r'</tool_call>', re.IGNORECASE)
    _CDATA_OPEN = '<![CDATA['
    _CDATA_CLOSE = ']]>'

    @staticmethod
    def _split_tool_calls(text: str) -> List[tuple]:
        """CDATA-aware 拆分。返回 [(inner, raw, is_trailing), ...]。

        截断是输出流尾部属性 → 只可能命中最后一个块：带 CDATA 外 </tool_call> 终止符的块
        按定义完整；扫到 EOF 仍未终止的块是唯一 trailing 块（截断候选，由 _parse_single_block
        判定）。查找终止符时**跳过 CDATA 区** → 内容里的字面 </tool_call> 不再误拆（旧版
        re.findall 在此处会被字面量腰斩）。
        """
        out: List[tuple] = []
        pos = 0
        while True:
            m_open = XMLToolCallParser._OPEN_RE.search(text, pos)
            if not m_open:
                break
            block_start, inner_start = m_open.start(), m_open.end()

            # 从 inner_start 起扫描，跳过 CDATA 区，找 CDATA 外的 </tool_call>
            scan = inner_start
            close_m = None
            while scan < len(text):
                ci = text.find(XMLToolCallParser._CDATA_OPEN, scan)
                cm = XMLToolCallParser._CLOSE_RE.search(text, scan)
                cm_idx = cm.start() if cm else -1
                if ci != -1 and (cm_idx == -1 or ci < cm_idx):
                    # 先遇 CDATA 开始 → 跳到其 ]]> 之后；无 ]]> 则 CDATA 一直到 EOF（截断）
                    end = text.find(XMLToolCallParser._CDATA_CLOSE, ci + len(XMLToolCallParser._CDATA_OPEN))
                    if end == -1:
                        break
                    scan = end + len(XMLToolCallParser._CDATA_CLOSE)
                    continue
                close_m = cm  # 可能为 None（再无 CDATA 外的 </tool_call>）
                break

            if close_m is not None:
                out.append((text[inner_start:close_m.start()],
                            text[block_start:close_m.end()], False))
                pos = close_m.end()
            else:
                # 扫到 EOF 仍未终止 → trailing 块（最后一个），停止
                out.append((text[inner_start:], text[block_start:], True))
                break
        return out

    @staticmethod
    def _parse_single_block(content: str, is_trailing: bool = False) -> Optional[ToolCall]:
        """
        解析单个 tool_call 块。先前置剥离 <reason>，再把 reason-free 内容交给 _inner 走
        原有解析 / repair 链 —— 这样 reason **不可能**被 _repair_scattered_params 误判为孤立
        参数并入 <params>（否则会以 "Unknown parameter: reason" 把一次本可救回的调用打挂）。
        reason 是 display-only，best-effort 回贴到结果 ToolCall。
        """
        if not content.strip():
            return None

        reason, content = XMLToolCallParser._extract_reason(content)
        tool_call = XMLToolCallParser._parse_single_block_inner(content, is_trailing=is_trailing)
        if tool_call is not None and reason:
            tool_call.reason = reason
        return tool_call

    @staticmethod
    def _extract_reason(content: str) -> tuple:
        """抽走顶层 <reason>...</reason>（CDATA 或纯文本均可），返回 (reason, 去掉 reason 的 content)。

        非贪婪匹配到第一个 </reason>；reason 是人类可读的一句话意图，含字面 </reason> 的概率可忽略
        —— best-effort 契约下不为这种极端情形加机器。未命中返回 (None, 原文)。
        """
        m = re.search(r'<reason\s*>(.*?)</reason\s*>', content, re.DOTALL | re.IGNORECASE)
        if not m:
            return None, content
        raw = m.group(1).strip()
        cd = re.search(r'<!\[CDATA\[(.*?)\]\]>', raw, re.DOTALL)
        reason = (cd.group(1).strip() if cd else raw) or None
        content = content[:m.start()] + content[m.end():]
        return reason, content

    @staticmethod
    def _parse_single_block_inner(content: str, is_trailing: bool = False) -> Optional[ToolCall]:
        """
        解析单个 tool_call 块的 inner 内容（不含 <tool_call> 包裹、已剥离 <reason>）。

        返回 None 仅当 content 为空白。无法解析时返回带 error 的 ToolCall（engine 反馈给 agent）。
        触发 repair 兜底时 warnings 带上祈使句提示。

        is_trailing：本块是拆分层判定的尾部未终止块 —— **截断只可能发生在这里**。complete 块
        （有 CDATA 外的 </tool_call>）按定义完整，永不按截断处理。
        """
        # 空白内容 → 跳过
        if not content.strip():
            return None

        # 严格 XML 解析先行（complete 块 / 只漏 </tool_call> 但字段都全的 trailing 块都走这里）。
        # etree_result 留底：解析成功但 params 为空（如无参工具调用）时先试 repair 看有没有散落
        # 标签，repair 无果则回退到这个干净结果 —— 而不是误判 __malformed__。
        etree_result: Optional[ToolCall] = None
        try:
            etree_result = XMLToolCallParser._parse_with_etree(content)
            if etree_result and etree_result.params:
                return etree_result
        except ET.ParseError:
            pass

        # 方案1：trailing 块若是尾部截断（CDATA 未闭合 / 末尾字段未闭合）→ 报清晰截断错、不
        # salvage。残缺的 new_str 之类无法可靠应用；旧 lossy 兜底会丢字段误报 "Missing <field>"，
        # 诱导模型原样重试 → 再次截断。complete 块不在此列：其"漏闭合标签"是格式错而非截断，
        # 交给下面的 repair 链。
        if is_trailing and XMLToolCallParser._detect_truncation(content):
            return XMLToolCallParser._truncated_toolcall(content)

        # 渐进 repair（仅"完整但松散"的格式问题；截断已在上面短路）——每个 repair 实际改写输入时
        # 往 warnings 登记一条祈使句提示
        warnings: List[str] = []
        repaired = XMLToolCallParser._repair_tool_name_as_tag(content, warnings)
        repaired = XMLToolCallParser._repair_tag_equals_syntax(repaired, warnings)
        repaired = XMLToolCallParser._repair_unclosed_cdata_tags(repaired, warnings)
        # _repair_missing_closing_tags 要先于 _repair_scattered_params：
        # 否则只缺 </params> 时会被误判为 "params 散落"，触发不相干的 warning
        repaired = XMLToolCallParser._repair_missing_closing_tags(repaired, warnings)
        repaired = XMLToolCallParser._repair_scattered_params(repaired, warnings)

        if repaired != content:
            try:
                repaired_result = XMLToolCallParser._parse_with_etree(repaired)
                # repair 后只要解析成功就返回——**不**用 params 是否非空判定。否则"需要 repair
                # 才能解析、且结果无参"的合法调用（如 `<name=ping</name><params></params>`、
                # `<ping><params></params></ping>`，或参数全可选的 custom HTTP tool）会被误判
                # __malformed__。repair 已经跑过（含 _repair_scattered_params 收散落标签），无参
                # 即最终结果。
                if repaired_result is not None:
                    repaired_result.warnings = warnings
                    return repaired_result
            except ET.ParseError:
                pass

        # repair 没改动内容 / 重解析失败 → 回退到首次严格解析的干净结果（含无参工具调用，params={}）。
        if etree_result is not None:
            etree_result.warnings = warnings
            return etree_result

        # 诚实失败 → 返回 error ToolCall（不再 lossy 抠取捏造残缺参数；已废 _fallback_parse）。
        # 保证 engine 知道 agent 尝试了 tool call，而非静默忽略。
        return ToolCall(
            name="__malformed__",
            params={},
            error=(
                "Your tool call could not be parsed. Please use the correct format:\n"
                "<tool_call>\n"
                "<name>tool_name</name>\n"
                "<params>\n"
                "<param_name><![CDATA[value]]></param_name>\n"
                "</params>\n"
                "</tool_call>"
            ),
            warnings=warnings,
        )

    @staticmethod
    def _truncated_toolcall(content: str) -> ToolCall:
        """方案1 统一截断错误。带提取到的工具名，便于 observability 仍归到该工具名下。"""
        nm = re.search(r'<name>\s*(.*?)\s*</name>', content, re.DOTALL)
        return ToolCall(
            name=(nm.group(1).strip() if nm else "__truncated__"),
            params={},
            error=(
                "Output appears truncated or incomplete (likely max_tokens limit), so this "
                "tool call could not be reliably parsed. Reduce the output size: for edits "
                "replace a smaller snippet; for large rewrites split into multiple writes."
            ),
        )

    @staticmethod
    def _parse_with_etree(content: str) -> Optional[ToolCall]:
        """使用 ElementTree 解析"""
        # 包装成完整 XML
        xml_str = f"<root>{content}</root>"
        root = ET.fromstring(xml_str)

        # 提取 name
        name_elem = root.find('name')
        if name_elem is None:
            return None
        name = (name_elem.text or "").strip()
        if not name:
            return None

        # 提取 params
        params_elem = root.find('params')
        params = XMLToolCallParser._parse_element(params_elem) if params_elem is not None else {}

        return ToolCall(name=name, params=params)

    @staticmethod
    def _parse_element(elem: ET.Element) -> Dict[str, Any]:
        """递归解析 XML 元素为字典"""
        result = {}

        for child in elem:
            result[child.tag] = XMLToolCallParser._parse_value(child)

        return result

    @staticmethod
    def _parse_value(elem: ET.Element) -> str:
        """解析单个元素的值（保持原始字符串，类型转换由 BaseTool._coerce_params 处理）"""
        return (elem.text or "").strip()

    @staticmethod
    def _repair_tool_name_as_tag(content: str, warnings: List[str]) -> str:
        """
        修复工具名作为 XML 标签包裹 params 的格式

        LLM 有时会把工具名写成标签，例如：
            <web_fetch>
            <params>
                <url><![CDATA[...]]></url>
            </params>
        修复为：
            <name>web_fetch</name>
            <params>
                <url><![CDATA[...]]></url>
            </params>

        也处理有闭合标签的情况：<web_fetch>...</web_fetch>
        """
        # 已有 <name> 标签 → 无需修复
        if re.search(r'<name[\s>=]', content) or re.search(r'<name>', content):
            return content

        # 匹配首个标签（跳过空白）
        match = re.match(r'\s*<(\w+)>(.*)', content, re.DOTALL)
        if not match:
            return content

        tag_name = match.group(1)
        rest = match.group(2)

        # 如果首标签就是 params → 不是工具名，跳过
        if tag_name.lower() == 'params':
            return content

        # 移除对应的闭合标签（如果有）
        rest = re.sub(rf'</\s*{re.escape(tag_name)}\s*>\s*$', '', rest, flags=re.DOTALL)

        warnings.append(
            f"Wrote tool name as outer wrapping tag (e.g., <{tag_name}>...</{tag_name}>). "
            f"Correct form: <name>{tag_name}</name> with <params> as a sibling. "
            f"Do not wrap the call body with the tool name."
        )
        return f'<name>{tag_name}</name>\n{rest}'

    @staticmethod
    def _repair_tag_equals_syntax(content: str, warnings: List[str]) -> str:
        """
        修复 <tag=value</tag> → <tag>value</tag>

        小模型容易把 <tag>value</tag> 写成 <tag=value</tag>，例如：
            <name=call_subagent</name>
        修复为：
            <name>call_subagent</name>
        """
        new_content = re.sub(
            r'<(\w+)=([^<>]+)</\1>',
            r'<\1>\2</\1>',
            content,
        )
        if new_content != content:
            warnings.append(
                "Used '=' inside tag opening (e.g., <name=foo</name>). "
                "Correct form: <name>foo</name>. "
                "Never use '=' in tag openings — open with '>' and close with '</tag>'."
            )
        return new_content

    @staticmethod
    def _detect_truncation(content: str) -> bool:
        """检测尾部截断结构（供 trailing 块的方案1 报错判定，**纯检测、不改写内容**）。

        栈式扫描跳过 CDATA 区，识别两类尾部截断：
        - 案例 A：尾部 CDATA 未闭合（无 ]]>，max_tokens 切在 CDATA 中途）。
        - 案例 B/C：CDATA 都闭合，但末尾有未闭合字段标签，且该标签的 open 就是最后一个 tag
          事件（gate：区分"尾部截断" vs "mid-content 漏闭合但后面还有 sibling 标签"——后者是
          格式错而非截断，交 _repair_unclosed_cdata_tags 处理）。
        排除 params / tool_call（结构标签，由拆分层 / 其他 repair 负责）。

        注：方案1 下我们只判定"是否截断"，截断即报清晰错（不再 salvage 补标签），所以这里
        不需要原 _repair_truncated_cdata 的内容改写逻辑。
        """
        stack: List[tuple] = []  # 未闭合标签栈：(tag_name, open_start_pos)
        pos = 0
        cdata_open_at_eof = False
        last_tag_event_pos = -1  # 最后一次见到 tag-shape 内容的位置

        tag_re = re.compile(r'<(/?)(\w+)\s*>')

        while pos < len(content):
            cdata_idx = content.find('<![CDATA[', pos)
            tag_match = tag_re.search(content, pos)
            tag_idx = tag_match.start() if tag_match else -1

            # 选最早的事件
            if cdata_idx != -1 and (tag_idx == -1 or cdata_idx < tag_idx):
                cdata_end = content.find(']]>', cdata_idx + 9)
                if cdata_end == -1:
                    cdata_open_at_eof = True  # 案例 A
                    break
                pos = cdata_end + 3
                continue

            if tag_idx == -1:
                break

            last_tag_event_pos = tag_idx
            is_close = tag_match.group(1) == '/'
            tag_name = tag_match.group(2)
            if is_close:
                if stack and stack[-1][0] == tag_name:
                    stack.pop()
                # 不匹配的关闭标签忽略
            else:
                stack.append((tag_name, tag_idx))
            pos = tag_match.end()

        if cdata_open_at_eof:
            return True

        # 案例 B/C：末尾未闭合字段标签 == 最后一个 tag 事件
        field_stack = [(n, p) for n, p in stack if n.lower() not in ('params', 'tool_call')]
        if field_stack:
            _, topmost_pos = field_stack[-1]
            if topmost_pos == last_tag_event_pos:
                return True

        return False

    @staticmethod
    def _repair_unclosed_cdata_tags(content: str, warnings: List[str]) -> str:
        """
        修复 CDATA 后缺失闭合标签的问题

        LLM 生成长内容时容易漏掉 CDATA 之后的闭合标签，例如：
            <content><![CDATA[...long text...]]>
            </params>
        修复为：
            <content><![CDATA[...long text...]]></content>
            </params>
        """
        # 匹配 <tag><![CDATA[...]]> 后面不是 </tag> 的情况
        def _repair_match(m):
            tag = m.group(1)
            cdata = m.group(2)
            after = m.group(3)
            return f'<{tag}><![CDATA[{cdata}]]></{tag}>{after}'

        # CDATA 内容用 (?:(?!\]\]>).)*+ 匹配：不跨越 ]]> 边界，且 *+（possessive，
        # Python 3.11+）禁止回溯进组——内容里不可能含 ]]>，回溯只会在未闭合 CDATA 上
        # 线性反复试 \]\]> 白耗 CPU（O(n²) 起步）。目前未闭合 CDATA 被上游
        # _detect_truncation 短路、走不到这里，但安全性不应依赖调用顺序这个隐式前提。
        new_content = re.sub(
            r'<(\w+)>\s*<!\[CDATA\[((?:(?!\]\]>).)*+)\]\]>(?!\s*</\1>)(\s*<[/\w])',
            _repair_match,
            content,
            flags=re.DOTALL,
        )
        if new_content != content:
            warnings.append(
                "Wrote <tag><![CDATA[...]]> without the matching </tag> before the next sibling tag. "
                "Always close the field tag immediately after ']]>' (e.g., <content><![CDATA[...]]></content>). "
                "Do not let CDATA blocks bleed into the next param."
            )
        return new_content

    @staticmethod
    def _mask_cdata(content: str) -> str:
        """返回与 content **等长**的串，每段 <![CDATA[...]]>（含定界符）替换为等量占位字符。

        占位字符（私有区 \\uE000）不含 < > / → 结构正则在 masked 串上跑，不会被 CDATA **内容里
        的字面标签**（</tool_call> / </params> / <div> 等）骗；长度不变，故 masked 上 match 的
        span 可直接切回 content 取真实文本。只遮蔽**已闭合**的 CDATA（未闭合 = 截断，已由
        _detect_truncation 在更早处短路，走不到这些 repair）。
        """
        return re.sub(
            r'<!\[CDATA\[.*?\]\]>',
            lambda m: '' * len(m.group(0)),
            content,
            flags=re.DOTALL,
        )

    @staticmethod
    def _repair_scattered_params(content: str, warnings: List[str]) -> str:
        """
        修复参数散落在多处的问题：
        1. 多个 <params> 块（如第一个是垃圾纯文本、第二个才有结构化内容）
        2. 参数标签出现在 <params> 外面（作为 <name> 的兄弟标签）

        例如：
            <name>create_artifact</name>
            <params><![CDATA[content]]></params>
            <content_type><![CDATA[text/markdown]]></content_type>
            <id><![CDATA[xxx]]></id>
            <params>
              <content><![CDATA[...]]></content>
              <title><![CDATA[...]]></title>
            </params>
        修复为：单个 <params> 内合并所有参数。

        CDATA-aware：所有结构判定都在遮蔽 CDATA 的 masked 串上做（span 与 content 1:1），真实
        文本按 span 从 content 切出。避免内容里的字面 <词> / </params> 触发误重组、把本来没问题
        的调用搅乱。
        """
        masked = XMLToolCallParser._mask_cdata(content)

        # span 取自 masked（CDATA 内字面标签已遮蔽）；(块起, 块止, inner 起, inner 止)
        params_spans = [(m.start(), m.end(), m.start(1), m.end(1))
                        for m in re.finditer(r'<params\s*>(.*?)</params\s*>', masked, re.DOTALL)]
        name_m = re.search(r'<name[^>]*>.*?</name>', masked, re.DOTALL)
        if not name_m:
            return content

        # 在 masked 上把 name + 所有 params 块按区间置空（保持位置），检测**真实**孤立标签
        masked_chars = list(masked)
        for s, e in [(name_m.start(), name_m.end())] + [(s, e) for s, e, _, _ in params_spans]:
            for i in range(s, e):
                masked_chars[i] = ''
        masked_remainder = ''.join(masked_chars)
        has_orphans = bool(re.search(r'<\w+\s*>', masked_remainder))

        # 只有一个 params 块且无孤立标签 → 无需修复
        if len(params_spans) <= 1 and not has_orphans:
            return content

        all_children = []
        # 孤立标签（放前面，后面 params 块同名 key 覆盖）；span 取自 masked，文本切自 content
        if has_orphans:
            for m in re.finditer(r'<(\w+)>.*?</\1>', masked_remainder, re.DOTALL):
                all_children.append(content[m.start():m.end()].strip())
        # 有子元素的 params 块（跳过纯文本/CDATA 的垃圾块——masked 后 inner 无 <词> 即垃圾块）
        for _, _, cs, ce in params_spans:
            if re.search(r'<\w+\s*>', masked[cs:ce]):
                all_children.append(content[cs:ce].strip())

        if not all_children:
            return content

        merged = '\n'.join(all_children)
        warnings.append(
            "Parameter tags appeared outside <params> or in multiple <params> blocks. "
            "Wrap ALL parameters in a single <params>...</params> block. "
            "Do not duplicate <params> and do not place param tags as siblings of <name>."
        )
        return f"{content[name_m.start():name_m.end()]}\n<params>\n{merged}\n</params>"

    @staticmethod
    def _repair_missing_closing_tags(content: str, warnings: List[str]) -> str:
        """
        修复缺失的结构性闭合标签（漏写 </params>）。

        LLM 有时会忘记写 </params>，例如：
            <name>create_artifact</name>
            <params>
                <id><![CDATA[task_plan]]></id>
                <content><![CDATA[...]]></content>
            （缺少 </params>）

        CDATA-aware：在遮蔽 CDATA 的 masked 串上判定 </params> 是否真缺，避免被内容里的字面
        </params> 骗（否则漏补真闭合标签 → etree 失败 → 旧 lossy fallback 丢字段误报 Missing）。
        """
        masked = XMLToolCallParser._mask_cdata(content)
        if re.search(r'<params\s*>', masked) and not re.search(r'</params\s*>', masked):
            content = content.rstrip() + '\n</params>'
            warnings.append(
                "Missing </params> closing tag. "
                "Always close <params> explicitly before </tool_call>."
            )

        return content


# 便捷函数（保持向后兼容）
def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析工具调用"""
    return XMLToolCallParser.parse_tool_calls(text)


if __name__ == "__main__":
    # 测试用例
    test_cases = [
        ("标准CDATA格式", """
<tool_call>
    <name>web_search</name>
    <params>
        <query><![CDATA[python async tutorial]]></query>
        <max_results><![CDATA[10]]></max_results>
    </params>
</tool_call>
"""),

        ("包含代码内容（CDATA）", """
<tool_call>
    <name>create_artifact</name>
    <params>
        <id><![CDATA[code_sample]]></id>
        <content><![CDATA[
def hello():
    if x < 10 and y > 5:
        print("Hello & World")
]]></content>
    </params>
</tool_call>
"""),

        ("旧格式（无CDATA，fallback）", """
<tool_call>
    <name>search</name>
    <params>
        <query>simple query</query>
        <count>5</count>
    </params>
</tool_call>
"""),

        ("标签等号语法 <name=value</name>", """
<tool_call>
<name=call_subagent</name>
<params>
<agent_name><![CDATA[research_agent]]></agent_name>
<instruction><![CDATA[research topic X across multiple sources]]></instruction>
  </params>
</tool_call>
"""),

        ("缺失</params>闭合标签", """
<tool_call>
    <name>create_artifact</name>
    <params>
        <id><![CDATA[task_plan]]></id>
        <content_type><![CDATA[text/markdown]]></content_type>
        <title><![CDATA[金融AI新闻稿件撰写任务计划]]></title>
        <content><![CDATA[# Task: 金融AI新闻稿件撰写

## 任务目标
撰写本月金融领域AI科技新闻稿件
]]></content>
</tool_call>
"""),

        ("重复params块+孤立参数+name等号语法", """
<tool_call>
<name=create_artifact</name>
<params><![CDATA[content]]></params>
<content_type><![CDATA[text/markdown]]></content_type>
<id><![CDATA[总结报告-研究背景与范围]]></id>
<params>
  <content><![CDATA[# 总结报告

## 一、研究背景
当今时代正经历前所未有的知识爆炸。]]></content>
  <id><![CDATA[总结报告 - 研究背景与范围]]></id>
  <title><![CDATA[总结报告 - 研究背景与范围]]></title>
</params>
</tool_call>
"""),

        ("孤立参数无params包裹", """
<tool_call>
<name>web_search</name>
<query><![CDATA[人工智能研究报告]]></query>
<max_results><![CDATA[5]]></max_results>
</tool_call>
"""),

        ("缺失</tool_call>闭合+name等号语法+孤立参数", """
<tool_call>
<name=create_artifact</name>
<params><![CDATA[content]]></params>
<content_type>text/markdown</content_type>
<id><![CDATA[总结报告 - 研究背景与范围]]></id>
<title><![CDATA[总结报告 - 研究背景与范围]]></title>
</params>
"""),

        ("工具名作为标签（无闭合）", """
<tool_call>
<web_fetch>
<params>
<url><![CDATA[https://k.sina.com.cn/article_7879922977_1d5ae152101901bba2.html]]></url>
<max_content_length><![CDATA[20000]]></max_content_length>
</params>
</tool_call>
"""),

        ("工具名作为标签（有闭合）", """
<tool_call>
<web_search>
<params>
<query><![CDATA[AI research 2024]]></query>
<max_results><![CDATA[5]]></max_results>
</params>
</web_search>
</tool_call>
"""),

        ("完全不可解析的 tool_call 块", """
<tool_call>
some random garbage that is not xml at all
</tool_call>
"""),
    ]

    print("=" * 70)
    print("XML解析器测试（CDATA版本）")
    print("=" * 70)

    for desc, test in test_cases:
        print(f"\n{'=' * 60}")
        print(f"测试: {desc}")
        print(f"{'=' * 60}")

        results = parse_tool_calls(test)
        for result in results:
            print(f"工具名: {result.name}")
            if result.error:
                print(f"错误: {result.error}")
            else:
                print(f"参数:")
                for k, v in result.params.items():
                    print(f"  {k}: {repr(v)}")
