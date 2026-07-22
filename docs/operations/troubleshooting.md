# 故障处理

先区分“整体站点不可用”“单个 Backend 不可用”和“依赖不可用”，再决定是否重启。重启会丢失进程现场，服务卡死时应先抓栈。

## 第一轮检查

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow status
docker ps --filter label=com.docker.compose.project=artifactflow
```

查看异常容器日志：

```bash
docker logs <container-id> --tail 300
```

| 现象 | 首要方向 |
|---|---|
| `site validate` 失败 | `site.toml`、`.env`、TLS capability 组合 |
| `doctor` 失败 | Docker/Compose、runsc、scratch mount、证书或镜像 |
| Release service 退出 | Alembic migration 或 config reconcile；看 release container 日志 |
| `/health/live` 成功、`/health/ready` 失败 | PostgreSQL、Redis 或依赖配置 |
| 一个 Backend unhealthy、其他副本正常 | 目标副本进程、资源或单任务 wedge |
| 所有 Backend CPU 高且响应慢 | 并发过高、同步 CPU 工具或共享依赖 |
| Caddy 503 且 maintenance=on | 正常维护窗口或上次失败保留的维护状态 |

Apply 失败时不要手工改 `.artifactflow/state.json`。保留命令输出和容器日志，修复原因后重新执行 `plan`/`apply`；只有已有 last-known-good Release 时才考虑 `rollback`。

## 定位目标 Backend

```bash
docker ps \
  --filter label=com.docker.compose.project=artifactflow \
  --filter label=com.docker.compose.service=backend
```

选择明确的 container ID，后续不要用可能随机命中副本的 `compose exec backend`：

```bash
export CID=<backend-container-id>
docker exec "$CID" curl -m 3 http://127.0.0.1:8000/health/live
docker exec "$CID" curl -m 5 http://127.0.0.1:8000/health/ready
```

- live 超时：该副本事件循环无法运行，继续抓进程现场；
- live 200、ready 非 200：读取 ready JSON，检查 DB/Redis；
- 两者都 200 但用户请求慢：检查 `/api/v1/admin/runtime` 的 loop lag、DB pool、RSS、FD 和长任务。

## Backend 卡死：重启前取证

先看资源：

```bash
docker stats --no-stream "$CID"
docker exec "$CID" cat /proc/1/status | head -5
```

默认 deadman 会在事件循环长时间无 heartbeat 时把 Python 全线程栈写入 stderr：

```bash
docker logs "$CID" --tail 500 2>&1
```

需要主动再抓一次：

```bash
PID=$(docker inspect -f '{{.State.Pid}}' "$CID")
sudo kill -USR1 "$PID"
docker logs "$CID" --tail 500 2>&1
```

备用 attach 路径：

```bash
docker exec "$CID" py-spy dump --pid 1
```

如果有软退化记录，同时拷出观测文件：

```bash
docker exec "$CID" tail -50 /app/data/observability/loop-lag.jsonl
docker cp "$CID":/app/data/observability ./observability-snapshot
```

取证完成后只重启目标副本：

```bash
docker restart "$CID"
```

随后确认该副本 live/ready 恢复，并记录触发请求的 request ID、conversation/message ID、栈、CPU/RSS、当前 Release 和重启时间。

常见 fingerprint：

| Fingerprint | 根因方向 |
|---|---|
| live 卡住、单核 CPU 约 100%、用户态占满 | 同步 CPU 计算或持 GIL 的扩展 |
| live 卡住、CPU 低、线程等待 futex | 死锁或锁未释放 |
| `/proc/1/status` 为 `D` | 磁盘/NFS 等不可中断 IO |
| live 200、DB pool overflow | 长查询或连接池饱和 |
| live 200、loop lag 上升 | Event loop 软退化，查看 loop-lag task 栈 |

## DB 与 Redis

Ready 检查已指出具体 component 时：

- 从 Backend 容器内验证目标地址，而不是只从宿主机验证；
- 对照 `control/.env` 检查 DNS、TLS、账号和端口；
- external PostgreSQL 检查连接上限、锁等待和当前 primary；
- Redis 检查 maxmemory、noeviction 写失败、连接数和 Cluster/Sentinel 状态；
- 不要通过清空 Redis 来“修复”未知故障，那里可能有在飞 lease、cancel、interrupt 和 stream。

PostgreSQL 语句被 `ARTIFACTFLOW_DB_COMMAND_TIMEOUT` 终止时，应查明慢查询或锁，不要直接把 timeout 调到与整个任务一样长。

## Config reconcile 失败

本地先运行：

```bash
python scripts/reconcile_config.py --dry-run
```

常见原因：

- Tool/Agent/Skill YAML frontmatter 不完整；
- 名称与 Builtin、另一个 unit 或 dynamic UI 配置冲突；
- Agent 引用了未知 Tool，或还在使用旧的 `auto`/`confirm` 绑定语法；
- HTTP/MCP 使用了非 `TOOL_SECRET_` 占位符；
- Toolset 缺 `_set.md`，或 Skill bundle 结构不合法；
- `ARTIFACTFLOW_CREDENTIAL_KEY` 缺失或格式错误。

生产失败应修改源码并构建新 Release，或从 current 做 [`afctl config checkout/apply`](releases.md#配置热修)。不要直接改数据库 registry 来绕过 reconcile。

## SSE 或对话执行异常

- 浏览器断开不会自动取消正在执行的任务；重新连接会从 StreamTransport 恢复可用事件。
- 同一对话并发执行返回 409 是 lease correctness gate，不是普通网络重试错误。
- Permission 长时间等待最终按 `ARTIFACTFLOW_PERMISSION_TIMEOUT` 拒绝。
- 任务达到 `ARTIFACTFLOW_EXECUTION_TIMEOUT` 会记录 `TIMED_OUT` 终态；它和用户取消是不同原因。
- 用户侧错误会被清理，Operator 用 request ID 在日志和 MessageEvent 中查看原始原因。

若需要提交问题，至少附上：Release ID、instance ID、request ID、时间范围、复现输入类型、相关容器日志和 health 输出；不要附带 `.env`、Token 或工具密钥。
