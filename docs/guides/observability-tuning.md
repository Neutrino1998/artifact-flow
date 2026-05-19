# 可观测性报告解读与调参

> `scripts/observability_report.py` 的输出怎么读 + 哪些隐藏常量在什么信号下要调。报告读出来再回来调常量,**不要凭印象先动**。

`observability_report.py` 数据源三处:`MessageEvent` 表(LLM / 工具调用)、`data/observability/metrics.jsonl*`(`RuntimeSampler` 周期采样)、`data/observability/loop-lag.jsonl*`(`LoopLagWatchdog` 软退化事件)。脚本对齐见 `scripts/observability_report.py:9-21` 的 docstring。

## 跑一下

```bash
# 默认 24h 窗口
python scripts/observability_report.py

# 拉长到 72h
python scripts/observability_report.py --hours 72

# pandas 缺失时:走 release bundle 的离线 wheel
pip install --no-index --find-links analyst-tools/wheels pandas
```

pandas 不在 runtime `requirements.txt`(`observability_report.py:14-17`),只有分析机要装;release bundle `--with-analyst-tools` 跟 backend 镜像 ABI 对齐(`scripts/release.sh:27-30`)。

数据库 URL 优先级:`ARTIFACTFLOW_DATABASE_URLS` 第一个 → `ARTIFACTFLOW_DATABASE_URL` → `sqlite+aiosqlite:///data/artifactflow.db`(`observability_report.py:62-81`,与 app 一致,生产同时设两者时不会查错库)。

---

## Section 1:`LLM calls`

```
=== LLM calls (24h, by model × agent) ===
              calls  in_tok  out_tok  p50_ms  p99_ms
model  agent_name
...
```

字段定义(`observability_report.py:84-94, 169-181`):

- `calls` — 24h 内 `llm_complete` 事件计数,按 `(model, agent_name)` 分组
- `in_tok` / `out_tok` — `data.token_usage.input_tokens / output_tokens` 求和(成本核算的基础)
- `p50_ms` / `p99_ms` — `data.duration_ms` 的 0.5 / 0.99 分位

**调参信号**:

- 同一 model 在不同 agent 下 p99 差距大 → 系统 prompt 长度差异或 tool round 多;看是否需要 per-agent `max_tool_rounds` 配置(`config/agents/*.md` frontmatter)
- p99 `> 60000ms` 且 calls 多 → compaction 阈值偏低,频繁触发;**单独看下方 `COMPACTION_TOKEN_THRESHOLD`**(在 `src/config.py`)
- `out_tok / calls` 异常低 → 模型早停或 cancel 多

## Section 2:`Tool calls`

```
=== Tool calls (24h, by tool) ===
              calls  p50_ms  p99_ms  max_ms  failures
tool
update_artifact   12     45     320     510         0
read_artifact     34     12      28      55         0
...
```

字段(`observability_report.py:97-106, 184-196`):

- `failures` — `data.success` 为 false 的计数;**任一非零都值得追**
- `max_ms` — 单次最慢(p99 平均化容易掩盖个别 outlier)

**调参信号**:

- `update_artifact max_ms` 接近 `MAX_FUZZY_WALL_CLOCK_MS`(默认 500ms)→ Layer 2 在踩 deadline,转看 Section 3
- 任何工具 `max_ms > 30s` 但循环没卡 → 工具内部已经在 `await`,但应该看是否能拆 / pagination
- `failures` 突增配合 LLM `p99_ms` 抬升 → 可能是上游接口异常,看应用日志

## Section 3:`update_artifact fuzzy_stats` 调参报表

这是事故后专为 Layer 2 fuzzy match 加的报表,字段来自 `update_artifact.py:288-312` 的 `_build_stats`,经 `ToolResult.metadata.fuzzy_stats` 透传(`observability_report.py:199-251`)。

### 3.1 `outcome distribution`

```
-- outcome distribution --
matched           87
bail_no_anchor     3
bail_deadline      1
```

枚举值(完整集合见 `update_artifact.py`,grep `outcome=`):

| outcome | 含义 | 健康线 |
|---|---|---|
| `matched` | Layer 2 找到唯一匹配 | 进 Layer 2 的样本里应是主项 |
| `bail_low_entropy` | 鸽巢推完 L 低于 `ANCHOR_MIN_USABLE_LEN`,无可用锚点 | 极少 |
| `bail_no_anchor` | 锚点全在 `ANCHOR_MAX_OCCURRENCES` 之上被标 common,或 content 里找不到 | 零星 |
| `bail_budget` | `m > MAX_FUZZY_OLD_STR_LEN`(input 长度上界) 或 unique_centers 超 `MAX_UNIQUE_CENTERS` | 零星 |
| `bail_deadline` | Step 4 内循环超 `MAX_FUZZY_WALL_CLOCK_MS` wall-clock | 零星 |
| `bail_ambiguous` | 多个区域无法区分(Layer 2 不能比 Layer 0/1 更松) | 零星,模型应改 old_str 重试 |
| `bail_no_window` | 候选 center 全部被 verify 拒绝 | 零星 |

**只有 Layer 2 路径设 `fuzzy_stats`**——Layer 0(exact)/ Layer 1(normalized)成功不进这张表(见 `update_artifact.py:639-718` 的 compute_update,Layer 0/1 的 `MatchInfo` 不带 `fuzzy_stats`)。这张表反映的是"进到 Layer 2 的样本"分布。

**注意**:Section 2 的 `update_artifact.calls` 减去这张表的总数 **不等于** Layer 0/1 命中量。`calls` 还包含一批没有 `fuzzy_stats` 的失败路径——precondition 失败(`ArtifactManager not configured` / `No active session`,见 `update_artifact.py:819-824`)、Layer 0 / Layer 1 唯一性失败(`appears N times`,`update_artifact.py:650-653` / `update_artifact.py:673-679`)、artifact_id 不存在等。要精确算 Layer 0/1 命中量,需在 `update_artifact.py` 显式记 `match_type=exact/normalized` 的成功事件,目前没有这个 metric。

`bail_*` 持续上涨表示模型在踩边界——通常不是常量调小了,**先看是不是 prompt 让模型给的 `old_str` 上下文太短**。

### 3.2 `unique_centers` 直方图

```
-- unique_centers histogram (vs MAX_UNIQUE_CENTERS) --
(-0.001, 5.0]     72
(5.0, 10.0]       11
(10.0, 20.0]       4
(20.0, 30.0]       0
(30.0, 50.0]       0
(50.0, 100.0]      2    ← unique_centers 已超 MAX_UNIQUE_CENTERS=50,bail_budget
(100.0, 10000.0]   0
```

分箱写死 `[0, 5, 10, 20, 30, 50, 100, 10000]`(`observability_report.py:222`),共 7 桶。`(50, 100]` 桶 = 已经踩穿 `MAX_UNIQUE_CENTERS=50`,这批是 `bail_budget`;`(100, 10000]` 是更极端的输入(中心数破百)。`MAX_UNIQUE_CENTERS` 不变的话这两个桶都应该是 0。

**调参信号**:

- `(50, 100]` + `(100, 10000]` 合计 > 5%(即 unique_centers > `MAX_UNIQUE_CENTERS=50` 总占比)→ 真实输入比设计假设密,这批一定走了 `bail_budget` 路径;先确认不是病态输入,再考虑放宽到 80 / 100,**不要无脑加大**(50 这个值是 Step 3 去重后 center 数上限,直接乘到 Step 4 的 `(2k+1)²` 偏移枚举里——常量翻倍 = 最坏 CPU 翻倍)
- 高分桶集中在 `(30, 50]` → 余量不够;`MAX_FUZZY_WALL_CLOCK_MS` 还没跳,但下一个边界用例就会跳

### 3.3 `elapsed_ms` vs `MAX_FUZZY_WALL_CLOCK_MS`

```
-- elapsed_ms vs MAX_FUZZY_WALL_CLOCK_MS --
  p50=12.3  p99=180.5  max=498.2
```

`MAX_FUZZY_WALL_CLOCK_MS` 默认 500ms。max 贴近 500 表示 deadline 在救场——是 deadline 应该工作的方式,不是 bug;但要看 `outcome` 里有几个 `bail_deadline`,持续多就转 3.1 找原因。

### 3.4 `similarity_pct` 直方图(仅 matched)

```
-- similarity_pct histogram (matched only) --
(-0.001, 70.0]    0
(70.0, 80.0]      1
(80.0, 90.0]      3
(90.0, 95.0]      11
(95.0, 99.0]      42
(99.0, 100.0]    30
```

分箱写死(`observability_report.py:241`)。`<90%` 命中堆积可能是 `FUZZY_MAX_RATIO`(0.10)放得太松——拒绝太弱,可能误匹配;`>99%` 占主流是预期。

### 3.5 `old_str_hash` Top-10

```
-- top-10 most frequently triggered old_str hashes --
3f2a...   23
ab19...    8
...
```

只存 hash,不存原文(`update_artifact.py:295-296` 的设计契约)。同一 hash 反复触发 = 同一 old_str 被反复编辑 / 反复 fuzzy 救场,可能值得查 conversation 看模型在不在打转。

---

## Section 4:`Runtime metrics`

```
=== Runtime metrics (data/observability/metrics.jsonl*) ===
  rows=2880, window=[2026-05-12 ... ~ 2026-05-13 ...]
  loop_lag p50_ms: median=3.0  p99=8.0  max=18.0
  loop_lag p99_ms: median=8.0  p99=22.0  max=120.0
  loop_lag max_1m_ms: median=18.0  p99=95.0  max=520.0
  RSS (MB): min=180.0  p50=420.0  p99=510.0  max=520.0
  CPU %: min=0.5  p50=8.0  p99=42.0  max=98.0
  Open FDs: min=80.0  p50=110.0  p99=130.0  max=145.0
  db_pool.in_use: p99=4.0  max=5.0
  db_pool.overflow: p99=0.0  max=0.0
```

数据来源:`RuntimeSampler.sample_once`(`src/observability/sampler.py:137`),每 `OBS_SAMPLE_INTERVAL_SEC`(默认 30s)采一次。打印逻辑 `observability_report.py:254-287`。

### 4.1 `loop_lag` 三个聚合

每条 sample 自带 `loop_lag_ms.{p50_ms,p99_ms,max_1m_ms}`(`watchdog.py:148-161`,1 分钟滚动窗 60 样本)。报告里对**这三个字段**再做跨 sample 聚合——所以 `p99 of p99` 看的是"1 分钟最坏延迟"的 24h 分布。

**调参信号 / `LOOP_LAG_WARN_MS`(默认 500ms)**:

- `max_1m_ms.p99` 贴近 `LOOP_LAG_WARN_MS` → 阈值偏紧,系统接近误报;放宽到 800-1000
- `max_1m_ms.p99` << `LOOP_LAG_WARN_MS` 且持续没事件 → 阈值偏松,降到 300 试探
- `max_1m_ms.max > 5s` → loop wedge 事件;查 Section 5

### 4.2 `RSS / CPU / Open FDs`

`process` 字段(`sampler.py:237-251`)。

**调参信号 / `OBS_MEM_LIMIT_MB`**:

- 默认 `OBS_MEM_LIMIT_MB=0` 走自动 resolve:env > cgroup v2 > cgroup v1 > 不告警(`sampler.py:330-362`)
- 高水位 ratio `_RSS_WARN_RATIO=0.80`(`sampler.py:60`)写死,不是隐藏常量
- RSS p99 持续 > 80% mem_limit → 容量规划信号,先看 `MAX_CONCURRENT_TASKS`(默认 10);别先加 mem_limit
- Open FDs 趋势上涨不下降 → connection / file 泄漏,看 `db_pool.size` 是否同涨

### 4.3 `db_pool`

字段 `in_use / size / overflow`(`sampler.py:204-221`)。

- `overflow > 0` p99 = 已经在用 `max_overflow` 兜底 → 调 `DATABASE_POOL_SIZE`(默认 5)
- `in_use.max >= size + max_overflow - 1` 持续 → 接近耗尽,加 pool 或查长事务

---

## Section 5:`Loop-lag events`(软退化)

```
=== Loop-lag events (软退化, data/observability/loop-lag.jsonl*) ===
  total events: 3
  lag_ms: p50=620  p99=1850  max=2100
  -- last 5 events (truncated) --
    2026-05-13T08:22:11+00:00  lag=620ms  tasks=15
    ...
  Hard wedge (GIL held by C extension) dump 入口:
    see docs/runbooks/service-hang.md Step 3 (compose mode varies)
```

数据源 `LoopLagWatchdog._record_wedge`(`watchdog.py:163`)。一行 = 一次循环 lag 超 `LOOP_LAG_WARN_MS`(默认 500ms)。

**调参信号**:

- `total events / 24h > 10` → 系统经常软退化,看 last 5 events 的 `tasks` 字段哪个 task 持续出现(`watchdog.py:192-220` 收集每个 task 的栈截断 8 帧)
- `wedged: true` 行 → 4× warn_ms 内回调没回来(`watchdog.py:135-139`),已经在 deadman 范围内,转 [service-hang.md](../runbooks/service-hang.md)
- 0 事件持续多日 → watchdog 没在跑(检查 `_start_observability` 启动日志)或阈值过松

**watchdog 的失效场景**(必须明确):C 扩展持 GIL 时所有 Python 线程一起 `futex_wait`,本组件也睡死,**这种场景 jsonl 不增长**。这是设计上的失效面(`watchdog.py:7-12`),由 `DeadmanSwitch` 走 C 线程兜底。

---

## 隐藏常量速查

所有常量在 `src/config.py`,不暴露给模型,只能改源码 + 重启。改之前先有数据(本文档对应 section 的信号)。

### update_artifact Layer 2(`src/config.py:53-62`)

| 常量 | 默认 | 调整信号 |
|---|---|---|
| `ANCHOR_SHINGLE_LEN` | 6 | 调小 = 更密集锚点(候选爆炸);调大 = 鸽巢可能不满足,bail_low_entropy 增 |
| `ANCHOR_MIN_USABLE_LEN` | 3 | 鸽巢推完 L 的最低门槛;`bail_low_entropy` 高于预期可降到 2,但**风险**:锚点更易在低熵文本被打 common |
| `ANCHOR_MAX_OCCURRENCES` | 20 | 一个 shingle 在 content 内最多出现几次仍视为可用锚点;`bail_no_anchor` 多且 content 有大量重复时,放宽到 30 试 |
| `MAX_UNIQUE_CENTERS` | 50 | 见 Section 3.2 |
| `MAX_FUZZY_WALL_CLOCK_MS` | 500 | 见 Section 3.3 |
| `FUZZY_MAX_L_DIST` | 16 | k 绝对上界;调大 = 允许更大编辑距离,但 Step 4 `(2k+1)²` 偏移枚举平方增,wall-clock 必然涨 |
| `FUZZY_MAX_RATIO` | 0.10 | k 比例上界;调大 = 短串容易过松误匹配,见 Section 3.4 |
| `MAX_FUZZY_OLD_STR_LEN` | 10000 | input 硬上界,m > 此值立即 `bail_budget`;**这是事故根因防线**,改之前看 incident doc PR-1 §决策依据 |

### Observability(`src/config.py:65-78`)

| 常量 | 默认 | 调整信号 |
|---|---|---|
| `LOOP_LAG_WARN_MS` | 500 | 见 Section 4.1 |
| `WATCHDOG_DEADMAN_TIMEOUT_MS` | 10000 | faulthandler dump 倒计时;偏保守,出现"长但合法的同步段"误触再加(目前主要风险已被 PR-1 覆盖);改前看 deadman 设计 `deadman.py:14-18` |
| `OBS_SAMPLE_INTERVAL_SEC` | 30 | sampler 周期;调小 = jsonl 增长加速 + 多一份 IO 自扰动观测者;调大 = 颗粒粗 |
| `OBS_LONG_TASK_AGE_SEC` | 60 | task 超此值进 `tasks_long_running`;按业务正常 turn 时长设 |
| `OBS_METRICS_LOG_PATH` | `data/observability/metrics.jsonl` | 必须在持久卷子目录(默认 `/app/data` 已在),容器重启 / autoheal 不丢 |
| `OBS_LOOP_LAG_LOG_PATH` | `data/observability/loop-lag.jsonl` | 同上 |
| `OBS_JSONL_MAX_MB` | 50 | 单文件大小上限,超即 rotate(`logging.handlers.RotatingFileHandler`);看实际占用 |
| `OBS_JSONL_BACKUP_COUNT` | 10 | 保留备份数 `.1 ~ .N`;默认 metrics ~600KB/天 × 50MB × 10 ≈ 800 天覆盖 |
| `OBS_MEM_LIMIT_MB` | 0 | RSS 高水位告警上界;0 = 自动 resolve(`sampler.py:330`),显式设需匹配 docker-compose `mem_limit`(避免重复 SoT) |
| `OBS_STDOUT_MIRROR` | `False` | 是否把 obs jsonl 镜像到 stdout;主通道是持久卷,默认关。**事故现场打开**作为 "持久卷未挂载 / 挂错路径" 的兜底通道(docker logs 拉得到),代价是污染主应用日志流。env 覆盖必须带前缀:`ARTIFACTFLOW_OBS_STDOUT_MIRROR=true`(裸 `OBS_STDOUT_MIRROR` 不生效) |

### Time convention(相关,非 obs 常量)

事件 `created_at` 全链路 naive UTC(`utils/time.utc_now`),`observability_report.py:121-125` 的 threshold 同 naive UTC——两边对齐,跨地域部署 / Shanghai (UTC+8) 不再有 8 小时偏差。详见 CLAUDE.md "Time convention" 段。

---

## 调参 SOP

1. **先看 24h 报告**——`python scripts/observability_report.py`
2. **定位异常 section** 而非常量(报告每个 section 告诉你哪个常量在被踩)
3. **改一个常量、改一倍以内**——`MAX_UNIQUE_CENTERS 50 → 80`,不要 50 → 200
4. **重启 backend、跑 24h、再看报告**
5. **保留前后两份报告 diff**——做小版本 ship 笔记的素材

不要试图通过 jsonl 直接接告警 / Prometheus——目前体系是 `jsonl + 报告 + 人工巡检`(决策见 incident fix-plan §待决策项 4)。Sampler 字段已就位,需要 exporter 时是平移工作。
