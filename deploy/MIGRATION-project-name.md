# 迁移:`deploy_*` volumes → `artifactflow_*`

> 仅对**已部署过 pre-pin 版本**的内网机有效(volume 真实名前缀是 `deploy_`)。新部署不需要做。本次为一次性动作,跑完之后未来 project 改名不会再要求迁移(volume 名已显式钉死)。

> **范围:** 默认覆盖 **3A**(自建 PG/Redis,三个 `deploy_*` volume 都在)。**3B**(外部 DB)只有 `deploy_artifactflow_data` 一个本地 volume,差异点:步骤 2-3 中 PG/Redis 两块整体跳过(helper image 改用任一已 load 的镜像,例如 `nginx:1.30.1-alpine`),步骤 4 起服命令不带 `--profile infra`,步骤 5 / 6 / 回滚段都有 3B 注释或独立子节。

## 背景

旧版 compose 没显式声明 project name,Docker Compose v2 默认拿 compose 文件目录的 basename → `deploy` → 容器名 `deploy-backend-1` / volume 名 `deploy_postgres_data` 等。

新版加了 `name: artifactflow` 顶层字段,容器变 `artifactflow-backend-1`,顺手把 volume 也显式钉成 `artifactflow_*`(`name:` 字段,无 project 前缀)。**新 volume 名 ≠ 旧 volume 名**,直接 `up -d` 会被挂上空 volume,PG 触发 initdb、上传 / observability jsonl 全空。本文档把数据原样搬过来。

## 必要 downtime

PG 大小决定 copy 时间。3GB / SSD 大致几十秒;HDD 内网机预留 5-10 分钟稳妥。Redis AOF / `artifactflow_data` 上传通常更快(MB 级)。

## 前置:确认现状

```bash
# 1. 当前用的项目名应是 deploy
docker compose -p deploy -f deploy/docker-compose.intranet.yml --profile infra ps

# 2. 三个旧 volume 都在
docker volume ls --format '{{.Name}}' | grep -E '^deploy_(artifactflow|postgres|redis)_data$'
#   deploy_artifactflow_data
#   deploy_postgres_data
#   deploy_redis_data

# 3. 新 volume 不应存在(否则上次升级已经创建了空 volume,见 §回滚)
docker volume ls --format '{{.Name}}' | grep -E '^artifactflow_(data|postgres_data|redis_data)$' || echo "OK: no new volumes yet"
```

## 步骤

```bash
# 进仓库根目录
cd /path/to/artifact-flow

# ── 1. 停服(NOT `-v`!否则连数据一起删) ──
#    `--profile infra` 必带:不加的话部分 compose 版本会跳过带 profile 的服务,
#    PG / Redis 在 step 3 cp 期间仍在写,copy 出脏数据。
docker compose -p deploy -f deploy/docker-compose.intranet.yml --profile infra down
# 验证全停:
docker compose -p deploy -f deploy/docker-compose.intranet.yml --profile infra ps -a

# ── 2. 创建空的新 volume ──
docker volume create artifactflow_data
docker volume create artifactflow_postgres_data
docker volume create artifactflow_redis_data

# ── 3. 通过临时容器把数据 cp 过去(-a 保元数据 / 权限 / 时间戳) ──
#    Helper image 必须用**已经 docker load 进来的**镜像 — 内网机离线,没法 pull
#    `alpine:latest`。下面用 `postgres:16-alpine` / `redis:7-alpine`(都有 sh + cp -a)。
#    3B 无 PG/Redis volume,本节只跑 data 那一块,helper 可改 nginx:1.30.1-alpine。
#    Postgres:
docker run --rm \
  -v deploy_postgres_data:/from:ro \
  -v artifactflow_postgres_data:/to \
  postgres:16-alpine sh -c 'cp -a /from/. /to/ && echo "PG copy done: $(du -sh /to)"'

#    Backend 持久数据(uploads + observability jsonl + sqlite if any):
docker run --rm \
  -v deploy_artifactflow_data:/from:ro \
  -v artifactflow_data:/to \
  postgres:16-alpine sh -c 'cp -a /from/. /to/ && echo "data copy done: $(du -sh /to)"'

#    Redis(AOF + RDB):
docker run --rm \
  -v deploy_redis_data:/from:ro \
  -v artifactflow_redis_data:/to \
  redis:7-alpine sh -c 'cp -a /from/. /to/ && echo "redis copy done: $(du -sh /to)"'

# ── 4. 起服(不带 -p,name: artifactflow 自动生效) ──
#    AF_VERSION 必显式:compose 里 fallback 是 ${AF_VERSION:-latest},但内网机
#    docker load 出来只有具体 tag,没有 :latest,会起失败或起错版本。
#    把 <x.y.z> 换成本次 release 的版本号(看 release tar 文件名或 .env 里的 AF_VERSION)。
AF_VERSION=<x.y.z> docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d

# ── 5. 验证 ──
docker compose -f deploy/docker-compose.intranet.yml --profile infra ps
#   名字应全是 artifactflow-X-1

# 3B 跳过 postgres / redis 两块(无本地容器,数据由外部 DB 验证):
docker compose -f deploy/docker-compose.intranet.yml exec postgres \
  psql -U "${POSTGRES_USER:-artifactflow}" -d "${POSTGRES_DB:-artifactflow}" \
  -c "SELECT count(*) FROM conversations; SELECT count(*) FROM message_events;"
#   行数应与停服前一致(`-p deploy` 时跑同样的查询记录一下做对照)
docker compose -f deploy/docker-compose.intranet.yml exec redis redis-cli DBSIZE
#   key 数应非零(if 之前有数据)

# 这块 3A / 3B 都跑:
docker compose -f deploy/docker-compose.intranet.yml exec backend \
  ls /app/data /app/data/observability
#   观测 jsonl 文件应在

# ── 6. 通过冒烟测试,真的没问题再删旧 volume ──
#    跑 1-2 个真实对话,看历史能不能拉出来、新消息能不能落库
#    再删(3B 只有第一个,删 deploy_artifactflow_data 一个即可):
docker volume rm deploy_artifactflow_data deploy_postgres_data deploy_redis_data
```

## 回滚

只要还没执行第 6 步,旧 volume 没动。

**关键前置:必须先把 compose 文件切回 pre-pin 版本。** 新版 compose 里 volume 的 `name:` 字段把名字钉死成 `artifactflow_*`,`-p deploy` 只改 project name **不改 volume name**(已用 `docker compose -p deploy ... config` 验证);照搬旧命令会让上一行刚删的新卷被空建,旧 `deploy_*` 数据无人挂载。

### 3A 回滚

```bash
docker compose -f deploy/docker-compose.intranet.yml --profile infra down
docker volume rm artifactflow_data artifactflow_postgres_data artifactflow_redis_data
# 切回 pre-pin 的 compose 文件(SHA 44d00e4 是引入 pin 的 commit,工作区改动,不提交):
git checkout 44d00e4^ -- deploy/docker-compose.intranet.yml
# 旧 compose 没有 `name:` 字段,volume 名 fallback 到 `<project>_<volume>` 即 deploy_*:
AF_VERSION=<旧版本> docker compose -p deploy -f deploy/docker-compose.intranet.yml --profile infra up -d
# 验证 OK 后再决定是否 `git revert <pin commit>` 入主线
```

### 3B 回滚

```bash
docker compose -f deploy/docker-compose.intranet.yml down
docker volume rm artifactflow_data
git checkout 44d00e4^ -- deploy/docker-compose.intranet.yml
AF_VERSION=<旧版本> docker compose -p deploy -f deploy/docker-compose.intranet.yml up -d
```

## 常见踩坑

- **`down` 后 `ps` 还显示容器**:用 `-p deploy` 才查到旧 project 的容器,新 project name 看不到。`down -p deploy` 即可。
- **`up -d` 后 PG 启动失败、log 里 `database files are incompatible with server`**:旧数据是 PG 主版本与镜像不一致。Mode 3 镜像是 `postgres:16-alpine`;如果旧部署是 PG 15,不能直接 copy,要走 pg_dumpall → restore。本仓库一直 pinned 在 16,正常不会踩。
- **copy 报 `cp: can't preserve ownership`**:文件系统不支持(罕见,内网 ext4/xfs 都没问题)。换 `cp -r` + 后续 `chown -R 999:999 /to`(PG uid)、`999:1000`(redis)、`root:root`(backend data)。
- **新 volume 已经被 `up -d` 创建过(`ls` 看见空 volume)**:`rm` 掉再走第 2-4 步。
