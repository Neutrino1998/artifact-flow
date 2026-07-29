"""Shared TLS policy for trusted backend HTTP clients."""

import ssl


def create_outbound_ssl_context() -> ssl.SSLContext:
    """Use the container system trust store while keeping peer checks enabled.

    Production entrypoint processing folds target-local CA certificates into
    the system bundle before Python starts. Passing this context explicitly to
    HTTPX lets callers retain ``trust_env=False`` for proxy/netrc isolation.
    """

    return ssl.create_default_context()
