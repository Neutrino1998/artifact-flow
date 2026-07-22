"""Admin-managed runtime site config.

The frontend serves the configured runtime site directory as static /site/*.json
files. This manager provides a small authenticated write path for those files
without introducing a database table for low-frequency site notices.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Iterable

from api.schemas.site_config import SiteNotification
from config import config


MISSING_REVISION = "missing"


class SiteConfigError(Exception):
    status_code = 500


class SiteConfigConflictError(SiteConfigError):
    status_code = 409


class SiteConfigInvalidError(SiteConfigError):
    status_code = 500


class SiteConfigWriteError(SiteConfigError):
    status_code = 500


class SiteConfigManager:
    def __init__(self, site_dir: str | None = None):
        self._site_dir = Path(site_dir or config.SITE_CONFIG_DIR)

    @property
    def notifications_path(self) -> Path:
        return self._site_dir / "notifications.json"

    @property
    def _notifications_lock_path(self) -> Path:
        return self._site_dir / ".notifications.lock"

    async def get_notifications(self) -> dict:
        return await asyncio.to_thread(self._get_notifications_sync)

    async def update_notifications(
        self,
        notifications: Iterable[SiteNotification],
        *,
        expected_revision: str,
    ) -> dict:
        return await asyncio.to_thread(
            self._update_notifications_sync,
            list(notifications),
            expected_revision,
        )

    def _get_notifications_sync(self) -> dict:
        raw, revision = self._read_raw()
        if raw is None:
            return {"notifications": [], "revision": revision}
        try:
            parsed = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            raise SiteConfigInvalidError(
                f"notifications.json is not valid JSON: {e}"
            ) from e
        if not isinstance(parsed, list):
            raise SiteConfigInvalidError("notifications.json must contain a JSON array")

        notifications: list[dict] = []
        seen: set[str] = set()
        try:
            for item in parsed:
                notification = SiteNotification.model_validate(item)
                if notification.id in seen:
                    raise ValueError(f"duplicate notification id: {notification.id}")
                seen.add(notification.id)
                notifications.append(notification.model_dump(exclude_none=True))
        except Exception as e:
            raise SiteConfigInvalidError(f"notifications.json schema is invalid: {e}") from e
        return {"notifications": notifications, "revision": revision}

    def _update_notifications_sync(
        self,
        notifications: list[SiteNotification],
        expected_revision: str,
    ) -> dict:
        with self._notifications_lock():
            current_revision = self._read_revision_only()
            if expected_revision != current_revision:
                raise SiteConfigConflictError(
                    "notifications.json changed since it was loaded; refresh and retry"
                )

            payload = [
                n.model_dump(exclude_none=True)
                for n in notifications
            ]
            raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            revision = self._hash(raw)
            self._atomic_write(raw)
            return {"notifications": payload, "revision": revision}

    @contextmanager
    def _notifications_lock(self):
        self._site_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self._notifications_lock_path.open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as e:
            raise SiteConfigWriteError(f"failed to lock notifications.json: {e}") from e

    def _read_raw(self) -> tuple[bytes | None, str]:
        path = self.notifications_path
        if not path.exists():
            return None, MISSING_REVISION
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise SiteConfigInvalidError(f"failed to read notifications.json: {e}") from e
        return raw, self._hash(raw)

    def _read_revision_only(self) -> str:
        raw, revision = self._read_raw()
        if raw is None:
            return revision
        return self._hash(raw)

    def _atomic_write(self, raw: bytes) -> None:
        self._site_dir.mkdir(parents=True, exist_ok=True)
        tmp_name = ""
        owner: tuple[int, int] | None = None
        mode = 0o644
        try:
            target_stat = self.notifications_path.stat()
            owner = (target_stat.st_uid, target_stat.st_gid)
            mode = stat.S_IMODE(target_stat.st_mode)
        except FileNotFoundError:
            try:
                dir_stat = self._site_dir.stat()
                owner = (dir_stat.st_uid, dir_stat.st_gid)
            except OSError:
                owner = None
        except OSError:
            owner = None

        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=self._site_dir,
                prefix=".notifications.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_name = tmp.name
                tmp.write(raw)
                tmp.flush()
                try:
                    os.fchmod(tmp.fileno(), mode)
                    if owner is not None:
                        os.fchown(tmp.fileno(), owner[0], owner[1])
                except OSError:
                    pass
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self.notifications_path)
            try:
                dir_fd = os.open(self._site_dir, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except OSError as e:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise SiteConfigWriteError(f"failed to write notifications.json: {e}") from e

    @staticmethod
    def _hash(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()
