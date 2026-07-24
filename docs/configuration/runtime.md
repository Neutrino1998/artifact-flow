# 应用与站点配置

生产目标机上的可变配置集中在 `/opt/artifactflow/control/`。Release 目录是只读快照，不承担现场状态。

```text
/opt/artifactflow/control/
├── site.toml
├── .env
├── certs/
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
| `ARTIFACTFLOW_JWT_SECRET` | JWT 签名 |
| `ARTIFACTFLOW_CREDENTIAL_KEY` | 加密 Tool/MCP 凭证；即使暂时没有凭证工具也必需 |
| `ARTIFACTFLOW_DATABASE_URL` 或 `ARTIFACTFLOW_DATABASE_URLS` | 数据库连接；后者是逗号分隔的 PostgreSQL 地址列表 |
| `ARTIFACTFLOW_REDIS_URL` | 生产运行时、SSE、lease 与多副本协调 |
| `ARTIFACTFLOW_REDIS_KEY_PREFIX` | Redis 命名空间；启用 Redis 时必需 |

按部署能力必需：

- `infra = "bundled"`：`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`；
- `tls = "acme"`：`AF_DOMAIN`（裸域名，不含 scheme、端口或路径）、`AF_ACME_EMAIL`，且外部端口必须是 80/443；
- Model：对应供应商的 API Key；
- Tool/MCP：定义引用的 `TOOL_SECRET_*`。

常用应用调节项：

| 变量 | 默认 | 何时调整 |
|---|---:|---|
| `ARTIFACTFLOW_EXECUTION_TIMEOUT` | `3600` 秒 | 单轮任务总 deadline |
| `ARTIFACTFLOW_PERMISSION_TIMEOUT` | `300` 秒 | 等待一次用户确认的时间 |
| `ARTIFACTFLOW_MAX_CONCURRENT_TASKS` | `32` | 按模型、DB 和主机容量限制并发 |
| `ARTIFACTFLOW_MAX_UPLOAD_SIZE` | `200 MiB` | 单文件上传上限；代理另有批量总量上限 |
| `ARTIFACTFLOW_ARTIFACT_USER_QUOTA_BYTES` | `2 GiB` | 每用户二进制 Artifact 配额；`0` 表示不限 |
| `ARTIFACTFLOW_SKILL_USER_MAX_PRIVATE_COUNT` | `3` | `-1` 不限，`0` 禁止个人导入 |
| `ARTIFACTFLOW_CORS_ORIGINS` | 本地前端 | 直接跨域访问 API 时设置明确 origin 列表 |
| `ARTIFACTFLOW_DB_COMMAND_TIMEOUT` | `30` 秒 | PostgreSQL 单语句上限；设 `0` 禁用 |

`src/config.py` 还有算法护栏和内部实现常量。它们即使能被环境变量覆盖，也不等于常规部署契约；没有具体容量或故障证据时不要照单调大。

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

修改 `.env`、证书或 `control/site/` 后先校验：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow site validate
```

站点内容由文件直接读取；`.env` 和证书需要重新 reconcile 当前 Release：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow plan apply current
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow apply current
```

不要用 `docker restart` 代替：容器重启不会重新读取已经展开的环境配置，也不会走 Release 校验和 readiness gate。
