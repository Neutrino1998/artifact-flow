"""Authentication and current-user HTTP endpoints."""

import asyncio
import math

from fastapi import APIRouter, Depends, HTTPException, Request

from config import config
from api.dependencies import (
    get_current_user,
    get_login_rate_limiter,
    get_user_account_manager,
)
from api.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UpdateMyProfileRequest,
    UserInfo,
)
from api.services.auth import TokenPayload, create_access_token
from core.management.user_account_manager import (
    CurrentPasswordIncorrectError,
    InactiveUserError,
    InvalidCredentialsError,
    PasswordReusedError,
    UserAccountError,
    UserAccountManager,
    UserAccountNotFoundError,
)

router = APIRouter()


def _client_ip(request: Request) -> str:
    """Resolve the trusted proxy-overwritten client IP for login throttling."""
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _login_retry_detail(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    unit = "minute" if minutes == 1 else "minutes"
    return f"Too many failed login attempts. Please try again in {minutes} {unit}."


def _map_account_error(exc: UserAccountError) -> HTTPException:
    if isinstance(exc, UserAccountNotFoundError):
        return HTTPException(status_code=404, detail=exc.detail)
    if isinstance(
        exc, (CurrentPasswordIncorrectError, PasswordReusedError)
    ):
        return HTTPException(status_code=400, detail=exc.detail)
    if isinstance(exc, (InvalidCredentialsError, InactiveUserError)):
        return HTTPException(status_code=401, detail=exc.detail)
    return HTTPException(status_code=400, detail=exc.detail)


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    manager: UserAccountManager = Depends(get_user_account_manager),
    rate_limiter=Depends(get_login_rate_limiter),
):
    username = request.username.strip()
    user_key = f"user:{username}"
    ip_key = f"ip:{_client_ip(http_request)}"
    user_retry_after, ip_retry_after = await asyncio.gather(
        rate_limiter.retry_after(user_key), rate_limiter.retry_after(ip_key)
    )
    retry_after = max(
        (
            seconds
            for seconds in (user_retry_after, ip_retry_after)
            if seconds is not None
        ),
        default=None,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=_login_retry_detail(retry_after),
            headers={"Retry-After": str(retry_after)},
        )

    try:
        result = await manager.authenticate(username, request.password)
    except InvalidCredentialsError as exc:
        await asyncio.gather(
            rate_limiter.record_failure(user_key),
            rate_limiter.record_failure(ip_key),
        )
        raise _map_account_error(exc) from exc
    except UserAccountError as exc:
        raise _map_account_error(exc) from exc

    await rate_limiter.reset(user_key)
    profile = result["profile"]
    token = create_access_token(
        profile["id"],
        profile["username"],
        profile["role"],
        result["password_version"],
    )
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=config.JWT_EXPIRY_DAYS * 86400,
        user=UserInfo(**profile),
    )


@router.get("/me", response_model=UserInfo)
async def get_me(
    current_user: TokenPayload = Depends(get_current_user),
    manager: UserAccountManager = Depends(get_user_account_manager),
):
    try:
        return UserInfo(**await manager.get_profile(current_user.user_id))
    except UserAccountError as exc:
        raise _map_account_error(exc) from exc


@router.post("/me/password", status_code=204)
async def change_my_password(
    request: ChangePasswordRequest,
    current_user: TokenPayload = Depends(get_current_user),
    manager: UserAccountManager = Depends(get_user_account_manager),
):
    try:
        await manager.change_password(
            current_user.user_id,
            current_password=request.current_password,
            new_password=request.new_password,
        )
    except UserAccountError as exc:
        raise _map_account_error(exc) from exc


@router.patch("/me", response_model=UserInfo)
async def update_my_profile(
    request: UpdateMyProfileRequest,
    current_user: TokenPayload = Depends(get_current_user),
    manager: UserAccountManager = Depends(get_user_account_manager),
):
    try:
        return UserInfo(
            **await manager.update_profile(
                current_user.user_id, display_name=request.display_name
            )
        )
    except UserAccountError as exc:
        raise _map_account_error(exc) from exc
