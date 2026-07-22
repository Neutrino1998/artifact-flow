# 一次性迁移旧 `deploy_*` Docker volumes

仅非常早期、volume 仍名为 `deploy_artifactflow_data`、`deploy_postgres_data`、`deploy_redis_data` 的安装需要本步骤。若 `docker volume ls` 已显示 `artifactflow_data`、`artifactflow_postgres_data`、`artifactflow_redis_data`，不要重复迁移。

v2 显式固定 volume 名，避免 release 路径或 Compose project name 改变时生成空数据盘。旧 volume 不能由 afctl 自动猜测、合并或删除；第一次 v2 apply 前安排维护窗口完成一次复制。

## 1. 确认并停止旧栈

先记录数据库行数和 Redis key 数，再用旧 release 自带的 Compose 文件停服。绝对不要加 `-v`：

```bash
docker volume ls --format '{{.Name}}' | grep -E '^deploy_(artifactflow|postgres|redis)_data$'
docker compose -p deploy -f /path/to/old/deploy/docker-compose.intranet.yml --profile infra down
```

使用外部 PostgreSQL/Redis 的安装只迁移 `deploy_artifactflow_data`。

## 2. 创建固定名称并离线复制

```bash
docker volume create artifactflow_data
docker volume create artifactflow_postgres_data
docker volume create artifactflow_redis_data

docker run --rm \
  -v deploy_artifactflow_data:/from:ro -v artifactflow_data:/to \
  postgres:16-alpine sh -c 'cp -a /from/. /to/'

docker run --rm \
  -v deploy_postgres_data:/from:ro -v artifactflow_postgres_data:/to \
  postgres:16-alpine sh -c 'cp -a /from/. /to/'

docker run --rm \
  -v deploy_redis_data:/from:ro -v artifactflow_redis_data:/to \
  redis:7-alpine sh -c 'cp -a /from/. /to/'
```

临时容器使用目标机已经加载的固定镜像，内网不需要联网。若新的 `artifactflow_*` volume 已被一次失败尝试创建，先确认它们为空再删除重建；不要把两个非空数据源合并。

## 3. 建立 v2 状态并验证

```bash
sudo ./<release>/afctl --root /opt/artifactflow site migrate-v1 \
  --preset intranet --sandbox-runtime runsc
sudo ./<release>/afctl --root /opt/artifactflow doctor
sudo ./<release>/afctl --root /opt/artifactflow apply ./<release>
```

验证历史对话、数据库行数、Redis key 数和 `/app/data` 后，至少保留旧 `deploy_*` volume 一个回滚观察窗口。afctl 不会删除它们。

若 v2 验证失败，停掉 v2 project，切回旧 release 的 Compose 文件即可重新挂载原 `deploy_*` volumes。不要用带显式 `name: artifactflow_*` 的 v2 Compose 文件做旧栈回滚。
