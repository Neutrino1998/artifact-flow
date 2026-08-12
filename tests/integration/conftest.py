"""Shared fixtures for external integration tests."""

from __future__ import annotations

import hashlib
import os
import re

import pytest
import pytest_asyncio


try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover - exercised only in a partial dev env
    aioredis = None


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


def _safe_prefix_part(value: str) -> str:
    """Keep Redis test prefixes parseable by the production key helpers."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", value)


@pytest.fixture
def redis_key_prefix(request: pytest.FixtureRequest) -> str:
    """Return a namespace unique to this run, worker, and test.

    The production key format is ``{prefix:entity}:suffix`` and its scan
    parsers treat the first colon as the prefix/entity boundary, so the test
    prefix deliberately contains no colons.
    """
    run_id = os.environ.get("PYTEST_XDIST_TESTRUNUID", f"local-{os.getpid()}")
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    test_id = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:12]
    return "-".join((
        "test",
        _safe_prefix_part(run_id),
        _safe_prefix_part(worker_id),
        test_id,
    ))


@pytest_asyncio.fixture
async def redis_client(redis_key_prefix: str):
    """Provide Redis and remove only this test's keys on teardown.

    Cleanup fans out single-key DEL commands through a non-transactional
    pipeline. Distinct entities use distinct hash slots, so one variadic DEL
    would fail with CROSSSLOT on Redis Cluster.
    """
    if aioredis is None:
        pytest.skip("redis package not installed")

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis not available")

    try:
        yield client
    finally:
        try:
            pattern = f"{{{redis_key_prefix}:*}}:*"
            keys = [key async for key in client.scan_iter(match=pattern, count=100)]
            if keys:
                pipe = client.pipeline(transaction=False)
                for key in keys:
                    pipe.delete(key)
                await pipe.execute()
        finally:
            await client.aclose()
