"""
数据库管理器
职责：
- 管理数据库连接（支持异步）
- 提供事务上下文管理器
- 初始化数据库 schema
- 配置 WAL 模式提高并发性能（SQLite）
- 连接池管理（MySQL/PostgreSQL）
"""

from typing import Any, Dict, List, Optional, AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
)

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class DatabaseManager:
    """
    数据库管理器

    SQLite 用于开发/测试，MySQL/PG 用于生产。
    差异仅在 initialize() 的引擎配置分支。

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
        database_urls: Optional[List[str]] = None,
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: int = 30,
        pool_recycle: int = 300,
        pool_pre_ping: bool = True,
    ):
        """
        初始化数据库管理器

        Args:
            database_url: 数据库连接 URL，默认为 SQLite
            database_urls: 多地址列表（优先级高于 database_url），用于多 PX failover
            echo: 是否打印 SQL 语句（调试用）
            pool_size: 连接池大小（仅 MySQL/PG）
            max_overflow: 连接池最大溢出（仅 MySQL/PG）
            pool_timeout: 连接池获取超时秒数（仅 MySQL/PG）
            pool_recycle: 连接回收秒数（仅 MySQL/PG）
            pool_pre_ping: 是否启用连接存活检测（仅 MySQL/PG）
        """
        assert database_url, "database_url must be provided"
        self.database_url = database_url
        self._database_urls = database_urls
        self.echo = echo
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_timeout = pool_timeout
        self._pool_recycle = pool_recycle
        self._pool_pre_ping = pool_pre_ping
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

    @staticmethod
    def _parse_db_url(url: str) -> Dict[str, Any]:
        """从 SQLAlchemy URL 解析出 asyncmy.connect kwargs（含 query string 参数）"""
        u = make_url(url)
        result: Dict[str, Any] = {
            "host": u.host or "127.0.0.1",
            "port": u.port or 3306,
            "db": u.database or "",
        }
        if u.username:
            result["user"] = u.username
        if u.password:
            result["password"] = u.password
        # query string 参数（charset, ssl_ca, ssl_cert 等）直接透传
        if u.query:
            result.update(u.query)
        return result

    async def initialize(self) -> None:
        """
        初始化数据库
        - 创建引擎和 session 工厂
        - SQLite: 配置 WAL 模式 + create_all 自动建表
        - MySQL/PG: 配置连接池，依赖 alembic upgrade head 建表
        """
        if self._initialized:
            logger.debug("Database already initialized")
            return

        # 创建异步引擎
        engine_kwargs = {
            "echo": self.echo,
        }

        if self._is_sqlite():
            engine_kwargs["connect_args"] = {"check_same_thread": False}

            if ":memory:" in self.database_url:
                # 测试用内存库 → 必须单连接
                from sqlalchemy.pool import StaticPool
                engine_kwargs["poolclass"] = StaticPool
            # else: 文件库 → 用默认策略，支持并发
        else:
            # MySQL/PostgreSQL 连接池配置
            engine_kwargs["pool_size"] = self._pool_size
            engine_kwargs["max_overflow"] = self._max_overflow
            engine_kwargs["pool_timeout"] = self._pool_timeout
            engine_kwargs["pool_recycle"] = self._pool_recycle
            engine_kwargs["pool_pre_ping"] = self._pool_pre_ping

            # 多 PX failover：primary-first 尝试
            if self._database_urls and len(self._database_urls) > 1:
                parsed_urls = [self._parse_db_url(u) for u in self._database_urls]

                async def _failover_creator():
                    """Primary-first: 按配置顺序尝试，首个成功即返回"""
                    import asyncmy
                    errors = []
                    for target in parsed_urls:  # 固定顺序，不轮转
                        try:
                            return await asyncmy.connect(**target, connect_timeout=5)
                        except Exception as e:
                            errors.append((target["host"], e))
                            logger.warning(f"DB connect failed: {target['host']}: {e}")
                    raise ConnectionError(
                        f"All DB nodes unreachable: {[(h, str(e)) for h, e in errors]}"
                    )

                engine_kwargs["async_creator"] = _failover_creator
                logger.info(f"Multi-PX failover enabled ({len(self._database_urls)} addresses)")

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

        if self._is_sqlite():
            # 开发模式：自动建表
            await self._create_tables()
        else:
            # 生产模式：依赖 alembic，仅检查 alembic_version 表
            await self._check_alembic_version()

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
            # 设置忙等待超时（毫秒），避免 database is locked 错误
            await conn.execute(text("PRAGMA busy_timeout=5000"))  # 5 秒

        logger.info("SQLite WAL mode configured")

    async def _create_tables(self) -> None:
        """创建所有数据库表（SQLite 开发模式）"""
        from db.models import Base

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Database tables created")

    async def _check_alembic_version(self) -> None:
        """
        检查 alembic_version 表并记录当前 revision（生产模式）。

        - 表不存在 → RuntimeError（迁移未执行）
        - 表为空 → RuntimeError（迁移状态异常）
        - 连接/鉴权等其他异常 → 原样抛出，让启动 fail fast

        注意：此方法只验证迁移是否执行过，不校验 revision 是否与代码期望的
        head 一致。Revision 与 head 的匹配校验应在部署流程中通过
        `alembic current --check-heads` 完成（CI/CD pipeline）。
        """
        from sqlalchemy import inspect as sa_inspect

        async with self._engine.connect() as conn:
            # 检查表是否存在
            has_table = await conn.run_sync(
                lambda sync_conn: sa_inspect(sync_conn).has_table("alembic_version")
            )
            if not has_table:
                raise RuntimeError(
                    "alembic_version table not found. "
                    "Run 'alembic upgrade head' before starting the server."
                )

            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            row = result.first()
            if row is None:
                raise RuntimeError(
                    "alembic_version table is empty. "
                    "Run 'alembic upgrade head' to apply migrations."
                )
            logger.info(f"Database schema revision: {row[0]}")

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
