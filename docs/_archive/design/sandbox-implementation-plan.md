# 沙盒化 Bash + 文件执行工具 —— 实施计划

> 状态:规划完成,实现未启动
> 起草:2026-05-21 · 最后更新:2026-05-21
> 前序产物:
> - `sandbox-gvisor-evaluation-2026-05.md`(本目录)—— gVisor 选型 + Kylin 兼容性诊断(已完成)
> - `tool-result-artifact-mount.md`(本目录)—— 工具结果溢出转 artifact 的先例(`source` 字段 / 构造函数注入 `ArtifactManager`)
> - 离线包 `dist/sandbox-gvisor-20260512.tar.gz`(未入 git)
> 底座依赖:
> - `artifact-layer-redesign-plan.md`(本目录)—— artifact 层重构(live 上事件轨 / 单一生命周期 / 删 `ArtifactManager`)。**本重构先于本 plan C 阶段落地**;沙盒回写/挂载建在新四层(`ArtifactService`/`WorkingSet`/`Repository`)上,A 阶段二进制存储复用其「binary 只发元数据事件」的溢出口。详见该 plan「与沙盒 plan 的衔接」节。

## 本文档定位

这是一份 **plan,不是详细设计**。它讲清楚每个阶段做什么、为什么、什么算完成;**落实细节(具体改哪些代码、schema 字段怎么定)留到开工那个阶段再探索敲定**。

同时它是**跨 session 的跟踪文档**:新 session 接手时,先读「进度」一节就知道做到哪、下一步从哪续。每推进一个阶段,更新阶段状态 + 在「变更日志」追加一句结论;方向有变也记到变更日志。

## 进度

- **当前**:**A / B(双架构)完成**(详见各段进展)。**C 全切片落地**(2026-06-10→11,C-0→C-wire,见 C 段「进展」)。沙盒**已 live 暴露**(lead/research 拿到 bash=CONFIRM/mount/persist)。
- **下一步**:剩唯一工作包 **D 段**(Kylin 端到端冒烟:真 runsc + Word 场景 + loop 池子 host-prep + git 镜像重冻结双架构 id + uid 1000 属主验)。**上传路由翻转已落地**(2026-06-11,见 C 段进展);**沙盒镜像加 git 已落地**(2026-06-11,见变更日志——双架构镜像 id 锚点作废,D 重冻结)。后续大方向:**skill 系统**(用户已自备一套 skill;上传翻转后的格式 remediation 提示归 skill)。
- **产物处置(2026-06-05 拍定)**:`feat/sandbox` 这批已验收产物(`sandbox/` 探针 + 构建脚本)**暂留分支不动**,不单独提早合 main。「是否把就绪探针子集(`unshare -U` 闸 + smoke + ENOSYS/uid)提升为通用部署机预检工具」**推迟到 C/D 阶段**——届时有真实第二调用点(每台新沙盒宿主预检 + D 端到端冒烟)再校准边界,现在抽象属投机(YAGNI)。

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | artifact 地基(二进制存储 + 多格式上传 + 识图) | **完成**(2026-06-08 三切片 + 2026-06-09 review 加固 + live E2E 用户实测通过) |
| B | Kylin gVisor 功能验证(内网) | x86_64 **完成**(验证通过+已撤出);arm64 **完成**(2026-06-08,64K→4K 换核后全绿) |
| C | 沙盒引擎集成(本机 runc 连调) | **完成**(C-0 / C-session / C-stage / C-reap / C-wire 全落地;沙盒已 live 暴露) |
| D | 上线前 Kylin 端到端冒烟 | 未开始 |

依赖:C 依赖 A 的二进制存储 + B 的冻结镜像。A 逻辑独立(识图本身就有价值),可先做,但本期不单独合 main —— 见分支策略。

**分支策略**:整个工作在 `feat/sandbox`(从 main 切)推进,**待沙盒成熟、整体验证通过后再整体 merge 回 main**;之后按既有策略 overlay intranet。不增量合 main 的原因:artifact 层会逐步改向"纯源承载"(原则 6)并最终下线现有 md→Word,**半迁移态(层已改、沙盒未就位)不能漏到 main/生产**。

## 目标与范围

给 lead / research agent 增加**沙盒化的 bash + 文件系统执行工具**,让 agent 安全执行任意 shell / Python、处理用户上传的多格式文件,产物写回 artifact 系统。用 gVisor(`runsc`)隔离,DooD 模式由同一个 Docker daemon 起,per-turn ephemeral。

**Non-goals(本期明确不做)**:Firecracker 后端、artifact 路径层级树、上传源 blob 随 md 编辑自动反向同步(注:agent 在沙盒里用 pandoc 显式生成新 docx 是 in-scope,见 A/C)、per-session 持久容器、blob 落对象存储、模型生成多文件项目的"带目录浏览"UI。理由散见各阶段与下方原则。

## 能力边界(产品定位推论)

> 这一节不讲沙盒做什么,讲 ArtifactFlow 整体的适用边界,作为后续技术决策的指南。沙盒只是兑现这条边界的工具之一。

**定位**:多用户中心的 **agent 编排平台**。灵活度落在「agent 即 MD 数据 + 跨 agent 编排是一等公民 + 工具/权限配置层可调 + SaaS 协作语义(跨中心持久、可审计、可回放、可分发)」三位一体,不是泛泛的"灵活"。

**不做**:不替代本地的、**持续工作态**的 dev loop。三类形态结构性不适配:
- **跨多轮代码库增删改**:文件系统语义(高频 byte-level 写)跟跨中心 DB 同步(低频 row-level 复制)阻抗失配,没有不破坏 DR 的折中。
- **低延迟 read-edit-test-debug 循环**:跨进程沙盒边界(DooD + 不可信代码隔离前提)叠加 per-turn 物化,延迟比本地 fs 高一个量级,是 SaaS 形态对 local-CLI 形态结构性的让步。
- **长时跑的中间态任务**(长 build / 训练 / daemon):per-turn ephemeral + lease 生灭决定跨 turn 留不下中间态。

**沙盒覆盖什么**:**单 turn / 短链路闭环**的任务都在范围内。重不重不是问题,"持续"才是问题 —— 数据处理、跑模型推理、文档转换、解析多文件、生成 zip 产物这类一次性重活都覆盖。

**对后续决策的约束**:遇到"跨轮持久工作态 / 低延迟 fs / 长任务中间态"的功能诉求,**先质疑这是不是 ArtifactFlow 该承担的场景**;若答案是"是",意味着要推翻原则 1(持久态归 TDSQL)或 Non-goal「per-session 持久容器」—— 本质是另起一个产品形态,不是加一个功能。

## 贯穿原则

1. **持久态归 TDSQL(跨中心同步),易失态归本地 worker + 单中心 Redis。** blob 是持久态 → 进 DB;沙盒容器是易失态 → per-turn、绑 worker、跟 lease 一起生灭。
2. **runtime 是 config 开关,不是硬编码。** 本机开发用默认 `runc`,生产换 `runsc`;引擎集成代码对 runtime 无感。这让"内网只验 gVisor,集成回本机做"成立(开发机 macOS 跑不了 runsc)。
3. **资源上限一律大声失败,不静默丢弃。** 文件大小 / 文件数 / 总字节 / 超时,超限即 loud-fail 并把后果写进给模型的提示;阈值是隐藏常量,不做模型可调参数。
4. **沙盒是显式 stage 进出的工作区,不是 artifact store 的自动镜像。** mount-in 与回写**都显式**:模型显式把指定 artifact 物化进工作区、显式调工具回写,不自动物化整 session、也不 diff 整个目录。**关键是定性**——容器 fs 不是"artifact 的第三态",而是 scratch 工作区:copy-in → 容器内随便改(发散是工作本身)→ 显式 persist,persist 落回来就**变成一次普通 artifact 写**(进 dirty cache,随 `flush_all` 落盘,与 `update_artifact` 同路)。工作区对 artifact store 没有同步义务,故没有三态一致性问题。对比 Claude Code:磁盘工作副本 vs git 记录,`commit` 是显式桥;沙盒文件在 persist 前就是个临时拷贝。理由:① 内存态是 artifact 系统的脊柱(多数 turn 不开沙盒、BLOCKED 节点也要能写 artifact),不能耦合到"有沙盒在跑";② per-turn ephemeral 决定每轮都要重新物化,auto vs 显式只差"谁命名",显式更省更清楚、且对小模型 legibility 更好;③ 显式 by construction 关掉"挂哪些 artifact"的待决项,避免 auto-mount 整 session blob 的 scaling footgun;④ 失败模式从"两份副本哪份权威"的静默困惑变成"忘 mount 就报错→补 mount"的响亮自纠(合原则 3)。
5. **带目录结构的多文件 = 一个 zip blob,只在 ephemeral 容器里解压/打包,从不进 DB 表。** 用户传整个仓库、或回写一个目录的"几千行炸库"问题,用这一条根除。
6. **artifact 层是文件源的承载方,不是转换器。** 用户传 Word 就是个 Word(blob),传图就是个图;要 md 让模型在沙盒里 word→md,要 Word 让模型写 md 再转 word。前端/后端**不提供固定格式转换功能**,一切转换是模型在沙盒里的职责。**目标态**:Word 导出等转换为沙盒专属能力;现有 md→Word 在**沙盒成熟前保留作过渡、成熟后下线**。推论:无可用沙盒的部署里,富格式上传只能当不可读 blob 存(连读 docx→md 也属沙盒能力)。

7. **沙盒默认全禁网;依赖走三层离线投递,绝不靠出网。** 沙盒跑不可信 LLM 代码,**网络出网是独立的第三条边界**——与「逃逸隔离(gVisor 管)」「DooD socket = backend 有 host root(创建参数防污染管)」正交,必须在**网络边界**封,**不能降级成「靠 CONFIRM 对命令授权来控」**:授权是 *consent*(人同意了某条命令的**意图**),网络是 *confinement*(不管谁同意,容器代码**够得着什么**);开网后 ① 被授权命令的传递行为对用户不可见(`pip install` = 任意代码执行),② 同容器里**没被授权**的代码(传递 import / 被污染 wheel / 生成代码任意一行)也拿到网做外泄/横移。内网 web 工具已禁、沙盒无任何合法公网需求,故默认 `--network=none` 是零成本纯收益。依赖因此**全离线投递,分三层**:① **烤进镜像**——跨场景通用、属「环境定义」而非某场景的选择、per-turn 现装太贵的(python / 科学栈 / pandoc / ripgrep);② **离线 wheel bundle 挂固定位**(`pip install --no-index --find-links`)——通用但太重、不值得人人烤的常驻 extras;③ **skill 自带 asset**——场景 specific 的长尾,骑 skill 的富态 bundle、仅该 skill 激活时按需挂。②③ 是**同一套 find-links 机制、不同生命周期**(常驻 vs 随 skill),别造两套。**类别纪律**:依赖 ≠ artifact——artifact 是用户拥有的**数据**(走 mount-in / persist,blob 进 DB),依赖是**执行环境**(走镜像 / bundle),别把依赖塞进 artifact 系统。**护栏**:skill bundle **只做加法、不 re-pin 基础栈版本**——否则一个 turn 内多 skill 版本冲突会逼出版本解析 / per-skill venv 的机器;用「基础栈全局固定、skill 只增量」的约定在源头掐掉(合「fix 复杂度超过 feature 价值就退回 scope」)。

## 已锁定的决策

1. **blob 存 DB(TDSQL)。** 生产是跨中心同步的 TDSQL + 单中心非同步 Redis,无对象存储;落盘只能单中心、破坏京沪 DR。所以二进制进 TDSQL,与文本/inventory 热路径隔离,并设大小上限(卡在 MySQL 包大小与跨中心复制成本之下)。
2. **artifact 身份轻解耦。** id 保持稳定句柄;补真实文件名用于物化与展示,去重不再改残名字;**不建路径层级树**(目录结构交给 zip)。
3. **回写二分。** 单文件 → 一等 artifact(用户面板可直接看);多文件/目录 → 一个 zip blob artifact(可下载整包)。
4. **识图折进 A 阶段。** 沙盒是驱动排序的北极星;识图与二进制存储同源,跟字段设计一起做。

## 阶段

### A — artifact 地基(沙盒与识图共同的前提)

**做什么**:让 artifact 能承载二进制并理顺多格式上传——这是"沙盒回写文件"和"用户传图让模型识图"共同的地基。逻辑独立、可先做,但跟随分支策略整体合并,不单独合 main。

**包含**:
- **二进制存储**:给 artifact 加一条与文本/inventory 热路径隔离的二进制存储,落 TDSQL,带大小上限。
- **多格式上传保真**:像 Word 这类富格式**按二进制 blob 存,源不可变**。富格式的读/写都交给沙盒里的 pandoc(见 C),artifact 层不做 md↔docx 逻辑、也不需要 backend 转换器。**原始 blob 因此一物两用:既是不可变源,又是 pandoc 重新生成时的样式模版**(驱动场景见 C)。
- **多模态识图**:模型 read 一张图能真正"看到"它。**这是引擎侧改动最大的一处**:目前一次 LLM 调用的 message 内容全链路是纯文本字符串,识图要让工具结果能携带图片块进上下文;事件里存图片**引用**而非字节,跨轮历史重建时再还原(否则事件表会被图撑爆)。
- **身份轻解耦**:见决策 2。

**到时再敲定**:blob 是否随版本走;大小上限取值;是否仍在上传时**预转一份 md 仅供面板预览**(已非架构必需,纯 UX 取舍,可后加);多图/大图的 token 成本与上限。

**进展**(后续 refer 的细节落点):
- 2026-06-04 多模态识图最底层验通:`astream_with_retry` 不改即可透传 content-blocks(`[{type:text},{type:image_url}]` 块列表 + base64 data URI),`qwen3.7-plus` 流式+usage 正常、自证读到图中数字。结论:识图改动不在 LLM 调用层,在上游全链路(ToolResult 携图块 / 事件存图片引用非字节 / `EventHistory` 重建时还原)。复跑脚本 `tests/manual/multimodal_vision.py`(2026-06-08 补 `multiturn`/`multiimage` 模式:直调 `astream_with_retry` 验透传层、非复原层——与下条「turn 内瞬态不重建」一致)。顺带:模型别名 3.6→3.7-plus、未知模型名改 loud-fail。
- **2026-06-08 A 开工前决策锁定**(逐项拍定,随即开工):
  - **二进制存储 = 独立 1:1 `ArtifactBlob` 表**(非在 Artifact 上加 nullable 列):彻底隔离 list/inventory 热路径,字节仅在显式 raw-fetch 时 lazy 载入。大小上限 = 隐藏 config 常量(loud-fail,卡在 MySQL 包大小 + 跨中心复制成本之下)。仍走「元数据事件 + REST 取字节」(2026-06-02 既定):`ARTIFACT_CREATED` 对 blob 只发元数据(仿 `content_omitted`),字节经新 `GET …/raw` 端点取。
  - **上传 = 加法,不拆现有转换**:富格式(docx/pdf)既存不可变原始 blob、**又保留**现有 `DocConverter` md 转换作过渡期可读 content/预览,直到 C 的 pandoc-in-sandbox 落地再下线(原则 6「沙盒成熟前保留」)。A 不做 blob-only(否则无沙盒期 docx 不可读)。
  - **识图 = turn 内瞬态,不跨轮重建**(关键简化,推翻原 A「跨轮历史重建时还原」设想):图仅在「读它的那一轮」可见——`read_artifact` 让该轮内存 `state["events"]` 的 tool_result 携图块(本轮后续 LLM 调用都看得到);**持久化事件只存引用**(artifact_id+version+content_type,非字节);`build_event_history` 保持纯函数(零 DB 取字节);下一轮重放只得占位文本 `[image: <id> — re-read to view if needed]`,要再看就再 `read_artifact`。引用→图块的还原放 `context_manager` 对 **turn-local WorkingSet** 做(本轮命中→图块,跨轮 miss→占位)。收益:① `build_event_history` 不被注入 DB 依赖;② 图 token 永不跨轮累积,compaction 天然正确;③ 合原则 4(显式/瞬态/scratch)。
  - **图像格式 + 尺寸**:识图路径**只认 png/jpeg**,其它 `image/*` 上传即拒 + remediation(仿 `DocConverter` 既有 idiom)。**resize-on-read**(原始 blob 不可变,只对注入上下文的 data-URI 降采样)到隐藏常量 `VISION_IMAGE_MAX_EDGE`,token 成本应用侧可控(不靠 provider 的 HF processor 不可控)。Pillow = backend 新依赖 → **DEP-02**(requirements.txt + 在 `python:3.11-slim` 内重生 lock + pip-audit);CPU 纪律(2026-05-14 教训):`Image.MAX_IMAGE_PIXELS` 解压炸弹闸 + `to_thread`,叠加上传字节上限。
  - **身份轻解耦(决策 2)= 现状已满足,A 无需新列**:`_normalize_filename_to_id` 现产出的 id 已是「清洗 + 带扩展名 + 小写 + 去重」的 fs-safe 句柄,可直接当沙盒 on-disk 名(无认知分裂);真实展示名已在 `title` + `metadata.original_filename`,去重只改 id、不动 `original_filename`——决策 2 实质已成立。余项仅:C 的 `mount` 用 id 当 on-disk 名;UI 展示 `original_filename`。
  - **上传图首轮不 auto-inject**:图与 artifact 行为对齐,模型须 `read_artifact` 才看见;提示面改动 = `read_artifact` 工具描述 + inventory 标注图像项(保持 agent-agnostic,不进 lead prompt)。
  - **切片**:A-bin(`ArtifactBlob` 表 + cap + raw 端点 + 元数据-only SSE)→ A-upload(图/富格式 blob,加法)→ A-vision(ToolResult 携图字段 + 引用事件 + `context_manager` 还原 + `context_manager.py:112` 块列表 concat 修复)。无深坑遗留,多次 sitting 完成。
- **2026-06-08 A 实现落地**(三切片 backend + 前端 view 全落;本机单元/集成验过,**待真实 server+LLM 端到端**):
  - **A-bin**:`ArtifactBlob` 独立表(泛型 `LargeBinary(length=100MB)` → MySQL LONGBLOB / PG BYTEA / SQLite BLOB,**零 dialect import**)+ `Artifact.blob` lazy 关系/ORM 级联 + migration `0003` + `repo/service.get_blob` + `GET …/raw`(图 inline、其它 attachment)。
  - **A-upload**:`DocConverter` 收 png/jpeg(Pillow 按**内容**探测 MIME + 解压炸弹闸)、拒其它 `image/*`;docx/pdf additive(原件 blob + md content)。blob 经 `ConvertedUpload`→chat→engine→`create_from_upload`(大小 loud-fail)→`ArtifactMemory.blob`→`create_artifact` **同事务**落 `ArtifactBlob`。`ARTIFACT_CREATED` 只发 blob 元数据、不发字节。
  - **A-vision**:`read_artifact` 图分支 resize-on-read(`utils/image.py`,executor + 炸弹闸)→ data-URI 进 `ToolResult.metadata`;引擎把 data-URI 移入内存 `state["vision_blocks"]`、事件只存**引用**;`event_history` 对照缓存还原——本轮命中→图块、跨轮→占位(**保持纯函数、无 DB IO**);`context_manager` 处理块列表 reminder 拼接 + inventory 图项标注。前端:authed `/raw` fetch→objectURL 的 `ImagePreview`,图 artifact 路由到 preview-only tab(上传无需改 —— 本就无 `accept` 过滤)。
  - **依赖**:Pillow 入 requirements + lock(12.2.0);顺带修 aiohttp CVE(3.13.5→3.14.1,pip-audit 全绿)。
  - **验证**:读→事件→历史→build 全链(turn 内图块 / 跨轮占位)、前端 tsc、**181 后端测试全过**。**未做**:真实 server+LLM 上传真图→模型识图的 live 回归。(原列「mid-turn panel 渲染待 flush」「本地缩略图」两项,已于 2026-06-09 review 加固一并补上,见下条。)
- **2026-06-09 A review 修复 + mid-turn 上传 UX 加固**(外部 review 过、无阻塞;`feat/sandbox` commits `f7b7d4d`→`3476e5e`):
  - **识图能力门控**(review P1):默认 agent 用文本档 `qwen3.7-max`,`read_artifact` 却无条件注图块 → provider 拒。改:`models.yaml` 加 `vision` 标志 + `model_supports_vision`,`build_event_history(vision_capable=)` 由 `context_manager` 按 agent 模型传入——仅识图模型注图块,否则占位「你非多模态、看不到图」;**lead/research/compact 切 `qwen3.7-plus`**(识图 out of box)。
  - **解压炸弹闸改显式像素上限**(review P2,**取代** A-vision 原依赖 Pillow `MAX_IMAGE_PIXELS` 的写法):Pillow 89–178M 像素段只 warn 不抛,小文件大像素图绕过;改隐藏常量 `VISION_IMAGE_MAX_PIXELS`(50MP),**解码前**(`Image.open` 只读头)校验 `w*h`,上传侧拒 + read 侧防御。
  - **LLM 重试收窄**(review):`astream_with_retry` 旧按 `str(e)` 子串匹配、除 auth 外全重试(BadRequest/400 图块、ContextWindow 都白重试 3 次)→ 改 litellm **类型化异常**,仅瞬态(网络/超时/429/5xx)重试,其余立即 loud-fail。
  - **debug 格式化容忍块列表**(review P1):`format_messages_for_debug` 旧假设 content 是 str、遇图块列表 `.split()` 崩(且每轮 eager 求值)→ 块列表压成摘要(不吐 base64)+ `logger.debug_mode` 守卫。
  - **mid-turn 上传图本地优先渲染**(取代原「未做」两项):图 artifact auto-open 后 `/raw` 在 flush 前 404、旧版把后端原始错误串(含 req-id)吐给用户像系统坏了。改:`ARTIFACT_CREATED` 带 `original_filename`,前端按名关联 composer 仍持有的 staged `File` 本地直接渲染;本轮生成、无本地副本的图(未来工具产物)显示「生成中」提示而非报错;真实失败显示干净文案(req-id 进日志)。chip 显示图片缩略图。
  - **同名图串图修复**(review P2 + 跨轮变体):暂存区文件名 `_N` dedup(修同轮)+ 本地预览限定 `pendingFlush` 本轮(修跨轮——往轮 artifact 一律读自己 DB blob,不被后续轮同名 staged File 串掉)。双管缺一不可。
  - **验证**:后端 **1095 passed**(+ 识图门控 / 重试瞬态-vs-确定性 / 像素闸 / 事件 `original_filename` 回归)、前端 **191 passed** + tsc/lint clean。**live E2E:用户实测通过**(2026-06-09,真实 server+LLM 上传真图→模型识图)。**A 阶段完成。**

### B — Kylin gVisor 功能验证(内网,验完即撤出)

**做什么**:在健康 Kylin 节点上把 gVisor 这侧的不确定性一次清掉,产出一个**冻结的沙盒镜像**和确定的 runtime 配置。之后引擎集成回本机用 runc 做(见原则 2)。

**验什么**:
- **真实数据科学 workload 在 runsc 下不踩 ENOSYS**(numpy / pandas / matplotlib 出图 / Pillow / openpyxl / pdf 解析)。**这是最大风险**,也是"保留 Firecracker 作后手"的依据来源——必须用真实负载验,而非 `echo`。每个库**独立 try 段**,踩 ENOSYS 时打印是哪个库哪个 syscall(回退判断的依据)。头号风险是 C 扩展项(numpy/pandas/matplotlib/Pillow);openpyxl/pypdf 纯 Python、风险低,报告要能区分,别让纯 Python 的 PASS 掩盖 C 扩展项。
- **ripgrep(tier-1 工具,顺手当 FS 遍历探针)**:它是静态 Rust 二进制,syscall 画像与数值 C 扩展不同——密集 `getdents64 / statx / openat` + 线程(`clone/futex`),覆盖**文件系统遍历**这块 Sentry 兼容面(理论低风险,但 5MB 二进制顺手验掉胜过假设)。与 bind-mount 项二合一(`rg` 一个挂入目录)。
- **pandoc 金丝雀(docx/html ↔ md)**:作为 Word↔Markdown 互转工具一并验。它是静态 Haskell 二进制 + 重文件 IO,比纯 Python 多覆盖 exec/文件这块的 Sentry 兼容性,顺带当金丝雀。PDF 输出(需 LaTeX 引擎)本期不管。
- **离线依赖投递机制(验原则 7 的 tier 2/3 投递路径)**:挂一个 stub wheel 目录到固定位,在 runsc 沙盒内跑 `pip install --no-index --find-links <dir> <一个纯 Python 包>`,确认 pip 离线安装(纯文件 IO、无网)在 Sentry 下行为正常。便宜地**提前验掉 skill-asset 依赖路径**,免得 skill 架构落地才发现 offline-install 在 gVisor 下有坑。
- **bind-mount 文件往返 + uid 映射**:沙盒内写文件、host 读回、权限/属主正确(gofer 文件系统上的 `getdents/statx` 路径与容器内 tmpfs 不同,单独验)。
- **网络策略 = 默认全禁网(`--network=none`,见原则 7),保留 allowlist 作退路**:验 ① none 下确无任何 egress;② **allowlist 到单一 stub host** 时只该 host 可达、公网仍封(退路验证:将来离线 bundle 太重时,可放行单台内部镜像在线装包,故 `verify-network.sh` 保留此分支);③ runsc 自带 netstack,DNS 解析路径与 runc 不同,**实测** DNS 行为。

**镜像内容(tier-1 定稿)**:`python:3.11-slim` + numpy / pandas / matplotlib / Pillow / openpyxl / pypdf + `apt: pandoc` + ripgrep。与 backend 的 `requirements.lock` **解耦**(沙盒是独立 runtime,非 backend 镜像,别共用 app lock),自带一份 `sandbox-wheels.lock.txt` 记可复现的传递依赖集(仿 analyst-tools 的 `wheels.lock` 范式)。

**完成 =** 冻结镜像(`docker save` tar + image digest 作冻结锚点)+ 文档化 runtime 配置(daemon.json 的 runsc 注册 + 固定 `docker run` 参数面:`--runtime=runsc` / 资源配额 / `--network=none` / uid 映射)。部署前预检 `sudo unshare -U /bin/true`,BLOCKED 节点禁入沙盒服务池(详见 gVisor 评估文档 §5)。验完即撤:`uninstall.sh` 卸 runsc + `docker rmi` 沙盒镜像 + `systemctl reload docker`,不留 runsc 在测试机。

**准备(介质与脚本,后续 refer 的落点)**:内网测试机不能联网 ⇒ 一切在有网构建机烤进 tar,内网只 `docker load` / `install.sh`、零网络。
- **介质**:① gVisor 离线包(装 runsc)—— **原 `dist/sandbox-gvisor-20260512.tar.gz` 已被删、且其 install/smoke/uninstall 脚本只存在于该 tar 内(dist/ 全 git-ignore),未入 repo → 需重建**:`runsc`+shim 从评估文档 §3 的 URL(`release-20260504.0`)重下,三个脚本按评估文档附录 A 规格重写;② **沙盒镜像 tar**(本期最大新介质,内容见上);③ 验证脚本(建议直接烤进镜像 `/opt/verify/`,与库版本同源;host 侧编排脚本单独带);④ **测试 fixture 无需携带任何二进制**——docx 用 pandoc 自身 `md→docx`(正好是金丝雀)、html 内联字符串、xlsx 用 openpyxl(本就是被测项)、png 用 Pillow、**pdf 用 matplotlib `savefig('.pdf')` 矢量后端生成**(不需 LaTeX,与"PDF 输出 out-of-scope"不冲突)再回喂 pypdf。气隙网零外部样本。
- **脚本**:(a) 构建机在线 —— `Dockerfile.sandbox` + `build-sandbox-image.sh`(照搬 release.sh 的 buildx/save 段)。**构建机定 = 本机 Mac + QEMU**,故脚本须带 `docker buildx --platform linux/amd64`(否则 arm64 → 内网 `exec format error`,release.sh 已踩);numpy/pandas/matplotlib 跨架构拉取慢,脚本加耗时预期 + 失败重试提示(对照 `release-build-proxy-flap`)。(b) 容器内(真实负载 = Tier 6,接在 gVisor 包 `smoke-test.sh` 的 Tier 1–5 之上、不重复)—— `verify-enosys.py`、`verify-pandoc.sh`、offline find-links 探针。(c) host 侧 —— `run-all.sh`(`docker run --runtime=runsc` 编排各探针收报告)、`verify-bindmount.sh`(含 ripgrep 二合一)、`verify-network.sh`。

**进展**(已落地实现 + 后续 refer 的落点):
- 2026-06-04 工具链落地于分支 `feat/sandbox`,本机 runc 彩排全绿。文件:
  - `sandbox/Dockerfile` + `sandbox/requirements.txt` —— tier-1 镜像。**已固化决策**:① deps 与 backend `requirements.lock` **解耦**(沙盒是独立 runtime);② 跑非 root `sandbox`(uid **1000**,固定值供 bind-mount uid 断言);③ `MPLBACKEND=Agg`;④ `pip freeze`→`/opt/sandbox-wheels.lock.txt`;⑤ offline-install stub wheel 烤进 `/opt/stub-wheels`(源 `sandbox/stub-pkg/`)。pin:numpy1.26.4/pandas2.2.3/matplotlib3.9.2/Pillow10.4.0/openpyxl3.1.5/pypdf4.3.1。
  - `scripts/build-sandbox-image.sh` —— 仿 release.sh:buildx `--platform linux/amd64`→`docker save`→`dist/artifactflow-sandbox-<date>.tar.gz` + `.sha256`/`.wheels.lock`/`.manifest.txt`(manifest 里的 **image id = C 阶段构建所依赖的冻结锚点**)。
  - `sandbox/gvisor-pkg/` —— 重建的 gVisor 包(原 dist tar 已删、脚本从未入 repo)。`fetch-and-package.sh`(构建机从评估文档 §3 URL 重下 runsc `release-20260504.0`→出 tar,二进制仍 git-ignore、脚本入 repo)、`install.sh`/`smoke-test.sh`(Tier0=`unshare -U` 闸门,失败即停)/`uninstall.sh`。
  - `sandbox/verify/` —— 五探针 + `run-all.sh`。**验证脚本走 bind-mount 进 `/opt/verify`(非烤进镜像)**:B 期可改即跑、且顺带验 bind-mount 路径;stub wheel 则必须烤进(离线 fixture)。
  - `sandbox/README.md` —— 内网一次过 runbook(装 gVisor→烟测→load 镜像→`run-all.sh`→撤)。
- **彩排范围**:native-arch build + `RUNTIME=runc bash run-all.sh` 全绿(ENOSYS 7/7、pandoc 3/3、offline-install、bind-mount+ripgrep)。network 三档需内网用真实内部 host 填 `PROBE_HOST`/`PROBE_NAME` 才实测(逻辑已通,本机 skip)。
- **介质 = 三个传输单元**(2026-06-04 上 milvus2 时踩出):gVisor 包 tar、镜像 tar、**verify tar**。探针**不烤进镜像**(host 侧 bindmount/network/run-all 必须在宿主跑),故 `sandbox/verify/` 自带一个 tar(`build-sandbox-image.sh` 现一并产出 `artifactflow-sandbox-verify-<date>.tar.gz`),内网 `tar xzf` 出 `./verify/` 再 `run-all.sh`。另:`docker save` 现同存 `:latest`(否则 load 后只有 `:<date>`,smoke/run-all 默认 `:latest` 落空——本期镜像只有 `:<date>`,须显式传 tag)。
- **已知差异**:`verify-bindmount.sh` 的 uid 用 GNU `stat -c`,Mac 彩排显示 `?` 属 host 工具差异,Linux 目标(Kylin)正常。
- **进度**:amd64 镜像 tar(image id `sha256:3b43b839…`,arch=amd64,17 deps)+ gVisor 包 tar 均已出并校验;**milvus2 已装 gVisor,smoke Tier0–3 全 ✓**(unshare -U / runsc / systrap+kvm / runsc 注册)——milvus2 = 健康参考节点。
- **2026-06-05 milvus2 `run-all.sh` 全绿,B 验收通过**(`IMAGE=artifactflow-sandbox:20260604`,RUNTIME=runsc):
  - **ENOSYS 7/7 PASS** —— numpy/pandas/matplotlib(PNG+PDF)/Pillow 这 4 个 C 扩展在 Sentry 下零 ENOSYS,openpyxl/pypdf(纯 Python)亦过。**核心赌注赢:gVisor-as-MVP 成立,不回退 Firecracker。**
  - **bind-mount + uid 3/3** —— 容器写→宿主读回,**host 侧属主 uid = 1000(无 remap)**,ripgrep 在 gofer 挂载上 getdents/statx 正常。
  - pandoc 3/3(docx/html↔md)、offline-install ✓(`--no-index --find-links` 在 runsc 下成立)。
  - **network 2/3**:`--network=none` 正确隔离(111.1.30.17:22 Errno101 BLOCKED)✓、bridge 出网可达 ✓;**DNS-under-runsc 判 N/A 而非失败** —— 测试内网无 DNS 服务器(容器 `/etc/resolv.conf` 空,milvus2 宿主纯靠 `/etc/hosts`),换 runc 同样无从解析,与 gVisor 无关。生产默认 `--network=none` 不需 DNS;allowlist 回退按 IP 放行亦不涉及 DNS。**follow-up(非 B 阻塞)**:若将来 allowlist 要按**域名**放行,需在一台**有内网 DNS**的节点补验 runsc netstack 的容器内解析。
- **冻结锚点(C 阶段构建依此)**:image id `sha256:3b43b83999c2547f84ff9b0b93d0861d0c9a854d9046e54b6c623750c0e57421`(`dist/artifactflow-sandbox-20260604.manifest.txt`,platform linux/amd64,built 2026-06-04T08:25:20Z)。**固定 runtime 配置**:daemon.json 注册 runsc(`install.sh` 合并)+ `docker run --runtime=runsc --network=none` + uid 1000 工作区 + 资源配额(C 阶段定额)。部署前预检 `sudo unshare -U /bin/true`,BLOCKED 节点禁入服务池。
- **撤出(2026-06-05 已执行)**:`uninstall.sh` 卸 runsc + `systemctl reload docker` + `docker rmi artifactflow-sandbox:20260604`,milvus2 零残留。B(x86_64)闭环。

**进展 · arm(鲲鹏)** —— 2026-06-05 起,补验第二 arch:
- **动因**:§B 的 ENOSYS/平台结论是 **per-arch、不迁移**(Sentry syscall 覆盖、KVM/systrap 行为按架构不同),环境里有昇腾/鲲鹏现实 ⇒ x86 验过不代表 arm 成立,必须独立重跑。
- **目标机**:2×Kylin V10 arm 16c/32G,**bare 机(docker 都没有)** ⇒ 比 x86 那趟多一层"离线装 docker"。
- **脚本已 arch 化**(本次):`build-sandbox-image.sh` 凭 `PLATFORM`、`gvisor-pkg/fetch-and-package.sh` 凭 `ARCH` 产出**带 arch 后缀**的产物(`-amd64`/`-arm64`/`-aarch64`),x86 与 arm 两套并存不覆盖;verify tar arch 无关、共享。arm64 在 Apple Silicon 上**原生构建**(比 amd64 的 QEMU 快)。
- **新增 `sandbox/docker-pkg/`**:静态二进制离线装 docker engine+compose(bare 节点前置),`install.sh` 写 systemd units + 起 dockerd,README 记 Kylin 坑(SELinux/overlay/iptables)。**bare 节点供给顺序**:docker-pkg → gvisor-pkg → smoke → load 镜像 → run-all。
- **传输单元(arm 趟,4 个)**:docker-offline tar、gVisor tar、镜像 tar(arm64)、verify tar(共享)。
- **上机结果(2026-06-08)**:发现 **arm 阻断 = 64K 页** —— Kylin V10 SP3 鲲鹏默认内核按 64K 页编译(`getconf PAGE_SIZE`=65536),gVisor Sentry 拒起(`host page size mismatch - running on non-4K host`),smoke Tier 2(systrap)/4/5 全挂。**这是 per-arch 真阻断,非 x86 那种 DNS 伪失败。**
- **解 = 在位换 4K 内核(不重建实例、不需 KVM)**:厂商把 4K 页内核作独立 RPM 集供(`update.cs2c.com.cn/CS/V10/V10SP3-2403/kernel-4k/`,非另封 ISO)。判别确认该实例是「镜像内自带内核 + GRUB 启动」(`/proc/cmdline` 有 `BOOT_IMAGE=`、UEFI、根在 LVM)⇒ 装 4K 内核 RPM + grubby 设默认 + 重启即生效,老 64K 内核留作回滚。gVisor 的 systrap 平台是用户态(ptrace),**不需要 `/dev/kvm`**,纯 VM 即可。
- **新增 `sandbox/kernel-4k-pkg/`**:离线 4K 内核包,`fetch-and-package.sh` 重现下载 4 个 boot-essential RPM(core/modules/modules-extra/meta)+ 三段运维脚本——`preflight.sh`(只读门禁:arch / 页大小 / `BOOT_IMAGE` 自带内核 / grubby / 介质校验)→ `install.sh`(并存装 + 确保 initramfs + grubby 设默认,**不自动重启**)→ `postcheck.sh`(重启后验 `PAGE_SIZE=4096`)。LVM 根必须确认 `initramfs-...4k.img` 已生成再重启(脚本会查),故装与重启分离。
- **验收(2026-06-08,`89.11(64K) → 89.38.4k`)**:smoke 5/5;`run-all.sh` **全绿** —— ENOSYS **7/7**(numpy/pandas/matplotlib(PNG+PDF)/Pillow 五个 C-ext 无 syscall 缺口,核心赌注 arm 上也赢)、pandoc 3/3、offline-install ✓、bind-mount 3/3(host 属主 uid=1000)、network 2 pass/1 skip(`--network=none` 隔离生效 + bridge egress 通;DNS 因内网无 DNS server 而 skip,同 x86 判 N/A)。**arm §B 闭环,与 x86 对齐。**
- **结论**:gVisor-as-MVP 在鲲鹏 arm **成立**,前置 = 目标节点跑 4K 页内核(一次性换核、可回滚);ENOSYS 未现 C-ext 缺口 ⇒ arm 无需走 Firecracker 回退。arm 镜像 id 冻结待操作;**两台 arm 不撤**(`ai-agent-app` 等),留作**沙盒版应用落地机** + 双机高可用验证(与 x86「验完即撤」不同——x86 那趟是借机验证,arm 这两台是目标部署机)。

### C — 沙盒引擎集成(本机 runc 连调;依赖 A 的二进制存储 + B 的镜像)

**做什么**:把沙盒接进引擎,给 agent **三个分立的模型面工具**(`bash` / `mount` / `persist`),底下共享**一个 per-turn `SandboxSession`**。

**工具面 = 三个动词,实现 = 一个共享 session**(拍定 2026-06-03):三者是语义不同的动词、参数形状各异(bash 吃 `command`、mount 吃 `artifact_id`、persist 吃 `path`+命名/zip),分立比合成单一 `sandbox(action=...)` 工具参数面更小、对小模型更可读,也与现有 `*_artifact` 工具 idiom 及 per-verb 权限粒度一致(见原则:Minimize tool parameter surface)。「共用启动/沙盒交互」是**实现层**事实(本就该有),不构成合并工具面的理由。`SandboxSession`(per-turn)owns 容器生命周期 + bind-mount 工作区/uid 映射 + reap 注册 + 绑定本 turn 的 `ArtifactWorkingSet`;三个工具都是其上的薄操作。推论:**lazy 创建 key 在「首个沙盒工具调用」而非「首个 bash」**——模型可能先 mount 再 bash。

**包含**:
- **容器生命周期(生 lazy / 灭跟 lease 同层)**:per-turn ephemeral,绑 message_id。
  - **生 = lazy**:首个沙盒工具调用时才起容器(多数 turn 不开沙盒,eager 等于在多数 turn 上空转创建+销毁 runsc),本 turn 内后续调用复用同一 scratch 工作区;创建失败 → **tool 级 loud-fail**,该 turn 沙盒工具不可用(模型据 tool result 改道),不拖垮非沙盒工作。
  - **灭 = 跟 lease 同一层**:bash 本身 IO 等待、天然可取消,**但容器在协程被取消后仍在烧 CPU**——拆除必须挂执行器 `_wrapped` 的 `finally`(`cleanup_execution` 隔壁),它是**真 `finally`**,成功 / 超时 / 协作取消 / 外部取消 / 崩溃五条退出都在解栈时执行,与 lease 释放同生灭(容器与 lease 都是绑 turn 的易失态,贯穿原则 1)。**绝不放 post-processing**——那是被设计成可被 late-cancel 抢占、靠重试补的区域,其单发 `except CancelledError` 恢复是"尽力补审计记录",兜不住会烧 CPU 的活资源;漏拆 = 孤儿容器(2026-05-14 同款失效模式)。
  - **进程死亡的二级兜底 = lease-anchored reap**:worker 被 SIGKILL / OOM 时 `finally` 不执行,容器归 daemon(DooD)不随 worker 死 → 孤儿继续烧 CPU。**lease 是唯一 liveness 真相源**(它恰在"turn 合法地在活 worker 上跑"期间被持续续租,正是容器应活的充要条件,故零误杀、无需猜固定余量)。reaper 启动时 + 定期把**带 label 的活容器集**与 `list_active_executions` 做**差集**,差集即孤儿 → `docker rm -f`。复用 `list_active_executions` 既有的 Cluster-safe scan+pipeline fan-out,**别 N 个容器各查一次 lease**。
    - **对账粒度必须 per-turn**:label 带到 turn 粒度(`af-sandbox-{conversation_id}-{message_id}` / 绑 task_id),对账问的是"**这个 message_id/task 还在 `list_active_executions` 里吗**"——**不能按会话**查,否则同会话紧接的新 turn 持着活 lease,会让上一 turn 漏拆的孤儿被误判"有主"而永不回收。
    - **自觉取舍**:最坏烧 CPU 时长 = lease TTL 剩余 + reaper 间隔(有界,~分钟级,对照 2026-05-14 的 96 分钟可忽略);加小 grace(只 reap 已存活 > N 秒且无对应活跃执行的容器)躲开"刚 lazy 创建、执行注册可见性差一拍"的竞态;reap 顺带兜住"创建到一半被 cancel、handle 未注册"的漏网(它以 daemon 为真相源、按 label 查,不靠内存句柄)。
    - **残留洞(决定要不要保留固定上限)**:reap 起效前提是"有活 reaper 能扫到孤儿所在 daemon"。worker 崩后重启(启动扫)/ 同 daemon 有活 sibling(周期扫)都覆盖;唯独"一 worker 独占一 daemon 且死不重启"够不着——**仅此场景**才需保留一个宽松固定上限(容器内 `timeout`+`--rm`)当最后兜底。是否需要取决于部署拓扑,留 TODO 到 B/D 阶段按真实拓扑拍。
- **bash 工具**:CONFIRM 权限(跑不可信代码)。
- **持久化工具**(模型显式调用,描述 present 给用户):落实决策 3 的回写二分,文件数与字节数上限兜底。
- **挂载(显式 stage 进出)**:模型显式把指定 artifact 物化进工作区(zip 作 blob 进、容器内解压),回写也显式;不自动物化整 session。见原则 4 的定性。
- **DooD + 配额**:backend 挂 docker.sock,经 **aiodocker**(Docker daemon HTTP API 的 asyncio 原生客户端——选它因容器生灭要 `await`、直接挂进引擎的 `asyncio.Task.cancel()` 取消/超时/lease 栈,而 `docker` CLI/同步 `docker-py` 与之对不上)起沙盒;资源配额(内存 / CPU / pids / `--network=none`,见原则 7;**磁盘 = loop 池子硬墙 + watchdog 软配额**,2026-06-10 review 收口定,见进展);**容器创建参数不可被模型生成内容污染**(镜像/挂载/runtime 固定在代码侧)。
  - **编排器可换性(收口在 `SandboxSession`)**:gVisor 是 OCI runtime、**不挑编排器**(containerd / CRI-O / k8s RuntimeClass=gvisor / podman 均可)。两个可换轴量级不同:**runc↔runsc = 一个 config 开关**(原则 2);**Docker↔k8s = 换控制面 client**(aiodocker → k8s API,per-turn 容器 → per-turn Pod,reap/socket-root 全重画)。当前单机 DooD 形态**不做 k8s**,但 aiodocker 调用须**收在 `SandboxSession` 这一个 seam 后**、不散进引擎——将来真上 k8s 只换该层,引擎无感(YAGNI:现在只保 seam、不抽象)。
    - **「应用与沙盒分机部署」并入此轴、有需求再做**(2026-06-05):分机不是改 aiodocker 连接串能办的——真正的耦合是 **bind-mount 工作区 daemon-local**(路径在 daemon 那台机解析,不在客户端机;uid 属主断言同理),aiodocker 本身可连远程 TCP+TLS daemon,但单远程 daemon = 无调度/无故障转移、bind-mount 还是断。**正解是 k8s**(per-turn Pod + volume/PVC stage 替代宿主 bind-mount),即本条上面那根「换控制面」轴。两个可能驱动都推迟到真有需求:① 专用沙盒主机池(隔离烧 CPU 的不可信执行);② 切掉「应用机经 docker.sock 拿 host root」的爆炸半径(正当安全驱动,但代价仍是换控制面)。在此之前结论不变:单机 DooD、seam 留着。
- **文档转换走沙盒**:pandoc 装进沙盒镜像(B 验过),富格式读(docx→md)和写都由 agent 在沙盒里跑 pandoc。**驱动场景**:用户要带格式的 Word 时,模型以用户上传/原有 docx 作 `--reference-doc` 样式模版,在沙盒里 md→docx 生成,产物回写成可下载 blob——比固定的 md→docx 导出保真,可能取代现有的 md→Word 导出路径。**门控变化(衔接 artifact plan 决策 6)**:现有 `/export` 是同步 REST 读,turn 中按「前端 UX 锁的读」处理;一旦导出搬进沙盒 = 起容器 = **执行**,就从读升级为 **lease 挡的写/执行**(跟 bash 工具同级),门控责任从前端移到后端 lease。替换 md→Word 路径时一并改门控,别留前端旧锁。

**到时再敲定**:并发上限;persist 多文件 zip 的命名与"可单独查看"白名单。(原**上传路由翻转**已于 2026-06-11 落地,含 magic 闸移除决策,见「进展」与变更日志;原"挂哪些 artifact:全部 vs 被引用"已由原则 4 的显式 mount 关闭——模型 mount 谁就有谁;原"沙盒工具是否合并"已拍定分立三工具 + 共享 `SandboxSession`,见上;原"bash 输出溢出"已拍定复用既有 idiom,见下「进展」。)

**进展**:
- **2026-06-10 C 开工决策锁定**(集成点已逐一核实存在:`_wrapped` finally=`execution_runner.py` cleanup_execution 旁、`list_active_executions` 返回 `{conv_id: message_id}` 故 per-turn 对账零新接口、工具构造注入仿 `create_artifact_tools`、CONFIRM 仿 `web_fetch`):
  - **切片顺序**:**C-0**(blob-only 上传一步到位:删 docx/pdf→md 自动转换 + **整删 `/export` md→Word**,先行、不依赖沙盒代码)→ **C-session**(`SandboxSession` 壳 + aiodocker + finally 拆除 plumbing + `bash` 工具 + `SANDBOX_*` 常量,跑无孤儿矩阵)→ **C-stage**(`mount`/`persist`)→ **C-reap**(lease-anchored reaper)→ **C-wire**(agent MD 权限、工具描述、文档)。
  - **blob-only 一步到位(C-0),不留过渡双轨**:中间态(同一 artifact 既有 md content 又有 blob)的唯一受害者是模型认知与 E2E 调试,受益人不存在(feat/sandbox 未合 main、mount/pandoc 调试走 blob 路径用不上 md)。上传按格式二分:文本类→content(可编辑、版本化),二进制类→blob-only(png/jpeg 识图不动);`read_artifact` 对 blob-only 给契约文案("binary,mount 进沙盒操作");前端预览退化为下载入口(文本下载=content,blob 下载=既有 `/raw`)。
  - **`/export` 整删,非搬沙盒**:原"门控升级(读→lease 挡执行)"账随之消失——agent 在沙盒生成 docx 走 bash,天然 lease 挡。**代价进验收**:「成熟」定义新增——"用户要带格式 Word"场景须由 agent 流程(mount 原 docx → pandoc `--reference-doc` → persist)在 D/live 真实跑通,merge 后无一键导出按钮,替代物必须先站住。
  - **persist 永远产新 artifact,blob 不版本化**(回答 `ArtifactBlob` docstring 留的问号):二进制 artifact 契约=不可变单版,改=persist 新建(`_N` dedup 已有);文本=可编辑版本化。否决 blob 版本化(glance 态不值强一致机器)与 blob 覆写(毁"原 blob=不可变源+`--reference-doc` 样式模版"一物两用)。
  - **mount 语义(blob-only 后每 artifact 单一权威载体)**:文本→WorkingSet overlay(本轮 dirty/new 必须可 mount,直读 DB 是空的);blob→DB `get_blob`(唯一例外:本轮 staged 上传读 `ArtifactMemory.blob`)。格式判别=有无 blob,无需白名单。
  - **mount 返回纯事实,提示分层**:返回值只报物化清单(容器内路径/字节/MIME);"binary 须 mount 操作"的**契约**文案进 inventory 标注 + `read_artifact` 拒绝文案(仿 A 图像项);**环境能力**清单(pandoc/git/科学栈)一次性进 bash 工具描述;**场景 how-to**(reference-doc 用法等)留 skill 系统,不做 per-type 提示表。
  - **拆除 plumbing(C 阶段唯一真架构决策)**:Session **对象壳** per-request 在 controller_factory 创建(同 ArtifactService,构造注入三工具),**容器** lazy 于首个沙盒工具调用;cleanup 句柄从 factory 递到 `execution_runner._wrapped` 的 finally(`cleanup_execution` 旁),无沙盒 turn = 空列表零成本。
  - **per-command 超时=容器内 `timeout` 包 argv**:exec API 收 argv 数组(`["timeout","--signal=KILL",N,"bash","-c",cmd]`),cmd 整体一个 argv 元素、无宿主侧 shell、无引号问题;tool 侧 asyncio 超时只负责提前返回(await 弃等 ≠ 进程死,2026-05-14 同型)。后台逃逸进程由 turn 末拆容器兜底。
  - **reaper 枚举方向=资源侧,lease=减法掩码**:孤儿定义=无 lease 的容器,从 Redis 出发永远发现不了;正确=daemon `ps --filter label` 枚举 − `list_active_executions` 活跃集。**scratch 目录是第二类残留**(容器先没了目录还在:mkdir 后/起容器前被 SIGKILL、`--rm` 自删、daemon 重启),需第二枚举源=scratch 根目录列目录,同一 lease 差集谓词、目录名同带 `{conv}-{msg}`。**scratch 根枚举须遵 fd 钉住纪律**(2026-06-10 第 3 轮收口外推):只列**根目录直属条目名**做 label 反解差集即可,不要按名字递归进子目录(活跃 turn 的容器正在写自己的 scratch,子目录可被换成池外链——同 watchdog 目录 TOCTOU);要看子目录就走 `sandbox_fs`(第 5 轮已把 fd 钉住 + fail-closed 收成模块,C-reap 直接复用 / 在其上加单层枚举,绝不另手写 `os.walk`/按 `entry.path` 重扫)。
  - **bash 输出溢出**:复用 `max_result_size_chars` + `_maybe_persist_tool_result` 溢出转 artifact idiom,引擎零改动。
  - **镜像加 git**(tier ①:通用、环境定义、无网无法现装):apt 入 `sandbox/Dockerfile` + 烤默认 git identity(无 user.name `commit` 即报错)+ `sandbox/verify/` 加探针(init/add/commit/diff);用途=本地仓库操作(zip 解压后 log/diff/blame/apply),`--network=none` 下 clone/fetch 死属 by design。**B 冻结锚点 `sha256:3b43b839…` 作废**,D 重跑 `run-all.sh` 重新冻结(本就既定动作,不为 git 单独回内网)。**已落地(2026-06-11,见变更日志)。**
  - **依赖**:aiodocker 入 requirements → DEP-02(slim 内重生 lock + pip-audit);exec multiplexed stream demux 自验。
- **2026-06-10 C-0 落地**(blob-only 一步到位,后端 1098 / 前端 205 测试全过 + tsc/lint clean):
  - **后端**:`DocConverter` docx/pdf 改 blob-only(content="" + content_type=真实 MIME + magic 预检 loud-fail,pdf 补 `%PDF-` 闸对齐 docx);pandoc 退出 backend(删 `export_docx`/`check_pandoc`,main.py 不再依赖);**pymupdf 文本抽取保留为独立 `extract_pdf_text` 供 web_fetch PDF 降级**(网页阅读路径,非上传,convert() 不再可达);删 `/export` 端点(连 `Query` import);`read_artifact` 序列化加 `blob_content_type` 判别字段 → ReadArtifactTool 对非图片 blob 返回契约文案(**success=True 防重试循环**)、inventory 二进制项标注;**新增 `_binary_immutable_error` 守门**——update/rewrite 在 blob artifact 上一律拒(否则文本编辑会让"双轨"借尸还魂),改=产新 artifact。
  - **REST/前端**:`ArtifactSummary`/`ArtifactResponse` 加 `has_blob`(由 `blob_content_type` 推导,OpenAPI types 重生成);事件 `ArtifactCreatedData` 补 `has_blob`/`blob_size`/`blob_content_type` 类型;store `LiveArtifact.hasBlob` 全链贯通(`defaultViewMode` 收 hasBlob);删 `exportArtifact`/「导出为 Word」菜单;下载原格式按 `has_blob` 分流(blob → authed `/raw` objectURL,文件名用 `original_filename`);新增 `BinaryFilePreview`(文件卡片 + 下载),**turn 内复用 ImagePreview 的 `pendingFlush` 闸**——卡片只靠事件元数据即可渲染(filename/MIME),仅下载按钮 flush 前换成「本回合完成后可下载」提示,不发 /raw、无 404;blob artifact 隐藏复制按钮、tabs 限 preview-only。
  - **~~TODO(C-wire)~~ 已完成(2026-06-11)**:read_artifact 契约文案 + inventory 二进制项已从「只可下载」升级为「mount 进沙盒检视/转换,或下载」,代码 TODO 注释清空。
- **2026-06-10 C-session 落地**(后端 1136 测试全过 + **本机 runc 真机无孤儿矩阵 6/6 双零残留**):
  - **`SandboxSession`**(`src/tools/builtin/sandbox_session.py`):per-turn 对象壳,aiodocker 全收口在此 seam(编排器可换性);容器 lazy 于首个 exec(壳零成本:不碰 docker、不建目录);创建参数全代码侧(`--network=none`、Memory=MemorySwap 禁 swap、NanoCpus、PidsLimit、Runtime 按 `SANDBOX_RUNTIME`);label 到 turn 粒度(`artifactflow.sandbox.{conversation,message}-id` + **namespace label**=`REDIS_KEY_PREFIX`,隔离共用 daemon 的多套部署,C-reap 按它过滤);scratch 目录 `{root}/{conv}__{msg}`(reaper 第二枚举源按此反解)。**创建失败 sticky**:本 turn 不重试,后续调用立即复述原因(失败多为环境性,重试只重复烧启动超时);**create 成功 start 失败的半成品句柄先记**,close 仍删得到。`close()` 幂等、每步独立 best-effort(容器→scratch→client),任一步失败只记日志等 reaper。
  - **exec = 容器内 `timeout --signal=KILL` 包 argv**(真机验证 exit 137、3.0s 准点杀);tool 侧 `asyncio.timeout(命令上限+30s grace)` 弃等护栏只兜 exec 通道卡死。输出 stdout/stderr 按到达序合流、**每流独立 incremental decoder**(frame 劈断多字节字符不出 �)、超 `SANDBOX_MAX_OUTPUT_CHARS`(200k)继续 drain 但丢弃+显式截断标记;ExitCode EOF 后有界轮询(daemon 落账延迟)。
  - **`bash` 工具**(`sandbox_ops.py`,CONFIRM):唯一参数 `command`;**非零退出码 = 信息不是故障**(success=True + `[exit code: N]`,grep 无命中不该触发失败语义);exit 137 按时长归因(≥上限才标 timeout 杀,避免误归因 OOM-kill);>50k 由引擎溢出转 artifact idiom 接手(引擎零改动)。`bash` 入 `RESERVED_TOOL_NAMES`。
  - **拆除 plumbing**:`ExecutionRunner.register_cleanup(task_id, cb)` 注册表;`_wrapped` 真 finally 在 `cleanup_execution` **之前**逐个 best-effort 执行(**先拆资源后放 lease**,"无 lease 即孤儿"谓词无窗口;CancelledError 也只记日志继续,绝不跳过 lease 释放/close_stream);controller_factory 创建壳 + 注册 + `create_sandbox_tools` 合入 all_tools(close 不依赖 DB session,晚于 factory context 退出安全)。
  - **矩阵**(`tests/manual/sandbox_no_orphan_matrix.py`,真 daemon):正常路径(顺带自验 exec multiplexed demux + workspace 跨调用持久)/ while-true 真杀 / exec 中取消 / 起容器中取消 / runner 成功+外部取消,每条断言 daemon 无 label 容器 + scratch 已删。SIGKILL worker 条留 C-reap。
  - **依赖**:aiodocker==0.27.0 入 lock(slim 内重生,pip-audit 零漏洞)。
  - 单测:session(fake aiodocker:lazy/配置/sticky 失败/close 幂等/截断/demux 解码)+ bash 契约 + runner cleanup 注册表(成功/异常/取消三路径 + 顺序断言)。
- **2026-06-10 C-session review 收口**(外部 review 两条有效发现 + 一确认):
  - **[P1 已修] cleanup 回调有界弃等**:`container.delete()` 走 aiohttp 默认无超时,daemon/socket 卡死时 cleanup 永不返回 → Redis 下 stream 不关/任务泄漏、InMemory 下**会话永久死锁**(heartbeat 已 cancel,无 TTL 兜底);且这是沙盒路径里唯一无界的环节(start 60s / exec 有弃等护栏)。修复:runner 对每个 cleanup 回调包 `asyncio.timeout(30s)`(放 runner 层,对未来任何注册回调都生效);超时=弃等不是修复,残留等 reaper;to_thread 里的 rmtree 弃等后线程自行跑完,无正确性问题。
  - **[P2 方向锁定] 磁盘配额 = loop 池子(硬墙)+ watchdog(软配额)+ ReadonlyRootfs,实现归 C-stage**。威胁面比 review 指出的更大:bind mount 无界之外,**容器 rootfs overlay upper 同样无界**(容器内 /tmp 写的是宿主 /var/lib/docker)。三层方案:① **loop 池子** = 部署时 `truncate/fallocate + mkfs.ext4 + mount -o loop` 一个定容文件系统作 `SANDBOX_SCRATCH_ROOT`(硬墙在正确的爆炸半径边界:race 窗口写穿也只是池子满,宿主无恙;独立 inode 表顺带兜住百万小文件轴;host-prep 几行进部署脚本/fstab,dev mac 不做、风险接受);② **per-turn watchdog** = worker 周期对自己活跃 session 的 scratch `du -s`(to_thread),超 `SANDBOX_WORKSPACE_QUOTA` 杀容器 + sticky 失败(软配额管 turn 间公平,池子兜住其 race 缺陷后够用);另对池子 `statvfs` 做起容器准入水位(O(1))。③ **ReadonlyRootfs + 容器 /tmp bind 到该 turn scratch 子目录**(堵 rootfs upper 洞,所有可写路径落池子,零内存开销;HOME 缓存类写点重定向细节实现时定)。**否决 tmpfs 方案**:内存账难看(额度×并发预留 RAM、冷文件全程占内存、工作区被内存预算压制),且连带 staging 改 exec+tar 流 + runsc tmpfs `size=` enforce 是 D 才能验的未知;C′ 对 plan 已锁的 scratch 机制零改动、无 runsc 赌注。**先后顺序**:watchdog/ReadonlyRootfs/配额常量与 mount/persist 同一套文件量纲,归 C-stage 工作包;开工先跑三条本机探针(du 节奏与开销 / exec 进行中杀容器的收尾行为 / ReadonlyRootfs 下 pandoc/matplotlib 兼容);**bash 在配额落地前不挂进 agent MD**(本就留给 C-wire,无 live 暴露窗口)。
  - **[确认] bash 未挂 agent MD 是刻意**:C-wire 才接 agent 权限;当前 bash 只在请求级工具表,模型不可见。
- **2026-06-10 C-stage 落地**(后端 1163 测试全过 + **真机无孤儿矩阵 8/8 双零残留**,新增超额杀 + stage 往返两条):
  - **三探针结论**(`tests/manual/sandbox_quota_probes.py`):① du 开销 —— 50k 小文件最坏档 os.walk ~150ms / `du -sk` ~50ms,线程内 5s 节奏无感;**关键发现:表观大小低估池子真实消耗 ~40×**(50k×100B 表观 4.8MB / 块占用 195MB)→ watchdog 按 `st_blocks×512` 计块占用。② exec 进行中杀容器 —— `delete(force)` 0.1s 返回,**in-flight exec 正常返回 exit=137**(stream EOF、ExitCode 可解析、无异常)→ 杀后只需 sticky + exec 末尾按 sticky 归因,裸 137 不会漏给模型误读。③ ReadonlyRootfs —— pandoc 裸只读 rootfs 即可跑;matplotlib 无重定向时降级建临时缓存+警告;**/tmp bind + `HOME=/tmp/home` + `XDG_CACHE_HOME` + `MPLCONFIGDIR` 全绿无警告** = 最终形态(host 侧预建 `tmp/home`,部分工具不自建 HOME)。
  - **配额三层进 `SandboxSession`**:scratch 拆 `workspace/` + `tmp/` 子目录双 bind(`/tmp` 入池堵 rootfs overlay upper 无界写)+ `ReadonlyRootfs=True` + 写点重定向;**per-session watchdog task**(`SANDBOX_WATCHDOG_INTERVAL_SEC=5` 周期 to_thread 扫块占用,超 `SANDBOX_WORKSPACE_QUOTA_MB=2048` → 置 sticky → 杀容器;**删成功才交出容器所有权** —— 矩阵实测踩中"close cancel 掉 await 中的 delete 后两边都不删"的孤儿窗口,句柄留给 close() 重删收口);`statvfs` 准入水位(`SANDBOX_POOL_MIN_FREE_MB=1024`,O(1),拒绝即 sticky)。`_start_failure` 升级为通用 sticky 通道(创建失败/准入拒绝/超额杀/容器中途死,统一"本 turn 不重试、立即复述")。
  - **`mount`**(AUTO):吃 `artifact_id`;文本 = WorkingSet overlay 当前内容 UTF-8 写盘(本轮 dirty/new 可 mount),blob = `get_blob`(staged 上传自动走 `ArtifactMemory.blob`);on-disk 名 = id,重复 mount = 刷新副本;lazy 起容器(先 mount 再 bash 成立);返回纯事实(容器内路径/字节/MIME)。
  - **`persist`**(AUTO):吃 `path`(realpath 圈地防 `../`/symlink 指池外 —— 容器内代码可植链,宿主侧跟链 = 读宿主文件进 artifact 的外流面;mount 写侧反向同理,unlink + `O_NOFOLLOW` 不跟链);文本/二进制二分 = 严格 UTF-8 可解码且 ≤ `SANDBOX_PERSIST_MAX_TEXT_BYTES`(20MB)→ 可编辑文本 artifact(MIME 按扩展名查 `EXTENSION_MIME_MAP`),否则 blob(MIME `mimetypes` 猜、兜底 octet-stream;上限复用 `ARTIFACT_BLOB_MAX_BYTES`,**读前按 lstat 拒超大**);落库走 `create_from_upload`(加 `source` 参数,persist 件 `source="sandbox"`,前端 source 徽标自然显示)—— `_N` dedup 即"永远产新 artifact"的机制,目录给 zip-it-first 提示。`mount`/`persist` 入 `RESERVED_TOOL_NAMES`;工厂改 `create_sandbox_tools(session, artifact_service)`。
  - **矩阵新增**:case 6 超额杀(50MB > 10MB 配额 → watchdog 杀、in-flight exec 按配额归因、sticky、双零残留 = 验收④本机形态);case 7 stage 往返(mount → 容器内 `tr` 改写 → persist 回读断言,顺带验 rootfs 只读生效 + /tmp 可写入池)。
  - 单测:配额四条(准入拒绝/超额杀+sticky/in-flight 归因/close 取消 watchdog)+ 容器中途死 sticky + mount/persist 23 条(含 `..` id、植链覆写不跟链、symlink 外流拒绝、超大拒读、超文本上限转 blob)。
- **2026-06-10 C-stage review 收口**(外部 review 四条有效发现,全部修在 C-stage 内、无 live 暴露窗口):
  - **[P1 已修,安全] persist 父目录 TOCTOU**:`realpath` 圈地 + 末端 `O_NOFOLLOW` 只保护最终组件——内核逐组件解析,中间目录是 symlink 照样跟过去。容器后台进程(前一条 bash 留的循环)能在"校验通过后、宿主打开前"把已验证父目录换成指向池外的 symlink → 宿主跟链把池外文件持久化成 artifact(**宿主文件外流**)。修复 = **逐级 `openat`**,每级目录 `O_DIRECTORY|O_NOFOLLOW` 持 fd:**fd 钉 inode 不钉名字**,持有期间容器 rename/换链都动不了它,整条路径无"按名字重新解析"窗口;末端 `fstat` 验 `S_ISREG` + 大小(目录/缺失/超大都从这一次 race-free fstat 出,消掉旧的独立 `lstat` 解析窗口)。复现脚本 2 万次活跃翻转父目录:0 外流 / 5167 次换链全挡 / 合法读照常。mount 写侧同样改逐级(其叶子在 workspace 顶层、父目录容器够不着,unlink+`O_NOFOLLOW` 已闭环)。
  - **[P2 已修] watchdog 漏计目录块占用**:`_dir_usage_bytes` 只数 `filenames`,**目录自身也是文件**(空目录在 ext4 占一块),海量空目录/深树能耗块与 inode 而 usage 不涨 → 绕 per-turn 配额只剩池子兜底。修复 = 每个 `dirpath` 一并 lstat 计入;另引 **每条目最低计费 = 一个 ext4 块**(`max(块占用, 4096)`),消除 APFS/tmpfs 对空目录报 `st_blocks=0` 的盲区,并把 inode 压力折进同一字节度量(**不加独立 inode 旋钮**——块配额修正后单 turn inode 消耗已压在池子预算内,合"fix 复杂度别超 feature 价值")。
  - **[P2 已修] mount 写文件被 umask 裁剪**:`os.open(mode=0o666)` 实际授 `mode & ~umask`,backend 以 umask 077 跑时落 0600、容器 uid 1000 读不了(mount 报成功、后续 bash 才 permission denied)。修复 = 写后 `fchmod(fd, 0o666)`(显式改权限不经 umask,与 `_prepare_scratch_dir` 的目录 chmod 同 idiom,当时文件侧漏了)。本机 Docker Desktop 探测不到,与 uid 属主策略同属 D 真机验证项,但该行现修对。
  - **[P3 已修] persist 超额杀后错报"没用过沙盒"**:超额杀置 `_container=None`(`started=False`),persist 的 not-started 检查排在前面,把配额 sticky 吞成"nothing to persist",与 bash/mount 复述 sticky 不一致。修复 = session 暴露 `sticky_failure` 只读属性,persist 在 not-started 检查**之前**先查并复述。**拍定不开"抢救残留产物"通道**:配额杀的契约 = 本 turn 沙盒不可用,开抢救等于给超额留后门、且超额现场文件完整性不可信。
- **2026-06-10 C-stage review 第 2 轮收口**(两条有效;第二条触发同型 bug step-back):
  - **[P2-a 已修] persist 读循环不复核大小(size TOCTOU)**:`_read_file_under` 只在 `fstat` 看一次大小,读循环读到 EOF 为止。容器后台进程能在 fstat 后继续 append → 读循环把超限内容读进内存,size guard 形同虚设(reviewer monkeypatch fstat 复现 max_bytes=4 实读 21)。修复 = 读循环**累计到 `max_bytes+1` 立即抛 `_FileTooLarge`**,内存占用钉死在 max_bytes+1,与文件实际涨多大无关;fstat 早拒保留(诚实大文件不必读、报真实大小)。与上轮 P1 同母题(开-查-用竞态)但不同轴(路径解析 vs 文件大小)。
  - **[P2-b 已修,退回架构] 目录计量换 `os.scandir`**:reviewer 发现 `os.walk(followlinks=False)` 把"指向目录的 symlink"放进 `dirnames`、既不递归也不作 `dirpath` 吐出 → symlink-dir 漏计(100 条增量 0)。**这是目录计量第 2 轮被发现盲区**(上轮空目录、本轮 symlink-dir),根因是 `os.walk` 按 dirnames/filenames/followlinks 给条目分类、每个分类语义都是潜在盲区——按 step-back 约定(同型 bug 第 2 轮退回架构,非加 case)弃 `os.walk`,改**显式 `os.scandir` 栈**:每个目录项一律先 `entry.stat(follow_symlinks=False)` 计费,只对 `S_ISDIR` 真实目录递归。计量与条目类型解耦——普通文件/空目录/symlink(指 dir 或 file,lstat 看链本体故 S_ISDIR 为 False,只计费不递归)/fifo/设备全走同一路径,整类盲区一次性关掉,不留"第 3 种条目"尾巴。每条目计费口径 `max(块占用, 4096)` 不变。
- **2026-06-10 C-stage review 第 3 轮收口**(一条;目录递归 TOCTOU——与 persist P1 同母题、补 fd 钉住纪律):
  - **[P2 已修] watchdog 递归的目录 TOCTOU**:第 2 轮的 scandir 重写解决了完整性,但递归那步退回**按 `entry.path` 字符串重扫**——容器后台进程能在"判定真目录"与"`scandir(entry.path)`"间把目录名换成指向池外的 symlink,重扫跟链遍历宿主文件系统(I/O 放大 + 错误 quota 归因,甚至错误超额杀)。**这与 persist 的 P1 是同一母题**(跨"检查→使用"间隙按名字重解析路径 = TOCTOU);persist 侧已用 openat 逐级走收口,但 watchdog 第 2 轮重写时没继承该纪律。修复 = **递归也全程 fd 钉住**:根 `O_DIRECTORY|O_NOFOLLOW` 开一次,每次下探 `openat(name, dir_fd, O_NOFOLLOW)` 拿子 fd 再 `scandir(fd)`,绝不按名字重解析(fd 钉 inode 不钉名字;换名/换链动不了已持 fd,`O_NOFOLLOW` 在下探时拒掉被换成的 symlink)。活跃换链复现:3000 次 0 跟链出池。**附带新约束**:fd 钉住的递归每层持一个目录 fd,容器能廉价 `mkdir -p` 深树 → 加 `_MAX_WALK_DEPTH=512` 防耗尽**整个 backend 进程**的 fd(超限 loud log、深埋由池子硬墙兜底)。**纪律外推**:① 真正零 TOCTOU/零遍历是 per-turn 文件系统级配额(独立 loop/XFS project + `statvfs` O(1)),属部署侧(D),in-app watchdog 作可移植软配额留着;② **C-reap 的 scratch 根枚举必须遵同一 fd 钉住纪律**(否则是第 4 轮),已记入 C-reap 工作项。

- **2026-06-10 C-stage review 第 4 轮收口**(一条;第 3 轮加的 depth cap 自身的 fail-open):
  - **[P2 已修] depth cap 命中 fail-open → fail-closed**:第 3 轮为防 fd 耗尽加的 `_MAX_WALK_DEPTH` 命中后只 `continue` 计浅层 → 恶意沙盒造 >512 层、把大文件埋更深,watchdog usage 只涨 ~2MB,**绕过 per-turn 软配额直到池子硬墙兜底(伤其他 turn)**。软配额的意义就是 turn 间公平,fail-open 把它掏空。修复:`_dir_usage_bytes` 返回 `(total, capped)`,watchdog 命中 `capped` **当超额杀(fail-closed)**——绝不只计浅层。**为何不取 reviewer 的"bounded-fd 遍历去掉深度截断":** depth cap 本是对的机制(DFS 下 fd 数=深度,cap 直接限住 fd 这个真实资源),错的只是 fail-open 的 policy;可移植的 bounded-fd(dev mac 无 openat2)只能"每目录从根逐级 openat 重钉",把深链树退化成 O(D²) syscall(fd-DoS 换 CPU-DoS),更复杂且非净改进。fail-closed 后计量再无"少算"路径(每条目要么被计、要么触发杀),偏差方向从 understate(危险)翻成 fail-closed(安全),终结该线。512 层远超真实用途、误判不可达。

- **2026-06-10 C-stage review 第 5 轮 + 架构收口**(fail-open 残留 + 把纪律收成一个模块):
  - **[P2 已修] openat 失败仍 fail-open**:第 4 轮只把**显式 depth cap** 改 fail-closed,`openat` 下探失败(EMFILE/ENFILE 开不出 fd、EACCES 容器 chmod 000 藏子树)仍 catch-all `continue` → 静默少算且 `incomplete=False`(reviewer 复现:RLIMIT_NOFILE=64 扫 80 层树)。修法 = **取反默认**:不再枚举"哪些错误危险"(白名单总漏一种 → 又一轮),而是枚举唯一**良性**错误 `ENOENT`(条目已被 rm、本不占空间),其余一切 OSError 都置 `incomplete=True` → 调用方 fail-closed。四个测不准点(根 open / 子树 scandir / entry.stat / 子目录 openat)统一适用。
  - **[架构,用户拍板] host 侧工作区 FS 访问收成一个模块 `sandbox_fs`**:五轮 review 同根(路径 TOCTOU → 目录 TOCTOU → depth fail-open → EMFILE fail-open),元病不是 seam 锋利、是 **fd 钉住 + fail-closed 这两条纪律手写在每个调用点**(persist/mount 在 `sandbox_ops`、watchdog 在 `sandbox_session`),每轮只修一个点、漏掉兄弟点——正是"同型 bug 跨轮 = 抽象边界错了"。把 `read_file`/`write_file`/`measure_usage` + 异常 + 常量收进 `tools/builtin/sandbox_fs.py`(纪律写一次、测一次),persist/mount/watchdog 全迁过去,**业务代码不得再手写 os.walk/open/path 访问工作区**。`measure_usage` 返回 `(bytes, incomplete)`,`incomplete=True` 即 fail-closed。**watchdog 角色明确降级**:它是 best-effort 软配额/可观测,**硬保证是 loop 池子(第 1 层)**;不再要求 host 递归扫描刀枪不入,只要不危险(不走出池 / 不 DoS)+ 不少算(fail-closed)。单测 `test_sandbox_fs.py`(含 RLIMIT 真降 fd 复现)。
  - **真正的 per-turn 强制下沉存储层 = D 段**:host 递归计量从根上是"没有 per-turn FS 配额时的可移植替身";部署侧上 XFS project quota / per-turn loop image 后,`measure_usage` 退化为纯可观测(见 D 段追加 ⑤)。

- **2026-06-10 C-reap 落地**(后端 1205 测试全过 + **真机无孤儿矩阵 12/12 双零残留**,新增 SIGKILL 孤儿 reaper 回收 + 零误杀两条;C 验收标准 ②③ 闭环):
  - **`SandboxReaper`**(`src/api/services/sandbox_reaper.py`):进程死亡(SIGKILL/OOM,`_wrapped` finally 不执行 → close 不跑、容器归 daemon 不随 worker 死)的二级兜底。**孤儿 = 资源侧双源枚举 − lease 活跃集**:① daemon 上带本命名空间 label 的容器(`containers.list(all=True, filters=...)`,filters 必须 `json.dumps`),② scratch 根直属的 `{conv}__{msg}` 目录,各自减去 `list_active_executions()` 的活跃 (conv,msg)。从 Redis 出发永远发现不了无 lease 的孤儿,故枚举必须资源侧、lease 作减法掩码。
  - **两条纪律**(plan 锁定 + 前序 review 外推):**① 对账粒度 per-turn**——活跃谓词 `active.get(conv)==msg`(非"conv 有没有活跃 turn"),否则同会话紧接的新 turn 持活 lease 会让上一 turn 漏拆的孤儿被误判有主、永不回收(矩阵 case 8 的 live turn 专验零误杀)。**② scratch 根枚举走 `sandbox_fs.list_dir`(fd 钉住、单层不递归)**——活跃容器能把自己 workspace 内子目录换成池外链,按名字递归会重蹈 watchdog 第 3 轮的目录 TOCTOU;reaper 只需根直属名做差集,够不着这个洞(rmtree 按名安全:scratch 根只 backend 写,容器只 bind 到 workspace/+tmp/、够不着父目录)。
  - **零误杀靠 lease + grace**:lease 是唯一 liveness 真相源(它恰在 turn 合法在活 worker 跑的期间被续租);资源恒在 lease 之后创建(lease 在 `_wrapped` 入口、容器 lazy 于其后),故"资源在、lease 不在"通常意味 turn 已结束。`SANDBOX_REAP_GRACE_SEC=60` 只回收存活 > 此值的资源,躲开 Redis 副本/scan 可见性差一拍的误杀窗口。**namespace 防御纵深**:daemon filters 已按 `LABEL_NAMESPACE` 过滤,reaper 侧再核一遍(误杀别部署的活容器后果跨部署严重,label 在手边、零成本)。
  - **多 worker / 韧性**:每 worker 各跑一个 reaper,共享 daemon + Redis 活跃集,重复回收幂等(容器 404 / 目录 FileNotFoundError 当成功);这也缓解"独占 daemon 的 worker 死不重启"残留洞(有 sibling 就扫得到)。单跳异常不杀循环(daemon 不可达 warning 去重:首跳 + 每 10 跳)。`SANDBOX_REAP_ENABLED`(无沙盒部署关掉免空轮询),lifespan 起停(仿 observability,启动失败不挂应用)。单测 `test_sandbox_reaper.py`(fake docker + fake store,12 例覆盖 per-turn 差集/grace/namespace/双源独立/幂等/label 残缺)。
  - **残留洞**(plan 已记):reap 起效前提是有活 reaper 能扫到孤儿所在 daemon;"一 worker 独占一 daemon 且死不重启"够不着——是否保留宽松固定上限(容器内 `timeout`+`--rm`)当最后兜底,取决于部署拓扑,留 D 按真实拓扑拍。

- **2026-06-10 C-reap review 收口**(两条;明确 InMemory 部署边界 + 停机顺序):
  - **[P1 已修] InMemory 多 worker 下 reaper 会误删兄弟进程的活沙盒 → 收成显式契约 + 安全默认**。元病是 app 的既有契约"有 Redis=共享/多 worker,无 Redis=InMemory 单进程"(lease/stream/限流的 InMemory 变体都标"单机")没在 reaper 上 enforce。关键非对称:同一误配(InMemory + `--scale 2`)对 lease/stream 只是**降级**(找不到资源),对 reaper 是**破坏性**(`list_active_executions` 只见本进程 → 兄弟的活沙盒被当无 lease 孤儿、60s 误删)——绕过伤到**另一个 actor**,按 CLAUDE.md 必须服务端强制,不能只文档。app 内拿不到自己的 worker 数(uvicorn/gunicorn 外部起),**唯一可靠判别 = store 是否共享**:给 `RuntimeStore` 加 `is_shared`(Redis=True/InMemory=False);reaper 启停判定提纯成 `_should_start_reaper`——共享 store 自动开,**InMemory 默认不起**(破坏性安全默认),单进程 InMemory(如 Mode-1)要用须显式 affirm `SANDBOX_REAP_ALLOW_LOCAL_STORE=true`(起时 WARNING 复述单进程契约)。
  - **[P2 已修] 停机时 reaper 先于 runner shutdown 停,兜不住 shutdown 期 close() 失败的孤儿**。原顺序在 `close_globals()`(内含 `runner.shutdown()`)前就停了 reaper,而 `SandboxSession.close()` 正是在 runner shutdown 时跑;单副本停机后若某 close 超时/失败,无下一个 startup scan 收尾 → 孤儿跑到下次启动。改顺序:**runner 先 shutdown(提前显式调,close_globals 内二次调 `_tasks` 已空 no-op)→ reaper `final_sweep()` 最后一扫 → 再 stop → close_globals**。

- **2026-06-10 C-reap review 第 2 轮收口**(一条;final_sweep 的 grace 策略修正):
  - **[P2 接续] final_sweep 在共享 store 下仍漏新鲜停机孤儿 → 改 per-resource worker-id**。上一版 final_sweep 的 grace 按 `is_shared` 二分(本地忽略/共享保留),漏洞在"共享保留 grace":单副本 + Redis 下刚建 < grace 的沙盒若 close() 失败,final_sweep 跳过、reaper 随即 stop → 漏到下次启动。且那条 grace 的**理由本身不成立**:lease 在 turn 入口就 SET、容器是首个工具调用才 lazy 建(隔几秒够 Redis 复制),"容器在 lease 不可见"的竞态在 Redis 下不真实;用 store 粗粒度近似一个本是 **per-resource 归属**的问题,两头不对(漏单副本新鲜孤儿 + 对多副本过度保守)。修(采纳 reviewer 方向)= **worker-id label**:每进程 import 时生成 `WORKER_ID`,容器打 `LABEL_WORKER`、scratch 目录名加第三段;`final_sweep` 只对**匹配本 WORKER_ID** 的资源绕 grace(我的 turn 此刻全 shutdown 完 = 必是孤儿,新鲜也收),别人的照走 grace。**与副本数无关地正确**,不靠 lease 时序;`is_shared` 退回只服务 P1 启动 gate,reaper 不再依赖它。后端 1211 全过;真机矩阵 14/14(新增 case 9:grace 内新鲜孤儿周期扫 skip、final_sweep 按 worker-id 收);`test_sandbox_reaper.py` 增 gate(4)+ final_sweep worker 二态(2)。

- **2026-06-11 C-reap review 第 3 轮收口**(一条;parse 格式韧性):
  - **[P2 接续] 两段 legacy 目录格式不兼容 → parse 接受 2/3 段**。worker-id 把目录名升到三段后,`parse_scratch_dir_name` 只认三段、其余静默跳过。**具体迁移场景 moot**:沙盒 pre-live(C-wire 前从未创建持久 scratch 目录),两段格式只活在未发布分支,没有"升级前→后"的真实目录可迁移。但 reviewer 点出的**底层缺口真实**:目录枚举源对任何不识别的名字 = silent skip forever,而纯目录残留(容器有 label 第二源兜底、目录没有)就此永久漏收;且 exact-3-only 对将来再改格式同样脆——这条静默跳过路径与全项目反复消灭的 silent under-collection 不一致。修=`parse` 接受 2 或 3 段(前两段恒 conv/msg、id 内部无 "__" 故无歧义;第三段有=worker、无=None),`final_sweep` 绕 grace **严格只认 `worker==WORKER_ID`**,legacy/无 worker 目录走普通周期 grace 回收、绝不被 no-grace 误删;真正陌生(非 2/3 段)的名字仍 None→跳过(不碰错配到共享根的别人目录)。定性=让解析从脆变韧 + 关一条静默泄漏路径,非 live bug。后端 1216 全过;真机矩阵 14/14;增 parse 往返/legacy/陌生名(3)+ reaper legacy 二态(2)测试。

- **2026-06-11 C-wire 落地**(C 末刀,沙盒首次 live 暴露;后端 1216→1221 全过,无新运行时路径故不重跑真机矩阵):
  - **agent MD 权限**(真正的暴露面):`lead_agent` / `research_agent` 的 `tools` 白名单各加 `bash: confirm` / `mount: auto` / `persist: auto`。白名单是引擎对模型的可见性闸(`engine.py` 工具不在 `agents[name].tools` → 直接拒)兼 per-agent 权限覆盖源(同 dict 的 value 覆工具默认权限)。C-stage/C-reap 全程刻意不挂(配额未落、无 live 暴露窗口),配额三件套 C-stage 落地后前置已满足。bash=CONFIRM 显式声明——这是唯一暴露面,**默认**触发 Permission Interrupt;用户 `always_allow` 后可跳过(CONFIRM 是同意/UX 闸、非 containment,详见本日 review 收口与 `sandbox.md`)。
  - **bash 描述能力清单**:按镜像现状已列全(python 科学栈/pandoc/ripgrep);git 待「沙盒镜像加 git」并行包落地补一行(**已补,2026-06-11,见变更日志**)。版本号刻意不写(与镜像漂移 + 非模型决策所需)。删 TODO(C-wire) 注释,改为指明 git 待补。
  - **mount 契约文案**(提示分层:契约归 inventory/read_artifact):`read_artifact` 命中 blob artifact、`context_manager` 的 inventory 二进制项,文案从「只能下载」升级为「mount 进沙盒检视/转换,或下载」。两处 TODO(C-wire) 删除。agent role prompt **不**加沙盒散文(能力归工具描述、场景 how-to 归 skill,prompt 保持 agent-agnostic)。
  - **文档**:新增自包含 `docs/architecture/sandbox.md`(定位/三工具一 session/生命周期拆除/三正交隔离边界/staging 直读写/三层配额/超时溢出/reaper/常量表),注册 mkdocs nav;`tools.md` 内置工具清单补三行 + 请求级说明;`overview.md` agent 清单补沙盒列。
  - **回归 guard**:`tests/agents/test_shipped_sandbox_wiring.py`——锁出厂配置 lead/research 授全三工具、**bash 必须 confirm**(误设 auto = 不可信代码绕过 CONFIRM 的安全回归)、compact 绝不渗到。
  - **C 段就此完成**;剩工作包(上传翻转/git 镜像/D 冒烟)不分先后,见「进度」。

- **2026-06-11 上传路由翻转落地**(独立工作包;后端 1240 全过 + vitest/tsc 绿):路由翻为「文本类 → content,png/jpeg → 识图 blob,**其余一律 → blob**」,上传口对格式零预判。
  - **magic 闸移除(决策升级,用户拍)**:原计划"docx/pdf magic 闸保留",落地时拍掉——"loud-fail at upload 优于静默死物"的前提是 blob 是死端(沙盒前没人能读),沙盒落地后 blob 不再是死端,失败从「上传时报」推迟到「首次使用时报」:模型 mount 后 pandoc/openpyxl 报错、自己诊断并解释给用户,remediation 提示归 **skill 系统**(下一个大方向,用户已自备一套 skill)。改后缀的 OLE2 `.doc`、损坏 PDF 照收。这同时隐式解决了 `.doc/.odt` 去向(原拟三选一):统一 blob + 模型诊断,不加 libreoffice、不特判(pandoc 本身可读 odt)。
  - **新路由(review 后最终形态:纯声明式三分,charset 启发式退出路由判定)**:`ext ∈ EXTENSION_MIME_MAP`(文本白名单)→ 文本解码,`ext ∈ {png,jpg,jpeg}` → 识图,**其余(含无扩展名/未知扩展)一律直进 blob、不试解码**——首版"未知 ext 先试解码、失败落 blob"被用户+reviewer P2 双向否决:近 ASCII 二进制(`.exe` 头/小 mp4/ascii `.bin`)能被 charset-normalizer"成功"解码,试一下就把原始字节丢成文本 artifact(不可下载/不可 mount)。`_BINARY_EXTENSION_MIME` 降为纯 MIME 正确性表(mimetypes 对 OOXML/heic 平台相关,非路由表非接受闸),未知 ext 由 mimetypes 猜、兜底 octet-stream。白名单内"声明文本但解不出"→ blob octet-stream(按声明猜 text/* 是撒谎);超 MAX_TEXT_CONVERT_BYTES → blob 不再 422。`.svg` 在文本白名单标 `text/xml`(它是 XML;text artifact 无 blob,标 image/* 会误入识图分支)。代价显式接受:无扩展名文本(README/Makefile)也进 blob,契约一致。仅存上传期 422:体积超限、png/jpg 扩展 Pillow 探不出合法图(识图路由是上传期决策,此闸是路由正确性非格式预判,保留)。
  - **识图链路(review 后最终形态:白名单限死 png/jpeg)**:read_artifact 识图分支 gate 从 `startswith("image/")` 收成 `∈ VISION_VIEWABLE_MIMES`(`utils/image.py` 常量)——首版"异型图让 Pillow 试试看"被用户否决:动图取首帧/多页 tiff 取首页都是藏在"试"里的语义坑。gif/webp 等照收 blob 但不进识图,read 返回契约文案(image/* 专属变体:mount → 沙盒转 PNG → persist → read 新件),inventory 标注同步(仅 png/jpeg 提示"read 即可看")。`resize_to_vision_data_uri` 的小图 passthrough MIME 错配修复保留为**第二道防御**(gate 后正常流量只剩 png/jpeg,错标的历史 blob 仍被兜住),其像素闸同为纵深。
  - **同步面**:`uploadFilter.ts` 前端镜像删黑名单(仅剩体积闸)+ stagedFilesStore 注释;api-reference 422 触发面重写;sandbox.md 定位句更新;原子性测试的 422 触发器换损坏 png。

**C 验收标准**(2026-06-10 定):① 三工具 runc 下 live E2E,含「mount 原 docx → pandoc 转 md → 改 → `--reference-doc` 生成新 docx → persist 新 artifact → 前端可下载」闭环;② **无孤儿矩阵**——`while true`、tool 超时、协作取消、外部取消、SIGKILL worker 五条退出路径各跑一遍,`docker ps` + scratch 根目录双零残留(SIGKILL 条靠 reaper 收);③ reaper 零误杀(活跃 turn 的容器/目录不被收);④ 磁盘配额——超额写入的 turn 被 watchdog 杀(沙盒 sticky 失败、宿主分区无恙;本机以普通目录模拟池子,loop 池子 host-prep 真机验归 D)。

### D — 上线前 Kylin 端到端冒烟

**做什么**:本机 runc 开发完成后,上线前在 Kylin 用**真 runsc + 真 artifact 挂载 + cancel-kill** 跑一次端到端回归;部署前跑 `unshare -U` 预检。这是开发期不回内网的代价里留的最后一道关。**追加(2026-06-10)**:① 加 git 后的新镜像在 Kylin 重跑 `run-all.sh` 并**重新冻结 image id**(双架构);② bind-mount 工作区 uid 1000 属主/权限在真实 Linux 上验(backend 进程 uid ≠ 1000 时的 chmod/chown 策略,本机 runc 感知不到);③ 「用户要带格式 Word」场景由 agent 流程真实跑通(merge 后无 `/export`,替代物必须先站住);④ **loop 池子 host-prep**(`fallocate + mkfs.ext4 + fstab` 挂 `SANDBOX_SCRATCH_ROOT`)在 Kylin 真机走一遍、入部署文档,并验 watchdog 超额杀 + 池满 ENOSPC 只伤沙盒不伤宿主;⑤ **评估把 per-turn 强制下沉存储层**(XFS project quota 给每个 turn 的 scratch 子目录分配 project id + `setquota`,或 per-turn loop image)——成立则 `sandbox_fs.measure_usage` 的 host 递归计量退化为纯可观测/补充,turn 间公平由 FS 层 O(1)、race-free 保证,不再靠 host 扫 attacker-controlled 树(五轮 review 的根本出路,2026-06-10 架构收口外推)。

## 关键风险

- **C 扩展 ENOSYS**(B 阶段验)—— 决定 gVisor-as-MVP 是否成立,还是要回退 Firecracker。
- **容器拆除漏路径**(C 阶段)—— 必须每条退出路径都拆,C 阶段专门测 `while-true` + 各种取消/超时确认无孤儿。
- **DooD socket = backend 有 host root** —— 创建参数严防被模型内容污染。
- **gVisor 仅健康 Kylin 节点可用** —— 部署预检不可省。
- **arch 假设(x86_64 ≠ arm64)** —— §B 的 ENOSYS/平台验证结论是 per-arch 的,**不跨架构迁移**;每个目标 arch(x86 / 鲲鹏 arm)须各自跑一遍 §B 才能信"gVisor-as-MVP 成立"。沙盒镜像(C 扩展 wheel)、gVisor 二进制、docker 静态包均分 arch 打。x86 已绿;**arm 已绿(2026-06-08)**——但**额外前置**:鲲鹏 arm 节点须跑 **4K 页内核**(`sandbox/kernel-4k-pkg/` 在位换核),64K 默认内核会让 gVisor Sentry 拒起(`non-4K host`)。见 B 段「进展 · arm」。

## 变更日志

- 2026-05-21 起草。锁定 4 决策(blob 存 DB / 身份轻解耦 / 回写二分 / 识图折进 A);摸底:迁移走 Alembic、message 全链路目前纯字符串(识图需扩块列表)、容器拆除挂执行器清理点。粒度=plan 级。
- 2026-05-21 B 阶段加 pandoc 金丝雀(仅 docx/html↔md,PDF 不在本期)。
- 2026-05-21 驱动场景定:富格式按 blob 存 + pandoc-in-sandbox 作统一转换层(读 docx→md、写 md→docx 以原 docx 作 `--reference-doc` 模版)。pandoc 落沙盒镜像(非 backend);"上传预转 md"降为可选预览;Non-goal 收窄为"不自动反向同步源 blob"。
- 2026-05-21 确立原则 6「artifact 只承载文件源、转换全归沙盒」;Word 导出=沙盒能力(现有 md→Word 过渡保留、成熟后下线);无沙盒部署连富格式"读"都没有。
- 2026-05-21 分支策略:全程 `feat/sandbox`,成熟后整体 merge 回 main、不增量合(撤销"A 可先合 main")。
- 2026-05-28 新增「能力边界」节:定位多用户 agent 编排平台,显式排除持续 dev loop,只覆盖单 turn 闭环任务(遇此类诉求先质疑场景)。
- 2026-06-02 新增底座依赖 `artifact-layer-redesign-plan.md`(发现 `ArtifactManager` 与多 worker 错配)。约束:重构先于 C 阶段、A 的二进制存储建在新四层、二进制走「元数据事件+REST 取字节」。本 plan 方向不变,仅换底座。
- 2026-06-02 挂载改显式(原则 4):沙盒=显式 stage 进出的 scratch 工作区,非 artifact store 自动镜像;persist=普通 artifact 写;保留内存态。关闭"挂哪些 artifact"待决项。
- 2026-06-03 容器生命周期(C 阶段):生=lazy(首个沙盒工具调用);灭=跟 lease 同层(执行器 `_wrapped` 真 `finally`,非 post-processing);进程死亡靠 lease-anchored reap(lease=唯一 liveness 真相源,对账须 per-turn 粒度)。固定上限降为可选最后兜底。
- 2026-06-03 沙盒工具面:分立三工具(`bash`/`mount`/`persist`)+ 共享 per-turn `SandboxSession`(分立胜出因参数面更小、合「Minimize tool parameter surface」);lazy 创建 key=首个沙盒工具调用。
- 2026-06-04 A 阶段识图最底层(litellm 透传 content-blocks)验通,详见 A 段「进展」。
- 2026-06-04 新增原则 7「沙盒默认全禁网 + 依赖三层离线投递」:网络是独立于逃逸隔离/socket-root 的第三条边界,只能在网络边界封不能降级成命令授权(consent≠confinement);依赖分镜像/wheel-bundle/skill-asset 三层,依赖≠artifact,skill bundle 只加法不 re-pin。详见原则 7。
- 2026-06-04 B 阶段细化(介质/脚本/验证项):镜像 tier-1 定稿(+ripgrep,与 backend lock 解耦)、加 ripgrep FS 遍历探针 + offline find-links 投递探针、网络默认 `--network=none`、fixture 全自生成无需携带二进制、构建机=Mac+QEMU(须 `--platform linux/amd64`)。详见 B 段「验什么」「准备」。
- 2026-06-04 网络拍定:默认 `--network=none`,**保留 allowlist 作退路**(将来离线 bundle 太重可放行单台内部镜像在线装包);`verify-network.sh` 保留 allowlist 分支。详见 B 段网络项。
- 2026-06-04 C 阶段补 aiodocker 定性(async Docker API 客户端,挂取消栈)+ 编排器可换性收口在 `SandboxSession`(gVisor 不挑编排器;runc↔runsc=config 开关、Docker↔k8s=换控制面,当前不做 k8s 只保 seam)。详见 C 段 DooD 条。

- 2026-06-04 B 阶段工具链落地(分支 `feat/sandbox`:`sandbox/` + `scripts/build-sandbox-image.sh`)+ 本机 runc 彩排全绿;gVisor 离线包已删→重建入 repo。文件清单 / 已固化决策 / 彩排结果 / 剩余步骤详见 B 段「进展」。
- 2026-06-05 **B 验收通过**:milvus2 `run-all.sh` 全绿,ENOSYS 7/7(C 扩展零 ENOSYS,gVisor-as-MVP 成立)、uid 1000 无 remap;DNS-under-runsc 判 N/A(测试内网无 DNS,与 gVisor 无关)。冻结 image id `sha256:3b43b839…`。milvus2 已撤出,B 闭环。详见 B 段「进展」。
- 2026-06-05 「应用与沙盒分机部署」并入 C 段「换控制面(Docker↔k8s)」轴、有需求再做:真正耦合是 bind-mount daemon-local 非 aiodocker;正解 k8s(Pod+PVC),非远程 Docker daemon。详见 C 段编排器可换性条。
- 2026-06-05 启动 **arm(鲲鹏)§B 补验**:ENOSYS/平台结论 per-arch 不迁移,目标 2×Kylin V10 arm(bare 机)。脚本 arch 化(产物带 `-amd64`/`-arm64` 后缀并存)+ 新增 `sandbox/docker-pkg/`(bare 节点离线装 docker+compose)。风险节加「arch 假设」。详见 B 段「进展 · arm」。
- 2026-06-08 **B(arm)验收通过**:发现 arm 阻断=64K 页(Sentry 拒 non-4K host),解=在位换 4K 内核(厂商 RPM 集,不重建/不需 KVM)。新增 `sandbox/kernel-4k-pkg/`(preflight/install/postcheck)。`89.11→89.38.4k` 后 smoke 5/5 + `run-all.sh` 全绿(ENOSYS 7/7)。双架构 §B 闭环。详见 B 段「进展 · arm」。
- 2026-06-08 **A 开工决策锁定**:blob = 独立 `ArtifactBlob` 表(热路径隔离)+ 元数据事件/REST 取字节;上传加法(blob + 留 md 转换过渡,不做 blob-only);**识图 = turn 内瞬态、不跨轮重建**(事件存引用、`build_event_history` 保持纯、下轮占位 re-read,compaction 天然正确);png/jpeg only + resize-on-read(Pillow,DEP-02);身份解耦已由现状满足、无需新列;首轮上传图不 auto-inject。切片 A-bin→A-upload→A-vision。详见 A 段「进展」。
- 2026-06-08 **A 实现落地**(backend 三切片 + 前端 image view):`ArtifactBlob` 独立表(泛型 `LargeBinary(length=100MB)`,零 dialect import)+ `/raw` 端点;png/jpeg + docx/pdf additive 上传(同事务落 blob);识图 turn 内瞬态(事件存引用、`event_history` 对 `state["vision_blocks"]` 还原、跨轮占位、`build_event_history` 保持纯)。Pillow 入 lock(12.2.0)+ 顺带修 aiohttp CVE(→3.14.1)。单元/集成验过(181 测试 + 前端 tsc),待真实 server+LLM live E2E。详见 A 段「进展」。
- 2026-06-09 **A 完成**(review 修复 + mid-turn 上传 UX 加固 + live E2E 通过):外部 review 无阻塞,修识图能力门控(`models.yaml` `vision` 标志 + lead/research/compact 切 `qwen3.7-plus`)、解压炸弹改显式 `VISION_IMAGE_MAX_PIXELS`、LLM 重试收窄到瞬态(类型化异常)、debug 格式化容忍块列表;mid-turn 上传图本地优先渲染(staged `File` 按 `original_filename` 关联)+ chip 缩略图 + 同名 dedup(`_N`)/本轮限定(`pendingFlush`)修同轮+跨轮串图。后端 1095 / 前端 191 全过;**live E2E 用户实测通过**(上传真图→模型识图)。**A 阶段闭环**,下一步 C。commits `f7b7d4d`→`3476e5e`。详见 A 段「进展」。
- 2026-06-10 **C 开工决策锁定**:切片 C-0(blob-only 一步到位 + 整删 `/export`,不留双轨过渡)→ C-session → C-stage → C-reap → C-wire;persist 永远产新 artifact(blob 不版本化、不覆写,二进制=不可变单版/文本=版本化);mount 语义单一权威载体(文本=WorkingSet overlay、blob=DB,本轮 staged 上传例外);mount 返回纯事实、提示分层(契约→inventory/read_artifact,能力→bash 工具描述,场景 how-to→skill);拆除 plumbing=Session 壳 factory 创建+容器 lazy+cleanup 句柄递 `_wrapped` finally;per-command 超时=容器内 `timeout` 包 argv(exec argv 数组无引号问题);reaper=资源侧枚举(daemon label + scratch 根目录双源)− lease 掩码;镜像加 git(锚点作废、D 重冻结);aiodocker 入依赖(DEP-02)。C 验收标准三条 + D 追加三项(git 重冻结/uid 实验证/Word 场景站住)。详见 C 段「进展」。
- 2026-06-10 **C-0 落地**:docx/pdf 上传 blob-only(magic 预检 loud-fail)、pandoc 退出 backend、pymupdf 保留为 web_fetch 专用 `extract_pdf_text`、删 `/export`、read/inventory 二进制契约文案(success=True 防重试)、update/rewrite 拒改 blob artifact(不可变单版守门)、REST/事件/store 全链 `has_blob`、前端 `BinaryFilePreview`(复用 pendingFlush 闸,turn 内零 /raw 404)。后端 1098 / 前端 205 全过。详见 C 段「进展」。
- 2026-06-10 **C-session 落地**:`SandboxSession`(aiodocker seam、容器 lazy、创建失败 sticky、close 幂等 best-effort、turn 粒度 label + namespace label)+ `bash` 工具(CONFIRM、非零退出=信息、137 按时长归因)+ `register_cleanup` 拆除 plumbing(_wrapped 真 finally、先拆资源后放 lease)+ aiodocker==0.27.0 入 lock(audit 零漏洞)。后端 1136 全过;**runc 真机无孤儿矩阵 6/6 双零残留**(while-true 由容器内 timeout 准点 KILL)。下一刀 C-stage(mount/persist)。详见 C 段「进展」。
- 2026-06-10 **C-session review 收口**:[P1 已修] cleanup 回调加 30s 有界弃等(aiodocker delete 无默认超时,daemon 卡死曾会扣死 lease/stream);[P2 方向锁定] 磁盘配额 = **loop 池子(硬墙,host-prep 部署一次性)+ per-turn watchdog du 超额杀容器(软配额)+ ReadonlyRootfs / 容器 /tmp 入池**(rootfs upper 同样无界,一并堵),否决 tmpfs(内存账 + staging 改道 + runsc `size=` 未知),实现归 C-stage、host-prep 验证归 D(各自验收标准已加 ④);[确认] bash 未挂 agent MD 刻意留 C-wire,配额落地前无 live 暴露。详见 C 段「进展」。
- 2026-06-10 **C-stage 落地**:三探针(du 按**块占用**计 —— 表观大小低估池子消耗 ~40×;杀容器时 in-flight exec 正常返回 137 → sticky 归因;ReadonlyRootfs + /tmp bind + HOME/MPLCONFIGDIR 重定向 = pandoc/matplotlib 全绿)→ 配额三层进 SandboxSession(workspace/tmp 双 bind 入池 + ReadonlyRootfs;watchdog 5s 块占用巡检超 2GB 杀容器,**删成功才交所有权**防 close-cancel 孤儿窗口;statvfs 准入水位;sticky 升级为通用失败通道)+ `mount`/`persist` 工具(staging 宿主直写直读,realpath 圈地 + O_NOFOLLOW 防容器植链双向逃逸;persist 文本/二进制二分、`source="sandbox"`、`_N` dedup 产新件)。后端 1163 全过;**真机矩阵 8/8 双零残留**(新增超额杀=验收④本机形态、stage 往返)。解锁上传路由翻转;下一刀 C-reap。详见 C 段「进展」。
- 2026-06-10 **C-stage review 收口**(四条有效,全修在 C-stage 内):[P1 安全] persist 父目录 TOCTOU——`realpath`+末端 `O_NOFOLLOW` 只保护叶子,容器换父目录链可外流宿主文件 → 改逐级 `openat`(fd 钉 inode,2 万次翻转 0 外流);[P2] watchdog 漏计目录块占用 → 计入 dirpath + 每条目最低一块(消 APFS 空目录 0 块盲区,不加独立 inode 闸);[P2] mount 写文件被 umask 裁剪成 0600 容器读不了 → `fchmod` 绕 umask;[P3] persist 超额杀后错报"没用过沙盒" → 暴露 `sticky_failure` 前置复述,拍定不开抢救通道。后端 1170 全过;真机矩阵 8/8。详见 C 段「进展」。
- 2026-06-10 **C-stage review 第 2 轮收口**(两条):[P2-a] persist 读循环不复核大小(size TOCTOU,fstat 后后台 append 绕 size guard)→ 累计到 max_bytes+1 即抛,内存钉死;[P2-b] 目录计量第 2 轮盲区(symlink-dir),**触发同型 bug step-back** → 弃 os.walk 换显式 os.scandir(每条目统一 lstat 计费、只递归真实目录,整类盲区一次性关掉)。后端 1175 全过;真机矩阵 8/8。详见 C 段「进展」。
- 2026-06-10 **C-stage review 第 3 轮收口**(一条):watchdog 递归的目录 TOCTOU——第 2 轮 scandir 重写解决完整性,但递归按 `entry.path` 重扫,容器可在判定真目录与重扫间把目录名换成池外链 → 跟链遍历宿主(I/O 放大 + 错误 quota)。**与 persist P1 同母题**(跨检查→使用按名字重解析),修复 = 递归也全程 fd 钉住(openat O_NOFOLLOW + scandir(fd),不按名重解析;活跃换链复现 3000 次 0 跟链出池),加 `_MAX_WALK_DEPTH=512` 防深树耗尽进程 fd。外推:C-reap scratch 枚举遵同一纪律(已记 C-reap 项);真正零遍历是 per-turn fs 级配额(D)。后端 1177 全过;真机矩阵 8/8。详见 C 段「进展」。
- 2026-06-10 **C-stage review 第 4 轮收口**(一条):第 3 轮加的 depth cap 命中 fail-open(只计浅层)→ 深埋大文件绕过 per-turn 软配额、伤其他 turn。改 `_dir_usage_bytes` 返回 `(total, capped)`,watchdog 命中 capped fail-closed 当超额杀。不取 bounded-fd 重写(可移植版退化 O(D²) syscall,fd-DoS 换 CPU-DoS);depth cap 是对的机制(限 fd 真实资源)、错的是 policy。fail-closed 后计量无"少算"路径。后端 1179 全过;真机矩阵 8/8。详见 C 段「进展」。
- 2026-06-10 **C-stage review 第 5 轮 + 架构收口**:[P2] openat 失败仍 fail-open(第 4 轮只修显式 depth cap,EMFILE/EACCES 漏了)→ 取反默认:唯一良性 ENOENT,其余 OSError 全 incomplete=True fail-closed,四个测不准点统一。[架构,用户拍板] 五轮同根 = fd 钉住+fail-closed 两条纪律手写在每个调用点、每轮漏一个兄弟点 → 把 host 侧工作区 FS 访问收成 `tools/builtin/sandbox_fs.py`(read_file/write_file/measure_usage,纪律写一次测一次),persist/mount/watchdog 全迁,业务代码不得再手写 os.walk/open/path;watchdog 角色降级为 best-effort 软配额(硬保证=loop 池子)。真正的 per-turn 强制下沉存储层(XFS project quota / per-turn loop)归 D(段加 ⑤)。后端 1191 全过;真机矩阵 8/8;新增 test_sandbox_fs.py(含 RLIMIT 真降 fd 复现)。详见 C 段「进展」。
- 2026-06-10 **C-stage review 第 6 轮收口**(一条,落在收口后的 `sandbox_fs`):[P2] `write_file` 单调 `os.write` 不看返回值——POSIX 允许短写(大 buffer / 信号中断返回 < len(data) 且不抛),mount 会报成功+原始字节数而 workspace 文件已静默截断(monkeypatch 让 os.write 只吐 3 字节复现)→ 抽 `_write_all` 循环写完,非空 buffer 返 0 字节(磁盘满/内核异常)loud-fail。同母题外延:乐观假设单个 syscall 的契约、未 fail-closed——但这轮收在已集中化的一处、加回归测试即闭(短写完成 + 0 字节 loud)。后端 sandbox 86 全过(+2)。详见 C 段「进展」。
- 2026-06-10 **C-reap 落地**:`SandboxReaper`(lease-anchored 孤儿回收,进程死亡 finally 不执行的二级兜底)——资源侧双源枚举(daemon 命名空间 label 容器 + scratch 根直属 `{conv}__{msg}` 目录)− `list_active_executions` 活跃集 = 孤儿 → 删。两条纪律:对账粒度 per-turn(`active.get(conv)==msg`,防同会话新 turn 让旧孤儿误判有主)、scratch 根枚举走 `sandbox_fs.list_dir`(fd 钉住单层不递归,继承第 3 轮纪律)。零误杀靠 lease(唯一 liveness 真相源)+ grace(60s 躲可见性差一拍)+ namespace 防御复核;多 worker 幂等;单跳异常不杀循环、失败去重;`SANDBOX_REAP_ENABLED` 关掉免空轮询;lifespan 起停仿 observability。后端 1205 全过(+14);真机矩阵 12/12(新增 SIGKILL 孤儿回收 + 活 turn 零误杀)。C 验收 ②③ 闭环;下一刀 C-wire(沙盒首次 live 暴露)。详见 C 段「进展」。
- 2026-06-10 **C-reap review 收口**(两条):[P1] InMemory 多 worker 下 reaper 误删兄弟进程活沙盒 —— app 既有契约(Redis=共享多worker / InMemory=单进程)没在 reaper enforce,且该误配对 reaper 是破坏性(删活资源)非仅降级。修=给 RuntimeStore 加 `is_shared`,`_should_start_reaper` 判定:共享 store 自动开、InMemory 默认不起(安全默认),单进程 InMemory 须 affirm `SANDBOX_REAP_ALLOW_LOCAL_STORE=true`。[P2] 停机时 reaper 先于 runner shutdown 停,兜不住 shutdown 期 close() 失败的孤儿 —— 改顺序:runner 先 shutdown → reaper `final_sweep` 最后一扫(grace 按 store 分:本地忽略/共享保留)→ 再 stop → close_globals。后端 1211 全过(+6);真机矩阵 12/12。详见 C 段「进展」。
- 2026-06-10 **C-reap review 第 2 轮收口**(一条):final_sweep 上版按 `is_shared` 二分 grace,共享 store 保留 grace → 单副本 Redis 刚建 < grace 的沙盒 close() 失败时漏收(且那条 grace 理由不成立:lease 先于容器创建数秒、Redis 可见性竞态不真实)。改 per-resource worker-id(采纳 reviewer 方向):每进程 `WORKER_ID`,容器打 `LABEL_WORKER` + scratch 目录名加第三段,final_sweep 只对本 worker 资源绕 grace、别人走 grace —— 与副本数无关地正确。`is_shared` 退回只服务 P1 启动 gate。后端 1211 全过;真机矩阵 14/14(新增 case 9 新鲜孤儿)。详见 C 段「进展」。
- 2026-06-11 **C-reap review 第 3 轮收口**(一条;parse 格式韧性):reviewer 指 worker-id 引入(三段目录名)后 `parse_scratch_dir_name` 只认三段,两段 legacy 目录被静默永久跳过。**具体迁移场景 moot**(沙盒 pre-live、C-wire 前从未创建过持久 scratch 目录,两段格式只活在未发布分支),但底层缺口真实:目录枚举源对任何不识别的名字 = silent skip forever、纯目录残留无第二兜底(容器有 label 兜底),且 exact-3-only 对将来再改格式同样脆 —— 与本项目反复消灭的 silent under-collection 不一致。修=`parse` 接受 2 或 3 段(前两段恒 conv/msg、内部无 "__" 无歧义,第三段有则 worker 无则 None),`final_sweep` 绕 grace 严格只认 `worker==WORKER_ID`,legacy/无 worker 目录走普通周期 grace 回收、绝不被 no-grace 误删。后端 1216 全过;真机矩阵 14/14;增 parse 往返/legacy/陌生名 + reaper legacy 二态测试。详见 C 段「进展」。
- 2026-06-11 **C-wire 落地(C 末刀,沙盒首次 live 暴露)**:`lead`/`research` agent MD 各加 `bash: confirm`/`mount: auto`/`persist: auto`(白名单=引擎可见性闸 + 权限覆盖源;配额三件套已落、前置满足才挂)。bash 描述能力清单按镜像现状列全(git 待并行包补)、版本号刻意不写;mount 契约文案落 `read_artifact`/inventory 二进制项(「mount 进沙盒检视/转换」),三处 TODO(C-wire) 清空;agent prompt 不加沙盒散文(提示分层)。新增自包含 `docs/architecture/sandbox.md` + nav 注册,`tools.md`/`overview.md` 补沙盒。回归 guard `test_shipped_sandbox_wiring.py` 锁 bash=confirm 安全闸 + 白名单授予 + compact 不渗。后端 1221 全过(无新运行时路径,不重跑真机矩阵)。**C 段完成**;剩工作包(上传翻转/git 镜像/D 冒烟)不分先后。
- 2026-06-11 **C-wire review 收口**(三条,纯文档;无代码/机器改动):[P1 措辞] reviewer 指 commit 把 bash 的 CONFIRM oversell 成「不可信代码必经 Permission Interrupt」,与 `always_allow` 可按工具名跨 agent/跨 turn(engine `always_allowed_tools` + controller message-metadata 继承)弃权确认不一致。核实机制属实,但**定性 = 我的 overclaim,非可利用洞**:沙盒安全靠 gVisor/DooD/`--network=none`+ephemeral 三正交边界,CONFIRM 不提供 containment;且 always_allow 只能用户触发(模型无法自授)、弃权后代码照样 contained → 按「前端锁 UX/后端锁正确性」落 UX 侧(对比 web_fetch confirm 把守真实出网=必须严防绕过)。**定向不加防绕过机器**(那是把 UX 闸当安全边界、属 fix>feature 的 creep),改 sandbox.md 加「bash 的 CONFIRM 是同意闸不是 containment」子节澄清;operator 合规级「不可弃权」留独立策略特性、点头才做。[P3] 坏锚点 `孤儿回收（reaper）`(全角括号 slug 丢分隔)→ heading 改空格,`mkdocs build --strict` 验过;[P3] 定位句 xlsx 当前不可上传(doc_converter 拒)→ 改「可上传 docx/pdf/图片」+ 点明沙盒可处理 ⊋ 可上传(更多 office 随上传翻转入 blob)。
- 2026-06-11 **上传路由翻转落地**:「文本→content,png/jpeg→识图,其余一律→blob」,上传口零格式预判。**magic 闸移除**(决策升级,用户拍:loud-fail at upload 的前提——blob 是死端——随沙盒落地消失,诊断归沙盒里的模型、remediation 归 skill 系统);`.doc/.odt` 去向隐式解决=统一 blob+模型诊断。`_BINARY_EXTENSION_MIME` 已知二进制直进 blob(防近 ASCII 二进制被误解码成文本,非接受闸);解不出/超文本帽→blob 不再 422;`.svg` 走文本 `text/xml`;仅剩 422=超限+png/jpg 探测失败(识图路由闸保留)。识图链路适配:`resize_to_vision_data_uri` 修非 png/jpeg 小图 passthrough MIME 错配(一律重编码 PNG+模式归一),read 失败文案补 mount 指引,像素闸独挑异型图炸弹。前端 `uploadFilter.ts` 删黑名单镜像;api-reference 422 面重写。后端 1240 全过 + vitest/tsc 绿。skill 系统定为下一个大方向(用户已自备一套 skill)。
- 2026-06-11 **上传翻转 review 收口**(用户两刀 + reviewer 三条):①(用户)路由收纯声明式——删"未知 ext 先试解码"启发式,文本白名单外一律 blob;恰是 reviewer **P2** 的结构解(实测 ascii `.bin`/`.exe` 头/小 mp4 可被 charset-normalizer 误解码成文本、丢原始字节)。`_BINARY_EXTENSION_MIME` 降为纯 MIME 表;白名单内解不出→octet-stream(按声明猜 text/* 是撒谎);代价显式接受=无扩展名文本(README)进 blob。②(用户)识图分支白名单限死 png/jpeg(`VISION_VIEWABLE_MIMES`)——"异型图让 Pillow 试"否决(动图首帧/多页 tiff 语义坑);异型图 read 返回 mount→转 PNG→persist 契约文案,inventory 同步;resize 的 MIME 错配修复降为第二道防御。③ **P1(致歉)**:前次"vitest 绿"只跑了 uploadFilter 单文件,`stagedFilesStore.test.ts` format-gate 3 红——已翻成 flip 后行为(size 是唯一前端拒);**全量** vitest 200 全过。④ **P3**:deployment.md 两处文本帽表述补"超闸落 blob 不再 422"。后端 1246 全过 + 新增 P2 回归(参数化近 ASCII 二进制)+ 识图 gate 回归(gif 契约文案/不进识图)。
- 2026-06-11 **上传翻转 review 第 2 轮收口**(一条 P3):`read_artifact` 工具描述(模型可见面)仍说 image/* 都返回真图——gate 收白名单时漏改提示词,模型会对 gif/webp/tiff 形成错误预期。改为「PNG/JPEG 直看;其余 binary/image 格式 mount 进沙盒转换(转 PNG → persist → read)」。顺扫确认无其它过时识图承诺(inventory 提示上一轮已 gate)。教训同 C-wire P1:**行为收紧时,所有模型可见文案(描述/契约/inventory)要在同一刀里对齐**。
- 2026-06-11 **沙盒镜像加 git 落地**(最后一个 C 并行包,剩 D 段):`sandbox/Dockerfile` apt 加 git + 烤 `--system` 默认配置四件(identity 两项=无 user.name commit 即硬错、`init.defaultBranch=main` 消每次 init 的 stderr 噪声、`safe.directory='*'`=dubious-ownership 检查防的是多用户宿主,沙盒里容器即信任边界,bind-mount 树带宿主 uid≠1000 否则全量 git 操作报错);新探针 `verify/verify-git.sh`(init/add/commit/diff/log,commit 即烤入 identity 的断言)入 `run-all.sh`;`build-sandbox-image.sh` manifest 加 git 版本行;bash 工具描述补「git (local repository operations only)」一行(无网已声明,clone/fetch 不赘述);`sandbox.md`/`README` 能力清单同步(模型可见文案同刀对齐,上轮教训)。**本机实测**:arm64 原生构建过 + 探针 runc `--network=none` 下 5/5 绿 + `--system` 四项落盘核实(git 2.47.3);**双架构镜像 id 锚点就此作废,D 重跑 `run-all.sh` 重新冻结**(既定动作)。后端 1246 全过、mkdocs --strict 过。
- 2026-06-11 **镜像加 git review 收口**(P2 一修一裁):[P2-1 修] reviewer 指 verify-git.sh 在容器内 mktemp 自建仓(属主即 1000),验不到 `safe.directory='*'` 针对的真场景(bind-mount 树属主 ≠ 1000)——删掉该配置探针照样全绿。修=`verify-bindmount.sh`(host 侧)加第 5 检:用镜像自己的 git 以 `-u 0` 在 bind-mount 建仓(.git 属主 0,零 host git 依赖)、再以默认 uid 1000 跑 status/log,无 waiver 必报 dubious ownership;verify-git.sh 补 `--system safe.directory` 含 `*` 的存在断言;清理兜底容器删 root 残留。**判别力实测**:mac virtiofs 把 bind-mount 属主映射成访问者 uid → 该检在 mac 恒绿(注释+README 已记,判别现场=Linux/Kylin);改用容器内真属主做阴性对照——屏蔽 system 配置 `fatal: detected dubious ownership`、烤入配置 OK,waiver 有效性成立。检在 `run-all.sh` 内,D 段重跑即自动纳入验收(答 open question ①)。[P2-2 裁 不管] 「bash 描述宣称 git 但 daemon 上 `:latest` 可能还是旧镜像」——用户拍不加机器:这是部署次序问题(新镜像 load 先于 backend rollout,部署流程既有保证),不为它加运行时能力探针/启动健康检查;描述与镜像漂移的一般性风险随 D 段重冻结镜像 id 消化。
<!-- 新日志按日期顺序追加到此行上方 -->

