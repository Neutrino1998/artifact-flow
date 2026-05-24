# 移交测试中心前安全评审 — 发现与修复建议

> 评审时间：2026-05-24,移交 QA/测试中心前的一次性专项安全扫查。
> 方法：后端 86 个 Python 文件 + 前端 114 个 TS/TSX,按 6 个安全域并行审计,关键项人工复核(SSRF 路径、grep 正则、web 工具禁用机制均已 `Read`/`git` 核实)。
> 基线:多用户系统,JWT 鉴权,异地双活部署(京沪),外联工具走 App→DMZ→server3,内网模式转纯离线。

---

## 总体结论

核心安全模型扎实——**认证/JWT、跨用户授权(IDOR,30+ 端点全覆盖)、SQL 注入、前端 XSS 四大块均干净**,无可利用越权或注入。真正风险集中在**外联工具(SSRF)** 与**一个 ReDoS(可卡死事件循环)**。

按门类分章,每章对应一个可独立 PR 的分支:

| 章 | 门类 | 优先级 | 建议分支 |
|----|------|--------|----------|
| 一 | SSRF / 外联工具(6 项) | 🔴 移交前必修 | `fix/sec-ssrf` |
| 二 | grep ReDoS / 事件循环(3 项) | 🔴 移交前必修 | `fix/sec-grep-redos` |
| 三 | 账户与认证(6 项 + 首次强制改密) | 🟡 建议修(部分待测试中心定标) | `feat/sec-account-auth` |
| 四 | 部署与配置(2 项) | 🟡 | `chore/sec-deploy` |
| 五 | 前端加固(3 项) | 🟢 防御纵深 | `feat/sec-frontend-csp` |

---

# 一、SSRF / 外联工具 🔴 `fix/sec-ssrf`

> 服务端请求伪造(SSRF)及相关外联工具加固。SSRF-01/02/03 是**同一根因**(只校验 scheme、不校验目标主机),一处 `validate_public_url()` + 关重定向即可统一解决;04/05/06 是同链路放大项,顺手一并修。
> 背景:`web_fetch`/`http_tool` 由本机发起请求,"服务器视角"可达云元数据 / 本机 / 内网,攻击者只要能影响 agent 输入即可读回内网内容(可读 SSRF)。

## SSRF-01 🔴 `web_fetch` 无主机校验 — 可读云元数据 / 内网凭证

**问题**:LLM 可控的 `url` 唯一校验是 `startswith(("http://","https://"))`(`web_fetch.py:112`),无主机校验。Jina 主路径对内网必然失败 → 回落到 bs4/PDF fallback,**直接从本机** `aiohttp.get(url)`,响应体原样塞回 agent 上下文。可达 `http://169.254.169.254/...`(云元数据 / 临时 IAM 凭证)、`127.0.0.1` 内网端口、RFC1918 网段。属可读 SSRF(危害最大)。

**涉及文件**:`src/tools/builtin/web_fetch.py:112`(scheme 检查)、`:266`(bs4 fetch)、`:316`(PDF fetch)。

**修复建议**:新增 `validate_public_url(url)` 共用工具(放 `src/utils/`),在**每次外联前**调用:解析主机 → IP,拒绝 loopback / link-local(`169.254.0.0/16`)/ 私网(`10/8`、`172.16/12`、`192.168/16`)/ unique-local / multicast / reserved,以及 `localhost` / `*.internal` / `metadata.google.internal`。`web_fetch` 与 `http_tool` 共用。

## SSRF-02 🔴 `http_tool` 端点无内网校验 + 可注入任意 env 变量

**问题**:`HttpTool` 向 `self._endpoint` 发请求,端点经 `{{VAR}}` 替换后从不做内网校验(`http_tool.py:77-91`)。能写入 `config/tools/*.md` 的人即可注册一个把 env 密钥 exfil 到外部、且 `permission: auto`(无需用户确认)的工具。且 `resolve_secrets` 的 `{{VAR}}` 正则 `\w+` 不限变量名,可注入 `{{ARTIFACTFLOW_JWT_SECRET}}` 等敏感值。

**涉及文件**:`src/tools/custom/http_tool.py:77-91`、`src/tools/custom/loader.py:110-120`、`src/tools/custom/secrets.py:40-47`。

**修复建议**:① resolve_secrets 后对解析出的 `endpoint` 跑同一 `validate_public_url`;② `{{VAR}}` 限定只能解析白名单前缀(如 `TOOL_SECRET_`),缺失即工具加载失败而非把占位符发出去。`config/tools/` 维持 `:ro` 挂载。

## SSRF-03 🔴 `aiohttp` 默认跟随重定向 — 绕过任何主机白名单

**问题**:`aiohttp.ClientSession.get` 默认 `allow_redirects=True`(最多 10 跳)。即便加了前置主机检查,公网 URL 仍可 `302 → http://169.254.169.254/...`,重定向目标不再校验。经典 SSRF 白名单绕过 + DNS-rebinding 面。

**涉及文件**:`src/tools/builtin/web_fetch.py:267`、`:317`(均无 `allow_redirects=False`)。

**修复建议**:`allow_redirects=False`,手动逐跳读 `Location` 并重新 `validate_public_url`;限制最大跳数。

## SSRF-04 🟡 fallback 路径无响应体大小上限 — 内存炸弹 DoS

**问题**:bs4/PDF fallback 调 `response.read()` 无 `Content-Length` 预检、无流式上限;aiohttp 自动解压 gzip → 多 GB 响应或 gzip 炸弹可 OOM worker。PDF 的 20MB 上限在全量入内存**之后**才查(`doc_converter.py:161`),保护不到下载本身。

**涉及文件**:`src/tools/builtin/web_fetch.py:276`(bs4)、`:329`(PDF)。

**修复建议**:新增隐藏配置 `WEB_FETCH_MAX_BYTES`,先查 `response.content_length`,再分块读累计字节超限即中断。

## SSRF-05 🟡 `trust_env=True` 让环境变量代理可劫持流量

**问题**:web_fetch 三处 session 用 `trust_env=True`,会读 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` 和 `.netrc`。若 env 可被污染,外联可被静默路由到任意代理(绕过应用层白名单 + 泄露 `.netrc` 凭证)。注意 `web_search.py:151` 和 `http_tool.py:77` 都没开 trust_env,**不一致**。

**涉及文件**:`src/tools/builtin/web_fetch.py:201`、`:266`、`:316`。

**修复建议**:除非确实需要代理(App→DMZ→server3),否则去掉 `trust_env`;需要代理则显式 `proxy=` 传参,关闭 `.netrc` 信任。

## SSRF-06 🟡 内网上游错误体 / 异常串回显给 LLM 与用户

**问题**:`http_tool` 出错回 `e.response.text[:500]`(`http_tool.py:131`),web_fetch 回原始 `str(e)`(多处)。配合 SSRF 会把内网服务的错误页(含内网主机名 / 栈 / token)读出来;`web_search.py:160-161` 还把完整上游错误体打到 ERROR 日志。

**涉及文件**:`src/tools/custom/http_tool.py:131`、`src/tools/builtin/web_search.py:160-161`、`src/tools/builtin/web_fetch.py:131/135/299/354`。

**修复建议**:回给 agent 的错误改为通用文案(状态码 + 脱敏原因);详情仅入 server debug 日志,不打完整上游响应体。

## ⏸ 暂缓 — 内网 web 工具 fail-open 硬开关

按决定**暂缓**(优先级低),记录待日后:`_load_tools()`(`dependencies.py:180-181`)无条件实例化两个 web 工具,禁用仅靠 agent MD 白名单(`engine.py:602`),无环境级断网开关。关键事实:删 `BOCHA_API_KEY` 能掐死 `web_search`(`web_search.py:106` 发请求前返回错误);删 `JINA_API_KEY` **掐不死** `web_fetch`(`web_fetch.py:174/194` bs4 fallback 无需 key 直连)。若日后要隔离网 fail-closed,加 `ARTIFACTFLOW_OFFLINE`:① `_load_tools` 不实例化这俩;② 或 `execute` 入口短路报错。

---

# 二、grep ReDoS / 事件循环 🔴 `fix/sec-grep-redos`

> 与 2026-05-14 事件循环卡死事故(`docs/_archive/ops/incident-2026-05-14-eventloop-wedge.md`)**同源失败模式**——同步 CPU-bound 操作钉死 GIL,击穿引擎 cancel/timeout/lease 整套协作式取消栈。本处比已修的 `update_artifact` 连一道界都没有。

## GREP-01 🔴 LLM 正则无 ReDoS / 超时护栏 — 可卡死整个事件循环

**问题**:`pattern` 用 Python `re`(回溯引擎)编译(`grep_artifact.py:250`)后对全文 `regex.finditer(content)`(`:263`/`:292` → `_scan_content`),**无墙钟、无输入上限、无算法界**。`(a+)+$`、`(.*a){20}`、`(\w+\s?)*$` 这类灾难性回溯对中等长度内容即可跑到事实无界。工具在 `engine.py:704` 直接 `await`,`re.finditer` 是同步 C 代码、钉死 GIL → **直接击穿 cancel/timeout/lease 全栈**。`fixed_strings=true` 会转义规避,但默认 `false`。

> **关键**:纯内层 deadline 检查(如 `update_artifact` 的做法)在这里**救不了**——单次 `finditer` 调用永不让出控制权,deadline 代码根本没机会执行。

**涉及文件**:`src/tools/builtin/grep_artifact.py:250`(编译)、`:263`(单 artifact)、`:292`(session)、`_scan_content`→`:83`(`finditer`)。

**修复建议**(择一,优先前者):
1. **换线性时间引擎**:`google-re2`(`re2`)支持真超时 / 线性匹配,无回溯爆炸。替换 `_compile_pattern` 底层。
2. **丢可硬杀的 worker 进程**:`ProcessPoolExecutor` + 进程级 timeout 真抢占(`task.cancel()` 抢不了同步 CPU)。
3. 无论哪种,**额外加** `GREP_CONTENT_MAX_CHARS`(隐藏配置)输入上限 + 编译后 pattern 长度上限。

## GREP-02 🟡 session 模式一次性载入全部 artifact 内容到内存

**问题**:session-scope grep 经 `list_artifacts(..., include_content=True)`(`grep_artifact.py:274`)把**全部** artifact 的 content 同时载入内存再迭代。只限了匹配输出数(`SESSION_GREP_MAX_TOTAL=200`),不限载入的 artifact 数与聚合字节。单次上传可达 20MB,累积多个大 artifact 时一次 grep 即可内存峰值飙升。与 GREP-01 叠加放大。

**涉及文件**:`src/tools/builtin/grep_artifact.py:274-276`、merge 逻辑 `src/tools/builtin/artifact_ops.py:639-693`。

**修复建议**:按 artifact 流式迭代 + 每次调用聚合字节预算(隐藏配置);或懒加载 content;超单 artifact 大小阈值的跳过/截断并给 model `hint`。

## GREP-03 🟢 `context` / `max_count` 无上界

**问题**:`context` 仅 clamp `>=0`、`max_count` 仅拒 `<=0`(`grep_artifact.py:239-247`),都无上界。超大 `context`(如 1e9)使每个匹配的上下文窗口铺满全文。比 GREP-01 轻,但是免费的 CPU/内存放大。

**涉及文件**:`src/tools/builtin/grep_artifact.py:239-247`。

**修复建议**:`config.py` 加 `GREP_MAX_CONTEXT` 等隐藏上界并 clamp。

---

# 三、账户与认证 🟡 `feat/sec-account-auth`

> ACC-02(弱密码标准)需测试中心拍板合规口径;计划中的"首次登录强制改密"正好根治 ACC-03,设计见末节。

## ACC-01 🟡 登录无频率限制 / 锁定 — 可无限撞库

**问题**:登录路径无频率限制、无失败计数、无锁定、无验证码(`routers/auth.py:63-91`)。配合 4 位密码下限,可无节制撞库 / credential stuffing。

**涉及文件**:`src/api/routers/auth.py:63-91`。

**修复建议**:per-username + per-IP 滑窗计数(Redis 已就绪,`get_redis_client`),指数退避 / 临时锁定。

## ACC-02 🟡→待定标 弱密码下限仅 4 位

**问题**:注册 / 改密 / 建用户 / `create_admin` 全是 `min_length=4`。

**涉及文件**:`src/api/schemas/auth.py`、`scripts/create_admin.py:113`、`src/api/routers/admin_users.py`。

**修复建议**:⬅️ **需测试中心定整改标准**(长度下限 / 复杂度 / 是否查常见弱口令字典)。定后统一改 `schemas/auth.py` 一处。建议下限至少 8 位。

## ACC-03 🟡 CSV 批量导入缺省密码 = 用户名

**问题**:批量导入时,行无 `password` 列则缺省密码取用户名(`admin_users.py:456`),规模化产生可猜凭证(`jdoe` / `jdoe`)。

**涉及文件**:`src/api/routers/admin_users.py:456-462`、`src/utils/csv_import.py`。

**修复建议**:强制要求 password 列,或强制随机初始密码 + **首次登录强制改密**(见末节,一并覆盖此风险)。

## ACC-04 🟡 bcrypt 5.0 对 >72 字节密码抛 ValueError → 未捕获 500

**问题**:`bcrypt==5.0.0` 对 >72 字节密码抛 `ValueError`(不再静默截断)。schema 按 128 **字符**限,非字节——60 字符多字节密码(如 `"密码"*30`=180 字节)过校验但在 `bcrypt.hashpw`/`checkpw` 炸,且无全局异常处理器 → 未捕获 500。任意用户可在注册 / 改密 / 批量导入触发。

**涉及文件**:`src/api/services/auth.py:28`(`hash_password`)、`:33`(`verify_password`);触达点 `routers/auth.py:72/124/127`、`admin_users.py:91/628`。

**修复建议**:hash 与 verify **前**统一 `plain.encode("utf-8")[:72]`(两处必须一致);或 schema 改按字节长度校验。补全局 handler 把意外 `ValueError` 映射成 400。

## ACC-05 🟢 登录时序可枚举用户名

**问题**:用户不存在时 `not user` 短路、**不跑 bcrypt**(~0ms 返回);用户存在但密码错则跑 bcrypt(~250ms)。时序差可枚举有效用户名(错误文案本身已通用,✅)。

**涉及文件**:`src/api/routers/auth.py:70-73`。

**修复建议**:用户不存在时也对固定假 hash 跑一次 bcrypt,使两分支耗时恒定。

## ACC-06 🟢 token 7 天且不可吊销,登出仅前端清 token

**问题**:access token 7 天,无 refresh / 轮换,无服务端黑白名单。唯一吊销手段是改密(bump `password_version`)。被盗 token 最长有效一周。(`is_active` 每请求 DB 校验 ✅,算 best-effort。)

**涉及文件**:`src/config.py:121`(`JWT_EXPIRY_DAYS=7`)、`src/api/services/auth.py:61`。

**修复建议**:可接受现状;若测试中心有要求,考虑缩短 token 寿命 + refresh token,或 Redis jti 黑名单支持"全端登出"。至少文档化"登出是前端行为"。

## 🆕 配套特性 — 首次登录强制改密

直接根治 ACC-03 与 ACC-02——首次强制重置后,弱/缺省初始密码不再是可利用窗口。

**实现要点**:
- `User` 表加 `must_change_password: bool`,建用户 / 批量导入 / 管理员重置密码时置 `True`。
- 加一道依赖/中间件:标志为 `True` 时,除 `POST /auth/me/password`(及登出)外的请求一律 403 引导改密;改密成功清标志。
- 复用现有 `password_version` 吊销链路,无需新增会话机制。

---

# 四、部署与配置 🟡 `chore/sec-deploy`

## DEP-01 🟢 CORS 凭证 + 通配组合无护栏(当前安全,缺断言)

**问题**:`CORS_ALLOW_CREDENTIALS=True` + `CORS_ALLOW_METHODS=["*"]` + `CORS_ALLOW_HEADERS=["*"]`。当前**不危险**——`CORS_ORIGINS` 默认是具体白名单,Starlette 不会对 `*`+credentials 反射。残留风险是操作失误:若有人设 `ARTIFACTFLOW_CORS_ORIGINS=["*"]`(env 可覆盖),Starlette 会特例化为回显 Origin → 变成带凭证的跨源读。无护栏阻止。

**涉及文件**:`src/config.py:21-24`、`src/api/main.py:204-210`。

**修复建议**:`validate_config()`(`config.py:140`)加断言:`CORS_ALLOW_CREDENTIALS` 为 True 时拒绝 `CORS_ORIGINS` 含 `"*"`。`CORS_ALLOW_HEADERS` 尽量收敛到实际用到的(`Authorization`、`Content-Type`)。

## DEP-02 🟢 依赖无 lockfile,版本不可复现

**问题**:几乎所有依赖用 `>=` 下限无上界、无 lockfile。`>=` 下限使 `pip install` 解析到构建时最新版 → 审计版本与上线版本可能不一致。(`litellm` 的 `!=1.82.7,!=1.82.8` 是排除已知坏版本,非 CVE。)

**涉及文件**:`requirements.txt`。

**修复建议**:`pip-compile` / `uv pip compile` 生成 `requirements.lock`,镜像从 lock 构建;再对 lock 跑 `pip-audit`。

## ⏸ 暂缓 / 接受 — 容器以 root 运行 + `SYS_PTRACE`

**接受降级**:代码执行将走单独 gVisor 容器(设计阶段),本容器无代码执行工具,原"RCE 爆炸半径"前提不成立;`SYS_PTRACE` 是 py-spy 取证正当需求。**待办**:gVisor 容器落地时,真正要锁死的是那个执行容器(非 root / drop caps / `no-new-privileges` / 只读根 / 网络隔离)。本容器残留风险仅"依赖级 RCE + SSRF 串联",优先级低。

**涉及文件**:`Dockerfile:32-69`(无 `USER`)、`docker-compose.prod.yml:56-57`、`deploy/docker-compose.intranet.yml:66-67`。

---

# 五、前端加固 🟢 `feat/sec-frontend-csp`

> LLM/artifact 生成内容**从不作为活动 HTML 渲染**(无 `rehype-raw`、无 iframe 预览、走转义文本路径),XSS 主轴已干净。本类是"万一未来出现 XSS / 恶意依赖"时的防御纵深。

## FE-01 🟡 JWT 存 localStorage — 未来任何 XSS 可窃取 token

**问题**:access token 存 `localStorage`(`af_token`),REST 与 SSE 都从这里读取附到 `Authorization` 头。当前无活跃 XSS sink,但一旦出现 XSS 回归 / 恶意 npm 依赖 / 恶意扩展,一行 JS 即可外带 token。

**涉及文件**:`frontend/src/stores/authStore.ts:29-30/49-54`、`frontend/src/lib/api.ts:75-81`、`frontend/src/lib/sse.ts:29-31`。

**修复建议**:优先改 httpOnly + Secure + SameSite cookie。若必须留 localStorage,作为已接受风险,以严格 CSP + 短 token 寿命补偿。

## FE-02 🟡 全站无 Content-Security-Policy

**问题**:`next.config.js` 无 `headers()`,`layout.tsx` 无 CSP meta。CSP 是缓解 FE-01 token 窃取、遏制未来注入的关键防御纵深。注意 `layout.tsx:21` 有内联主题脚本,CSP 需配 nonce / hash。

**涉及文件**:`frontend/next.config.js`、`frontend/src/app/layout.tsx`。

**修复建议**:`next.config.js` 的 `headers()` 加 CSP,如 `default-src 'self'; script-src 'self' 'nonce-…'; connect-src 'self' <API origin>; frame-ancestors 'none'; object-src 'none'; base-uri 'none'`;加 `X-Frame-Options: DENY`。

## FE-03 🟢 HTML artifact "下载原格式" 产生 `.html` 文件(接受)

**问题**:下载 `text/html` artifact 会写出 `.html`,用户日后从本地 `file://` 打开会执行其中脚本。Blob 用 `type:'text/plain'` + `a.download` 强制下载,**应用内无 XSS**,风险纯在下载后、应用源外。属"下载模型所写内容"的通用行为。

**涉及文件**:`frontend/src/components/artifact/ArtifactToolbar.tsx:54-67`。

**修复建议**:可接受。若要加固:`text/html` 也保留 `.txt` 扩展名,或提示"此文件含活动内容"。

---

# ✅ 已确认安全(供测试中心参考,勿误报)

复核后明确无问题的区域,列出以免重复告警:

- **授权 / IDOR**:30+ 端点全部 `get_current_user` / `require_admin` + 所有权校验;404-not-403、boundary-auth、cascade-from-user-delete 一致落地。无跨租户越权。
- **SQL 注入**:仓库层全参数化 ORM;LIKE 通配符已转义(`%`/`_`/`\`)。无注入点。
- **前端 XSS**:LLM/artifact 生成的 HTML/SVG/Markdown **从不作为活动 HTML 渲染**(无 `rehype-raw`、无 iframe 预览、走转义文本路径)。两处 `dangerouslySetInnerHTML` 均安全(mermaid `securityLevel:'strict'` + 静态主题脚本)。
- **自定义工具加载器非 RCE**:仅 `yaml.safe_load` + 构造 `HttpTool`,非 `http` 类型拒绝,不 exec/import 任意 Python。风险仅在 `config/tools/` 来源信任(已 `:ro` 挂载)。
- **JWT 本身正确**:显式 `algorithms=[HS256]`(无 alg 混淆),验签 + exp 默认开,空 secret 启动即 hardfail,token 内 `role` 每请求从 DB 重核(无提权)。
- **CPU/上传边界**:`update_artifact` 三重界(`MAX_FUZZY_OLD_STR_LEN` / `MAX_UNIQUE_CENTERS` / `MAX_FUZZY_WALL_CLOCK_MS`)、上传 size-check(commit 4f156e8)、文件名→artifact_id 防穿越,均人工复核正确。
- **committed secrets**:`git` 历史与追踪树均无密钥,入库的只有 `.example` 占位符。
