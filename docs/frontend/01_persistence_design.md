# ArtifactFlow 持久化改造方案

> 版本: v1.1 | 优先级: P0（API和前端的前置依赖）
> 
> 变更记录：
> - v1.1: 新增双层存储策略、依赖注入规范、乐观锁机制、Checkpointer替换方案

## 1. 改造目标

将当前基于内存的 `ConversationManager` 和 `ArtifactStore` 改造为 SQLite 持久化存储，实现：

1. **数据持久化**：服务重启后数据不丢失
2. **解耦存储**：Conversation 和 Artifact 独立存储，通过 ID 关联
3. **可扩展性**：为后续 PostgreSQL 迁移和用户系统预留接口
4. **性能平衡**：热数据内存缓存 + 冷数据数据库存储
5. **🆕 无状态化**：支持容器化水平扩展

---

## 2. 🆕 双层存储策略 (Dual Storage Strategy)

### 2.1 核心概念

系统采用**双层存储架构**，明确区分两类存储的职责：

```
┌─────────────────────────────────────────────────────────────────┐
│                      双层存储架构                                │
│                                                                 │
│  ┌─────────────────────────┐    ┌─────────────────────────┐    │
│  │    Application DB       │    │  LangGraph Checkpointer │    │
│  │    (SQLite/Postgres)    │    │   (AsyncSqliteSaver)    │    │
│  ├─────────────────────────┤    ├─────────────────────────┤    │
│  │ 角色: "长期记忆"         │    │ 角色: "短期工作台"       │    │
│  │ 真相来源 (Source of Truth)│   │ 执行状态暂存            │    │
│  ├─────────────────────────┤    ├─────────────────────────┤    │
│  │ 职责:                   │    │ 职责:                   │    │
│  │ - UI 展示               │    │ - 执行栈保存            │    │
│  │ - 搜索和历史回溯         │    │ - 中断/恢复状态         │    │
│  │ - 跨会话持久化          │    │ - Agent 变量状态        │    │
│  ├─────────────────────────┤    ├─────────────────────────┤    │
│  │ 数据:                   │    │ 数据:                   │    │
│  │ - conversations         │    │ - thread states        │    │
│  │ - messages              │    │ - interrupt values     │    │
│  │ - artifacts             │    │ - agent memories       │    │
│  │ - artifact_versions     │    │                         │    │
│  └─────────────────────────┘    └─────────────────────────┘    │
│              │                              │                   │
│              └──────────┬───────────────────┘                   │
│                         │                                       │
│              ExecutionController                                │
│              (状态注入 & 同步)                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 状态注入机制 (State Injection)

**关键原则**：App DB 是真相来源，LangGraph Checkpointer 仅用于执行过程。

**实现要点**：
1. 每次启动新对话时，**强制**从 App DB 拉取清洗过的历史记录
2. 将历史记录注入到 Agent State 的 `conversation_history` 字段
3. 覆盖 LangGraph Checkpointer 可能残留的旧数据

```python
# ExecutionController._execute_new_message() 中的逻辑
async def _execute_new_message(self, content, conversation_id, parent_message_id):
    # 1. 从 App DB 获取对话历史（真相来源）
    conversation_history = self.conversation_manager.format_conversation_history(
        conv_id=conversation_id,
        to_message_id=parent_message_id
    )
    
    # 2. 创建新的 thread_id（每次执行都是新线程）
    thread_id = f"thd-{uuid4().hex}"
    
    # 3. 构建初始状态，注入历史记录
    initial_state = create_initial_state(
        task=content,
        conversation_history=conversation_history,  # 注入历史
        thread_id=thread_id,
        # ...
    )
    
    # 4. 执行图（Checkpointer 只保存本次执行状态）
    result = await self.graph.ainvoke(initial_state, config)
```

### 2.3 分支对话的 Thread 策略

当用户创建分支时：

1. **App DB 层**：记录树状结构（`messages.parent_id` 指向分支点）
2. **LangGraph 层**：创建**新的** `thread_id`
3. **状态注入**：将分支点之前的历史作为初始状态注入

```python
# 分支创建流程
def create_branch(self, conv_id, parent_message_id, new_content):
    # 1. 获取从根到分支点的历史
    history = self.conversation_manager.get_message_path(conv_id, parent_message_id)
    
    # 2. 创建新的 thread_id（不复用旧分支的）
    new_thread_id = f"thd-{uuid4().hex}"
    
    # 3. 注入历史到新执行
    # ...
```

---

## 3. 🆕 开发规范

### 3.1 依赖注入与无状态化

**强制要求**：废弃所有模块级全局单例变量。

**废弃模式**（不再使用）：
```python
# ❌ 旧代码 - artifact_ops.py
_artifact_store = ArtifactStore()

def get_artifact_store():
    return _artifact_store
```

**新模式**：
```python
# ✅ 新代码 - 通过构造函数注入
class ArtifactManager:
    def __init__(self, repository: ArtifactRepository):
        self.repository = repository
        self._cache = {}  # 实例级缓存

# ✅ API 层使用 FastAPI Depends
from fastapi import Depends

def get_artifact_manager(
    db: AsyncSession = Depends(get_db_session)
) -> ArtifactManager:
    repo = ArtifactRepository(db)
    return ArtifactManager(repo)
```

**目的**：
- 确保内存中不残留用户状态
- 支持容器化水平扩展 (Horizontal Scaling)
- 便于单元测试（可注入 Mock）

### 3.2 ORM 规范

**强制要求**：所有数据库操作必须通过 SQLAlchemy ORM 模型进行，禁止原生 SQL。

```python
# ❌ 禁止
result = await session.execute(text("SELECT * FROM conversations WHERE id = :id"), {"id": conv_id})

# ✅ 必须使用 ORM
result = await session.execute(
    select(Conversation).where(Conversation.id == conv_id)
)
```

**目的**：确保从 SQLite 迁移到 PostgreSQL 时只需更改连接字符串，无需重写查询逻辑。

---

## 4. 数据库 Schema 设计

### 4.1 核心原则

```
┌─────────────────────────────────────────────────────────────┐
│                      关系设计原则                            │
│                                                             │
│  Conversation ◄──1:1──► ArtifactSession                    │
│       │                        │                            │
│       │ 1:N                    │ 1:N                        │
│       ▼                        ▼                            │
│   Messages                 Artifacts                        │
│       │                        │                            │
│       │ tree structure         │ 1:N                        │
│       ▼                        ▼                            │
│   (parent_id)           ArtifactVersions                    │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 表结构设计

#### 表1: `conversations`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PRIMARY KEY | conversation_id，同时也是关联的 session_id |
| active_branch | TEXT | 当前活跃的叶子节点 message_id |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 最后更新时间 |
| user_id | TEXT NULLABLE | 预留：用户ID（Phase 2） |
| title | TEXT NULLABLE | 对话标题（可由首条消息自动生成） |
| metadata | JSON | 扩展元数据 |

#### 表2: `messages`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PRIMARY KEY | message_id |
| conversation_id | TEXT FK | 所属对话 |
| parent_id | TEXT NULLABLE | 父消息ID（实现树结构） |
| content | TEXT | 用户消息内容 |
| thread_id | TEXT | 关联的 LangGraph 线程ID |
| graph_response | TEXT NULLABLE | Graph 最终响应 |
| created_at | TIMESTAMP | 创建时间 |
| metadata | JSON | 扩展元数据（可存储简化的执行摘要） |

#### 表3: `artifact_sessions`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PRIMARY KEY | session_id（与 conversation_id 相同） |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 最后更新时间 |

#### 表4: `artifacts` 🆕 增加乐观锁字段
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | artifact_id |
| session_id | TEXT FK | 所属会话 |
| content_type | TEXT | 内容类型 (markdown/python/etc) |
| title | TEXT | 标题 |
| content | TEXT | 🆕 当前内容（冗余存储，避免每次查版本表） |
| current_version | INTEGER | 当前版本号 |
| **lock_version** | **INTEGER DEFAULT 1** | 🆕 **乐观锁版本号** |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 最后更新时间 |
| metadata | JSON | 扩展元数据 |
| PRIMARY KEY (id, session_id) | | 复合主键 |

#### 表5: `artifact_versions`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 自增ID |
| artifact_id | TEXT | 所属 artifact |
| session_id | TEXT | 所属会话 |
| version | INTEGER | 版本号 |
| content | TEXT | 版本内容 |
| update_type | TEXT | 更新类型 (create/update/rewrite) |
| changes | JSON NULLABLE | 变更记录 [(old, new), ...] |
| created_at | TIMESTAMP | 创建时间 |
| UNIQUE (artifact_id, session_id, version) | | 唯一约束 |

### 4.3 🆕 乐观锁机制

**实现方式**：
```python
class ArtifactRepository:
    async def update_artifact(
        self, 
        session_id: str, 
        artifact_id: str, 
        content: str,
        expected_lock_version: int
    ) -> Artifact:
        """
        更新 Artifact 内容（带乐观锁）
        
        Raises:
            VersionConflictError: 版本冲突时抛出
        """
        result = await self.session.execute(
            update(Artifact)
            .where(
                Artifact.id == artifact_id,
                Artifact.session_id == session_id,
                Artifact.lock_version == expected_lock_version  # 乐观锁检查
            )
            .values(
                content=content,
                current_version=Artifact.current_version + 1,
                lock_version=Artifact.lock_version + 1,  # 递增锁版本
                updated_at=datetime.now()
            )
        )
        
        if result.rowcount == 0:
            raise VersionConflictError(
                f"Artifact {artifact_id} has been modified by another process"
            )
        
        await self.session.commit()
        return await self.get_artifact(session_id, artifact_id)
```

**冲突处理策略**：
- Agent 工具调用失败时，返回错误信息让 Agent 决定重试或读取最新版
- 前端可展示冲突提示，让用户选择保留哪个版本

---

## 5. 🆕 LangGraph Checkpointer 配置

### 5.1 替换 MemorySaver

**当前代码**（需要修改）：
```python
# graph.py
from langgraph.checkpoint.memory import MemorySaver

def compile(self, ...):
    if checkpointer is None:
        checkpointer = MemorySaver()  # ❌ 内存存储，重启丢失
```

**改造后**：
```python
# graph.py
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

async def compile(self, db_path: str = "data/artifactflow.db", ...):
    if checkpointer is None:
        # ✅ 使用 SQLite 持久化，与 App DB 共享同一文件
        checkpointer = AsyncSqliteSaver.from_conn_string(
            f"sqlite+aiosqlite:///{db_path}"
        )
```

### 5.2 配置建议

| 配置项 | 推荐值 | 说明 |
|--------|--------|------|
| 数据库文件 | `data/artifactflow.db` | 与 App DB 共享，简化部署 |
| 连接池大小 | 5 | SQLite 有写锁限制，不宜过大 |
| WAL 模式 | 启用 | 提高并发读性能 |

```python
# database.py
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    "sqlite+aiosqlite:///data/artifactflow.db",
    echo=False,
    pool_size=5,
    connect_args={"check_same_thread": False}
)

# 启用 WAL 模式
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
```

---

## 6. 架构设计

### 6.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Application Layer                        │
│         (ExecutionController, Agents, Tools)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Manager Layer (改造重点)                 │
│   ┌─────────────────────┐   ┌─────────────────────────┐    │
│   │ ConversationManager │   │    ArtifactManager      │    │
│   │  - 内存缓存热数据     │   │  - 内存缓存热数据        │    │
│   │  - 调用 Repository    │   │  - 调用 Repository       │    │
│   │  - 🆕 依赖注入        │   │  - 🆕 依赖注入           │    │
│   └─────────────────────┘   └─────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Repository Layer (新增)                   │
│   ┌─────────────────────┐   ┌─────────────────────────┐    │
│   │ConversationRepository│   │  ArtifactRepository    │    │
│   │  - CRUD 操作         │   │  - CRUD 操作            │    │
│   │  - 树结构查询        │   │  - 版本管理             │    │
│   │  - 🆕 ORM Only       │   │  - 🆕 乐观锁            │    │
│   └─────────────────────┘   └─────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Database Layer (新增)                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                   DatabaseManager                    │  │
│   │  - 连接池管理                                        │  │
│   │  - 事务管理                                          │  │
│   │  - 🆕 WAL 模式                                       │  │
│   └─────────────────────────────────────────────────────┘  │
│                           │                                  │
│              ┌────────────┴────────────┐                    │
│              ▼                         ▼                    │
│     App DB (SQLite)          LangGraph Checkpointer        │
│     (conversations,          (AsyncSqliteSaver)            │
│      messages, artifacts)    (thread states)               │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 缓存策略

**热数据定义**：
- 当前活跃的 Conversation（最近访问的 N 个）
- 当前活跃的 ArtifactSession
- 最近修改的 Artifact 内容

**缓存失效策略**：
- LRU 淘汰：缓存容量达到阈值时淘汰最久未使用的数据
- 写穿透：写操作同时更新缓存和数据库
- 延迟加载：首次访问时从数据库加载到缓存

---

## 7. 文件结构规划

### 7.1 新增文件

```
src/
├── db/                          # 新增：数据库层
│   ├── __init__.py
│   ├── database.py              # DatabaseManager：连接池、事务管理
│   ├── models.py                # SQLAlchemy ORM 模型定义
│   └── migrations/              # 数据库迁移脚本
│       ├── __init__.py
│       └── versions/
│           └── 001_initial_schema.py
│
├── repositories/                # 新增：数据访问层
│   ├── __init__.py
│   ├── base.py                  # BaseRepository 抽象类
│   ├── conversation_repo.py     # ConversationRepository
│   └── artifact_repo.py         # ArtifactRepository
│
├── core/
│   ├── conversation_manager.py  # 改造：从 controller.py 分离
│   └── ... (其他文件)
│
└── tools/implementations/
    └── artifact_ops.py          # 改造：ArtifactStore → ArtifactManager
```

### 7.2 改造文件

| 文件 | 改造内容 |
|------|----------|
| `controller.py` | 将 `ConversationManager` 类移出到独立文件，改为使用 Repository |
| `artifact_ops.py` | 将 `ArtifactStore` 改造为 `ArtifactManager`，底层使用 Repository，🆕 移除全局单例 |
| `graph.py` | 🆕 更新 Checkpointer 为 AsyncSqliteSaver |
| `requirements.txt` | 添加 `sqlalchemy>=2.0`, `aiosqlite` |

---

## 8. 核心类设计要点

### 8.1 DatabaseManager

**职责**：
- 管理数据库连接（支持异步）
- 提供事务上下文管理器
- 初始化数据库 schema
- 🆕 配置 WAL 模式

**设计要点**：
- 使用 `aiosqlite` 实现异步 SQLite 访问
- 提供同步和异步两种接口（兼容现有代码）
- 数据库路径可配置（默认 `data/artifactflow.db`）

### 8.2 ConversationRepository

**职责**：
- Conversation CRUD
- Message CRUD
- 树结构查询（获取从根到指定节点的路径）

**关键方法**：
```python
- create_conversation(conv_id) → Conversation
- get_conversation(conv_id) → Optional[Conversation]
- add_message(conv_id, message) → Message
- get_message_path(conv_id, to_message_id) → List[Message]
- update_response(conv_id, message_id, response)
- list_conversations(user_id=None, limit, offset) → List[Conversation]
```

### 8.3 ArtifactRepository

**职责**：
- ArtifactSession CRUD
- Artifact CRUD
- 版本管理
- 🆕 乐观锁并发控制

**关键方法**：
```python
- create_session(session_id) → ArtifactSession
- get_session(session_id) → Optional[ArtifactSession]
- create_artifact(session_id, artifact_data) → Artifact
- get_artifact(session_id, artifact_id) → Optional[Artifact]
- update_artifact(session_id, artifact_id, content, expected_lock_version) → Artifact  # 🆕 乐观锁
- save_version(session_id, artifact_id, version_data) → ArtifactVersion
- get_version(session_id, artifact_id, version) → Optional[ArtifactVersion]
- list_artifacts(session_id) → List[Artifact]
```

### 8.4 ConversationManager（改造）

**改造要点**：
- 保持现有 API 不变（向后兼容）
- 内部增加 `ConversationRepository` 依赖
- 增加内存缓存层（`Dict[str, Conversation]`）
- 写操作：同时更新缓存和数据库
- 读操作：优先从缓存读取，miss 时从数据库加载
- 🆕 **通过构造函数注入依赖，不使用全局单例**

**缓存管理**：
```
- 最大缓存 conversation 数量：100（可配置）
- 淘汰策略：LRU
- 提供 `load_conversation(conv_id)` 方法显式加载到缓存
- 提供 `evict_conversation(conv_id)` 方法手动淘汰
```

### 8.5 ArtifactManager（原 ArtifactStore 改造）

**改造要点**：
- 保持现有工具调用 API 不变
- 内部增加 `ArtifactRepository` 依赖
- 增加内存缓存（当前活跃 session 的 artifacts）
- `Artifact.update()` 中的 diff-match-patch 逻辑保持不变
- 版本保存时同步写入数据库
- 🆕 **通过构造函数注入依赖，不使用全局单例**
- 🆕 **更新操作使用乐观锁**

---

## 9. 实施步骤

### Phase 1: 基础设施（预计 2-3 天）

1. **创建 `db/` 模块**
   - 实现 `DatabaseManager`（含 WAL 配置）
   - 编写 SQLAlchemy ORM 模型（含 `lock_version` 字段）
   - 编写初始化 migration
   - 🆕 配置 AsyncSqliteSaver 与 App DB 共享

2. **创建 `repositories/` 模块**
   - 实现 `BaseRepository`
   - 实现 `ConversationRepository`
   - 实现 `ArtifactRepository`（含乐观锁）

3. **编写单元测试**
   - Repository 层的 CRUD 测试
   - 树结构查询测试
   - 🆕 乐观锁冲突测试

### Phase 2: Manager 层改造（预计 2-3 天）

1. **分离 ConversationManager**
   - 从 `controller.py` 移出到独立文件
   - 添加 Repository 依赖注入
   - 实现缓存逻辑
   - 🆕 移除全局单例模式

2. **改造 ArtifactStore → ArtifactManager**
   - 重构为使用 Repository
   - 保持 `Artifact` 类的核心逻辑不变
   - 实现缓存逻辑
   - 🆕 移除全局单例 `_artifact_store`
   - 🆕 集成乐观锁

3. **更新依赖方**
   - `controller.py` 使用新的 ConversationManager
   - `graph.py` 使用新的 ArtifactManager
   - 🆕 `graph.py` 替换 MemorySaver 为 AsyncSqliteSaver

4. **集成测试**
   - 运行现有的 `core_graph_test.py` 确保功能正常
   - 添加持久化相关的测试用例

### Phase 3: 优化和清理（预计 1-2 天）

1. **性能优化**
   - 添加数据库索引
   - 优化批量查询

2. **配置外部化**
   - 数据库路径配置
   - 缓存大小配置

3. **清理工作**
   - 移除旧的内存存储代码
   - 更新文档

---

## 10. 后续扩展预留

### 10.1 用户系统（Phase 2）

**预留设计**：
- `conversations` 表的 `user_id` 字段
- `artifact_sessions` 表添加 `user_id` 字段
- Repository 方法支持 `user_id` 过滤

**迁移路径**：
1. 添加 `users` 表
2. 为现有数据添加默认 user_id
3. 更新 Repository 查询方法
4. 更新 Manager 层传递 user_id

### 10.2 PostgreSQL 迁移

**预留设计**：
- 使用 SQLAlchemy ORM（数据库无关）
- Repository 层抽象数据库操作
- 使用 Alembic 管理 migrations
- 🆕 ORM Only 规范确保迁移无痛

**迁移路径**：
1. 修改 `DatabaseManager` 连接字符串
2. 运行 migration
3. 数据迁移脚本

---

## 11. 注意事项

### 11.1 并发安全

- SQLite 在写入时有锁，需要注意并发场景
- 建议使用 WAL 模式提高并发性能
- 🆕 使用乐观锁处理 Artifact 并发更新
- 对于高并发场景，考虑直接使用 PostgreSQL

### 11.2 数据一致性

- 缓存和数据库可能出现不一致
- 关键写操作使用事务
- 提供强制刷新缓存的接口
- 🆕 App DB 是真相来源，LangGraph Checkpointer 仅用于执行

### 11.3 向后兼容

- 所有 public API 保持不变
- 现有测试应该全部通过
- 通过依赖注入支持测试时使用内存数据库

---

## 12. 依赖清单

```txt
# 新增依赖
sqlalchemy>=2.0.0
aiosqlite>=0.19.0
alembic>=1.12.0  # 可选：用于生产环境的数据库迁移管理
langgraph-checkpoint-sqlite>=1.0.0  # 🆕 LangGraph SQLite Checkpointer
```

---

## 附录：架构演进路线图

1. **Phase 1 (当前目标)**：单机 Docker + SQLite
   - 实现双层存储，确保数据不丢失
   - 实现乐观锁，确保 Artifact 数据一致
   - 代码全异步化，使用依赖注入

2. **Phase 2 (少量并发)**：分离数据库
   - SQLite → PostgreSQL 容器
   - 利用 ORM 优势平滑迁移

3. **Phase 3 (生产并发)**：水平扩展
   - Nginx 负载均衡 + 多实例 API 容器
   - 引入 Redis 做分布式缓存和锁
