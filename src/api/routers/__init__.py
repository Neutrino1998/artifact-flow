"""
API Routers

Contains public router modules; admin-only routers live in ``routers.admin``.
"""

from . import (
    admin,
    artifacts,
    auth,
    chat,
    departments,
    meta,
    notifications,
    skills,
    stream,
)

__all__ = [
    "admin",
    "auth",
    "chat",
    "artifacts",
    "departments",
    "meta",
    "notifications",
    "skills",
    "stream",
]
