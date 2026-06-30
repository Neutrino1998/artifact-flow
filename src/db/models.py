"""
SQLAlchemy ORM 模型定义

表结构设计遵循改造方案 Section 4.2：
- conversations: 对话表
- messages: 消息表（树结构）
- artifact_sessions: Artifact 会话表
- artifacts: Artifact 表（含乐观锁）
- artifact_versions: Artifact 版本表
"""

from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    Boolean,
    String,
    Text,
    Integer,
    LargeBinary,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    JSON,
    UniqueConstraint,
    Index,
    Computed,
    func,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# 二进制列的类型长度 hint（见 ArtifactBlob.data）：仅用于在 MySQL 上把列推到
# LONGBLOB 这一 tier，PG/SQLite 忽略长度。app 侧 config.ARTIFACT_BLOB_MAX_BYTES
# 才是真正的大小闸门。取 >16MB(MEDIUMBLOB 上限)即可保证 LONGBLOB；这里取 100MB
# 与当前 cap 对齐、自文档化(LONGBLOB 物理可达 4GB,M 只选 tier 不限长)。
_BLOB_TYPE_TIER_HINT = 100 * 1024 * 1024


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


class User(Base):
    """
    用户表

    存储用户认证信息和角色。
    """
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # 密码版本号：每次改密 +1，老 token 的 pwd_v 与当前不一致即视为失效。
    # 不是 blacklist —— 单调递增计数器，无需 Redis / 持久化吊销集合。
    password_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # 等保密码策略（门类三）。三者归一在一道闸门 + 一个时间戳 + 一个历史列：
    #   - must_change_password: 建用户/导入/admin 重置/登录超期 → True;
    #     get_current_user 闸门在 True 时除改密/登出外一律 403,改密成功清。
    #     根治 ACC-03(缺省密码)、承载首次强制改密 + 周期到期强制改密。
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #   - password_changed_at: 每次设置口令时写 utc_now();登录时算龄 > 到期天数
    #     即置 must_change_password。NULL 视为「未知 → 已过期」。
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    #   - password_history: 最近若干个**旧** hash(most-recent-first,trim 到
    #     PASSWORD_HISTORY_RETAIN)。改密查重候选 = [当前 hash] + history[:COUNT-1]。
    #     从 day 1 起维护,故调高 PASSWORD_HISTORY_COUNT 无需再迁移(列存得比查得多)。
    password_history: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True, default=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # 部门归属（可空：未分配 / 自助注册的用户）
    # ondelete=SET NULL：删除部门时把用户的 department_id 置空，不级联删用户
    department_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 关系：一对多 -> conversations
    # passive_deletes=True：删除 User 时让 DB 的 FK CASCADE 处理子行，
    # 不让 ORM 预先 SET NULL 或逐行 DELETE 而绕过 CASCADE。
    conversations: Mapped[List["Conversation"]] = relationship(
        "Conversation",
        back_populates="owner",
        lazy="selectin",
        passive_deletes=True,
    )

    # 关系：多对一 -> department（按需 lazy load，列表场景不预加载）
    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        back_populates="users",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, role={self.role})>"


class Department(Base):
    """
    部门表（邻接表实现的层级结构）

    每个部门可以有一个父部门，形成树。深度可变 —— 用户可以挂在任意一级，
    取决于实际组织结构。

    设计要点：
    - parent_id ondelete=RESTRICT：不允许删有子部门的部门（必须先迁子）
    - UNIQUE(parent_id, name)：同父下部门名不重复，堵手抖空格 / 重复创建
    - 删除非空部门（含 user）由路由层校验，DB 不做 cascade
    """
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # 跨方言根级去重的生成列实现 — 详见 __table_args__ 的 uq_dept_root_name 注释。
    # ORM 视角只读：Computed 列由 DB 在 INSERT/UPDATE 时根据 parent_id + name 自动
    # 计算，SQLAlchemy 默认不会在 INSERT 语句里写这一列。
    root_name_key: Mapped[Optional[str]] = mapped_column(
        String(128),
        Computed(
            "CASE WHEN parent_id IS NULL THEN name END",
            persisted=True,
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # 自引用：父-子关系
    parent: Mapped[Optional["Department"]] = relationship(
        "Department",
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[List["Department"]] = relationship(
        "Department",
        back_populates="parent",
        passive_deletes=True,
    )

    # 一对多 -> users（只在按部门反查用户时显式 join，平时不预加载）
    users: Mapped[List["User"]] = relationship(
        "User",
        back_populates="department",
        passive_deletes=True,
    )

    __table_args__ = (
        # 同父下名称唯一 — 适用于 parent_id 非 NULL 的所有行
        UniqueConstraint("parent_id", "name", name="uq_dept_parent_name"),
        # 根级（parent_id IS NULL）名称唯一性兜底。
        #
        # 为什么不是 partial unique index：
        #   - SQL 标准把多个 NULL 视为 DISTINCT，上面的 UC 在 parent_id IS NULL
        #     的行上失效（NULL,'A' 不等于 NULL,'A'，两条都允许插入）
        #   - SQLite/PostgreSQL 支持 partial unique index（带 WHERE 条件），
        #     可以专门约束根级；但 MySQL 5.7~8.x 不支持 partial index
        #     （8.0.13 的 functional index 是另一回事），sqlite_where /
        #     postgresql_where 在 MySQL 方言下会被忽略，编译成全表
        #     UNIQUE(name)，反而误伤"不同父下同名子部门"的合法情况
        #
        # 改用生成列 + 普通 UNIQUE：
        #   - root_name_key 在根级行 = name，非根级 = NULL
        #   - UNIQUE(root_name_key)：根级 NULL 与 name 相比，相同 name 直接冲突；
        #     非根级行的 NULL 互相 DISTINCT，不冲突，不影响"不同父下同名子"
        #   - SQLite 3.31+ / PostgreSQL 12+ / MySQL 5.7+ 都原生支持 STORED
        #     生成列（与 UNIQUE 索引兼容）
        #
        # 这条约束是 source of truth，路由层 pre-check 只是为了给 admin 早返回
        # 友好的 409；并发请求穿过 pre-check 后，DB 这层会原子拒绝第二条 INSERT。
        UniqueConstraint("root_name_key", name="uq_dept_root_name"),
    )

    def __repr__(self) -> str:
        return f"<Department(id={self.id}, name={self.name}, parent={self.parent_id})>"


class Conversation(Base):
    """
    对话表
    
    存储对话的元信息，每个对话包含多条消息（树结构）。
    conversation_id 同时也是关联的 artifact_session_id。
    """
    __tablename__ = "conversations"
    
    # 主键：conversation_id
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    
    # 当前活跃的叶子节点 message_id
    active_branch: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # 对话标题（可由首条消息自动生成）
    title: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    
    # 用户ID（认证隔离）
    # ondelete=CASCADE：硬删用户时连带删除其所有会话（messages / events /
    # artifacts 通过下一级 CASCADE 自动清理）。内网工具不保留孤儿会话；
    # 若要保留需走"禁用 (is_active=False)"软删路径。
    user_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    # 关系：多对一 -> user
    owner: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="conversations"
    )
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # 关系：一对多 -> messages
    messages: Mapped[List["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    # 关系：一对一 -> artifact_session
    artifact_session: Mapped[Optional["ArtifactSession"]] = relationship(
        "ArtifactSession",
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, title={self.title})>"


class Message(Base):
    """
    消息表

    存储用户消息和助手响应，通过 parent_id 实现树结构。
    message_id 同时作为执行标识。
    """
    __tablename__ = "messages"

    # 主键：message_id（同时作为执行标识）
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 外键：所属对话
    conversation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # 父消息ID（实现树结构）
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True
    )

    # 用户消息内容（显示用，不再承担历史注入职责 —— 历史由 MessageEvent 提供）
    user_input: Mapped[str] = mapped_column(Text, nullable=False)

    # 助手最终响应（显示用）
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )

    # 扩展元数据（顶层 keys: always_allowed_tools, execution_metrics,
    # uploaded_files=[{id, filename}] 本轮上传文件 display 快照；
    # last_input_tokens 嵌在 execution_metrics 内部）
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
        default=dict
    )

    # 关系：多对一 -> conversation
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages"
    )

    __table_args__ = (
        Index("ix_messages_conv_created", "conversation_id", "created_at"),
    )

    def __repr__(self) -> str:
        input_preview = self.user_input[:50] + "..." if len(self.user_input) > 50 else self.user_input
        return f"<Message(id={self.id}, user_input={input_preview})>"


class MessageEvent(Base):
    """
    消息事件表（事件溯源）

    存储执行过程中的完整事件链，用于历史回放和可观测性。
    llm_chunk 不持久化（SSE-only），其他事件全量存储。
    在两个持久化边界 batch write：execution_complete 或 error。
    """
    __tablename__ = "message_events"

    # 自增主键，天然有序
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 业务去重键：{message_id}-{seq}，用于 retry 幂等
    event_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True, unique=True)

    # 外键：所属消息
    message_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 事件类型（StreamEventType.value）
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # 产生事件的 agent
    agent_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # JSON 完整数据，不截断
    data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )

    __table_args__ = (
        Index("ix_message_events_message", "message_id"),
    )

    def __repr__(self) -> str:
        return f"<MessageEvent(id={self.id}, type={self.event_type}, agent={self.agent_name})>"


class ArtifactSession(Base):
    """
    Artifact 会话表
    
    每个对话对应一个 Artifact Session，包含多个 Artifact。
    session_id 与 conversation_id 相同。
    """
    __tablename__ = "artifact_sessions"
    
    # 主键：session_id（与 conversation_id 相同）
    id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True
    )
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # 关系：一对一 -> conversation
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="artifact_session"
    )
    
    # 关系：一对多 -> artifacts
    artifacts: Mapped[List["Artifact"]] = relationship(
        "Artifact",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    def __repr__(self) -> str:
        return f"<ArtifactSession(id={self.id})>"


class Artifact(Base):
    """
    Artifact 表
    
    存储 Artifact 的当前内容和元数据。
    使用复合主键 (id, session_id)。
    """
    __tablename__ = "artifacts"
    
    # 复合主键
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("artifact_sessions.id", ondelete="CASCADE"),
        primary_key=True
    )
    
    # 内容类型 (MIME type, e.g. text/markdown, text/x-python)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # 来源 (agent, user_upload)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")

    # 标题
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    
    # 当前内容（冗余存储，避免每次查版本表）
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # 是否有二进制 blob（XOR:text artifact 走 content,binary 走 blob）。这是「是不是
    # 二进制」的权威判别 —— 建 artifact 时按 blob 在场写死、set-once 不可变(blob 不可改)。
    # 序列化/读侧据此判别,无需触碰 lazy 的 Artifact.blob 关系(避免把字节拖进列表读)。
    has_blob: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # 当前版本号
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # 扩展元数据
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
        default=dict
    )

    # 关系：多对一 -> session
    session: Mapped["ArtifactSession"] = relationship(
        "ArtifactSession",
        back_populates="artifacts"
    )
    
    # 关系：一对多 -> versions
    versions: Mapped[List["ArtifactVersion"]] = relationship(
        "ArtifactVersion",
        back_populates="artifact",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ArtifactVersion.version"
    )

    # 关系：一对一 -> blob（二进制存储，与热路径隔离）。
    # **刻意 lazy="select"（非 selectin）**：list/inventory 查询绝不能把 MB 级字节
    # 拖进每次列表读——只有显式访问 `.blob`（仅 raw-fetch 路径）才发 SQL 载入。
    # cascade 由 ORM 驱动（SQLite dev 不开 FK pragma，DB 级 ondelete 不生效）；
    # DB 级 ondelete=CASCADE 作 prod 兜底。除 raw 端点外，任何序列化都不得碰 .blob。
    blob: Mapped[Optional["ArtifactBlob"]] = relationship(
        "ArtifactBlob",
        back_populates="artifact",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Artifact(id={self.id}, title={self.title}, version={self.current_version})>"


class ArtifactVersion(Base):
    """
    Artifact 版本表

    存储 Artifact 的历史版本，用于版本回溯和 diff 展示。

    版本号可以是稀疏的（不保证 1..N 连续存在）。执行期间 artifact
    的多次内存编辑由 ArtifactService/WorkingSet write-back 机制折叠为一个最终快照，
    因此同一轮执行内的中间版本不会产生持久化记录。
    """
    __tablename__ = "artifact_versions"
    
    # 自增主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # 所属 Artifact（复合外键）
    artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    
    # 版本号
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # 版本内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    
    # 更新类型 (create/update/update_fuzzy/rewrite)
    update_type: Mapped[str] = mapped_column(String(32), nullable=False)
    
    # 变更记录 [(old, new), ...]
    changes: Mapped[Optional[List]] = mapped_column(JSON, nullable=True)
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )

    # 关系：多对一 -> artifact
    artifact: Mapped["Artifact"] = relationship(
        "Artifact",
        back_populates="versions",
        foreign_keys=[artifact_id, session_id],
        primaryjoin="and_(ArtifactVersion.artifact_id==Artifact.id, "
                   "ArtifactVersion.session_id==Artifact.session_id)"
    )
    
    # 唯一约束：每个 artifact 的每个版本只能有一条记录
    __table_args__ = (
        UniqueConstraint(
            "artifact_id", "session_id", "version",
            name="uq_artifact_version"
        ),
        # 外键约束（复合外键）
        ForeignKeyConstraint(
            ["artifact_id", "session_id"],
            ["artifacts.id", "artifacts.session_id"],
            ondelete="CASCADE"
        ),
        # 索引：按 artifact 查询版本
        Index("ix_artifact_versions_artifact", "artifact_id", "session_id"),
    )
    
    def __repr__(self) -> str:
        return f"<ArtifactVersion(artifact={self.artifact_id}, version={self.version})>"


class ArtifactBlob(Base):
    """
    Artifact 二进制存储表（与文本/inventory 热路径隔离）

    1:1 绑定 Artifact（复合主键 = 复合外键 (artifact_id, session_id)），承载用户
    上传的富格式原始字节（docx/pdf）与图片（png/jpeg）—— **源不可变，A 阶段不随
    版本走**（一个 artifact 一条 blob；版本化 blob 是 C 阶段沙盒回写才有的问题）。

    刻意独立成表而非在 Artifact 上加 nullable 列：list/inventory 查询永不 JOIN
    此表，字节仅在显式 raw-fetch（`Artifact.blob` 关系 lazy 载入）时进内存，避免
    把 MB 级 blob 拖进每次列表读。

    类型(刻意不依赖方言):泛型 `LargeBinary(length=...)` —— PG → `BYTEA`(忽略
    长度,~1GB),SQLite → `BLOB`(忽略长度)。MySQL/TDSQL 上 `LargeBinary` **不带
    长度**会映射成 64KB 的 `BLOB` 静默截断;带长度则 emit `BLOB(M)`,而 MySQL 按
    `BLOB(M)` 选**能容下 M 字节的最小 blob tier**——M=100MB>16MB ⇒ 落 `LONGBLOB`
    (4GB)。于是一条泛型声明在三库都对、零 dialect import。大小由
    `config.ARTIFACT_BLOB_MAX_BYTES` 在写入侧 loud-fail 兜底(M 只选 tier 不限长)。
    """
    __tablename__ = "artifact_blobs"

    # 复合主键 = 复合外键 → artifacts(id, session_id)，1:1
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 原始字节。length hint 仅为在 MySQL 上把列推到 LONGBLOB tier(见类 docstring)。
    data: Mapped[bytes] = mapped_column(
        LargeBinary(length=_BLOB_TYPE_TIER_HINT),
        nullable=False,
    )

    # 字节数冗余存：metadata / 校验 / 展示不必把 data 载入内存
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )

    # 关系：一对一 -> artifact
    artifact: Mapped["Artifact"] = relationship(
        "Artifact",
        back_populates="blob",
    )

    __table_args__ = (
        # 复合外键 → artifacts，prod(PG/MySQL) DB 级级联兜底；SQLite dev 靠 ORM cascade
        ForeignKeyConstraint(
            ["artifact_id", "session_id"],
            ["artifacts.id", "artifacts.session_id"],
            ondelete="CASCADE"
        ),
        # 存储配额聚合用:`SUM(size_bytes) WHERE session_id IN (...)`(列表 GROUP BY +
        # per-用户 join Conversation)。复合主键以 artifact_id 打头,服务不了按 session_id
        # 的过滤;(session_id, size_bytes) 让聚合走 index-only,绝不触 data(blob 字节)。
        Index("ix_artifact_blobs_session_size", "session_id", "size_bytes"),
    )

    def __repr__(self) -> str:
        return f"<ArtifactBlob(artifact={self.artifact_id}, size_bytes={self.size_bytes})>"


# ============================================================================
# 工具/agent 注册表(config 仅种子,DB 是物化缓存)
#
# 设计源:docs/_archive/design/skill-system-phase-b-design.md
# 核心不变量:
#   - identity = natural key(unit.name / agent.name 作 PK),所有 m2m 真 FK +
#     ON DELETE CASCADE → ABA/孤儿由构造消失(决策 10)。
#   - 权限两正交轴(决策 11):**等级**(auto/confirm)唯一来源 = 工具定义
#     (ToolMember.permission / builtin BaseTool.permission),agent 侧只存
#     **成员态**(enabled/disabled),不存等级。
#   - source = seeded(config 种子,reconciler 拥有,UI 不可改)/ dynamic(UI 新建)。
#     agent 暂只 seed-only 物化(无 UI、无 dept 消费者)。
#   - visibility/defer 列暂不被引擎消费:visibility 供部门授权、defer 供渐进式披露,
#     消费侧另行接入。builtin = 代码、不入这些表(for-everyone)。
# ============================================================================


class ToolUnit(Base):
    """
    External 工具单元 —— 授权 + 生命周期 + 披露的边界(决策 5/10/11)。

    kind 三态:tool(singleton,1 个 member,full_name==name)/ toolset(一平台多
    endpoint)/ mcp(member 运行期由 tools/list 灌入)。unit name 全局唯一、
    **禁含 `__`**(`<unit>__<tool>` 前缀分隔保留),启动期撞名 loud-fail。
    """
    __tablename__ = "tool_units"

    # natural key:unit 名作 PK(决策 10)。禁含 `__`(reconciler 校验)。
    name: Mapped[str] = mapped_column(String(64), primary_key=True)

    # tool(singleton) / toolset / mcp
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    # set 级描述(索引行语境;singleton = 工具自身描述)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # 未列出部门的默认姿态(决策 10):public=默认 allow / department=默认 deny。
    # 暂不消费;部门授权(department_unit_rule)接入后消费。private 仅 skill 有,unit 无。
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public", server_default="public"
    )

    # 渐进式披露开关:True → <available_tools> 只出索引行,完整 schema 由 search_tools
    # 按需补。显式开关、不按 token 自动(私有化无 tokenizer,原则 7)。
    defer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # provider 抽象缝(MCP 接入时填 mcp):http | mcp
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="http", server_default="http"
    )

    # seeded(config 种子,UI 不可改)/ dynamic(UI 新建)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="seeded")

    # seeded 行内容哈希:reconciler 幂等 upsert(hash 同则 skip)。dynamic 行为 NULL。
    seed_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 一对多 -> members。删 unit 时级联删成员行(PG/MySQL 走 DB FK CASCADE;
    # SQLite dev 靠此 ORM cascade)。members 数量小,selectin 预载无压力。
    members: Mapped[List["ToolMember"]] = relationship(
        "ToolMember",
        back_populates="unit",
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<ToolUnit(name={self.name}, kind={self.kind}, source={self.source})>"


class ToolMember(Base):
    """
    工具单元下的具体可调工具/endpoint。

    full_name = 注册/可调名:toolset → `<unit>__<member>`;singleton → `== unit_name`
    (无 `__`)。**等级(permission)唯一来源在此**(决策 11),agent/skill/dept 均不改。
    """
    __tablename__ = "tool_members"

    # 复合 natural key(unit_name, member_name)
    unit_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tool_units.name", ondelete="CASCADE"),
        primary_key=True,
    )
    # 作者裸名(search_repos);loader 据 unit 名加 `<unit>__` 前缀产 full_name
    member_name: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 注册/可调全名:resolver/registry/always_allow 的 key。全局唯一。
    full_name: Mapped[str] = mapped_column(String(130), nullable=False)

    # 等级:auto | confirm —— 决策 11 的唯一来源
    permission: Mapped[str] = mapped_column(
        String(16), nullable=False, default="confirm"
    )

    # provider 相关定义:http → endpoint/method/headers/params/response_extract/
    # timeout/secret 引用;mcp → 运行期由 tools/list 填(F)。JSON 不锁 schema,
    # 按 unit.provider 分派解释。
    definition: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    unit: Mapped["ToolUnit"] = relationship("ToolUnit", back_populates="members")

    __table_args__ = (
        # full_name 全局唯一:跨 unit 同 member 名不撞、resolver 按 full_name 寻址
        UniqueConstraint("full_name", name="uq_tool_members_full_name"),
    )

    def __repr__(self) -> str:
        return f"<ToolMember(full_name={self.full_name}, permission={self.permission})>"


class Agent(Base):
    """
    Agent 定义(决策 5:config 仍唯一作者真相,DB 只是 seed-only 物化缓存)。

    物化只为:统一存储 + 撞名检查 + 将来 dept 化(加 department_agent_rule,v0 无消费者)。
    **无 UI-native、无运行时编辑**(运行时可编辑 agent 仍 Non-goal)。

    builtin_tools = 声明的 builtin 工具成员态 {名: enabled|disabled}(决策 11:builtin
    不进 agent_units m2m,引擎从此列直读)。external 单元在 agent_units。
    """
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    max_tool_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    role_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # {builtin名: "enabled"|"disabled"}(决策 11 成员轴,不含等级)
    builtin_tools: Mapped[Optional[Dict[str, str]]] = mapped_column(
        JSON, nullable=True, default=dict
    )

    # v0 永 seeded(agent 暂不开 UI)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="seeded", server_default="seeded"
    )
    seed_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Agent(name={self.name}, model={self.model})>"


class AgentUnit(Base):
    """
    agent ⟷ tool_unit 绑定 = 该 agent 暴露的 external 单元宇宙(决策 11)。

    成员态 enabled/disabled(absent = 不建行 = 不在宇宙)。source:seeded(agent MD
    经 reconciler 种)/ dynamic(UI 勾选挂载)。两端真 FK + CASCADE。
    """
    __tablename__ = "agent_units"

    agent_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agents.name", ondelete="CASCADE"),
        primary_key=True,
    )
    unit_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tool_units.name", ondelete="CASCADE"),
        primary_key=True,
    )

    # enabled | disabled(决策 11 成员轴;skill 在收窄后宇宙内翻 disabled)
    member_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="enabled", server_default="enabled"
    )

    # seeded(agent MD)/ dynamic(UI 挂载)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="seeded", server_default="seeded"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentUnit(agent={self.agent_name}, unit={self.unit_name}, state={self.member_state})>"


class ToolCredential(Base):
    """
    External 工具单元的可逆加密凭证(B-4,unit 级多行,仿 artifact_blobs 与定义隔离)。

    一 unit 多行,每行一个 `{{placeholder}}` 的 Fernet 密文(复用 secrets.py 的 {{NAME}}
    替换语义,值的来源从 env 换成此表 → 不退化多 secret 能力)。凭证 + base_url 是 **unit
    级**(toolset 共享给所有 member;要 per-endpoint 不同 key = 拆 unit)。

    **故意不建 ToolUnit→credentials relationship**:catalog / per-turn 快照都不载入密文。
    CredentialResolver 在 execute 期按 unit 名开一条短 retrying session lazy 解密(B-5:不
    骑 turn-long session、execute 期短读),解密明文只作单次调用的局部、用完即弃 —— 不进
    事件 / catalog,也不驻留整轮(只解被调工具的 unit)。

    source:seeded(reconciler 从 env 取值加密落库,UI 不可改)/ dynamic(UI 写明文加密)。
    """
    __tablename__ = "tool_credentials"

    unit_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tool_units.name", ondelete="CASCADE"),
        primary_key=True,
    )
    # {{NAME}} 占位符名(seeded 即 TOOL_SECRET_*;dynamic 由 UI/定义决定)
    placeholder_name: Mapped[str] = mapped_column(String(128), primary_key=True)

    # Fernet 密文(urlsafe-base64 文本)。**可逆加密、非哈希**:execute 时要解开外发。
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)

    # seeded(env 种子,reconciler 拥有)/ dynamic(UI 写)。reconciler 只 prune seeded 行。
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="seeded", server_default="seeded"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ToolCredential(unit={self.unit_name}, placeholder={self.placeholder_name}, source={self.source})>"


# ============================================================================
# Skill 系统(Phase C)—— skill 定义 + per-user 覆盖 + 部门授权两张 FK 表。
#   - 真相源 = config(seeded)/ 原始上传 blob(dynamic);DB 是物化缓存(决策 3/5)。
#   - identity = natural key(slug),m2m 全按 name 引用 + DB ON DELETE CASCADE
#     → ABA 由构造消失(决策 10/changelog 06-23)。
#   - 6 标准字段按"系统消费与否"分流:消费列开独立列、其余归 `metadata` JSON;
#     原始 frontmatter 结构在 bundle blob 里无损保留(决策 3/9)。
# ============================================================================


class Skill(Base):
    """
    Skill 定义(决策 1/3/9)。slug = natural key(PK,kebab-case,= 目录名)。

    可见性两正交字段(替不透明 scope,决策 1):`visibility`(private 仅 owner /
    public 全员 / department 按 dept rule)+ `default_enabled`(shared skill 默认是否
    进 L1)。per-user 覆盖在 user_skills 稀疏表;部门可见走 department_skill_rules。

    存储四处(决策 3):①消费列(下列)②`metadata` JSON(系统不单独消费的 license/
    version/未知扩展)③`skill_md` 正文(去 frontmatter,L2 read_skill 直返)④`bundle`
    完整原始 zip(L3 mount + 无损导出;单 SKILL.md 无附属文件时为 NULL)。
    """
    __tablename__ = "skills"

    # natural key:slug 作 PK(= config 目录名 / 上传归一化名)
    slug: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 展示名(frontmatter `name`,缺省回落 slug);description 折入 when_to_use
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # private(仅 owner)/ public(全员)/ department(按 department_skill_rules)。
    # skill 独有 private(unit 无,决策 1);private + builtin 都不进 dept rule 表。
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public", server_default="public"
    )
    # shared skill 默认是否注入 L1(决策 1:preinstalled=true、marketplace=false)
    default_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # private 用(指向 owner);shared(public/department)为 NULL。删用户级联删其私有 skill。
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # frontmatter `allowed-tools` 原样条目列表(unit 名 / `<unit>__<tool>` 全名 / builtin
    # 名)。import 期校验存在性、runtime(C-2)经共享 resolver 解析到 unit 建 skill_grants
    # ——只翻 agent 宇宙内 disabled 的(决策 11)。raw 存储、解析靠共享函数(import+runtime 同一个)。
    allowed_tools: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    # `compatibility` 声明(气隙依赖校验,决策 6;C 存、D/E 消费)
    compatibility: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # 系统不单独消费的 frontmatter 字段杂项(license/标准 metadata 容器[含 version]/未知扩展)。
    # 属性名避开 SQLAlchemy 保留的 `metadata`,DB 列名仍为 "metadata"(决策 3)。
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    # SKILL.md 正文(frontmatter 已剥离),L2 read_skill 直返
    skill_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 完整原始 zip(含 SKILL.md + references + scripts + assets);单文件 skill 无附属 → NULL。
    # length hint 同 ArtifactBlob(只影响 MySQL LONGBLOB tier,PG/SQLite 忽略)。
    bundle: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary(length=_BLOB_TYPE_TIER_HINT), nullable=True
    )

    # seeded(config/skills 种子,reconciler 拥有,UI 不可改)/ dynamic(UI 上传)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="seeded")
    seed_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Skill(slug={self.slug}, visibility={self.visibility}, source={self.source})>"


class UserSkill(Base):
    """
    用户对 skill 的个人开关(稀疏覆盖,决策 1)。

    无行 = 走 visibility/default_enabled;有行 = 用户显式开/关。marketplace 选用 =
    enabled 行、关掉预装 = disabled 行(link 与 toggle 同一机制)。两端真 FK + CASCADE。
    """
    __tablename__ = "user_skills"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    skill_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("skills.slug", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserSkill(user={self.user_id}, skill={self.skill_slug}, enabled={self.enabled})>"


class DepartmentSkillRule(Base):
    """
    部门 ⟷ skill 授权例外(决策 10)。**无 `effect` 列** —— 一行 = 该部门是默认姿态的
    「例外」,方向从 skill 的 `visibility` 派生(public→deny / department→grant)。

    一资源多部门 = 多行;祖先链解析时父覆盖整子树(各方向只需 1 行)。改 visibility
    清规则(reconciler + Manager 两路),故行不熬过 visibility 变更。两端真 FK + CASCADE。
    C 即消费(skill 可见性);unit 侧的 department_unit_rules 建好但空跑到 G。
    """
    __tablename__ = "department_skill_rules"

    department_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True
    )
    skill_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("skills.slug", ondelete="CASCADE"), primary_key=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<DepartmentSkillRule(dept={self.department_id}, skill={self.skill_slug})>"


class DepartmentUnitRule(Base):
    """
    部门 ⟷ tool_unit 授权例外(决策 10)。与 DepartmentSkillRule 同构(无 `effect` 列、
    方向派生自 unit `visibility`)。**C 建表但空跑**:resolver 的 dept 收窄输入层 G 才加
    (line 101 分阶段输入);C 接通的只是 reconciler/Manager 改 visibility 时的 clear 钩子。
    tool/toolset/mcp 细分在 `tool_units.kind`,规则表不存类型列(unit-everywhere)。
    """
    __tablename__ = "department_unit_rules"

    department_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True
    )
    unit_name: Mapped[str] = mapped_column(
        String(64), ForeignKey("tool_units.name", ondelete="CASCADE"), primary_key=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<DepartmentUnitRule(dept={self.department_id}, unit={self.unit_name})>"
