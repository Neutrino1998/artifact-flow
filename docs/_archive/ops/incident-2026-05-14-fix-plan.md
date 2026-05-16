# 修复 PR 计划:2026-05-14 事件循环卡死事故

配套复盘文档:`incident-2026-05-14-eventloop-wedge.md`

本次事故定位到 **4 个 bug**(2 核心 + 2 衍生)、**1 个非我方问题**、**一批可观测性/运维缺口**。下面按 PR 拆分,标注范围、优先级、分支策略与待决策项。

> **分支策略**(见项目惯例):通用功能修复一律先进 `main`,再合并到内网分支。仅"前端 compose 临时覆盖"是内网特定的运维操作,不走 PR。

---

## 优先级总览

| 优先级 | PR | 说明 |
|---|---|---|
| P0 | PR-1 Layer 2 改造为锚定 + RapidFuzz 校验 | 用有界算法替换 fuzzysearch,关掉病态输入炸 CPU 的可能、保留 Layer 2 救援能力 |
| P0(运维) | 前端 compose 临时覆盖 | 非 PR,内网机直接操作 |
| P1 | PR-obs-lite 轻量可观测性框架 | watchdog(自动栈 dump)+ sampler(jsonl 采样)+ `/admin/runtime` + 分析脚本;不动 DB schema、不上 Prometheus |
| P1 | PR-3 fencing 事件持久化 | 修复 bug ④,恢复审计/回放完整性 |
| P2 | PR-forensics-bundle 取证工具 + 部署前置 | release 内置 py-spy / preflight 检查 / SOP |
| P2 | PR-5 前端镜像重建 | 正式消除 HOSTNAME 误配 |
| P3 | 文档 runbook | 固化"服务卡死"排查流程 |

**小版本迭代打包**:PR-1 + PR-obs-lite(+ PR-3)走同一个 release tag,定位为"事故后加固"——一边修根因,一边补让下次事故 30 秒能拿到栈的观测。PR 独立,可分别回滚。

---

## PR-1:Layer 2 改造为锚定 + RapidFuzz 校验(P0)

**目标**:用"先取稀有精确锚点收窄搜索窗口,再在小窗口内做有界 Levenshtein 校验"的算法替换现有 `fuzzysearch.find_near_matches`。把最坏成本从"文档熵的函数"变成"自选的常数",同时**保留** Layer 2 兜底能力。

**决策依据(为什么走 B 不走 A)**:本地测试日志 `logs/artifactflow.log` 显示 Layer 2 在正常使用中被触发(实测 7 次命中,均为典型"单字符替换"用例:`关于人工智能枝术` → `关于人工智能技术`)。这不是边缘情形,A 路线砍掉 Layer 2 会引入已知回归。B 既保留这个救援能力,又关掉病态输入炸 CPU 的可能。

**决策依据(为什么不分两步)**:这批修复打包发版,没有"当天热修"的紧迫性。原 PR-1 那套封顶代码会被原 PR-2 立刻删掉,**做两次纯浪费**;A/B 选择已基于真实日志数据,不再需要 instrumentation 来选。

**范围**:
- `src/tools/builtin/artifact_ops.py`(`compute_update` Layer 2 重写 + `UpdateArtifactTool.execute` 补 Layer 1 对称 warning)
- `src/config.py`(新增隐藏常量)
- `requirements.txt`(`+rapidfuzz`、`-fuzzysearch`)

**算法**(四步,详细原理见 incident 文档同名小节):

1. **锚点切分**:把 `old_str` 按 shingle(长度 `ANCHOR_SHINGLE_LEN`)切成候选,记录每个 shingle 在 `old_str` 内的偏移。
2. **锚点筛选 + bail-out**:对每个候选 shingle 在 `self.content` 里 `str.count()`,只接受 `1 <= count <= ANCHOR_MAX_OCCURRENCES` 的稀有锚点。从合格锚点里挑 `ANCHOR_NUM` 个,优先 count 小、来自 `old_str` 不同区域。**合格锚点数为 0 → 立即响亮失败**,带 hint。这一步是核心安全机制——低熵 / 大幅漂移内容在进入字符级比对**之前**就被识别并拒绝。
3. **候选窗口生成**:对每个锚点的每个出现位置,反推 `old_str` 起点 = `pos_in_content − offset_in_pattern`,取窗口 `content[start − slack : start + len(old_str) + slack]`。总窗口数硬上界 = `ANCHOR_NUM × ANCHOR_MAX_OCCURRENCES`。
4. **有界校验**:每个窗口跑 `rapidfuzz.distance.Levenshtein.distance(old_str, window, score_cutoff=FUZZY_MAX_L_DIST)`,达标进 matches。复用现有的"挑最佳 + ambiguity 检测"逻辑。

**隐藏常量(`src/config.py`,按 CLAUDE.md 惯例放在这里)**:

| 常量 | 含义 | 建议起点 |
|---|---|---|
| `ANCHOR_SHINGLE_LEN` | shingle 切分长度 | 6 |
| `ANCHOR_MAX_OCCURRENCES` | 锚点最多接受的出现次数 | 20 |
| `ANCHOR_NUM` | 选几个锚点 | 3 |
| `FUZZY_MAX_L_DIST` | 校验编辑距离上限 | 16 |
| `WINDOW_SLACK_RATIO` | 窗口比 old_str 多取的比例 | 0.2 |
| `FUZZY_OLD_STR_MIN_LEN` / `MAX_LEN` | fuzzy 层 old_str 长度上下限 | ~16 / ~3000 |

**失败路径 hint 设计**(统一回明确的下一步指引):
- `no rare anchor`(Step 2 bail) → "old_str 太重复或文档已大幅漂移,请重新 Read 后提供更独特的上下文,或改用 `rewrite_artifact`"
- `no window matched`(Step 4 全部超 cutoff) → 同上
- `ambiguous`(Step 4 校验后**达标窗口数 ≥ 2,不论距离差是否并列**)→ "old_str 在文档中有多个候选位置,请扩展上下文使其唯一"

> **唯一性对齐 Layer 0/1**:旧 fuzzysearch 实现里"多候选时静默挑距离最近"是个跟 Layer 0/1 不一致的设计漏洞——Layer 0/1 因为精确匹配里**没有合理的挑选依据**一直坚持 `count == 1`,Layer 2 没理由更松。新实现里**任何 ≥2 个达标窗口都直接报错**,让模型显式澄清,不替它做隐式选择。这是本 PR 顺手堵掉的一个隐性 bug。

**最坏成本上界**:`ANCHOR_NUM × ANCHOR_MAX_OCCURRENCES × O(len(old_str) × FUZZY_MAX_L_DIST / 64)` ≈ `3 × 20 × O(3000 × 16 / 64)` ≈ 几毫秒级,**自选常数**。

**顺带补:Layer 1 normalized 的对称 warning**(这次事故顺带翻出的设计不一致):

现状是 fuzzy 命中时 `UpdateArtifactTool.execute` 返回带 `fuzzy="X%"` + `<fuzzy_detail>` 的结构化提示,但 **Layer 1 归一化命中时只在消息字符串里说一句 `normalized match X%`、没有结构化 expected/matched 对比**——模型看不到 `Ⅳ → IV`、`café` 的 NFC/NFD 这类归一化具体改了什么。`compute_update` 的 Layer 1 分支其实已经在 `match_info` 里返回了 `expected_text` / `matched_text`,只是 tool 那层没拎出来用。

改动:把 `UpdateArtifactTool.execute` 里 `match_type == "fuzzy"` 那段的判断扩成 `match_type in ("fuzzy", "normalized")`,Layer 1 走对称的 `normalized="X%"` 属性 + `<normalize_detail>` 块。5–10 行,纯加,不动 Layer 1 算法本身。

> 注:更彻底的做法(Option B)是迁到 xml_parser 用的 `<parser_warnings>` 那条独立通道、ToolResult 加 `tool_warnings` 字段,涉及 formatter 和 ToolResult 数据结构。**本 PR 不做**,作为日后清理时跟"Layer 1 的 similarity 改名 / 含义对齐"一起处理。

**测试覆盖**:
- ✅ 单字符替换(测试日志里的 `枝/技` case)—— 走主路径
- ✅ 低熵 / 表格 / 纯模板 —— 走 Step 2 bail-out,失败响亮
- ✅ 长 `old_str`(>1500 字符)—— 验证有界
- ✅ Ambiguity case(≥2 个窗口同时达标,**不论距离差**)—— 失败带 hint(对齐 Layer 0/1 的严格唯一性)
- ✅ 完全漂移(`old_str` 字符级散乱漂移,没有 shingle 精确出现)—— Step 2 bail(诚实 trade-off:旧 fuzzysearch 有概率给一个不可靠的"匹配",B 选择失败而不是猜)
- ✅ Layer 1 normalized 命中(如 `Ⅳ ↔ IV`、`café` NFC/NFD)—— 返回值含 `normalized="X%"` 属性 + `<normalize_detail>` 块,跟 fuzzy 对称

---

## PR-3:lease fencing 事件持久化(P1)

**目标**:修复 bug ④,让被 fencing 取消的 turn 也持久化已产生的事件,恢复 "events persist unconditionally" 不变量。

**范围**:`src/api/services/execution_runner.py`(`_wrapped` 的 `CancelledError` 处理)、可能涉及 controller 的 `post_process` / 事件持久化路径。

**做法**:在 `_wrapped` 捕获 `CancelledError` 时,走一遍事件 batch write(类似 `error` 边界的处理),再重新抛出 / 收尾。需注意 `CancelledError` 不能被吞掉,持久化失败也要有兜底。

**关联 bug ③**:bug ③(cancel 杀不掉同步 CPU)的根治不在本 PR——它由 PR-1 的"算法本身就有界"直接解决(最坏几毫秒级,根本不需要被中断)。本 PR 只负责"被取消时不丢事件"。建议在代码注释 / CLAUDE.md 里补一句架构约束:**同步 CPU 密集的工具会击穿引擎所有 cancel/timeout 机制,工具作者需自负成本纪律**。下次 wedge 的发现路径由 PR-obs-lite 的 watchdog 承担(超阈值自动 dump 栈到 `logs/loop-lag.jsonl`),不指望 cancel 路径救场。

---

## PR-obs-lite:轻量可观测性框架(P1)

**目标**:用最小代价补齐 incident 暴露的可观测性缺口(A1–6 / B1–7),让下次事故 30 秒内有栈可看、试运行期间能用一个 Python 脚本跑出资源使用报告。

**约束(明确写下,避免范围漂移)**:
- ❌ 不动 DB schema(沿用现有 `MessageEvent.data` JSON 列;聚合走 `data->>` 表达式)
- ❌ 不上 Prometheus / Grafana / OTel
- ❌ 不拆 cache token(自家模型,不涉及差价计费)
- ✅ 业务侧观测复用已有 event 流,运行时 / 系统侧观测落 jsonl
- ✅ 产出形态满足"裸 Python 脚本 + pandas 跑得出报告"

### 数据源(三处,零 schema 变更)

| 来源 | 内容 | 状态 |
|---|---|---|
| `MessageEvent` 表(JSON 列) | LLM/工具调用:`model`、`token_usage`、`duration_ms`、`tool`、`params`、`success` — 均已在 `llm_complete` / `tool_complete` payload | ✅ 已有 |
| `logs/metrics.jsonl` | 周期采样:loop_lag p50/p99、process RSS/CPU/FD、DB pool、Redis used、in-flight 数及最长任务 age | ➕ 新增 |
| `logs/loop-lag.jsonl` | 事件驱动:loop_lag 超阈值时一条记录,带 `asyncio.all_tasks()` 各任务栈截断 | ➕ 新增 |

> 复核结论:`engine.py` 已在 L344-378 emit `llm_complete`(含 `model` / `token_usage` / `duration_ms`)、L686-695 emit `tool_complete`(含 `tool` / `params` / `duration_ms` / `success`)。MessageEvent 业务侧不缺字段,**不需要加 event payload**,只需在分析侧用 JSON 表达式取出。

### 注入点(三个组件,共 `src/observability/`)

1. **`watchdog.py` — 事件循环延迟监测(独立 OS 线程)**
   - `threading.Thread(daemon=True)`,FastAPI lifespan 启动
   - 每 1s 通过 `loop.call_soon_threadsafe` 投时间戳回调,测投递→执行延迟
   - 滚动窗口存 p50 / p99 / 1 分钟 max(供 `/admin/runtime` 端点读)
   - 超 `LOOP_LAG_WARN_MS`(默认 500ms)→ 写一行到 `logs/loop-lag.jsonl`,含 `asyncio.all_tasks()` 每个 task 的 `get_stack()` 前 N 帧(截断)
   - 超 `LOOP_LAG_DUMP_MS`(默认 5000ms)→ 同时 `faulthandler.dump_traceback()` 到 stderr
   - **失败必须吞**(observer 不能拖累 observee)。**不在 asyncio task 里**——循环卡死时它自己也会被卡

2. **`sampler.py` — 周期采样(asyncio task)**
   - `OBS_SAMPLE_INTERVAL_SEC`(默认 30s)采一次,写一行 JSON 到 `logs/metrics.jsonl`,字段示例:
     ```json
     {"ts":"2026-05-16T03:14:00Z","loop_lag_ms":{"p50":3,"p99":18,"max_1m":95},
      "in_flight":2,"tasks_total":134,"tasks_long_running":0,
      "db_pool":{"in_use":3,"overflow":0,"waiters":0},
      "redis":{"used_mb":87},
      "process":{"rss_mb":512,"cpu_pct":12,"open_fds":87},
      "data_dir_mb":1843}
     ```
   - 逼近上限(RSS > 80% mem_limit / Redis used > 80% maxmemory / DB pool waiters > 0 / open_fds > 80% ulimit)→ 额外打一行 WARN 日志(loud failure 原则,对齐 `feedback-loud-failure-over-silent-eviction`)
   - sampler 自身异常一律吞

3. **`admin_runtime.py` — `GET /admin/runtime` 端点**
   - `require_admin`,返回 sampler 最近快照 + 现拉的 `RuntimeStore.list_active()`
   - 卡住时第一个 curl 的东西(走 nginx,不依赖 SSE)。诊断 1.0 工具

### 分析脚本:`scripts/observability_report.py`

目标:你跑一下就能看的报告。

```python
# 业务侧:从 MessageEvent JSON 列拉
df_llm = pd.read_sql("""
    SELECT created_at, agent_name,
           data->>'model' AS model,
           (data->'token_usage'->>'input_tokens')::int  AS in_tok,
           (data->'token_usage'->>'output_tokens')::int AS out_tok,
           (data->>'duration_ms')::int AS dur_ms
    FROM message_events
    WHERE event_type='llm_complete' AND created_at > now() - interval '24 hours'
""", engine)
df_tool    = pd.read_sql("""... event_type='tool_complete' ...""", engine)
df_runtime = pd.read_json("logs/metrics.jsonl",  lines=True)
df_lag     = pd.read_json("logs/loop-lag.jsonl", lines=True)
```

报告内容:
- LLM 调用按 model / agent 聚合(次数、token 总量、p99 latency)
- 工具调用按 name 聚合(p50 / p99 / max latency、失败率)
- 24h loop lag 分布(中位 / p99 / max,看有没有逼近阈值)
- 24h RSS / CPU / FD / DB pool / Redis 时序图(matplotlib 几张图,可选)
- 触发的 loop-lag 事件列表(直接是下次事故的诊断入口)

### 隐藏常量(`src/config.py`)

| 常量 | 含义 | 建议起点 |
|---|---|---|
| `LOOP_LAG_WARN_MS` | watchdog 写 loop-lag.jsonl 阈值 | 500 |
| `LOOP_LAG_DUMP_MS` | watchdog 触发 `faulthandler.dump_traceback` 阈值 | 5000 |
| `OBS_SAMPLE_INTERVAL_SEC` | sampler 周期 | 30 |
| `OBS_LONG_TASK_AGE_SEC` | "长时间运行任务"门槛 | 60 |
| `OBS_METRICS_LOG_PATH` | 周期采样 jsonl 路径 | `logs/metrics.jsonl` |
| `OBS_LOOP_LAG_LOG_PATH` | loop-lag 事件 jsonl 路径 | `logs/loop-lag.jsonl` |

### 新依赖

- `psutil`(进程 RSS / CPU% / FD / disk usage,跨平台)。`requirements.txt` 加一行。
- 内网 release bundle 需带上对应 wheel(走 vendor / offline 安装)。

### `faulthandler` 注册(运维兜底)

`src/api/main.py` lifespan 启动时:
- `faulthandler.enable()` 把 SIGSEGV 等致命信号的栈打到 stderr
- `faulthandler.register(signal.SIGUSR1)` 保留 `kill -USR1 <pid>` 手动 dump 能力(不依赖 watchdog 自动)

### 日志分级原则(明确写下,避免出过事故就反射性开 DEBUG)

- **INFO(生产常驻)**:运维诊断必需事件——工具 start/duration、关键参数尺寸、状态转移、慢操作 WARN 的对侧、sampler 心跳异常。**这次缺的数据本就该是 INFO,不该藏在 DEBUG 后面。**
- **DEBUG(仅排查时点亮)**:大体积 / 低频价值 / 敏感负载——完整 LLM messages、完整工具结果 body、内部 trace breadcrumb、压缩详情等。
- **WARNING / ERROR**:真异常,不因 INFO 看着乱就降级。

> 心智模型:DEBUG 是调试时点的灯,不是"出过事故所以以后都开着"。生产 INFO 必须自带足够诊断信息——把 DEBUG 永久打开 = 用错工具应对真问题(给不到你想要的,又把你不想要的塞过来)。

### 日志审计(随 PR 一起做)

扫一遍 `src/core/` / `src/tools/` / `src/api/` 的 `logger.*` 调用,按上述分级原则调整。典型方向:
- **工具完成 / 外部调用结果**(如 `web_fetch.py:213/279`、`artifact_ops` Layer 命中)→ DEBUG → INFO
- **状态转移 / 业务事件**(conversation 创建、artifact 版本变更)→ 确认 INFO
- **大体积内容 dump**(`engine.py:732` 完整 messages、压缩详情)→ 保持 DEBUG
- **异常路径**:检查"应当 WARNING 却只在 DEBUG 打"、"INFO 误打 KB 级内容"

**和上面新增 INFO 项同一个 PR 做**——分开做容易出现两套风格。

### 范围

`src/observability/`(新)、`src/api/main.py`(lifespan 注册 + 路由)、`src/config.py`(常量)、`requirements.txt`(`+psutil`)、`scripts/observability_report.py`(新)、+ 项目内 `logger.*` 审计涉及的零散文件。

### 与 CLAUDE.md 不变量的兼容性

- **Event sourcing**:采样数据**不**进 `MessageEvent`(避免污染 history;EventHistory 不该看到这些)
- **Three-layer**:watchdog / sampler 是 infra,装在 `src/observability/`,不进 Repo/Manager;`/admin/runtime` 走标准 Router → 新 `RuntimeInspector` Manager
- **参数最小化**:阈值常量全进 `src/config.py`,不暴露给模型、不暴露 API
- **Loud failure**:逼近上限走 WARN 日志,不做自动 LRU / 驱逐 / 降级

### 撤销 / 移走的项

- **原 PR-4b**(`/metrics` + Prometheus + Grafana)→ **撤销**,挪到 P3 backlog。等出现"jsonl + 脚本不够用、需要图和告警"的明确信号再做;届时 sampler 字段已就位,加 Prometheus exporter 是平移工作
- **原 PR-4c 的取证工具 / preflight**(py-spy bundle 等)→ **拆为单独 PR**(见下方 PR-forensics-bundle)

---

## PR-forensics-bundle:取证工具与部署前置(P2)

**目标**:消除"内网现抓不到 py-spy"这类事故时的工具就绪缺口。属部署 / 发版工程,不进运行时。

**核心心态**:取证工具的可用性是部署时的保证,不是事故时的指望。

**范围**:`deploy/`(release bundle 脚本)、`deploy/scripts/preflight.sh`(新)、`docs/_archive/ops/deployment-sop.md` / `cloud-service-checklist.md`(增改)。

**内容**:
1. release bundle 内置 `py-spy` 静态二进制(单文件几 MB),从根上消除"内网现抓不到"
2. preflight 脚本验证宿主机有 `gdb`/`gcore`、`strace`、`procps`
3. 诊断策略定调:app 镜像保持精简,取证走宿主机(不往镜像塞 `top`)
4. SOP 增"取证工具就绪"检查项

**与 PR-obs-lite 的关系**:观测框架在容器内自动产出数据;取证工具是手动深挖时的最后一公里。两者互补,但部署节奏可以分开——PR-obs-lite 主要是代码改动,本 PR 主要是 release bundle / 文档,合到同一个 release 也行,但 PR 独立可分别回滚。

---

## PR-5:前端镜像重建(P2)

**目标**:正式消除 HOSTNAME 误配(bug ②)。

**正式修复**:用当前源码(`frontend/Dockerfile:21` 已含 `ENV HOSTNAME=0.0.0.0`)重新构建前端镜像并发版。本质是走一遍正常发版流程,无代码改动。

**内网临时缓解**(非 PR,立即可做):在内网机 `deploy/docker-compose.intranet.yml` 的 `frontend` 服务加:
```yaml
    environment:
      - HOSTNAME=0.0.0.0
```
然后 `docker compose -f deploy/docker-compose.intranet.yml up -d --force-recreate frontend`。新镜像发版后此覆盖可保留(无害)或移除。

---

## 文档:服务卡死排查 runbook(P3)

把本次现场摸索出的诊断序列固化为 runbook(放 `docs/` 运维章节):
- `/health/live` vs `/health/ready` 判别(循环卡死 vs 依赖问题)
- `docker stats` / `pidstat` / `/proc/<pid>/status` / `strace -f` 判别 CPU 型卡死
- `py-spy dump` 抓栈(含内网离线获取 py-spy 的方法)
- `gcore` 冻结现场流程
- 止血:`restart backend`,以及"重启前先取证"的纪律

---

## 待决策项汇总

1. **PR-1 配置常量起点值**:`FUZZY_MAX_L_DIST` / `ANCHOR_MAX_OCCURRENCES` / `ANCHOR_NUM` / `ANCHOR_SHINGLE_LEN` 等的初始默认值——上面给的是建议起点,可在实现/测试阶段微调。上线后按真实日志可调(都是隐藏常量)。
2. **PR-obs-lite 配置常量起点值**:`LOOP_LAG_WARN_MS` / `LOOP_LAG_DUMP_MS` / `OBS_SAMPLE_INTERVAL_SEC` 等——同上,先按建议值上,试运行第一轮看 jsonl 报告再调。
3. **jsonl 轮转**:`logs/metrics.jsonl` 30s 一行,一天 ~2880 行 / 几百 KB;`logs/loop-lag.jsonl` 仅事件驱动,体量小。试运行阶段可手动归档;长期需接 logrotate(独立运维配置,不进 PR)。
4. **Prometheus / 告警路径**:暂不做(`/metrics` exporter + 告警体系)。等出现"jsonl + 脚本不够用、需要图和阈值告警"的明确信号再启动决策;届时 sampler 字段已就位,加 exporter 是平移工作。
5. **运维**:是否引入 unhealthy → 自动重启(autoheal 容器 / 编排层)?独立于以上 PR 的韧性决策。
6. **告警**:容器健康 / CPU 占满 / 事件循环延迟 / 探针失败的告警体系——本次所有问题都是被动发现。短期靠 PR-obs-lite 的 jsonl + 人工巡检兜住;成体系告警依赖第 4 项的 Prometheus 决策。

> **已决策**(留作可追溯):A vs B 选 B,基于本地 `logs/artifactflow.log` 实测 Layer 2 被触发(`枝/技` 单字符替换用例)。详见 PR-1 节"决策依据"。原 PR-1(封顶热修)和原 PR-2(分两步走)已合并为单一 PR-1。
