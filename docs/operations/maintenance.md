# 日常维护

本页覆盖站点已经成功上线后的常规操作。所有命令示例使用 `/opt/artifactflow` 作为安装根目录。

## 状态与健康

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow status
```

该命令读取 current/previous 状态，显示 Compose 服务，并通过 Caddy 检查 `/health/ready`。

两个健康端点含义不同：

| 端点 | 含义 |
|---|---|
| `/health/live` | 当前 Backend 事件循环仍能响应 |
| `/health/ready` | Backend 可服务，并且 DB、Redis 等依赖可用 |

生产流量走 Caddy。多副本排障时，Caddy 返回成功只说明至少有可用副本；诊断某个 Backend 必须进入目标容器访问 `127.0.0.1:8000`，详见[故障处理](troubleshooting.md)。

## 日志

先通过 Compose labels 找到实际容器：

```bash
docker ps --filter label=com.docker.compose.project=artifactflow
```

然后查看目标容器：

```bash
docker logs <container-id> --tail 200
docker logs -f <container-id>
```

应用日志包含 `instance_id`、`request_id`、`conversation_id` 和 `message_id`，应从用户报告中的 request ID 开始定位。生产 Docker 日志已经配置大小和文件数上限；长期保留需要接入现场日志系统。

Backend 的 `/app/data/observability/` 保存 metrics 和 loop-lag JSONL。需要离线分析时：

```bash
docker cp <backend-container-id>:/app/data/observability ./observability-snapshot
python scripts/observability_report.py --hours 24 --obs-dir ./observability-snapshot
```

分析脚本需要 pandas，应在独立分析环境准备，不属于运行时 Release 依赖。

## 维护页

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow \
  maintenance on "数据库维护"

sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow maintenance status

sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow maintenance off
```

维护状态会让业务、API 和 SSE 返回 503 页面，但健康端点继续反映真实状态。Apply 会在自身窗口管理维护状态；不要在另一终端同时手工切换。

## 通知与现场内容

管理员可在 UI 编辑通知，也可修改：

```text
/opt/artifactflow/control/site/notifications.json
/opt/artifactflow/control/site/welcome_tips.json
/opt/artifactflow/control/site/branding.json
```

通知文件有 revision 并发保护。直接编辑后先检查 JSON；错误文件会让相应 UI fallback，但不会阻断服务。

## 证书和 Secret 轮换

- Tool/MCP Secret：更新 `control/.env` 中对应 `TOOL_SECRET_*`，执行 `apply current`，Release gate 会重新加密 seeded credential。
- JWT Secret：变更会使现有登录 token 失效，应安排通知窗口。
- Credential Key：当前是单主密钥且没有在线轮换协议；不要直接替换，否则数据库中的既有 Tool credential 无法解密。
- 静态 TLS：原子替换 `server.crt` 和 `server.key`，保持私钥 `0600`，再执行 `plan apply current` / `apply current`。

## 备份

`afctl` 当前不提供一键 backup/restore；备份由数据库和现场运维体系负责。

必须备份：

1. PostgreSQL：用户、对话、事件、注册表、Artifact 和二进制数据的权威来源；
2. `/opt/artifactflow/control/`：站点配置、Secret、证书和现场内容；
3. `/opt/artifactflow/.artifactflow/state.json`、当前/上一 Release 和 hotfix bundle：用于重建精确部署状态。

可选备份：

- `artifactflow_data` 中的 observability JSONL，用于事故分析；
- Redis AOF，用于缩短 Redis 故障后的运行态丢失，但不能代替业务数据备份。

使用 bundled PostgreSQL 时，可通过 Compose service label 找到 PostgreSQL 容器，再使用官方 `pg_dump`/`pg_restore`。备份文件必须写到容器外的独立存储，并定期在隔离环境做恢复演练。

恢复顺序应是：准备干净目标站点 → 恢复 PostgreSQL → 恢复 `control/` 与 Release/State → 运行 `doctor`/`plan` → Apply current → 做业务验收。数据库恢复点必须与应用 schema 兼容。

## 容量巡检

至少监控：

- 主机内存、load、Docker data root 空间和 inode；
- Sandbox scratch 剩余空间和 inode；
- PostgreSQL volume、连接数、慢查询和备份新鲜度；
- Redis `used_memory`、连接数与 noeviction 写失败；
- Backend restart count、health、loop lag、RSS 和 open FDs；
- TLS 到期时间。

达到限制时优先扩容或降低并发。不要把 Redis 改成 LRU，也不要单纯抬高 Sandbox/上传/算法上限来掩盖容量问题。

## Autoheal

Docker HEALTHCHECK 只会把容器标成 unhealthy，不会自动重启。需要无人值守恢复时，可显式安装 Release 内的 systemd timer；完整安装和真机验收命令见仓库的 [autoheal README](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/autoheal/README.md)。

Autoheal 在维护旗标存在时 no-op，并把重启 marker 留给管理面板。安装后应在维护窗口用 `kill -STOP` 模拟一个 Backend wedge，确认：Caddy 摘除 → 实例变红 → timer 重启 → 恢复健康。
