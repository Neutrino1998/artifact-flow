"""
Admin Tools Router —— external 工具注册表管理(admin-only)。挂 /api/v1/admin,故路径:
- GET    /api/v1/admin/tools/units                              列出 unit(含成员/挂载/凭证状态)
- POST   /api/v1/admin/tools/units                              新建 dynamic unit
- GET    /api/v1/admin/tools/units/{name}                       单查
- PUT    /api/v1/admin/tools/units/{name}                       整体替换(仅 dynamic)
- DELETE /api/v1/admin/tools/units/{name}                       删除(仅 dynamic)
- GET    /api/v1/admin/tools/agents                             agent 列表(挂载 UI 用)
- PUT    /api/v1/admin/tools/units/{name}/agents/{agent}        挂载/改成员态(写 dynamic)
- DELETE /api/v1/admin/tools/units/{name}/agents/{agent}        卸载(仅 dynamic 绑定)
- PUT    /api/v1/admin/tools/units/{name}/credentials/{ph}      配置凭证(写-only,加密落库)
- DELETE /api/v1/admin/tools/units/{name}/credentials/{ph}      删凭证

router 只做 transport:认证(require_admin)、解析、把 ToolRegistryError 映射 HTTP。
业务规则(seeded 只读、撞名闸、序列化掩码、加密)全在 ToolRegistryManager。
"""

from fastapi import APIRouter, Depends, HTTPException, Path

from api.dependencies import get_tool_registry_manager, require_admin
from api.schemas.tools import (
    AgentListResponse,
    CreateToolUnitRequest,
    MountResponse,
    MountUnitRequest,
    SetCredentialRequest,
    ToolUnitListResponse,
    ToolUnitResponse,
    UpdateToolUnitRequest,
)
from api.services.auth import TokenPayload
from core.tool_registry_manager import ToolRegistryError, ToolRegistryManager

# 凭证占位符路径参数上限 = ToolCredential.placeholder_name 列宽。在边界挡超长值,
# 否则 >128 字符落到 asyncpg 触发 StringDataRightTruncation(DataError)→ 漏出 500;
# 在此校验直接 422(reviewer #10)。
_PLACEHOLDER_MAX = 128

router = APIRouter()


def _map(e: ToolRegistryError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/tools/units", response_model=ToolUnitListResponse)
async def list_units(
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    return ToolUnitListResponse(units=await mgr.list_units())


@router.post("/tools/units", response_model=ToolUnitResponse, status_code=201)
async def create_unit(
    request: CreateToolUnitRequest,
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        return await mgr.create_unit(request.model_dump())
    except ToolRegistryError as e:
        raise _map(e)


@router.get("/tools/units/{name}", response_model=ToolUnitResponse)
async def get_unit(
    name: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        return await mgr.get_unit(name)
    except ToolRegistryError as e:
        raise _map(e)


@router.put("/tools/units/{name}", response_model=ToolUnitResponse)
async def update_unit(
    name: str,
    request: UpdateToolUnitRequest,
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        spec = request.model_dump()
        spec["name"] = name  # 路径为准,body.name 仅占位
        return await mgr.update_unit(name, spec)
    except ToolRegistryError as e:
        raise _map(e)


@router.delete("/tools/units/{name}", status_code=204)
async def delete_unit(
    name: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        await mgr.delete_unit(name)
    except ToolRegistryError as e:
        raise _map(e)


@router.get("/tools/agents", response_model=AgentListResponse)
async def list_agents(
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    return AgentListResponse(agents=await mgr.list_agents())


@router.put("/tools/units/{name}/agents/{agent_name}", response_model=MountResponse)
async def mount_unit(
    name: str,
    agent_name: str,
    request: MountUnitRequest,
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        return await mgr.mount(name, agent_name, request.member_state)
    except ToolRegistryError as e:
        raise _map(e)


@router.delete("/tools/units/{name}/agents/{agent_name}", status_code=204)
async def unmount_unit(
    name: str,
    agent_name: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        await mgr.unmount(name, agent_name)
    except ToolRegistryError as e:
        raise _map(e)


@router.put("/tools/units/{name}/credentials/{placeholder}", status_code=204)
async def set_credential(
    name: str,
    request: SetCredentialRequest,
    placeholder: str = Path(..., max_length=_PLACEHOLDER_MAX),
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        await mgr.set_credential(name, placeholder, request.value)
    except ToolRegistryError as e:
        raise _map(e)


@router.delete("/tools/units/{name}/credentials/{placeholder}", status_code=204)
async def delete_credential(
    name: str,
    placeholder: str = Path(..., max_length=_PLACEHOLDER_MAX),
    _admin: TokenPayload = Depends(require_admin),
    mgr: ToolRegistryManager = Depends(get_tool_registry_manager),
):
    try:
        await mgr.delete_credential(name, placeholder)
    except ToolRegistryError as e:
        raise _map(e)
