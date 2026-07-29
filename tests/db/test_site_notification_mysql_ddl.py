"""Cross-dialect DDL guards for the singleton site-notification row."""

import importlib

from sqlalchemy import Column
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from db.models import SiteNotificationConfig


def test_model_mysql_ddl_does_not_auto_increment_singleton_id():
    ddl = str(
        CreateTable(SiteNotificationConfig.__table__).compile(dialect=mysql.dialect())
    )

    assert "AUTO_INCREMENT" not in ddl.upper()
    assert "CHECK (id = 1)" in ddl


def test_0003_migration_marks_singleton_id_non_autoincrement(monkeypatch):
    migration = importlib.import_module(
        "db.alembic.versions.0003_site_notification_config"
    )
    captured: dict[str, tuple] = {}

    def capture_table(name, *elements):
        captured[name] = elements
        return object()

    monkeypatch.setattr(migration.op, "create_table", capture_table)
    monkeypatch.setattr(migration.op, "bulk_insert", lambda *_args, **_kwargs: None)

    migration.upgrade()

    id_column = next(
        element
        for element in captured["site_notification_config"]
        if isinstance(element, Column) and element.name == "id"
    )
    assert id_column.autoincrement is False
