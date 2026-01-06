# ArtifactFlow 持久化改造方案

> 版本: v1.0 | 优先级: P0（API和前端的前置依赖）

## 1. 改造目标

将当前基于内存的 `ConversationManager` 和 `ArtifactStore` 改造为 SQLite 持久化存储，实现：

1. **数据持久化**：服务重启后数据不丢失
2. **解耦存储**：Conversation 和 Artifact 独立存储，通过 ID 关联
3. **可扩展性**：为后续 PostgreSQL 迁移和用户系统预留接口
4. **性能平衡**：热数据内存缓存 + 冷数据数据库存储

---

## 2. 数据库 Schema 设计

### 2.1 核心原则

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

### 2.2 表结构设计

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

#### 表4: `artifacts`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | artifact_id |
| session_id | TEXT FK | 所属会话 |
| content_type | TEXT | 内容类型 (markdown/python/etc) |
| title | TEXT | 标题 |
| current_version | INTEGER | 当前版本号 |
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

---

## 3. 架构设计

### 3.1 分层架构

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
│   │  (原 ConversationManager) │  (原 ArtifactStore)     │    │
│   │  - 内存缓存热数据     │   │  - 内存缓存热数据        │    │
│   │  - 调用 Repository    │   │  - 调用 Repository       │    │
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
│   │  - Migration 支持                                    │  │
│   └─────────────────────────────────────────────────────┘  │
│                           │                                  │
│                           ▼                                  │
│                      SQLite / PostgreSQL                     │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 缓存策略

**热数据定义**：
- 当前活跃的 Conversation（最近访问的 N 个）
- 当前活跃的 ArtifactSession
- 最近修改的 Artifact 内容

**缓存失效策略**：
- LRU 淘汰：缓存容量达到阈值时淘汰最久未使用的数据
- 写穿透：写操作同时更新缓存和数据库
- 延迟加载：首次访问时从数据库加载到缓存

---

## 4. 文件结构规划

### 4.1 新增文件

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

### 4.2 改造文件

| 文件 | 改造内容 |
|------|----------|
| `controller.py` | 将 `ConversationManager` 类移出到独立文件，改为使用 Repository |
| `artifact_ops.py` | 将 `ArtifactStore` 改造为 `ArtifactManager`，底层使用 Repository |
| `graph.py` | 更新 `_get_or_create_session` 的调用方式 |
| `requirements.txt` | 添加 `sqlalchemy>=2.0`, `aiosqlite` |

---

## 5. 核心类设计要点

### 5.1 DatabaseManager

**职责**：
- 管理数据库连接（支持异步）
- 提供事务上下文管理器
- 初始化数据库 schema

**设计要点**：
- 使用 `aiosqlite` 实现异步 SQLite 访问
- 提供同步和异步两种接口（兼容现有代码）
- 数据库路径可配置（默认 `data/artifactflow.db`）

### 5.2 ConversationRepository

**职责**：
- Conversation CRUD
- Message CRUD
- 树结构查询（获取从根到指定节点的路径）

**关键方法**：
```
- create_conversation(conv_id) → Conversation
- get_conversation(conv_id) → Optional[Conversation]
- add_message(conv_id, message) → Message
- get_message_path(conv_id, to_message_id) → List[Message]
- update_response(conv_id, message_id, response)
- list_conversations(user_id=None, limit, offset) → List[Conversation]
```

### 5.3 ArtifactRepository

**职责**：
- ArtifactSession CRUD
- Artifact CRUD
- 版本管理

**关键方法**：
```
- create_session(session_id) → ArtifactSession
- get_session(session_id) → Optional[ArtifactSession]
- create_artifact(session_id, artifact_data) → Artifact
- get_artifact(session_id, artifact_id) → Optional[Artifact]
- update_artifact(session_id, artifact_id, ...) → Artifact
- save_version(session_id, artifact_id, version_data) → ArtifactVersion
- get_version(session_id, artifact_id, version) → Optional[ArtifactVersion]
- list_artifacts(session_id) → List[Artifact]
```

### 5.4 ConversationManager（改造）

**改造要点**：
- 保持现有 API 不变（向后兼容）
- 内部增加 `ConversationRepository` 依赖
- 增加内存缓存层（`Dict[str, Conversation]`）
- 写操作：同时更新缓存和数据库
- 读操作：优先从缓存读取，miss 时从数据库加载

**缓存管理**：
```
- 最大缓存 conversation 数量：100（可配置）
- 淘汰策略：LRU
- 提供 `load_conversation(conv_id)` 方法显式加载到缓存
- 提供 `evict_conversation(conv_id)` 方法手动淘汰
```

### 5.5 ArtifactManager（原 ArtifactStore 改造）

**改造要点**：
- 保持现有工具调用 API 不变
- 内部增加 `ArtifactRepository` 依赖
- 增加内存缓存（当前活跃 session 的 artifacts）
- `Artifact.update()` 中的 diff-match-patch 逻辑保持不变
- 版本保存时同步写入数据库

---

## 6. 实施步骤

### Phase 1: 基础设施（预计 2-3 天）

1. **创建 `db/` 模块**
   - 实现 `DatabaseManager`
   - 编写 SQLAlchemy ORM 模型
   - 编写初始化 migration

2. **创建 `repositories/` 模块**
   - 实现 `BaseRepository`
   - 实现 `ConversationRepository`
   - 实现 `ArtifactRepository`

3. **编写单元测试**
   - Repository 层的 CRUD 测试
   - 树结构查询测试

### Phase 2: Manager 层改造（预计 2-3 天）

1. **分离 ConversationManager**
   - 从 `controller.py` 移出到独立文件
   - 添加 Repository 依赖注入
   - 实现缓存逻辑

2. **改造 ArtifactStore → ArtifactManager**
   - 重构为使用 Repository
   - 保持 `Artifact` 类的核心逻辑不变
   - 实现缓存逻辑

3. **更新依赖方**
   - `controller.py` 使用新的 ConversationManager
   - `graph.py` 使用新的 ArtifactManager

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

## 7. 后续扩展预留

### 7.1 用户系统（Phase 2）

**预留设计**：
- `conversations` 表的 `user_id` 字段
- `artifact_sessions` 表添加 `user_id` 字段
- Repository 方法支持 `user_id` 过滤

**迁移路径**：
1. 添加 `users` 表
2. 为现有数据添加默认 user_id
3. 更新 Repository 查询方法
4. 更新 Manager 层传递 user_id

### 7.2 PostgreSQL 迁移

**预留设计**：
- 使用 SQLAlchemy ORM（数据库无关）
- Repository 层抽象数据库操作
- 使用 Alembic 管理 migrations

**迁移路径**：
1. 修改 `DatabaseManager` 连接字符串
2. 运行 migration
3. 数据迁移脚本

---

## 8. 注意事项

### 8.1 并发安全

- SQLite 在写入时有锁，需要注意并发场景
- 建议使用 WAL 模式提高并发性能
- 对于高并发场景，考虑直接使用 PostgreSQL

### 8.2 数据一致性

- 缓存和数据库可能出现不一致
- 关键写操作使用事务
- 提供强制刷新缓存的接口

### 8.3 向后兼容

- 所有 public API 保持不变
- 现有测试应该全部通过
- 通过依赖注入支持测试时使用内存数据库

---

## 9. 依赖清单

```txt
# 新增依赖
sqlalchemy>=2.0.0
aiosqlite>=0.19.0
alembic>=1.12.0  # 可选：用于生产环境的数据库迁移管理
```
