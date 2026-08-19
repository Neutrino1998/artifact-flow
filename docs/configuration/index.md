# 配置总览

ArtifactFlow 的配置不是一个大文件。不同配置有不同的所有者、存储位置和生效方式。

## 配置地图

| 配置 | 作者真相 | 生产运行时 | 用途 |
|---|---|---|---|
| Model | `config/models/models.yaml` | Release 文件 | 模型 alias、供应商、端点和采样参数 |
| Agent | `config/agents/*.md` | DB registry | 角色提示词、模型和可用能力 |
| HTTP Tool | `config/tools/` | DB registry | 外部 REST 操作和权限级别 |
| [企业认证](authentication.md) | `config/auth/remote_bearer_userinfo.yaml` | 启动期文件 | 可选的 bearer userinfo 登录来源；生产由 `control/auth/` 覆盖 |
| MCP | `config/mcp/*.md` | DB registry + 每进程发现缓存 | MCP Server 连接和工具发现 |
| Skill | `config/skills/` | DB registry | 按需工作方法、附件和工具授予 |
| 部署能力 | `control/site.toml` | 目标机文件 | TLS、基础设施、Sandbox、executor |
| Secret 与应用参数 | `control/.env` | 目标机文件 | 数据库、Redis、模型凭证、运行参数 |
| 出站 HTTPS 信任 | `control/trust/ca-certificates/*.crt` | 目标机文件 | 内网 Tool/MCP 的企业根 CA 或自签 leaf |
| 站点静态内容 | `control/site/` | 目标机文件 | 欢迎提示和品牌信息 |
| 在线通知 | 管理员 UI | PostgreSQL / SQLite | 多 backend 共享的通知正文与 revision |

`config/` 随 Release 发布，不应在已经物化的 Release 目录中原地修改。`control/` 属于目标站点，升级和回滚不会覆盖它。

## 本地配置工作流

`config/` 不会在运行中自动重新载入。Docker Compose 和原生开发使用不同的数据库，命令不要混用。

### Docker Compose

先在运行中的 Backend 容器里检查已经挂载的配置文件：

```bash
docker compose exec backend python scripts/reconcile_config.py --dry-run
```

确认无误后重建 Backend，使 Compose 重新读取 `.env`：

```bash
docker compose up -d --force-recreate backend
docker compose logs --tail=100 backend
```

`docker compose exec` 使用现有容器的环境，因此前面的 dry-run 看不到刚写入 `.env` 的新值。强制重建会加载新环境；容器 entrypoint 随后执行完整校验，并在启动服务前把 Agent、Tool、MCP 和 Skill reconcile 到 Compose volume 中的数据库。Model 配置和供应商凭证也会在这次重建后重新载入。

### 原生开发

在运行 Backend 的同一个 checkout 和 Python 环境中执行：

```bash
python scripts/reconcile_config.py --dry-run
python scripts/reconcile_config.py
```

然后停止并重新运行 `python run_server.py --reload`，使进程内配置缓存重新载入。`--dry-run` 不提交 registry 变更，但仍会连接并初始化当前环境配置的数据库，不是离线语法检查。

生产容器会在 Release gate 中自动完成数据库迁移与 reconcile；生产配置变更使用[配置热修](../operations/releases.md#配置热修)，不要手工操作 registry 表。

## 生效与失败原则

- 配置错误、名称冲突和未知 Agent 工具引用会 loud-fail，不会悄悄忽略。
- 删除或改名 seeded Tool/Skill 会在 reconcile 时删除对应 seeded registry 记录；相关部门规则可能需要重新授权。
- HTTP Tool 和 MCP 定义中的 Secret 引用只允许 `{{TOOL_SECRET_*}}`。
- Agent 的 `tools` 只表达 `enabled` / `disabled` 成员关系；执行权限以 Tool 定义中的 `auto` / `confirm` 为唯一来源。
- REST/API 字段以运行中服务的 OpenAPI 为准；Wiki 不复制完整响应 schema。

## 配置顺序

新增一项能力时，推荐按依赖顺序配置：

1. 在 [Model](models.md) 中准备 Agent 要用的模型；
2. 在 [Tool 与 MCP](tools.md) 中注册外部能力；
3. 创建 [Agent](agents.md) 并启用对应 Tool unit；
4. 如需按需指导或按需授予工具，再创建 [Skill](skills.md)；
5. 在目标站点的[应用与站点配置](runtime.md)中提供 Secret 和运行参数。
