"""
数据库管理器
职责：
- 管理数据库连接（支持异步）
- 提供事务上下文管理器
- 初始化数据库 schema
- 配置 WAL 模式提高并发性能（SQLite）
- 连接池管理（MySQL/PostgreSQL）
"""

from typing import Any, Dict, List, Optional, AsyncGenerator, Tuple
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

    # Query params we know how to translate per driver. Unknown keys are
    # rejected at init to avoid silently dropping DSN options when moving
    # from DATABASE_URL (SQLAlchemy-parsed) to DATABASE_URLS (raw probes).
    #
    # Each entry describes how to apply the param to the driver's connect()
    # call — the raw path bypasses SQLAlchemy's dialect translation, so every
    # param must either be a real connect() kwarg or be explicitly routed.
    _SSL_FILE_KEYS = frozenset({"ssl_ca", "ssl_cert", "ssl_key"})

    # PostgreSQL (asyncpg.connect): sslmode handled separately (→ ssl=).
    # application_name must go through server_settings, not a direct kwarg.
    _PG_DIRECT_KWARGS = {
        "command_timeout": float,    # asyncpg expects float/int, not str
    }
    _PG_SERVER_SETTINGS = frozenset({"application_name"})

    # MySQL (aiomysql.connect). read_timeout/write_timeout are NOT aiomysql
    # kwargs (PyMySQL has them, aiomysql doesn't). connect_timeout is reserved
    # for the 5s probe in _failover_creator — DSN override would conflict with
    # Python kwarg duplication rules.
    _MYSQL_DIRECT_KWARGS = {
        "charset": str,
        "autocommit": "bool",        # coerce 'true'/'false'/'1'/'0'
        "unix_socket": str,
        "init_command": str,
        "program_name": str,
    }

    @staticmethod
    def _coerce_bool(value: str, *, key: str) -> bool:
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
        raise ValueError(
            f"DSN query param '{key}' expects a boolean (true/false/1/0), got {value!r}"
        )

    @staticmethod
    def _parse_db_url(url: str) -> Tuple[str, Dict[str, Any]]:
        """Parse SQLAlchemy URL into driver-specific connect kwargs for failover probes.

        Supported DSN query params (others raise ValueError to prevent silent drops):
        - Both drivers: ``ssl_ca`` / ``ssl_cert`` / ``ssl_key`` (file paths → SSLContext)
        - PostgreSQL:
            - ``sslmode`` (→ asyncpg ``ssl=`` string)
            - ``command_timeout`` (coerced to float)
            - ``application_name`` (routed to asyncpg ``server_settings={...}``)
        - MySQL:
            - ``charset``, ``unix_socket``, ``init_command``, ``program_name`` (str)
            - ``autocommit`` (coerced bool)

        ``connect_timeout`` is intentionally NOT accepted on MySQL — the 5s
        probe timeout in ``_failover_creator`` is an architectural choice and
        would collide with the DSN value via Python kwarg duplication.

        Returns:
            (driver, kwargs) where driver is "mysql" or "postgres" and kwargs
            are shaped for aiomysql.connect or asyncpg.connect respectively.
        """
        u = make_url(url)
        backend = u.get_backend_name()
        is_pg = backend.startswith("postgres")

        if is_pg:
            result: Dict[str, Any] = {
                "host": u.host or "127.0.0.1",
                "port": u.port or 5432,
                "database": u.database or "",
            }
        else:
            result = {
                "host": u.host or "127.0.0.1",
                "port": u.port or 3306,
                "db": u.database or "",
            }

        if u.username:
            result["user"] = u.username
        if u.password:
            result["password"] = u.password

        if u.query:
            ssl_file_params: Dict[str, str] = {}
            pg_sslmode: Optional[str] = None
            pg_server_settings: Dict[str, str] = {}

            direct_kwargs = (
                DatabaseManager._PG_DIRECT_KWARGS if is_pg
                else DatabaseManager._MYSQL_DIRECT_KWARGS
            )

            for key, value in u.query.items():
                if key in DatabaseManager._SSL_FILE_KEYS:
                    ssl_file_params[key] = value
                elif is_pg and key == "sslmode":
                    pg_sslmode = value
                elif is_pg and key in DatabaseManager._PG_SERVER_SETTINGS:
                    pg_server_settings[key] = value
                elif key in direct_kwargs:
                    coerce = direct_kwargs[key]
                    if coerce == "bool":
                        result[key] = DatabaseManager._coerce_bool(value, key=key)
                    else:
                        try:
                            result[key] = coerce(value)
                        except (TypeError, ValueError) as e:
                            raise ValueError(
                                f"DSN query param '{key}' cannot be coerced to "
                                f"{coerce.__name__}: {value!r}"
                            ) from e
                else:
                    driver_name = "postgres" if is_pg else "mysql"
                    if is_pg:
                        supported = ", ".join(sorted(
                            {"ssl_ca", "ssl_cert", "ssl_key", "sslmode"}
                            | set(DatabaseManager._PG_DIRECT_KWARGS)
                            | set(DatabaseManager._PG_SERVER_SETTINGS)
                        ))
                    else:
                        supported = ", ".join(sorted(
                            {"ssl_ca", "ssl_cert", "ssl_key"}
                            | set(DatabaseManager._MYSQL_DIRECT_KWARGS)
                        ))
                    raise ValueError(
                        f"Unsupported DSN query param '{key}' for {driver_name} "
                        f"failover path. Supported: {supported}"
                    )

            # PG: sslmode (string) and ssl_* (file paths → SSLContext) are two
            # different ways to configure TLS. Mixing them has ambiguous
            # semantics (e.g. sslmode=disable + ssl_ca= would silently enable
            # TLS, reversing the user's intent) and asyncpg does not replicate
            # libpq's prefer/allow fallback behaviour, so merged semantics
            # cannot be honoured faithfully. Reject the combination.
            if is_pg and ssl_file_params and pg_sslmode is not None:
                raise ValueError(
                    "PostgreSQL DSN cannot mix 'sslmode' with file-based SSL "
                    "params (ssl_ca/ssl_cert/ssl_key). Use either "
                    "'?sslmode=require' (or other mode) alone, or "
                    "'?ssl_ca=/path&ssl_cert=/path&ssl_key=/path' alone."
                )

            if ssl_file_params:
                import ssl
                ctx = ssl.create_default_context()
                if "ssl_ca" in ssl_file_params:
                    ctx.load_verify_locations(cafile=ssl_file_params["ssl_ca"])
                if "ssl_cert" in ssl_file_params and "ssl_key" in ssl_file_params:
                    ctx.load_cert_chain(
                        certfile=ssl_file_params["ssl_cert"],
                        keyfile=ssl_file_params["ssl_key"],
                    )
                result["ssl"] = ctx
            elif pg_sslmode is not None:
                # asyncpg accepts the mode string directly via ssl=
                result["ssl"] = pg_sslmode

            if pg_server_settings:
                result["server_settings"] = pg_server_settings

        return ("postgres" if is_pg else "mysql", result)

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

            # 多地址 failover：primary-first 尝试
            if self._database_urls and len(self._database_urls) > 1:
                parsed_urls = [self._parse_db_url(u) for u in self._database_urls]
                drivers = {d for d, _ in parsed_urls}
                if len(drivers) > 1:
                    raise ValueError(
                        f"database_urls must use a single driver, got: {drivers}"
                    )
                driver = next(iter(drivers))

                async def _failover_creator():
                    """Primary-first: 按配置顺序尝试，首个成功即返回"""
                    if driver == "postgres":
                        import asyncpg
                        connect_fn = asyncpg.connect
                        timeout_kw = "timeout"
                    else:
                        import aiomysql
                        connect_fn = aiomysql.connect
                        timeout_kw = "connect_timeout"

                    errors = []
                    for _, kwargs in parsed_urls:  # 固定顺序，不轮转
                        try:
                            return await connect_fn(**kwargs, **{timeout_kw: 5})
                        except Exception as e:
                            errors.append((kwargs["host"], e))
                            logger.warning(f"DB connect failed: {kwargs['host']}: {e}")
                    raise ConnectionError(
                        f"All DB nodes unreachable: {[(h, str(e)) for h, e in errors]}"
                    )

                engine_kwargs["async_creator"] = _failover_creator
                logger.info(
                    f"Multi-address failover enabled "
                    f"({len(self._database_urls)} addresses, driver={driver})"
                )

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

    async def with_retry(self, fn, *, max_retries=3, base_delay=1.0):
        """
        用 fresh session 重试 DB 瞬断异常（连接断开/事务回滚）。

        fn: async (session: AsyncSession) -> result
        每次 attempt 创建独立 session，仅用于读操作或幂等写操作。
        """
        import asyncio
        from sqlalchemy.exc import OperationalError, DisconnectionError

        for attempt in range(max_retries + 1):
            try:
                async with self.session() as session:
                    return await fn(session)
            except (OperationalError, DisconnectionError) as e:
                if attempt == max_retries:
                    raise
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"DB transient error (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {delay:.1f}s: {e}"
                )
                await asyncio.sleep(delay)


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
