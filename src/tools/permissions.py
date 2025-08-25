"""
工具权限控制系统
管理工具的访问权限和执行授权
"""

from typing import Dict, Set, Optional, List, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from .base import ToolPermission, BaseTool
from utils.logger import get_logger

logger = get_logger("ToolPermissions")


@dataclass
class PermissionRequest:
    """权限请求"""
    tool_name: str
    agent_name: str
    reason: str
    requested_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"  # pending, approved, denied
    reviewed_at: Optional[datetime] = None
    reviewer: Optional[str] = None
    
    def approve(self, reviewer: str = "system"):
        """批准请求"""
        self.status = "approved"
        self.reviewed_at = datetime.now()
        self.reviewer = reviewer
    
    def deny(self, reviewer: str = "system"):
        """拒绝请求"""
        self.status = "denied"
        self.reviewed_at = datetime.now()
        self.reviewer = reviewer


@dataclass
class PermissionGrant:
    """权限授予记录"""
    tool_name: str
    agent_name: str
    permission_level: ToolPermission
    granted_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    granted_by: str = "system"
    
    def is_valid(self) -> bool:
        """检查授权是否有效"""
        if self.expires_at and datetime.now() > self.expires_at:
            return False
        return True


class PermissionManager:
    """
    权限管理器
    控制Agent对工具的访问权限
    """
    
    def __init__(self):
        """初始化权限管理器"""
        # Agent默认权限级别
        self.agent_permissions: Dict[str, Set[ToolPermission]] = {
            "lead_agent": {
                ToolPermission.PUBLIC,
                ToolPermission.NOTIFY,
                ToolPermission.CONFIRM
            },
            "search_agent": {
                ToolPermission.PUBLIC
            },
            "crawl_agent": {
                ToolPermission.PUBLIC
            }
        }
        
        # 特殊权限授予记录
        self.special_grants: List[PermissionGrant] = []
        
        # 待处理的权限请求
        self.pending_requests: List[PermissionRequest] = []
        
        # 工具特殊要求（某些工具可能需要额外检查）
        self.tool_requirements: Dict[str, callable] = {}
    
    def check_permission(
        self,
        agent_name: str,
        tool: BaseTool,
        auto_request: bool = False
    ) -> bool:
        """
        检查Agent是否有权限使用工具
        
        Args:
            agent_name: Agent名称
            tool: 工具实例
            auto_request: 如果无权限，是否自动创建请求
            
        Returns:
            是否有权限
        """
        # 检查特殊授权
        for grant in self.special_grants:
            if (grant.agent_name == agent_name and 
                grant.tool_name == tool.name and
                grant.is_valid()):
                logger.debug(f"Agent '{agent_name}' has special grant for '{tool.name}'")
                return True
        
        # 检查默认权限
        agent_perms = self.agent_permissions.get(agent_name, {ToolPermission.PUBLIC})
        has_permission = tool.permission in agent_perms
        
        if not has_permission:
            logger.warning(
                f"Agent '{agent_name}' lacks permission for '{tool.name}' "
                f"(requires: {tool.permission.value})"
            )
            
            if auto_request:
                self.request_permission(
                    agent_name,
                    tool.name,
                    f"Auto-requested for task execution"
                )
        
        return has_permission
    
    def request_permission(
        self,
        agent_name: str,
        tool_name: str,
        reason: str
    ) -> PermissionRequest:
        """
        请求工具权限
        
        Args:
            agent_name: Agent名称
            tool_name: 工具名称
            reason: 请求原因
            
        Returns:
            权限请求对象
        """
        request = PermissionRequest(
            tool_name=tool_name,
            agent_name=agent_name,
            reason=reason
        )
        
        self.pending_requests.append(request)
        logger.info(
            f"Permission request created: {agent_name} -> {tool_name} "
            f"(reason: {reason})"
        )
        
        return request
    
    def grant_permission(
        self,
        agent_name: str,
        tool_name: str,
        permission_level: ToolPermission,
        duration_hours: Optional[int] = None,
        granted_by: str = "system"
    ) -> PermissionGrant:
        """
        授予临时权限
        
        Args:
            agent_name: Agent名称
            tool_name: 工具名称
            permission_level: 权限级别
            duration_hours: 有效期（小时），None表示永久
            granted_by: 授权者
            
        Returns:
            授权记录
        """
        expires_at = None
        if duration_hours:
            expires_at = datetime.now() + timedelta(hours=duration_hours)
        
        grant = PermissionGrant(
            tool_name=tool_name,
            agent_name=agent_name,
            permission_level=permission_level,
            expires_at=expires_at,
            granted_by=granted_by
        )
        
        self.special_grants.append(grant)
        
        logger.info(
            f"Permission granted: {agent_name} -> {tool_name} "
            f"(level: {permission_level.value}, expires: {expires_at})"
        )
        
        return grant
    
    def revoke_permission(
        self,
        agent_name: str,
        tool_name: str
    ) -> bool:
        """
        撤销权限
        
        Args:
            agent_name: Agent名称
            tool_name: 工具名称
            
        Returns:
            是否成功撤销
        """
        initial_count = len(self.special_grants)
        self.special_grants = [
            g for g in self.special_grants
            if not (g.agent_name == agent_name and g.tool_name == tool_name)
        ]
        
        revoked = len(self.special_grants) < initial_count
        if revoked:
            logger.info(f"Permission revoked: {agent_name} -> {tool_name}")
        
        return revoked
    
    def set_agent_default_permissions(
        self,
        agent_name: str,
        permissions: Set[ToolPermission]
    ):
        """
        设置Agent的默认权限
        
        Args:
            agent_name: Agent名称
            permissions: 权限集合
        """
        self.agent_permissions[agent_name] = permissions
        logger.info(
            f"Set default permissions for '{agent_name}': "
            f"{[p.value for p in permissions]}"
        )
    
    def get_pending_requests(
        self,
        agent_name: Optional[str] = None
    ) -> List[PermissionRequest]:
        """
        获取待处理的权限请求
        
        Args:
            agent_name: 筛选特定Agent的请求
            
        Returns:
            权限请求列表
        """
        requests = [r for r in self.pending_requests if r.status == "pending"]
        
        if agent_name:
            requests = [r for r in requests if r.agent_name == agent_name]
        
        return requests
    
    def process_request(
        self,
        request: PermissionRequest,
        approve: bool,
        reviewer: str = "user",
        auto_grant_duration: Optional[int] = 24
    ) -> Optional[PermissionGrant]:
        """
        处理权限请求
        
        Args:
            request: 权限请求
            approve: 是否批准
            reviewer: 审核者
            auto_grant_duration: 批准时的授权时长（小时）
            
        Returns:
            如果批准，返回授权记录
        """
        if approve:
            request.approve(reviewer)
            
            # 自动创建授权
            # 这里简化处理，授予CONFIRM权限
            grant = self.grant_permission(
                agent_name=request.agent_name,
                tool_name=request.tool_name,
                permission_level=ToolPermission.CONFIRM,
                duration_hours=auto_grant_duration,
                granted_by=reviewer
            )
            
            logger.info(f"Request approved: {request.tool_name} for {request.agent_name}")
            return grant
        else:
            request.deny(reviewer)
            logger.info(f"Request denied: {request.tool_name} for {request.agent_name}")
            return None
    
    def cleanup_expired(self):
        """清理过期的授权"""
        initial_count = len(self.special_grants)
        self.special_grants = [g for g in self.special_grants if g.is_valid()]
        
        removed = initial_count - len(self.special_grants)
        if removed > 0:
            logger.info(f"Cleaned up {removed} expired permissions")
    
    def get_agent_permissions(self, agent_name: str) -> Dict[str, Any]:
        """
        获取Agent的完整权限信息
        
        Args:
            agent_name: Agent名称
            
        Returns:
            权限信息字典
        """
        # 默认权限
        default_perms = self.agent_permissions.get(
            agent_name,
            {ToolPermission.PUBLIC}
        )
        
        # 特殊授权
        special_grants = [
            g for g in self.special_grants
            if g.agent_name == agent_name and g.is_valid()
        ]
        
        # 待处理请求
        pending = [
            r for r in self.pending_requests
            if r.agent_name == agent_name and r.status == "pending"
        ]
        
        return {
            "agent_name": agent_name,
            "default_permissions": [p.value for p in default_perms],
            "special_grants": [
                {
                    "tool": g.tool_name,
                    "level": g.permission_level.value,
                    "expires": g.expires_at.isoformat() if g.expires_at else None
                }
                for g in special_grants
            ],
            "pending_requests": [
                {
                    "tool": r.tool_name,
                    "reason": r.reason,
                    "requested_at": r.requested_at.isoformat()
                }
                for r in pending
            ]
        }


# 全局权限管理器实例
_global_permission_manager = PermissionManager()


# 便捷函数
def check_permission(agent_name: str, tool: BaseTool, **kwargs) -> bool:
    """检查权限的便捷函数"""
    return _global_permission_manager.check_permission(agent_name, tool, **kwargs)


def grant_permission(agent_name: str, tool_name: str, **kwargs) -> PermissionGrant:
    """授予权限的便捷函数"""
    return _global_permission_manager.grant_permission(
        agent_name, tool_name, ToolPermission.CONFIRM, **kwargs
    )


def get_permission_manager() -> PermissionManager:
    """获取全局权限管理器"""
    return _global_permission_manager