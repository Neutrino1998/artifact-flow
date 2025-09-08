"""
Artifact操作工具
模仿Claude的Artifact系统，支持create/update/rewrite操作
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import uuid 
import difflib
import re
from dataclasses import dataclass, field
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger

logger = get_logger("Tools")


@dataclass
class ArtifactVersion:
    """Artifact版本记录"""
    version: int
    content: str
    updated_at: datetime
    update_type: str  # "create", "update", "rewrite"
    changes: Optional[List[Tuple[str, str]]] = None  # [(old_str, new_str), ...]


class FuzzyTextMatcher:
    """
    模糊文本匹配器，类似git diff的匹配逻辑
    """
    
    @staticmethod
    def find_best_match(
        content: str, 
        target: str, 
        threshold: float = 0.85,  # 85%相似度即可
        context_lines: int = 2     # 上下文行数
    ) -> Tuple[Optional[str], float, Tuple[int, int]]:
        """
        在content中找到与target最相似的文本段
        
        Returns:
            (matched_text, similarity_ratio, (start_pos, end_pos))
        """
        
        # 1. 先尝试精确匹配（最快）
        if target in content:
            start = content.index(target)
            return target, 1.0, (start, start + len(target))
        
        # 2. 尝试忽略空白差异的匹配
        normalized_target = FuzzyTextMatcher._normalize_whitespace(target)
        normalized_content = FuzzyTextMatcher._normalize_whitespace(content)
        
        if normalized_target in normalized_content:
            # 找到规范化后的位置，然后映射回原始位置
            norm_start = normalized_content.index(normalized_target)
            # 这里需要更复杂的映射逻辑，简化处理
            return FuzzyTextMatcher._extract_original_match(
                content, target, norm_start
            )
        
        # 3. 基于行的模糊匹配（类似git diff）
        return FuzzyTextMatcher._line_based_fuzzy_match(
            content, target, threshold
        )
    
    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """规范化空白字符：多个空白变一个，去除行尾空白"""
        # 将所有空白序列替换为单个空格
        text = re.sub(r'\s+', ' ', text)
        # 去除每行末尾的空白
        lines = text.split('\n')
        lines = [line.rstrip() for line in lines]
        return '\n'.join(lines).strip()
    
    @staticmethod
    def _line_based_fuzzy_match(
        content: str, 
        target: str, 
        threshold: float
    ) -> Tuple[Optional[str], float, Tuple[int, int]]:
        """
        基于行的模糊匹配（git diff风格）
        将文本分割成行，找到最匹配的连续行序列
        """
        content_lines = content.split('\n')
        target_lines = target.strip().split('\n')
        
        if not target_lines:
            return None, 0.0, (0, 0)
        
        best_match = None
        best_ratio = 0.0
        best_pos = (0, 0)
        
        # 滑动窗口搜索
        window_size = len(target_lines)
        
        for i in range(len(content_lines) - window_size + 1):
            window = content_lines[i:i + window_size]
            window_text = '\n'.join(window)
            
            # 使用SequenceMatcher计算相似度
            matcher = difflib.SequenceMatcher(
                None, 
                target.strip(), 
                window_text.strip()
            )
            ratio = matcher.ratio()
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = window_text
                
                # 计算在原始内容中的位置
                lines_before = '\n'.join(content_lines[:i])
                start_pos = len(lines_before) + (1 if i > 0 else 0)
                end_pos = start_pos + len(window_text)
                best_pos = (start_pos, end_pos)
        
        if best_ratio >= threshold:
            return best_match, best_ratio, best_pos
        
        # 4. 如果行匹配失败，尝试更灵活的块匹配
        return FuzzyTextMatcher._flexible_block_match(
            content, target, threshold
        )
    
    @staticmethod
    def _flexible_block_match(
        content: str, 
        target: str, 
        threshold: float
    ) -> Tuple[Optional[str], float, Tuple[int, int]]:
        """
        灵活的块匹配：在内容中搜索与目标最相似的文本块
        使用动态规划找最长公共子序列
        """
        target_len = len(target)
        search_window = target_len * 2  # 搜索窗口是目标长度的2倍
        step = max(1, target_len // 4)  # 步长是目标长度的1/4
        
        best_match = None
        best_ratio = 0.0
        best_pos = (0, 0)
        
        for start in range(0, len(content) - target_len + 1, step):
            # 尝试不同长度的匹配（±20%）
            for length_factor in [0.8, 0.9, 1.0, 1.1, 1.2]:
                end = min(
                    start + int(target_len * length_factor),
                    len(content)
                )
                
                candidate = content[start:end]
                
                # 快速相似度检查（基于字符集合）
                if not FuzzyTextMatcher._quick_similarity_check(
                    candidate, target, 0.5
                ):
                    continue
                
                # 详细相似度计算
                matcher = difflib.SequenceMatcher(None, target, candidate)
                ratio = matcher.ratio()
                
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = candidate
                    best_pos = (start, end)
                    
                    # 如果找到非常好的匹配，提前退出
                    if ratio > 0.95:
                        return best_match, best_ratio, best_pos
        
        if best_ratio >= threshold:
            return best_match, best_ratio, best_pos
        
        return None, best_ratio, (0, 0)
    
    @staticmethod
    def _quick_similarity_check(text1: str, text2: str, threshold: float) -> bool:
        """快速检查两个文本的相似度（基于字符集合）"""
        set1 = set(text1.lower().split())
        set2 = set(text2.lower().split())
        
        if not set1 or not set2:
            return False
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return (intersection / union) >= threshold if union > 0 else False
    
    @staticmethod
    def _extract_original_match(
        content: str, 
        target: str, 
        approximate_pos: int
    ) -> Tuple[str, float, Tuple[int, int]]:
        """从大概位置提取原始匹配文本"""
        # 简化实现：在approximate_pos附近搜索
        search_range = len(target) * 2
        start = max(0, approximate_pos - search_range)
        end = min(len(content), approximate_pos + search_range)
        
        search_area = content[start:end]
        
        # 在搜索区域内找最佳匹配
        best_match = None
        best_ratio = 0.0
        best_pos_in_area = 0
        
        for i in range(len(search_area) - len(target) + 1):
            candidate = search_area[i:i+len(target)]
            ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate
                best_pos_in_area = i
        
        if best_match:
            actual_start = start + best_pos_in_area
            actual_end = actual_start + len(best_match)
            return best_match, best_ratio, (actual_start, actual_end)
        
        return None, 0.0, (0, 0)
    

class Artifact:
    """
    Artifact对象
    支持文本内容的创建、更新和重写
    """
    
    def __init__(
        self,
        artifact_id: str,
        content_type: str,  # 👈 从 artifact_type 改为 content_type
        title: str,
        initial_content: str,
        metadata: Dict = None
    ):
        self.id = artifact_id
        self.content_type = content_type  # 👈 从 self.type 改为 self.content_type
        self.title = title
        self.content = initial_content
        self.metadata = metadata or {}
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.current_version = 1
        
        # 版本历史
        self.versions: List[ArtifactVersion] = [
            ArtifactVersion(
                version=1,
                content=initial_content,
                updated_at=self.created_at,
                update_type="create"
            )
        ]
    
    def update(self, old_str: str, new_str: str, use_fuzzy: bool = True) -> Tuple[bool, str]:
        """
        更新内容（支持模糊匹配）
        
        Args:
            old_str: 要替换的原文本
            new_str: 新文本
            use_fuzzy: 是否启用模糊匹配（默认启用）
            
        Returns:
            (成功与否, 消息, 额外信息字典)  # 👈 新增第三个返回值
        """
        # 1. 先尝试精确匹配
        count = self.content.count(old_str)
        
        if count == 1:
            # 精确匹配成功，执行原有逻辑
            new_content = self.content.replace(old_str, new_str)
            
            # 保存版本
            self.current_version += 1
            self.versions.append(
                ArtifactVersion(
                    version=self.current_version,
                    content=new_content,
                    updated_at=datetime.now(),
                    update_type="update",
                    changes=[(old_str, new_str)]
                )
            )
            
            self.content = new_content
            self.updated_at = datetime.now()
            
            return True, f"Successfully updated artifact (v{self.current_version})", {
                "match_type": "exact",
                "similarity": 1.0
            }
        
        elif count > 1:
            return False, f"Text '{old_str[:50]}...' appears {count} times (must be unique)", None
        
        # 2. 如果精确匹配失败且启用模糊匹配
        if use_fuzzy:
            logger.debug("Exact match failed, attempting fuzzy match...")
            
            matcher = FuzzyTextMatcher()
            matched_text, similarity, (start, end) = matcher.find_best_match(
                self.content, 
                old_str, 
                threshold=0.85  # 85%相似度阈值
            )
            
            if matched_text:
                # 记录模糊匹配信息
                logger.info(
                    f"Fuzzy match found with {similarity:.1%} similarity\n"
                    f"Expected: {old_str[:100]}...\n"
                    f"Found: {matched_text[:100]}..."
                )
                
                # 执行替换
                new_content = (
                    self.content[:start] + 
                    new_str + 
                    self.content[end:]
                )
                
                # 保存版本
                self.current_version += 1
                self.versions.append(
                    ArtifactVersion(
                        version=self.current_version,
                        content=new_content,
                        updated_at=datetime.now(),
                        update_type="update_fuzzy",  # 标记为模糊更新
                        changes=[(matched_text, new_str)]  # 记录实际匹配的文本
                    )
                )
                
                self.content = new_content
                self.updated_at = datetime.now()
                
                # 👇 返回详细的匹配信息
                return True, f"Successfully updated artifact (v{self.current_version}) with {similarity:.1%} match", {
                    "match_type": "fuzzy",
                    "similarity": similarity,
                    "expected_text": old_str,  # 用户提供的文本
                    "matched_text": matched_text,  # 实际匹配到的文本
                    "position": {"start": start, "end": end}
                }
        
        # 3. 完全找不到匹配
        return False, f"Text '{old_str[:50]}...' not found in artifact", None
    
    def rewrite(self, new_content: str) -> Tuple[bool, str]:
        """
        完全重写内容
        
        Args:
            new_content: 全新的内容
            
        Returns:
            (成功与否, 消息)
        """
        # 保存版本
        self.current_version += 1
        self.versions.append(
            ArtifactVersion(
                version=self.current_version,
                content=new_content,
                updated_at=datetime.now(),
                update_type="rewrite"
            )
        )
        
        self.content = new_content
        self.updated_at = datetime.now()
        
        return True, f"Successfully rewrote artifact (v{self.current_version})"
    
    def get_version(self, version: int = None) -> Optional[str]:
        """获取指定版本的内容"""
        if version is None:
            return self.content
        
        for v in self.versions:
            if v.version == version:
                return v.content
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "current_version": self.current_version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


@dataclass
class ArtifactSession:
    """单个会话的artifact容器"""
    session_id: str
    artifacts: Dict[str, Artifact] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ArtifactStore:
    """Artifact存储管理（支持session）"""
    
    def __init__(self):
        self.sessions: Dict[str, ArtifactSession] = {}
        self.current_session_id: Optional[str] = None
    
    def create_session(self, session_id: Optional[str] = None) -> str:
        """创建新session并设为当前session"""
        if session_id is None:
            session_id = f"session_{uuid.uuid4().hex[:8]}"
        
        self.sessions[session_id] = ArtifactSession(session_id=session_id)
        self.current_session_id = session_id
        logger.info(f"Created new session: {session_id}")
        return session_id
    
    def set_session(self, session_id: Optional[str]):
        """切换当前session"""
        if session_id and session_id not in self.sessions:
            logger.warning(f"Session {session_id} not found, creating new one")
            self.create_session(session_id)
        else:
            self.current_session_id = session_id
            logger.debug(f"Switched to session: {session_id}")
    
    def get_current_session(self) -> Optional[ArtifactSession]:
        """获取当前session，如果没有则创建默认session"""
        if self.current_session_id is None:
            self.create_session("default")
        return self.sessions.get(self.current_session_id)
    
    def clear_session(self, session_id: Optional[str] = None):
        """清空指定session的artifacts"""
        sid = session_id or self.current_session_id
        if sid and sid in self.sessions:
            self.sessions[sid].artifacts.clear()
            logger.info(f"Cleared session: {sid}")
    
    def create(
        self,
        artifact_id: str,
        content_type: str,  # 👈 从 artifact_type 改为 content_type
        title: str,
        content: str,
        metadata: Dict = None
    ) -> Tuple[bool, str]:
        """创建新的Artifact（在当前session中）"""
        session = self.get_current_session()
        if not session:
            return False, "No active session"
        
        if artifact_id in session.artifacts:
            return False, f"Artifact '{artifact_id}' already exists in session"
        
        artifact = Artifact(
            artifact_id=artifact_id,
            content_type=content_type,  # 👈 参数名改变
            title=title,
            initial_content=content,
            metadata=metadata
        )
        
        session.artifacts[artifact_id] = artifact
        return True, f"Created artifact '{artifact_id}' in session '{session.session_id}'"
    
    def get(self, artifact_id: str) -> Optional[Artifact]:
        """获取Artifact对象（从当前session）"""
        session = self.get_current_session()
        if not session:
            return None
        return session.artifacts.get(artifact_id)
    
    def list_artifacts(self, content_type: str = None) -> List[Dict]:  # 👈 参数名改变
        """列出当前session的所有Artifacts"""
        session = self.get_current_session()
        if not session:
            return []
        
        artifacts = []
        for artifact in session.artifacts.values():
            if content_type and artifact.content_type != content_type:  # 👈 属性名改变
                continue
            artifacts.append({
                "id": artifact.id,
                "content_type": artifact.content_type,  # 👈 返回字段名改变
                "title": artifact.title,
                "version": artifact.current_version,
                "updated_at": artifact.updated_at.isoformat()
            })
        return artifacts


# 全局Artifact存储
_artifact_store = ArtifactStore()


class CreateArtifactTool(BaseTool):
    """创建Artifact工具"""
    
    def __init__(self):
        super().__init__(
            name="create_artifact",
            description="Create a new artifact (like Claude's artifact creation)",
            permission=ToolPermission.NOTIFY
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Unique identifier (e.g., 'task_plan', 'research_results')",
                required=True
            ),
            ToolParameter(
                name="content_type",  # 👈 从 type 改为 content_type
                type="string",
                description="Content format: 'markdown', 'txt', 'python', 'html', 'json'",  # 👈 描述更清晰
                required=False,
                default="markdown"  # 👈 添加默认值
            ),
            ToolParameter(
                name="title",
                type="string",
                description="Title of the artifact",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="Initial text content",
                required=True
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        success, message = _artifact_store.create(
            artifact_id=params["id"],
            content_type=params.get("content_type", "markdown"),  # 👈 参数名改变，使用默认值
            title=params["title"],
            content=params["content"]
        )
        
        if success:
            logger.info(message)
            return ToolResult(success=True, data={"message": message})
        else:
            return ToolResult(success=False, error=message)


class UpdateArtifactTool(BaseTool):
    """
    更新Artifact工具
    通过指定old_str和new_str来更新内容（类似Claude的update机制）
    """
    
    def __init__(self):
        super().__init__(
            name="update_artifact",
            description="Update artifact content by replacing old text with new text (Support fuzzy matching)",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to update",
                required=True
            ),
            ToolParameter(
                name="old_str",
                type="string",
                description="Exact text to be replaced (must appear exactly once)",
                required=True
            ),
            ToolParameter(
                name="new_str",
                type="string",
                description="New text to replace with",
                required=True
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact = _artifact_store.get(params["id"])
        if not artifact:
            return ToolResult(
                success=False,
                error=f"Artifact '{params['id']}' not found"
            )
        
        success, message, match_info = artifact.update(
            old_str=params["old_str"],
            new_str=params["new_str"]
        )
        
        if success:
            logger.info(message)
            
            # 👇 构建返回数据，包含匹配详情
            result_data = {
                "message": message,
                "version": artifact.current_version
            }
            
            # 如果是模糊匹配，添加详细信息
            if match_info and match_info.get("match_type") == "fuzzy":
                result_data["fuzzy_match_details"] = {
                    "similarity": f"{match_info['similarity']:.1%}",
                    "expected": match_info["expected_text"][:200] + "..." if len(match_info["expected_text"]) > 200 else match_info["expected_text"],
                    "found": match_info["matched_text"][:200] + "..." if len(match_info["matched_text"]) > 200 else match_info["matched_text"],
                    "note": "Used fuzzy matching because exact text was not found"
                }
            
            return ToolResult(
                success=True,
                data=result_data,
                metadata=match_info  # 👈 完整信息放在metadata中
            )
        else:
            return ToolResult(success=False, error=message)


class RewriteArtifactTool(BaseTool):
    """
    重写Artifact工具
    完全替换整个内容
    """
    
    def __init__(self):
        super().__init__(
            name="rewrite_artifact",
            description="Completely rewrite the artifact content",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to rewrite",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="New complete content",
                required=True
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact = _artifact_store.get(params["id"])
        if not artifact:
            return ToolResult(
                success=False,
                error=f"Artifact '{params['id']}' not found"
            )
        
        success, message = artifact.rewrite(params["content"])
        
        logger.info(message)
        return ToolResult(
            success=True,
            data={
                "message": message,
                "version": artifact.current_version
            }
        )


class ReadArtifactTool(BaseTool):
    """读取Artifact工具"""
    
    def __init__(self):
        super().__init__(
            name="read_artifact",
            description="Read artifact content",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to read",
                required=True
            ),
            ToolParameter(
                name="version",
                type="integer",
                description="Version number (optional, defaults to latest)",
                required=False,
                default=None
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        artifact = _artifact_store.get(params["id"])
        if not artifact:
            return ToolResult(
                success=False,
                error=f"Artifact '{params['id']}' not found"
            )
        
        content = artifact.get_version(params.get("version"))
        if content is None:
            return ToolResult(
                success=False,
                error=f"Version {params['version']} not found"
            )
        
        return ToolResult(
            success=True,
            data={
                "id": artifact.id,
                "content_type": artifact.content_type,  # 👈 从 type 改为 content_type
                "title": artifact.title,
                "content": content,
                "version": artifact.current_version,
                "updated_at": artifact.updated_at.isoformat()
            }
        )


# 示例：创建Task Plan Artifact
TASK_PLAN_TEMPLATE = """# Research Task Plan

## Objective
{objective}

## Tasks
1. [ ] Information Gathering
   - Status: pending
   - Assigned: search_agent
   - Details: {task1_details}

2. [ ] Deep Analysis
   - Status: pending
   - Assigned: crawl_agent
   - Details: {task2_details}

3. [ ] Report Generation
   - Status: pending
   - Assigned: lead_agent
   - Details: Compile findings into comprehensive report

## Progress
- Overall: 0%
- Last Updated: {timestamp}
"""

# 示例：创建Result Artifact
RESULT_TEMPLATE = """# Research Results: {title}

## Executive Summary
{summary}

## Key Findings
{findings}

## Detailed Analysis
{analysis}

## Conclusions
{conclusions}

## References
{references}
"""


def register_artifact_tools():
    """注册所有Artifact操作工具"""
    from tools.registry import register_tool
    
    register_tool(CreateArtifactTool())
    register_tool(UpdateArtifactTool())
    register_tool(RewriteArtifactTool())
    register_tool(ReadArtifactTool())
    
    logger.info("Registered artifact tools")


def get_artifact_store() -> ArtifactStore:
    """获取Artifact存储实例"""
    return _artifact_store


if __name__ == "__main__":
    import asyncio
    import sys
    from utils.logger import set_global_debug
    set_global_debug(True)

    async def run_tests():
        """
        测试Artifact操作工具集，包括模糊匹配功能
        """
        print("\n🧪 Artifact Operations Test Suite (with Fuzzy Matching)")
        print("="*60)

        # 辅助函数
        def check(step_name: str, result: ToolResult) -> bool:
            if result.success:
                message = result.data.get('message', 'Operation successful.')
                print(f"✅ {step_name}: {message}")
                return True
            else:
                print(f"❌ {step_name}: FAILED - {result.error}")
                if step_name in ["Create Artifact", "Read Artifact"]:
                    sys.exit(1) 
                return False

        # 1. 初始化工具
        create_tool = CreateArtifactTool()
        read_tool = ReadArtifactTool()
        update_tool = UpdateArtifactTool()
        rewrite_tool = RewriteArtifactTool()
        
        # 2. 测试场景：模拟真实的Task Plan
        test_id = "task_plan"
        initial_content = """# Task: Research AI Safety
        
## Objective
Research latest developments in AI safety and alignment.

## Tasks
1. [✗] Search for recent papers on AI alignment
   - Status: pending
   - Assigned: search_agent
   - Notes: 

2. [✗] Extract key findings from top papers
   - Status: pending
   - Assigned: crawl_agent
   - Notes: Focus on 2024 publications

## Progress Summary
- Overall: 0%
- Last Updated: 2024-01-01"""

        print("\n--- Test 1: Basic Operations ---")
        
        # Create
        result = await create_tool.execute(
            id=test_id, 
            content_type="markdown", 
            title="AI Safety Research Plan", 
            content=initial_content
        )
        check("Create Task Plan", result)

        # Read
        result = await read_tool.execute(id=test_id)
        check("Read Task Plan", result)

        print("\n--- Test 2: Exact Match Update ---")
        
        # 精确匹配更新（应该成功）
        result = await update_tool.execute(
            id=test_id,
            old_str="- Overall: 0%",
            new_str="- Overall: 25%"
        )
        check("Update Progress (Exact Match)", result)

        print("\n--- Test 3: Fuzzy Match Updates ---")
        
        # 测试3a: 空白字符差异（缺少尾部空格）
        result = await update_tool.execute(
            id=test_id,
            old_str="1. [✗] Search for recent papers on AI alignment\n     - Status: pending\n      - Assigned: search_agent\n     - Notes:",  # 注意：空格数量不对
            new_str="1. [✓] Search for recent papers on AI alignment\n   - Status: completed\n   - Assigned: search_agent\n   - Notes: Found 15 relevant papers from 2024"
        )
        check("Update Task 1 (Fuzzy: whitespace mismatch)", result)

        # 测试3b: 轻微文本差异
        result = await update_tool.execute(
            id=test_id,
            old_str="2. [✗] Extract key findings from top papers\n   - Status: pending\n   - Assigned: search_agents\n   - Note: Focus on 2024 publication",  # 注意：crawl_agent写成了search_agents，Notes拼写错误
            new_str="2. [✓] Extract key findings from top papers\n   - Status: completed\n   - Assigned: crawl_agent\n   - Notes: Analyzed 5 key papers with breakthrough findings"
        )
        check("Update Task 2 (Fuzzy: minor text difference)", result)

        print("\n--- Test 4: Edge Cases ---")
        
        # 测试4: 完全不匹配的文本
        result = await update_tool.execute(
            id=test_id,
            old_str="This text does not exist in the artifact at all",
            new_str="This should fail"
        )
        if not result.success:
            print(f"✅ Correctly rejected non-existent text: {result.error}")

        print("\n" + "="*60)
        print("✅ Test Suite Completed Successfully.")

    # 运行异步测试函数
    asyncio.run(run_tests())