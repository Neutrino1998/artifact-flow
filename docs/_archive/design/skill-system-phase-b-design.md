# Phase B 开工设计 —— 工具渐进式披露(tool-set DB 模型 + reconciler + EffectiveToolset + search_tools)

> 状态:开工设计;主 plan = `skill-system-implementation-plan.md` Phase B(本文件是它「到时再敲定」的落实)
> 起草:2026-06-25
> 已定方向(2026-06-25 与用户敲定):
> 1. **交付 = 4 片增量合 main**(B-1→B-2→B-3→B-4,各自独立可测、纯加法/行为保持);
> 2. **工具集 config = 目录即单元**(`config/tools/<set>/` + `_set.md`;扁平 `config/tools/foo.md` = singleton unit);
> 3. **agent MD `tools:` 本阶段做 decision 11 完整改写**(成员态 enabled/disabled、等级 sole-source 工具定义);
> 4. **后端+种子先行,前端 CRUD + agent_unit 绑定 UI 作 B-4(最后一片)**。

## 代码现状基线(开工前实测,2026-06-25)

- **工具扁平 + 静态**:`config/tools/*.md` → 每个一个 `HttpTool`(`src/tools/custom/loader.py:49`),启动一次性进进程级 `_tools`(`dependencies.py:189`、`_load_tools`)。无分组、无 DB、无 `tool_unit`/`agent_unit`。
- **权限双角色**:`AgentConfig.tools: dict[str,str]` = `{名: auto|confirm}`,既是允许清单又是等级覆盖(`src/agents/loader.py:25`)。等级解析在 `engine.py:900-910`:`agents[a].tools.get(name, tool.permission.value)`。
- **4 读点**(行号比主 plan 漂移,以此为准):渲染 `context_manager.py:89-92`;条件段 `context_manager.py:204-217`(+`:85` subagents 闸);执行允许闸 `engine.py:816-827`(+`_resolve_tool` `:830`/`:343`);等级检查 `engine.py:900-910`。全直读 `AgentConfig.tools`。
- **per-turn 装配**在 `controller_factory.py:79-118`:`all_tools = {**get_tools(), **artifact_tools, **sandbox_tools}`(dict-splat,不重跑 `build_tool_map` 撞名检查)。
- **撞名**:`build_tool_map`(`base.py:305-334`)custom-vs-builtin/reserved → `ValueError`;builtin-vs-builtin、custom-vs-custom 静默覆盖。`load_all_agents`(`loader.py:84-127`)同名 `config.name` **静默覆盖**(末名胜)。
- **ORM 全在 `src/db/models.py`**(单 `Base`,SQLAlchemy 2.0 `Mapped`);PK = app 生成 String(64) 或 autoincrement;时间戳 `server_default=func.now()`、naive UTC(连接层强制,`database.py:314-346`)。`User.department_id`(FK SET NULL)、`User.role`、`Department(parent_id 自 FK,parent_id 树)` 均**已存在**。
- **迁移**:`alembic.ini` → `src/db/alembic/`;唯一一条 `0001_initial_schema.py`(squash,既是 base 也是 head)。**仅 PG/MySQL 跑 alembic;SQLite 走 `create_all`**(`entrypoint.sh:6-9`)。`entrypoint.sh:17-78` = PG advisory lock leader 槽跑 `alembic upgrade head`,末 `exec "$@"`(uvicorn)。
- **无 reconciler / 无种子 hook**;最近先例 = `scripts/create_admin.py`(手动幂等)。
- **`active_skills` 不存在**(C 才引入);B 只用 `always_allowed_tools`(`controller.py:176/529` 回合间承接,已工作)。

## 不变量与约束(贯穿 4 片)

- **B 全程纯加法 / 行为保持**,唯一有意的行为语义变更 = decision 11(等级 sole-source 工具定义 + agent MD 成员态/等级分离)落在 **B-2**(engine 消费侧翻转处),B-1 write-only、engine 不动。
- **identity = natural key**(decision 10):`tool_unit.name`/`agent.name` 作 PK;所有 m2m 真 FK + `ON DELETE CASCADE`。
- **dept 规则表不在 B**(C 建 `department_skill_rule`+`department_unit_rule`,G 消费 unit 规则)。B 只产出 `visibility` 列 + 稳定 unit name 作未来 FK 目标 + reconciler 的 clear-on-visibility 钩子(B 内空跑)。
- **凭证统一加密落库**(独立 `tool_credential` 表,B-4 落地):resolve 一条路 = 读库解密、lazy 到 execute、只解被调工具。dynamic = UI 写;seeded = reconciler seed 时按 `{{TOOL_SECRET_*}}` 从 env 取值后加密落库(MD 仍只放引用、不放原始值 —— secret 间接层 `secrets.py` 保留)。主密钥在 env、单把、**不做轮转**。详见 B-4。

---

## B-1 —— DB 地基 + 通用 reconciler + per-turn 快照

**目标**:external 工具/agent 物化进 DB(config 仅种子),建通用 reconciler,引擎每 turn 从 DB 读快照。**对引擎输出零行为变化**(除 decision 11 等级语义),靠回归测试守。

### 新 ORM 模型(`src/db/models.py`)

**`tool_unit`**(external 工具单元,decision 5/10/11)
| 列 | 类型 | 说明 |
|---|---|---|
| `name` | String(64) **PK** | 全局唯一 unit 名;**禁含 `__`**(前缀分隔保留) |
| `kind` | String(16) | `tool`(singleton)\| `toolset` \| `mcp`(F 才用) |
| `description` | Text | set 级描述(索引行语境;singleton = 工具自身描述) |
| `visibility` | String(16) | `public`(默认,无 dept 行=allow)\| `department`(默认 deny);**B 不消费、G 消费** |
| `defer` | Boolean=False | 披露开关(B-3 用) |
| `provider` | String(16)=`http` | `http` \| `mcp`(provider 抽象缝,B-4 归一化、F 填 mcp) |
| `source` | String(16) | `seeded`(config,UI 不可改)\| `dynamic`(UI 新建,B-4) |
| `seed_hash` | String(64) nullable | seeded 行内容哈希(幂等 upsert) |
| `created_at`/`updated_at` | DateTime | `func.now()` / `onupdate` |

**`tool_member`**(unit 下的具体工具/endpoint)
| 列 | 类型 | 说明 |
|---|---|---|
| `unit_name` | String(64) FK→`tool_unit.name` **ON DELETE CASCADE** | |
| `member_name` | String(64) | 作者裸名(`search_repos`) |
| `full_name` | String(130) **unique index** | 可调用/注册名:set = `<unit>__<member>`;singleton = `== unit_name`(无 `__`) |
| `permission` | String(16) | `auto`\|`confirm` —— **等级唯一来源(decision 11)** |
| `definition` | JSON | HttpTool 配置(endpoint/method/headers/params/response_extract/timeout/secret 引用);provider=mcp 时运行期填(F) |
| `show_example` | Boolean=True | |
| (PK) | `(unit_name, member_name)` | natural composite |

**`agent`**(decision 5:seed-only 物化,无 UI、无 dept 消费者)
| 列 | 类型 | 说明 |
|---|---|---|
| `name` | String(64) **PK** | |
| `model` / `max_tool_rounds` / `internal` / `description` | | 对应 `AgentConfig` |
| `role_prompt` | Text | MD body |
| `builtin_tools` | JSON | `{builtin名: enabled\|disabled}`(decision 11:声明的 builtin,引擎直读、不进 m2m) |
| `source`=`seeded` / `seed_hash` | | v0 永 seeded |

**`agent_unit`**(decision 11:agent 宇宙的 external 部分)
| 列 | 类型 | 说明 |
|---|---|---|
| `agent_name` | String(64) FK→`agent.name` **CASCADE** | |
| `unit_name` | String(64) FK→`tool_unit.name` **CASCADE** | |
| `member_state` | String(16)=`enabled` | `enabled`\|`disabled`(decision 11 成员轴;**不含等级**) |
| `source` | String(16) | `seeded`(agent MD)\| `dynamic`(B-4 UI 挂载) |
| (PK) | `(agent_name, unit_name)` | |

> 迁移:**无存量数据,4 张表就地写进 squash 的 `0001_initial_schema.py`,不加 `0002`**(全新建库假设,沿用 A 阶段 `has_blob` 姿态)。SQLite dev 走 `create_all` 自动建;**dev 现有 SQLite 库删库重建**(`create_all` 不改已有表)。

### config 格式(目录即单元)

```
config/tools/
  _example.md           # 现状单工具,保留 = singleton unit(kind=tool)
  weather.md            # singleton:unit_name=weather, 1 member full_name=weather
  github/               # toolset unit
    _set.md             # frontmatter: name/description/visibility/defer(无 endpoint)
    search_repos.md     # member,裸名 search_repos → full_name=github__search_repos
    create_issue.md     # → github__create_issue
```

- 扁平 `*.md` = 现有格式不动(`loader.py` 解析复用),归一为 singleton unit(`full_name==name`,无前缀)。
- 目录:`_set.md` 给 unit 级 `description`/`visibility`/`defer`;其余 `*.md` = member(沿用现有工具 frontmatter,但 `name` 视为裸名,loader 自动加 `<set>__` 前缀)。**member 不可重声明 `endpoint` 之外的 set 级字段**。
- **作者写裸名 / 单 `_`,loader 加 `__` 前缀**;`<unit>__*` 前缀唯一可识别(unit 名禁 `__`),tool 段可含 `__`(MCP 合法名,F)。

### 通用 reconciler(横切底座,原则 5 / decision 5)

`src/reconcile/`(新):`reconcile_config_to_db(session)` →
1. **扫 + 解析**:`config/tools/`(B)、`config/agents/`(B);`config/skills/`(C 接入)。per-type parser → 归一化记录 + 内容哈希。
2. **撞名 loud-fail**:同类型 config 内重名;seed 撞已有 `dynamic` 行 → loud-fail(不静默覆盖)。**顺带关掉 `load_all_agents` 同名静默覆盖**(agent retrofit)。
3. **幂等 upsert**(name 作 PK):新 → insert;hash 同 → skip;hash 异(同名)→ **UPDATE 定义列**,m2m(`agent_unit`/未来 dept 规则)**按 name 引用、原样保留**。
4. **clear-on-visibility 钩子**:检测 `tool_unit.visibility` 列变更 → 同事务清该资源 dept 规则(decision 10 第二条改 visibility 路径)。**B 内 dept 规则表不存在 = 空跑**,C 建表后自动生效。
5. **prune / rename**:config 删 seeded → delete(DB cascade 清 `agent_unit`);改名 = prune 旧 + insert 新 + **loud-log 丢弃的规则**(人工重授)。

**入口 = 独立脚本 `scripts/reconcile_config.py`**(同 `create_admin.py` 风格;`--dry-run` 只解析+报告)。reconcile 逻辑在 `src/reconcile/`,脚本是薄 wrapper。
- **dev**:改完 config 后**手动跑** `python scripts/reconcile_config.py`(用户定:dev 不自动)。
- **prod**:`entrypoint.sh` 在 migration 后、`exec uvicorn` 前,于 **同一 leader 槽**(复用 advisory lock)调用该脚本。**绝不在 per-worker lifespan 跑**(每副本互写,原则 5)。
- **运维触发零新增**:config bind-mount,`pause→改→resume`(recreate)重起容器即重跑 entrypoint。
- entrypoint 接线本身是 **B-1 收尾的一小步**(脚本先行、可独立验证);若暂不接 entrypoint,prod 首次部署手动跑一次亦可。

### snapshot 读 repo(B-1 建,B-2 接进引擎)

- 新 `ToolRegistryRepository`(读侧):一次性快照读 `tool_units`/`tool_members`/`agents`/`agent_units`,并提供**重建器**:
  - external 工具:`HttpTool`(从 `tool_member.definition` 重建,`full_name` 作 `.name`)。
  - agent:`AgentConfig` 等价物(builtin 从 `agents.builtin_tools`、external 从 `agent_units`)。
- **B-1 只交付 repo + 重建器 + 单测**(证明 DB 行能重建出与现有等价的形状);**接进 `controller_factory.py:79-118`(每 turn 快照替进程级 `get_tools()`/`_agents`)留 B-2**,与 resolver flip 同落。
- per-turn(非进程缓存)理由:避跨 worker 失效(否则要 pub/sub)、保 turn 内一致;成本同引擎今天每 turn 重建 `MessageEvent` 历史(原则 5)。builtin/网络工具仍进程级,artifact/sandbox 仍 request-scoped。

### B-1 是 write-only 物化(engine 不动),decision 11 等级迁移留 B-2

为保 B-1 严格纯加法,**B-1 只写不读**:reconciler 把 config 物化进 4 张表 + 提供 snapshot 读 repo,但**引擎仍走现有 `load_all_agents()`/`_load_tools()` 路径**,DB 行暂不被引擎消费。

- 当前 agent MD 全是 builtin(无 external unit)→ 物化为 `agents.builtin_tools={名: "enabled"}`、`agent_units` 空;旧 `auto/confirm` 等级从 agent 行**丢弃**(decision 11:等级 sole-source 工具定义,不存 agent 行)。
- reconciler 把 agent MD `tools:` 条目按 `BUILTIN_TOOL_NAMES`(`base.py`)分流:builtin → `builtin_tools`;匹配已注册 unit 名 / `<unit>__<tool>` full_name → `agent_units`(B-1 当前为空);其余 → loud-fail。
- **engine flip + MD 重写一起留 B-2**:`{名: enabled/disabled}` 重写、等级移到工具定义(给 `bash`/`web_fetch` 等设 `CONFIRM`、防 P0 降级)、旧字面量 loud-fail、4 读点改读 resolver、引擎切 DB 快照 —— 全在 B-2 同落,因为它们都是「引擎消费侧翻转」的同一件事。
- external 等级 = `tool_member.permission`(沿用 config/tools MD 的 `permission` frontmatter),B-1 即写入列、B-2 消费。

### 验收

- reconciler 跑两次幂等(第二次全 skip,无写);改 config 内容(同名)→ UPDATE 定义列、不动 m2m。
- 扁平 `*.md` → singleton unit(1 member,full_name==name);`<set>/` 目录 → toolset unit + 多 member,full_name=`<set>__*`。
- snapshot repo 能从 DB 行重建出 `HttpTool` + agent 元数据(`AgentSnapshot`)等价形状(单测)。
- 撞名 loud-fail:同类型 config 内重名 / seed 撞已有 dynamic 行 / unit 名含 `__` / agent MD 引用未知工具。
- **引擎行为零变化**(engine 仍走 in-memory 路径);**现有后端测试全过** + 新增 reconciler/repo/迁移用例。

---

## B-2 —— `EffectiveToolset` resolver(读点收口,纯消费重构)

**目标**:把 4 读点收成唯一解析点(decision 11),并把引擎从 in-memory `load_all_agents`/`_load_tools` 翻到 B-1 的 DB 快照。**必须在 B 收口**(B 是首个改工具集形状的阶段,拖到 C/G 则同 4 点被 refactor 三轮 = 退回架构信号)。

**包含**:
- `src/core/effective_toolset.py`:`resolve(agent, snapshot, always_allowed) -> {full_name: ToolPermission}` + 渲染/可见集等查询。
- **B 输入只静态两样**(原则 line 199):① agent 宇宙(`agent.builtin_tools` ∪ `agent_unit` external,每项 enabled/disabled,absent=不在宇宙)② tool-set 展开(`<set>__*`)。输出扁平 `{full_name: level}`。**dept/skill/MCP 输入层 C/F/G 各加一个,不再碰读点**。
- 替换:`context_manager.py:89/204/215/85`、`engine.py:816/830/900`(执行闸 + 等级)全改读 resolver;删 B-1 adapter。
- **每工具等级**从其定义查(`tool_member.permission` / builtin BaseTool.permission),绑定表不存等级。
- `search_tools`(B-3)成为第 5 读点,B-2 预留接口。

**验收**:4 读点行为与 B-1 一致(回归);resolver 单测覆盖 enabled/disabled/absent/singleton/set 展开。

### B-2 进展(2026-06-25 落地)

- **`src/core/effective_toolset.py`**:`EffectiveToolset`(`{full_name: ToolPermission}` + `__contains__`/`names`/`level`/`has_any`)+ `resolve_effective_toolset(agent_snapshot, registry_snapshot, tools)` + `resolve_all`。等级一律取自工具对象 `.permission`(绑定不存等级);unit enabled→展开成员 full_name,缺工具对象/缺 unit 静默跳过(与旧 `if name in tools` 同义)。
- **读点收口(4→resolver)**:`context_manager.build` 加 `effective_toolset` 参数,85/89/204/215 改读它;`engine.execute_loop` 加 `effective_toolsets` map,执行闸(816)+ 等级检查(901)改读它(901 等级 = `effective.level(name) or tool.permission`,绑定不再覆盖)。
- **引擎切 DB 快照**:`controller_factory` 每 turn `load_registry_snapshot(session)` → `agents=snapshot.agents`、`all_tools = builtin ∪ snapshot.external_tools ∪ artifact ∪ sandbox`、`effective_toolsets=resolve_all(...)`;缺 `lead_agent` loud-fail(指引跑 reconcile)。`dependencies._load_tools` 瘦成 builtin-only —— **external 工具自此唯一来源 = DB 快照**,不再进程级加载 `config/tools/*.md`。
- **decision-11 MD 重写 + reconcile 收紧**:`config/agents/*.md` 的 `tools:` 值 `auto/confirm`→`enabled`(bash/web_fetch 的 CONFIRM 早已在工具类上,丢弃绑定覆盖**零行为变化**);`parse_agent_seeds` 读成员态(`enabled`/`disabled`),旧 `auto/confirm` 字面量 **loud-fail**。
- **测试**:resolver 单测 `tests/core/test_effective_toolset.py`;`test_reconciler` 补 legacy-literal loud-fail + disabled 两轴;engine/build/controller 既有用例经测试桥 `tests/core/_toolset.py` 注入 `effective_toolsets`;chat E2E 用合成快照替 DB。

---

## B-3 —— 披露机制(prompt-caching catalog 挪位 + deferred + `search_tools`)

**目标**:解「30-endpoint = 30 份描述常驻」。

**包含**:
- **拆 `generate_tool_instruction`**(`xml_formatter.py:12-49`):`<format>` 协议语法块(`:17-42`)留 system prompt 稳定前缀(保 APC);**catalog 循环(`:44-45`)挪进 `_build_dynamic_context`** 成 `<available_tools>` reminder(挨 `artifacts_inventory`,`context_manager.py:204-209` 同级)。catalog 变化只失效末尾、grammar 前缀恒稳。
- **deferred 渲染**:`tool_unit.defer=True` → `<available_tools>` 只出**索引行**(set 描述 + 成员 `full_name` 列表,**不给每工具 param schema**);非 defer → 完整描述照常。**显式开关、不按 token 自动**(私有化无 tokenizer,原则 7)。
- **`search_tools` 内建工具**(注册进 `_load_tools`,`dependencies.py:189`):`select:Name,Name` 直选 / 关键词搜 → `generate_tool_instruction([匹配工具])` 作 tool_result。**必须过滤到当前 EffectiveToolset 可调集**(reviewer P1:含 enabled-but-deferred,排 disabled/absent/未授)→ resolver 第 5 读点。描述随 tool_result 留历史、**不维护已发现集**(被压缩则模型见索引行自己再 search,decision 2)。
- **`always_allow` key = 规范全名 `<unit>__<tool>`**(builtin 裸名)。
- **compaction**:`compact_agent` 提示词保留 `search_tools` 发现(同保留 artifact IDs);全 schema 不入 summary。

**验收**:defer 的 set 在 system prompt 只占索引行;`search_tools` 补全描述、过滤掉不可调工具;APC 前缀不被发现动作击穿(prompt 快照比对)。

---

## B-4 —— provider 缝 + 前端(B 最后一片)

**目标**:为 F(MCP)铺 provider 抽象;给 B 的 DB 工具配 CRUD + agent_unit 绑定 UI。

**包含**:
- **provider 抽象**(F 地基):`tool_unit.provider`(B-1 已建列)+ registry 归一化(所有来源 → 一个 tool 形 + 合并函数,仿 CC `isMcp`)。B 按 MCP 形状留缝(命名空间 `<server>__*`、按 server 名搜、动态 set),F 纯加法。
- **后端 CRUD**(`src/api/routers/` 新 `tools.py`):`tool_unit`/`tool_member` 增删改(仅 `dynamic`;`seeded` 只读);`agent_unit` 挂载 API(operator 勾选「unit 挂给哪些 agent」→ 写 `dynamic` 行)。**这是 UI 建的工具对 agent 可达的唯一入口**(否则全 agent `absent`)。三层:Repo/Manager/Router。
- **凭证统一加密落库**(见下「凭证模型」):dynamic 工具 UI 直接配 api key,补 `base_url`/凭证的 unit 级管理。
- **前端**:`scripts/export_openapi.py` + `npm run generate-types` 同步类型;管理页 = 工具 unit 列表/编辑 + agent_unit 绑定勾选 + 凭证配置(写-only)。`visibility` 列展示但 G 才接 dept 授权 UI。
- **边界**:这只是给 agent 挂能力单元,**非编辑 agent 的 prompt/model**(运行时可编辑 agent 仍 Non-goal);与 G 的部门授权正交(宇宙=agent 暴露什么,dept=哪个部门能用)。

**验收**:UI 新建 dynamic unit + 配 api key → 挂给 agent → 该 agent 可调真实出站;seeded unit UI 只读;凭证 GET 永不回明文;类型同步无漂移。

### 凭证模型(unit 级 · 统一加密落库)

动机:env-only 对 dynamic 工具反 ergonomic —— UI 里建工具却要 ssh 改 `.env` + 重启才能配 key,dynamic 就是假的。统一改为加密落库。

**红线边界(确认未碰两条 defer 的轴)**:① 这是 **backend HTTP 工具**(`HttpTool.execute` 在受信 backend、有网),不是沙盒工具 → 沙盒「永不拿凭证」红线不受影响;② 凭证绑在**工具/unit**上(如 RAGFlow 一把团队级 key),不是 per-user 身份 → 不重开「用户凭证透传(B1/B2 OAuth 金库)」那根 defer 的轴。

- **两种 key 别混**:**主密钥**(`ARTIFACTFLOW_CREDENTIAL_KEY`)= 1 把、加密/解密用,锁住所有凭证;**工具凭证**(RAGFlow key 等)= 被锁的东西,一个 unit 可挂多把。
- **独立多行表 `tool_credential(unit_name, placeholder_name, encrypted_value)`**(仿 `artifact_blobs` 与 artifacts 隔离):一 unit 多行,每行一个 `{{NAME}}` placeholder 的可逆加密值(复用现有 `resolve_secrets` 的 `{{NAME}}` 替换语义,只把值的来源从 env 换成此表 → **不退化现有多 secret 能力**)。`lazy="select"` —— per-turn 快照 / catalog / resolver 全程**不载入密文**。凭证 + `base_url` 是 **unit 级**(toolset 共享给所有 member;singleton unit==member;要 per-endpoint 不同 key = 拆 unit)。
- **resolve 一条路 = 读库解密**(可逆加密,execute 时解开替换 `{{NAME}}`),**lazy 到 execute、只解被调工具**。`HttpTool.execute` 多一条「问 credential resolver 要解密值」的路径,resolver 句柄带 live DB session(引擎执行在有 DB 的请求上下文里)。
- **dynamic**:UI 按 placeholder 填明文 → 后端用主密钥加密落行;改 = UPDATE 覆盖那行(当前主密钥重新加密)。**写-only API**:GET 永不回明文(回 `configured: true` / 掩码)。
- **seeded**:reconciler 扫定义里的 `{{TOOL_SECRET_X}}`、从 env 取值加密落行,标 seed、UI 不可改;改 = 改 env + 重 reconcile。**凭证 reconcile 独立于 unit 定义 hash**(否则定义没变 → hash skip → 凭证不更新,P1):判变靠**解密旧行 ↔ 比 env 新值**(reconcile 本就握 env 明文 + 主密钥,解密成本可忽略、无新增暴露),变了才重加密 update;定义里删掉的 placeholder 行随之 prune。**MD 仍只放引用、不放原始值**(secret 间接层 `secrets.py` + 前缀白名单保留,防工具读 JWT/DB 密钥)。
- **主密钥**:env、单把、固定。**不做轮转**(无版本化、无 re-encrypt 工具);DB dump 是废密文(无主密钥不可解),暴露面与 dynamic 同级、和 JWT secret 信任模型同级。
- **加密 ≠ 哈希**:`encrypted_value` 必须**可逆加密**(AES/Fernet)—— execute 时要解开把真 key 发出去;不存任何单向哈希(sha256 不可逆、无法还原 key,只会徒增混淆)。
- **解密值纪律**:只在 execute 期存在,永不进日志 / 事件 / `tool_member.definition` / 给模型看的 catalog(沿用现有 `HttpTool` execute 期 resolve + 失败回 generic message 的先例)。

---

## 跨片开放细节(到时敲定 / 需留意)

1. **reconciler dev 路径**:entrypoint 已覆盖容器场景;裸跑 uvicorn 的 dev 用 make target vs SQLite-only lifespan 守卫 —— B-1 落地时按团队 dev 习惯定(倾向 make target,显式)。
2. **singleton unit 的 `full_name` 约定**:`== unit_name`(无 `__`),与 set member 区分靠「有无 `<known-unit>__` 前缀」(decision 11 解析:按已知 unit 前缀剥离、不 split `__`)。
3. **`tool_member.definition` 的 schema**:B 只装 http provider 字段;F 加 mcp 运行期填充形态 —— JSON 列不锁 schema,provider 分派解释。
4. **per-turn 快照成本**:external 工具数量级小(operator 策展),HttpTool 重建廉价;若未来量大再议进程级 + pub/sub(原则 5 留待将来)。
5. **agent MD external 引用校验基准**(decision 11):import 期只对全局 ceiling 校验 unit 存在(unknown → loud-fail / warn),**不做 dept 收窄**(那是 runtime × user 部门)。
