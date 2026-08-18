"""Authentication identity primitives shared by account and SSO use cases."""

LOCAL_AUTH_PROVIDER = "local_password"


def local_auth_subject(username: str) -> str:
    """The local-password provider uses the login username as its subject."""
    return username


def can_change_password(auth_provider: str) -> bool:
    return auth_provider == LOCAL_AUTH_PROVIDER
