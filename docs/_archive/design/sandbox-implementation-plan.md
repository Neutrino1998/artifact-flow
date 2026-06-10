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

- **当前**:**A / B(双架构)完成**(详见各段进展)。**C 开工**(2026-06-10 开工决策锁定,切片 C-0→C-wire,见 C 段「进展」)。
- **下一步**:**C-stage**(`mount`/`persist`)。C-0 / C-session 已落地(2026-06-10,见 C 段进展;C-session 含 runc 真机无孤儿矩阵 6/6)。并行待办:arm §B 镜像 id 冻结(机器在位时;注意 C 镜像加 git 后锚点作废,统一在 D 重冻结);沙盒镜像加 git(Dockerfile + verify 探针,可与 C-stage 并行)。
- **产物处置(2026-06-05 拍定)**:`feat/sandbox` 这批已验收产物(`sandbox/` 探针 + 构建脚本)**暂留分支不动**,不单独提早合 main。「是否把就绪探针子集(`unshare -U` 闸 + smoke + ENOSYS/uid)提升为通用部署机预检工具」**推迟到 C/D 阶段**——届时有真实第二调用点(每台新沙盒宿主预检 + D 端到端冒烟)再校准边界,现在抽象属投机(YAGNI)。

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | artifact 地基(二进制存储 + 多格式上传 + 识图) | **完成**(2026-06-08 三切片 + 2026-06-09 review 加固 + live E2E 用户实测通过) |
| B | Kylin gVisor 功能验证(内网) | x86_64 **完成**(验证通过+已撤出);arm64 **完成**(2026-06-08,64K→4K 换核后全绿) |
| C | 沙盒引擎集成(本机 runc 连调) | **进行中**(C-0 / C-session 已落地,下一刀 C-stage) |
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
- **DooD + 配额**:backend 挂 docker.sock,经 **aiodocker**(Docker daemon HTTP API 的 asyncio 原生客户端——选它因容器生灭要 `await`、直接挂进引擎的 `asyncio.Task.cancel()` 取消/超时/lease 栈,而 `docker` CLI/同步 `docker-py` 与之对不上)起沙盒;资源配额(内存 / CPU / pids / `--network=none`,见原则 7);**容器创建参数不可被模型生成内容污染**(镜像/挂载/runtime 固定在代码侧)。
  - **编排器可换性(收口在 `SandboxSession`)**:gVisor 是 OCI runtime、**不挑编排器**(containerd / CRI-O / k8s RuntimeClass=gvisor / podman 均可)。两个可换轴量级不同:**runc↔runsc = 一个 config 开关**(原则 2);**Docker↔k8s = 换控制面 client**(aiodocker → k8s API,per-turn 容器 → per-turn Pod,reap/socket-root 全重画)。当前单机 DooD 形态**不做 k8s**,但 aiodocker 调用须**收在 `SandboxSession` 这一个 seam 后**、不散进引擎——将来真上 k8s 只换该层,引擎无感(YAGNI:现在只保 seam、不抽象)。
    - **「应用与沙盒分机部署」并入此轴、有需求再做**(2026-06-05):分机不是改 aiodocker 连接串能办的——真正的耦合是 **bind-mount 工作区 daemon-local**(路径在 daemon 那台机解析,不在客户端机;uid 属主断言同理),aiodocker 本身可连远程 TCP+TLS daemon,但单远程 daemon = 无调度/无故障转移、bind-mount 还是断。**正解是 k8s**(per-turn Pod + volume/PVC stage 替代宿主 bind-mount),即本条上面那根「换控制面」轴。两个可能驱动都推迟到真有需求:① 专用沙盒主机池(隔离烧 CPU 的不可信执行);② 切掉「应用机经 docker.sock 拿 host root」的爆炸半径(正当安全驱动,但代价仍是换控制面)。在此之前结论不变:单机 DooD、seam 留着。
- **文档转换走沙盒**:pandoc 装进沙盒镜像(B 验过),富格式读(docx→md)和写都由 agent 在沙盒里跑 pandoc。**驱动场景**:用户要带格式的 Word 时,模型以用户上传/原有 docx 作 `--reference-doc` 样式模版,在沙盒里 md→docx 生成,产物回写成可下载 blob——比固定的 md→docx 导出保真,可能取代现有的 md→Word 导出路径。**门控变化(衔接 artifact plan 决策 6)**:现有 `/export` 是同步 REST 读,turn 中按「前端 UX 锁的读」处理;一旦导出搬进沙盒 = 起容器 = **执行**,就从读升级为 **lease 挡的写/执行**(跟 bash 工具同级),门控责任从前端移到后端 lease。替换 md→Word 路径时一并改门控,别留前端旧锁。

**到时再敲定**:并发上限;persist 多文件 zip 的命名与"可单独查看"白名单;**上传路由翻转(挂 C-stage mount 落地后)**——现状是 blob 白名单(docx/pdf/png/jpeg)+ 显式拒绝名单(Office/ODF/异型图)+ 默认文本解码兜底,沙盒可用后翻为「文本类白名单→content,其余→blob」:zip(原则 5)/xlsx/pptx 等出拒绝名单入 blob(沙盒里 openpyxl/解压直接处理,"请导出为 csv"类 remediation 同步过时),`_UNSUPPORTED_IMAGE` 异型图(gif/webp 等)同理出名单入 blob(沙盒里转 png 可看),docx/pdf magic 闸保留;**翻转是一个工作包,须同步**:① `frontend/src/lib/uploadFilter.ts` 是后端拒绝名单的**前端镜像**(预挡+remediation 文案复制品),只翻后端则前端继续拒掉后端已收的格式;② api-reference.md 上传段 422 触发面描述;③ read_artifact/inventory 契约文案升级为指引 mount(代码已留 TODO(C-wire))。**沙盒落地前不翻**——blob-default 会产不可用死物 + 吞掉改后缀/损坏文件的 loud-fail。(原"挂哪些 artifact:全部 vs 被引用"已由原则 4 的显式 mount 关闭——模型 mount 谁就有谁;原"沙盒工具是否合并"已拍定分立三工具 + 共享 `SandboxSession`,见上;原"bash 输出溢出"已拍定复用既有 idiom,见下「进展」。)

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
  - **reaper 枚举方向=资源侧,lease=减法掩码**:孤儿定义=无 lease 的容器,从 Redis 出发永远发现不了;正确=daemon `ps --filter label` 枚举 − `list_active_executions` 活跃集。**scratch 目录是第二类残留**(容器先没了目录还在:mkdir 后/起容器前被 SIGKILL、`--rm` 自删、daemon 重启),需第二枚举源=scratch 根目录列目录,同一 lease 差集谓词、目录名同带 `{conv}-{msg}`。
  - **bash 输出溢出**:复用 `max_result_size_chars` + `_maybe_persist_tool_result` 溢出转 artifact idiom,引擎零改动。
  - **镜像加 git**(tier ①:通用、环境定义、无网无法现装):apt 入 `sandbox/Dockerfile` + 烤默认 git identity(无 user.name `commit` 即报错)+ `sandbox/verify/` 加探针(init/add/commit/diff);用途=本地仓库操作(zip 解压后 log/diff/blame/apply),`--network=none` 下 clone/fetch 死属 by design。**B 冻结锚点 `sha256:3b43b839…` 作废**,D 重跑 `run-all.sh` 重新冻结(本就既定动作,不为 git 单独回内网)。
  - **依赖**:aiodocker 入 requirements → DEP-02(slim 内重生 lock + pip-audit);exec multiplexed stream demux 自验。
- **2026-06-10 C-0 落地**(blob-only 一步到位,后端 1098 / 前端 205 测试全过 + tsc/lint clean):
  - **后端**:`DocConverter` docx/pdf 改 blob-only(content="" + content_type=真实 MIME + magic 预检 loud-fail,pdf 补 `%PDF-` 闸对齐 docx);pandoc 退出 backend(删 `export_docx`/`check_pandoc`,main.py 不再依赖);**pymupdf 文本抽取保留为独立 `extract_pdf_text` 供 web_fetch PDF 降级**(网页阅读路径,非上传,convert() 不再可达);删 `/export` 端点(连 `Query` import);`read_artifact` 序列化加 `blob_content_type` 判别字段 → ReadArtifactTool 对非图片 blob 返回契约文案(**success=True 防重试循环**)、inventory 二进制项标注;**新增 `_binary_immutable_error` 守门**——update/rewrite 在 blob artifact 上一律拒(否则文本编辑会让"双轨"借尸还魂),改=产新 artifact。
  - **REST/前端**:`ArtifactSummary`/`ArtifactResponse` 加 `has_blob`(由 `blob_content_type` 推导,OpenAPI types 重生成);事件 `ArtifactCreatedData` 补 `has_blob`/`blob_size`/`blob_content_type` 类型;store `LiveArtifact.hasBlob` 全链贯通(`defaultViewMode` 收 hasBlob);删 `exportArtifact`/「导出为 Word」菜单;下载原格式按 `has_blob` 分流(blob → authed `/raw` objectURL,文件名用 `original_filename`);新增 `BinaryFilePreview`(文件卡片 + 下载),**turn 内复用 ImagePreview 的 `pendingFlush` 闸**——卡片只靠事件元数据即可渲染(filename/MIME),仅下载按钮 flush 前换成「本回合完成后可下载」提示,不发 /raw、无 404;blob artifact 隐藏复制按钮、tabs 限 preview-only。
  - **TODO(C-wire)**:read_artifact 契约文案与 inventory 标注当前只说「可下载」,mount 工具落地后改为指引 mount 进沙盒(代码内已留 TODO 注释)。
- **2026-06-10 C-session 落地**(后端 1136 测试全过 + **本机 runc 真机无孤儿矩阵 6/6 双零残留**):
  - **`SandboxSession`**(`src/tools/builtin/sandbox_session.py`):per-turn 对象壳,aiodocker 全收口在此 seam(编排器可换性);容器 lazy 于首个 exec(壳零成本:不碰 docker、不建目录);创建参数全代码侧(`--network=none`、Memory=MemorySwap 禁 swap、NanoCpus、PidsLimit、Runtime 按 `SANDBOX_RUNTIME`);label 到 turn 粒度(`artifactflow.sandbox.{conversation,message}-id` + **namespace label**=`REDIS_KEY_PREFIX`,隔离共用 daemon 的多套部署,C-reap 按它过滤);scratch 目录 `{root}/{conv}__{msg}`(reaper 第二枚举源按此反解)。**创建失败 sticky**:本 turn 不重试,后续调用立即复述原因(失败多为环境性,重试只重复烧启动超时);**create 成功 start 失败的半成品句柄先记**,close 仍删得到。`close()` 幂等、每步独立 best-effort(容器→scratch→client),任一步失败只记日志等 reaper。
  - **exec = 容器内 `timeout --signal=KILL` 包 argv**(真机验证 exit 137、3.0s 准点杀);tool 侧 `asyncio.timeout(命令上限+30s grace)` 弃等护栏只兜 exec 通道卡死。输出 stdout/stderr 按到达序合流、**每流独立 incremental decoder**(frame 劈断多字节字符不出 �)、超 `SANDBOX_MAX_OUTPUT_CHARS`(200k)继续 drain 但丢弃+显式截断标记;ExitCode EOF 后有界轮询(daemon 落账延迟)。
  - **`bash` 工具**(`sandbox_ops.py`,CONFIRM):唯一参数 `command`;**非零退出码 = 信息不是故障**(success=True + `[exit code: N]`,grep 无命中不该触发失败语义);exit 137 按时长归因(≥上限才标 timeout 杀,避免误归因 OOM-kill);>50k 由引擎溢出转 artifact idiom 接手(引擎零改动)。`bash` 入 `RESERVED_TOOL_NAMES`。
  - **拆除 plumbing**:`ExecutionRunner.register_cleanup(task_id, cb)` 注册表;`_wrapped` 真 finally 在 `cleanup_execution` **之前**逐个 best-effort 执行(**先拆资源后放 lease**,"无 lease 即孤儿"谓词无窗口;CancelledError 也只记日志继续,绝不跳过 lease 释放/close_stream);controller_factory 创建壳 + 注册 + `create_sandbox_tools` 合入 all_tools(close 不依赖 DB session,晚于 factory context 退出安全)。
  - **矩阵**(`tests/manual/sandbox_no_orphan_matrix.py`,真 daemon):正常路径(顺带自验 exec multiplexed demux + workspace 跨调用持久)/ while-true 真杀 / exec 中取消 / 起容器中取消 / runner 成功+外部取消,每条断言 daemon 无 label 容器 + scratch 已删。SIGKILL worker 条留 C-reap。
  - **依赖**:aiodocker==0.27.0 入 lock(slim 内重生,pip-audit 零漏洞)。
  - 单测:session(fake aiodocker:lazy/配置/sticky 失败/close 幂等/截断/demux 解码)+ bash 契约 + runner cleanup 注册表(成功/异常/取消三路径 + 顺序断言)。

**C 验收标准**(2026-06-10 定):① 三工具 runc 下 live E2E,含「mount 原 docx → pandoc 转 md → 改 → `--reference-doc` 生成新 docx → persist 新 artifact → 前端可下载」闭环;② **无孤儿矩阵**——`while true`、tool 超时、协作取消、外部取消、SIGKILL worker 五条退出路径各跑一遍,`docker ps` + scratch 根目录双零残留(SIGKILL 条靠 reaper 收);③ reaper 零误杀(活跃 turn 的容器/目录不被收)。

### D — 上线前 Kylin 端到端冒烟

**做什么**:本机 runc 开发完成后,上线前在 Kylin 用**真 runsc + 真 artifact 挂载 + cancel-kill** 跑一次端到端回归;部署前跑 `unshare -U` 预检。这是开发期不回内网的代价里留的最后一道关。**追加(2026-06-10)**:① 加 git 后的新镜像在 Kylin 重跑 `run-all.sh` 并**重新冻结 image id**(双架构);② bind-mount 工作区 uid 1000 属主/权限在真实 Linux 上验(backend 进程 uid ≠ 1000 时的 chmod/chown 策略,本机 runc 感知不到);③ 「用户要带格式 Word」场景由 agent 流程真实跑通(merge 后无 `/export`,替代物必须先站住)。

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
<!-- 新日志按日期顺序追加到此行上方 -->

