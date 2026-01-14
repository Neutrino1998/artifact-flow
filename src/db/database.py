"""
数据库管理器
职责：
- 管理数据库连接（支持异步）
- 提供事务上下文管理器
- 初始化数据库 schema
- 配置 WAL 模式提高并发性能
"""

import os
from pathlib import Path
from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
)
from sqlalchemy.pool import StaticPool

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class DatabaseManager:
    """
    数据库管理器
    
    职责：
    - 管理异步数据库连接
    - 提供 session 工厂
    - 初始化数据库 schema
    - 配置 SQLite WAL 模式
    
    使用方式：
        db_manager = DatabaseManager("sqlite+aiosqlite:///data/app.db")
        await db_manager.initialize()
        
        async with db_manager.session() as session:
            # 使用 session 进行数据库操作
            ...
    """
    
    def __init__(
        self,
        database_url: Optional[str] = None,
        echo: bool = False,
    ):
        """
        初始化数据库管理器
        
        Args:
            database_url: 数据库连接 URL，默认为 SQLite
                         格式: sqlite+aiosqlite:///path/to/db.sqlite
            echo: 是否打印 SQL 语句（调试用）
        """
        # 默认数据库路径
        if database_url is None:
            data_dir = Path("data")
            data_dir.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite+aiosqlite:///{data_dir}/artifactflow.db"
        
        self.database_url = database_url
        self.echo = echo
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._initialized = False
        
        logger.info(f"DatabaseManager created with URL: {self._mask_url(database_url)}")
    
    def _mask_url(self, url: str) -> str:
        """隐藏 URL 中的敏感信息"""
        if ":///" in url:
            # SQLite 本地文件
            return url
        # 其他数据库可能包含密码
        if "@" in url:
            parts = url.split("@")
            return f"***@{parts[-1]}"
        return url
    
    def _is_sqlite(self) -> bool:
        """判断是否是 SQLite 数据库"""
        return "sqlite" in self.database_url.lower()
    
    async def initialize(self) -> None:
        """
        初始化数据库
        - 创建引擎和 session 工厂
        - 配置 SQLite WAL 模式
        - 创建所有表
        """
        if self._initialized:
            logger.debug("Database already initialized")
            return
        
        # 创建异步引擎
        engine_kwargs = {
            "echo": self.echo,
        }
        
        # SQLite 特殊配置
        if self._is_sqlite():
            engine_kwargs["connect_args"] = {"check_same_thread": False}
    
            # 区分处理
            if ":memory:" in self.database_url:
                # 测试用内存库 → 必须单连接
                engine_kwargs["poolclass"] = StaticPool
            # else: 文件库 → 用默认策略，支持并发
        
        self._engine = create_async_engine(self.database_url, **engine_kwargs)
        
        # 配置 SQLite WAL 模式
        if self._is_sqlite():
            await self._configure_sqlite_wal()
        
        # 创建 session 工厂
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        
        # 创建所有表
        await self._create_tables()
        
        self._initialized = True
        logger.info("Database initialized successfully")
    
    async def _configure_sqlite_wal(self) -> None:
        """
        配置 SQLite WAL 模式
        
        WAL (Write-Ahead Logging) 模式的优势：
        - 读写可以并发进行
        - 写操作不会阻塞读操作
        - 更好的崩溃恢复能力
        """
        async with self._engine.begin() as conn:
            # 设置 WAL 模式
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            # 设置同步模式为 NORMAL（平衡性能和安全）
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            # 设置缓存大小（负数表示 KB）
            await conn.execute(text("PRAGMA cache_size=-64000"))  # 64MB
            # 启用外键约束
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            
        logger.info("SQLite WAL mode configured")
    
    async def _create_tables(self) -> None:
        """创建所有数据库表"""
        from db.models import Base
        
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        logger.info("Database tables created")
    
    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        获取数据库 session 的上下文管理器
        
        使用方式：
            async with db_manager.session() as session:
                result = await session.execute(select(User))
                ...
        
        Yields:
            AsyncSession: 数据库会话
        """
        if not self._initialized:
            await self.initialize()
        
        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """关闭数据库连接"""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._initialized = False
            logger.info("Database connection closed")
    
    @property
    def engine(self) -> Optional[AsyncEngine]:
        """获取数据库引擎"""
        return self._engine
    
    @property
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._initialized


# ============================================================
# 全局管理器（仅用于向后兼容和测试）
# 生产环境应通过依赖注入使用
# ============================================================

_default_manager: Optional[DatabaseManager] = None


async def get_database_manager(
    database_url: Optional[str] = None,
    echo: bool = False,
) -> DatabaseManager:
    """
    获取数据库管理器实例
    
    注意：此函数仅用于向后兼容和简单场景。
    在生产环境中，应通过依赖注入创建和传递 DatabaseManager 实例。
    
    Args:
        database_url: 数据库连接 URL
        echo: 是否打印 SQL
        
    Returns:
        DatabaseManager 实例
    """
    global _default_manager
    
    if _default_manager is None:
        _default_manager = DatabaseManager(database_url, echo)
        await _default_manager.initialize()
    
    return _default_manager


async def close_database() -> None:
    """关闭全局数据库连接"""
    global _default_manager
    
    if _default_manager:
        await _default_manager.close()
        _default_manager = None


# ============================================================
# 测试支持
# ============================================================

def create_test_database_manager() -> DatabaseManager:
    """
    创建用于测试的内存数据库管理器
    
    Returns:
        使用内存数据库的 DatabaseManager
    """
    return DatabaseManager(
        database_url="sqlite+aiosqlite:///:memory:",
        echo=False,
    )


if __name__ == "__main__":
    import asyncio
    
    async def test():
        """测试数据库管理器"""
        print("\n🧪 DatabaseManager 测试")
        print("=" * 50)
        
        # 使用测试数据库
        db = create_test_database_manager()
        
        try:
            # 初始化
            await db.initialize()
            print("✅ 数据库初始化成功")
            
            # 测试 session
            async with db.session() as session:
                # 执行简单查询
                result = await session.execute(text("SELECT 1"))
                value = result.scalar()
                assert value == 1
                print("✅ Session 工作正常")

            print("\n✅ 所有测试通过!")
            
        finally:
            await db.close()
            print("✅ 数据库连接已关闭")
    
    asyncio.run(test())
