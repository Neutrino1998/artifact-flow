"""Authentication and current-user HTTP endpoints."""

import asyncio
import json
import math

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from config import config
from api.dependencies import (
    get_current_user,
    get_login_rate_limiter,
    get_remote_auth_manager,
    get_remote_bearer_config,
    get_sso_start_rate_limiter,
    get_user_account_manager,
)
from api.schemas.auth import (
    AuthPublicConfigResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    SsoExchangeRequest,
    SsoPublicProviderConfig,
    SsoStartResponse,
    UpdateMyProfileRequest,
    UserInfo,
)
from api.services.auth import TokenPayload, create_access_token
from api.services.sso_rate_limiter import SsoStartRateLimitError
from core.management.user_account_manager import (
    CurrentPasswordIncorrectError,
    InactiveUserError,
    InvalidCredentialsError,
    PasswordReusedError,
    PasswordUnavailableError,
    UserAccountError,
    UserAccountManager,
    UserAccountNotFoundError,
)
from core.management.remote_auth_manager import (
    RemoteAuthDisabledError,
    RemoteAuthIdentityDisabledError,
    RemoteAuthManager,
    RemoteAuthPersistenceError,
    RemoteAuthStateError,
)
from core.security.remote_bearer_userinfo import (
    RemoteBearerCredentialsRejected,
    RemoteBearerProtocolError,
    RemoteBearerUpstreamUnavailable,
)
from core.security.sso_state import SsoStateCapacityError
from utils.logger import get_logger

router = APIRouter()
logger = get_logger("ArtifactFlow")

_SSO_BINDING_COOKIE = "af_sso_binding"
_SSO_EXCHANGE_BODY_MAX_BYTES = 20 * 1024


async def _parse_sso_exchange(http_request: Request) -> SsoExchangeRequest:
    chunks: list[bytes] = []
    total = 0
    async for chunk in http_request.stream():
        total += len(chunk)
        if total > _SSO_EXCHANGE_BODY_MAX_BYTES:
            logger.warning("SSO exchange request body exceeded the fixed size limit")
            raise HTTPException(status_code=400, detail="Invalid exchange request")
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
        return SsoExchangeRequest.model_validate(payload)
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
        TypeError,
        RecursionError,
    ):
        # Do not log ValidationError: its structured input may contain the bearer.
        logger.warning("SSO exchange request body had an invalid JSON shape")
        raise HTTPException(status_code=400, detail="Invalid exchange request") from None


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
    if isinstance(exc, PasswordUnavailableError):
        return HTTPException(status_code=403, detail=exc.detail)
    if isinstance(exc, (CurrentPasswordIncorrectError, PasswordReusedError)):
        return HTTPException(status_code=400, detail=exc.detail)
    if isinstance(exc, (InvalidCredentialsError, InactiveUserError)):
        return HTTPException(status_code=401, detail=exc.detail)
    return HTTPException(status_code=400, detail=exc.detail)


@router.get("/config", response_model=AuthPublicConfigResponse)
async def get_auth_config(
    response: Response,
    provider_config=Depends(get_remote_bearer_config),
):
    """Anonymous, read-only capabilities for the login page."""
    response.headers["Cache-Control"] = "no-store"
    if not provider_config.enabled:
        sso = SsoPublicProviderConfig(enabled=False)
    else:
        sso = SsoPublicProviderConfig(
            enabled=True,
            provider_id=provider_config.provider.id,
            display_name=provider_config.provider.display_name,
            token_param=provider_config.login.token_param,
        )
    return AuthPublicConfigResponse(sso=sso)


@router.post("/sso/start", response_model=SsoStartResponse)
async def start_sso(
    http_request: Request,
    response: Response,
    manager: RemoteAuthManager = Depends(get_remote_auth_manager),
    rate_limiter=Depends(get_sso_start_rate_limiter),
):
    response.headers["Cache-Control"] = "no-store"
    if not manager.is_enabled():
        raise HTTPException(
            status_code=404, detail="Enterprise authentication is unavailable"
        )

    try:
        await rate_limiter.admit(_client_ip(http_request))
    except SsoStartRateLimitError as exc:
        headers = {"Retry-After": str(exc.retry_after)}
        if exc.scope == "ip":
            raise HTTPException(
                status_code=429,
                detail="Too many enterprise authentication attempts",
                headers=headers,
            ) from exc
        logger.warning("SSO start global admission exhausted")
        raise HTTPException(
            status_code=503,
            detail="Enterprise authentication is temporarily unavailable",
            headers=headers,
        ) from exc
    except Exception:
        logger.exception("SSO start admission check failed")
        raise HTTPException(
            status_code=503,
            detail="Enterprise authentication is temporarily unavailable",
        ) from None

    try:
        authorization_url, issued = await manager.start()
    except RemoteAuthDisabledError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except SsoStateCapacityError as exc:
        logger.error("SSO state store admission failed: capacity exhausted")
        raise HTTPException(
            status_code=503, detail="Enterprise authentication is temporarily unavailable"
        ) from exc
    except Exception:
        logger.exception("SSO state issuance failed")
        raise HTTPException(
            status_code=500, detail="Enterprise authentication could not be started"
        ) from None

    secure = manager.callback_uses_https()
    response.set_cookie(
        _SSO_BINDING_COOKIE,
        issued.browser_binding,
        max_age=issued.expires_in,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/v1/auth/sso/exchange",
    )
    return SsoStartResponse(
        authorization_url=authorization_url, expires_in=issued.expires_in
    )


@router.post(
    "/sso/exchange",
    response_model=LoginResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["state", "upstream_token"],
                        "properties": {
                            "state": {"type": "string", "minLength": 1, "maxLength": 256},
                            "upstream_token": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 16384,
                                "writeOnly": True,
                            },
                        },
                    }
                }
            },
        }
    },
)
async def exchange_sso(
    http_request: Request,
    response: Response,
    manager: RemoteAuthManager = Depends(get_remote_auth_manager),
):
    request = await _parse_sso_exchange(http_request)
    browser_binding = http_request.cookies.get(_SSO_BINDING_COOKIE, "")
    try:
        result = await manager.exchange(
            state=request.state,
            browser_binding=browser_binding,
            upstream_token=request.upstream_token,
        )
    except RemoteAuthDisabledError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except (RemoteAuthStateError, RemoteBearerCredentialsRejected) as exc:
        raise HTTPException(status_code=401, detail="Enterprise authentication failed") from exc
    except RemoteAuthIdentityDisabledError as exc:
        raise HTTPException(status_code=401, detail=exc.detail) from exc
    except RemoteBearerProtocolError as exc:
        logger.error("SSO userinfo protocol failure: %s", exc)
        raise HTTPException(
            status_code=502, detail="Enterprise identity response was invalid"
        ) from exc
    except RemoteBearerUpstreamUnavailable as exc:
        logger.error("SSO userinfo upstream unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail="Enterprise authentication is temporarily unavailable"
        ) from exc
    except RemoteAuthPersistenceError as exc:
        logger.exception("SSO local identity synchronization failed")
        raise HTTPException(
            status_code=500, detail="Enterprise authentication could not be completed"
        ) from exc
    except Exception:
        logger.exception("Unexpected SSO exchange failure")
        raise HTTPException(
            status_code=500, detail="Enterprise authentication could not be completed"
        ) from None

    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        _SSO_BINDING_COOKIE,
        path="/api/v1/auth/sso/exchange",
        secure=manager.callback_uses_https(),
        httponly=True,
        samesite="lax",
    )
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
        expires_in=config.JWT_EXPIRY_SECONDS,
        user=UserInfo(**profile),
    )


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
        expires_in=config.JWT_EXPIRY_SECONDS,
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
