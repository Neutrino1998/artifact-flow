# Runbook · 服务卡死(后端无响应)

> 现场参照:2026-05-14 `compute_update` 卡死事件循环 96 分钟事故,见 `docs/_archive/ops/incident-2026-05-14-eventloop-wedge.md`。本 runbook 把当次实战命令固化下来,oncall 直接 copy-paste。

## 适用症状

- `/health/live` 卡住 / 504(纯协程端点,正常 1ms 内返回)
- 前端"对话发出去没动静",所有 SSE 断
- `docker compose ps` backend 单个核 100%、容器没退出
- 健康探针翻 unhealthy 但容器不重启(`HEALTHCHECK` 翻红 ≠ 自动 restart)

如果只是依赖问题(DB / Redis 慢),`/health/live` **能正常 200**;不在本 runbook 范围,看 `/health/ready` 输出哪个 component `error`。

---

## Step 1:判别"循环卡死"还是"依赖问题"

```bash
# /health/live 不碰任何依赖,只测事件循环本身
curl -m 3 http://localhost:8000/health/live
# 200 → 循环活着,问题在依赖或慢操作,跳 Step 5
# 超时 / 卡住 → 事件循环被饿死,继续 Step 2

# /health/ready 跑 DB + Redis 探针
curl -m 5 http://localhost:8000/health/ready
```

`/health/live` 卡住即可定性:**事件循环死锁**。`/admin/runtime`(`src/observability/admin_runtime.py:42`)同理是 FastAPI 协程端点,循环卡时它也无响应——它的定位是"还活但变慢"水位 triage,不是硬 wedge 入口。

## Step 2:确认是 CPU 型卡死(GIL 被攥住)

```bash
# 1) docker stats — backend CPU 100%(单核)、内存 / 连接数都正常
docker stats --no-stream backend

# 2) 进容器看 process 状态
docker exec backend cat /proc/1/status | head -5
# State 应是 R (running);若是 D (uninterruptible sleep) 看 Step 6 IO 卡死分支

# 3) pidstat:用户态 vs 内核态
docker exec backend pidstat -p 1 1 3
# %usr ≈ 100、%system ≈ 0 → 纯 Python 用户态死算

# 4) strace 看线程在干啥(宿主机执行,带 -f 跟所有线程)
PID=$(docker inspect -f '{{.State.Pid}}' $(docker compose ps -q backend))
sudo strace -f -p $PID 2>&1 | head -40
# 典型 wedge 形态:多个线程停在 futex(FUTEX_WAIT...),没有任何线程发 syscall
# (烧 CPU 的线程攥着 GIL,从不释放,strace 抓不到它)
```

事故当时 `docker stats` 报 backend CPU 101%、内存 474 MiB / 2 GiB 正常、PG 连接 ~12 正常;9 线程里 8 个 `futex_wait`,占 CPU 的那个 strace 完全不出现——单线程纯用户态死算典型 fingerprint。

## Step 3:抓 Python 栈(取证,**重启之前**必跑)

三条路径,**任一**成功即可拿到栈:

```bash
# A) deadman switch 自动 dump(faulthandler.dump_traceback_later,纯 C 线程)
#    循环 wedge >= WATCHDOG_DEADMAN_TIMEOUT_MS (默认 10s) 时自动打 stderr
docker logs backend 2>&1 | grep -A 200 'Thread 0x'

# B) SIGUSR1 手动 dump(faulthandler.register 注册在 main.py:55)
#    适用于 deadman 阈值还没踩到 / 已经 dump 过想再来一发
PID=$(docker inspect -f '{{.State.Pid}}' $(docker compose ps -q backend))
sudo kill -USR1 $PID
docker logs backend --tail 300 2>&1 | grep -A 200 'Thread 0x'

# C) py-spy(backend 镜像已内置,见 Dockerfile:29;需 cap_add: SYS_PTRACE)
docker exec backend py-spy dump --pid 1
# 不出栈 → py-spy 缺 cap(`docker exec backend py-spy --version` 验在;
#                      attach 报 Operation not permitted 即缺 SYS_PTRACE)
# 镜像里没 py-spy → 跑 deploy/scripts/preflight.sh 验镜像版本是否带 PR-forensics-bundle
```

A 路径是默认通道(`src/observability/deadman.py`);C 路径是 deadman 失效时的备份。两者互补:deadman 走 C 线程不需要 GIL,py-spy 直接 attach 进程也不需要 GIL,任一都能在 wedge 期间拿到栈。

## Step 4:看刚才有没有"软退化"事件

事件循环 lag 超 `LOOP_LAG_WARN_MS`(默认 500ms)即由 `LoopLagWatchdog`(`src/observability/watchdog.py`)写一行 `loop-lag.jsonl`,附 `asyncio.all_tasks()` 各任务的栈截断。**watchdog 本身在 Python 线程里持 GIL,硬 wedge 场景下它和事件循环一起睡死,这个文件可能 *没有* 新增条目**——见 `watchdog.py:7-12` 的失效面注释。但在"循环还在跑只是变慢"的退化场景下它是定位拖慢源的主入口。

```bash
# 最近的 loop-lag 事件(jsonl 已轮转,带 * 通配)
docker exec backend tail -f data/observability/loop-lag.jsonl
# 或宿主机直读(持久卷挂载在 ./data/)
tail -f data/observability/loop-lag.jsonl

# 看是不是 wedged
docker exec backend cat data/observability/loop-lag.jsonl | tail -20 | \
  python -c 'import json,sys;[print(json.loads(l).get("ts"),json.loads(l).get("lag_ms"),"wedged" if json.loads(l).get("wedged") else "soft") for l in sys.stdin]'
```

## Step 5:服务还活但变慢——`/admin/runtime` 水位检查

`/health/live` 200 但请求慢、SSE 卡顿,跑这个看实时水位(`src/observability/admin_runtime.py:42`,需 admin token):

```bash
TOKEN=<admin JWT>
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/admin/runtime | jq
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
# 软重启(只 backend,前端不动,nginx 会自动重连)
docker compose -f docker-compose.prod.yml restart backend
# 或内网:
docker compose -f deploy/docker-compose.intranet.yml restart backend

# 重启前冻结现场拿 coredump(可选,debug symbol 完整才有意义)
PID=$(docker inspect -f '{{.State.Pid}}' $(docker compose ps -q backend))
sudo gcore -o /tmp/backend-hang $PID         # 等几秒到几分钟,看 RSS 大小
# gcore 之后进程继续运行,不杀;杀进程用 docker compose restart
```

`unhealthy ≠ 自动 restart`——`HEALTHCHECK` 翻红只改状态,要靠 autoheal 容器或编排层补救;事故当时干挂 96 分钟零自动恢复,见 incident doc §B.6。

## Step 7:留痕

- `docker logs backend > backend-<ts>.log` 落盘(带 `Thread 0x` dump 行)
- `data/observability/loop-lag.jsonl*`、`data/observability/metrics.jsonl*` 复制走(持久卷里就有,但重启可能 rotate)
- 触发输入:看 `MessageEvent` 表当前 turn 的 `tool_call` 事件——cancel 路径事件持久化已在 PR-3 修(`docs/_archive/ops/incident-2026-05-14-fix-plan.md` §PR-3),事故现场是丢的,现在不丢
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

- 算法侧给上界(本次 PR-1 给 `MAX_UNIQUE_CENTERS` 静态 budget)
- 第二层挂 wall-clock deadline(`MAX_FUZZY_WALL_CLOCK_MS`),内循环检查
- 引擎兜不住——和 compaction 不兜底 tool-result 溢出同理

见 `update_artifact.py:315-339`(find_fuzzy_match docstring)对照参考。
