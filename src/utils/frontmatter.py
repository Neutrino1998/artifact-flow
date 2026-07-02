"""MD frontmatter 解析 —— seed(reconcile)与 skill 导入(E)共用的最底层方言。

原居 reconcile/seeds.py;抽到 utils 是为了 skill_validator(同层)能用而不 import
reconcile。**一套解析、两个消费者**:seed 侧把 FrontmatterError 转 SeedError,导入侧
把它折成 validator Finding —— frontmatter 方言由此永不漂移(与 skill_zip 的定位器
同一姿态)。
"""

from typing import List, Tuple

import yaml


class FrontmatterError(ValueError):
    """frontmatter 结构性错误(缺 `---` 头 / 未闭合 / allowed-tools 形状不对)。"""


class _NoAliasSafeLoader(yaml.SafeLoader):
    """禁 YAML anchor/alias 的 SafeLoader。safe_load 仍会展开 alias —— billion-laughs
    在 1KB 内即可写出,而本解析跑在 backend 宿主(非沙盒),是不可信导入的内存 DoS 面。
    合法 frontmatter 从不用锚点,一刀切禁。"""

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.composer.ComposerError(
                None, None, "YAML aliases are not allowed in frontmatter",
                self.peek_event().start_mark,
            )
        return super().compose_node(parent, index)


def parse_frontmatter_text(content: str, where: str) -> Tuple[dict, str]:
    """MD 文本 → (frontmatter dict, body)。`where` 仅用于报错定位(文件路径 / zip 成员)。"""
    if not content.startswith("---"):
        raise FrontmatterError(f"MD file must start with YAML frontmatter: {where}")
    try:
        end_idx = content.index("---", 3)
    except ValueError:
        raise FrontmatterError(f"MD file has unterminated YAML frontmatter: {where}")
    try:
        frontmatter = yaml.load(content[3:end_idx].strip(), Loader=_NoAliasSafeLoader) or {}
    except (yaml.YAMLError, RecursionError) as e:
        # RecursionError:深嵌套构造('['*50000)穿透 yaml.YAMLError —— 一并折成解析错误
        raise FrontmatterError(f"invalid YAML frontmatter in {where}: {e}")
    if not isinstance(frontmatter, dict):
        raise FrontmatterError(f"frontmatter must be a YAML mapping: {where}")
    body = content[end_idx + 3:].strip()
    return frontmatter, body


def normalize_allowed_tools(raw, where: str) -> List[str]:
    """`allowed-tools` → 条目列表(标准允许 list 或逗号分隔字符串;None → [])。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    raise FrontmatterError(f"{where}: allowed-tools must be a list or string")
