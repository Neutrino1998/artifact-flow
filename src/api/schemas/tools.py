"""
External 工具注册表管理 Pydantic schemas(B-4 admin CRUD)。

请求模型用 *Request 后缀(export_openapi 据此把非 Request 模型字段标 required);
响应模型字段对前端 = 始终在场。凭证写-only:请求收明文 value,响应永不回明文
(只 configured 布尔 + 占位符名)。
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# 请求
# --------------------------------------------------------------------------


class ToolParamSpec(BaseModel):
    name: str = Field(..., max_length=64)
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = ""
    required: bool = True
    default: Optional[Any] = None
    enum: Optional[List[Any]] = None


class ToolMemberSpec(BaseModel):
    member_name: str = Field(..., max_length=64, description="作者裸名;singleton 会被规整为 unit 名")
    permission: Literal["auto", "confirm"] = "confirm"
    description: str = ""
    endpoint: str = ""
    method: str = "GET"
    headers: Dict[str, str] = Field(default_factory=dict)
    parameters: List[ToolParamSpec] = Field(default_factory=list)
    response_extract: Optional[str] = None
    timeout: int = Field(60, ge=1, le=600)


class CreateToolUnitRequest(BaseModel):
    """POST /api/v1/admin/tools/units —— 新建 dynamic unit。"""
    name: str = Field(..., max_length=64, description="unit 名,全局唯一,禁含 '__'")
    kind: Literal["tool", "toolset"] = "tool"
    description: str = ""
    visibility: Literal["public", "department"] = "public"
    defer: bool = False
    members: List[ToolMemberSpec] = Field(..., min_length=1)


class UpdateToolUnitRequest(CreateToolUnitRequest):
    """PUT /api/v1/admin/tools/units/{name} —— 整体替换 dynamic unit(name 取自路径)。"""


class MountUnitRequest(BaseModel):
    """PUT /api/v1/admin/tools/units/{name}/agents/{agent_name}"""
    member_state: Literal["enabled", "disabled"] = "enabled"


class SetCredentialRequest(BaseModel):
    """PUT /api/v1/admin/tools/units/{name}/credentials/{placeholder} —— 写-only。"""
    value: str = Field(..., min_length=1, max_length=8192, description="明文,加密落库后永不回读")


# --------------------------------------------------------------------------
# 响应
# --------------------------------------------------------------------------


class ToolMemberResponse(BaseModel):
    member_name: str
    full_name: str
    permission: str
    definition: Dict[str, Any]


class MountedAgentResponse(BaseModel):
    agent_name: str
    member_state: str
    source: str


class MountResponse(BaseModel):
    """PUT .../agents/{agent} 的返回(挂载/改成员态后的绑定快照)。"""
    agent_name: str
    unit_name: str
    member_state: str
    source: str


class CredentialStatusResponse(BaseModel):
    placeholder: str
    configured: bool
    source: Optional[str] = None


class ToolUnitResponse(BaseModel):
    name: str
    kind: str
    description: str
    visibility: str
    defer: bool
    provider: str
    source: str
    members: List[ToolMemberResponse]
    mounted_agents: List[MountedAgentResponse]
    credentials: List[CredentialStatusResponse]


class ToolUnitListResponse(BaseModel):
    units: List[ToolUnitResponse]


class AgentSummaryResponse(BaseModel):
    name: str
    description: str
    internal: bool


class AgentListResponse(BaseModel):
    agents: List[AgentSummaryResponse]
