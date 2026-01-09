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
    String,
    Text,
    Integer,
    DateTime,
    ForeignKey,
    JSON,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


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
    
    # 预留：用户ID（Phase 2）
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=datetime.now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False
    )
    
    # 扩展元数据
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",  # 数据库列名
        JSON,
        nullable=True,
        default=dict
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
    
    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, title={self.title})>"


class Message(Base):
    """
    消息表
    
    存储用户消息和 Graph 响应，通过 parent_id 实现树结构。
    """
    __tablename__ = "messages"
    
    # 主键：message_id
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
    
    # 用户消息内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    
    # 关联的 LangGraph 线程ID
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    
    # Graph 最终响应
    graph_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        nullable=False
    )
    
    # 扩展元数据（可存储简化的执行摘要）
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
    
    def __repr__(self) -> str:
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"<Message(id={self.id}, content={content_preview})>"


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
        default=datetime.now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
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
    包含乐观锁字段 lock_version。
    """
    __tablename__ = "artifacts"
    
    # 复合主键
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("artifact_sessions.id", ondelete="CASCADE"),
        primary_key=True
    )
    
    # 内容类型 (markdown/python/etc)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    
    # 标题
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    
    # 当前内容（冗余存储，避免每次查版本表）
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    
    # 当前版本号
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    
    # 乐观锁版本号
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
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
    
    def __repr__(self) -> str:
        return f"<Artifact(id={self.id}, title={self.title}, version={self.current_version})>"


class ArtifactVersion(Base):
    """
    Artifact 版本表
    
    存储 Artifact 的历史版本，用于版本回溯和 diff 展示。
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
        default=datetime.now,
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
        ForeignKey(
            ["artifact_id", "session_id"],
            ["artifacts.id", "artifacts.session_id"],
            ondelete="CASCADE"
        ),
        # 索引：按 artifact 查询版本
        Index("ix_artifact_versions_artifact", "artifact_id", "session_id"),
    )
    
    def __repr__(self) -> str:
        return f"<ArtifactVersion(artifact={self.artifact_id}, version={self.version})>"


# ============================================================
# 异常定义
# ============================================================

class VersionConflictError(Exception):
    """
    乐观锁版本冲突异常
    
    当更新 Artifact 时检测到版本冲突时抛出。
    """
    def __init__(self, message: str, artifact_id: str = None, expected_version: int = None):
        super().__init__(message)
        self.artifact_id = artifact_id
        self.expected_version = expected_version


if __name__ == "__main__":
    import asyncio
    from sqlalchemy import select
    from db.database import create_test_database_manager
    
    async def test():
        """测试 ORM 模型"""
        print("\n🧪 ORM 模型测试")
        print("=" * 50)
        
        db = create_test_database_manager()
        
        try:
            await db.initialize()
            print("✅ 数据库初始化成功")
            
            async with db.session() as session:
                # 创建对话
                conv = Conversation(
                    id="conv-test-001",
                    title="测试对话"
                )
                session.add(conv)
                await session.flush()
                print(f"✅ 创建对话: {conv}")
                
                # 创建 Artifact Session
                art_session = ArtifactSession(id=conv.id)
                session.add(art_session)
                await session.flush()
                print(f"✅ 创建 ArtifactSession: {art_session}")
                
                # 创建消息
                msg = Message(
                    id="msg-test-001",
                    conversation_id=conv.id,
                    content="Hello, World!",
                    thread_id="thd-test-001"
                )
                session.add(msg)
                await session.flush()
                print(f"✅ 创建消息: {msg}")
                
                # 创建 Artifact
                artifact = Artifact(
                    id="task_plan",
                    session_id=art_session.id,
                    content_type="markdown",
                    title="任务计划",
                    content="# Task Plan\n\n- Step 1"
                )
                session.add(artifact)
                await session.flush()
                print(f"✅ 创建 Artifact: {artifact}")
                
                # 创建版本
                version = ArtifactVersion(
                    artifact_id=artifact.id,
                    session_id=artifact.session_id,
                    version=1,
                    content=artifact.content,
                    update_type="create"
                )
                session.add(version)
                await session.flush()
                print(f"✅ 创建版本: {version}")
                
                # 查询测试
                result = await session.execute(
                    select(Conversation).where(Conversation.id == "conv-test-001")
                )
                loaded_conv = result.scalar_one()
                print(f"✅ 查询对话: {loaded_conv}")
                print(f"   - 消息数: {len(loaded_conv.messages)}")
                print(f"   - Artifacts: {len(loaded_conv.artifact_session.artifacts)}")
            
            print("\n✅ 所有 ORM 测试通过!")
            
        finally:
            await db.close()
    
    asyncio.run(test())
