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
| P1 | PR-4a 应用内可观测性 | faulthandler / watchdog(自动栈 dump)/ 心跳快照日志 / 工具耗时日志 |
| P1 | PR-4c 取证工具 + 部署前置 | release 内置 py-spy / preflight 检查 / SOP |
| P3 | PR-4b metrics 端点 + 采集 | 可选,暂缓——4a 心跳日志已覆盖"自己看趋势"核心需求,需独立决策 |
| P1 | PR-3 fencing 事件持久化 | 修复 bug ④,恢复审计/回放完整性 |
| P2 | PR-5 前端镜像重建 | 正式消除 HOSTNAME 误配 |
| P3 | 文档 runbook | 固化"服务卡死"排查流程 |

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

**关联 bug ③**:bug ③(cancel 杀不掉同步 CPU)的根治不在本 PR——它由 PR-1 的"算法本身就有界"直接解决(最坏几毫秒级,根本不需要被中断)。本 PR 只负责"被取消时不丢事件"。建议在代码注释 / CLAUDE.md 里补一句架构约束:**同步 CPU 密集的工具会击穿引擎所有 cancel/timeout 机制,工具作者需自负成本纪律**。

---

## PR-4:可观测性基建(拆为 4a / 4b / 4c)

可观测性按"失效区间"分层设计——见 incident 文档同名小节。一句话定调:

> **4a + 4c 足够*诊断*一个已知的问题(出事后查原因),但不足以*提前发现*问题。** 这次 backend 卡 96 分钟、前端红 2 天都是被动撞见的,缺口正在"提前发现"。补这个缺口最便宜的不是上监控栈,而是 4a 里的"心跳快照日志"。

### PR-4a:应用内可观测性(P1,代码)

覆盖失效区间 ② + ③。

**日志分级原则**(明确写下来,避免出过事故就反射性开 DEBUG):

- **INFO(生产常驻)**:运维诊断必需的事件——工具 start/duration、关键参数尺寸、状态转移、慢操作 WARN 的对侧、watchdog 心跳。**这次缺的数据本来就该是 INFO,不该藏在 DEBUG 后面。**
- **DEBUG(仅排查时点亮)**:大体积 / 低频价值 / 含敏感数据的负载——完整 LLM messages dump、完整工具结果 body、内部 trace breadcrumb、压缩详情等。
- **WARNING / ERROR**:留给真正异常路径,不要因为"INFO 看着乱"把异常事件降级到 DEBUG。

> 心智模型:DEBUG 开关是调试时点的灯,不是"出过事故所以以后都开着"。生产 INFO 必须自带足够诊断信息——把 DEBUG 永久打开 = 用错工具应对真问题(给不到你最想要的,又把你不想要的一股脑塞过来)。

**落地内容**:

1. **`faulthandler` + `SIGUSR1`**:进程启动注册,`kill -USR1 <pid>` 即时 dump 所有线程 Python 栈。C 级信号处理器,主线程卡在纯 Python / C 调用里都能 dump。
2. **事件循环 watchdog(独立 OS 线程)**——做两件事:
   - 检测循环延迟超阈值 → **自动 dump 主线程栈到日志**(把"卡死瞬间"变成日志里的一份栈,无需人工介入)。可基于 `faulthandler.dump_traceback_later(timeout, repeat=True)` + 从事件循环周期性 reset 实现。
   - 周期性心跳快照日志(见下条)。
3. **心跳快照日志(INFO)**:watchdog 线程每 30~60s 打一行结构化日志,记录关键 gauge——事件循环延迟、在飞 execution 数及最长运行时长、DB 连接池占用、Redis 内存、进程 RSS。**这就是"极简、方便自己看"的 metrics**:无新依赖、无新基建,时间序列直接 `grep` 日志就有,creeping 型问题(慢泄漏、池慢慢满)靠它能看出斜率。
4. **工具执行 start/duration 日志(INFO)+ 慢操作 WARN**——每个 tool 打 start、结束打 duration,超阈值 WARNING。
5. **关键参数补打长度(INFO)**(`old_str` 等,内容可截断、长度必须有)。
6. **健康端点暴露内部状态**:`/health` 或新增 `/debug/status` 返回循环延迟、在飞任务数、池占用等。注意:循环卡死时该端点本身也会卡,所以它是必要不充分,真正的兜底是 #1 #2。
7. **项目内日志分级审计**——按上述原则扫一遍所有 `logger.*` 调用,典型反模式与方向:
   - **工具完成 / 外部调用结果**(如 `web_fetch.py:213/279` 抓取结果摘要、`artifact_ops` 各 Layer 命中)——属于"想知道发生了什么"的运维信息,**DEBUG → INFO**。
   - **状态转移 / 业务事件**(conversation 创建、artifact 版本变更等)——确认是 INFO,不是 DEBUG。
   - **大体积内容 dump**(`engine.py:732` 完整 messages、压缩详情、长 reasoning/response 预览)——**保持 DEBUG**,生产关闭。
   - **异常路径**——检查有无"应当 WARNING 但只在 DEBUG 打"的反例,以及"INFO 级别误打了 KB 级内容"。
   - 这一项**和 #1–#6 同一个 PR 做**:新增的 INFO 项要和存量 INFO 风格一致,分开做容易出现两套风格。

**范围**:`src/api/main.py`(启动钩子)、引擎工具执行路径、新增 watchdog 模块,+ 项目内 `logger.*` 审计涉及的零散文件(主要在 `src/core/`、`src/tools/`、`src/api/`)。

### PR-4b:真正的 metrics 端点 + 采集(P3,可选 / 暂缓,需决策)

`/metrics`(Prometheus 文本格式)+ 一个 Prometheus 容器(可选 Grafana)。给到 4a 心跳日志给不了的:像样的图、阈值告警、跨指标关联。

**当前判断:暂不做,标记"待决策"。** 理由:4a 的心跳快照日志已覆盖"自己看趋势"的核心需求;内网/离线环境没有现成采集端,自建 Prometheus 是个独立的"采纳一个工具"的决策,不该和热修绑在一起。等出现"4a 的 grep 日志不够用、需要图和告警"的明确信号再做。届时 4a 的 gauge 已定义好,加 `/metrics` 端点是平移工作。

### PR-4c:取证工具与部署前置(P1,运维/部署)

覆盖失效区间 ③ 的外部兜底。**核心心态:取证工具的可用性是部署时的保证,不是事故时的指望。**

1. **release bundle 内置 `py-spy` 静态二进制**(单文件几 MB),从根上消除"内网现抓不到"。
2. **preflight 检查**:`deploy/scripts/verify-bundle.sh`(或新增 `preflight.sh`)验证宿主机有 `gdb`/`gcore`、`strace`、`procps`。
3. **诊断策略定调**:app 镜像保持精简,取证走宿主机(不往镜像塞 `top`)。
4. **写进 SOP**:`docs/_archive/ops/deployment-sop.md` / `cloud-service-checklist.md` 加"取证工具就绪"检查项。
5. **runbook**:见下方文档项。

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
2. **运维**:是否引入 unhealthy → 自动重启(autoheal 容器 / 编排层)?这是独立于以上 PR 的韧性决策。
3. **PR-4b 是否要做**:4a 的心跳快照日志是"自己看趋势"的最低保证、随 4a 一起落地;`/metrics` + Prometheus + 阈值告警属独立的"采纳工具"决策,暂缓,等"grep 日志不够用"的明确信号。
4. **告警**:容器健康 / CPU 占满 / 事件循环延迟 / 探针失败的告警体系——本次所有问题都是被动发现。最低限度靠 4a 心跳日志 + 人工巡检兜住;成体系的告警依赖 PR-4b 的决策。

> **已决策**(留作可追溯):A vs B 选 B,基于本地 `logs/artifactflow.log` 实测 Layer 2 被触发(`枝/技` 单字符替换用例)。详见 PR-1 节"决策依据"。原 PR-1(封顶热修)和原 PR-2(分两步走)已合并为单一 PR-1。
