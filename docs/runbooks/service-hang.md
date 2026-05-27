# Runbook · 服务卡死(后端无响应)

> 现场参照:2026-05-14 一次 `update_artifact` 同步 CPU 死算攥住 GIL、卡死 event loop 约 96 分钟、`/health/live` 全程无响应的事故。本 runbook 把当次实战命令固化下来,oncall 直接 copy-paste;wedge 形态与工具作者纪律见文末。

## 前置:挑 compose 文件 + 选目标副本

不同部署模式 backend 容器名 / 端口暴露不一致。**先按部署模式 export 三个变量,后续所有命令引用**:

```bash
# Mode 1 (Quick Trial,根 docker-compose.yml,backend 直暴 8000)
export COMPOSE=docker-compose.yml
export HEALTH=http://localhost:8000        # backend 直连,无 nginx

# Mode 2 (Production,docker-compose.prod.yml,backend expose 8000 内网,nginx 暴 host AF_HTTP_PORT:-80)
# Mode 3 (Intranet,deploy/docker-compose.intranet.yml)同款
export COMPOSE=docker-compose.prod.yml                       # 或 deploy/docker-compose.intranet.yml
# 走 nginx;直接查活实例的端口映射,不依赖 .env / shell 里 AF_HTTP_PORT 是否 export
export HEALTH="http://localhost:$(docker compose -f "$COMPOSE" port nginx 80 | cut -d: -f2)"

# ── 挑要诊断的 backend 副本 ──
# 默认是 1 个 backend;但生产可能 `--scale backend=2` 起多副本(见
# docker-compose.prod.yml 顶部 usage 注释)。多副本时 `compose ps -q backend`
# 会返回多行 ID,直接喂 docker stats / inspect 会失败,先列出来挑一个:
docker compose -f "$COMPOSE" ps backend
# 拿哪个 = 你怀疑卡死的那个(unhealthy / restart count 高 / 上一次诊断指向的)
export CID=<上面 NAME 或 CONTAINER ID 列任一>
```

`HEALTH` 推导用 `compose port nginx 80` 而不是 `${AF_HTTP_PORT:-80}`:Compose 读 `.env` 文件做变量替换,但 oncall 的 shell **不会** auto-source `.env`(`AF_HTTP_PORT=8080` 写在 `.env` 里 / oncall 没 export → `${AF_HTTP_PORT:-80}` 拿到 80,然后 curl 错端口)。`compose port nginx 80` 直接问活着的 docker network,返回真实 host 映射端口,不依赖 shell env。nginx 自己挂了不能用时回退 `${AF_HTTP_PORT:-80}` 并先 `set -a; . deploy/.env; set +a`。

为什么要手动挑 CID 而不让脚本自动 `$(compose ps -q backend)`:`compose ps -q` 多副本时输出多行,`docker stats "$CID"` 会把多行字符串当一个不存在的容器名报错。Oncall 必须自己判定要诊断的是哪个副本——卡死通常只是其中一个,另几个正常服务用户。

## 适用症状

- `/health/live` 卡住 / 504(纯协程端点,正常 1ms 内返回)
- 前端"对话发出去没动静",所有 SSE 断
- `docker compose -f $COMPOSE ps backend` CPU 100%、容器没退出
- 健康探针翻 unhealthy 但容器不重启(`HEALTHCHECK` 翻红 ≠ 自动 restart)

如果只是依赖问题(DB / Redis 慢),`/health/live` **能正常 200**;不在本 runbook 范围,看 `/health/ready` 输出哪个 component `error`。

---

## Step 1:判别"循环卡死"还是"依赖问题"

`$HEALTH` 走 nginx 是**整体服务**检查——多副本时 nginx upstream `backend:8000` 会 LB(`deploy/nginx.conf:1` upstream 块),命中健康副本就返 200,**不能用来证明 `$CID` 的循环活着**。诊断目标副本必须 `docker exec "$CID"` 走 127.0.0.1。

```bash
# ── 权威:目标副本自身的 /health/live ──
# 直连 127.0.0.1:8000,不经过 nginx,锁死打的是 $CID 而非随机副本
docker exec "$CID" curl -m 3 http://127.0.0.1:8000/health/live
# 200 → $CID 循环活着,问题在依赖或慢操作,跳 Step 5
# 超时 / 卡住 → $CID 事件循环被饿死,继续 Step 2

# 同副本 /health/ready 测 DB + Redis
docker exec "$CID" curl -m 5 http://127.0.0.1:8000/health/ready

# ── 旁证:整体服务还能不能用(nginx + 全部 backend 副本) ──
# 多副本时此处仍 200 说明至少有一个副本能服务,但不告诉你 $CID 的状态;
# 单副本部署里就是权威检查的等价物
curl -m 3 "$HEALTH/health/live"
```

`/health/live` 卡住即可定性:**目标副本的事件循环死锁**。`/api/v1/admin/runtime`(`src/observability/admin_runtime.py:42`)同理是 FastAPI 协程端点,循环卡时它也无响应——它的定位是"还活但变慢"水位 triage,不是硬 wedge 入口。

## Step 2:确认是 CPU 型卡死(GIL 被攥住)

`$CID` 在前置已经选定;后续 `docker stats / exec / inspect / logs` 全用 `$CID`,**不要**用 `compose exec backend` —— 多副本时 compose 会随机挑一个,不一定是要诊断的那个。

```bash
# 1) docker stats — 副本 CPU 100%(单核)、内存 / 连接数都正常
docker stats --no-stream "$CID"

# 2) 进容器看 process 状态(/proc/1 = entrypoint 主进程)
docker exec "$CID" cat /proc/1/status | head -5
# State 应是 R (running);若是 D (uninterruptible sleep) 是 IO 卡死(见末尾形态表)

# 3) pidstat:用户态 vs 内核态(宿主机侧执行 — 容器内未装 sysstat)
PID=$(docker inspect -f '{{.State.Pid}}' "$CID")
pidstat -p "$PID" 1 3
# %usr ≈ 100、%system ≈ 0 → 纯 Python 用户态死算

# 4) strace 跟所有线程(宿主机执行)
sudo strace -f -p "$PID" 2>&1 | head -40
# 典型 wedge 形态:多个线程停在 futex(FUTEX_WAIT...),没有任何线程发 syscall
# (烧 CPU 的线程攥着 GIL,从不释放,strace 抓不到它)
```

事故当时 `docker stats` 报 backend CPU 101%、内存 474 MiB / 2 GiB 正常、PG 连接 ~12 正常;9 线程里 8 个 `futex_wait`,占 CPU 的那个 strace 完全不出现——单线程纯用户态死算典型 fingerprint。

## Step 3:抓 Python 栈(取证,**重启之前**必跑)

三条路径,**任一**成功即可拿到栈:

```bash
# A) deadman switch 自动 dump(faulthandler.dump_traceback_later,纯 C 线程)
#    循环 wedge >= WATCHDOG_DEADMAN_TIMEOUT_MS (默认 10s) 时自动打 stderr
docker logs "$CID" 2>&1 | grep -A 200 'Thread 0x'

# B) SIGUSR1 手动 dump(faulthandler.register 注册在 main.py:55)
#    适用于 deadman 阈值还没踩到 / 已经 dump 过想再来一发
PID=$(docker inspect -f '{{.State.Pid}}' "$CID")
sudo kill -USR1 "$PID"
docker logs "$CID" --tail 300 2>&1 | grep -A 200 'Thread 0x'

# C) py-spy(backend 镜像已内置,见 Dockerfile:29;需 cap_add: SYS_PTRACE)
docker exec "$CID" py-spy dump --pid 1
# 不出栈 → py-spy 缺 cap(先 `docker exec "$CID" py-spy --version` 验在;
#                      attach 报 Operation not permitted 即缺 SYS_PTRACE)
# 镜像里没 py-spy → 跑 deploy/scripts/preflight.sh 验镜像版本是否带 PR-forensics-bundle
```

A 路径是默认通道(`src/observability/deadman.py`);C 路径是 deadman 失效时的备份。两者互补:deadman 走 C 线程不需要 GIL,py-spy 直接 attach 进程也不需要 GIL,任一都能在 wedge 期间拿到栈。

## Step 4:看刚才有没有"软退化"事件

事件循环 lag 超 `LOOP_LAG_WARN_MS`(默认 500ms)即由 `LoopLagWatchdog`(`src/observability/watchdog.py`)写一行 `loop-lag.jsonl`,附 `asyncio.all_tasks()` 各任务的栈截断。**watchdog 本身在 Python 线程里持 GIL,硬 wedge 场景下它和事件循环一起睡死,这个文件可能 *没有* 新增条目**——见 `watchdog.py:7-12` 的失效面注释。但在"循环还在跑只是变慢"的退化场景下它是定位拖慢源的主入口。

```bash
# jsonl 在 named volume artifactflow_data:/app/data 内(不是宿主 ./data 的 bind mount);
# 通过 docker exec 读容器内路径。最新事件就在当前文件,直接 tail:
docker exec "$CID" tail -f /app/data/observability/loop-lag.jsonl

# 看刚刚 wedged 没有(就当前文件,不要扫轮转):
docker exec "$CID" tail -20 /app/data/observability/loop-lag.jsonl | \
  python -c 'import json,sys
for l in sys.stdin:
    o=json.loads(l)
    print(o.get("ts"), o.get("lag_ms"), "wedged" if o.get("wedged") else "soft")'

# 要回看更早(包含轮转):RotatingFileHandler 的命名是
# loop-lag.jsonl(当前最新)、loop-lag.jsonl.1(最近一次轮转)、.2(更旧)、...
# 一直到 .N(N = OBS_JSONL_BACKUP_COUNT,默认 10)。要按时间顺序拼接需把
# `.10` 排到最前(最旧),`.1` 中间,无后缀的 current 末尾。
# `sort -r` 是字典序反转,在 `.10` / `.1` / `.2` 三者顺序上会乱;用数字字段排序:
docker exec "$CID" sh -c '
  ls -1 /app/data/observability/loop-lag.jsonl* |
    sort -t. -k3,3rn |    # 第 3 段(".N" 的数字)reverse-numeric 排序;无 .N = 0,排最后
    xargs cat | tail -50
'

# 拷整个 obs 目录到宿主机分析。`docker cp` 在容器停止状态也能跑(只要容器
# 没被 compose down --rmf / rm 掉),所以"backend 已 unhealthy 还没 kill"
# 是最稳的取证窗口:
docker cp "$CID":/app/data/observability ./obs-snapshot-$(date +%s)

# 极端 case:backend 容器已被移除但 named volume 还在。volume 物理名 =
# `<project>_<volume-label>`(intranet 已显式 pin 成 `artifactflow_data`,
# prod / Mode1 仍是 `<project>_artifactflow_data`)—— 先查:
docker volume ls --format '{{.Name}}' | grep -E 'artifactflow_data$'
# 然后用任意有 cp/tar 的镜像挂这个 volume 拷出来。air-gapped 没 alpine 时,
# 直接复用本机已有的镜像(`docker image ls`),只要 entrypoint 不冲突即可。
```

## Step 5:服务还活但变慢——`/admin/runtime` 水位检查

`/health/live` 200 但请求慢、SSE 卡顿,跑这个看实时水位(`src/observability/admin_runtime.py:42`,需 admin token)。同 Step 1 的理由,**多副本场景下必须 docker exec `$CID`**——nginx LB 会把 admin 请求转给任意一个副本,看到的 sampler snapshot 不一定来自卡死的那个。

```bash
TOKEN=<admin JWT>

# 权威:目标副本自身的 sampler
docker exec "$CID" curl -s \
  -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/admin/runtime | jq

# 旁证(单副本 / 想顺手看整体状态)
curl -s -H "Authorization: Bearer $TOKEN" "$HEALTH/api/v1/admin/runtime" | jq
```

关注字段(对齐 `RuntimeSampler` snapshot,见 `src/observability/sampler.py:137`):

- `sampler.loop_lag_ms.p99_ms` / `max_1m_ms` — 看 1 分钟内最坏延迟
- `sampler.db_pool.overflow > 0` — 连接池饱了,正在用 `max_overflow` 兜底
- `sampler.redis.used_mb / maxmemory_mb` — Redis 接近 maxmemory(noeviction 策略,逼近即将拒写)
- `sampler.process.rss_mb` / `open_fds` — 高水位告警 ratio 80%(`sampler.py:60-62`)
- `active_conversations` / `active_tasks` — 在飞 turn,长跑 turn 也在 `sampler.tasks_long_running`(超 `OBS_LONG_TASK_AGE_SEC`,默认 60s)

## Step 6:止血——**取证完了**再重启

取证齐了(Step 3 拿到栈、Step 4 看了 loop-lag)再动手。重启即丢现场,而 wedge 通常半小时内不会自己醒(本次 96 分钟,纯靠同步计算自行算完),所以 Step 3 没成功别急着重启。

```bash
# 软重启(只重启目标副本,其它副本继续服务;前端 / nginx 不动)
docker restart "$CID"
# 或一次重启 backend service 全部副本(多副本部署不想保留任何活动副本时):
docker compose -f "$COMPOSE" restart backend

# 重启前冻结现场拿 coredump(可选,debug symbol 完整才有意义)
PID=$(docker inspect -f '{{.State.Pid}}' "$CID")
sudo gcore -o /tmp/backend-hang "$PID"   # 等几秒到几分钟,看 RSS 大小
# gcore 之后进程继续运行,不杀;杀进程用 docker restart "$CID"
```

`unhealthy ≠ 自动 restart`——`HEALTHCHECK` 翻红只改状态,要靠 autoheal 容器或编排层补救;事故当时干挂 96 分钟零自动恢复(无 autoheal 即无人值守时段全程不可用)。

## Step 7:留痕

- `docker logs "$CID" > backend-<ts>.log` 落盘(带 `Thread 0x` dump 行)
- `data/observability/loop-lag.jsonl*` / `metrics.jsonl*` 通过 Step 4 末尾的 `docker cp` 拷出来 — 持久卷里有,但 rotate 后老切片会丢
- 触发输入:看 `MessageEvent` 表当前 turn 的 `tool_call` 事件——cancel 路径现在无条件持久化事件(事故现场曾因 cancel 抢先而丢失触发输入,现已修复,不再丢)
- 后续分析跑 `python scripts/observability_report.py --hours 24`(数据源:`MessageEvent` + `data/observability/*.jsonl`),解读见 [observability-tuning.md](../guides/observability-tuning.md)

## 已知 wedge 形态参考

| Fingerprint | 根因方向 |
|---|---|
| `/health/live` 卡 + CPU 100% 单核 + `pidstat %usr=100` | 同步 CPU 死算攥 GIL(本次事故型) |
| `/health/live` 卡 + CPU 低 + 多线程 `futex_wait` | 死锁 / asyncio.Lock 未释放 |
| `/health/live` 卡 + `/proc/1/status` State=D | IO 卡死(NFS / 慢盘 / 失联挂载) |
| `/health/live` 200 + 慢 + `db_pool.overflow>0` | 连接池饱;看 `/admin/runtime` 长跑 task |
| `/health/live` 200 + 慢 + `loop_lag p99` 抬升 | 软退化;`loop-lag.jsonl` 看哪 task 拖循环 |

## 工具作者纪律(防止再踩)

CLAUDE.md 已写:**asyncio cancel / timeout / fencing 全是协作式的**,同步 CPU 工具(无 `await` 或 C 扩展持 GIL)能同时击穿所有这些安全机制。工具作者必须自负 CPU 成本:

- 算法侧给上界(如 `update_artifact` 的 `MAX_UNIQUE_CENTERS` 静态 budget)
- 第二层挂 wall-clock deadline(`MAX_FUZZY_WALL_CLOCK_MS`),内循环检查
- 引擎兜不住——和 compaction 不兜底 tool-result 溢出同理

见 `update_artifact.py:315-339`(find_fuzzy_match docstring)对照参考。
