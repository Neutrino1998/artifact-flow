# 修复 PR 计划:2026-05-14 事件循环卡死事故

配套复盘文档:`incident-2026-05-14-eventloop-wedge.md`

本次事故定位到 **4 个 bug**(2 核心 + 2 衍生)、**1 个非我方问题**、**一批可观测性/运维缺口**。下面按 PR 拆分,标注范围、优先级、分支策略与待决策项。

> **分支策略**(见项目惯例):通用功能修复一律先进 `main`,再合并到内网分支。仅"前端 compose 临时覆盖"是内网特定的运维操作,不走 PR。

---

## 优先级总览

| 优先级 | PR | 状态 | 说明 |
|---|---|---|---|
| P0 | PR-1 Layer 2 改造为锚定 + RapidFuzz 校验 | ✅ 已完成 | 用有界算法替换 fuzzysearch,关掉病态输入炸 CPU 的可能、保留 Layer 2 救援能力 |
| P0(运维) | 前端 compose 临时覆盖 | — | 非 PR,内网机直接操作 |
| P1 | PR-obs-lite 轻量可观测性 + 日志持久化 | ✅ 已完成 | watchdog(自动栈 dump)+ sampler(jsonl 采样)+ `/admin/runtime` + 分析脚本 + 主日志迁持久卷 + obs jsonl 循环写;不动 DB schema、不上 Prometheus |
| P1 | PR-3 fencing 事件持久化 | ✅ 已完成 | 修复 bug ④,恢复审计/回放完整性 |
| P2 | PR-forensics-bundle 取证工具 + 部署前置 | ✅ 已完成 | 实质交付两件事:① py-spy 进 backend 镜像 + compose `cap_add: SYS_PTRACE`(faulthandler deadman 失效时的备份)② `release.sh --with-analyst-tools` 打 pandas/numpy 离线 wheels(给 analyst 机器零联网装)。`preflight.sh` 只验这两件事;Yama policy / host 深挖工具 由 cloud-service-checklist 第四段对齐云托管 |
| P2 | PR-5 前端镜像重建 | 待启动 | 正式消除 HOSTNAME 误配 |
| P3 | PR-tz-unify 事件时间戳时区统一 | ✅ 已完成 | 全链路 naive UTC:新增 `src/utils/time.utc_now()` + `frontend/src/lib/time.parseUtcIso`,后端写入路径所有 `datetime.now()` → `utc_now()`,前端 `new Date(<naive ISO>)` 一律改 `parseUtcIso` 锚定 UTC;observability_report.py 注释对齐,CLAUDE.md 增"时间约定"条目。Schema 不动、不迁数据 |
| P3 | 文档 PR(`docs/runbooks/service-hang.md` + `docs/guides/observability-tuning.md`) | 待启动 | 应急 runbook + obs 调参手册,纯 markdown PR;待 PR-obs-lite + PR-forensics-bundle 代码落地后启动(字段 / 路径稳定后下笔) |

**小版本迭代打包**:整套 fix plan 同一个 release bundle,分两批 ship:
- **第一批(已合 `main`)**:PR-1(算法根治)+ PR-3(fencing 事件持久化),走"立即修根因"的 release tag
- **第二批(部分已合 `main`)**:PR-obs-lite ✅ 已合;PR-forensics-bundle ✅ 已合;PR-tz-unify ✅ 已合;PR-5 + P3 文档 PR 仍待启动。同一个"事故后加固完整体"release tag,**一起 ship**——观测框架 / 取证工具 / 时区统一 / 前端镜像 / runbook + 调参手册,所有部件齐了才算闭环

PR 之间逻辑独立可分别回滚,但**默认一捆发**:避免 "obs-lite 上线后 tuning 手册还没写" / "runbook 提了 py-spy 但内网还没装" 这种部分到位的尴尬态。

---

## PR-1:Layer 2 改造为锚定 + RapidFuzz 校验(P0)✅ 已完成

**落地记录**(commits 在 `main`):
- `d295791` v6 算法 + 测试骨架(`fuzzysearch` → `rapidfuzz`,752 通过)
- `1cf6703` reviewer 反馈 ①:同 center 等距不同 span 的静默选择 → `bail_ambiguous`
- `b4c551a` reviewer 反馈 ②:`MAX_FUZZY_OLD_STR_LEN=10000` 硬上界 → `bail_budget`(实测 m≈400K 后 Step 1-3 Python 开销本身超 deadline)

下方 spec 保留原文(包含 v1→v6 演进、决策依据、5 轮 review)作为审计材料。`bail_size`/`FUZZY_OLD_STR_{MIN,MAX}_LEN` 在第二轮 review 后被合并到 `bail_budget` + `MAX_FUZZY_OLD_STR_LEN`,常量表与下方略有出入,以代码为准。

**目标**:用"先取稀有精确锚点收窄搜索窗口,再在小窗口内做有界 Levenshtein 校验"的算法替换现有 `fuzzysearch.find_near_matches`。把最坏成本从"文档熵的函数"变成"自选的常数",同时**保留** Layer 2 兜底能力。

**决策依据(为什么走 B 不走 A)**:本地测试日志 `logs/artifactflow.log` 显示 Layer 2 在正常使用中被触发(实测 7 次命中,均为典型"单字符替换"用例:`关于人工智能枝术` → `关于人工智能技术`)。这不是边缘情形,A 路线砍掉 Layer 2 会引入已知回归。B 既保留这个救援能力,又关掉病态输入炸 CPU 的可能。

**决策依据(为什么不分两步)**:这批修复打包发版,没有"当天热修"的紧迫性。原 PR-1 那套封顶代码会被原 PR-2 立刻删掉,**做两次纯浪费**;A/B 选择已基于真实日志数据,不再需要 instrumentation 来选。

**范围**(顺手把 update 整条链从 `artifact_ops.py` 里拆出来,延续 `grep_artifact.py` 的"一文件一工具"先例;`artifact_ops.py` 当前 1466 行,update 这块 Layer 0/1/2 + tool 占大头,拆完 ~600/~900 行更清爽):

**新文件**:
- `src/tools/builtin/update_artifact.py`(~600 行,自洽,只 import `ArtifactManager` 做类型):
  - `Span = Tuple[int, int]` 类型别名
  - `_nfkc_span_map` / `_normalize_for_match` Layer 1 归一化 helper(从 artifact_ops 搬出,确认无其它消费者)
  - `find_fuzzy_match(old_str, content) -> FuzzyResult` Layer 2 v6 算法
  - dataclass:`MatchInfo` / `FuzzyMatch` / `FuzzyBail`
  - `compute_update(content, old_str, new_str) -> MatchInfo` Layer 0/1/2 dispatcher(从 `ArtifactMemory` method 改 free function;artifact 数据模型不再夹带匹配算法逻辑)
  - `UpdateArtifactTool`(tool execute + Layer 1 对称 `<normalize_detail>` warning + Layer 2 `<fuzzy_detail>` 块组装 + `ToolResult.metadata.fuzzy_stats` 结构化产出,见下方"可观测产出"段)
- `tests/builtin/test_update_artifact.py`,分两类 test class,边界对齐两层关注点:
  - `TestAlgorithm` — 纯 `from update_artifact import compute_update, find_fuzzy_match`,**不构造 `ArtifactManager` / session**。覆盖 v6 spec 列的 8 条算法测试 + Layer 0/1/2 dispatch + 算法侧 `MatchInfo.fuzzy_stats` 字段完整性(成功 + 各 bail 路径)。这是算法 spec 的可执行投影
  - `TestToolBoundary` — 用最小 fake `ArtifactManager`(对齐 `artifact_ops.py:1111-1150` 的真实依赖形状),无 DB / session。**实测 `UpdateArtifactTool.execute()` 依赖三件**(写实,免得测试照搬错):
    - `current_session_id`:attribute / property,返回 str
    - `update_artifact(session_id, artifact_id, old_str, new_str) -> (success, message, match_info)`:async,`match_info` 即 fuzzy_stats 的载体
    - `get_artifact(session_id, artifact_id) -> ArtifactMemory | None`:async,只为渲染 XML 取 `current_version`,fake 返回带该属性的 stub 即可
    **只锁两件事**:`ToolResult.metadata["fuzzy_stats"]` 与底层算法返回的 fuzzy_stats 是同一对象(透传不丢字段 / 不改字段,断言用 `is` 或字段集相等);`<fuzzy_detail>` / `<normalize_detail>` 块按预期出现在 tool 文本输出里。算法正确性由 `TestAlgorithm` 兜,本组只看"算法 → tool → metadata 通路"。若实现时 tool 改了 manager 获取方式(如换成 DI 容器),本测试自然连带断,提示边界形状改了

**修改**:
- `src/tools/builtin/artifact_ops.py`:
  - 删 `Span` / `_nfkc_span_map` / `_normalize_for_match`(随 Layer 1 搬走)
  - 删 `ArtifactMemory.compute_update` method
  - 删 `UpdateArtifactTool` class
  - `create_artifact_tools()` 工厂局部 import `UpdateArtifactTool`(与 `GrepArtifactTool` 同样的循环依赖回避模式)
  - 文件顶部加注释说明历史多工具来源 + 拆分进度(grep / update 已拆,其余 create/read/rewrite 暂留)
- `src/tools/builtin/__init__.py`:`UpdateArtifactTool` import 行从 `.artifact_ops` 切到 `.update_artifact`(`__all__` 不变,对外 API 完全向后兼容)
- `src/config.py`:加 7 个隐藏常量(见下方常量表)
- `requirements.txt`:`+rapidfuzz`、`-fuzzysearch`

**依赖方向**(单向无循环):`update_artifact.py → artifact_ops.py`(仅为 `ArtifactManager` 类型);反向通过工厂局部 import,不进包级。

**算法**(五步,详细原理见 incident 文档同名小节;**当前为 v6 spec**,吸收了五轮内部 review 反馈,演进记录见本节末统一 "Spec evolution" 段):

**Preamble:推导有效参数**。`m = len(old_str)`:
```python
allowed_dist = min(FUZZY_MAX_L_DIST, max(1, int(m * FUZZY_MAX_RATIO)))   # ratio cap
L           = min(ANCHOR_SHINGLE_LEN, m // (allowed_dist + 1))           # n-gram 鸽巢硬约束
if L < ANCHOR_MIN_USABLE_LEN:
    bail("低熵 / 距离上限相对 m 过大")     # 没有可用锚点长度,直接响亮失败
```
- **`FUZZY_MAX_RATIO`(必须)**:cutoff 单靠绝对值不够。m=13 时单字符替换 Lev=1,但若 cutoff=16 则"任何 13 字邻近串"都过——退化为无锚点筛选。
- **鸽巢约束(必须)**:任意两个 Lev ≤ k 的串必共享一段长度 ≥ `⌊m/(k+1)⌋` 的子串。`L` 超过该数会让合法匹配漏召回(false negative);`L` 小到 < `ANCHOR_MIN_USABLE_LEN` 说明 m 太短或 k 相对太大,根本无法用 shingle 召回稳定锚点 → 当场 bail。

**Step 1 — 锚点候选构造**(单次扫描,O(m)):
对 `old_str` 滑窗切长度 `L` 的 shingle,记 `old_pos: dict[shingle, list[int]]`(同一 shingle 可能多个 `p`)。顺手按 `is_low_info_shingle` 过滤(纯空格 / `|` / `-` / 数字 / 标点等无信息片段——表格 / 模板的主要噪声源)。

> **Anchor 概念 vs. center 展开**(v5):
> - **没有"选 N 个 anchor"这一步**——鸽巢只保证"匹配子串含某个 shingle",不保证幸存 shingle 在 rarity / 任何排序里是前 N 名。任何按排序的 top-N gating 都会在"幸存 shingle 不在 top-N"时假阴性。
> - **center 生成 = 全部稀有 shingle × 全部 `p` × 全部 content 出现位置**(三重笛卡尔积,不靠"运气选对 shingle")。
> - 总成本由两道收口钉死:`MAX_UNIQUE_CENTERS` 静态 budget(Step 3 末)+ `MAX_FUZZY_WALL_CLOCK_MS` 动态 deadline(Step 4 内循环检查)。任一触发即响亮 bail。

**Step 2 — 锚点稀有度扫描**(单次扫描,O(n)):
对 `self.content` **重叠**滑窗扫一遍,只记录 `old_pos` 里的 shingle 位置,出现次数超 `ANCHOR_MAX_OCCURRENCES` 即把该 shingle 标 `common`、移出候选并停止追踪。这一步同时拿到了 Step 3 需要的位置索引。
```python
positions = defaultdict(list); common = set()
for i in range(n - L + 1):
    s = content[i:i+L]
    if s in old_pos and s not in common:
        positions[s].append(i)
        if len(positions[s]) > ANCHOR_MAX_OCCURRENCES:
            common.add(s); del positions[s]
```
**关键:不能用 `str.count()`**——非重叠计数会低估低熵串(`"aaaaaaaa".count("aaaa") == 2`,实际重叠位置更多),把"实际很常见"的 shingle 误判稀有 → 候选窗口爆炸。

筛后得到稀有 shingle 集合 `rare = {s : 1 <= len(positions[s]) <= ANCHOR_MAX_OCCURRENCES}`。

**合格 shingle 集为空 → 立即响亮失败**——这是核心安全机制:低熵 / 大幅漂移内容在进入字符级比对**之前**就被拒绝。

**Step 3 — Center 全展开 + 去重 + budget 钉死**:
对**所有**稀有 shingle 展开 `old_pos[s] × positions[s]` 的全部对齐 hypothesis(**没有 top-N 选择**,鸽巢不保证幸存 shingle 在任何排序里靠前)。产出 `unique_centers: list[int]`(只存 center_start,`center_end = center_start + m` 在 Step 4 派生,避免冗余):
```python
raw_centers: list[int] = []
for s in rare:
    for p in old_pos[s]:
        for q in positions[s]:
            raw_centers.append(q - p)
```
原始数 ≤ `sum_s(|old_pos[s]| × |positions[s]|) ≤ (m - L + 1) × ANCHOR_MAX_OCCURRENCES`,即 ~`m × ANCHOR_MAX_OCCURRENCES`(每个 pattern shingle 出现位置最多贡献 `ANCHOR_MAX_OCCURRENCES` 个 content 位置)。

**Center 去重**:按 `abs(center_i - center_j) <= allowed_dist` 合并,每组保留一个代表(任选,Step 4 会枚举 `±k` 偏移自动恢复细微差异)。真实场景中 unique_centers 在去重后通常 << 原始数(各 shingle 都会收敛到真实匹配位置附近)。

**Budget check(全局硬上界,Step 4 之前)**:
```python
unique_centers: list[int] = consolidate(raw_centers, tol=allowed_dist)
if len(unique_centers) > MAX_UNIQUE_CENTERS:
    bail("center budget exceeded:内容低熵或 pattern 重复度过高")
```
**不能截断**——截断会以不可预测方式漏召回(真实匹配的 center 可能恰好被截掉);bail 让模型提供更独特的上下文是唯一不猜的路径,符合"宁可响亮失败,不偷偷错"。Budget 把 Step 4 的总成本钉死在与输入无关的常数。

**Step 4 — 有界子串校验**(对每个去重后的 `(center_start, center_end)`):
**不是**整窗距离!整窗距离会把窗口外多取的 slack 字符算成编辑、又会把 slack 错当 matched_text 替换。正确做法是以锚点对齐为中心,枚举起止偏移 `(δ_start, δ_end) ∈ [-k, k]²`(k = `allowed_dist`)的子串、各算一次有界 Levenshtein,取最小的作为该 region 的最佳匹配:
```python
deadline = monotonic() + MAX_FUZZY_WALL_CLOCK_MS / 1000
for center_start in unique_centers:
    center_end = center_start + m              # 显式从 start 派生
    best = None
    for ds in range(-k, k+1):
        for de in range(-k, k+1):
            if monotonic() > deadline:        # ← deadline 检查必须在内层,
                bail("fuzzy deadline exceeded")  #    单 center 内 (2k+1)² 调用足以
                                                 #    把 500ms deadline 跑过头
            ms = max(0, center_start + ds); me = min(n, center_end + de)
            if me - ms <= 0:              continue
            if abs((me - ms) - m) > k:    continue   # 长度差已超 k,必不可能 ≤ k
            d = Levenshtein.distance(old_str, content[ms:me], score_cutoff=k)
            if d is not None and d <= k and (best is None or d < best[0]):
                best = (d, ms, me)
    if best: matches.append(best)
```

> **Deadline 触发语义**:bail 时**丢弃所有 partial 状态**——当前 center 的 `best`(可能尚未扫完全部偏移)、已累积的 `matches`(可能漏后续 center 里更优的命中)都不带出。理由:超时意味着我们不知道未跑的部分会不会推翻已有结论,任何"部分结果"都是不诚实的猜。直接 loud bail,模型重提更独特的 old_str,对齐与 budget bail 一致的失败语义。
关键产物:**真正的 `(match_start, match_end)`**——上层只替换这段,不再吃 slack。

> **RapidFuzz cutoff 契约(必须按此防御)**:`Levenshtein.distance(..., score_cutoff=k)` 在 `distance > k` 时返回 **`k + 1`**,**不是 `None`**。判断必须写 `d is not None and d <= k`,**不能只判 `is not None`**——否则所有超 cutoff 的候选都会进 matches、且按 `k+1` 排序,Step 5 去重也救不回(同一区域不同子串都被收下,best 选了个超 cutoff 的)。
>
> **长度预跳**:`abs((me-ms) - m) > k` 时 Lev 必 > k,一次比较换一次 RapidFuzz 调用 + 切片分配,净赚。
>
> **复杂度**:RapidFuzz Levenshtein worst-case `O(⌈n_window/64⌉ × m)`(Myers bit-parallel)。`score_cutoff` 提供 early bail,实践中接近 `O(k × max(m,n_window) / 64)`,但**不是算法上界保证**——spec 不该把它写成硬上界。

**Step 5 — Region 去重 + 唯一性判定**:
即使 Step 3 已经按 center_start 合并过,Step 4 的 `(2k+1)²` 偏移枚举仍可能让不同 center 收敛到几乎相同的最佳 `(ms, me)`(因为真实匹配前后的插入/删除会把不同锚点反算的 center 都拉到这里)。按 `(ms, me)` 区间二次合并:`abs(ms_i - ms_j) <= allowed_dist` 且 `abs(me_i - me_j) <= allowed_dist` 视为同一 region,保留 dist 最小者。

> **阈值用 `allowed_dist`(= k),不用 `L/2`**:L 可能小到 3、L/2 仅为 1;真实匹配前后只要有 1-2 个插入,不同锚点反算的 `(ms, me)` 就会差 ≥2 个字符。用 k 作阈值的语义是"两个 region 在它们各自的距离容忍内基本重合就是同一个",自洽。
```python
regions = consolidate_overlapping(matches)   # 按近似 start/end 去重
if len(regions) == 0:  return fail("no window matched")
if len(regions) >= 2:  return fail("ambiguous")
return regions[0]
```

**隐藏常量(`src/config.py`,按 CLAUDE.md 惯例)**:

| 常量 | 含义 | 建议起点 |
|---|---|---|
| `ANCHOR_SHINGLE_LEN` | shingle 切分长度(最终生效值受鸽巢约束) | 6 |
| `ANCHOR_MIN_USABLE_LEN` | 鸽巢推完的 `L` 低于此则当场 bail | 3 |
| `ANCHOR_MAX_OCCURRENCES` | shingle 在 content 内最多接受的出现次数(超即视为 common) | 20 |
| `MAX_UNIQUE_CENTERS` | Step 3 去重后 center 数上限,超即 bail | 50 |
| `MAX_FUZZY_WALL_CLOCK_MS` | Step 4 verify 总 wall-clock 上限,超即 bail | 500 |
| `FUZZY_MAX_L_DIST` | 校验编辑距离绝对上限 | 16 |
| `FUZZY_MAX_RATIO` | 校验编辑距离比例上限(取 min) | 0.10 |
| `FUZZY_OLD_STR_MIN_LEN` / `MAX_LEN` | fuzzy 层 old_str 长度上下限 | 8 / 3000 |

> 删除 `WINDOW_SLACK_RATIO`:Step 4 不再用 slack 取整窗,起止偏移直接由 `allowed_dist` 推。
> `FUZZY_OLD_STR_MIN_LEN` 从 16 → **8**:旧值 16 会把"证明 Layer 2 必需"的真实案例(`关于人工智能枝术…`,m=13)自己挡掉——spec 内部矛盾,需修正。

**失败路径 hint 设计**(统一回明确的下一步指引):
- `low entropy / large k` bail(Preamble) → "old_str 太短或与目标差异过大,请提供更长 / 更独特的上下文"
- `no rare anchor`(Step 2 bail) → "old_str 太重复或文档已大幅漂移,请重新 Read 后提供更独特的上下文,或改用 `rewrite_artifact`"
- `center budget exceeded`(Step 3 bail,超 `MAX_UNIQUE_CENTERS`) → "old_str 在文档中触发过多候选对齐,请提供更独特的上下文"
- `fuzzy deadline exceeded`(Step 4 bail,超 `MAX_FUZZY_WALL_CLOCK_MS`) → 同上(动态兜底,基本不应触发,触发即说明 budget 或 ratio cap 调高了 / 实现退化) — 同时打 WARN 日志,运维侧用 PR-obs-lite 监控
- `no window matched`(Step 4 全部超 cutoff) → 同 no rare anchor 提示
- `ambiguous`(Step 5 去重后 distinct region 数 ≥ 2)→ "old_str 在文档中有多个候选位置,请扩展上下文使其唯一"

> **唯一性对齐 Layer 0/1**:旧 fuzzysearch 实现里"多候选时静默挑距离最近"是个跟 Layer 0/1 不一致的设计漏洞——Layer 0/1 因为精确匹配里**没有合理的挑选依据**一直坚持 `count == 1`,Layer 2 没理由更松。新实现里**任何 ≥2 个 distinct region 都直接报错**(注意是 distinct region,不是 raw match——多锚召回同一区域不算多匹配),让模型显式澄清,不替它做隐式选择。这是本 PR 顺手堵掉的一个隐性 bug。

**最坏成本上界**(三段全有界):
```text
anchor scan      : O(n + m)                                       # Step 1+2 单次扫描
center generate  : O(m × ANCHOR_MAX_OCCURRENCES)                  # Step 3 raw centers,与文档长度无关
                   # 去重后 unique_centers ≤ MAX_UNIQUE_CENTERS,否则 bail
candidate verify : O(MAX_UNIQUE_CENTERS × (2k+1)² × ⌈m/64⌉ × m)   # Step 4 worst case
                   # ∧ wall-clock ≤ MAX_FUZZY_WALL_CLOCK_MS
                   # 单次 RapidFuzz 调用 O(⌈n_window/64⌉ × m),score_cutoff + 长度预跳
                   # 提供 early bail,但**非算法上界保证**;deadline 才是动态硬兜底
```
默认值代入:m=3000 / k=16 / `MAX_UNIQUE_CENTERS=50` / `(2k+1)²=1089` → verify 段 worst-case 约 5万次 RapidFuzz 调用,加 deadline 500ms 兜底,**两道独立收口**(budget 静态、deadline 动态),任一触发都 loud bail。**与文档长度只在 anchor scan 处线性相关**(O(n) 单次扫描,常数极小)。

> **两道收口的分工**:
> - `MAX_UNIQUE_CENTERS` 是**静态意图上界**——超限说明 anchor 区分度不够,任意截断都会以不可预测方式漏召回(真实 center 可能恰好被截掉),所以 bail 而非截断。
> - `MAX_FUZZY_WALL_CLOCK_MS` 是**动态实现兜底**——即使 budget 没触发,实测也可能超时(score_cutoff 不剪、长度预跳没触发等退化路径),wall-clock 是最后一道防线,保证再也不会回到 96 分钟。
> - 两道都触发即 loud bail,符合"宁可响亮失败,不偷偷错"原则,对齐 Layer 0/1 的严格语义。

**可观测产出**(无原文,只尺寸 / 计数 / bail 原因 / hash):

Layer 2 在**成功和所有 bail 路径**都写一份 `fuzzy_stats` 到 `ToolResult.metadata`,直接流过 `tool_complete.data.metadata` 进 `MessageEvent` JSON 列——**不开新 jsonl、不打新 INFO 行**,与现有 event 流复用,分析侧从 `tool_complete.data.metadata.fuzzy_stats` 取 JSON 对象即可(具体 SQL 见 PR-obs-lite §分析脚本,要点是用 `data->'metadata'` 拿 jsonb,**别走 `->>` text 提取**)。`engine.py` L686-695 已 emit 该字段,落库通路现成,本 PR 只填内容。

字段(无原文,但留 hash 便于跨事件去重 / 聚类):
```json
"fuzzy_stats": {
  "m": 1342, "n": 38501, "k": 16, "L": 6,
  "rare_shingles": 23, "raw_centers": 187, "unique_centers": 12,
  "verify_calls": 1452, "elapsed_ms": 47,
  "outcome": "matched",        // matched | bail_low_entropy | bail_no_anchor
                                //   | bail_budget | bail_deadline | bail_ambiguous
                                //   | bail_no_window
  "distance": 3, "similarity_pct": 99.8,  // 仅 outcome=matched 时有
  "old_str_hash": "sha256:..."  // 不存原文,跨事件 dedup / 聚类用
}
```

PR-obs-lite 的 `observability_report.py` 据此聚合:
- **bail outcome 分布** — 看 `bail_no_anchor` / `bail_budget` / `bail_deadline` 各自频率,判断哪个阈值是热点
- **`unique_centers` 直方图 vs `MAX_UNIQUE_CENTERS`** — 给 budget 调参依据;若 99% 调用 `< 20`,默认 50 偏宽,可收紧
- **`elapsed_ms` p99 vs `MAX_FUZZY_WALL_CLOCK_MS`** — 给 deadline 调参依据;触发即说明算法退化或 budget 调高了
- **`similarity_pct` 直方图**(matched 子集)— 看实际命中分布,辅助验证 `FUZZY_MAX_RATIO` 不过松

没有这套 stats,所有隐藏常量的调参都是猜。

> 自洽性:这是 PR-1 算法的一部分,不是 obs-lite 的"另外加"——bail 原因分布与 budget/deadline 触发情况,只有算法实现自己最清楚。塞进同一 PR 避免"算法上线了但调参盲飞"。

**顺带补:Layer 1 normalized 的对称 warning**(这次事故顺带翻出的设计不一致):

现状是 fuzzy 命中时 `UpdateArtifactTool.execute` 返回带 `fuzzy="X%"` + `<fuzzy_detail>` 的结构化提示,但 **Layer 1 归一化命中时只在消息字符串里说一句 `normalized match X%`、没有结构化 expected/matched 对比**——模型看不到 `Ⅳ → IV`、`café` 的 NFC/NFD 这类归一化具体改了什么。`compute_update` 的 Layer 1 分支其实已经在 `match_info` 里返回了 `expected_text` / `matched_text`,只是 tool 那层没拎出来用。

改动:把 `UpdateArtifactTool.execute` 里 `match_type == "fuzzy"` 那段的判断扩成 `match_type in ("fuzzy", "normalized")`,Layer 1 走对称的 `normalized="X%"` 属性 + `<normalize_detail>` 块。5–10 行,纯加,不动 Layer 1 算法本身。

> 注:更彻底的做法(Option B)是迁到 xml_parser 用的 `<parser_warnings>` 那条独立通道、ToolResult 加 `tool_warnings` 字段,涉及 formatter 和 ToolResult 数据结构。**本 PR 不做**,作为日后清理时跟"Layer 1 的 similarity 改名 / 含义对齐"一起处理。

**测试覆盖**:
- ✅ 单字符替换(测试日志里的 `枝/技` case,m=13)—— 走主路径;验证 `allowed_dist = min(16, max(1, 1)) = 1` 恰好放行 1 次替换
- ✅ **短 old_str + ratio cap**(m=13,人造一段 Lev=3 的偏差)—— 必须失败(ratio cap 把 cutoff 卡到 1,不能落到 16 上)
- ✅ **RapidFuzz cutoff 契约防御**(构造一段 Lev = k+2 的候选)—— 验证 `d is not None and d <= k` 判断真的把它拒掉,**单独构造测试用例确认 `d == k+1` 的返回行为**;若以后 RapidFuzz 改契约返回 None,该测试也会立刻失败提示。回归保险
- ✅ **Pattern 内重复 shingle 不漏召回**:某个稀有 shingle 在 old_str 内 3 个 `p`,真实匹配对应 `p2` —— centers 集合必须包含 `q - p2`,Step 4 在该 center 上 verify 成功
- ✅ **幸存 shingle 不在 rarity top-N 时仍能召回**(v4→v5 关键回归):构造一段 old_str,真实编辑发生在前半区,前半区 shingle 在 rarity 排序前 3 名;后半区 shingle 完好但 rarity 排名靠后。验证 v5 实现仍能召回(因为全展开,不再 top-N gating);反向断言 v4 实现在此 case 必假阴性
- ✅ **Center budget 超限触发 bail**(`MAX_UNIQUE_CENTERS`):构造高重复 content,让去重后 unique_centers > 50 → 必须返回 `center budget exceeded`,不能截断后继续
- ✅ **Wall-clock deadline 触发 bail**(`MAX_FUZZY_WALL_CLOCK_MS`):极端 case mock `Levenshtein.distance` 单次慢 ≥10ms,让总耗时超 500ms → 必须返回 `fuzzy deadline exceeded`,且**不返回任何已部分计算的 best match**(避免在超时的 cutoff 状态下静默接受半成品)
- ✅ 低熵 / 表格 / 纯模板 —— 走 Step 2 bail-out 或 Preamble bail(L 推到 < `ANCHOR_MIN_USABLE_LEN`),失败响亮
- ✅ 长 `old_str`(>1500 字符)—— 验证有界,verify 段实测 < 100ms
- ✅ **多锚召回同一区域**(构造一段 old_str,3 个稀有锚点都指向同一真实匹配位置)—— Step 5 去重后 distinct region 数 = 1,**应替换成功**,不能误报 ambiguous
- ✅ **Region 去重阈值与 k 对齐**(构造一段 Lev 偏移 ≈ k/2 的真实匹配,使多个锚点反算的 `(ms, me)` 差 2-3 字符)—— 用 k 阈值能合并、用 L/2 阈值会拆成两 region 误报 ambiguous(回归保险:防止以后改回旧阈值)
- ✅ Ambiguity case(distinct region ≥ 2)—— 失败带 hint(对齐 Layer 0/1 的严格唯一性)
- ✅ 完全漂移(`old_str` 字符级散乱漂移,没有 shingle 精确出现)—— Step 2 bail(诚实 trade-off:旧 fuzzysearch 有概率给一个不可靠的"匹配",B 选择失败而不是猜)
- ✅ Layer 1 normalized 命中(如 `Ⅳ ↔ IV`、`café` NFC/NFD)—— 返回值含 `normalized="X%"` 属性 + `<normalize_detail>` 块,跟 fuzzy 对称
- ✅ **`fuzzy_stats` 字段完整性(算法层,`TestAlgorithm`)**:成功路径 + 各 bail 路径(low_entropy / no_anchor / budget / deadline / ambiguous / no_window)各一例,直接调 `find_fuzzy_match` / `compute_update`,断言返回的 `MatchInfo.fuzzy_stats` 含全部字段、`outcome` 取值与实际触发分支一致、不含 `old_str` 原文(只 hash);`outcome=matched` 时 `distance` / `similarity_pct` 存在,其它分支不存在
- ✅ **`fuzzy_stats` metadata 透传契约(tool 层,`TestToolBoundary`)**:走 `UpdateArtifactTool.execute()`,fake manager 返回固定 content,断言 `ToolResult.metadata["fuzzy_stats"] is MatchInfo.fuzzy_stats`(同一对象 / 同字段);改算法字段时本测试自然连带断,锁死 tool 层"零加工透传"语义。这条单独测,因为算法层测不到 tool execute 链路

---

### Spec evolution(v1 → v6,五轮内部 review 收敛)

v1 spec(commit `7bf0db1` ~ `176adc9`)经五轮 review 演进到 v6,**算法骨架贯穿未变**(稀有 shingle 锚定 + 有界子串校验);共修 14 处问题,按时序:

| 版本 | 反馈 | 修法 | 严重度 |
|---|---|---|---|
| v1→v2 | Step 4 整窗距离把 slack 算成编辑、错当 matched_text | 锚点对齐中心 + `(2k+1)²` 偏移枚举,产出真正的 `(ms, me)` | P0 |
| v1→v2 | `FUZZY_MAX_L_DIST=16` 单独用,m=13 时退化为无 anchor 筛选 | 加 `FUZZY_MAX_RATIO=0.10`,`allowed_dist` 取 min | P0 |
| v1→v2 | `FUZZY_OLD_STR_MIN_LEN=16` 自打证明 case(m=13) | 改 8 | P1 spec 矛盾 |
| v1→v2 | Step 2 `str.count()` O(shingle_num × n) + 非重叠低估低熵串 | 单次重叠滑窗 + dict 索引,O(n+m) | P0 性能 + 正确性 |
| v1→v2 | Ambiguity 按 raw window 数判 → 多锚召回同区域误报 | Step 5 按区间去重再判 | P1 |
| v1→v2 | n-gram 鸽巢约束 `L ≤ m//(k+1)` 未写(adjustment 隐性"必须") | 加进 Preamble | P0 正确性硬约束 |
| v2→v3 | RapidFuzz `score_cutoff` 超阈返回 `k+1` 而非 None,`is not None` 放行全部 | `is not None and d <= k` 防御性写法 | **P0 直跑 bug** |
| v2→v3 | Anchor 定义含糊(同 shingle 多 `p` 未说取一个还是全部),bound 不闭合 | 明确 `(s,p)` 元组 + center 去重 | P1 |
| v2→v3 | Region 去重 `L/2` 过紧(L=3 时阈值=1) | 改 `<= allowed_dist` (= k) | P2 |
| v3→v4 | "选 ANCHOR_NUM 个 `(s,p)` 对"挤掉同 shingle 其他 p → pattern 重复时假阴性 | 改"选 shingle、Step 3 全 `p` 展开"+ `MAX_UNIQUE_CENTERS` budget | **P0 假阴性** |
| v4→v5 | "选 ANCHOR_NUM 个 shingle 按 rarity 排序"——鸽巢不保证幸存 shingle 在 top-N | 移除 ANCHOR_NUM,展开**所有**稀有 shingle + `MAX_UNIQUE_CENTERS=50` 静态 budget + `MAX_FUZZY_WALL_CLOCK_MS=500` 动态 deadline 双重收口 | **P0 假阴性** |
| v5→v6 | deadline 只在外层检查,单 center 内 `(2k+1)²=1089` 次 distance 调用足以跑过 500ms—— 与事故同类的"同步循环不让出"洞 | deadline 检查下沉到内层,触发时丢弃所有 partial 状态(当前 center 的 best + 已累积 matches)loud bail | **P0 同事故类问题** |
| v5→v6 | `unique_centers` 形状不一致:Step 3 append 标量 `int`,Step 4 解元组 `(start, end)` | 明确 `unique_centers: list[int]`(只存 start),`center_end = center_start + m` 在 Step 4 派生 | P2 clarity |
| v5→v6 | 待决策项残留 `ANCHOR_NUM`(v5 已从核心算法移除) | 换成 v5 的新常量 `MAX_UNIQUE_CENTERS` / `MAX_FUZZY_WALL_CLOCK_MS` | P3 housekeeping |

**贯穿四轮的关键 trade-off**:

1. **Loud bail vs 静默截断 / 静默选择**——Layer 0/1 严格 `count==1`,Layer 2 没理由更松。所有多候选场景(原 fuzzysearch 静默挑距离最近、`MAX_UNIQUE_CENTERS` 超限想截断、ambiguous distinct region ≥2)一律响亮失败让模型澄清,不替它做隐式选择。
2. **静态 budget + 动态 deadline 双重收口**——`MAX_UNIQUE_CENTERS` 是意图上界(超限说明 anchor 区分度不够,截断会以不可预测方式漏召回);`MAX_FUZZY_WALL_CLOCK_MS` 是实现兜底(score_cutoff / 长度预跳没剪到的退化路径)。两道独立,任一触发即 loud bail。
3. **召回完整性优先于性能优化**——v5 移除 ANCHOR_NUM 排序选择,不再依赖"幸存 shingle 大概率靠前"的统计直觉,而是展开全部候选 + budget 控成本。Fast-path(top-N 先试)是纯性能优化,有真实负载证据再加。

**fuzzysearch 依赖移除**(四轮不变):Step 2 用裸 dict + 滑窗,Step 4 用 rapidfuzz,顺带避开 fuzzysearch 0.8.1 已知 n-gram 鸽巢正确性 bug(本次事故诱因之一)。

**文件结构调整(v6 实施时确定,非算法变更不 bump 版本)**:update 整条链(Layer 0/1/2 + tool + Layer 1 归一化 helper + Layer 2 算法)从 `artifact_ops.py` 抽到 `src/tools/builtin/update_artifact.py`,延续 `grep_artifact.py` 一文件一工具先例。详见本节"范围"。**好处**:算法变更不再受 1466 行大杂烩文件拖累、测试可纯 `import find_fuzzy_match` 直接验证、`ArtifactMemory` 数据模型回归纯净(不再承担"什么算匹配"语义)。

**未做项(上线后按真实日志再决定)**:
- **动态 anchor_len**(ASCII / Markdown 长文本用 10–16 提精度):固定 6 + 鸽巢约束 + 低信息 shingle 过滤已能过所有已知 case。
- **分维度 cap**(`MAX_PATTERN_OCCURRENCES_PER_SHINGLE` 等):v5 单 budget 统一性更好,如某场景需分维度控制再加。
- **Top-N fast path**:见 trade-off #3。

---

## PR-3:lease fencing 事件持久化(P1)✅ 已完成

**落地记录**(commit 在 `main`):
- `119c813` `run_engine` 加 `except asyncio.CancelledError` 分支,直接调 `_persist_events` 写入累积事件 + `CANCELLED`(`reason="external_cancel"`)终态后再 re-raise;`stream_execute` finally 显式 `engine_task.cancel()` 把外层取消信号传到内层 task。新增 3 个集成测试覆盖:(1) 外部 cancel 时事件 + 终态都落库、(2) 内层 task 确实收到 cancel(防回归 bug ③ 同款症状)、(3) 持久化失败时不遮蔽 CancelledError。CLAUDE.md 补"Tool authors own CPU-cost discipline" + "Lease-fencing cancel persists events too" 两条架构约束。
- `3259330` reviewer 反馈 ①:`engine_task` 内的持久化只覆盖 `execute_loop` 仍在跑的窗口——若 cancel 落在 post-processing 阶段(`exists_async` / `flush_all` / `_persist_events` 期间),`engine_task.done()` 已为 True,`run_engine` 的 except 也已退出,事件仍丢。在 post-processing 外加 `except CancelledError` 边界,`events_persisted` 标志位提升到函数级,迟到 cancel 时幂等 late-persist(已有 terminal 保留不覆写,避免触碰 `batch_create` "全 duplicate 才 short-circuit" 的契约)。新增 `reason="external_cancel_post_processing"`。+ 2 个新测试。
- `1b91e17` reviewer 反馈 ②:引擎错误路径上 `run_engine except Exception` append ERROR 到 `initial_state["events"]` 但不赋 `final_state`,而 `final_state = initial_state` fallback 排在 `_on_engine_exit` 之后——cancel 命中 `_on_engine_exit` 时 late-handler 看到 `final_state is None` 跳过持久化。把 fallback 上移到 `_on_engine_exit` 之前根除竞态;late-handler 同步用 `final_state or initial_state` 防守。CLAUDE.md cancel-path bullet 重写为三条路径(cooperative / external-during-execute_loop / external-during-post-processing),含 batch_create 幂等契约说明。+ 1 个回归测试。
- `64408af` UI gating 发现:前端 `MessageList` 用 `node.response` 非空 gate 整个 `AssistantMessage`(事件流嵌套在内),原来 path 2/3 的 cancel 只写 events 不写 `Message.response`,前端整条 turn 隐形不可见。补:cooperative 写 `*Task cancelled by user*`,path 2/3 写 `*Task cancelled by system*`(operator 视角的细分原因继续走 events 的 `reason` 字段)。placeholder 放 `src/config.py`,按 cancel 来源 2 路分。
- `974409a` reviewer 反馈 ③:cancel handler 没检查 `_persist_events` 返回值,events 持久化失败时还是写了 `Message.response` placeholder,造成 "cancel-shown-but-events-missing"(UI 显示已取消,但下一轮 LLM 没有这一轮的历史)。绑定返回值到 boolean,gate response 写入。+ 2 个回归测试(engine_task + late handler 双路径)。
- `f936d5c` reviewer 反馈 ④:`response_updated` flag 设在 `await update_response_async` 之后 —— cancel mid-await race:DB 已 commit 但 Python 没走到 post-await 赋值,late handler 看到 attempted=False 又写一遍 placeholder 覆盖真实 response(reviewer 复现 `call_args_list == ['real engine output', '*Task cancelled by system*']`)。flag 上移到 await 之前,改名 `response_update_attempted`(语义:"已 claim 这个 slot,无论成败")。+ 1 个 race 复现测试。
- `bf316e1` 重构 phase-based post-processing ledger:同一类 bug 已经被反馈第 5 次(每轮一个新 phase edge case),根因是 success path 和 late-cancel handler 各自算 `(terminal_type, response)`,任一边漏一种 case 就漂移。新增 `src/core/post_processing.py`:`PostProcessState` dataclass(跨 await 状态显式化)+ `decide_terminal` / `ensure_terminal` / `choose_response_for_terminal` / `make_external_cancelled_event` 纯函数。`choose_response_for_terminal` 是单一真相源 —— success path / engine_task handler / late-cancel recovery 都过它,`(terminal_type, cancel_source) → display string` 在代码结构上不再有漂移面。新增 `_recover_from_late_cancel(pp)` 接管 late handler,phase 1 ensure_terminal+persist,phase 2 写 response,读 pp ledger 而不重算。+ 21 个 post_processing 纯函数矩阵测试 + 1 个 round-5 集成回归(cancel race during persist,COMPLETE terminal 必须保留真实 response,不能被 BY_SYSTEM 覆盖)。
- `2e5050d` reviewer 反馈 ⑤(refactor 后暴露的 pre-existing bug):`ensure_terminal` scan events 没区分 historical / current。多轮场景下 `state["events"]` 是 `[parent turns 的 historical events, 本轮 fresh events]` 的拼接,`_persist_events` 只写非 historical 段。late cancel 落在 `decide_terminal` 之前时,`ensure_terminal` 把父轮的 COMPLETE 误 adopt 成本轮 terminal,合成路径被跳过,过滤 historical 后本轮 batch 只有 `['llm_complete']` —— 本轮 DB 里没有任何 terminal,沉默失败。修法:filter `is_historical=False`,reverse 扫(同 turn 同时只有一个 terminal,reverse 是 defense-in-depth)。pre-PR-3 的 late handler 有同款 bug,被 refactor 抽出到独立小函数后 reviewer 一眼看出来。+ 2 个单元测试 + 1 个集成测试(reviewer 复现路径,断言本轮 batch 含 CANCELLED terminal)。
- `aa40120` CLAUDE.md cancel-path bullet 跟上代码:(a) 三条路径都写 `Message.response`(不再是"只有 cooperative path 写"),(b) 显式两条 cross-cutting invariant —— events-first(`Message.response` only written when `_persist_events` 返回 True)+ slot-claim-before-await(`pp.response_update_attempted` set 在 await 之前,defeat cancel-mid-await race),(c) path 3 改用 `_recover_from_late_cancel(pp)` 读 `PostProcessState` ledger、`ensure_terminal` 必须过滤 historical 这条约束。

总测试数:12 个 cancel-persist 集成测试 + 23 个 post_processing 纯函数单元测试 + 全套 789 passed / 26 skipped。

下方 spec 保留作为审计材料。落地与 spec 的关键差异:**修复点放在 controller(`run_engine` + `stream_execute` post-processing)而非 `_wrapped`**——两者语义等价(都是在"被 cancel 后但 await 仍可用"的独立 task 里跑持久化),但 controller 内更近 state、不需要跨 runner/chat/controller 三层拉契约。spec 提到的"在 `_wrapped` 捕获 CancelledError 时"由 stream_execute 的 finally `engine_task.cancel()` + 外层 `except CancelledError` 实现等效效果。落地中**实际暴露了三条 cancel 路径**(spec 只想到两条),三条都已覆盖。

**目标**:修复 bug ④,让被 fencing 取消的 turn 也持久化已产生的事件,恢复 "events persist unconditionally" 不变量。

**范围**:`src/api/services/execution_runner.py`(`_wrapped` 的 `CancelledError` 处理)、可能涉及 controller 的 `post_process` / 事件持久化路径。

**做法**:在 `_wrapped` 捕获 `CancelledError` 时,走一遍事件 batch write(类似 `error` 边界的处理),再重新抛出 / 收尾。需注意 `CancelledError` 不能被吞掉,持久化失败也要有兜底。

**关联 bug ③**:bug ③(cancel 杀不掉同步 CPU)的根治不在本 PR——它由 PR-1 的"算法本身就有界"直接解决(最坏几毫秒级,根本不需要被中断)。本 PR 只负责"被取消时不丢事件"。建议在代码注释 / CLAUDE.md 里补一句架构约束:**同步 CPU 密集的工具会击穿引擎所有 cancel/timeout 机制,工具作者需自负成本纪律**。下次 wedge 的发现路径由 PR-obs-lite 的两层 watchdog 承担——软退化由 Python 线程 watchdog 写 `data/observability/loop-lag.jsonl`(附 task 栈),硬 wedge(C 扩展持 GIL)由 `faulthandler` deadman switch 在 C 线程里 dump 栈到 stderr / docker logs。不指望 cancel 路径救场。

---

## PR-obs-lite:轻量可观测性 + 日志持久化(P1)✅ 已完成

**落地记录**(commits 在 `main`):

第一波(framework + 日志分级 + 截断标长度):
- `a1314dc` feat(obs): obs framework + 持久化路径(`src/observability/` 五件套 + lifespan wiring + admin endpoint + report script + 21 个测试,跟 PR-1 一样的 spec→骨架结构)
- `0561d59` chore(logging): 5 处 DEBUG → INFO(web_fetch / compaction trigger / batch_create 持久化边界 / task complete)
- `ce2f42e` fix(logging): 截断的 logger 输出必须报原始长度,对齐事故 bug ① 教训

第二波(reviewer 反馈):
- `e3cc524` reviewer 反馈 ①–④:DB URL 优先级倒过来对齐 `effective_database_url`、`tasks_long_running` 经 ExecutionRunner `_task_started_at` 真接通、RSS WARN 接 cgroup v2/v1 + `OBS_MEM_LIMIT_MB` env override、data_dir 扫描走 `asyncio.to_thread`(+ pandas 决策按 release bundle 走 + 一批 docstring 引用密度清理)
- `9693228` reviewer 反馈 ⑤:脚本走 `AsyncEngine.run_sync` 桥接复用 app 的 async driver,不再 +asyncpg → +psycopg2 翻译,消除 sync driver 依赖
- `ec6c5f4` reviewer 反馈 ⑥:脚本改 ORM `select(MessageEvent)` + `AsyncSession`,删 `_build_sql` 三方言分支 + SQLite metadata 二次 parse hack,MySQL 自动 supported

第三波(收尾):
- `b303d48` reviewer 反馈 ⑦(TZ 偏差):热路径 `datetime.now()` 本地朴素 vs `server_default=func.now()` UTC 不一致,Shanghai 部署偏 8h;在 fix plan 立 P3 follow-up PR(PR-tz-unify),不在本 PR 修
- `8fb0f3e` reviewer minor:顶部 docstring 跟 ORM 实现对齐
- `8c19437` reviewer 收尾:`_print_*_summary` 接 `hours` 参数(原写死 24h 跟 `--hours N` 不一致)+ `_resolve_engine_url` docstring 跟 PR-forensics-bundle 的 sync-driver 描述同步刷新

总计 9 个 commit,full suite 830 / 26 → 留下 1 个 follow-up(PR-tz-unify P3)。

**已知未修(留给 PR-tz-unify)**:`observability_report.py` 时间窗 threshold 用 naive UTC,非 UTC 部署偏 TZ 量;Shanghai 用 `--hours N+8` 兜,直到 PR-tz-unify 落地。详见本文档 PR-tz-unify 章节。

下方 spec 保留原文(三件事合一的决策依据、组件分工、注入点、隐藏常量表)作为审计材料。落地与 spec 的关键差异:
- `tasks_long_running` 字段从"sampler 暂返 0,follow-up 接通"被 reviewer 推到本 PR 内接通(`e3cc524`),`ExecutionRunner` 加了 `_task_started_at` dict + `long_running_count(threshold)` 方法
- `mem_limit_bytes` 从"sampler 接口暴露但 lifespan 不传"被 reviewer 推到本 PR 内接通(`e3cc524`),走 env > cgroup v2 > cgroup v1 > None 的解析链
- 报告脚本从"raw SQL + sync driver 翻译"两轮迭代到 ORM `select(MessageEvent)`,反而比 spec 更干净 —— spec 写的是"用 JSON 表达式聚合",ORM 让方言分支整个消失

**目标**(三件事合一,同一个事故后加固 release):
1. 用最小代价补齐 incident 暴露的可观测性缺口(A1–6 / B1–7),让下次事故 30 秒内有栈可看、试运行期间能用一个 Python 脚本跑出资源使用报告
2. **主应用日志从容器 fs `logs/` 迁到持久卷 `data/logs/`**,事故重启不丢(本次事故重启时丢日志的直接修复)
3. **obs jsonl 走和主日志同款 `RotatingFileHandler` 大小循环**,避免长期跑下来无界增长

**合 PR 而非拆三个**:三件事共享同一个"持久卷子目录"决策、同一个 `src/utils/logger.py` 模块改动、同一批测试 fixture 路径替换;互相逻辑独立无依赖,但 review 心智 + 夹具修改一次更省,且 rollback 粒度一致(整个"事故后加固" release 一起回滚)。**注意:不是挂新卷**——沿用 `artifactflow_data:/app/data` 现成卷,只把 `log_dir` 默认从 `logs` 改成 `data/logs`,跟 obs jsonl 同款做法,零 compose 改动。

**约束(明确写下,避免范围漂移)**:
- ❌ 不动 DB schema(沿用现有 `MessageEvent.data` JSON 列;按需用 JSON 表达式聚合——标量字段走 `data->>`,嵌套对象走 `data->...->'key'` 保 jsonb 给 `pd.json_normalize`)
- ❌ 不上 Prometheus / Grafana / OTel
- ❌ 不拆 cache token(自家模型,不涉及差价计费)
- ❌ 不加新 docker volume,不改 compose
- ✅ 业务侧观测复用已有 event 流,运行时 / 系统侧观测落 jsonl
- ✅ 主日志 + obs jsonl 都落在 `artifactflow_data:/app/data` 现成持久卷下
- ✅ 产出形态满足"裸 Python 脚本 + pandas 跑得出报告"

### 持久化与循环写策略

**唯一原则:所有需要事故后查阅的数据都落在 `artifactflow_data:/app/data` 现成持久卷子目录下,不挂新卷、不改 compose。**

背景:`deploy/docker-compose.intranet.yml` L28 只挂 `artifactflow_data:/app/data`,**未挂 `/app/logs`**。当前主日志写 `logs/` 在容器 fs → autoheal 重启 / `up -d --force-recreate` / 升级时蒸发,**本次事故重启时就是这样丢了完整 INFO 日志**。obs 数据消费场景几乎全在事故后,同款问题。修法:全部走现成 `/app/data/` 子目录,跟 SQLite (`data/artifactflow.db`) 在同一个卷里。

| 数据 | 路径 | 循环策略 | 总占用上限 |
|---|---|---|---|
| 主应用日志 | `data/logs/artifactflow.log` | 10MB × 5(沿用现有 `RotatingFileHandler` 配置,不变) | 60 MB |
| 主错误日志 | `data/logs/artifactflow_error.log` | 5MB × 3(同上) | 20 MB |
| obs 周期采样 | `data/observability/metrics.jsonl` | `OBS_JSONL_MAX_MB` × `OBS_JSONL_BACKUP_COUNT` | 默认 50MB × 10 = 500 MB |
| obs loop lag | `data/observability/loop-lag.jsonl` | 同上(实际稀有,远不到上限) | 同上 |

**主日志迁路径**:`src/utils/logger.py` L141 默认 `log_dir="logs"` → `"data/logs"`。已有的 `RotatingFileHandler(maxBytes=10MB, backupCount=5)` 配置完全不动,只换默认路径。环境变量 / 显式参数仍可覆盖(开发本机习惯 `logs/` 不受影响)。`Logger.__init__` 里的 `Path(log_dir).mkdir(parents=True, exist_ok=True)` 已经能处理多级目录,免代码改动。

**obs jsonl 循环写**:新增 `src/observability/jsonl_sink.py`,用 stdlib `RotatingFileHandler` 包装(formatter 仅 `%(message)s` 透传,不加时间戳前缀污染 jsonl "一行一对象"契约)。sampler / watchdog 两侧的写入都用同一个 `JsonlSink`,不直接 `open()` 文件 handle:

```python
from logging.handlers import RotatingFileHandler

class JsonlSink:
    def __init__(self, path: Path, max_mb: int, backups: int):
        self._h = RotatingFileHandler(
            path, maxBytes=max_mb * 1024 * 1024,
            backupCount=backups, encoding='utf-8'
        )
        self._h.setFormatter(logging.Formatter('%(message)s'))
        self._log = logging.getLogger(f"obs.{path.name}")
        self._log.handlers = [self._h]
        self._log.propagate = False    # 不污染根 logger 的 stdout/file handler

    def write(self, obj: dict) -> None:
        try:
            self._log.info(json.dumps(obj, ensure_ascii=False))
        except Exception:
            pass    # observer 不能拖累 observee
```

约束:
- **默认值**:`OBS_JSONL_MAX_MB=50` / `OBS_JSONL_BACKUP_COUNT=10`。metrics.jsonl ~600 KB/天 → 单切片覆盖 ~80 天 / 总覆盖 ~800 天;loop-lag.jsonl 事件驱动,远低于此
- **单进程同写约束**:ArtifactFlow backend 单进程,`RotatingFileHandler` 内置锁就够;**若日后切多 worker / 多进程模式,本 sink 需重新设计**(rotate 瞬间互相覆盖,文档里写一句即可)
- **分析脚本侧 glob 所有切片**:`pd.concat([pd.read_json(f, lines=True) for f in sorted(glob('data/observability/metrics.jsonl*'))])`
- **stdout mirror 不走 RotatingFileHandler**:stdout 那条是 `docker logs` 兜底通道,Docker 自己有 `log-opts max-size/max-file` 容器侧轮转,独立配置,不在本 PR 范围

### 数据源(三处,零 schema 变更)

| 来源 | 内容 | 状态 |
|---|---|---|
| `MessageEvent` 表(JSON 列) | LLM/工具调用:`model`、`token_usage`、`duration_ms`、`tool`、`params`、`success` — 均已在 `llm_complete` / `tool_complete` payload | ✅ 已有 |
| `data/observability/metrics.jsonl` | 周期采样:loop_lag p50/p99、process RSS/CPU/FD、DB pool、Redis used、in-flight 数及最长任务 age | ➕ 新增(持久卷) |
| `data/observability/loop-lag.jsonl` | 事件驱动:loop_lag 超阈值时一条记录,带 `asyncio.all_tasks()` 各任务栈截断 | ➕ 新增(持久卷) |
| stderr / `docker logs backend` | 硬 wedge dump:deadman switch 超时(GIL 被 C 扩展攥死也能出)+ jsonl 同步 mirror | ➕ 新增(二级兜底通道) |

> 复核结论:`engine.py` 已在 L344-378 emit `llm_complete`(含 `model` / `token_usage` / `duration_ms`)、L686-695 emit `tool_complete`(含 `tool` / `params` / `duration_ms` / `success`)。MessageEvent 业务侧不缺字段,**不需要加 event payload**,只需在分析侧用 JSON 表达式取出。

### 注入点(四个组件,共 `src/observability/`)

> **两层 wedge 检测的角色分工**(避开本次事故的 GIL 失效模式):
> - 组件 #1(Python 线程 watchdog)**软观测**——loop_lag 采样、附 task 栈,覆盖"loop 调度有 await 但被拖慢"。**事故的本次形态(C 扩展持 GIL 不释放)会让本线程跟其它 Python 线程一起 `futex_wait`,不能作为硬兜底**。
> - 组件 #2(`faulthandler` deadman switch)**硬兜底**——C 线程定时器,dump 路径不需要 GIL,GIL 被攥时也能出栈。专门覆盖本次同类硬 wedge,bug 出现时栈直接进 docker logs。
> 两者目的不同,都留;不要因为组件 #2 存在就删 #1(后者承担可统计的 loop_lag 分布,前者只在硬 wedge 时 dump 一次)。

1. **`watchdog.py` — 事件循环 lag 软观测(Python 线程)**
   - `threading.Thread(daemon=True)`,FastAPI lifespan 启动
   - 每 1s 通过 `loop.call_soon_threadsafe` 投时间戳回调,测投递→执行延迟
   - 滚动窗口存 p50 / p99 / 1 分钟 max(供 `/admin/runtime` 端点读)
   - 超 `LOOP_LAG_WARN_MS`(默认 500ms)→ 写一行到 `data/observability/loop-lag.jsonl`,含 `asyncio.all_tasks()` 每个 task 的 `get_stack()` 前 N 帧(截断)
   - **失败必须吞**(observer 不能拖累 observee)。**不在 asyncio task 里**——循环卡死时它自己也会被卡
   - **失效面写明**:CPython GIL 由 C 扩展持续持有(本次事故就是)→ Python 线程全部 `futex_wait`,本组件**与其它 Python 线程一起睡死**,产不出数据。该场景由组件 #2 兜。

2. **`deadman.py` — `faulthandler` deadman switch(硬 wedge 兜底)**
   - `faulthandler.dump_traceback_later(timeout, repeat=False, file=sys.stderr)`:CPython 用 `PyThread_start_new_thread` 起的**纯 C 线程**,内部 `sem_timedwait` + `_Py_DumpTraceback()`——**dump 路径不获取 GIL**,专为 "interpreter hung" 场景设计
   - 用法 = **deadman switch**(不是无脑 `repeat=True` 定时 dump):一个独立 asyncio task 周期 reset 定时器
     ```python
     async def _heartbeat():
         interval = WATCHDOG_DEADMAN_TIMEOUT_MS / 2 / 1000  # 余量 reset
         while True:
             faulthandler.cancel_dump_traceback_later()
             faulthandler.dump_traceback_later(
                 WATCHDOG_DEADMAN_TIMEOUT_MS / 1000, repeat=False)
             await asyncio.sleep(interval)
     ```
   - loop 健康:heartbeat 持续 reset → 永远在 timeout 前被 cancel → **不产生噪声 dump**
   - loop wedge(无论是 await-less 同步 CPU、还是 C 扩展持 GIL,还是死锁):heartbeat 跑不到 → C 线程到点 dump 全部线程的 Python 栈到 stderr → `docker logs backend` 拉得到
   - **失败必须吞**(faulthandler 本身极轻,但 enable 失败必须 swallow + WARN,不挂应用启动)
   - **常量**:`WATCHDOG_DEADMAN_TIMEOUT_MS` 默认 10000(10s);明显大于正常工具最长耗时 + 单次 LLM 调用,小于事故 96 分钟若干个数量级

3. **`sampler.py` — 周期采样(asyncio task)**
   - `OBS_SAMPLE_INTERVAL_SEC`(默认 30s)采一次,写一行 JSON 到 `data/observability/metrics.jsonl`,字段示例:
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

4. **`admin_runtime.py` — `GET /admin/runtime` 端点(半活状态诊断)**
   - `require_admin`,返回 sampler 最近快照 + 现拉的 `RuntimeStore.list_active()`
   - **定位:服务还活但变慢 / 资源逼近上限**——pool 即将耗尽、Redis 接近 maxmemory、有长跑任务、loop_lag 在抬升但还能调度。这类 "走慢了但还回得来" 状态下用它看实时水位。
   - **不是硬 wedge 第一入口**——本身就是 FastAPI 协程端点,事件循环卡死它跟 `/health/live` 一样无响应(本次事故已证)。硬 wedge 的第一入口是组件 #2 的 stderr dump + 外部 docker healthcheck 状态 + `kill -USR1 <pid>` 手动 dump(详见 `docs/runbooks/service-hang.md`),**全在 Python 解释器之外**。

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
df_tool    = pd.read_sql("""
    SELECT created_at,
           data->>'tool' AS tool,
           (data->>'duration_ms')::int AS dur_ms,
           (data->>'success')::bool   AS success,
           data->'metadata'           AS metadata   -- JSON 对象,保留供 fuzzy_stats 抽取
    FROM message_events
    WHERE event_type='tool_complete' AND created_at > now() - interval '24 hours'
""", engine)

# fuzzy_stats 子表:只取 update_artifact + metadata 含 fuzzy_stats 的行
df_fuzzy = pd.json_normalize(
    df_tool[df_tool["tool"] == "update_artifact"]
           ["metadata"]
           .dropna()
           .apply(lambda m: m.get("fuzzy_stats"))
           .dropna()
)   # 展开 m/n/k/L/rare_shingles/.../outcome/distance/similarity_pct 等顶层字段

df_runtime = pd.read_json("data/observability/metrics.jsonl",  lines=True)
df_lag     = pd.read_json("data/observability/loop-lag.jsonl", lines=True)
```

> SQL 用 `data->'metadata'`(jsonb,不是 `->>` text)是关键——`pd.json_normalize` 需要的是 JSON 对象,`->>` 会把整个 metadata 序列化成 text 后再要二次 `json.loads`,多绕一步且失类型。

报告内容:
- LLM 调用按 model / agent 聚合(次数、token 总量、p99 latency)
- 工具调用按 name 聚合(p50 / p99 / max latency、失败率)
- **`update_artifact` fuzzy_stats 调参报表**(PR-1 承诺产物,本节兑现):
  - `outcome` 分布:`matched` vs 各 `bail_*` 频率 → 看哪个阈值是热点
  - `unique_centers` 直方图 vs `MAX_UNIQUE_CENTERS` 默认 50 → 99% < 20 即可收紧
  - `elapsed_ms` p50 / p99 / max vs `MAX_FUZZY_WALL_CLOCK_MS` 默认 500ms → 接近即说明算法退化
  - `similarity_pct` 直方图(`outcome=matched` 子集)→ 验证 `FUZZY_MAX_RATIO` 不过松
  - `old_str_hash` 频次 top-N → 重复触发的同一 pattern,看 prompt 是否教得不好导致模型反复写错
- 24h loop lag 分布(中位 / p99 / max,看有没有逼近阈值)—— **仅软退化**;硬 wedge(GIL 持久攥死)看下方 deadman dump
- **硬 wedge dump 入口**:`docker logs backend 2>&1 | grep -A 200 "Thread 0x"`(faulthandler dump 标志),或 stderr 直接 tail。不在本脚本聚合(事件性质 / 频率 / 数据形态都不同),报表只指路
- 24h RSS / CPU / FD / DB pool / Redis 时序图(matplotlib 几张图,可选)
- 触发的 loop-lag 事件列表(**软退化诊断入口**,带 task 栈截断;不覆盖硬 wedge)

### 隐藏常量(`src/config.py`)

| 常量 | 含义 | 建议起点 |
|---|---|---|
| `LOOP_LAG_WARN_MS` | 组件 #1 watchdog 写 loop-lag.jsonl 阈值 | 500 |
| `WATCHDOG_DEADMAN_TIMEOUT_MS` | 组件 #2 deadman switch 触发 dump 的超时(heartbeat 不来即 dump) | 10000 |
| `OBS_SAMPLE_INTERVAL_SEC` | sampler 周期 | 30 |
| `OBS_LONG_TASK_AGE_SEC` | "长时间运行任务"门槛 | 60 |
| `OBS_METRICS_LOG_PATH` | 周期采样 jsonl 路径(**必须在持久卷内**) | `data/observability/metrics.jsonl` |
| `OBS_LOOP_LAG_LOG_PATH` | loop-lag 事件 jsonl 路径(**必须在持久卷内**) | `data/observability/loop-lag.jsonl` |
| `OBS_JSONL_MAX_MB` | obs jsonl 单文件大小上限,超即 rotate | 50 |
| `OBS_JSONL_BACKUP_COUNT` | obs jsonl 保留备份数(`.1` ~ `.N`) | 10 |

> 路径策略详见上方"持久化与循环写策略"小节。两个 sink 都额外 mirror 一份到 stdout(JSON 单行),走 `docker logs backend` 兜底通道,即便卷意外丢也还有一条命。Stdout mirror 由实现层处理,不进常量。

### 新依赖

- `psutil`(进程 RSS / CPU% / FD / disk usage,跨平台)。`requirements.txt` 加一行。
- 内网 release bundle 需带上对应 wheel(走 vendor / offline 安装)。

### `faulthandler` 注册(运维兜底)

`src/api/main.py` lifespan 早期(组件 #2 启动前):
- `faulthandler.enable()` 把 SIGSEGV 等致命信号的栈打到 stderr
- `faulthandler.register(signal.SIGUSR1)` 保留 `kill -USR1 <pid>` 手动 dump(独立于组件 #2 的 deadman 自动,故障现场可手动追加)

> 组件 #2 的 `dump_traceback_later` 本质上是 `faulthandler` 的另一个用法,但与上述两项目标不同(deadman 是周期 reset 的自动兜底,这里是面向 crash signal + 手动 SIGUSR1)。三件事都依赖 `faulthandler.enable()` 提前执行。

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

- **`src/observability/`**(新目录):`watchdog.py` / `deadman.py` / `sampler.py` / `admin_runtime.py` / `jsonl_sink.py`
- **`src/api/main.py`**:lifespan 注册组件 #1–#3 + `faulthandler.enable()` + 路由
- **`src/utils/logger.py`**:默认 `log_dir` `"logs"` → `"data/logs"`(单行改动,`RotatingFileHandler` 配置不动)
- **`src/config.py`**:新增 obs / watchdog / jsonl 相关常量
- **`requirements.txt`**:`+psutil`
- **`scripts/observability_report.py`**(新):pandas 报告脚本
- **测试 fixture**:扫一遍硬编码 `logs/` 路径的位置同步更新
- **`logger.*` 审计**:项目内调用按 INFO / DEBUG / WARNING 分级原则调整,涉及零散 `src/core/` / `src/tools/` / `src/api/` 文件

### 与 CLAUDE.md 不变量的兼容性

- **Event sourcing**:采样数据**不**进 `MessageEvent`(避免污染 history;EventHistory 不该看到这些)
- **Three-layer**:watchdog / sampler 是 infra,装在 `src/observability/`,不进 Repo/Manager;`/admin/runtime` 走标准 Router → 新 `RuntimeInspector` Manager
- **参数最小化**:阈值常量全进 `src/config.py`,不暴露给模型、不暴露 API
- **Loud failure**:逼近上限走 WARN 日志,不做自动 LRU / 驱逐 / 降级

### 撤销 / 移走的项

- **原 PR-4b**(`/metrics` + Prometheus + Grafana)→ **撤销**,挪到 P3 backlog。等出现"jsonl + 脚本不够用、需要图和告警"的明确信号再做;届时 sampler 字段已就位,加 Prometheus exporter 是平移工作
- **原 PR-4c 的取证工具 / preflight**(py-spy bundle 等)→ **拆为单独 PR**(见下方 PR-forensics-bundle)

---

## PR-forensics-bundle:取证工具与部署前置(P2)✅ 已完成

**落地记录**(commits 在 `main`,七轮收敛 = reviewer×5 + 用户主导 step-back×2):

第一轮(initial 落地):
- `c606187` 整 PR 一并 commit(5 个文件 + 430 净增行):`scripts/release.sh` 加 `--with-forensics` 开关产出 `artifactflow-forensics-<slug>.tar.gz`;构建机下载 py-spy + pandas/numpy 离线 wheels;`deploy/scripts/preflight.sh` 新增两层离线检查;deployment-sop / cloud-service-checklist 增"取证就绪"
- `80117a4` fix plan 回填本 PR 落地 SHA

第二轮(reviewer 反馈 4 条):
- `dee0763` 修 reviewer 反馈:① py-spy GitHub Releases URL 是 404(asset 根本不存在),改走 `pip download py-spy==<ver> --no-deps` 拉 PyPI wheel,unzip 提取 `.data/scripts/py-spy` 二进制;② SHA 口径不一致(release 哈希 tarball、preflight 哈希 binary)→ 统一到**binary** SHA,release 写 `forensics/bin/py-spy.sha256`(sha256sum 标准格式),preflight 走 `sha256sum -c` 同文件零漂移;③ pandas/numpy 未 pin → 顶层加 `PANDAS_VERSION` / `NUMPY_VERSION` + 下载后 `ls *.whl | sort > forensics/wheels.lock.txt` 记录解析全套;④ ptrace_scope 描述偏乐观(把 Yama mode 1 写成"同 UID 即可",实际是 descendant only)→ cloud-service-checklist 重写四模式表 + 当时推荐了 `docker exec backend + cap_add` 路径

第三轮(reviewer round 2,3 条):
- `27d13ee` 修 reviewer round 2 反馈(三条都是落地与文档措辞不一致):① 撤销"`docker exec backend py-spy + cap_add`"虚假推荐(当前 bundle 不动镜像/compose 时这条路径不可执行) → 回归宿主机 attach + 依赖云托管 ptrace_scope 协调,**并重定位 py-spy 为 PR-obs-lite faulthandler deadman 失效时的备份**;② slug `pyspy<v>-py<v>` 加上 `pandas<v>-numpy<v>`(NECESSARY 校验) + 文档明确 wheels.lock.txt 才是 SUFFICIENT;③ fix plan 落地记录补 dee0763 + 重定位

第四轮(reviewer round 3,3 条 + 用户主导的架构纠偏):
- **用户主导**: 反复 push "是不是只有 py-spy / gdb 不是在容器里跑吗 / 为啥不一开始打进去 / 打进镜像是不是反而配置更简单",三次戳穿 round 2 那个"装宿主机 + 看云托管脸色"的中间态 —— 把不确定性外推到云托管协调上,真出事故时备份路径可能不可用。**最终走向**: py-spy 进 backend 镜像 + compose `cap_add: [SYS_PTRACE]`,前两层(主路径 faulthandler + 备份 docker exec py-spy)零云托管依赖。这不是 round 2 想避免的"反射性扩张",而是**精准扩张** —— 只装 py-spy(第三方分发 + 事故现场最常用)、不装 gdb/strace/top(OS 包 + 频次低 + 全局视图);+6MB 镜像换运维心智模型大幅简化(SOP/checklist/preflight 整体 -176 行)
- **P2 反馈**: round 2 后 reviewer 戳穿 preflight 把 host gdb/gcore/strace/ps/top 全当 hard failure → 跟"主路径 faulthandler 不依赖 host 工具"新模型冲突,可能阻塞"云托管不允许装深挖工具但部署完全 OK"的合法场景。**采纳**: preflight 分 required(bundle 完整性 + 容器内 py-spy) / optional(host 深挖工具 warning) 两段,exit 只看 required;output 用 ✓/✗/⚠ 三色分明
- **P3 反馈**(注释 stale): release.sh 旧注释还说"content-addressed by py-spy + Python version" → py-spy 出镜像 + bundle 改名后这段注释整段重写,语义跟代码同步
- **P3 反馈**(行尾空格): fix plan 633 行 trailing whitespace → 清理

第五轮(reviewer round 4,P1×2 + P2 + P3 半真):
- **P1 #1**(optional `--with-analyst-tools` vs preflight hard-fail 不一致): SOP/release 都说 analyst-tools 可后做、可在另一台机器,但 preflight 缺省就 hard-fail。**采纳**: preflight 改为缺省 lenient(analyst-tools 不在默认路径 → info-skip),显式传路径才 strict(`./preflight.sh /opt/af/analyst-tools` → 缺则 err)。三态(默认无 / 默认有 / 显式无)实测通过
- **P1 #2**(`py-spy --version` 不证明 attach path): reviewer 担心 cap/seccomp/Yama 任一漂移都让 attach 失败但 `--version` 仍 pass。**用户挑战 + 重新拆解**: 容器内 attach 实际依赖二进制(镜像保证)+ cap_add(compose 声明式)+ seccomp 默认 profile(自动放行有 cap 的 ptrace)+ host Yama(唯一外部变量)。前三环都是我们声明式管住的,从容器内复读是 over-engineering;**只有 Yama mode 3(host-wide ptrace 禁)能绕过我们的 cap**。**采纳收窄**: 不加 CapEff bit / seccomp 探测,只加 host-side Yama 读取(`/proc/sys/kernel/yama/ptrace_scope`),mode 3 时 warn"备份路径失效、依赖主路径 faulthandler",其他 mode + 不可读(非 Linux)都 info-skip
- **P2**(`docker exec backend` 不会解析): compose 没设 `container_name`(prod/intranet 是 `<project>-backend-1`),Mode 1 设了但是 `artifactflow-backend`。文档六处当前态 `docker exec backend` / `docker logs backend` → 全改 `docker compose -f <file> exec backend` / `docker compose ... logs backend`(service-name-based,跟 container_name 解耦,也支持 `--scale`)。preflight 自己之前就用 `docker ps | grep backend | head -1` 解析,无需改逻辑
- **P3 半真**(fix plan stale): line 19 优先级总览还在写 `--with-forensics` + "打 py-spy" → 更新为 round 4+5 真实描述。line 660+ 原始 spec 段(reviewer 指 661)**是有意保留的审计原稿**,差异在 644-649 已显式列出 —— 但 reviewer 不该需要读到 644 才理解 661 是历史;**加强方法**: 660 段头从"内容"改为"原始 spec 内容(round 0,差异见 644-649)"明示历史定位

第六轮(用户主导的 scope walk-back):
- **触发**: 第六轮 reviewer 又戳出 release.sh recipe 和 SOP 命令块都仍然无条件 `scp/tar/pip` analyst-tools,跟"optional"承诺仍然矛盾(round 5 修了 preflight 没修 recipe/SOP)。我准备继续加 compose 自动探测 + skipped/passed 状态机时被用户喊停:**"我有点没懂为什么现在好像这个事情搞得很复杂?就是打一个 bundle 带 pandas/numpy + 容器装 py-spy 应该没别的了吧?"**
- **诊断**: 用户对。本 PR 实质交付就**两件事**(analyst-tools tar + 镜像 py-spy),round 1-5 累积出来的验证机制(Yama 检查 / host tools 清单 / strict-vs-lenient / required-optional 分层 / 多 compose 探测)比交付物本身还重 —— 验证机制不该比被验证的东西复杂。每一条单独看都有 reviewer 戳过的"合理"理由,但累积起来背离了 PR 初衷
- **scope 收敛契约**: preflight 只验**两件事**(analyst-tools wheels 离线 resolve + 容器内 `py-spy --version`),其他全部移交或砍掉
  - **砍**: Yama runtime 检查、host gdb/strace 清单、strict/lenient 模式、required/optional 分层 + 多状态计数器、四态 ✓/✗/⚠/info 输出
  - **移**: Yama mode policy → `cloud-service-checklist.md` 第四段(问云托管,不是 preflight 跑时查);host 工具清单本来就在第四段
  - **简化**: 多 compose 探测 → `AF_COMPOSE_FILE` env override,默认 intranet path(zero-config 单一来源)
  - **保留**: ANALYST_DIR 仍接受位置参数(显式 / 默认两种用法)
- **release.sh recipe 条件渲染**: heredoc 里的 infra + analyst-tools scp/tar/pip 三组步骤按 `WITH_INFRA` / `WITH_ANALYST_TOOLS` 条件包含/省略;未带 flag 时给 footer 一行 "to add later, rerun with --with-X"。配 4 种 flag 组合实测渲染正确
- **SOP "取证就绪"重写**: 三层表 + 大段背景 → 二段文字(faulthandler 主 + py-spy 备份),analyst-tools 安装标"仅在 release 带 --with-analyst-tools 时",深挖工具引用 checklist 第四段
- **行数变化**: preflight ~200 → ~80(-120);recipe + SOP + checklist 增减相抵,fix plan 加本段(+30 净增)。总净减约 -100 行,scope 真正落回"bundle + 镜像 py-spy"
- **方法论 takeaway**: reviewer 反馈每条都"理论真",但**累积响应**会把简单事做复杂。用户的"step back"是必要的力学纠正 —— 单轮看不出来,跨多轮才能看出"验证机制比交付物还重"的失衡

第七轮(reviewer round 5,P2×2 残留收口):
- `1006e0c` (+7 行) round 6 收 SOP/checklist 时漏了 release.sh 内部三处 + recipe 顺序问题。**架构 OK,纯文案/SOP 顺序补漏**:
  - **P2 #1**(recipe preflight 只在 up 前): 第一次 preflight pass py-spy 必 ℹ skip,实际只验了 analyst-tools wheels。**采纳**: recipe 加 pass 2 (up -d 后),注释明示两次的角色差异
  - **P2 #2**(分进 tar 的 README + manifest 残留 `docker exec backend`): release.sh:247 (README in tar) / :360 (manifest) / :85 (内部注释) 三处统一 → `docker compose -f deploy/docker-compose.intranet.yml exec backend py-spy ...`,跟 round 5 已修过的 SOP/checklist 对齐
- **reviewer 评价定调**: "剩下主要是 release 输出和打包 README 的残留,不是架构问题" —— 印证 round 6 scope walk-back 方向正确,本 PR **架构面收敛完毕**,后续不会再有同源新增

**关键转向(第四轮)**: py-spy 从"forensics bundle 工具(装宿主机)" → "**backend 镜像内置工具**(`docker exec backend py-spy`)"。这是对 round 2 的修正:round 2 想避免"为低频备份做反射性镜像膨胀",但实际效果是把可用性外推给云托管协调;round 3 承认 py-spy 是事故现场最常用 + 第三方分发 + 我们能完全控制的诊断工具,直接打进镜像是**精准而非反射性**的扩张。三层分明:① 主路径 faulthandler dump(零依赖) ② 备份路径 `docker exec backend py-spy`(容器内 cap 自洽,零云托管依赖) ③ 深挖路径 host gdb/strace(云托管协调)。前两层完全自洽,事故时秒级可用。

**bundle 改名 + 减容**: `artifactflow-forensics-<slug>.tar.gz` → `artifactflow-analyst-tools-<slug>.tar.gz`(语义更准 —— 现在只剩 pandas/numpy 给 analyst 用);slug 简化为 `pandas<v>-numpy<v>-py<v>`(py-spy 已出 bundle,不在 slug 里);release.sh `--with-forensics` flag 改名 `--with-analyst-tools`。py-spy 段整体砍掉(下载/SHA pin/sha256 文件/README 段 ~100 行 -)。

**验证范围**:
- bash syntax + preflight 三态(missing analyst-tools / empty wheels / fake wheel)全测过,docker 不在场时容器内 py-spy 检查 info 跳过(为首次部署 preflight 设计)
- release.sh `--help` 渲染过、新 slug + bundle 名核对过
- **未跑** `--with-analyst-tools` 端到端(必触发 docker buildx),但 wheels 下载路径跟 round 1 实测同款(reviewer 之前实测过 pandas/numpy 解析路径)

下方 spec 保留原文作为审计材料。落地与 spec 的差异:
- spec 写 `deploy/wheels/`,实际走**独立 analyst-tools tar** 而非检入 git(.gitignore 已忽略 `wheels/`)
- py-spy 走**镜像内置**(round 3 重定位),不是 spec 想象的 release bundle 装宿主机;事故现场用 `docker exec backend py-spy`,无云托管协调依赖
- preflight 走 required(bundle + 容器内 py-spy)+ optional(host 深挖工具 warning) 分层,**不**把宿主机工具当硬失败 —— 主路径已自洽,host 工具缺位不阻塞部署
- spec 第 4 条"诊断策略定调:取证走宿主机"修正为三层:① **主路径** faulthandler(进程内,镜像) ② **备份** py-spy(镜像 + 容器 cap) ③ **深挖** host gdb/strace(宿主机)。第 1 + 2 层完全脱离云托管协调
- **air-gap 硬合约**: release.sh 注释 + SOP + preflight 三处显式"目标机器零联网",`pip install` 永远 `--no-index --find-links`

**目标**(round 3 重定位):
1. backend 镜像内置 `py-spy` + compose `cap_add: [SYS_PTRACE]`,事故时 `docker exec backend py-spy` 秒级可用作为 PR-obs-lite `faulthandler` deadman 失效时的备份
2. 提供 `pandas`/`numpy` 离线 wheels 作为 analyst 机器的零联网安装路径(跑 `observability_report.py`)
3. preflight 分 required(bundle + 容器 py-spy) / optional(host 深挖工具)两层,部署不被云托管政策卡住

**核心心态**:取证工具的可用性是部署时的保证,不是事故时的指望。精准扩张 ≠ 反射性扩张 —— 选事故现场最常用的一个工具(py-spy)进镜像,代价 6MB / +4%;不为深挖工具(gdb/strace/top)做同等投入,它们仍走宿主机/云托管。

**范围**:`deploy/`(release bundle 脚本)、`deploy/scripts/preflight.sh`(新)、`docs/_archive/ops/deployment-sop.md` / `cloud-service-checklist.md`(增改)。

**原始 spec 内容(round 0,差异见上方 644-649)**:
1. release bundle 内置 `py-spy` 静态二进制(单文件几 MB),从根上消除"内网现抓不到"
2. release bundle 内置 `pandas` / `numpy` 离线 wheel(`deploy/wheels/`),供 `scripts/observability_report.py` 在分析机用 `pip install --no-index --find-links` 安装。**不进 runtime `requirements.txt`** —— pandas+numpy ~80 MB 是分析工具,产品运行时不需要,跟 py-spy 同属"部署时可用 / 运行时不依赖"类别。`numpy` 是 pandas 的依赖,顺带;**不**再带 `psycopg2` / `pymysql` 这类 sync DB driver —— 脚本走 ORM `select(MessageEvent)` + `AsyncSession` 复用 app 已有的 asyncpg / aiomysql / aiosqlite,sync driver 没必要再多带一份
3. preflight 脚本验证宿主机有 `gdb`/`gcore`、`strace`、`procps`
4. 诊断策略定调:app 镜像保持精简,取证走宿主机(不往镜像塞 `top`)
5. SOP 增"取证工具就绪"检查项(含 py-spy 二进制 + analyst wheels 已就位)

**与 PR-obs-lite 的关系**:观测框架在容器内自动产出数据;取证工具(py-spy)与分析工具(pandas)是手动深挖时的最后一公里。两者互补,但部署节奏可以分开——PR-obs-lite 主要是代码改动,本 PR 主要是 release bundle / 文档,合到同一个 release 也行,但 PR 独立可分别回滚。

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

## PR-tz-unify:事件时间戳时区统一(P3)✅ 已完成

**落地记录**(commits 在 `main`):

第一轮(initial 落地,commit `263dc25`):
- 新增 `src/utils/time.utc_now()` 作为后端 naive UTC 单一入口;新增 `frontend/src/lib/time.parseUtcIso()` 把后端发来的 naive ISO 锚定为 UTC
- 后端热路径所有 `datetime.now()` → `utc_now()`:`core/events.py:55` `ExecutionEvent.created_at` 默认值、`core/engine.py`(metrics start/completed + 5 处 timestamp/duration 起点)、`core/controller.py`(5 处 timestamp)、`core/compaction_runner.py`(start/duration/timestamp)、`core/conversation_manager.py`(message timestamp)、`tools/builtin/artifact_ops.py`(`ArtifactMemory.created_at/updated_at` + `persisted_at`)、`tools/builtin/web_fetch.py`(3 处 fetched_at)、`api/routers/{chat,artifacts,stream}.py`(error event timestamp + memory fallback)、`api/schemas/events.py`(`SSEEvent.timestamp` default_factory + `from_dict` fallback)、`api/services/{controller_factory,stream_transport}.py`、`observability/{watchdog,sampler,admin_runtime}.py`(jsonl ts 字段从 aware UTC → naive UTC 与 threshold 对齐)
- 显示侧:`ObservabilityPanel.tsx`(formatTime helper + 三处 created_at + 一处 updated_at)、`sidebar/ConversationItem.tsx`、`sidebar/AdminConversationList.tsx`、`forms/UserDetailForm.tsx`、`chat/ConversationBrowser.tsx`、`artifact/ArtifactList.tsx` 一律 `new Date(...)` → `parseUtcIso(...)`
- 保留本地:`core/context_manager.py:69` 给 LLM 的"当前时间"提示词(UX 要求用户本地)、`api/services/auth.py:54` JWT iat/exp(aware UTC 是 PyJWT 契约)
- `scripts/observability_report.py:121-123` 注释重写:由"SQLite 用 func.now() 是 naive UTC"改成准确描述"应用写 + DB server_default 都是 UTC naive,PG 部署需 TIMEZONE=UTC"
- CLAUDE.md "ORM instances" bullet 后新增独立"Time convention" bullet 说明 `utc_now()` 约定 + PG TIMEZONE=UTC 要求 + 两处例外
- 测试:`tests/utils/test_time.py`(naive UTC + ExecutionEvent default 三条回归)+ `frontend/src/lib/time.test.ts`(naive / Z / +08:00 / 紧凑 -0500 四态)
- **不迁移历史数据**:Shanghai 部署中 `message_events.created_at` 历史行偏 +8h(本地朴素写入),分界点 = 本 PR 部署时间;observability_report `--hours N` 在跨分界点窗口里有 8h 错位,影响窗口有限,自然衰减

**决策依据**(对应"决策点"三选):
- 方向 1(naive UTC):改动最小、跟现有 schema + SQLite/PG 默认对齐;方向 2(aware UTC)留给真正出现跨 TZ 部署或 DST 场景再做
- 纳入显示侧:后端改 UTC 后浏览器对 naive ISO 仍按本地解析,不改前端会反向偏 TZ;集中在 `parseUtcIso` 一处处理便于后续审计
- 不迁移历史数据:`server_default=func.now()` 在 SQLite 上本就是 UTC naive,真出问题的是 Python 写入路径;backfill 需要按表逐个判断写入来源,复杂度高于回报,接受历史窗口偏移

**与 PR-obs-lite 的约束**:`observability_report.py` threshold 已经是 naive UTC(L121-123),实现层本就对;事故是 Python 写入路径偏了,本 PR 修这一边,threshold 表达式不变、只更新注释。

**PG 部署运维要求**(round 1 后已在代码层兜住,见下):自托管 PG 容器配 `-c timezone=UTC`(四个 compose 全已加);云托管 PG(RDS / Aurora) + DATABASE_URLS failover 节点的 server timezone 由 `src/db/database.py._apply_session_tz_kwargs` 在连接层强制(asyncpg `server_settings={"timezone": "UTC"}` / aiomysql `init_command="SET time_zone='+00:00'"`),不依赖部署者配置。SQLite 自动 UTC,无需配置。

第二轮(reviewer round 1,P1 一条二修):
- **P1 #1 漏改的 compose**:reviewer 戳出 root 还有三个 compose,intranet 第一轮只覆盖了 `deploy/docker-compose.intranet.yml`。补 `docker-compose.prod.yml`(Mode 2 prod,同款 `command` 加 `-c timezone=UTC`)+ `docker-compose.dev.yml`(dev infra,`postgresql` profile 加 `command: postgres -c timezone=UTC`)。`docker-compose.yml`(Mode 1 Quick Trial)是 SQLite-only,不涉及
- **P1 #2 DB 连接层兜底**:compose flag 不覆盖云托管 PG(RDS / Aurora / Aliyun RDS)与 DATABASE_URLS failover 节点 —— 我们不控制服务器 timezone GUC。采纳建议在 `src/db/database.py` 加 session-TZ 强制:新增 `_apply_session_tz_kwargs(driver, kwargs)` 纯函数 helper(setdefault 语义,不覆盖 DSN 显式 timezone / 不破坏已有 server_settings),`initialize()` 非 SQLite 分支按 URL backend 注入 `connect_args`(单 URL 路径),`_failover_creator` per-iteration 注入(`async_creator` 绕过 SQLAlchemy `connect_args`,必须再注一遍)。+ 11 个新测试覆盖 helper 6 个不变量 + 5 个集成场景(failover probe / initialize PG / MySQL / SQLite passthrough)
- CLAUDE.md "Time convention" bullet 更新一句:云 PG / failover 由 database.py 强制,不只靠 compose flag

第三轮(reviewer round 2,P2 单 URL DSN 覆盖):
- **P2 单 URL 路径 DSN init_command 静默丢失**:reviewer 戳出 round 1 的单 URL 注入 `self._apply_session_tz_kwargs("mysql", {})` 喂的是空 dict,但 SQLAlchemy 会把 URL 里的 `?init_command=...` 解析成 connect 的 kwargs;`connect_args` 整 key 替换语义把用户的 init_command 静默替成 helper 输出的 timezone 命令。PG 那边同款症状 —— `?application_name=af` 会被 SQLAlchemy 翻成 `server_settings={"application_name": "af"}`,我的 `connect_args["server_settings"]={"timezone":"UTC"}` 整 dict 覆盖,application_name 丢失(reviewer 只点了 MySQL,PG 顺手都修)
- 修法:`initialize()` 在喂 helper 前从 `make_url(database_url).query` 抽 session-affecting key(PG 用 `_PG_SERVER_SETTINGS` frozenset,MySQL 用 `init_command`),merge 到 existing dict 再传 helper。helper 已有的 setdefault / prepend 语义就能保留 DSN 值。+ 2 个新单 URL 集成测试:`test_initialize_mysql_preserves_dsn_init_command`(`?init_command=SET autocommit=1` → 最终 `"SET time_zone='+00:00'; SET autocommit=1"`)、`test_initialize_pg_preserves_dsn_application_name`(`?application_name=af` → 最终 `server_settings={"application_name": "af", "timezone": "UTC"}`)
- 既存 helper 测试 `test_pg_preserves_existing_server_settings` / `test_mysql_prepends_existing_init_command` 已经覆盖 helper 这一侧;round 2 补的是 `initialize()` → helper 这一条 wiring(reviewer 原话:"helper itself handles prepending correctly, but initialize() does not feed it the existing URL init_command")
- 测试 844 → 846 passed(+2)

第四轮(reviewer round 3,P2 PG URL 泄露 top-level kwarg):
- **P2 round 2 修了 `connect_args.server_settings`,但没修 URL 本身**:reviewer 戳出 `create_async_engine(self.database_url, ...)` 仍然拿到 `?application_name=af` 的原始 URL。SQLAlchemy asyncpg dialect 的 `create_connect_args` 把整个 `url.query` dict 拷到 `opts`,`application_name='af'` 作为 **top-level** kwarg 传给 `asyncpg.connect`;asyncpg signature 不接受这个顶层 kwarg(必须经 server_settings),启动会 TypeError。**经实测验证**:`asyncpg_dialect.create_connect_args(make_url('postgresql+asyncpg://host/app?application_name=af'))` 返回的 opts 顶层有 `application_name='af'`
- MySQL 不需要同款 sanitize:`aiomysql.connect` 接受 `init_command` 作为合法 kwarg,即使 SQLAlchemy 从 URL query 二次传一份,`connect_args["init_command"]` 也只是覆盖而非 TypeError(MySQL 路径 round 2 自洽)
- 修法:`initialize()` 增 local `engine_url` 变量(默认 `self.database_url`),PG 单 URL 路径抽完 server_settings keys 后 `engine_url = url.difference_update_query(self._PG_SERVER_SETTINGS)` 把这些 key 从 URL 剥掉;`create_async_engine(engine_url, ...)` 接收 sanitized URL。connect_args 那条路径继续接管 application_name(不丢)。SQLite / failover / MySQL 路径 engine_url 保持 self.database_url 不动
- 测试 +3:
  - `test_initialize_pg_strips_server_settings_keys_from_url` —— 校验 create_async_engine 拿到的 URL `query` 不含 `application_name`,同时 connect_args 仍含完整 server_settings
  - `test_pg_asyncpg_dialect_on_sanitized_url_no_toplevel_application_name` —— 直接走 SQLAlchemy asyncpg dialect 的 `create_connect_args(sanitized_url)`,断言 opts 顶层无 `application_name`(这是 reviewer 关心的 smoking gun:asyncpg.connect 的实际 kwarg surface)
  - `test_pg_asyncpg_dialect_on_unsanitized_url_leaks_application_name` —— **负向控制**:确认未 sanitize 的 URL 走 dialect 确实会 leak。SQLAlchemy 未来若把 asyncpg dialect 改成自动翻 application_name → server_settings,此测试会失败提醒"round 3 的 sanitize 步骤可能可移除",防止静默行为漂移
- 为什么没用 reviewer 建议的"patch `asyncpg.connect` 端到端测试":那需要真 AsyncEngine 启动到 pool acquire 才触发 `asyncpg.connect`,中间还要绕过 `_check_alembic_version()` 的 DB 查询。dialect-level 测试隔离了 SQLAlchemy URL → opts 的映射,正是 round 3 修改的 surface,且不依赖 SQLAlchemy 启动栈的具体实现细节
- 测试 846 → 849 passed(+3)

第五轮(reviewer round 4,结构性统一 —— DSN query 翻译层封装边界):
- **问题不是单个 key 没补全,而是 *两套* DSN query 解释路径并存**:reviewer 戳出 round 1–3 的修法是"发现一个 key 泄露就打一个补丁",而真正的根因是项目内同一个 DSN 有两条解析路径:
  - **单 URL 路径**(`initialize()`):依赖 SQLAlchemy dialect 解析 `url.query`,我们只从 query 抽 round 2 列出的少数几个 session-affecting key
  - **failover 路径**(`_parse_db_url` → `_failover_creator`):自己解析整个 query,直接喂 `asyncpg.connect` / `aiomysql.connect`
  两条路径对 *同一个 DSN query* 的语义解释会漂移:failover 已知 `sslmode → ssl=`、`command_timeout → float`、`application_name → server_settings`、`ssl_ca → SSLContext`、`charset/autocommit/init_command/...` 翻译规则;但单 URL 那条只截了 `application_name`(PG)和 `init_command`(MySQL),其余 key 全部由 SQLAlchemy asyncpg dialect 当作 verbatim kwargs dump 给 `asyncpg.connect`,asyncpg signature 不接受这些 → TypeError;round 3 修的只是表里的一行(application_name),后面再加任何 key 都会重演同一个 bug
- **修法(结构性)**:让 `DatabaseManager` 成为 **唯一** 的 DSN query 翻译层,SQLAlchemy dialect 不再"二次解释"任何 query key
  - 拆出 `_parse_db_query_params(url: URL, driver: str) -> (connect_args, consumed_keys)` 纯函数,作为唯一的 query → driver-kwargs 翻译器
  - `_parse_db_url`(failover 路径)瘦身,query 段委托给新 helper(只保留 host/port/auth 组装)
  - `initialize()` 单 URL 路径改成:`_parse_db_query_params(url, driver)` → `_apply_session_tz_kwargs(driver, ...)` → 把 `consumed_keys` 一次性从 URL 上剥掉(`url.difference_update_query(consumed_keys)`)
  - 单 URL 路径无法识别的 key 现在在 `initialize()` 时立刻 `ValueError`(fail-loud-fail-early),不再延后到 `asyncpg.connect` 的运行时 TypeError;与 failover 路径行为一致
- **扩展功能而非缩减**:之前单 URL DSN 实际上只能用 `application_name` / `init_command` 而不出问题,其余 query 都是定时炸弹;round 4 后 `sslmode` / `command_timeout` / `ssl_ca` / `charset` / `autocommit` / `unix_socket` / `program_name` 全部可用于单 URL DSN —— 跟 `DATABASE_URLS` failover 列表完全等价
- 测试 +22:
  - `TestParseDbQueryParams`(12 条):helper 直接单测,覆盖每个支持 key 的 `(connect_args, consumed_keys)` 配对、空 query、sqlite passthrough、unknown key fail-loud、组合 key、输入 URL 不被 mutate
  - `TestInitializeUnifiedTranslation`(7 条):单 URL 通过 `initialize()` 的端到端,每个新支持 key 的 translate + URL strip 双断言,unknown key fail-at-init-not-at-connect,以及"单 URL vs failover 在同一 DSN 上 connect_args 全等"的统一性断言
  - `TestDialectSmokingGunsRound4`(2 条):一条正向(sanitize 后 dialect 不会发出任何被 parser 消费的 key 到 top-level opts)+ 一条负向控制(不 sanitize 至少有一个会 leak,SQLAlchemy 行为漂移哨兵)
- 测试 849 → 871 passed(+22)
- CLAUDE.md "Time convention" bullet 维持不变(round 4 是 DSN parsing layer 的内部封装重构,不改对外的时区契约)

---

### 原始 spec(round 0,差异见上方落地记录)

**发现来源**:PR-obs-lite reviewer 在 Shanghai 部署(UTC+8)测 `scripts/observability_report.py --hours 1`,发现一条本地 2 小时前写入的 `llm_complete` 仍被报告查出来 —— 时间窗口偏差正好 8 小时。

**问题成因**:事件 `created_at` 在代码里走两条路,语义不一致。

| 路径 | 位置 | 时间约定 |
|---|---|---|
| 热路径(几乎所有事件) | `src/core/events.py:55` `ExecutionEvent.created_at = datetime.now()` | **本地朴素**(Shanghai 部署即 UTC+8 朴素) |
| 冷回退(没传 created_at 时 ORM 兜底) | `src/db/models.py` 各表 `server_default=func.now()` | DB 端时间:SQLite naive UTC、PG aware UTC、MySQL DB-local |

`MessageEventRepository.batch_create` 总会传 `event_data["created_at"]`,所以**生产 DB 里几乎全是本地朴素**;`server_default` 是 schema 兜底,正常走不到。

PR-obs-lite 的 `observability_report.py` 一开始把 threshold 写成 naive UTC(对齐 `func.now()` 在 SQLite 上的行为),恰好挑错了 —— 在非 UTC 部署里就是 TZ 偏移量大小的窗口错位。

**影响面**:
- 任何按 `created_at` 做时间窗口查询的代码 —— 目前已知仅 `observability_report.py`,但跨地域多部署 / 跨 TZ 比对场景会触发
- DST 地区(本项目内网在 Shanghai,无 DST;若未来扩到有 DST 的地区,边界两侧会有 1h 歧义)
- 显示侧:前端 / 日志渲染当前对这个字段的时区假设需要审一遍

**当前状态**:本 PR 范围内**不修**(怕碰热路径 + schema 而 scope creep)。`observability_report.py` 当前实现保留 naive UTC threshold,**非 UTC 部署里时间窗有 TZ 偏移**;Shanghai 用户建议手动 `--hours N+8` 来覆盖实际 N 小时,直到本 PR 落地。

**修复方向(两选一,落地前需先决策)**:

1. **全链路 naive UTC**
   - `events.py` 改 `datetime.now(timezone.utc).replace(tzinfo=None)`,所有 `server_default=func.now()` 保持(SQLite/PG 都是 UTC naive)
   - 优点:改动最小,DB schema 不动
   - 缺点:仍然是"朴素",未来 DST 地区 / aware 比较场景还会再踩;前端显示要做"DB 是 UTC,展示按 client TZ 转"的约定
2. **全链路 aware UTC**
   - 所有 `datetime` 字段改 `DateTime(timezone=True)`、所有 Python 侧 `datetime` aware
   - SQLite 没有原生 tz 支持,SQLAlchemy 会做文本格式约定,需要小心读路径
   - 优点:消除朴素时区的根本歧义
   - 缺点:迁移面更大,需要 schema 改动 + 历史数据迁移(按部署 TZ 一次性偏移回 UTC)

**范围**:
- 审 `grep -rn "datetime.now()" src/` 所有写时间戳的 callsite
- 审所有 `server_default=func.now()` 的列(messages、message_events、conversations、artifacts、artifact_versions、users、departments)
- 审显示路径:前端如何渲染 `created_at`、log 怎么打
- `observability_report.py` 的 threshold 跟着改对(naive UTC → 跟最终决策一致)
- Migration 脚本:历史数据按部署 TZ 偏移回 UTC(若选方向 2)或保持(若选方向 1,因为 DB 端 `func.now()` 已经是 UTC,需 audit 是否真的全部一致)
- 测试:`tests/observability/test_reviewer_fixes.py::test_load_message_events_via_orm` 的种子时间需对齐最终约定

**决策点**(开 PR 前需先确定):
- 方向 1 vs 2 —— 倾向 1(改动最小,跟现有 ORM 默认对齐;方向 2 留给真正出现跨 TZ 部署时再做)
- 是否同时审显示侧 —— 倾向纳入(否则后端统一了 UTC,前端如果还用 local naive 渲染会展示偏移)
- Migration 策略 —— 若选方向 1,可能不需要数据迁移,只需声明"从某个时间点开始 created_at 统一为 UTC naive";若选方向 2,必须 backfill

---

## 文档 PR(P3)

纯 markdown PR,两个文件、零代码:

### `docs/runbooks/service-hang.md`(应急 runbook)

新建 `docs/runbooks/` 顶层目录,跟 `docs/architecture/` / `docs/guides/` 平级,后续可扩展其它应急场景。本次首批内容:
- `/health/live` vs `/health/ready` 判别(循环卡死 vs 依赖问题)
- `docker stats` / `pidstat` / `/proc/<pid>/status` / `strace -f` 判别 CPU 型卡死
- `docker logs backend | grep "Thread 0x"` 抓 deadman switch 的硬 wedge dump
- `tail -f data/observability/loop-lag.jsonl` 看刚才有没有软退化事件
- `GET /admin/runtime`(组件 #4)看实时水位(服务还活但变慢的 triage 入口)
- `kill -USR1 <pid>` 手动 dump(独立于 deadman 自动)
- `py-spy dump` 抓栈(走 PR-forensics-bundle 的内置二进制路径)
- `gcore` 冻结现场流程
- 止血:`restart backend`,以及"重启前先取证"的纪律

### `docs/guides/observability-tuning.md`(调参手册)

`scripts/observability_report.py` 的输出怎么读 + 隐藏常量怎么调,跟 PR-obs-lite 的脚本耦合:
- LLM / 工具调用聚合表的解读(p50 / p99 / max latency、失败率)
- `update_artifact` fuzzy_stats 调参报表(outcome 分布、`unique_centers` 直方图 vs `MAX_UNIQUE_CENTERS`、`elapsed_ms` vs `MAX_FUZZY_WALL_CLOCK_MS`、`similarity_pct` 直方图)
- 24h loop_lag 分布(p50 / p99 / max)与 `LOOP_LAG_WARN_MS` 阈值的对照
- RSS / DB pool / Redis 趋势 → 容量规划与告警阈值的推导逻辑
- 何时调 `OBS_JSONL_MAX_MB` / `OBS_JSONL_BACKUP_COUNT`(看实际占用)

放 `docs/guides/` 跟 `add-tool.md` / `add-agent.md` 同位置,语义"日常操作型 how-to"匹配。

---

## 待决策项汇总

1. **PR-1 配置常量起点值**:`FUZZY_MAX_L_DIST` / `FUZZY_MAX_RATIO` / `ANCHOR_MAX_OCCURRENCES` / `ANCHOR_SHINGLE_LEN` / `ANCHOR_MIN_USABLE_LEN` / `MAX_UNIQUE_CENTERS` / `MAX_FUZZY_WALL_CLOCK_MS` 等的初始默认值——上面给的是建议起点,可在实现/测试阶段微调。上线后按真实日志可调(都是隐藏常量)。是否引入动态 anchor_len(ASCII / 长文本)是单独的优化决策,等真实日志显示固定 6 触发过多虚假候选再说。
2. **PR-obs-lite 配置常量起点值**:`LOOP_LAG_WARN_MS` / `WATCHDOG_DEADMAN_TIMEOUT_MS` / `OBS_SAMPLE_INTERVAL_SEC` 等——同上,先按建议值上,试运行第一轮看 jsonl 报告再调。`WATCHDOG_DEADMAN_TIMEOUT_MS` 默认 10s 偏保守,试运行如出现"长但合法的同步段"误触(目前已知风险面已被 PR-1 算法上界消除,但不排除其它工具)再加。
3. **jsonl 轮转**:默认 `data/observability/{metrics,loop-lag}.jsonl` 都在 `artifactflow_data` 持久卷(`/app/data`)内,事故后不会随容器消失。**循环写已集成进本 PR**(stdlib `RotatingFileHandler` + `OBS_JSONL_MAX_MB` × `OBS_JSONL_BACKUP_COUNT`,默认 50MB × 10),不再依赖外部 `logrotate`。`metrics.jsonl` ~600 KB/天 → 单切片覆盖 ~80 天 / 总覆盖 ~800 天;`loop-lag.jsonl` 事件驱动远低于此。两个 sink 都同步 mirror 到 stdout,`docker logs backend` 是二级兜底通道(防卷意外丢)。试运行第一轮看实际占用再决定是否调阈值。
4. **Prometheus / 告警路径**:暂不做(`/metrics` exporter + 告警体系)。等出现"jsonl + 脚本不够用、需要图和阈值告警"的明确信号再启动决策;届时 sampler 字段已就位,加 exporter 是平移工作。
5. **运维**:是否引入 unhealthy → 自动重启(autoheal 容器 / 编排层)?独立于以上 PR 的韧性决策。
6. **告警**:容器健康 / CPU 占满 / 事件循环延迟 / 探针失败的告警体系——本次所有问题都是被动发现。短期靠 PR-obs-lite 的 jsonl + 人工巡检兜住;成体系告警依赖第 4 项的 Prometheus 决策。

> **已决策**(留作可追溯):A vs B 选 B,基于本地 `logs/artifactflow.log` 实测 Layer 2 被触发(`枝/技` 单字符替换用例)。详见 PR-1 节"决策依据"。原 PR-1(封顶热修)和原 PR-2(分两步走)已合并为单一 PR-1。
