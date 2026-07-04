# 多副本/多机 部署·运维·可观测(ops bundle)—— 实施计划

> 状态:规划完成,未开工
> 起草:2026-07-03
> 前序产物:
> - `skill-system-implementation-plan.md`(本目录)Phase B 乙2 —— release/serve 拆分(reconcile 单次部署门禁 + PG advisory lock)+ nginx 变量 `proxy_pass` 已合 main;其「真机 `--scale` 验证」挂账由本 plan Phase B 承接。
> - `deploy/MULTI-REPLICA.md` —— 单机多副本 runbook + 验证清单(= 本 plan Phase B 的执行脚本)。
> 现状基线(2026-07-02 评估,起草前做):**执行正确性一侧多副本条件已齐** —— reconcile 单次(release job)、控制面状态全外置(Redis lease/cancel/interrupt Pub/Sub + SSE Redis Stream `last_event_id` 任意副本续读)、文件/blob 进 DB(无本地文件粘性;唯一粘性 = turn producer 绑接单副本,合理契约)、sandbox reaper 有 InMemory 拒启守卫。**缺口全在运维/可观测一侧**:日志与观测 jsonl 无实例维度(`RequestContextFilter` 只注入 request_id/conv_id/message_id 三元组);观测 jsonl 单进程假设、多副本共享卷上 rotate 互相覆盖(`jsonl_sink.py` 自注);`/admin/runtime` 的 sampler 快照/在途 turn 数是进程本地(LB 随机路由 → 读数跳变、漏看其余副本);unhealthy 不自动重启(compose `restart: unless-stopped` 不认 healthcheck,wedge 副本挂着不死还继续被轮询);多机编排零实现(仅 `release.sh` 尾部的手工 scp/ssh 配方)。

## 本文档定位

这是一份 **plan,不是详细设计**。讲清每个阶段做什么、为什么、什么算完成;落实细节(字段、具体改哪些代码)留到开工那个阶段再敲定。同时是**跨 session 跟踪文档**:新 session 先读「进度」知道做到哪;每推进一阶段更新状态 + 「变更日志」追加结论。

覆盖三件事:**部署编排**(多机单命令发布)、**负载均衡**(单机/多机统一由反代负责)、**可观测与自愈**(实例身份 + 舰队视图 + unhealthy 自动重启)。一起设计的理由:三者共用同一份 **fleet.conf 机器清单**作唯一真相源(原则 1)——部署目标、LB upstream、实例注册全由它派生,「傻瓜化」靠所有配置只写一遍,不靠少写配置。

## 进度

- **当前**:Phase C 代码已全部落仓(2026-07-04)——心跳注册表 + `/admin/instances` + 前端独立「舰队实例」tab + autoheal 脚本/systemd units/marker 链路。Mac 侧验过后端语义(心跳读写、判色、ERROR 计数、marker→心跳代报);autoheal 的 systemd 周期触发闭环标真机验收。Phase B 的 Mac 侧此前已完成。
- **下一步**:(a) Phase C 真机验收随发版窗口:`--scale backend=2` 面板见两实例 + `kill -STOP` 模拟 wedge→面板红→autoheal 重启→恢复绿 + 造 ERROR→变黄;(b) Phase B 剩余 = 内网真机 nginx→Caddy 切换(证书就位 → down/up)。之后 Phase D。

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | instance 身份地基(日志/jsonl/响应头/lease/错误事件 五处注入 + jsonl 按实例分目录) | **已完成**(2026-07-04) |
| B | Mac 全功能验证(`--scale` 清单,以 Caddy 为入口)+ 内网 nginx→Caddy 收敛 + HTTPS 静态证书 + 真机 smoke | **Mac 侧已完成**(2026-07-04);剩内网真机切换随发版窗口 |
| C | 舰队可观测 + 自愈(Redis 心跳注册表 + `/admin/instances` + 前端实例面板 + autoheal) | **代码已落仓**(2026-07-04);autoheal systemd 周期闭环标真机验收 |
| D | 多机 fleet(fleet.conf + fleet.sh 单命令发布 + LB 模板生成 + env 单源推送) | 未开始 |

依赖:**B 依赖 A**(instance_id 是「请求真的落在两个副本」的观察手段);**C 依赖 A**(心跳/面板都以 instance_id 为 key);**D 依赖 B**(单机多副本验证过、内网入口已收敛 Caddy,才谈多机);A/C 与 D 内部无耦合、可穿插。1–3 阶段做完,单机多副本即生产可用 + 可观测;D 是多机的全部增量。

## 目标与范围

让多副本(单机 `--scale`)/多机部署在**部署、运维、可观测**三面都友好:发布 = 控制机一条命令;任一请求/对话可定位到受理实例;实例失联/卡死/报错在管理端一眼可见并自动重启;加机器 = 清单加一行。**附带收编两件内网入口的事**(随 Phase B):反代收敛单一 Caddy(nginx 退役,消双份维护)+ 内网入口升 HTTPS(测试中心签发的静态证书)。

**Non-goals(本期明确不做)**:
- **Prometheus / Grafana / OTel 全家桶** —— 气隙离线装 + 两个新组件的运维成本,换来的只是历史曲线;历史回溯靠既有 per-instance `metrics.jsonl` 落盘文件,事后拉取即可。
- **集中日志聚合(Loki/ELK)** —— 2~4 台机 + instance_id 定位 + 管理端实例面板,逐台看日志可承受;机器规模上来再议。
- **Ansible / k8s / Swarm** —— 编排走 shell fleet 脚本(决策 1,含重估触发条件);k8s 沿用 skill plan 结论(沙盒 spawner 重写不划算,仅「本就是 k8s 厂」时考虑)。
- **服务自动发现(consul/traefik)** —— 跨机 upstream 是生成的静态列表(决策 2),规模不值一个发现组件。
- **跨机沙盒调度** —— 沙盒天然在接单副本本机起(DooD),无需调度器;每台 app 机装 runsc/内核/scratch 属 provisioning,归现有离线包脚本(`sandbox/gvisor-pkg`、`kernel-4k-pkg`),不进 fleet.sh 发布循环。
- **release 动态选主** —— fleet.conf 静态指定哪台跑 release(决策 6),不做选举。
- **用户面暴露实例信息** —— 实例归属是运维信息,只进 admin 运行监控,普通用户界面不动。

## 贯穿原则

1. **fleet.conf 是唯一真相源,一切派生。** 机器清单(infra/app/lb/release 四组)只写一遍;部署目标、LB upstream 配置、预检对象全由脚本从它生成。加机器 = 加一行 + 重跑 deploy,不存在「第二份要手工同步的清单」。
2. **可观测用最轻形态:复用已有件 + Redis 易失 key,不引外部监控栈。** 心跳 = 现有 `RuntimeSampler` 快照多写一份到 Redis;面板 = 现有 admin 运行监控页加一块;ERROR 计数 = 一个 logging.Handler。凡「加一个新常驻组件」的方案先问能不能用已有件拼出来。
3. **自愈用宿主原生机制,不引外来镜像。** autoheal = 每台 app 机一个 systemd timer + 十行脚本(`docker ps` unhealthy → restart + 日志),气隙少一个镜像、行为可审计;脚本进 bundle 由 fleet.sh 安装。
4. **env 一致性是正确性,不是便利。** `ARTIFACTFLOW_JWT_SECRET` / `ARTIFACTFLOW_CREDENTIAL_KEY` 多机必须全 fleet 一致(A 机签发 B 机验证/解密),`.env` 由控制机单源推送 + per-host override;与 reconcile 的「config 必须是单一 artifact」是同一条纪律的两个面。
5. **先单机后多机;验证 Mac-first,真机只剩发版顺手 smoke。** 功能轴(反代轮询、SSE、跨副本 cancel/interrupt、reaper —— 沙盒用 runc 即可,lease 逻辑与 runtime 无关)在 Mac 的 compose 里跑和真机等价,全清单在 Mac 跑绿。环境轴已基本被消化:目标机 = 两台麒麟 V10 鲲鹏 arm,沙盒 plan 已真机验完 docker/runsc/DooD/agent 端到端,且 **docker+compose 是自带静态离线包**(`sandbox/docker-pkg/`,版本自己控)—— 不存在「宿主自带老 compose」一类风险。真机剩的只有真证书/真域名,随首次多副本发版顺手确认,不是专门行程。

## 已锁定的决策

1. **编排 = shell fleet 脚本 + inventory,现在不上 Ansible。** 理由:① 气隙网 Ansible 本体 + 依赖要离线装,是一个纯新增的运维负担;② 规模 2~4 台,Ansible 的强项(大机群 provisioning/防 drift)用不上——provisioning 已被自家离线包模式解决(`docker-pkg`/`gvisor-pkg`/`kernel-4k-pkg` 就是这个形态);③ 现有工具链(release.sh / maintenance 全家)就是成熟 shell 形态。**重估触发**:机器 >~5 台、或 per-host 配置 drift 成为真实问题;届时 fleet.conf 直译 Ansible inventory,不浪费。
2. **负载均衡 = 反代统一负责,且收敛单一 Caddy(nginx 退役);跨机无自动发现,静态 upstream 由 fleet.conf 生成。** 单机内已解决(docker DNS 重解析,乙2);跨机 nginx/Caddy 都**不会**自动发现别的机器,正解是「生成的静态列表」——手动配置但不手写。**收敛 Caddy 的理由**(原「纯 DRY 零功能收益、等 prod 验证」的定调被两件事推翻):① 不再并行维护两套反代配置;② 内网要上 HTTPS(测试中心签发静态证书),Caddy `tls cert.pem key.pem` 显式静态证书即可、配了显式证书不会碰 ACME(全局仍 `auto_https off` 防手滑 —— 气隙 ACME 卡死坑只在「裸主机名 + 无 tls」时触发);③ 主动健康检查白拿(`health_uri /health/ready`,wedge 副本主动摘出轮询;nginx 开源只有被动 `max_fails`)。**验证姿势**:原「prod Caddy 真机验证先行」闸由「Mac 上以内网 compose + Caddy 入口 + 测试证书跑全清单」替代(同样满足「不用未验证配置替换气隙在跑的 nginx」的原始顾虑),真机切换放发版窗口。改动点在案:compose nginx 块→caddy、`release.sh` 约 5 处、`resume.sh` 探针(变简单)、内网 Caddyfile 薄壳;换证 = 覆盖 pem + `caddy reload`(进 maintenance 脚本);顺手作废 nginx `Host $host` 丢端口待办。证书侧注意:pem 含完整链、浏览器侧公司根 CA 信任(均非 Caddy 的事)。
3. **instance_id = 进程启动时铸造(hostname + 容器短 id),五处注入。** ① 日志格式(`[request_id|conv_id|message_id]` 三元组升四元组);② 观测 jsonl 记录 + **jsonl/file-log 路径按实例分子目录**(顺手修掉共享卷 rotate 互覆的已知硬伤);③ `X-Instance-ID` 响应头(与 `X-Request-ID` 并排);④ Redis lease 的 owner 字段 →「某对话正在哪台机执行」成为可查询事实;⑤ 错误事件 `data` 创建时冻结(同 `request_id` 的冻结语义)→ 事后从 DB 可答「这次失败发生在哪台机」。
4. **舰队监控 = Redis 心跳注册表 + 管理端实例面板,不建时序库。** 每实例把 `RuntimeSampler` 快照(30s 周期现成)写 `{af:instance:<id>}` hash + TTL ~90s —— 单 key 写 + scan 列举,Cluster-safe 姿态同 `list_active_executions` fan-out(无跨 slot 多 key 操作)。内容:hostname/版本/started_at/loop_lag/RSS/在途 turn 数/**ERROR 日志计数**(logging.Handler 计数器)/**watchdog 最近事件摘要**(时间 + lag)。后端 `GET /admin/instances` 聚合,前端运行监控页加「实例」面板(绿=心跳新鲜无异常;黄=活着但 loop_lag 高 / 近期有 ERROR / watchdog 抓到过事件;红=心跳 TTL 过期)。**wedge 检测 by-construction**:心跳是 asyncio task,loop 卡死 = 心跳停 = key 过期 = 面板变红——不需要任何额外检测机制,「发不出心跳」本身就是信号(与宕机/断网不可区分,但两者都该红,无所谓)。
5. **自愈闭环 = LB 主动摘除 + 宿主 autoheal 重启,deadman 留取证。** 故障实例生命周期:wedge → Caddy 健康检查摘出轮询(秒级)→ 心跳过期面板变红(≤90s)→ systemd timer 脚本 `docker restart`(≤timer 间隔)→ 恢复进轮询。deadman 的 faulthandler 栈 dump 仍走 stderr(docker logs)供事后定因;watchdog/sampler 每进程各跑一份不变(它们监控的就是本进程 loop)。
6. **release 单点 = fleet.conf 静态指定。** 多机发布时由 fleet.sh 在 `[release]` 指定机上跑一次性 `entrypoint.sh release`(乙2 现成机制的多机形态 =「delegate 到一台」),其余机器全部 `AF_SKIP_RELEASE=1`。不做动态选主:部署期人为指定,简单可审计;advisory lock 仍在(误双跑时兜底互斥),但常态就跑一次。
7. **fleet.sh 是生产部署的统一入口:单机/多 worker/多机是同一连续谱,不是三种形态。** 单机 = fleet.conf 只有一行且 host 为本机;多 worker = 该行的 `scale=N` 参数(「多 worker」即容器级 `--scale`,每容器单 uvicorn 进程是既定设计,脚本只有一个旋钮);多机 = 清单多几行。deploy 序列对三者是同一段代码,单机只是退化情形 —— **生产单机每次发版都在排练多机路径**,消掉「平时一套流程、扩容才碰另一套」的 drift。两个边界:① **Mode 1 试用 / dev Mac 不强制**,裸 compose 保持零门槛(入口按 persona 分:试用/开发 = compose,生产运维 = fleet.sh,不论几台);② **本机 host 走 local 特判不经 ssh**(fleet.conf host 写 `local` → 同一组函数本地执行,特判只在传输层,序列逻辑同一份)。

## 阶段

### A — instance 身份地基

**做什么**:让「哪个实例」成为全链路一等公民(决策 3 的五处注入),并修掉 jsonl 多写者互覆。这是 B 的观察手段、C 的 key,先行。

**包含**:instance_id 铸造(启动时一次,挂 config/globals);logger 四元组;jsonl sink 与 file-log 目录按 instance 分子目录(旧目录按 mtime 清理或直接容忍小文件堆积);中间件加 `X-Instance-ID`;`RedisRuntimeStore` lease 带 owner;错误事件 `data` 冻结 instance_id;`/admin/runtime` 响应标注本次应答实例(多副本下读数跳变从此可解释)。

**到时再敲定**:lease owner 放 lease value 内还是旁挂同 slot key;控制台日志(短格式)是否也带 instance 段;InMemory store 下 owner 字段的降级表现。

**验收**:单机 `--scale backend=2` 下,同一对话的日志/事件/响应头三处 instance 一致;两副本 jsonl 各写各目录,rotate 无互覆。

### B — Mac 全功能验证 + 内网 Caddy 收敛 + HTTPS(承接乙2挂账)

**做什么**:价值主体是 ①② 两件真实工作,③ 只是发版顺手确认。① **Mac 上跑 `deploy/MULTI-REPLICA.md` 清单全项**(dev-Mac 此前已实证轮询/门禁/撞名;**跨副本 cancel/interrupt[共享 Redis resolve]、SSE 经真 LB 入口整流程、杀副本 reaper 跨副本回收 三项在任何环境都还没跑过** —— 不是环境问题,是「从未验证」问题):单副本回归、release 恰跑一次、反代真轮询、SSE + 重连 `/resume`、跨副本 cancel/interrupt(Redis profile + `--scale 2`)、reaper(沙盒 runc)—— **入口直接用 Caddy**(内网薄壳 Caddyfile + 测试/自签证书),验证的就是将要上线的配置;② **内网 nginx→Caddy 切换 + HTTPS 静态证书**,随发版窗口执行(改动点见决策 2;infra 变更走 down/up 非 pause/resume);③ **真机顺手 smoke**(非专门行程,随首次多副本发版):release 恰跑一次、双 backend healthy、一条 SSE 经 Caddy 入口走通、真证书生效 —— 目标机麒麟 arm 的 docker/runsc/DooD 已被沙盒 plan 真机验完,docker+compose 为自带静态包版本可控,无宿主环境轴残留。有 A 的 instance_id,「请求落在哪个副本」看响应头/日志即得。

**包含**:验证执行 + 问题修复;内网 Caddyfile 薄壳(全局 `auto_https off` + 显式 `tls`,与 prod Caddyfile 抽共享 route 片段 import,别两份全量);`release.sh`/`resume.sh`/compose 联动改动;`deploy/nginx.conf` 退役;换证步骤进 maintenance 脚本;结果记回 runbook 与本 plan 变更日志。

**到时再敲定**:共享 route 片段的切分粒度;HTTP→HTTPS 跳转与端口保持;证书文件挂载路径与权限。

**验收**:Mac 清单全绿(Caddy 入口);内网真机切换后 smoke 过、HTTPS 生效(证书链完整、浏览器无告警);skill plan 的「真机验收挂账」销账;仓库不再有需维护的 nginx 配置。

### C — 舰队可观测 + 自愈

**做什么**:决策 4 的心跳注册表 + 前端实例面板,决策 5 的 autoheal。做完后「实例是否存活 / 是否有 error / watchdog 是否抓到过异常」在管理端一眼可见,wedge 副本自动重启。

**包含**:sampler 快照写 Redis(TTL);ERROR 计数 Handler + watchdog 最近事件透出(进程内计数/摘要 → 心跳字段);`GET /admin/instances`(scan + pipelined GET);前端运行监控页「实例」面板(红黄绿 + 点开看摘要);活跃对话列表加「实例」列(数据源 = lease owner,A 已备好);autoheal systemd timer 脚本(进 bundle,fleet.sh/手工均可装);**autoheal 行为本身可见**:脚本重启时追加「时间+容器名+原因」到宿主约定文件,该目录只读挂进容器,实例心跳带 `last_autoheal`(最近时间+累计次数)→ 面板显示「曾被 autoheal 重启」——宿主脚本不直连 Redis(免 client/凭证/Cluster 姿态,保十行可审计),经挂载文件中转、本机容器代报;`docker restart` 保留容器身份,面板同一行连续,`started_at` 变新即重启轨迹;Redis 全局资源告警(如 used_memory)保持各副本各报、容忍重复(去重不值机器)。

**到时再敲定**:黄色阈值(loop_lag / ERROR 窗口);实例面板并进现有运行监控页还是独立 tab;心跳字段精确集;autoheal timer 间隔与「维护窗口暂停」(pause.sh)的互斥;autoheal marker 文件路径/格式与保留长度(追加型,按行数或 mtime 截断)。

**验收**:`--scale backend=2` 下面板见两实例;`kill -STOP` 一个副本(模拟 wedge)→ 面板 ≤90s 变红 → autoheal 重启 → 恢复绿;制造一条 ERROR 日志 → 对应实例变黄且计数可见。

### D — 多机 fleet(fleet.conf + fleet.sh)

**做什么**:生产部署统一入口(决策 7:单机/多 worker/多机同一连续谱)。`deploy/fleet.conf`(`[infra]`/`[app]`/`[lb]`/`[release]` 四组,app 行带 `scale=N`;单机 = 一行 + host=`local`)+ `deploy/fleet.sh`(`preflight` / `deploy <bundle>` / `status` / `rollback` 四个子命令),幂等、每步失败即停、可重跑。落地即切换:内网现行发版从手工 scp/ssh 配方改走 fleet.sh(单机形态),多机扩容时只改清单。

**包含**:`deploy` 序列 = 逐台 scp bundle + `docker load`(复用 `verify-bundle.sh` 校验)→ 推送单源 `.env`(+per-host override,决策 4/原则 4)→ `[release]` 机跑一次性 release(成功才继续)→ app 机滚动重启(逐台 up → 等 `/health/ready` 绿 → 下一台,发布期始终有副本在线)→ 从 fleet.conf 生成 Caddy upstream 片段并 reload(单模板,nginx 已于 B 退役,决策 2)→ 经 LB 打 smoke(登录 + 一个 cheap authed endpoint)。`preflight` = 逐台 docker/磁盘/runsc/时钟/端口检查。`rollback` = 各机保留上一镜像 tag,一条命令回退。PG/Redis 外置到 `[infra]` 机(或托管),app 机 compose 不再自带。

**到时再敲定**:fleet.conf 格式(ini 分组 vs sh 变量);控制机与构建机是否同一台;per-host override 的形态;`[infra]` 机 PG/Redis 用 compose 还是裸装;与 `release.sh` 产物 manifest 的对接方式(fleet.sh 读 manifest 知道要 load 哪些 tar);**per-host arch 标注**(目标机现为麒麟 arm,若过渡期混入 x86 机,fleet.conf 按 host 标 arch、fleet.sh 选对应架构 tar —— release/sandbox 产物已按 `-amd64`/`-arm64` 后缀双份并存)。

**验收**:两台真机从「装好 docker + runsc 的空机」到可服务 = `preflight` + `deploy` 两条命令;发布期间持续请求不中断(滚动);杀掉一台的 backend,服务经 LB 继续可用、面板显示该实例红;`rollback` 一条命令回上一版本;全程 release 恰跑一次;**单机形态回归**:一行 fleet.conf(host=`local`)走完同一 deploy 序列,替代手工发版配方。

## 变更日志

- **2026-07-04 Phase C 代码落仓(可观测 + 自愈)**。五个提交:① ERROR 计数 `logging.Handler`(挂 `ArtifactFlow` logger 非 root,避掺第三方 ERROR 噪声)+ watchdog `_record_wedge` 额外留 `{ts,lag_ms,wedged}` 轻摘要供心跳读(栈明细仍只落 loop-lag.jsonl);② 心跳注册表 `HeartbeatWriter`——不新增常驻循环,`RuntimeSampler` 每 tick 把快照子集多写一份到 `{prefix:instance:<id>}`(单 `SET+EX`、hash-tag 每实例独占 slot,读侧 scan+pipelined GET fan-out 三形态通用;redis=None 单机 no-op);③ `GET /admin/instances`(scan+pipeline 镜像 `list_active_executions`,读侧判色);④ 前端独立「舰队实例」admin tab(10s 轮询、红黄绿卡片 + wedge/autoheal 徽章 + 展开详情);⑤ autoheal.sh + systemd `.service`/`.timer` + marker 挂载(backend `:ro` `/app/autoheal`)。**判色定案(到时再敲定项)**:双时间轴——key TTL(默认 300s)放长于 STALE(默认 60s),颜色由 payload 内 `ts` 新鲜度**读侧**算,不落 Redis(阈值可调不需回填);绿=新鲜无异常,黄=loop_lag 近分钟峰值超阈/窗口内出过 ERROR/watchdog 抓到过 wedge/近期被 autoheal,红=`ts` 陈旧但 key 未过期(wedge 在册可见,留自愈窗口)。**对 plan「TTL ~90s」草图的构造性修正**:90s TTL 下 wedge 实例 key 直接过期蒸发、面板根本没机会显红——与 Phase B 那三个 LB bug 同性质(不真想清楚就埋)。**其余到时再敲定项定案**:面板并入 vs 独立 tab → 用户选**独立 tab**;心跳字段集 = sampler 快照子集 + version/started_at/error_count/last_error_ts/last_wedge/last_autoheal;ERROR 窗口 5min 单值(不做滑窗 deque);autoheal 与 pause 互斥 = 查 `MAINTENANCE_ON` 旗标在即 no-op;marker = `deploy/autoheal/restart-marker.jsonl` 追加型 JSONL、按行数 500 截断,归因经文件中转 backend 代报(脚本不碰 Redis)。**Mac 验**:后端语义单测级验过(心跳读写/判色八态/ERROR 计数只认 ERROR/marker 追加+截断/backend 按 instance_id 过滤读取);前端 tsc+lint 绿;两份 compose config 校验通过。**遗留真机验收**(随发版窗口,无 systemd 的 Mac 测不了):`--scale backend=2` 面板见两实例 + `kill -STOP` 模拟 wedge→面板红→systemd timer 自动重启→恢复绿 + 造 ERROR→变黄。**归因前提**:backend `instance_id`=容器 hostname(docker 默认);若显式设 `ARTIFACTFLOW_INSTANCE_ID` 覆盖则 marker 归因需调整(默认部署不设)。

- **2026-07-04 Phase B Mac 侧完成(用户定调:跳过专门真机行程,Mac 能测的测完 + nginx→Caddy)**。**配置形态**:`deploy/caddy/` 整目录挂载(`common.caddy` 共享站点主体 + 薄壳 `Caddyfile`/`Caddyfile.intranet`),intranet compose nginx→caddy(`AF_HTTP_PORT`/`AF_HTTPS_PORT`,HTTP 只做 308 跳转且带公开端口),`deploy/certs/` 放静态证书(gitignore + release tar 排除),`nginx.conf` 删除;pause/resume 探针统一「exec caddy 打 :2021」下沉 `_maint_lib.sh`;release.sh infra 镜像 `caddy:2.10-alpine`(slug `caddy2.10-pg16-redis7`)。**验证结果**(backend×2 + Redis + PG + 自签证书,真实 LLM turn):release 恰跑一次 / HTTPS 健康 / 维护页 gate / Swagger 404 / SSE 整流程含断线重连(断在 22 chunk 重连续到 complete)/ 跨副本 cancel(B 副本受理、DB 落 cancelled、lease+owner 同释放)/ 逐实例日志目录(`--scale 2` 三处一致性,A 阶段验收销账)/ wedge 摘除(pause 副本后 20/20 零失败)/ 单副本回归。**Mac 实测抓到并修掉三个 LB 真 bug**(全是不真跑发现不了的):① `reverse_proxy backend:8000` 经 keepalive 连接池钉死单副本(12/12 同实例)→ `dynamic a` 动态 upstream + round_robin;② 单文件 bind-mount pin inode,主机编辑/tar 覆盖后容器内 reload 断(实测复现)→ 整目录挂载 by-construction 修掉;③ wedge 副本摘不掉——主动健康检查(health_uri)不作用于 dynamic upstream(决策 2 的「主动健康检查白拿」在单机 dynamic 形态下**不成立**,多机 fleet 静态 upstream 时才成立),且 pause/wedge 下 TCP 握手由内核完成、dial_timeout 不触发 → 被动摘除:分路径 `response_header_timeout`(health 3s / SSE 15s / API 60s——上传转换在 POST 请求内,必须留宽)+ `fail_duration 30s` 失败记忆(upstream 健康按 dial 地址全局共享)+ caddy 容器 healthcheck 打 :2021 兼作穿过 dynamic upstream 的探针。**跳过**:reaper 跨副本回收(需沙盒镜像,留真机窗口,MULTI-REPLICA.md 已标注)。**遗留到发版窗口**:内网真机 down/up 切换(注意旧 nginx 容器是 orphan,`up` 不会自动移除——Mac 上真实撞到,3 周前的旧 nginx 容器一直占着 80)+ 真证书链验证。

- **2026-07-04 Phase A 完成**。instance_id = `utils/instance.py` 启动铸造(容器 hostname=容器短 id,`ARTIFACTFLOW_INSTANCE_ID` 可覆盖,字符集收窄防路径/头注入)。五处注入全落:① 日志四元组 `[instance_id|request_id|conv_id|message_id]` + file-log/obs-jsonl 都按 `<dir>/<instance_id>/` 分子目录(rotate 互覆 by-construction 消除;控制台短格式不带 instance——docker logs 天然按容器分流);② sampler/watchdog 记录内也带 `instance_id` 字段(文件拷走聚合后目录信息即丢);③ 中间件 `X-Instance-ID` 响应头(正常 + 兜底 500,CORS expose);④ lease owner 取「旁挂同 slot key」方案(`{prefix:conv_id}:owner`,acquire/release Lua 顺带写删、renew 续 TTL——lease value=msg_id 是所有 compare 脚本的既定契约,不改复合值;InMemory 降级=有 lease 即返回本进程 id);⑤ ERROR 事件 `data` 创建时冻结 `instance_id`(engine `_emit` + `decide_terminal` + 两处 transport 直发,共四个构建点;sanitize 只重写 `error` 字段,定位码保留)。`/admin/runtime` 响应标注应答实例。验收:单测全绿(1693 passed;Redis 集成 22 项含 owner 生命周期,一次性容器跑通);`--scale 2` 三处一致性留待 Phase B 清单一并验(观察手段已备好)。
- **2026-07-03 autoheal 可见性补入(用户提问驱动)**:Phase C 补「autoheal 行为本身可见」——结果可见白拿(`docker restart` 保容器身份,面板同行连续 + `started_at` 变新);归因可见走「宿主脚本追加 marker 文件 → 只读挂载 → 实例心跳代报 `last_autoheal`」,宿主脚本不直连 Redis(保十行可审计);进程内证据随重启消失,宿主 marker + deadman stderr 是幸存取证链。
- **2026-07-03 决策 7 补入(用户确认)**:fleet.sh 定为生产部署统一入口 —— 单机/多 worker/多机是同一连续谱(一行清单 + scale 参数 → 多行),同一段 deploy 序列,单机是退化情形;价值 = 生产单机日常发版持续排练多机路径,消「两套流程 drift」。边界:Mode 1 试用/dev 保持裸 compose 不强制;本机 host `local` 特判不经 ssh(特判只在传输层)。D 阶段验收补「单机形态回归」项,落地即替代手工 scp/ssh 发版配方。
- **2026-07-03 环境轴修正(用户指正)**:删掉「CentOS 7 老 compose 认不认 `service_completed_successfully` 门禁」这个风险项 —— 它来自旧记忆(bsyshealthyapc),实际目标机将换成两台麒麟 V10 鲲鹏 arm,且 docker+compose 本就是自带静态离线包(`sandbox/docker-pkg/`,版本自己控),沙盒 plan 已在这两台真机验完 docker/runsc/DooD/agent 端到端 → 该风险 by-construction 不存在。连带:Phase B 的真机 smoke 从「专门验证」降为「随首次多副本发版顺手确认」,B 的价值主体重申为 Caddy+HTTPS 切换 + 三项从未跑过的跨副本功能(cancel/interrupt、SSE 经真 LB、reaper 跨副本);决策 1 去掉 CentOS 7 Python 论据(离线装负担 + 规模 + 自家离线包模式已覆盖 provisioning 三条仍立);D 补 per-host arch 标注(产物已双架构后缀并存)。
- **2026-07-03 B 阶段改形 + Caddy 收敛并入**(用户三问驱动):① 验证 Mac-first —— 功能轴在 Mac compose 与真机等价(dev-Mac 已实证过一部分),真机降为发版窗口 smoke,唯一 Mac 测不出的是环境轴(CentOS 7 老 compose 认不认 `service_completed_successfully` 门禁 = 首要确认项);② 内网 nginx→Caddy 收敛从「deferred 等 prod 真机验证」改为**并入 Phase B**——推翻旧定调的两个新事实:用户不愿并行维护两套反代 + 内网要上 HTTPS(测试中心静态证书),Caddy 功能收益不再为零(静态 tls + 主动健康检查),验证闸由「Mac 以 Caddy 入口跑全清单」替代;③ D 的 LB 模板从 nginx/Caddy 双份改单 Caddy。
- **2026-07-03 起草**。定调:编排 shell fleet 脚本非 Ansible(环境性理由 + 重估触发)、LB 生成式静态 upstream(Caddy 主动健康检查优先、挂 prod 验证闸)、可观测走 Redis 心跳 + 现有 admin 页(不上 Prometheus)、自愈走宿主 systemd timer(不引 autoheal 镜像)、release 单点静态指定。现状基线来自 2026-07-02 的多副本条件评估(执行正确性已齐、缺口在运维/可观测)。
