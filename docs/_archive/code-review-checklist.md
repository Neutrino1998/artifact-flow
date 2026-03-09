# Code Review Checklist

适用于 Python (FastAPI) + React (Next.js/TypeScript) 技术栈，轻量生产级、小团队维护。
可用于 PR review，也可用于整体代码库审查。

## 总原则

**如非必要不引新依赖** — 能用现有技术栈 robust 覆盖的就不引入重型框架。引入前先问：现有方案缺什么能力？是当前就需要还是假设未来需要？

- TaskManager (asyncio.Task + Semaphore + Redis 锁) → 不需要 Celery
- 结构化日志 + PostgreSQL → 不需要 SkyWalking / Jaeger
- Redis Streams → 不需要 Kafka / RabbitMQ
- FastAPI Depends → 不需要独立 DI 框架
- Zustand → 不需要 Redux

**不做向后兼容** — 快速迭代阶段，保持代码 clean 优先于兼容旧行为。

- 删掉的代码直接删，不留 `# removed`、不 re-export、不 rename 成 `_deprecated_xxx`
- 改接口直接改，不加 shim 层兼容旧调用方
- 改数据结构直接改，不写数据迁移脚本（开发阶段数据可丢弃）
- 不为假设的未来需求预留扩展点（不加空接口、不建方言适配层、不搞 feature flag）

---

## 必须满足

### 单一职责 / 改动边界清晰
- [ ] 一个函数只做一件事；每个变更只有一个清晰目标（跨多文件正常，但每个文件的改动理由可解释）
- [ ] 分层边界不越权：Router 只管 HTTP，Manager/Controller 管业务逻辑，Repository 管数据访问
- [ ] Agent 不直接操作数据库，Router 不写业务逻辑

### Fail Fast — 有问题尽早暴露
- [ ] 无 `except: pass` 或静默吞异常 — 数据库错误、连接失败必须抛出
- [ ] 不猜测、不兜底：前端调用后端用生成的类型，不手写端口/字段猜测；后端读配置缺必填项直接启动失败
- [ ] 外部边界（用户输入、LLM 返回、工具调用、外部 API）有明确的错误处理和错误信息
- [ ] 内部调用信任框架保证，不加多余的防御性校验（不给不可能为 None 的值加 `if x is not None`）；但跨模块边界的关键契约点可加 assert
- [ ] 默认值要有明确来源（config / schema default），不在业务代码里 `or` 一个 fallback

### 类型安全
- [ ] 后端：API 入出通过 Pydantic schema 校验，函数签名有类型标注
- [ ] 前端：不用 `any`，API 类型从 OpenAPI 生成（`npm run generate-types`）不手写

### 安全
- [ ] 日志 / LogEntry 不输出 token、API key、密码等敏感信息
- [ ] 用户输入经过 Pydantic 校验后再进入业务层

---

## 应该满足

### DRY 但不过度
- [ ] 重复 3 次以上再抽象，2 次重复可以接受
- [ ] 不为消除两行重复搞一个 utility 函数

### 依赖方向单向
```
Router → Manager/Controller → Repository → DB
  ↓
Agent → Tool
```
- [ ] 下层不 import 上层
- [ ] 同层之间尽量不互相依赖

### 命名即文档
- [ ] 函数名说清做什么：`list_active_skills()` 而不是 `get_data()`
- [ ] 变量名说清是什么：`conv_id` 而不是 `id`，`retry_count` 而不是 `n`
- [ ] 不需要注释的代码 > 需要注释才能读懂的代码

### 状态最小化
- [ ] 后端 request-scoped 实例不持有跨请求状态
- [ ] 前端 Zustand store 只存跨组件共享状态，局部状态用 `useState`
- [ ] 全局单例有明确理由（StreamManager、TaskManager 等基础设施组件）

---

## 最好满足（架构级 review）

### 可观测性
- [ ] 关键操作有日志 / LogEntry
- [ ] 异步操作有超时，不能无限等待
- [ ] request_id 贯穿一次请求的所有日志

### 可测试性
- [ ] 依赖注入而不是硬编码（FastAPI Depends / 构造函数参数）
- [ ] 纯函数优先，副作用推到边界
- [ ] 不可测的代码大概率职责不清晰 — 考虑重构

### 变更成本可预估
- [ ] 新增 Agent = 写子类 + 注册到 graph，不改框架
- [ ] 新增 Tool = 继承 BaseTool + 注册到 Registry，不改框架
- [ ] 小功能改 8+ 文件且出现重复改动模式 → 抽象可能有问题，review 时讨论
