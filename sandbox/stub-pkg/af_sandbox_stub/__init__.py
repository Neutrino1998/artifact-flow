"""Trivial pure-Python package — offline-install fixture for the sandbox.

Baked into a wheel at image-build time (on the networked build host) so the
in-sandbox probe can `pip install --no-index --find-links /opt/stub-wheels
af-sandbox-stub` with ZERO network, proving the tier-2/3 offline find-links
delivery path (plan 原则 7) survives gVisor's Sentry. Has no deps and does
nothing — its only job is to be installable offline.
"""


def ping() -> str:
    return "pong"
