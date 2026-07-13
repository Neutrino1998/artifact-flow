"""
XML工具调用解析器
使用标准 xml.etree.ElementTree，支持 CDATA
"""

import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Any, Optional, Iterator
from dataclasses import dataclass, field

from tools.xml_protocol import STRUCTURAL_TAGS, TOOL_CALL_EXAMPLE


class ToolCallProtocolError(ValueError):
    """XML is well-formed but violates the tool-call grammar."""


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
    _NAME_RE = re.compile(r'<name>\s*(.*?)\s*</name>', re.DOTALL)
    _CDATA_OPEN = '<![CDATA['
    _CDATA_CLOSE = ']]>'

    @staticmethod
    def _iter_cdata_regions(text: str, start: int = 0) -> Iterator[tuple[bool, int, int, bool]]:
        """Yield ``(is_cdata, start, end, is_complete)`` regions in one forward pass.

        Regions cover ``text[start:]`` without overlap. Once a CDATA opener is found, only
        its first following ``]]>`` can close it; nested opener-shaped text is literal CDATA
        content. Every search therefore starts after the previous consumed region, keeping
        all CDATA-aware parser paths linear in the response length.
        """
        limit = len(text)
        pos = start
        open_len = len(XMLToolCallParser._CDATA_OPEN)
        close_len = len(XMLToolCallParser._CDATA_CLOSE)

        while pos < limit:
            cdata_start = text.find(XMLToolCallParser._CDATA_OPEN, pos)
            if cdata_start == -1:
                yield False, pos, limit, True
                return

            if cdata_start > pos:
                yield False, pos, cdata_start, True

            close_start = text.find(
                XMLToolCallParser._CDATA_CLOSE,
                cdata_start + open_len,
            )
            if close_start == -1:
                yield True, cdata_start, limit, False
                return

            cdata_end = close_start + close_len
            yield True, cdata_start, cdata_end, True
            pos = cdata_end

    @staticmethod
    def _split_tool_calls(text: str) -> List[tuple]:
        """CDATA-aware 拆分。返回 [(inner, raw, is_trailing), ...]。

        尾部不完整是输出流属性 → 只可能命中最后一个块：带 CDATA 外 </tool_call> 终止符的块
        按定义完整；扫到 EOF 仍未终止的块是唯一 trailing 候选，由 _parse_single_block
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

            # 在不重叠的 CDATA 外区间各搜索一次 </tool_call>。
            close_m = None
            for is_cdata, region_start, region_end, is_complete in (
                XMLToolCallParser._iter_cdata_regions(text, inner_start)
            ):
                if is_cdata:
                    if not is_complete:
                        break
                    continue

                close_m = XMLToolCallParser._CLOSE_RE.search(
                    text,
                    region_start,
                    region_end,
                )
                if close_m is not None:
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
        解析单个 tool_call 块的 inner 内容（不含 <tool_call> 包裹）。

        返回 None 仅当 content 为空白。无法解析时返回带 error 的 ToolCall（engine 反馈给 agent）。
        触发 repair 兜底时 warnings 带上祈使句提示。

        <reason>（调用意图，display-only）由语法层 _parse_with_etree 像 <name>/<params> 一样
        depth-0 提取——不在这里、也不在任何 repair 里特殊处理。malformed 调用经 generic repair
        合法化后再解析，reason 的深度由 etree 权威判定；修不好的退化路径 reason 为 None（best-effort）。

        is_trailing：本块是拆分层判定的尾部未终止块。complete 块（有 CDATA 外的
        </tool_call>）按定义完整，不走尾部不完整分支。
        """
        # 空白内容 → 跳过
        if not content.strip():
            return None

        # 严格 XML 解析先行（complete 块 / 只漏 </tool_call> 但字段都全的 trailing 块都走这里）。
        # well-formed XML 若违反 tool-call grammar，直接失败：多个 params / 散落参数
        # 的合并语义不唯一，parser 不替模型猜测覆盖顺序。
        etree_result: Optional[ToolCall] = None
        try:
            etree_result = XMLToolCallParser._parse_with_etree(content)
            if etree_result and etree_result.params:
                return etree_result
        except ToolCallProtocolError as exc:
            return XMLToolCallParser._parse_error(
                content,
                observed_issue=str(exc),
            )
        except ET.ParseError:
            pass

        # trailing 块若尾部不完整（CDATA 未闭合 / 末尾字段未闭合）则拒绝
        # salvage。这可能是 provider cutoff，也可能只是模型漏了闭合符；单凭文本
        # 无法区分，更不能猜测 CDATA 边界后执行残缺参数。
        if is_trailing and XMLToolCallParser._detect_incomplete_tail(content):
            return XMLToolCallParser._incomplete_toolcall(content)

        # 渐进 repair（仅保留可唯一确定意图的格式问题）——每个 repair 实际改写输入时
        # 往 warnings 登记一条祈使句提示
        warnings: List[str] = []
        repaired = XMLToolCallParser._repair_tool_name_as_tag(content, warnings)
        repaired = XMLToolCallParser._repair_tag_equals_syntax(repaired, warnings)
        repaired = XMLToolCallParser._repair_unclosed_cdata_tags(repaired, warnings)
        repaired = XMLToolCallParser._repair_missing_closing_tags(repaired, warnings)

        if repaired != content:
            try:
                repaired_result = XMLToolCallParser._parse_with_etree(repaired)
                # repair 后只要解析成功就返回——**不**用 params 是否非空判定。否则"需要 repair
                # 才能解析、且结果无参"的合法调用（如 `<name=ping</name><params></params>`、
                # `<ping><params></params></ping>`，或参数全可选的 custom HTTP tool）会被误判
                # __malformed__。无参即最终结果。
                if repaired_result is not None:
                    repaired_result.warnings = warnings
                    return repaired_result
            except ToolCallProtocolError as exc:
                return XMLToolCallParser._parse_error(
                    repaired,
                    observed_issue=str(exc),
                    warnings=warnings,
                )
            except ET.ParseError:
                pass

        # repair 没改动内容 / 重解析失败 → 回退到首次严格解析的干净结果（含无参工具调用，params={}）。
        if etree_result is not None:
            etree_result.warnings = warnings
            return etree_result

        # 诚实失败 → 返回 error ToolCall（不再 lossy 抠取捏造残缺参数；已废 _fallback_parse）。
        # 保证 engine 知道 agent 尝试了 tool call，而非静默忽略。
        return XMLToolCallParser._parse_error(
            content,
            observed_issue=(
                "The XML is malformed or does not match the tool-call structure."
            ),
            warnings=warnings,
        )

    @staticmethod
    def _incomplete_toolcall(content: str) -> ToolCall:
        """统一未完整调用错误，不猜测是 provider cutoff 还是模型漏闭合符。"""
        return XMLToolCallParser._parse_error(
            content,
            observed_issue=(
                "A CDATA block or field appears unfinished. The response may have been "
                "interrupted, or a closing delimiter may have been omitted; no partial "
                "parameters were executed."
            ),
            fallback_name="__incomplete__",
            extract_name=True,
        )

    @staticmethod
    def _parse_error(
        content: str,
        observed_issue: str,
        warnings: Optional[List[str]] = None,
        fallback_name: str = "__malformed__",
        extract_name: bool = False,
    ) -> ToolCall:
        """构造统一的模型可恢复错误：规范格式始终存在，分支只追加可观测事实。"""
        name = fallback_name
        if extract_name:
            extracted_name = XMLToolCallParser._extract_name_outside_cdata(content)
            if extracted_name:
                name = extracted_name
        return ToolCall(
            name=name,
            params={},
            error=(
                "Your tool call could not be parsed as a complete, valid tool call. "
                "Retry using exactly this format:\n"
                f"{TOOL_CALL_EXAMPLE}\n"
                f"Observed issue: {observed_issue}"
            ),
            warnings=warnings or [],
        )

    @staticmethod
    def _parse_with_etree(content: str) -> Optional[ToolCall]:
        """使用 ElementTree 解析"""
        # 包装成完整 XML
        xml_str = f"<root>{content}</root>"
        root = ET.fromstring(xml_str)

        # 没有 name 的 well-formed 输入可能是可修复的 <tool_name>...</tool_name> 形式。
        if root.find('name') is None:
            return None

        XMLToolCallParser._validate_protocol_shape(root)

        # 提取 name
        name_elem = root.find('name')
        assert name_elem is not None
        name = (name_elem.text or "").strip()
        if not name:
            return None

        # 提取 params
        params_elem = root.find('params')
        params = XMLToolCallParser._parse_element(params_elem) if params_elem is not None else {}

        # 提取 reason（调用意图，display-only）。root.find 只匹配直接子节点 = depth-0，故嵌在
        # <params> 里、名为 reason 的合法参数（root/params/reason）天然不会被当成调用意图。
        # 与 name/params 同属协议 grammar，由语法层在此一处处理，repair 层不感知。
        reason_elem = root.find('reason')
        reason = (reason_elem.text or "").strip() if reason_elem is not None else None

        return ToolCall(name=name, params=params, reason=reason or None)

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
        return elem.text or ""

    @staticmethod
    def _validate_protocol_shape(root: ET.Element) -> None:
        """校验 well-formed XML 之上的 tool-call grammar，不做猜测性合并。"""
        top_level = list(root)
        top_level_tags = [child.tag for child in top_level]

        if top_level_tags.count('name') != 1:
            raise ToolCallProtocolError("Expected exactly one top-level <name> element.")
        if top_level_tags.count('params') > 1:
            raise ToolCallProtocolError(
                "Found multiple top-level <params> blocks; use a single <params> block."
            )
        if top_level_tags.count('reason') > 1:
            raise ToolCallProtocolError("Found multiple top-level <reason> elements.")

        unexpected = [
            tag for tag in top_level_tags if tag not in STRUCTURAL_TAGS
        ]
        if unexpected:
            names = ", ".join(f"<{tag}>" for tag in unexpected)
            raise ToolCallProtocolError(
                f"Parameter element(s) {names} appeared outside <params>."
            )

        if (root.text or "").strip() or any((child.tail or "").strip() for child in top_level):
            raise ToolCallProtocolError(
                "Found text outside the <reason>, <name>, or <params> elements."
            )

        nested_control = [
            child.tag for child in top_level
            if child.tag in {'reason', 'name'} and list(child)
        ]
        if nested_control:
            names = ", ".join(f"<{name}>" for name in nested_control)
            raise ToolCallProtocolError(
                f"Control element(s) {names} contain nested XML; wrap text in CDATA."
            )

        params_elem = root.find('params')
        if params_elem is None:
            return
        if (params_elem.text or "").strip():
            raise ToolCallProtocolError(
                "Found text directly inside <params>; wrap every value in its parameter element."
            )
        if any((child.tail or "").strip() for child in params_elem):
            raise ToolCallProtocolError(
                "Found text between parameter elements; keep values inside their own elements."
            )

        seen = set()
        duplicates = set()
        nested = []
        for child in params_elem:
            if child.tag in seen:
                duplicates.add(child.tag)
            else:
                seen.add(child.tag)
            if len(child):
                nested.append(child.tag)

        if duplicates:
            names = ", ".join(f"<{name}>" for name in sorted(duplicates))
            raise ToolCallProtocolError(
                f"Found duplicate parameter element(s) {names}; emit each parameter once."
            )

        if nested:
            names = ", ".join(f"<{name}>" for name in nested)
            raise ToolCallProtocolError(
                f"Parameter value(s) {names} contain nested XML; wrap each value in CDATA."
            )

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
        # 所有结构判定都在遮蔽 CDATA 的 masked 串上做（_mask_cdata 等长替换，span 与 content
        # 1:1），真实文本按 span 切自 content —— 否则 CDATA 内的字面 <name>/</reason> 会骗过下面的
        # 早退与 <reason> 跳过逻辑，把本可 repair 的调用判死（reviewer round-5）。
        masked = XMLToolCallParser._mask_cdata(content)

        # 已有 <name> 标签 → 无需修复（masked 上查，CDATA 里的字面 <name> 不算）
        if re.search(r'<name[\s>=]', masked):
            return content

        # 跳过开头的结构性 sibling <reason>（完整元素，承载调用意图）——它不是工具名外包标签。
        # 否则"第一个标签=工具名"会把 <reason> 误当工具名（name='reason'、真实工具名丢失）。
        # span 取自 masked → CDATA 内字面 </reason> 不会截断 prefix；prefix 真实文本切自 content。
        reason_m = re.match(r'\s*<reason\s*>.*?</reason\s*>\s*', masked, re.DOTALL | re.IGNORECASE)
        start = reason_m.end() if reason_m else 0
        prefix = content[:start]

        # 在 reason 之后找首个标签（masked 上匹配开标签，位置即可切 content）
        match = re.match(r'\s*<(\w+)>', masked[start:], re.DOTALL)
        if not match:
            return content

        tag_name = match.group(1)

        # 首标签是结构性 sibling（params/reason）→ 不是工具名，原样返回（含已剥的 prefix）
        if tag_name.lower() in STRUCTURAL_TAGS:
            return content

        # 开标签之后的真实内容；末尾若有该工具名闭合标签（end-anchored，不会误伤 CDATA 内字面量）去掉
        rest = content[start + match.end():]
        rest = re.sub(rf'</\s*{re.escape(tag_name)}\s*>\s*$', '', rest, flags=re.DOTALL)

        warnings.append(
            f"Wrote tool name as outer wrapping tag (e.g., <{tag_name}>...</{tag_name}>). "
            f"Correct form: <name>{tag_name}</name> with <params> as a sibling. "
            f"Do not wrap the call body with the tool name."
        )
        return f'{prefix}<name>{tag_name}</name>\n{rest}'

    @staticmethod
    def _repair_tag_equals_syntax(content: str, warnings: List[str]) -> str:
        """
        修复 <tag=value</tag> → <tag>value</tag>

        小模型容易把 <tag>value</tag> 写成 <tag=value</tag>，例如：
            <name=call_subagent</name>
        修复为：
            <name>call_subagent</name>

        CDATA-aware：匹配只在遮蔽 CDATA 的 masked 串上找（span 与 content 1:1），按 span 逆序
        改写 content —— 否则 CDATA 内的字面 <a=b</a>（如 reason/content 里的代码示例）会被误重写、
        污染参数值。
        """
        masked = XMLToolCallParser._mask_cdata(content)
        matches = list(re.finditer(r'<(\w+)=([^<>]+)</\1>', masked))
        if not matches:
            return content

        # 逆序按 span 改写，避免前面的替换位移后面的偏移；value 真实文本切自 content
        result = content
        for m in reversed(matches):
            tag = m.group(1)
            value = content[m.start(2):m.end(2)]
            result = result[:m.start()] + f'<{tag}>{value}</{tag}>' + result[m.end():]

        warnings.append(
            "Used '=' inside tag opening (e.g., <name=foo</name>). "
            "Correct form: <name>foo</name>. "
            "Never use '=' in tag openings — open with '>' and close with '</tag>'."
        )
        return result

    @staticmethod
    def _detect_incomplete_tail(content: str) -> bool:
        """检测 trailing 块的尾部不完整结构（纯检测、不改写内容）。

        栈式扫描跳过 CDATA 区，识别两类尾部不完整：
        - 案例 A：尾部 CDATA 未闭合（无 ]]>）。
        - 案例 B/C：CDATA 都闭合，但末尾有未闭合字段标签，且该标签的 open 就是最后一个 tag
          事件（gate：区分"尾部不完整" vs "mid-content 漏闭合但后面还有 sibling 标签"——后者是
          格式错而非截断，交 _repair_unclosed_cdata_tags 处理）。
        排除 params / tool_call（结构标签，由拆分层 / 其他 repair 负责）。
        """
        stack: List[tuple] = []  # 未闭合标签栈：(tag_name, open_start_pos)
        last_tag_event_pos = -1  # 最后一次见到 tag-shape 内容的位置

        tag_re = re.compile(r'<(/?)(\w+)\s*>')

        for is_cdata, region_start, region_end, is_complete in (
            XMLToolCallParser._iter_cdata_regions(content)
        ):
            if is_cdata:
                if not is_complete:
                    return True  # 案例 A
                continue

            for tag_match in tag_re.finditer(content, region_start, region_end):
                tag_idx = tag_match.start()
                last_tag_event_pos = tag_idx
                is_close = tag_match.group(1) == '/'
                tag_name = tag_match.group(2)
                if is_close:
                    if stack and stack[-1][0] == tag_name:
                        stack.pop()
                    # 不匹配的关闭标签忽略
                else:
                    stack.append((tag_name, tag_idx))

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
        # _detect_incomplete_tail 短路、走不到这里，但安全性不应依赖调用顺序这个隐式前提。
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
        span 可直接切回 content 取真实文本。只遮蔽**已闭合**的 CDATA；未闭合区域保留原文
        并线性结束，函数本身不依赖上游必须先短路。

        **不变量**：凡是在 raw 内容上做结构判定的 repair（找标签 / 判早退 / 切 span）都必须先经此
        遮蔽，否则会被 CDATA 内字面标签骗。
        """
        parts = []
        changed = False
        for is_cdata, start, end, is_complete in XMLToolCallParser._iter_cdata_regions(content):
            if is_cdata and is_complete:
                parts.append('\uE000' * (end - start))
                changed = True
            else:
                parts.append(content[start:end])
        return ''.join(parts) if changed else content

    @staticmethod
    def _extract_name_outside_cdata(content: str) -> Optional[str]:
        """线性提取 CDATA 外的 <name>，仅用于失败事件的 observability 归类。"""
        for is_cdata, start, end, _ in XMLToolCallParser._iter_cdata_regions(content):
            if is_cdata:
                continue
            match = XMLToolCallParser._NAME_RE.search(content, start, end)
            if match:
                return content[match.start(1):match.end(1)].strip() or None
        return None

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
