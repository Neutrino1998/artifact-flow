#!/usr/bin/env python3
"""
Bootstrap admin user — run once during initial setup.

Usage:
    python scripts/create_admin.py admin
    python scripts/create_admin.py admin --password mypassword
    python scripts/create_admin.py admin --no-claim
"""

import sys
import asyncio
import argparse
from pathlib import Path
from getpass import getpass
from uuid import uuid4

# 与 run_server.py 对齐：注入 src 路径 + load_dotenv
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv
load_dotenv()


async def main(username: str, password: str, no_claim: bool) -> None:
    from sqlalchemy import update
    from db.database import DatabaseManager
    from db.models import User, Conversation
    from repositories.user_repo import UserRepository
    from api.services.auth import hash_password
    from api.config import config

    db = DatabaseManager(config.DATABASE_URL)
    await db.initialize()

    try:
        async with db.session() as session:
            user_repo = UserRepository(session)

            # 检查用户名是否已存在
            existing = await user_repo.get_by_username(username)
            if existing:
                user_id = existing.id
                # 确保是 admin 且已激活
                changed = []
                if existing.role != "admin":
                    existing.role = "admin"
                    changed.append("role → admin")
                if not existing.is_active:
                    existing.is_active = True
                    changed.append("is_active → True")
                if changed:
                    await user_repo.update(existing)
                    print(f"User '{username}' already exists (id={user_id}), upgraded: {', '.join(changed)}")
                else:
                    print(f"User '{username}' already exists (id={user_id}), already admin")
            else:
                user_id = f"user-{uuid4().hex}"
                user = User(
                    id=user_id,
                    username=username,
                    hashed_password=hash_password(password),
                    display_name=username,
                    role="admin",
                    is_active=True,
                )
                await user_repo.add(user)
                print(f"Admin user created: {username} (id={user_id})")

            # 将所有 user_id IS NULL 的 conversation 归属到新 admin
            if not no_claim:
                result = await session.execute(
                    update(Conversation)
                    .where(Conversation.user_id.is_(None))
                    .values(user_id=user_id)
                )
                await session.commit()
                claimed = result.rowcount
                if claimed > 0:
                    print(f"Claimed {claimed} existing conversation(s) with no owner")
                else:
                    print("No unclaimed conversations found")

    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create admin user for ArtifactFlow")
    parser.add_argument("username", help="Admin username")
    parser.add_argument("--password", help="Admin password (prompted if not given)")
    parser.add_argument(
        "--no-claim",
        action="store_true",
        help="Skip claiming existing conversations with no owner",
    )

    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass("Password: ")
        confirm = getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match")
            sys.exit(1)

    if len(password) < 4:
        print("Password must be at least 4 characters")
        sys.exit(1)

    asyncio.run(main(args.username, password, args.no_claim))
