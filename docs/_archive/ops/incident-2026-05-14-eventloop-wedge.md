# 事故复盘:compute_update fuzzy match 卡死事件循环

- **日期**:2026-05-14
- **环境**:内网部署(Mode 3 / CentOS 7,Docker)
- **影响时段**:16:22 – 17:58(约 96 分钟后端无响应)
- **状态**:已自行恢复;根因已定位;修复见 `incident-2026-05-14-fix-plan.md`
- **严重度**:高(用户可见的全后端不可用)

---

## TL;DR

一次 `update_artifact` 工具调用进入 `compute_update` 的 Layer 2 fuzzy match,`fuzzysearch.find_near_matches` 在一段重复性极高的 Markdown 表格上以 `max_l_dist` ≈ 300 运行,陷入候选爆炸,**纯同步 CPU 计算持续约 96 分钟**,占满一个核、攥着 GIL,把整个 asyncio 事件循环饿死。期间所有 API / SSE 无响应,健康探针卡住,前端 SSR 也跟着卡。计算自行算完后服务恢复。

排查过程额外牵出 2 个衍生 bug、1 个预先存在的前端误配、1 个非我方的 DNS 问题,以及一批可观测性 / 运维工具缺口。

---

## 时间线(均来自服务日志,同一 message `msg-260fa24a...`)

| 时间 | 事件 |
|---|---|
| 16:07:34 | 第一次 LLM 调用开始 |
| 16:21:53 | 第一次 `compute_update` 成功(similarity 98.3%),artifact → v3;同秒 SSE 客户端断开;第二次 LLM 调用开始 |
| 16:22:23 | 心跳续租失败,`_renew_loop` 调用 `task.cancel()`("Lease lost — fencing execution") |
| **16:22 – 17:58** | **日志完全静默,backend CPU 100%** |
| 17:58:07 | 第二次 `compute_update` 的 fuzzy match 终于成功(similarity 98.5%),artifact → v4;紧接着任务才响应取消("Task cancelled (lease fencing or shutdown)") |

`task.cancel()` 16:22:23 就发出了,但直到 17:58:07 同步计算自行结束、协程碰到下一个 `await` 时才生效——前后挂起了 96 分钟。

---

## 诊断证据(如何确认是 CPU 型卡死)

- `GET /health/live`(不碰任何依赖的纯协程端点)**也无响应** → 事件循环本身被卡死,不是依赖问题
- `docker stats`:backend CPU **101%**;内存 474MiB / 2GiB(**正常**,排除内存泄漏);PG 连接约 12(**正常**,排除连接池耗尽)
- `cat /proc/<pid>/status` → `State: R (running)`
- `strace -f -p <pid>`:9 个线程,**8 个停在 `futex(FUTEX_WAIT...)`**(排队等 GIL);占 CPU 的那个线程在 strace 里**完全不出现**(它一个 syscall 都不发)
- `pidstat`:**100% `%usr`,0% `%system`** → CPU 全烧在用户态计算

结论:单线程纯用户态 CPU 死算、攥着 GIL,其余线程全部 `futex_wait` 等待。

---

## 发现的问题

### ① 核心 bug:fuzzy match 打爆 CPU,卡死事件循环

- **位置**:`src/tools/builtin/artifact_ops.py` → `MemoryArtifact.compute_update`(签名 ~L297),Layer 2 在 L378–379:
  ```python
  max_l_dist = max(5, int(len(old_str) * max_diff_ratio))   # max_diff_ratio 默认 0.3
  matches = find_near_matches(old_str, self.content, max_l_dist=max_l_dist)
  ```
- **根因**:`max_l_dist` 无绝对上限,由 `len(old_str)` 推出。`fuzzysearch 0.8.1` 的 Levenshtein 搜索在 `m // (max_l_dist+1) >= 3` 时走 n-gram 鸽巢预筛;`max_diff_ratio=0.3` 让 n-gram 长度恰好 ≈ 3。在 Markdown 表格(大量重复 `|`、空格、数字)上,3 字符 n-gram 到处命中 → 候选位置爆炸 → 每个候选还要做左右 Levenshtein 扩展。基准实测:428 字符表格 / 2KB 文档下,k=16 用 0.05s,k=128 即超 3s;生产文档更大,实测 96 分钟。
- **`compute_update` 是同步函数,直接跑在引擎 loop 线程上**(工具执行不在线程池),所以单个慢函数能拖垮整个后端。
- **触发输入**:已丢失(见 bug ④),但日志预览确认是一段 Markdown 表格。

### ② 核心 bug:前端容器 unhealthy(预先存在,红鲱鱼)

- **现象**:`docker compose ps` 显示 frontend `unhealthy`,排查时一度误以为与后端故障同时发生。
- **根因**:部署的前端镜像较旧,缺 `frontend/Dockerfile:21` 的 `ENV HOSTNAME=0.0.0.0`。Next.js standalone 读 `process.env.HOSTNAME`(= 容器 ID)只绑该网卡;healthcheck 探 `localhost:3000` → `ECONNREFUSED` → 永远失败。
- **关键**:**用户流量一直正常**——nginx 走 `frontend:3000` 解析到容器 eth0,恰好就是 Next 监听的网卡。`unhealthy` 是确定性的监控误报,与本次故障**无因果**。最初部署时是 healthy 的(nginx `depends_on: frontend healthy` 能启动起来即为证),约 2 天前一次 app 镜像重发换上了旧镜像才变红。
- **副作用**:监控失明;下次完整 `docker compose up` 时 nginx 会因 `depends_on` 卡住不启动。

### ③ 衍生 bug:cancel / timeout 杀不掉同步 CPU 任务

- asyncio 的取消是**协作式**的:`task.cancel()` 只设标志位,`CancelledError` 要等协程下一次碰到 `await` 才抛出。`find_near_matches` 是纯同步无 `await` 的 CPU 计算 → lease fencing 的 `task.cancel()`(16:22:23)、客户端断开取消、`_check_cancelled` 轮询**全部失效**,只能干等 96 分钟。
- **推论**:加 `asyncio.wait_for` 超时同样无效(其超时实现也是 `task.cancel()`);单纯丢 `asyncio.to_thread` 也不够(线程杀不掉,C 扩展持 GIL 时事件循环仍被饿死)。
- **架构盲点**:引擎整套 cancel/fence/timeout 机制隐含假设"所有耗时操作都是 await 式的"。一个同步 CPU 密集的工具会**同时击穿所有这些安全机制**。类比 CLAUDE.md "compaction 不兜底 tool-result 溢出,工具作者自负输出大小纪律"——这里是同理:工具作者需自负 CPU 成本纪律,引擎兜不住。

### ④ 衍生 bug:lease fencing 的 cancel 路径不持久化事件

- `MessageEvent` 设计为在 `execution_complete` 或 `error` 两个边界 batch write。本次 turn 被 `task.cancel()` kill,`CancelledError` 是 `BaseException`,绕过所有 `except Exception`,**没走到 `error` 持久化边界** → 整个 turn 的内存事件链随任务被 kill 一起丢失,DB 中查无此 msg 的任何 `llm_complete` / `tool_*` 事件。
- **影响**:违反 CLAUDE.md "events persist unconditionally" 不变量。被 fencing 的 turn 无回放、无审计痕迹——本次想分析触发 `old_str` 时正因此线索全无。

### 非我方问题:域名解析

- 域名 `search.cncc.cn` 的 DNS A 记录指向旧 IP `51.1.30.13`,真实服务器在 `111.1.30.13`,故域名访问"无法连接"、IP 访问正常。与 nginx / docker 栈无关(`server_name _;` 不区分域名/IP,IP 通即证明栈正常)。
- 附带:`nslookup` 显示 DNS 服务器 `17.2.209.13` 本身也不稳(请求 timeout)。
- 处置:交 IT / 网络组修正 A 记录。

---

## 暴露的可观测性 / 运维缺口

整个事故的诊断几乎全靠**事后反推**和**宿主机层面取证**,因为应用自身几乎什么都没暴露。这是本次最大的元发现。

### A. 日志缺失

1. **没有工具执行的耗时日志**(最致命)——`compute_update` 只在结束时打一行成功日志,无 start、无 duration。96 分钟是从两行日志之间的静默推断出来的。
2. **没有慢操作的阈值告警日志**("tool X 已运行 >Ns")。
3. **`old_str` 只截断打 100 字符,且不打长度**;配合 bug ④ 事件未落盘,触发输入线索全无。
4. **没有事件循环延迟日志**——循环饿死 96 分钟,应用自身毫无察觉。
5. **健康探针失败不落应用日志**——`/health/ready` 卡住(非抛异常)时什么都不打;容器翻 unhealthy 无对应日志。
6. **cancel "已请求但未生效"这段不可见**——只见 fencing 与 cancelled 两端,中间 96 分钟挂起态无日志。

### B. 运维工具缺失

1. 没有 `py-spy` 或任何 Python 栈抓取手段,且内网离线,临时获取走了一大圈。
2. **没有进程内状态 dump 手段**(最高杠杆)——应用未注册 `faulthandler` 信号处理器,无法 `kill -USR1` 即时 dump Python 栈 / asyncio 任务。
3. backend 容器内工具极简(无 `top`),诊断全靠宿主机。
4. 没有"列出在飞任务"的能力——RuntimeStore 有 lease/task,但无接口可问运行中系统"当前在跑什么、跑了多久"。
5. 没有事件循环 watchdog(独立 OS 线程监测循环响应性)。
6. **unhealthy 不触发任何自愈**——docker healthcheck 翻 unhealthy 不重启容器,卡死但未退出的 backend 干挂 96 分钟、零自动补救。
7. 没有"服务卡死"排查 runbook(`/health/live` vs `/health/ready` 判别、`strace`/`pidstat`/`/proc` 序列、`gcore` 冻结现场流程均为现场摸索)。

### C. 元问题:全部被动发现

backend 卡 96 分钟、前端红 2 天、域名挂、DNS 不稳——无一被告警发现,全是手动 `docker compose ps` 撞见。最根本缺口是**没有告警**:容器健康、CPU 占满、事件循环延迟、探针失败均无人/无系统盯防。

---

## 调研结论(用于指导修复)

两个并行调研:

**Claude Code 怎么做的** —— Edit 工具**完全没有 fuzzy 匹配层**。是一条 3 层确定性链:精确子串 → 引号归一化精确 → 去消毒精确,每层 O(n)。三层全 miss 即响亮失败、回显 `old_str`、靠 prompt 教模型重试。多匹配则数次数、>1 即拒绝。即:本项目的 Layer 2 fuzzy 正是 Claude Code 刻意决定不做的东西。

**业界常用算法** —— `fuzzysearch` 结构性不合格(无成本上界,且有已知正确性 bug),应弃用。`git apply` / Aider / Roo 等严肃工具都避免无边界字符级 fuzzy:要么行级上下文锚定,要么要求调用方给位置 hint,共同点是**先收窄窗口再做 fuzzy**。推荐方案:行锚定两阶段 + `rapidfuzz` 的 `Levenshtein.distance(score_cutoff=k)` 做有界校验,低熵内容(无稀有锚点)当场响亮失败。`regex` 模块的 `timeout=` 是唯一真正的 wall-clock 上界,可作安全网。

---

## 关键代码位置(附录)

| 项 | 位置 |
|---|---|
| fuzzy match | `src/tools/builtin/artifact_ops.py` `compute_update` ~L297,Layer 2 L378–379,接受判定 L394 |
| 心跳续租 / fencing | `src/api/services/execution_runner.py` `_renew_loop`、`_wrapped` |
| 引擎主循环 / cancel | `src/core/engine.py` `_call_llm` L269、主循环 L711、`_check_cancelled` L700 |
| 事件持久化模型 | `src/db/models.py` `MessageEvent` L337(注释说明 batch write 边界) |
| 健康端点 | `src/api/main.py` `/health/live` L111、`/health/ready` L115 |
| 前端 HOSTNAME fix | `frontend/Dockerfile:21`(`ENV HOSTNAME=0.0.0.0`,当前源码已有,旧镜像未带) |
