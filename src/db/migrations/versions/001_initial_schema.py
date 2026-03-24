"""
初始数据库 Schema 迁移脚本

版本: 001
创建时间: 2024-01
描述: 创建所有初始表结构

表结构：
- conversations: 对话表
- messages: 消息表（树结构）
- artifact_sessions: Artifact 会话表
- artifacts: Artifact 表（含乐观锁）
- artifact_versions: Artifact 版本表

注意：此脚本可独立运行，用于在不使用 Alembic 的情况下初始化数据库。
生产环境建议使用 Alembic 进行版本管理。
"""

import asyncio
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# 数据库 Schema 定义（原生 SQL，用于手动迁移场景）
SCHEMA_SQL = """
-- ============================================================
-- 对话表
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    active_branch TEXT,
    title TEXT,
    user_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSON
);

CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations(user_id);

-- ============================================================
-- 消息表
-- ============================================================
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    parent_id TEXT,
    content TEXT NOT NULL,
    response TEXT,
    content_summary TEXT,
    response_summary TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSON,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS ix_messages_parent_id ON messages(parent_id);

-- ============================================================
-- 消息事件表（事件溯源）
-- ============================================================
CREATE TABLE IF NOT EXISTS message_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    agent_name TEXT,
    data JSON,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_message_events_message ON message_events(message_id);

-- ============================================================
-- Artifact 会话表
-- ============================================================
CREATE TABLE IF NOT EXISTS artifact_sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- ============================================================
-- Artifact 表
-- ============================================================
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSON,
    PRIMARY KEY (id, session_id),
    FOREIGN KEY (session_id) REFERENCES artifact_sessions(id) ON DELETE CASCADE
);

-- ============================================================
-- Artifact 版本表
-- ============================================================
CREATE TABLE IF NOT EXISTS artifact_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    update_type TEXT NOT NULL,
    changes JSON,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (artifact_id, session_id, version),
    FOREIGN KEY (artifact_id, session_id) REFERENCES artifacts(id, session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_artifact_versions_artifact 
    ON artifact_versions(artifact_id, session_id);

-- ============================================================
-- 版本记录表（用于追踪迁移历史）
-- ============================================================
CREATE TABLE IF NOT EXISTS schema_versions (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- 记录此次迁移
INSERT OR IGNORE INTO schema_versions (version, description) 
VALUES ('001', '初始 Schema 创建');
"""


async def apply_migration(database_url: str) -> bool:
    """
    应用迁移
    
    Args:
        database_url: 数据库连接 URL
        
    Returns:
        是否成功
    """
    print(f"🚀 开始应用迁移 001_initial_schema...")
    
    engine = create_async_engine(database_url)
    
    try:
        async with engine.begin() as conn:
            # 检查是否已应用
            try:
                result = await conn.execute(
                    text("SELECT version FROM schema_versions WHERE version = '001'")
                )
                if result.scalar():
                    print("ℹ️  迁移 001 已应用，跳过")
                    return True
            except Exception:
                # 表不存在，继续执行
                pass
            
            # 执行 Schema 创建
            for statement in SCHEMA_SQL.split(";"):
                statement = statement.strip()
                if statement:
                    await conn.execute(text(statement))
            
            print("✅ 迁移 001_initial_schema 应用成功")
            return True
            
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        return False
        
    finally:
        await engine.dispose()


async def check_migration_status(database_url: str) -> dict:
    """
    检查迁移状态
    
    Args:
        database_url: 数据库连接 URL
        
    Returns:
        迁移状态信息
    """
    engine = create_async_engine(database_url)
    
    try:
        async with engine.begin() as conn:
            try:
                result = await conn.execute(
                    text("SELECT version, applied_at, description FROM schema_versions ORDER BY version")
                )
                versions = [
                    {"version": row[0], "applied_at": str(row[1]), "description": row[2]}
                    for row in result.fetchall()
                ]
                return {"applied_versions": versions}
            except Exception:
                return {"applied_versions": [], "error": "schema_versions table not found"}
                
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import sys
    
    async def main():
        print("\n🔧 ArtifactFlow 数据库迁移工具")
        print("=" * 50)
        
        # 默认数据库路径
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite+aiosqlite:///{data_dir}/artifactflow.db"
        
        if len(sys.argv) > 1:
            command = sys.argv[1]
            
            if command == "apply":
                await apply_migration(database_url)
                
            elif command == "status":
                status = await check_migration_status(database_url)
                print("\n📊 迁移状态:")
                if status.get("error"):
                    print(f"   ⚠️  {status['error']}")
                else:
                    for v in status["applied_versions"]:
                        print(f"   - {v['version']}: {v['description']} (applied: {v['applied_at']})")
                    if not status["applied_versions"]:
                        print("   （无已应用的迁移）")
                        
            else:
                print(f"❌ 未知命令: {command}")
                print("   可用命令: apply, status")
        else:
            print("用法:")
            print("  python 001_initial_schema.py apply   # 应用迁移")
            print("  python 001_initial_schema.py status  # 查看状态")
    
    asyncio.run(main())
