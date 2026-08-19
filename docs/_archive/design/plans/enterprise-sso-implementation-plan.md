# 企业统一认证（Remote Bearer UserInfo）接入 —— 实施计划

> 状态：A、B 阶段已完成，C 阶段待开始
> 起草：2026-07-28 · 最后更新：2026-08-18
> 关联资料：
> - `temp/统一认证/AI应用接入统一身份认证指引.doc` —— 上游登录回跳和用户信息接口说明。
> - `temp/统一认证/auth-probe/` —— 已完成的生产环境连通性与返回结构探针；探针导出含个人信息，不进入版本库。
> - `src/core/management/department_manager.py::DepartmentManager.resolve_path()` —— 现有“名称路径 → 本地部门树 → 末级 department_id”实现，本计划直接复用。

## 本文档定位

这是一份 **plan，不是详细设计**。它负责锁定一期范围、架构边界、实施顺序和验收标准；A 阶段开工时已经在本文锁定身份自然键、Provider 配置载体、state 和无部门语义，函数签名由实现保持局部清晰。

已取得的信息足够启动实现。尚需统一认证平台确认的稳定用户 ID、一级/二级直属用户返回形状和正式回调地址，属于上线门禁，不阻塞主干先用 stub 完成通用能力。

## 进度

**当前**：生产统一认证门户和 `/auth/info` 已通过独立探针验证；用户、启用状态、末级部门和从根到末级的名称路径均可取得。A、B 阶段的通用后端能力和浏览器闭环已完成实现与回归，下一步是 C 的内网生产配置和受控切换。

| 阶段 | 内容 | 依赖 | 状态 |
|---|---|---|---|
| A | `main`：通用远程 Bearer 用户信息交换与本地身份落库 | 无 | 已完成 |
| B | `main`：统一认证登录入口、回调页和双入口 UX 闭环 | A | 已完成 |
| C | `intranet`：生产配置、真实账号验收和受控切换 | A、B 已合入 `main` | 未开始 |

依赖关系：

- **B 依赖 A**：前端只消费 A 提供的公开认证配置、登录 state 和 exchange 响应，不解析企业专有用户 JSON。
- **C 依赖 A、B**：必须先让通用功能完整进入 `main`，再同步到 `intranet` 配置生产地址和字段映射。
- **关键路径**：A → B → C；A 可按“身份模型/Provider client → exchange/JIT provisioning”两个小 commit 切片。

分支与发布策略：A、B 是通用功能，默认关闭且不改变现有登录行为（统一 JWT 时长调整除外），逐阶段进入 `main`。之后将 `main` 完整同步到 `intranet`，C 只保留现场部署配置和必要的内网站点文案；若 `intranet` 仍需企业专属 Python 解析代码，应回到 `main` 补通用适配边界。生产 Provider 配置写入目标机 `control/auth/remote_bearer_userinfo.yaml` 并只读挂载，不提交真实现场配置；静态 secret 若未来出现仍留在 `control/.env`，一期配置不含 secret。

## 目标与范围

为 ArtifactFlow 增加一种可配置的 **Remote Bearer UserInfo** 登录来源：浏览器从企业门户取得短期 bearer token，ArtifactFlow 后端用它读取用户信息并换发自己的 8 小时 JWT；后续所有业务接口仍只接受 ArtifactFlow JWT。

**本计划包含**：

- 保留本地用户名/密码入口，同时增加企业统一认证入口；两种入口最终签发同一种 ArtifactFlow JWT。
- 通过配置描述登录地址、回跳参数、userinfo 地址和最小字段映射，不把本次企业接口写死在业务代码中。
- SSO 首次登录即时创建独立的 ArtifactFlow 本地用户，后续按稳定 Provider subject 找回并更新展示信息和部门归属；ArtifactFlow 内部 `users.id` 继续由本系统生成，认证自然键统一存为 `(auth_provider, auth_subject)`：local-password 的 subject 就是 username，远程 Provider 的 subject 是上游稳定用户 ID。
- 本地密码与 SSO 是不同认证来源；即使 username 相同也允许作为两个独立用户共存，各自拥有独立的内部 ID、数据、角色、启用状态和部门归属。
- 使用 `superiorDeptName` 的完整名称路径复用现有部门树解析器，不存储或同步外部部门 ID。
- ArtifactFlow JWT 统一调整为 8 小时；上游 token 只用于当次 exchange，不进入业务请求、数据库、日志或浏览器持久存储。
- 登录 state、防 token 泄漏、失败日志、配置校验、自动化测试和内网发布/回滚说明。

**Non-goals（本期明确不做）**：

- **把该平台声明为 OAuth2/OIDC** —— 当前实测只能确认“门户回跳 bearer token + 远程 userinfo”，没有 authorization code、client credential、discovery、refresh token 等标准协议证据。
- **旧本地用户与 SSO 用户迁移、关联或合并** —— 不按 username、姓名或部门猜测同一自然人；跨认证来源同名默认就是两个独立用户。若以后需要迁移历史数据或绑定多种登录方式，另立经过身份核验和数据归属审计的方案。
- **外部部门 ID、全量组织同步和自动重组** —— 一级/二级缺少 ID，投入同步机制得不偿失；部门改名或搬家按新名称路径创建，旧路径由管理员处理。
- **映射上游角色和数据权限** —— `isAdmin`、roles、jobs、dataScopes 不授予 ArtifactFlow 权限；本地 `role` 和部门授权仍是唯一权限来源。
- **PAT/API Key** —— 用户自建长期 token 是独立安全模型，另开计划，不与交互式 SSO 登录混做。
- **静默刷新或保存上游 token** —— 8 小时到期后重新走统一认证；不保存上游 token 实现后台续期。

**完成后的可观察结果**：

- 用户点击“统一认证登录”，从门户回跳后进入 ArtifactFlow，不再输入一套 ArtifactFlow 密码。
- 首次登录创建本地用户和缺失的部门路径；再次登录复用同一外部身份，不产生重复用户或重复同级部门。
- 已有本地用户与新 SSO 用户 username 相同时仍可分别登录，并得到不同的 ArtifactFlow `user.id`；两者不会共享会话、Artifact、角色、启用状态或用户级配置。
- 本地应急管理员仍可通过原密码入口登录；SSO-only 用户不能通过本地密码入口或改密接口获得密码登录能力。
- REST、SSE 和 resume 继续只携带现有 ArtifactFlow JWT，业务 API 不理解企业 token。
- 未启用 SSO 配置时，`main` 的现有部署和登录页面行为保持不变。

## 原则与决策

1. **这是一次上游 bearer token exchange，不是第二套业务 token。**
   - 浏览器回跳得到 `authorization_key`，后端只用它调用配置的 userinfo 接口。
   - userinfo 验证成功后立即签发 ArtifactFlow JWT；上游 token 用完即丢。
   - 所有现有鉴权依赖、REST 和 SSE 继续使用内部 JWT，因此核心业务授权路径不分叉。

2. **一个认证来源对应一个本地 `User`，跨来源同名不合并。**
   - 会话、Artifact、角色和部门 FK 都需要本地用户主键，无法只在内存里使用企业身份。
   - ArtifactFlow `users.id` 始终由本系统生成；上游四位 ID 等 provider subject 只作为外部身份属性存储，不能替代内部主键。
   - 所有 `User` 统一以 `(auth_provider, auth_subject)` 唯一定位：local-password 的 `auth_subject=username`，远程 Provider 的 `auth_subject=上游稳定 subject`。数据库只保留这一条身份唯一约束，不建立 external-subject 映射表或条件 username key。
   - username 不再全局唯一：local-password 用户由结构性约束保证 `auth_subject=username`；remote-provider 用户的 username 仅供展示和检索，不参与身份定位，也不要求唯一。
   - 同一自然人通过本地密码和 SSO 登录时，默认得到两个内部 ID 和两套独立数据。系统不建立映射表、不自动转移所有权，也不根据同名提供跨账号访问。

3. **双登录入口共用一套 8 小时 ArtifactFlow JWT。**
   - 本地密码和企业 SSO 是两种身份校验入口，不是两套 JWT 验证体系。
   - 两条入口都调用统一身份查询 `(auth_provider, auth_subject)`：本地密码使用 `("local_password", username)`，SSO exchange 使用 `(配置的 provider id, 上游 subject)`。两条路径最终都把各自的 ArtifactFlow `users.id` 写入 JWT subject。
   - `get_current_user` 仍逐请求读取本地用户的 `is_active`、role 和当前部门；管理员本地禁用立即生效。
   - 上游账号在现有 ArtifactFlow JWT 存续期间被禁用，最长仍可能使用到 8 小时到期；一期接受该窗口，不为此逐请求调用上游。

4. **部门以完整名称路径建树，不使用外部部门 ID。**
   - 本地节点身份仍是内部 `departments.id`，名称路径的逐级自然键是 `(parent_id, name)`，不能仅按全局单个名称匹配。
   - exchange 严格拆分 `superiorDeptName`，拒绝路径内空层级，并校验路径末级等于 `dept.name`；然后直接调用现有 `DepartmentManager.resolve_path()`。
   - 用户可以直属任意一级；返回路径有几级就绑定最后一级。
   - 上游明确同时缺少部门路径和末级部门时，允许创建/更新 `department_id=NULL` 的用户；若用户此前有部门，下次登录明确无部门时必须清空归属，避免继续继承旧部门权限。只有路径和末级一有一无、末级不一致等矛盾形状才拒绝。
   - 部门改名/搬家会形成新路径，用户下次登录转到新节点；旧节点、旧授权和清理由管理员处理，这是明确的 best-effort 边界。

5. **企业信息只提供身份和部门事实，不覆盖本地授权决策。**
   - 新 SSO 用户默认 `role=user`，上游 `isAdmin`、roles、jobs、dataScopes 一律忽略。
   - 成功登录只更新由 `(auth_provider, auth_subject)` 找到的 SSO 用户之 username、display name 和 department；不得触碰同名本地用户、自动提升 role，或把被本地管理员禁用的 SSO 用户重新启用。
   - 同名本地用户和 SSO 用户的 role、`is_active`、部门及用户级授权互不联动；相同完整部门路径可以让两者引用同一个本地部门节点，但不会让两个用户成为同一主体。
   - 上游 `enabled=false` 时拒绝创建或签发新会话；已有内部 JWT 的失效窗口遵循决策 3。

6. **通用性限定在一种清晰协议形状，不实现任意认证脚本平台。**
   - `main` 支持一个可配置的 `remote_bearer_userinfo` Provider：登录 URL、回跳参数、userinfo URL、超时和必要 JSON 字段路径。配置由启动时直接读取的 `config/auth/remote_bearer_userinfo.yaml` 提供；生产用 target-local `control/auth/` 只读覆盖，不经 config→DB reconcile，也不支持热更新。
   - Provider 标识是 ArtifactFlow 为该认证来源配置的固定名称：同一身份系统即使地址变化也继续使用原标识，接入完全不同的身份系统时使用新标识；一期不为此增加改名、版本或同步机制。
   - 字段映射只支持完成规范化 DTO 所需的简单取值和部门路径分隔，不在配置中引入脚本、模板语言或任意表达式。
   - 登录、callback 和 userinfo URL 支持绝对 HTTP/HTTPS；HTTPS 始终校验证书，HTTP 必须在配置中显式 `allow_insecure_http: true`，不提供关闭 TLS 校验的选项。企业私有 CA 继续复用 `control/trust/ca-certificates/`。
   - OIDC、LDAP、多 Provider 并存等真实需求出现后再新增明确 provider 类型，不把一期适配器提前抽象成万能框架。

7. **回跳 token 按凭证处理，任何错误出口同时满足用户脱敏和运维可诊断。**
   - 登录 start 产生短期、不可猜且绑定浏览器的 state；callback/exchange 校验后立即失效，防止登录 CSRF。
   - state 是 ArtifactFlow 自己生成并嵌入 `entryPath` callback 的随机握手值，上游只需原样保留回跳 URL。Redis 实现保证多副本共享和原子一次性消费，InMemory 实现服务无 Redis 的单进程开发模式；它不替代或保存上游 bearer token。
   - callback 读取 token 后先清理地址栏，再发同源 POST；不得写 localStorage、cookie、导出文件或日志。
   - 上游 5xx、超时、返回结构错误必须记录 request ID 和脱敏原因；401/403 返回统一登录失败，不记录 bearer 内容。
   - 部署入口必须确认反向代理不记录 callback query string；上游 API 当前为 HTTP，C 阶段须确认链路处于受控网络或取得 HTTPS/mTLS 地址。

**上线前待确认**：

- `/auth/info.user.id` 是否永久稳定且不回收 —— 当前以其字符串值作为 external subject；最晚 C 阶段前由平台方确认。
- 一级、二级直属用户的 `superiorDeptName` 是否分别结束在其所属一级、二级，且末级与 `dept.name` 一致 —— 最晚 C 阶段用真实账号验收。
- 企业平台登记的正式 `entryPath`、HTTPS 域名和代理日志策略 —— B 可用本地 stub 开发，C 上线前必须敲定。

## 实施阶段

### A — `main`：通用后端 exchange 与本地身份落库

**做什么**：在不改变现有业务鉴权入口的前提下，增加默认关闭的远程用户信息 Provider、SSO 本地主体和完整 exchange 服务。

**包含**：

- **认证来源模型**：让每个本地用户明确记录非空 `auth_provider` 和 `auth_subject`，统一以 `UNIQUE(auth_provider, auth_subject)` 定位主体；local-password 行必须满足 `auth_subject=username`，remote-provider 行的 subject 取上游稳定 ID 且 username 可重复；不新增身份映射表。
- **SSO-only 凭证边界**：SSO 用户不能走本地密码验证、首次/周期改密闸门或 admin 重置密码；精确列形状和约束在本阶段一次性确定并实现。
- **Provider 配置与 client**：启动时直接加载独立 YAML；启用时严格校验登录地址、回跳参数、userinfo 地址、字段映射、超时和 HTTP/TLS 策略；配置缺失或矛盾则 fail-to-start。配置不物化到 DB，修改后重启全部 Backend 生效。
- **规范化 userinfo**：将上游 JSON 转换为固定内部 DTO：provider、subject、username、display name、enabled、department path、leaf name；原始 roles/jobs/dataScopes 不进入用户授权模型。
- **exchange/JIT provisioning**：校验上游 token → 读取 userinfo → 校验账号和部门形状 → 有完整路径时复用 `DepartmentManager.resolve_path()`、明确无部门时使用 NULL → 按 `(auth_provider, auth_subject)` 查找或并发安全地创建带全新内部 ID 的用户 → 更新允许同步的资料 → 签发 8 小时内部 JWT；不得按展示 username 查找、认领或更新远程主体。
- **公开认证握手**：提供未登录页面所需的最小公开配置，以及 start/state/exchange 端点；不向前端暴露内部 userinfo 地址或字段映射。
- **匿名资源准入**：start 在 state 签发前执行 per-IP 429 和 Redis 共享全局 503 准入；Redis/InMemory state 均有硬容量，userinfo 每 Backend 使用独立有界连接池。容量项属于 ENV 部署配置，不进入 Provider YAML。
- **密码入口隔离**：本地密码登录只查询 local-password 用户；若只有同名 SSO 用户则执行等时假哈希并返回普通 401，若同时存在同名本地用户则只认证该本地用户。`get_current_user`、REST 和 SSE 不增加上游分支。
- **一次性 schema 切换**：`auth_provider`、`auth_subject`、统一身份唯一约束、SSO-only 密码约束及所有身份查询在同一版本完整落地；不设计过渡态、双写或新旧应用混跑能力。

**不包含**：

- 登录页和真实企业环境配置。
- 旧用户迁移/关联、跨账号数据共享、部门全量同步、外部角色同步。

**开工合同（已敲定）**：

- `users.auth_provider`、`users.auth_subject` 均非空，唯一约束仅 `UNIQUE(auth_provider, auth_subject)`；存量用户回填 `("local_password", username)`。local-password 必须有 hash 且 `auth_subject=username`；remote-provider 必须无 hash、无改密闸状态。
- MySQL/TDSQL 的两个认证自然键列显式使用 `utf8mb4_bin`，唯一约束和身份查询不得继承大小写不敏感的数据库默认排序规则。
- 所有单主体身份查询统一走 `(auth_provider, auth_subject)`；管理员列表/搜索仍按非唯一 username 展示多个同名用户。
- Provider 使用独立 YAML，字段路径采用仅对象逐级取值的点路径；不复用 HttpTool 的 DB reconcile，不支持数组、JMESPath、脚本或表达式。
- 明确无部门是合法、最小权限状态；不完整或矛盾的部门数据才是 Provider 合同错误。

**验收项**：

- Provider 关闭时现有本地登录行为保持不变，SSO 入口与新 exchange 不可用；已有 SSO 用户及其数据不被修改，已签发的 ArtifactFlow JWT 仍按 8 小时有效期使用。OpenAPI 允许增加向后兼容的 SSO 端点和响应字段；配置不完整时启动失败并指出缺失项。
- stub 上游的成功响应能创建/复用同一用户，签发 `expires_in=28800` 的内部 JWT；JWT 可访问现有 REST/SSE 鉴权路径。
- 同一 `(auth_provider, auth_subject)` 并发首次登录只产生一个用户；内部 `users.id` 由 ArtifactFlow 生成且不等于上游 subject。
- local-password 与 remote-provider 的同名用户可同时存在并分别登录，拥有不同内部 ID；同一 remote-provider 下 username 相同但 external subject 不同的用户也可共存。SSO exchange 不按 username 读取、认领或更新任何用户。
- 一级、二级、三级和同名不同父路径均正确创建/复用；明确无部门创建 NULL 归属并清除旧归属；路径内空层级、路径/末级单边缺失、末级不一致和超长字段均拒绝并有测试。
- 上游 disabled、401/403、超时、5xx、非 JSON、缺字段均返回脱敏错误；所有 5xx 有带 request ID 的 ops 日志，日志和响应均不出现 bearer token。
- SSO 用户行没有可用的本地密码且不能改/重置密码；存在同名本地用户时，密码入口得到的始终是本地用户身份。本地用户的密码策略、失效机制和管理员能力回归通过。
- 数据库迁移和新应用作为一个不可拆分版本验收；测试确认不存在仍以未限定 username 定位单个登录主体的路径，并明确禁止新旧应用混跑。

**进展**：

- 已完成：`0008` 一次性身份迁移、统一且 MySQL 大小写敏感的 `(auth_provider, auth_subject)` 查询、SSO-only 密码约束、8 小时 JWT、启动期严格 YAML client、Redis/InMemory 有界一次性 state、匿名配置/start/exchange 准入、JIT 部门/用户同步和 `control/auth/` 部署挂载均已落地。
- 自动化覆盖同名跨来源隔离、同 Provider 同 username 不同 subject、并发首登、明确无部门清权、上游错误脱敏、SSO 改密隔离及 SQLite 迁移往返；全量无外部依赖后端回归、前端类型/测试和 Go 测试通过。

### B — `main`：登录入口与浏览器闭环

**做什么**：让用户在现有登录页选择企业统一认证，并把回跳 token 安全交换成现有 authStore 使用的内部 JWT。

**依赖**：A 的公开认证配置、start/state/exchange 合同稳定。

**包含**：

- Provider 启用时显示“统一认证登录”入口；本地用户名/密码保留为独立入口，供本地应急管理员使用。
- start 使用配置的 return 参数跳到企业门户；短期 state 与浏览器绑定，callback 只接受匹配且未过期的流程。
- callback 文档请求先由 middleware 内部 rewrite 到无 query 的回调壳，阻止 Next App Router 把上游 token 序列化进 RSC/DOM；浏览器地址仍保留原参数，组件在第一次 JavaScript 调用栈中抓取后立即通过 `history.replaceState` 清理完整 query，再读取公开配置指定的 token 参数并调用 exchange。callback 文档使用 `Referrer-Policy: no-referrer`；成功写入 ArtifactFlow JWT/UserInfo 后用全页面 replace 进入首页，失败仅以一次性的允许列表错误类别和可选 request ID 跨文档传递，再全页面 replace 到无 query callback，确保原始导航条目随旧 Document 一起销毁。
- UserInfo/UserResponse 暴露认证来源、`can_change_password` 和 `can_edit_profile`。remote-provider 的 username、display name 和 department 是 Provider 管理的身份/组织事实，本地自助与管理员 API 均不得修改；本地 role、`is_active` 仍是 ArtifactFlow 授权决策并可由管理员修改。SSO 用户界面不显示改名/改密入口，管理员详情将 Provider 管理字段设为只读、不能重置密码，批量设置部门逐用户拒绝 SSO 主体。
- 管理员界面在用户名旁明确展示来源和内部 ID，以区分跨来源同名用户。SSO 用户仍可删除，但界面明确提示上游账号仍启用时下次登录会以同一 Provider subject 重新创建新的本地主体；禁用是阻止登录且保留数据的常规操作。
- exchange 失败、state 失效或 JWT 到期时给出可理解提示并允许重新发起 SSO，不在页面中展示上游原始错误。
- 重新生成 OpenAPI 前端类型，REST 和 SSE 继续复用当前 Authorization header 逻辑。

**不包含**：

- 静默续期、refresh token、将上游 token 缓存在浏览器。
- 内网真实门户联调。
- Provider 管理字段的本地 override/合并规则；一期直接拒绝本地修改，避免产生下次登录被静默覆盖的临时状态。

**验收项**：

- 浏览器 stub E2E 覆盖：首次登录、再次登录、用户取消/失败、state 缺失/过期、callback 刷新以及本地管理员登录。
- 浏览器 stub E2E 覆盖同一 username 的本地与 SSO 用户：密码入口进入本地内部 ID，SSO 入口进入远程内部 ID，两者会话和用户态互不串用。
- 地址栏在 exchange 网络请求发出前已移除 token；localStorage、cookie、控制台、前端错误和下载内容均无上游 token。
- SSO 用户自助改名、管理员改显示名/部门/密码均被后端拒绝，前端也不提供这些入口；管理员仍可修改 role、启用状态和执行带明确重建提示的删除。混合更新请求整体拒绝，不产生部分写入。
- SSO 登录成功后，现有会话列表、发消息、SSE、resume 和退出登录无需专有分支即可工作。
- Provider 关闭时 SSO UI 不出现，现有密码登录页面和测试保持不变。
- 前端单测、类型检查、构建以及后端目标测试全部通过。

**开工合同（已敲定）**：

- 登录页在 Provider 启用时以 SSO 为主要入口，本地账号表单以独立分隔区始终可见；Provider 关闭或公开配置暂时加载失败时，不阻断本地应急登录。
- Profile 字段所有权一次性锁定：remote-provider 管 username/display name/department；ArtifactFlow 管 role/`is_active`。不增加本地 override 状态或下次登录 merge 规则。
- 自动化分三层：Pytest 使用真实 Manager/Repository 和合成 userinfo 验证身份与写入约束；Vitest 验证组件与 callback 调用顺序；Playwright 使用真实浏览器和测试专用假门户/userinfo 验证导航、cookie、地址栏、Referer 和浏览器存储。生产返回只用于校准不含个人信息的合成 fixture，不成为自动测试依赖。

**进展**：

- 已完成双入口登录页、一次性 callback/exchange、JWT 到期提示、公开类型生成以及 SSO 资料/密码能力在自助与管理员界面的只读态。
- 后端强制 Provider 字段所有权并保持 role/启用状态可管理；批量部门变更逐用户拒绝 SSO，删除确认明确重新创建语义。
- Vitest 覆盖 callback 顺序、错误脱敏和管理界面能力；Playwright 通过动态端口和测试假门户覆盖重复登录、同名本地/远程身份、取消、过期、刷新及 token 不进入持久化、DOM、console、下载、Referer、sessionStorage 或当前 Document 的 Performance Timeline。

### C — `intranet`：生产配置与受控切换

**做什么**：把已进入 `main` 的通用能力同步到 `intranet`，只通过现场配置接入已验证的企业生产服务，并完成真实账号上线验收。

**依赖**：A、B 已合入 `main`；`intranet` 已完整包含 `main` 的提交。

**包含**：

- 按发布纪律确认 `git log intranet..main` 为空且构建时 checkout 为 `intranet`。
- 在目标机 `control/auth/remote_bearer_userinfo.yaml` 配置固定的 Provider 标识、`https://ucs.cncc.cn/dashboard`、生产 `/auth/info` 地址、`entryPath`、`authorization_key` 和各字段路径；真实现场文件不进 git。同一统一认证来源以后变更地址时保留该标识，接入不同身份系统时使用新标识。
- 在单一维护窗口一次性切换：停止全部旧实例并完成数据库备份，执行 schema 迁移，启动已包含来源限定查询和生产 Provider 配置的新版本，再进行 smoke；不得让旧应用连接迁移后的数据库，也不做分阶段启用。若切换失败则整体恢复该备份，并明确接受丢弃备份之后由本地或 SSO 用户产生的全部写入。
- 用服务器连通性检查和一级/二级/三级真实账号 smoke 验证 subject、enabled、名称路径、末级校验和重复登录。
- 保留或新建至少一个仅本地可用的应急管理员；上游 admin 标记不能替代该 bootstrap。
- 生产已有本地用户默认原样保留；同名 SSO 用户作为独立账号创建。是否停用或删除旧本地账号属于上线后的显式运维决定，应用迁移和启动过程绝不自动关联、停用或清理用户。
- 更新内网运行文档、健康检查、脱敏日志检查、失败回退和操作窗口记录。

**不包含**：

- 在 `intranet` 增加企业专属 Python adapter；发现通用映射不足时回 `main` 修正。
- 自动删除旧部门或迁移旧部门上的 skill/tool 授权。

**验收项**：

- Backend 所在网络可访问生产 userinfo；门户可回跳正式 HTTPS callback，反向代理日志不包含 `authorization_key`。
- 一级、二级、三级用户分别登录后绑定正确末级，重复登录不产生重复用户/部门；同父同名唯一约束在并发下成立。
- 选择一个与生产本地用户同 username 的真实 SSO 账号验证：两者可分别登录、内部 ID 不同、数据和权限互不串用，管理界面可明确辨认来源。
- 普通 SSO 用户始终以本地 `role=user` 首次创建；企业管理员不会自动获得 ArtifactFlow admin。
- 禁用同名本地用户不改变 SSO 用户状态，反之亦然；被本地禁用的 SSO 用户不能由下一次 exchange 自动启用，上游 disabled 用户不能取得新 ArtifactFlow JWT。
- 8 小时 JWT 到期行为符合预期：运行中的服务端任务不因 token 到期被取消，新请求或 SSE 重连要求重新登录。
- 关闭远程 Provider 后本地应急管理员仍能登录，SSO 入口和新 exchange 不可用；已有 SSO 用户及其数据保持不变，已签发的内部 JWT 可使用到期。若切换失败，不允许只降级应用继续使用新 schema；回退必须停止新版本并整体恢复切换前应用与数据库，接受丢弃备份后的全部写入。
- 若删除旧用户，已单独确认其会话、消息、Artifact 等 FK cascade 影响并完成可恢复备份。

**进展**：

- 尚未开始。

### 整体完成条件

- A、B 已以通用实现进入 `main`，C 只使用部署配置完成企业接入。
- 从企业门户回跳到 ArtifactFlow 业务请求的端到端流程通过，业务 API 从未接收上游 token。
- 本地登录、SSO 登录、跨来源同名隔离、用户禁用、部门继承授权、SSE/resume 和 8 小时过期行为均有自动化或真实 smoke 证据。
- 配置、日志脱敏、备份、发布和回滚文档收口；上线前待确认项均有结论。
- PAT、组织全量同步、旧身份迁移等遗留项已明确移出范围。

## 关键风险

- **外部 subject 不稳定或被回收** —— 触发信号：平台无法确认 `user.id` 合同，或同一人员返回不同 ID；影响：重复主体或错误数据归属；应对：C 前取得确认，不退化成 username 猜测关联。
- **用户误以为同名账号共享数据** —— 触发信号：用户从本地入口改走 SSO 后看不到原会话或 Artifact；影响：被误报为数据丢失，或管理员误操作两个同名主体；应对：登录页和管理页展示认证来源，上线公告明确两者独立，任何迁移另走经身份核验的显式流程。
- **一次性切换不完整** —— 触发信号：迁移时仍有旧实例存活，或只回退应用而未恢复数据库；影响：旧查询误读跨来源同名行，造成登录报错或主体选择错误；应对：维护窗口内先停净旧实例，应用和 schema 作为同一版本发布，失败时整体恢复切换前应用与数据库，并接受丢弃备份后的全部写入。
- **回跳 bearer 泄漏** —— 触发信号：浏览器历史、代理访问日志、前端存储或错误日志出现 `authorization_key`；影响：8 小时内可重放企业身份；应对：短 state、立即清 URL、只 POST exchange、全链路日志验收和受控网络/TLS。
- **名称路径发生组织改名/搬家** —— 触发信号：相同人员下一次登录出现新路径；影响：产生新部门节点，旧节点授权不会自动迁移；应对：接受 best-effort，管理员核对并迁移/清理，不增加残缺外部 ID 同步层。
- **旧用户清理误删业务数据** —— 触发信号：删除影响预览包含会话或 Artifact；影响：FK cascade 永久删除用户数据；应对：清理不自动化，先备份、核对并在发布窗口显式执行。
- **上游不可用放大为登录不可用** —— 触发信号：userinfo 超时/5xx；影响：新 SSO 会话无法建立，但已有内部会话继续；应对：短超时、明确 502/503、带 request ID 日志和本地应急管理员。

## 变更日志

- 2026-07-28 **起草**：生产探针确认 `/auth/info` 足以提供身份、启用状态和完整部门名称路径；锁定“Remote Bearer UserInfo exchange → 本地 JWT”主线。
- 2026-07-28 **部门范围收缩**：确认一级/二级祖先没有 ID 后，放弃外部部门 ID 和同步机制，改为完整名称路径 + 本地 `(parent_id, name)` 建树并接受改名/搬家的 best-effort 边界。
- 2026-07-28 **分支策略锁定**：通用能力先进入 `main`，再同步到 `intranet` 通过目标机配置接入生产服务；内网分支不维护第二套认证实现。
- 2026-08-17 **身份隔离决策**：ArtifactFlow 内部 ID 与上游 subject 分离；local-password 与 remote-provider 允许同 username 但始终是两个独立用户，不自动关联、共享数据或迁移所有权。
- 2026-08-17 **一次性切换决策**：认证来源 schema、来源限定查询和 Provider 配置在同一维护窗口完整切换；不实现过渡兼容层或新旧应用混跑，失败时应用与数据库整体恢复。
- 2026-08-17 **跨来源隔离收口**：local-password 与 remote-provider 不按同名关联；Provider 采用固定配置标识，不增加改名或同步机制；关闭 Provider 不修改已有用户和数据。统一自然键的最终列形状由随后 A 阶段开工合同锁定。
- 2026-08-18 **会话时长调整**：本地密码与 SSO 登录统一签发 8 小时 ArtifactFlow JWT；上游 token 仍只用于当次 exchange，不保存或用于后台续期。
- 2026-08-18 **A 阶段开工合同**：本地与远程身份统一为 `(auth_provider, auth_subject)`，只保留一条身份唯一约束；Provider 改用启动期直读 YAML，HTTP/HTTPS 均支持且 HTTP 需显式确认；state 采用 Redis/InMemory 一次性存储并绑定浏览器；明确无部门允许以 `department_id=NULL` 登录，后续无部门会清除旧归属。
- 2026-08-18 **A 阶段完成**：后端 exchange、身份/密码结构约束、配置与部署挂载、OpenAPI/前端类型以及自动化回归全部落地；真实登录页和 callback 地址栏清理仍严格留给 B。
- 2026-08-18 **A 阶段资源与身份收口**：Provider YAML 仅保留连接/映射协议，ENV 承担 per-IP/全局 start 准入、state 容量和每实例 userinfo 并发；Redis state 改为单个有界 ZSET。MySQL 身份列使用 `utf8mb4_bin`，URL 通过运行时 HTTP parser 加严格 hostname 校验，`af_sso_state` 固定为保留参数名。
- 2026-08-18 **B 阶段完成**：登录页双入口、callback/exchange、SSO 用户自助与管理员只读边界、项目级 Vitest/Playwright 回归全部落地。浏览器测试发现 Next App Router 会在 client effect 前把 callback query 序列化进 RSC，且 client-side route replace 不销毁 Performance Navigation 条目；最终通过 middleware queryless 内部 rewrite、两次 middleware 均保持 `no-referrer` 以及 exchange 后全页面 replace，使上游凭据不留在 HTML/DOM、后续 Referer 或当前 Document Performance Timeline。
