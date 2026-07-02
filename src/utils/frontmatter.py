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


def parse_frontmatter_text(content: str, where: str) -> Tuple[dict, str]:
    """MD 文本 → (frontmatter dict, body)。`where` 仅用于报错定位(文件路径 / zip 成员)。"""
    if not content.startswith("---"):
        raise FrontmatterError(f"MD file must start with YAML frontmatter: {where}")
    try:
        end_idx = content.index("---", 3)
    except ValueError:
        raise FrontmatterError(f"MD file has unterminated YAML frontmatter: {where}")
    try:
        frontmatter = yaml.safe_load(content[3:end_idx].strip()) or {}
    except yaml.YAMLError as e:
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
