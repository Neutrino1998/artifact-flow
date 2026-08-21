# 应用与站点配置

生产目标机上的可变配置集中在 `/opt/artifactflow/control/`。Release 目录是只读快照，不承担现场状态。

```text
/opt/artifactflow/control/
├── site.toml
├── .env
├── auth/                        # Backend 企业认证 Provider
├── certs/                       # Caddy 入站证书/私钥
├── trust/
│   └── ca-certificates/         # Backend 出站 HTTPS 信任锚
├── site/
├── maintenance/
└── autoheal/
```

## `site.toml`

`site.toml` 只声明部署能力，不存 Secret：

```toml
schema = 1
executor = "local"
tls = "static"
infra = "bundled"
sandbox_runtime = "runsc"
scratch_root = "/data/artifactflow/sandbox"
backend_replicas = 2
ready_timeout_seconds = 120
```

| 字段 | 允许值 / 说明 |
|---|---|
| `schema` | 当前必须为 `1` |
| `executor` | `local`；`ansible` 仍为实验性多机路径 |
| `tls` | `static` 或 `acme` |
| `infra` | `bundled` 或 `external` |
| `sandbox_runtime` | 生产使用 `runsc`；`runc` 是显式降低隔离的 trusted/dev 模式 |
| `scratch_root` | 目标机上的绝对路径，必须是独立挂载点 |
| `backend_replicas` | 单机 Backend 副本数，至少为 1 |
| `ready_timeout_seconds` | Apply 后等待 Caddy readiness 的总 deadline |
| `inventory` | 仅 `executor = "ansible"` |
| `ansible_ee_image` | 仅 Ansible，必须是 `@sha256:` 固定镜像 |

未知字段和不支持的组合会被 `afctl site validate` 拒绝。

## `control/.env`

`site init` 会生成随机 JWT、Fernet 和 bundled PostgreSQL 密钥。Operator 需要补充模型和工具 Secret。

始终必需：

| 变量 | 用途 |
|---|---|
| `ARTIFACTFLOW_JWT_SECRET` | JWT 签名，并以域隔离 HMAC 派生 PAT 校验密钥 |
| `ARTIFACTFLOW_CREDENTIAL_KEY` | 加密 Tool/MCP 凭证；即使暂时没有凭证工具也必需 |
| `ARTIFACTFLOW_DATABASE_URL` 或 `ARTIFACTFLOW_DATABASE_URLS` | 数据库连接；后者是逗号分隔的 PostgreSQL 地址列表 |
| `ARTIFACTFLOW_REDIS_URL` | 生产运行时、SSE、lease 与多副本协调 |
| `ARTIFACTFLOW_REDIS_KEY_PREFIX` | Redis 命名空间；启用 Redis 时必需 |

按部署能力必需：

- `infra = "bundled"`：`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`；
- `tls = "acme"`：`AF_DOMAIN`（裸域名，不含 scheme、端口或路径）、`AF_ACME_EMAIL`，且外部端口必须是 80/443；
- Model：对应供应商的 API Key；
- Tool/MCP：定义引用的 `TOOL_SECRET_*`。

应用调节项：

| 变量 | 级别 | 默认 | 何时调整 |
|---|---|---:|---|
| `ARTIFACTFLOW_EXECUTION_TIMEOUT` | 标准 | `3600` 秒 | Agent 引擎 loop 的 deadline；含权限等待，不含结束后的持久化 |
| `ARTIFACTFLOW_PERMISSION_TIMEOUT` | 标准 | `300` 秒 | 等待一次用户确认的时间 |
| `ARTIFACTFLOW_MAX_CONCURRENT_TASKS` | 标准 | `32` | 按模型、DB 和主机容量限制并发 |
| `ARTIFACTFLOW_REDIS_CLUSTER` | 高级 | `false` | 外部 Redis 使用 Cluster 拓扑时开启 |
| `ARTIFACTFLOW_REDIS_MAX_CONNECTIONS` | 高级 | `64` | 每个 Backend 的 Redis 连接池上限；结合副本数和任务并发调整 |
| `ARTIFACTFLOW_SSO_START_IP_MAX_REQUESTS` | 标准 | `60` | 单一客户端 IP 在 SSO start 窗口内的准入上限 |
| `ARTIFACTFLOW_SSO_START_GLOBAL_MAX_REQUESTS` | 标准 | `120` | 所有 Backend 通过 Redis 共享的 SSO start 窗口上限 |
| `ARTIFACTFLOW_SSO_START_RATE_WINDOW_SEC` | 标准 | `60` 秒 | SSO start 固定窗口长度 |
| `ARTIFACTFLOW_SSO_STATE_MAX_PENDING` | 标准 | `1000` | 一次性 SSO state 的硬容量上限 |
| `ARTIFACTFLOW_SSO_USERINFO_MAX_CONNECTIONS` | 标准 | `10` | 每个 Backend 的 userinfo 出站连接上限 |
| `ARTIFACTFLOW_MAX_UPLOAD_SIZE` | 高级 | `200 MiB` | 单文件上传上限；调整时同步核对代理批量总量和存储 envelope |
| `ARTIFACTFLOW_ARTIFACT_USER_QUOTA_BYTES` | 标准 | `2 GiB` | 每用户二进制 Artifact 配额；`0` 表示不限 |
| `ARTIFACTFLOW_MAX_BULK_IMPORT_ROWS` | 高级 | `1000` | 单次用户 CSV 导入的数据行上限 |
| `ARTIFACTFLOW_MAX_BULK_IMPORT_BYTES` | 高级 | `5 MiB` | 单次用户 CSV 导入的文件字节上限 |
| `ARTIFACTFLOW_ADMIN_PRIVACY_MODE` | 标准 | `false` | 开启后，会话监控不返回账户关联字段和上传文件原名，并禁止管理员读取任何 Artifact 的内容 |
| `ARTIFACTFLOW_SKILL_USER_MAX_PRIVATE_COUNT` | 标准 | `3` | `-1` 不限，`0` 禁止个人导入 |
| `ARTIFACTFLOW_CORS_ORIGINS` | 标准 | 本地前端 | 直接跨域访问 API 时设置明确 origin 列表 |
| `ARTIFACTFLOW_CORS_ALLOW_CREDENTIALS` | 高级 | `true` | 跨域请求是否允许凭证；为 `true` 时 origins 不得包含 `*` |
| `ARTIFACTFLOW_DB_COMMAND_TIMEOUT` | 标准 | `30` 秒 | PostgreSQL 单语句上限；设 `0` 禁用 |
| `ARTIFACTFLOW_DATABASE_POOL_SIZE` | 高级 | `10` | 每个 Backend 的数据库常驻连接池容量，必须至少为 `1` |
| `ARTIFACTFLOW_DATABASE_MAX_OVERFLOW` | 高级 | `20` | 数据库连接池耗尽后允许临时增加的连接数，必须至少为 `0` |
| `ARTIFACTFLOW_DATABASE_POOL_TIMEOUT` | 高级 | `30` 秒 | 等待数据库连接池槽位的最长时间；`0` 表示不等待、立即失败 |
| `ARTIFACTFLOW_JWT_EXPIRY_SECONDS` | 标准 | `28800` 秒 | 本地密码和远程身份共用的登录会话有效期 |
| `ARTIFACTFLOW_LOGIN_MAX_FAILURES` | 标准 | `5` | 用户名或客户端 IP 在窗口内达到该失败次数后临时锁定 |
| `ARTIFACTFLOW_LOGIN_FAILURE_WINDOW_SEC` | 标准 | `900` 秒 | 登录失败累计窗口及临时锁定窗口 |
| `ARTIFACTFLOW_PASSWORD_MIN_LENGTH` | 标准 | `8` | 新口令最小字符数，允许 `1–72`；前端通过 `/api/v1/meta` 同步提示 |
| `ARTIFACTFLOW_PASSWORD_REQUIRE_LETTER` | 标准 | `true` | 新口令是否必须包含字母 |
| `ARTIFACTFLOW_PASSWORD_REQUIRE_DIGIT` | 标准 | `true` | 新口令是否必须包含数字 |
| `ARTIFACTFLOW_PASSWORD_REQUIRE_SYMBOL` | 标准 | `true` | 新口令是否必须包含符号 |

密码 hash 保持标准 bcrypt 格式。bcrypt 最多使用 72 个输入字节，因此新口令在 UTF-8
编码后不得超过 72 字节；这是代码内部固定的算法边界，不能通过环境变量调整。历史版本
可能曾接受更长输入并只 hash 前 72 字节，为避免已有账户失效，登录和“当前口令”校验继续
兼容该行为；用户设置新口令时会收敛到明确的 72 字节边界，不需要改写已有 hash。

`src/config.py` 中的字段先分为两类：

- **运维配置**：本页、相关专题配置文档或 `.env.example` 明确命名的 Secret、连接信息、
  路径、容量、超时和策略，是稳定的 `ARTIFACTFLOW_*` 配置契约。源码中的“标准运维”与
  “高级运维”标记必须与这些活跃文档一致；“标准”项可独立调整，“高级”项仍受支持，
  但调整时必须同时检查代理、数据库和容器层的关联约束；
- **代码内部参数**：grep/fuzzy 算法、Prompt/预览截断、固定协议映射和展示文案，默认值
  随代码评审和 Release 演进，不作为常规部署旋钮。

当前 `Settings` 保持统一、简单的加载方式，字段在技术上都能被环境变量覆盖；上述分类
是支持范围和维护责任的边界，不额外引入运行时过滤、第二配置源或优先级规则。

`ARTIFACTFLOW_MAX_CONCURRENT_TASKS` 对每个 Backend 进程内唯一的 TaskSupervisor capacity gate 生效。超过容量的 Conversation turn 保持 QUEUED：它继续持有并续租 Conversation lease，但尚未标记为可交互 RUNNING，因此 inject/cancel 会按现有 409 契约拒绝。多 Backend 副本必须使用 Redis，让 lease、interactive、interrupt、cancel 与 SSE 状态跨进程共享；进程内 TaskSupervisor 只保留本进程 task 引用，不承担崩溃恢复。

SSO 的五个容量项属于部署资源边界，不写入认证 Provider YAML。per-IP 超限返回 429；
Redis 共享的全局窗口或 state 容量耗尽返回 503。userinfo 连接数是每个 Backend 的
独立上限，因此部署总出站并发约为该值乘以 Backend 副本数；它不复用 Conversation
TaskSupervisor 的信号量，登录和 Agent 执行不会互相占用准入名额。

`ARTIFACTFLOW_ADMIN_PRIVACY_MODE=true` 是部署级的 Admin 会话监控边界：会话和反馈接口把属主显示为“匿名用户”、拒绝按 `user_id` 筛选、把附件名称显示为通用名称。Admin Artifact 列表只返回受保护的通用条目，任何来源的 Artifact 内容、版本和 raw/blob 接口都返回 404；`artifact_created` 和 `artifact_updated` live 事件也不向 Admin SSE 转发。Prompt 重建作为排障能力保持开放；它和会话正文、模型输出、工具事件一样可能包含附件的名称、片段，甚至完整内容，不做自由文本扫描。因此该模式降低的是账户直接关联和文件接口的直接读取风险，不承诺内容层面的完全匿名。

`ARTIFACTFLOW_EXECUTION_TIMEOUT` 只包住 `AgentRuntime` 的引擎 loop。触发后 Runtime 返回 timeout stop reason，再由 Conversation turn 的统一结束路径写入 `timed_out` terminal、事件和展示 response；Artifact flush、事件写入等 post-processing 刻意位于该 deadline 之外。持久化查询由 `ARTIFACTFLOW_DB_COMMAND_TIMEOUT` 分别约束，因此不要把 execution timeout 当成包含所有 DB cleanup 的 HTTP 请求总时限。

Redis 中的 lease、interactive、interrupt、cancel 和 stream 是会过期的 live coordination state，不是执行历史；PostgreSQL 中的 Conversation、MessageEvent、配置注册表和 Artifact 才是 durable state。Admin runtime 页面只读观察这些状态，不会把某一侧反写或同步成另一侧。未配置 Redis 的进程内模式只适合单 Backend，本地进程退出后其 live state 会自然消失。

## 出站 HTTPS 信任

HTTP Tool、MCP 以及其他 Backend HTTPS 客户端默认验证服务端证书。内网服务由
企业私有 CA 签发，或确实使用自签 leaf 时，把信任证书放在：

```text
/opt/artifactflow/control/trust/ca-certificates/<name>.crt
```

目录可为空，此时 Backend 只使用镜像默认公共 CA。证书采用 Debian
`update-ca-certificates` 接受的 PEM `.crt` 格式，不要放私钥。优先放稳定的企业根
CA：服务 leaf 续期后只要仍由该根签发，就不必更新 ArtifactFlow。只有没有上级 CA
的自签 leaf 才直接放 leaf，它到期或轮换时需要同步替换。

示例：

```bash
sudo install -d -m 0755 /opt/artifactflow/control/trust/ca-certificates
sudo install -m 0644 company-root.crt \
  /opt/artifactflow/control/trust/ca-certificates/company-root.crt
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow site validate
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow apply current
```

Apply 会重建所有 Backend 副本，在 Python 启动前把这些证书合入容器系统 CA
bundle；TLS 有效期与主机名验证保持开启。不要设置 `verify=false`，也不要手工修改
容器中的 `certifi/cacert.pem`，这些修改会在容器重建时丢失。移除一个信任锚也要
再次执行 `apply current`，Ansible executor 会用源目录精确替换远端目录，避免旧 CA
残留。容器启动只检查 `.crt` 是否恰好包含一张可解析的 PEM X.509 证书；损坏文件
会阻断 Apply，但不会在这里检查证书有效期、SAN、证书链或远端服务可达性。

## 站点内容

`control/site/` 可保存：

- `welcome_tips.json`：新对话页轮播提示；
- `branding.json`：开发方和问题反馈链接。

示例位于 Release 的 `config/site/*.example.json`。文件缺失或解析失败时相应 UI 使用 fallback，不阻塞应用启动。

左侧栏通知不再属于 `control/site/`：通知正文与 revision 存在共享数据库中，
管理员 UI 的在线编辑在多 backend 间一致。旧部署遗留的
`control/site/notifications.json` 不再被运行时读取，也不会自动导入数据库；升级
后的通知配置从空状态开始，旧通知自然失效。遗留文件仅作为历史备份保留，后续
新通知统一通过通知管理页发布并使用新的 ID。

## 修改后的应用方式

修改 `.env`、入站证书、出站信任锚或 `control/site/` 后先校验：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow site validate
```

站点内容由文件直接读取；`.env` 和证书需要重新 reconcile 当前 Release：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow plan apply current
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow apply current
```

不要用 `docker restart` 代替：容器重启不会重新读取已经展开的环境配置，也不会走 Release 校验和 readiness gate。
