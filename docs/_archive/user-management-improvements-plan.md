# 用户管理优化 PR 计划

> 起因：内网部署上线后 admin 反馈用户管理功能不够用 + 讨论中发现一个删除路径的隐性 bug。
> 讨论日期：2026-05-06 ~ 2026-05-07
> 范围：用户增删改、批量操作、密码自助、部门体系、删除路径数据完整性、面板布局重构

---

## 背景：原始反馈

用户提出的 5 条改进诉求：

1. **管理员没有用户删除** — 现状只有"禁用 (`is_active=False`)"软删，缺硬删入口
2. **普通用户应能改自己密码** — 现状 `PUT /auth/users/{id}` 需 admin 权限，缺自助路径
3. **批量创建用户** — 内网批量铺人 200~300 个，单条点击不现实
4. **创建用户的输入校验** — 现在 `username` 只查重不校验格式，可能因空格/特殊字符出脏数据
5. **未来知识库按部门隔离** — 用户系统现在和 agent/工具完全解耦，加字段也用不上，需要先想清楚联动方式

讨论中额外发现：

6. **`DELETE /api/v1/chat/{conv_id}` 不检查 active 执行** — 引擎在跑时会话行被删，post-processing 写库阶段命中 FK 违规，部分数据写一半失败被静默吞掉

---

## 决策记录

下列设计选择已和用户对齐，PR 实施时不再重新讨论：

### 关于删除（合并第 1 条 + 第 6 条）

- **保留软删（"禁用"）作为日常路径**，新增硬删作为显式管理员操作
- **FK 改为 CASCADE**：硬删用户时连同其全部会话一起删；内网工具不保留孤儿会话
- **删除路径不抢 lease**，改由引擎在 post-processing 入口自检：会话已不存在则跳过持久化，并辅以 IntegrityError catch 兜 TOCTOU。理由：引擎所有 DB 写都在 turn 结束的 edge phase（CLAUDE.md 已钉死），冲突窗口本就只有几十毫秒，让 admin 等 lease 不划算
- **二次确认用 checkbox 模式**而非 type-username（type-username 在批量场景下不可用，而我们要单条/批量统一）
- **"最后一个 admin" 保护**：admin 不能删自己；admin 不能 demote 或删除导致系统 admin 数 = 0。后端在 `DELETE /auth/users/{id}` 和 `PATCH role='user'` 双路径强校验

### 关于自助改密（第 2 条）

- 新增 `POST /auth/me/password`，body `{current_password, new_password}`，校验 current_password
- 批量创建未指定密码时 **默认密码 = username**（admin 口头通知用户登录后改密；不做强制改密 flag）

### 关于批量导入（第 3 条）

- **走 CSV 而非 xlsx** — stdlib `csv` 零依赖；admin 在 Excel 编辑后"另存为 CSV"即可
- **best-effort，非原子** — 返回 `{created, failed: [{row, username, reason}], skipped}`
- **冲突策略**：`username` 已存在 → skip + 报告，不做 overwrite（避免误改已激活用户密码）
- **文件内重复**：预扫描查重，整体拒绝并返回重复行号

### 关于校验（第 4 条）

- **username 严格**：正则 `^[A-Za-z0-9._-]{2,64}$`，禁止空格和中文。理由：username 进 URL/日志/JOIN/SSE channel，非 ASCII 出诡异 bug 概率高。中文留给 `display_name`
- **空格策略**：拒绝（返回 400），不 silent strip — silent strip 会造成 "name" / "name " 字面相同实际不同的 collision 误判
- **password 完全放开**：保留 schema 现有 `min_length=4` 防极端，不加复杂度规则、不加 "≠ username" 规则。内网工具，admin 自己说了算
- **`Department` 表加 `UNIQUE(parent_id, name)`** — 堵住手抖空格创建重复部门

### 关于部门体系（第 5 条）

- **采用部门表（邻接表）** 而非 User 表平铺 `org_l1/l2` 字符串列
  - 邻接表 = `Department(id, parent_id, name)` + `User.department_id` FK
  - 部门改名/搬家/重组 cost = O(1)，不动用户行
  - 深度天然可变，叶子部门下系统账户挂 2 级或 3 级都行
- **本轮一并做部门管理 UI**（更新决策）— 原本计划留到"真发生时再做"，但和用户讨论后决定一锤子做完。理由：批量导入会灌入大量真实数据，admin 立刻就需要改名/搬家/删除等维护能力，否则数据脏了没救
- **管理操作集**：新建 / 改名 / 搬家 / 删除（空部门）；不做合并端点（用 PR5a 的批量改部门 + 删空部门等价实现）
- **关键约束**：环检测（不能搬到自己子孙下）；`UNIQUE(parent_id, name)` 冲突 → 409；非空拒删
- **去重函数 `resolve_department_path`** 复用三处：单条创建、bulk import、显式 create
- **新增 `User.is_system` 布尔列** 区分服务账号 — KB / 统计 / 告警逻辑能据此分流

### 关于 KB 联动（第 5 条续）

- **本轮只做"采集字段"，不做 plumbing**
- 现状：`user_id` 在 `chat.py:122` 传给 `runner.submit`，但**没进 engine 的 `state`，也没进工具**。CLAUDE.md 里那句"core/engine/tools receive `user_id`"今天还没实装
- 真正的桥接（user → tool）应该在 controller turn 启动时 resolve `principal = {user_id, username, dept_l1, dept_l2}` 并放进 `state["principal"]`，工具通过现有 tool-context 读取，**不查 UserRepository** — 维持 CLAUDE.md 的三层架构边界
- 该 plumbing 等 KB 立项时再做。本轮先把组织数据攒上（数据是最慢的环节）

### 关于批量管理 UX

- **进入"选择模式"** 而非常驻 checkbox。理由：99% 是单条交互，常驻 checkbox 是噪音
- 顶部按钮"批量管理"切模式，进入后中间每行显示 checkbox；Esc / 退出按钮 / 切面板 都自动清空选中
- **"全选"渐进披露** Gmail 模式：先全选当前页，提示条出现 `"已选中 20 条，点击此处选中全部 347 条匹配项"`
- 后端不让前端循环调单条 — 提供 bulk 端点，best-effort，逐条独立成功/失败
- **批量动作集中在右面板**（master-detail 形态下）：
  - 进入选择模式时右面板**自动切换**为"批量动作面板"（显示选中数 + 4 个动作按钮）
  - 中间面板**不显示底部工具栏**，所有动作都在右面板触发
  - 退出选择模式时右面板回到 `empty`，选中清空
- **会话面板 (PR5b) 不走右面板** — `ConversationBrowser` / `AdminConversationList` 在 sidebar/中间面板，不进入用户管理模式，因此用传统底部工具栏即可

### 关于面板布局（master-detail 右面板）

发现 `userManagementVisible && isAdmin` 时右面板（`ArtifactPanel` slot）实际闲置 — admin 在管用户根本不看 artifact。决定改造：

- **进入用户管理模式时，右面板接管为"详情/表单面板"**（master-detail 模式）
  - 中间**最终形态**：纯列表/树（搜索 + 列表 + 选择模式 + 顶部"+ 新建用户" / "+ 管理部门"按钮），**无 inline 表单**
  - 右：根据中间的选中/动作显示表单（新建用户 / 编辑用户 / 新建部门 / 编辑部门）
  - **过渡说明**：PR0 阶段保留现有 inline 表单作 fallback；PR2b 落地时清理（详见 PR2b "显式删除清单"）
- **此前 inline 的所有交互都迁移到右面板**：
  - `UserRow` 的 inline 改名 / 重置密码 → 右面板"用户详情"页
  - `UserManagementPanel` 顶部的"+ 新建用户"展开表单 → 右面板"新建用户"表单
  - 部门管理（PR4）的树编辑 → 右面板"部门详情"页
- **不影响 `ArtifactPanel`**：退出用户管理模式时右面板自动恢复为 `ArtifactPanel`
- **移动端复用现有 overlay**：`ThreeColumnLayout.tsx:133-142` 已实现 `< md` 下的 overlay 模式，新右面板内容直接复用，零成本
- **影响所有 PR**：基础设施在 **PR0** 落地；PR1（改密弹窗例外，不在用户管理面板内）、PR2b、PR3、PR4、PR5a 的前端形态都按 master-detail 设计

### 关于 disable 用户的执行隔离（澄清）

`get_current_user` (`api/dependencies.py:300-324`) 每次请求都查 DB 校验 `is_active`。所以：

- 用户被禁用后 **无法再发起任何 API 请求**（POST /chat、GET /stream 全部 401）
- **正在跑的 engine 不受影响**（已在 `submit` 闭包里跑，不再走 API 校验）— 但用户也看不见，只是后台浪费一点 LLM token，turn 结束正常持久化（user 还在表里）
- **禁用 ≠ 中断**。如果未来确实需要"禁用立即停手"，那是另一个 cooperative cancellation 功能，不在本轮范围

---

## PR 计划

### 依赖关系图

```
PR0  (布局重构：右面板 mode-aware)        —— 基础设施，独立
PR1  (自助改密 + username 校验)            —— 独立（弹窗在 UserMenu，不依赖 PR0）
PR2a (修 active-execution 删除 bug)        —— 独立，纯 bug fix
PR2b (硬删除用户 + 用户编辑表单)            —— 依赖 PR0 + PR2a（删除按钮在右面板用户详情里）
PR4  (Department 表 + 级联选择器 + 管理 UI) —— 依赖 PR0 + PR2b（扩展 UserDetailForm）
PR3  (CSV 批量导入)                        —— 依赖 PR0 + PR1 + PR4（首次导入需联动部门）
PR5a (用户面板批量操作)                    —— 依赖 PR0 + PR2a + PR4
PR5b (会话面板批量删除)                    —— 依赖 PR2a
```

推荐落地顺序：**PR0 → PR1 → PR2a → PR2b → PR4 → PR3 → PR5b → PR5a**。

- **PR0 必须先做** — 它是 layout-only 重构，不引入新功能，但给后续 4 个 PR（PR2b/3/4/5a）提供干净的 master-detail 支点。否则每个 PR 都要重复改一遍接入逻辑
- **PR2a 优先于其他功能 PR** — 它是修当前线上隐患，不该和功能 PR 排队
- **PR1 与 PR0 解耦** — 改密入口在 sidebar 的 UserMenu 里，不在用户管理面板内，因此不依赖 PR0；可与 PR0 并行
- **PR4 必须在 PR3 之前** — 否则首次批量导入的 200~300 用户，CSV 里的 dept_l1/l2/l3 列会被忽略，admin 必须等 PR4 落地后再用 PR5a 批量补部门，体验极差。让 PR4 先就绪，PR3 第一次导入就能联动建表

### 实施进度

> 截至 2026-05-08（PR5 全部落地，整个计划闭环）

| PR | 状态 | 备注 |
|---|---|---|
| PR0 | ✅ 已落地（main） | 4 笔 commit：`df41032` 主体 + `6be85dd` / `983c14e` / `18405db` 三笔 reviewer follow-up |
| PR1 | ✅ 已落地（main） | 3 笔 commit：`3091f60` 主体（自助改密 + username 校验） + `39c81c7` reviewer follow-up（P1 pwd_v 失效机制 + P3 友好错误透传） + `d1f3caa` alembic baseline 修正（`password_version` 直改 0001 而非追加 0002，下详） |
| PR2a | ✅ 已落地（main） | 1 笔 commit：`e829ad6` —— controller post-processing 入口加 `exists()` 早返回（Layer 1）+ flush_all / `_persist_events` 加 `IntegrityError` 兜底（Layer 2）；`_persist_events` 改为透传 `IntegrityError` 让 caller 区分"基础设施失败"和"被外部删除"。Lease 释放仍由 `ExecutionRunner._wrapped finally` 兜底，post-processing 不需手动管理。 |
| PR2b | ✅ 已落地（main） | 2 笔 commit：`3612d3a` 主体 + `8d73e0a` reviewer follow-up（P2 单 submit 路径用 `form="..."` 关联 + P3 `DangerConfirmModal` 内 `try/catch` inline 错误显示）。**两处与原 plan 偏离**：(a) self-protection 取代 last-admin guard —— "admin 不能删自己 / 不能改自己 role / 不能改自己 is_active"，配合 disabled admin 进不来后台已足够保住至少 1 活跃 admin，无需 `count_admins()` 查询；(b) `UserRepository.hard_delete` 用 Core 级 `delete()` 语句而非 ORM `session.delete()` —— 后者在配合 `lazy='selectin'` 加载的子集合时会主动 emit `UPDATE conversations SET user_id=NULL` 绕过 DB CASCADE，即使设了 `passive_deletes=True` 也救不了（`User.conversations` 仍加上 `passive_deletes=True` 作正确的关系语义）。 |
| PR4 | ✅ 已落地（main） | 4 笔 commit：`7dcc1a9` 主体（Department 邻接表 + 8 个 admin 端点 + `resolve_department_path` / `expand_subtree` helpers + 用户搜索按部门子树扩展 + 右面板 master-detail 部门管理 UI） + `c0997b3` reviewer follow-up A（P1 路由层 pre-check 加 IntegrityError 兜底 + partial unique index 关 TOCTOU、P2 `list_users` 响应加回 `department_id`、P3 cascader 不再因叶子节点提前退出） + `e52520a` reviewer follow-up B（partial unique index 在 MySQL 方言下被编译成全表 `UNIQUE(name)` 反伤"不同父下同名子部门"的合法插入；改用 STORED 生成列 `root_name_key` 跨 SQLite 3.31+ / PG 12+ / MySQL 5.7+ 一致兜根级去重） + `ba17cdd` 前端打磨（form input `bg-surface` 与 panel 区分 + UserRow 顺序调整 + 部门名前 `·` 去掉）。**与原 plan 三处偏离**：(a) **`is_system` 字段不加** —— 用部门归属（顶级"系统账号"挂相应子部门）天然表达服务账号语义，布尔字段冗余；(b) **取消 [用户/部门] tab**，改用 `UserManagementPanel` 顶部按钮 `[+ 新建用户] [管理部门]` + 右面板 `dept-manager` 复合视图（内部私有 state 切 tree / edit / create 三态），`uiStore.userManagementRightView` 因此只新增 `dept-manager` 一个 type 而非 plan 原来的 `create-dept` / `edit-dept`；(c) **根级唯一性不用 partial unique index** —— `sqlite_where` / `postgresql_where` 在 MySQL 方言下被忽略，会编译成错误的全表 UNIQUE；改用 STORED 生成列 + 普通 UNIQUE 跨方言一致。 |
| PR3 | ✅ 已落地（main） | 3 笔 commit：`5be2314` 主体（CSV 解析 util + `POST /auth/users/bulk-import` + `BulkImportForm` 三段式 UI + `UserManagementPanel` 顶栏按钮组扩展）+ `eeaab40` reviewer follow-up A（P1 同步 bcrypt 阻塞 event loop → 拆 3 段 + `asyncio.gather + to_thread` 并行 hash；P2 字段长度旁路 → 加 `display_name / dept_l* / 显式 password ≤ 128` 校验；P3 native file input 不重置）+ `fd79300` reviewer follow-up B（P3 修补：上一轮只覆盖"换一个文件 / 再导入一批"路径，submit catch 分支没清 file state + DOM value，"修源 CSV 重传"主流程仍 stuck；统一在 catch 顶部 `setFile(null) + clearNativeInput()` + 顺手把"换一个文件"按钮样式与"下载 CSV 模板"对齐为 accent 色）。**与原 plan 五处偏离**：(a) 用 `charset-normalizer`（项目已有依赖，复用 `doc_converter.py` 同款）替换 plan 里的 `chardet`，零新增依赖；(b) 加 `UserRepository.find_existing_usernames(set) -> set` 单次 IN 查询替代逐行 `get_by_username`，避免 1000 行 N+1；(c) 同 CSV 内重复部门路径在内存 cache（`dict[tuple[str, ...], Optional[str]]`）只 resolve 一次，200~300 行常聚集少数部门时省掉绝大多数 SELECT；(d) 部门路径 gap（`dept_l1='', dept_l3='X'`）在 importer 自己拒，不动 `resolve_department_path` 的折叠语义（cascader 路径仍能用）；(e) 行数 cap 之外加字节硬 cap `MAX_BULK_IMPORT_BYTES = 5MB`，先于解析检查兜恶意大文件 OOM。 |
| PR5a | ✅ 已落地（main） | PR5a + PR5b 同笔 commit `65519a6` 主体（用户管理批量动作 + 会话批量删除 + 共享 `Checkbox` 组件 + uiStore selection 状态 + `BulkActionPanel` 右面板 + `BulkDeleteResponse` / `BulkActionResponse` schemas + 34 个集成测试）+ `bcacb09` reviewer follow-up（P1 narrow IntegrityError catch + rollback；P2 docstring 紧字段表明为何不需要对称 catch）。**与原 plan 三处偏离**：(a) **last-admin guard 直接落 self-protection** —— 与 PR2b 的演进一致，bulk-action 单条 self-id 走 `forbidden_self`，不引入 `count_admins()`；(b) **`failed.reason` 词汇精简** —— 不出 `last_admin` / `executing`，只 `forbidden_self` / `not_found` / `internal_error`，与 PR2b / PR2a fail-soft 模型对齐；(c) **"全选当前页"先做、跨页全选延后** —— 跨页选择需要拉一次全量 IDs，UX 风险大于本轮收益，待后续按反馈推进。 |
| PR5b | ✅ 已落地（main） | 同上 `65519a6` + `bcacb09`。**与原 plan 两处偏离**：(a) **不新增 admin bulk-delete 端点**（计划原写 `POST /admin/conversations/bulk-delete`）—— 用户对齐："admin 管 user 不管 user 数据，要清就删用户走 FK CASCADE"；`AdminConversationList.tsx` 维持只读 observability。已存为 memory `feedback-admin-scope-user-mgmt.md`，未来类似设计自动遵循；(b) **不复用 `BulkDeleteConfirm` 新组件**，直接用 PR2b 的 `DangerConfirmModal`（消息支持多行 + 已带 acknowledge checkbox + 已带 inline error），避免重复造轮。 |

**Alembic 策略（PR1 确立、PR4 期间用户确认长期化）**：本轮采用"drop-and-redeploy"姿态 —— 任何 schema 变更直接改进 `0001_initial_schema.py`，不追加 `0002_*.py` / `0003_*.py`。原本计划"PR3 灌入真实用户后切回追加模式"，但落地讨论中确认：在内网真有保留数据需求出现之前，全部直改 0001 都是可接受姿势（删 db + `alembic upgrade head` 即重建）。**真正的切换信号**：当生产 / 内网某次升级开始有"不能丢的数据"，下一笔 schema 变更才开 `0002_*.py` 走追加。当前为止已直改 0001 的列：`password_version`（PR1）、`Conversation.user_id` ondelete=CASCADE（PR2b）、`departments` 表 + `User.department_id` + `root_name_key` 生成列 + `uq_dept_root_name` UNIQUE（PR4）。PR3 不引入 schema 变更（纯 router + util + 前端），未触发切换决策。

---

### PR0：布局重构 — 右面板 mode-aware

**状态**：✅ 已落地于 `main`（2026-05-07）。commits：
- `df41032` 主体（uiStore RightView 状态、`UserManagementDetailPanel` 骨架、`ThreeColumnLayout.forceArtifactVisible`、page.tsx 路由、`UserManagementPanel` 行点击 hit zone）
- `6be85dd` reviewer follow-up：移动端 force-show 闸门 + RightView 在切换路径上 reset
- `983c14e` reviewer follow-up：`forceArtifactVisible` 升级三态 override（解决 `artifactPanelVisible` 被遗留状态绕过的问题）
- `18405db` reviewer follow-up：用户管理模式下隐藏 sidebar 的 artifact toggle 按钮

**已知遗留**：
- 行点击与 inline 改名按钮的双重交互（PR2b 清理 inline UI 时一并消除）
- 移动端 admin 在用户管理模式下看不到详情面板（**已声明为桌面端专用**，详情面板要等 PR2b 真表单 + 后续 mobile master-detail 设计才有意义）


**目标**：把 `ThreeColumnLayout` 的右面板（`ArtifactPanel` slot）改造成内容感知 — 进入用户管理模式时切换为 `UserManagementDetailPanel`，退出时恢复 `ArtifactPanel`。**纯 layout-only 重构，不引入新功能。**

**现状**：

- `ThreeColumnLayout.tsx` 右面板 slot 固定接收 `<ArtifactPanel />` props（`page.tsx:24-28`）
- 右面板可见性由 `uiStore.artifactPanelVisible` 控制
- 中间面板内容由 `ChatPanel.tsx:55-65` 根据多个 UI flag（`observabilityVisible`/`userManagementVisible`/`conversationBrowserVisible`）切换
- 用户管理模式下右面板要么显示陈旧 artifact、要么不可见 — 没人为它服务

**改造：**

1. **`uiStore` 新增 detail 状态**（用户管理模式下控制右面板内容）：

   ```ts
   type UserMgmtRightView =
     | { type: 'empty' }
     | { type: 'create-user' }
     | { type: 'edit-user'; userId: string }
     | { type: 'create-dept'; parentId: string | null }
     | { type: 'edit-dept'; deptId: string }
     | { type: 'bulk-action' }                  // PR5a 用，selection 在独立字段

   userManagementRightView: UserMgmtRightView
   ```

   **`selection` 不放进 RightView payload** — 选择模式是中间面板的状态，与右面板是协调关系而非包含。PR5a 引入独立顶层字段 `userManagementSelection: string[]` 和 `selectionMode: boolean`，由中间面板维护，右面板订阅显示。

2. **新组件 `UserManagementDetailPanel`**（`frontend/src/components/chat/UserManagementDetailPanel.tsx`）：
   - 读 `userManagementRightView`，根据 type 分发渲染对应表单
   - PR0 阶段只渲染 `empty` 占位（"选择一个用户查看详情，或点击 + 新建"）；具体表单各 PR 各自补
   - 其他 type 的表单组件由 PR2b/3/4/5a 各自实现，PR0 不写

3. **`page.tsx` 改造右面板内容**：

   ```tsx
   const userManagementVisible = useUIStore((s) => s.userManagementVisible);
   const isAdmin = useAuthStore((s) => s.user?.role === 'admin');

   const rightContent = userManagementVisible && isAdmin
     ? <UserManagementDetailPanel />
     : <ArtifactPanel />;

   return <ThreeColumnLayout ... artifact={rightContent} />;
   ```

4. **`ThreeColumnLayout` 右面板可见性协调**：

   - 当前是 `artifactPanelVisible && artifact`
   - 改为 `(artifactPanelVisible || (userManagementVisible && isAdmin)) && artifact`
   - 进入用户管理模式自动显示右面板；退出后恢复用户原本 `artifactPanelVisible` 设置（不主动改变 store 值）

5. **`UserManagementPanel` 重构（中间面板）— fallback 策略，避免功能断档**：

   PR0 **不删除任何现有 inline 交互**，只新增右面板基础设施和"打开右面板"的入口：
   - 保留 inline "新建用户" 表单（`UserManagementPanel.tsx:184-240`）原样可用
   - 保留 `UserRow` 的 inline 改名 / 重置密码（line 280+）原样可用
   - **新增** 整行可点击 → `setUserManagementRightView({type: 'edit-user', userId})`，与现有 inline 改名共存（点 row 主体打开右面板，点 inline 改名按钮维持原行为）。**实施约束**：行点击的 hit zone 必须避开 `UserRow` 内已有的 inline 触发区（displayName 区域、重置密码 / 禁用 / 启用按钮组）— 推荐让行左侧空白 / id / created_at 区域作为打开右面板的入口，displayName 保留 inline 改名 click handler
   - **不加** "+ 管理部门" 按钮 — PR4 落地时再加并接入逻辑（避免 PR0 阶段显示一个不能点的按钮）

   **删除时机**：所有 inline UI 的清理由 **PR2b** 一次性完成 — 详细的 state / 函数 / JSX 块清单见 PR2b 节"显式删除清单（PR0 fallback 清理）"。这样 PR0 → PR1 → PR2a 三个 PR 的发布窗口期间，用户管理体验**完全不降级**。

**测试：**

- 单元：`page.tsx` 在不同 UI flag 组合下渲染正确的右面板内容
- e2e：进入用户管理模式右面板自动显示空状态；退出后恢复 artifact panel 可见性
- e2e：行点击打开右面板编辑视图；inline 改名按钮仍工作（fallback 共存）

**影响文件：**

- `frontend/src/stores/uiStore.ts` (新增 `userManagementRightView` 状态 + setter)
- `frontend/src/components/layout/ThreeColumnLayout.tsx` (右面板可见性条件改造)
- `frontend/src/app/page.tsx` (右面板内容动态选择)
- `frontend/src/components/chat/UserManagementDetailPanel.tsx` (新文件，空骨架)
- `frontend/src/components/chat/UserManagementPanel.tsx` (新增行点击 + "+ 管理部门" 占位；保留所有现有 inline 交互)

**已知限制（不在本 PR 修）：**

- 行点击与 inline 改名按钮的双重交互在 PR0 阶段是临时形态，UX 略有重复（同一个 row 上既能点出右面板又能点出 inline 改名）— PR2b 落地时清理

---

### PR1：自助改密 + username 校验

**目标**：补齐用户自助改密入口；收紧 username 校验。

**后端：**

- 新增 `POST /api/v1/auth/me/password`
  - body：`{current_password: str, new_password: str}`
  - 校验 `current_password`，hash 写入 `User.hashed_password`
  - 不需要 admin 权限，仅 `Depends(get_current_user)`

- 新增 `validate_username(name: str) -> None` (utility)
  - 正则 `^[A-Za-z0-9._-]{2,64}$`
  - 失败抛 400 带具体原因（长度/字符/空格）
  - 在 `CreateUserRequest`、`POST /auth/users` 调用，PR3 复用

- `CreateUserRequest`、`UpdateUserRequest`（`api/schemas/auth.py`）
  - `username` Pydantic validator 接入 `validate_username`
  - 保留 password `min_length=4`，不加其他规则

**前端：**

- 用户菜单 (`components/sidebar/UserMenu.tsx`) 加 "修改密码" 入口
- 弹窗：current_password / new_password / 确认 new_password
- 调 `api.changeMyPassword({current_password, new_password})`
- 错误提示：current 错误 → "当前密码错误"；其他 → 透传后端 message

**测试：**

- 后端：`tests/api/test_auth.py` 加自助改密 success / 旧密码错 / new 太短 三 case
- 后端：`tests/api/test_auth.py` username 正则 reject 各种非法输入

**影响文件**（参考）：

- `src/api/routers/auth.py` (新端点)
- `src/api/schemas/auth.py` (validator)
- `src/api/services/auth.py` (复用 hash/verify)
- `src/utils/validators.py` (新文件，`validate_username`)
- `frontend/src/lib/api.ts` (`changeMyPassword`)
- `frontend/src/components/sidebar/UserMenu.tsx` (入口)
- `frontend/src/components/dialogs/` (新弹窗组件，按现有 dialog 规范)

---

### PR2a：修 active-execution 删除路径

**状态**：✅ 已落地于 `main`（2026-05-07，commit `e829ad6`）。

**目标**：修复 `DELETE /api/v1/chat/{conv_id}` 在引擎执行中触发的 FK 违规静默失败。**纯 bug fix，不引入新功能。**

**当前 bug 链路**：

1. `DELETE /api/v1/chat/{conv_id}` (`src/api/routers/chat.py:300-314`) → `conversation_manager.delete_conversation` → `repo.delete_by_id`
2. 全程不查 `runtime_store`（admin 视图 `src/api/routers/admin.py:45` 早就在用 `list_active_conversations()`，但删除路径没接）
3. FK 全是 CASCADE (`src/db/models.py:164,232,272,320`)，行立即被干掉
4. 引擎在 post-processing 阶段 (`flush_all` / 事件批写 / `Message.response` 更新) 命中 FK 违规
5. 三个独立事务 (CLAUDE.md 已记录) 部分写一半失败被吞，留下不一致状态

**修复方案：引擎自检 + IntegrityError catch（two-layer guard）**

在 `controller.py` 的 post-processing 入口处：

```python
# Layer 1: 早 check，省掉无谓的写尝试
async with db.session() as s:
    if not await ConversationRepo(s).exists(conv_id):
        logger.info(f"Conversation {conv_id} deleted during execution, skip persistence")
        await runtime_store.release_lease(conv_id, message_id)
        return                       # 跳过 artifact flush / event 批写 / response 更新

# Layer 2: 三段独立事务每段包 IntegrityError，兜 SELECT-INSERT 之间的 TOCTOU
try:
    await artifact_mgr.flush_all()
except IntegrityError as e:
    logger.warning(f"Conv {conv_id} deleted mid-persist (artifact phase): {e}")
    return
# 同理 event 批写、Message.response 更新
```

**实施清单：**

- 新增 `ConversationRepository.exists(conv_id) -> bool` (`src/repositories/conversation_repo.py`) — 简单 `SELECT 1`
- `controller.py` post-processing 入口加 exists check
- post-processing 三个事务段都加 `IntegrityError` catch + 走 release_lease + 早返回
- POST /chat 入口可选加 IntegrityError catch（覆盖 "delete 撞 Message INSERT" 这个毫秒级窗口）— 转 404 给前端

**测试：**

- `tests/integration/test_delete_during_execution.py`（新文件）
  - 启一个 conversation，发一条耗时消息 → mid-stream 触发 DELETE → 验证：
    - 引擎不抛异常到顶层
    - 日志含 "deleted during execution, skip persistence"
    - DB 中 conversation/messages/events 全部清空
    - runtime_store 中 lease 已释放
- 单元测试：mock `ConversationRepo.exists` → False，验证 post-processing 直接 return

**影响文件：**

- `src/repositories/conversation_repo.py` (新增 `exists`)
- `src/core/controller.py` (post-processing 改造)
- `tests/integration/test_delete_during_execution.py` (新)

**已知限制（不在本 PR 修）：**

- 删除时引擎已经在跑，会跑完一整个 turn 才发现自己白干，浪费 LLM token。属于"协作式取消"问题，本 PR 不解
- 同样的方式不能解决"禁用用户后引擎仍在跑"的场景，因为禁用不删除会话，post-processing 找得到行，会正常写入。这个属于 cooperative cancel 问题，独立另说

---

### PR2b：硬删除用户 + 用户编辑表单

**状态**：✅ 已落地于 `main`（2026-05-07）。commits：
- `3612d3a` 主体（FK CASCADE + DELETE/GET-impact/GET-single 端点 + 右面板 UserDetailForm/CreateUserForm/DangerConfirmModal + UserManagementPanel inline UI 清理）
- `8d73e0a` reviewer follow-up（P2 CreateUserForm 单 submit 路径 + P3 DangerConfirmModal inline 错误显示）

**两处与原 plan 偏离**（实施期间确认）：
1. **self-protection 取代 last-admin guard** —— 用户审视后简化：admin 不能删自己 / 不能改自己 role / 不能改自己 is_active。配合"disabled admin 进不来 admin 后台"，足以保住至少 1 个活跃 admin。`UserRepository.count_admins()` 不需要，每次操作 O(1) 自身判断。错误消息更直观（"Cannot delete yourself" vs "Cannot delete the last admin"）。
2. **`UserRepository.hard_delete` 用 Core 级 `delete()`**，绕过 ORM `session.delete()`。原因：ORM 在配合 `lazy='selectin'` 加载的子集合时会主动 emit `UPDATE conversations SET user_id=NULL` 绕过 DB-level CASCADE，**即使设了 `passive_deletes=True` 也救不了**。`User.conversations` 仍加了 `passive_deletes=True` 作为正确的关系语义，但 hard_delete 路径显式走 Core `await session.execute(delete(User).where(...))` 才是稳的写法。集成测试覆盖了完整 cascade 链（user → conv → messages/events/artifacts）。

**目标**：admin 可以硬删用户；级联删除其全部会话。同时把现有 inline 编辑（改名/重置密码/启用禁用）迁移到右面板"用户详情"表单。

**前置**：PR0（master-detail layout）+ PR2a（否则硬删用户触发的级联会话删除会撞到同样的 FK bug）

**后端：**

- 新增 `DELETE /api/v1/auth/users/{user_id}`，admin 权限
- 新增 `GET /api/v1/auth/users/{user_id}/impact` — 返回 `{conversation_count: N}`，给前端二次确认显示
- FK 改为 CASCADE：`Conversation.user_id` 从 `ondelete="SET NULL"` (`src/db/models.py:100`) 改为 `ondelete="CASCADE"`
- Alembic migration：
  - 新文件 `src/db/alembic/versions/0002_user_cascade.py`
  - DROP CONSTRAINT + ADD CONSTRAINT (注意 SQLite 不支持 ALTER CONSTRAINT，要 batch op)
- `UserRepository.hard_delete(user_id)` 新方法

**前端（master-detail 形态）：**

#### 新增（master-detail 形态）

- 新组件 `UserDetailForm`（在 `UserManagementDetailPanel` 内部，按 `userManagementRightView.type` 分发）：
  - `edit-user` 模式 — 显示用户基本信息（id / username 不可改 / display_name / role / is_active / created_at）
  - 表单字段：display_name 输入框、role 下拉、密码重置按钮（点击后展开新密码字段 + 确认按钮）、启用/禁用 toggle、删除按钮（红色，置底）
  - 自身用户：删除按钮和 role 字段禁用（防误锁）
  - **"最后一个 admin" 保护**（与"防误锁"互补）：后端在 `DELETE` 和 `PATCH role='user'` 路径上判断，若操作会导致 admin 数 = 0 → 403。前端在 UI 上若已知 admin 计数为 1 也禁用对应按钮，但**不依赖前端**，后端必须强校验
  - `create-user` 模式同样由 `UserDetailForm` 渲染（或拆 `CreateUserForm` 复用大部分字段），表单字段：username（接 `validate_username`）+ password + display_name + role
- `UserManagementPanel` 中间面板的行点击 → `setUserManagementRightView({type: 'edit-user', userId})`
- `UserManagementPanel` 中间面板顶栏 "+ 新建用户" 按钮（取代 PR0 阶段的 inline 触发）→ `setUserManagementRightView({type: 'create-user'})`
- 删除二次确认：保留独立 `ConfirmModal`（destructive 操作打断 master-detail 流程，强制确认）
  - 显示影响："将级联删除该用户的 N 条会话，操作不可恢复"（调 `getUserImpact`）
  - **checkbox** `"我已了解此操作不可恢复"`，勾选才启用确认按钮
  - 确认按钮红色
  - 确认后右面板回到 `empty` 状态，列表刷新

#### 显式删除清单（PR0 fallback 清理）

PR2b **必须**在 `UserManagementPanel.tsx` 中删除以下 PR0 阶段保留的临时 inline 交互：

- `showCreate` 状态、`createForm` 状态、`creating` 状态、`handleCreate`
- `UserManagementPanel.tsx:184-240` 的整段 inline "新建用户" 表单 JSX
- `UserRow` 内 `editing` / `editValue` / `editRef` / `savingRef` 状态及 `startEditing` / `saveDisplayName` / `cancelEditing` 函数
- `UserRow` 内 `resettingPassword` / `passwordValue` / `passwordSaving` / `passwordRef` 状态及 `startResetPassword` / `handleResetPassword` 函数
- `UserRow` 行内的"重置密码 / 禁用 / 启用"按钮组（`isSelf ? ... : <>重置密码/禁用/启用</>` JSX 块）
- 行点击进入编辑（PR0 加的）保留并接管 row 主体，不再有 inline 编辑入口

清理后 `UserManagementPanel` 只剩：搜索框 + "+ 新建用户" 按钮 + 用户列表（行可点击，无 inline 编辑控件）。PR4 后再加 [用户/部门] tab 切换。

**测试：**

- 后端：`test_delete_user_cascades_conversations`
- 后端：`test_delete_user_with_active_conversation` — 用户被删时其会话正在执行 → DELETE 请求成功；engine post-process 命中 PR2a 的 exists check fail-soft；DB 干净；runtime_store 无孤儿 lease（**依赖 PR2a 已落地**）
- 后端：`test_delete_self_forbidden` （admin 不能删自己 — 防止误锁）
- 后端：`test_delete_last_admin_forbidden` （删除会导致 admin 数 = 0 → 403）
- 后端：`test_demote_last_admin_forbidden` （PATCH 把最后一个 admin 改成 user → 403）
- 前端：编辑表单各字段 round-trip；删除流程包含二次确认 modal

**影响文件：**

- `src/api/routers/auth.py` (新端点 + impact 端点)
- `src/db/models.py` (FK 改 CASCADE)
- `src/db/alembic/versions/0002_user_cascade.py` (新)
- `src/repositories/user_repo.py` (`hard_delete`)
- `frontend/src/components/chat/UserManagementDetailPanel.tsx` (扩展：分发 `edit-user` / `create-user`)
- `frontend/src/components/forms/UserDetailForm.tsx` (新组件，编辑/创建/删除表单)
- `frontend/src/components/chat/UserManagementPanel.tsx` (**删除所有 PR0 fallback inline UI**，按上方"显式删除清单"清理)
- `frontend/src/lib/api.ts` (`deleteUser`, `getUserImpact`)

---

### PR3：CSV 批量导入

**状态**：✅ 已落地于 `main`（2026-05-08）。commits：
- `5be2314` 主体（`src/utils/csv_import.py` charset-normalizer 解码 + header 标准化 + 行数 cap + 文件内 username 重复检测；`POST /auth/users/bulk-import` admin 端点：byte cap → parse → reject 内重 → 批查 existing → dept-path cache → 逐行 INSERT；`UserRepository.find_existing_usernames` 单次 IN 查；`BulkImportForm.tsx` 三态 UI：upload / submitting / result，含拖拽 + 模板下载 + 失败行 CSV 导出；`UserManagementPanel` 顶栏加"批量导入"按钮，三按钮共享 selected-state 样式）
- `eeaab40` reviewer follow-up A（**P1**：同步 bcrypt 阻塞 event loop ~50s/300 行 → 主 loop 拆 3 段 + `asyncio.gather + asyncio.to_thread` 并行 hash，bcrypt-python 在 C 层释放 GIL，hash 阶段缩到 ~6s 且 event loop 全程不卡；**P2**：CSV 路径绕过 schema `max_length=128` → 加 `_validate_field_lengths(row)` per 行兜 `display_name / dept_l* / 显式 password`，超长行 failed 而非 PG/MySQL mid-batch 500；**P3**：native `<input type="file">` value 不随 React state 清 → `clearNativeInput()` helper 在 `handleFile(null)` / `reset()` 调）
- `fd79300` reviewer follow-up B（P3 修补：上一轮只覆盖"换一个文件 / 再导入一批"两条主动路径，**submit catch 分支没清** —— "修源 CSV 重传"主流程仍 stuck（input.value 不变 → onChange 不触发 → 上传旧 File 句柄；Firefox 对已修改文件可能抛 NotReadableError）；统一在 catch 顶部 `setStage('upload') + setFile(null) + clearNativeInput()`，error 提示 / duplicate 红框照常渲染但 file 槽清空，submit 按钮变灰强制重选；顺手把"换一个文件"按钮改成 `text-accent + hover:underline` 与"下载 CSV 模板"对齐，affordance 一致）

**与原 plan 五处偏离**（实施期间确认）：
1. **`charset-normalizer` 替换 `chardet`** —— 项目已有依赖（`requirements.txt:23` + `doc_converter.py:229`），零新增
2. **`UserRepository.find_existing_usernames(set) -> set` 批查** —— 替代逐行 `get_by_username`，1000 行场景免 N+1
3. **同 CSV 内部门路径 cache**（`dict[tuple[str, ...], Optional[str]]`）—— 200~300 行常聚集少数部门，省掉绝大多数 dept SELECT
4. **部门路径 gap（`dept_l1='', dept_l3='X'`）严格拒绝** —— importer 自己校验，不动 `resolve_department_path` 的折叠语义（cascader 路径仍能用空中间段表达"主动选到第几级"）
5. **`MAX_BULK_IMPORT_BYTES = 5MB` 字节硬 cap** —— 在解析前兜恶意大文件 OOM；与 `MAX_BULK_IMPORT_ROWS = 1000` 行数 cap 是双层保护

**目标**：admin 可上传 CSV 一次创建 200~300 用户。

**前置**：PR0 + PR1 + PR4。
- PR0：批量导入 UI 在右面板渲染
- PR1：共用 `validate_username`
- PR4：CSV 里的 dept_l1/2/3 列要靠 `resolve_department_path` 联动建表。**PR4 之前发布 PR3 是错的**（首次大宗导入会丢部门数据），按推荐顺序 PR4 已先于 PR3 落地

**CSV 格式**：

| username | password | display_name | dept_l1 | dept_l2 | dept_l3 |
|---|---|---|---|---|---|
| user01 |  | 用户一 | 部门A | 子部门A1 | 小组A1a |
| user02 | custompw | 用户二 | 部门B | | |

- 必填列：`username`
- 可选列：`password`（**留空时使用 username 作为默认密码**）、`display_name`、`dept_l1`、`dept_l2`、`dept_l3`
- 列顺序通过表头识别，允许子集（缺失列等同于空值）
- 编码：UTF-8 优先；fallback 用 `chardet` sniff（处理 Excel 中文环境默认 GBK 的情况）
- **行数上限**：`MAX_BULK_IMPORT_ROWS = 1000`，超过直接返回 400（防止误传巨型文件爆内存）

**后端：**

- 新增 `POST /api/v1/auth/users/bulk-import`，admin 权限，multipart upload
- 解析用 stdlib `csv.DictReader`
- 处理流程：
  1. 解码 + sniff 编码（必要时）
  2. 全量读入内存；行数 > `MAX_BULK_IMPORT_ROWS`（1000）→ 整体拒绝 400
  3. 文件内查重 (`username`)：有重复 → 整体拒绝，返回 400 + 重复行号
  4. 逐行处理：
     - `validate_username` 失败 → `failed.append({row, username, reason})`
     - 已存在 → `skipped.append({row, username})`
     - 部门字段非空 → `resolve_department_path([dept_l1, dept_l2, dept_l3])` 拿到 `department_id`（PR4 已落地，必有此函数）
     - 正常 INSERT（含 `department_id`）
  5. 返回 `{created: [user_response, ...], failed: [...], skipped: [...]}`

**前端（master-detail 形态）：**

- 用户管理面板（中间）顶栏加"批量导入"按钮 → `setUserManagementRightView({type: 'bulk-import'})`（uiStore 加这个 type）
- 新组件 `BulkImportForm`（在右面板渲染）：
  - 步骤 1：拖拽 / 选择 CSV 文件 → 解析（前端只做格式预检：UTF-8 解码、表头识别、行数）
  - 步骤 2：预览前 5 行 + 字段映射确认 + 解析告警（编码可疑 / 缺列等）
  - 步骤 3：点提交 → 调 `bulkImportUsers` → 显示进度
  - 步骤 4：结果展示 — 成功数 / 跳过数 / 失败列表（行号 + 用户名 + 原因），下载失败列表为 CSV
  - 整个流程在右面板内完成；不打断中间用户列表

**测试：**

- 单元：CSV 解析（UTF-8 / GBK / 缺列 / 多余列 / 空行）
- 集成 A：导入 50 行**无文件内重复**的混合 case（有效 / username 非法 / 已存在 / 部门新建 / 部门已存在）→ 验证 `created/failed/skipped` 各分类正确
- 集成 B：含文件内重复的 CSV → 整体拒绝，返回 400 + 重复行号（这条是早返回，不进入逐行处理）

**影响文件：**

- `src/api/routers/auth.py` (新端点)
- `src/api/schemas/auth.py` (`BulkImportResponse`)
- `src/utils/csv_import.py` (新文件，解析 + 编码 sniff)
- `frontend/src/components/chat/UserManagementPanel.tsx` (顶栏"批量导入"按钮)
- `frontend/src/components/chat/UserManagementDetailPanel.tsx` (扩展：分发 bulk-import)
- `frontend/src/components/forms/BulkImportForm.tsx` (新组件，多步流程)
- `frontend/src/stores/uiStore.ts` (RightView 加 `bulk-import` type)
- `frontend/src/lib/api.ts` (`bulkImportUsers`)

---

### PR4：Department 表 + 级联选择器 + 部门管理 UI

**目标**：建立部门体系数据基础设施；提供创建用户时的级联选择 + 现场新建 UX；提供完整的部门管理界面（新建/改名/搬家/删除）。

**前置**：PR0（master-detail layout）+ PR2b（扩展其 `UserDetailForm`，把部门级联选择嵌进用户编辑）

**重申**：**本轮只采集组织数据，不接通 agent/工具**。KB / principal 注入留给未来。

**Schema：**

```python
class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # dept-{uuid}
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("departments.id", ondelete="RESTRICT"),  # 不允许删有子的
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at / updated_at  # 同其他表

    __table_args__ = (
        UniqueConstraint("parent_id", "name", name="uq_dept_parent_name"),
        Index("ix_dept_parent", "parent_id"),
    )
```

User 表新增：

```python
department_id: Mapped[Optional[str]] = mapped_column(
    String(64),
    ForeignKey("departments.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
)
is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

**Migration**：`0003_departments.py`

- CREATE departments 表
- ALTER users 加 `department_id` (nullable) + `is_system` (default false)
- 不 backfill — 现有用户的 `department_id` 留 NULL，admin 后续手动补或下次批量重导

**后端工具函数：**

```python
async def resolve_department_path(
    repo: DepartmentRepository,
    path: list[str],
) -> Optional[str]:
    """
    输入部门路径列表（顶层 → 末级）→ 返回末级 dept_id
    每级 SELECT (name=:name AND parent_id <=> :parent)，缺失则 INSERT
    name 在比较前 .strip()
    返回 path 末级的 id；空 path / 全空字符串 → None

    并发安全：INSERT 命中 UNIQUE(parent_id, name) 冲突 → 捕获 IntegrityError →
    重新 SELECT 拿已存在的 dept_id（被其他并发请求抢先创建的情况）。
    最多重试 1 次（第二次仍冲突说明真有 bug，向上抛）。
    """
```

**API：**

- `GET /api/v1/departments?parent_id=xxx` — 列出某父下的所有子部门（级联选择器 + 树视图都用）
  - `parent_id` 缺省/空 → 一级部门
- `GET /api/v1/departments/tree` — 返回完整树（部门管理 UI 主视图用）
- `GET /api/v1/departments/{id}` — 单个部门详情（含 `user_count`, `child_count`，编辑面板用）
- `POST /api/v1/departments/resolve` — body `{path: [...]}`，返回 `{id: str}`，admin 权限（创建用户/批量导入时调）
- **`POST /api/v1/departments`** — body `{name, parent_id?}`，显式创建（admin 权限）
- **`PATCH /api/v1/departments/{id}`** — body `{name?, parent_id?}`，改名 + 搬家
  - 后端必须做**环检测**：从 `parent_id` 向上遍历，遇到自己 → 400
  - `UNIQUE(parent_id, name)` 冲突 → 409 + 提示用户选合并或改名
- **`DELETE /api/v1/departments/{id}`** — 删除空部门
  - 有 user 或子部门挂着 → 409 + `{user_count, child_count}` 让 admin 知道先迁

**前端（master-detail 形态）：**

#### A. 创建用户时的部门级联选择

`UserDetailForm`（PR2b 引入的右面板编辑表单，PR4 扩展）/ `CreateUserForm`（PR0 占位 + 后续填充）：

- 新建用户表单内嵌 `DepartmentCascader` 组件（一级/二级/三级，每级下拉带"+ 新建当前级"选项）
- 用户深度可变 — 选到第几级算第几级
- 提交时先调 `/departments/resolve` 拿 `department_id`，再调 `POST /users`
- `DepartmentCascader` 同样在 PR3 `BulkImportForm` 的修正流程里复用

#### B. 部门管理界面（master-detail）

**入口**：PR4 在 `UserManagementPanel` 顶部新增 [用户] / [部门] tab 切换控件（PR0 阶段没加这个控件，PR4 才加）。

- 顶部 [用户] / [部门] tab 切换：
  - 选 [部门] 后中间显示树视图：
    ```
    ▼ 部门A          (15 人)  [← 选中态]
      ▼ 子部门A1      (3 人)
        · 小组A1a     (1 人)
      · 子部门A2      (12 人)
    ▼ 部门B          (8 人)
    ```
  - 树节点显示用户数，点击节点 → `setUserManagementRightView({type: 'edit-dept', deptId})`
  - 顶部 "+ 新建一级部门" 按钮 → `setUserManagementRightView({type: 'create-dept', parentId: null})`
  - 树节点 hover 时显示 "+ 新建子部门" 小按钮 → `setUserManagementRightView({type: 'create-dept', parentId: deptId})`

- 右面板新组件：
  - `DepartmentDetailForm`（`edit-dept` 模式）：
    - 字段：name（输入框）、parent_id（DepartmentCascader 选父，禁选自己和自己的子孙）
    - 操作：保存（调 `PATCH`）、删除（红色置底，调 `DELETE`）
    - 删除前提示：若该部门下有 user 或子部门，按钮禁用 + tooltip 提示先迁走
    - 删除二次确认：独立 `ConfirmModal` + checkbox，与 PR2b 用户删除一致风格
    - 显示该部门下的用户列表（只读，可点跳转到 edit-user）
  - `CreateDepartmentForm`（`create-dept` 模式）：name + parent_id（默认填充传入的 parentId） + 创建按钮

#### C. 共享组件

- **`DepartmentCascader`**（`frontend/src/components/forms/DepartmentCascader.tsx`）：
  - props: `value: string | null`, `onChange: (deptId: string | null) => void`, `excludeSubtreeOf?: string`（搬家场景：禁选自己的子孙）
  - 内部维护 N 级下拉状态，每级调 `GET /departments?parent_id=...`
  - 提供 "+ 新建当前级" 入口（弹小输入框 → 调 `POST /departments`）

#### D. 用户搜索扩展（按部门搜索）

部门表落地后，admin 搜索用户时也希望按部门名命中。**子树匹配** — 搜根部门名应该返回整个条线的人（包括所有子部门下的用户），而非仅直属。

**后端改动**：扩展 `UserRepository._apply_search_filter`：

```python
# 1. 找出名字匹配的部门 (ILIKE)
matching_dept_ids = await ...select(Department.id).where(Department.name.ilike(pattern))

# 2. 内存里展开子树（部门数几十个，全量拉一次按 parent_id 建索引 + BFS）
subtree_ids = expand_subtree(all_depts, matching_dept_ids)

# 3. OR 三条：username ILIKE、display_name ILIKE、department_id IN (subtree)
return query.where(or_(
    User.username.ilike(pattern),
    User.display_name.ilike(pattern),
    User.department_id.in_(subtree_ids) if subtree_ids else false(),
))
```

**`expand_subtree(depts, seeds) -> set[str]` helper**：在 `src/utils/department_tree.py`（新文件，部门管理 UI 也复用其反向索引能力）。BFS 内存遍历，无 SQL 递归。

**性能**：每次搜索多两次查询（dept 名匹配 + 全 dept 拉取），表小不需要缓存。

**前端**：`UserManagementPanel.tsx` 搜索框 placeholder 由 `"搜索用户名或显示名..."` 改为 `"搜索用户名 / 显示名 / 部门..."`。

**测试：**

- 单元：`resolve_department_path` 各种 case（新建 / 复用 / 空格 trim / 空 path）
- 单元：`UNIQUE(parent_id, name)` 并发插入冲突 → IntegrityError → catch 后重 SELECT
- 单元：环检测 — `PATCH` 把 `dept-001` 的 parent 设为 `dept-003`（其子孙）→ 400
- 单元：`DELETE` 非空部门 → 409 with counts
- 集成：`GET /departments/tree` 返回正确的 parent → children 链；`GET /departments?parent_id=...` 列表过滤正确
- 集成：搬家后 `GET /departments/tree` 反映新结构；改名后 `User.department_id` 不变
- 单元：`expand_subtree` — 单根 / 多根 / seed 找不到 / 深度 0~3
- 集成：用户搜索按部门 — 搜根部门名返子树所有用户；搜叶部门名只返直属用户；搜 display_name 仍走原 ILIKE；空查询返全部
- e2e：建/改/搬/删走完整 master-detail 流程

**影响文件：**

- `src/db/models.py` (Department + User 加列)
- `src/db/alembic/versions/0003_departments.py` (新)
- `src/repositories/department_repo.py` (新)
- `src/api/routers/departments.py` (新)
- `src/api/schemas/department.py` (新)
- `src/utils/department_resolve.py` (新，`resolve_department_path` + 环检测 helper)
- `src/utils/department_tree.py` (新，`expand_subtree(depts, seeds)` BFS helper — 部门管理 UI 与用户搜索复用)
- `src/repositories/user_repo.py` (`_apply_search_filter` 加部门子树匹配)
- `frontend/src/components/chat/UserManagementPanel.tsx` (顶部 [用户/部门] tab 切换；搜索框 placeholder 改为含 "部门")
- `frontend/src/components/chat/DepartmentTreeView.tsx` (新，中间面板的树视图)
- `frontend/src/components/chat/UserManagementDetailPanel.tsx` (扩展：分发 edit-dept/create-dept)
- `frontend/src/components/forms/DepartmentDetailForm.tsx` (新)
- `frontend/src/components/forms/CreateDepartmentForm.tsx` (新)
- `frontend/src/components/forms/DepartmentCascader.tsx` (新，含 `excludeSubtreeOf` 支持)
- `frontend/src/stores/uiStore.ts` (RightView 加 `edit-dept`/`create-dept` type；中间 tab 状态 `userMgmtTab: 'users' | 'departments'`)
- `frontend/src/lib/api.ts` (departments 系列 API)

---

### PR5a：用户面板批量操作

**状态**：✅ 已落地于 `main`（2026-05-08）。commits：
- `65519a6` 主体（PR5a + PR5b 同笔提交：`POST /auth/users/bulk-action` + `GET /auth/users/bulk-impact` + `ConversationRepository.count_by_users` + uiStore `selectionMode` / `userManagementSelection` + 顶栏"批量管理"按钮 + `BulkActionPanel` 右面板（idle / set-department / confirm-delete 三态）+ `Checkbox.tsx` 共享组件 + 25 个 PR5a 集成测试）
- `bcacb09` reviewer follow-up（**P1**：`bulk_user_action` 单条失败原本走宽 `except Exception` 不 rollback，set_department 的 dept 在 loop 外预校验通过后被并发 admin 删除会让 per-row UPDATE 撞 FK；改窄 `except IntegrityError` + `await session.rollback()` 与 PR3 `bulk_import_users` 同模式；其他异常冒泡为 5xx loud failure 与 CLAUDE.md "不为不会发生的场景加防御代码" 一致；加 `test_integrity_error_one_row_does_not_poison_subsequent` + `test_unknown_exception_bubbles_loudly` 两个回归）

**与原 plan 三处偏离**：
1. **last-admin guard 直接落 self-protection** —— 与 PR2b 的演进一致，bulk-action 单条 self-id 走 `forbidden_self`，不引入 `count_admins()`。配合"disabled admin 进不来后台"，足以保住至少 1 个活跃 admin。
2. **`failed.reason` 词汇精简** —— 原 plan 写"含 `last_admin`"已被 (1) 取消；最终词汇只 `forbidden_self` / `not_found` / `internal_error`，与 PR2b / PR2a fail-soft 模型对齐。
3. **"全选当前页"先做、跨页全选延后** —— 跨页选择需要拉一次全量 IDs，UX 风险大于本轮收益（admin 实际选择多半在 1 页内），等真有反馈再补。原 plan 的"已选中此页 N 条，点击此处选中所有 M 条匹配项" Gmail 模式不在本轮。

**目标**：用户管理面板支持多选 + 批量禁用 / 启用 / 删除 / 改部门。

**前置**：PR0（master-detail layout）+ PR2a（active execution 检查）+ PR4（批量改部门 + DepartmentCascader 复用）。

**后端：**

- 新增 `POST /api/v1/auth/users/bulk-action`，admin 权限
  - body: `{ids: [str], action: "disable" | "enable" | "delete" | "set_department", payload: {...}}`
  - 逐条处理，best-effort
  - 删除时复用 PR2a 的安全删除路径（fire-and-forget：不查 active，直接删；引擎 fail-soft 处理）
  - 返回 `{succeeded: [id, ...], failed: [{id, reason}, ...]}`，`reason` 含 `not_found` / `last_admin` / 其他校验错误，**不含 `executing`**
- 新增 `GET /api/v1/auth/users/bulk-impact?ids=...` — 给前端二次确认弹窗显示 "将影响 N 条会话"

**前端（master-detail 形态）：**

- 选择模式开关（顶栏按钮"批量管理"）：进入后
  - `setSelectionMode(true)` + `setUserManagementSelection([])`（独立顶层状态，与 RightView 解耦）
  - 中间每行加 checkbox（绑定到 `userManagementSelection`）
  - 右面板自动切换为 `bulk-action` view（`setUserManagementRightView({type: 'bulk-action'})`）
  - 右面板订阅 `userManagementSelection`，显示当前选中数 + 4 个动作按钮（禁用/启用/改部门/删除）
  - 中间面板底部不再显示工具栏 — 动作集中在右面板
- "全选"渐进披露：中间面板顶部，先勾当前页，提示条 `"已选中此页 20 条，点击此处选中所有 347 条匹配项"`
- 退出选择模式：右面板回到 `empty` 状态；选中清空
- 删除二次确认：独立 `ConfirmModal`（destructive 操作打断流程）
  - "将删除 X 个用户、共 Y 条会话"
  - checkbox `"我已了解此操作不可恢复"`
  - 红色确认按钮
- 改部门：右面板的"改部门"按钮直接展开 `DepartmentCascader`（PR4 已建）→ 选末级 → 调 `bulk-action(action="set_department")`

**测试：**

- 后端：bulk-action 各 action 的 succeeded/failed 分流
- 后端：bulk delete 含一个有 active 执行的用户 → 该条也进 succeeded（fire-and-forget），引擎 fail-soft；其余正常
- 后端：bulk delete 含最后一个 admin → 该条 failed reason=`last_admin`
- 前端 e2e：选择模式切换 / 跨页选择（如果实装跨页）/ 右面板与中间选中状态联动

**影响文件：**

- `src/api/routers/auth.py` (新端点)
- `src/api/schemas/auth.py` (BulkActionRequest/Response)
- `frontend/src/components/chat/UserManagementPanel.tsx` (选择模式切换 + checkbox)
- `frontend/src/components/chat/UserManagementDetailPanel.tsx` (扩展：分发 bulk-action)
- `frontend/src/components/forms/BulkActionPanel.tsx` (新，右面板批量动作面板)
- `frontend/src/components/dialogs/BulkDeleteConfirm.tsx` (新，二次确认 modal)
- `frontend/src/stores/uiStore.ts` (新增 `selectionMode: boolean` + `userManagementSelection: string[]` + setter)

---

### PR5b：会话面板批量删除

**状态**：✅ 已落地于 `main`（2026-05-08）。commits：
- `65519a6` 主体（PR5a + PR5b 同笔提交：`POST /api/v1/chat/bulk-delete` + `BulkDeleteRequest/Response/FailedItem` schemas + `ConversationBrowser` 选择模式（顶栏"批量管理"按钮 / 行 checkbox / 底部工具栏 / Esc 退出）+ `bulkDeleteConversations` API + 9 个 PR5b 集成测试）
- `bcacb09` reviewer follow-up（**P2**：reviewer 反馈 docstring "best-effort" 与代码只 catch `NotFoundError` 不一致；audit `src/db/models.py` 后确认所有 conversations.id FK 都是 ondelete=CASCADE，单行 IntegrityError 在这条路径上不存在；按 CLAUDE.md "不为不会发生的场景加防御代码" 紧 docstring 而非加冗余 catch）

**与原 plan 两处偏离**：
1. **不新增 admin bulk-delete 端点**（原 plan 写 `POST /admin/conversations/bulk-delete`）—— 用户对齐："admin 管 user 不管 user 数据，要清就删用户走 FK CASCADE"；`AdminConversationList.tsx` 维持只读 observability。已存为 memory `feedback-admin-scope-user-mgmt.md`（"admin 角色边界 = 用户管理"），未来类似设计自动遵循。
2. **不复用 `BulkDeleteConfirm` 新组件** —— 直接用 PR2b 的 `DangerConfirmModal`（消息支持 `whitespace-pre-line` 多行 + 已带 acknowledge checkbox + 已带 inline error 处理），新建组件就是冗余。

**目标**：自己的会话面板（`ConversationBrowser.tsx`）支持多选批量删除。**Admin 视角不做**（详见上"偏离 1"）。

**前置**：PR2a。

**后端：**

- 新增 `POST /api/v1/chat/bulk-delete`，body `{ids: [str]}`
  - 对每个 ID 验证 ownership（防越权）
  - 复用 PR2a 的安全删除路径（fire-and-forget：不查 active，直接删；正在跑的引擎自检 fail-soft）
  - 返回 `{deleted: [...], failed: [{id, reason: "not_found" | "forbidden"}]}` — **不再有 `executing`**，因为不再做 active 检查
- 新增 `POST /api/v1/admin/conversations/bulk-delete`，admin 权限
  - 不查 ownership（admin 全局）
  - 同样的 fire-and-forget 行为

**前端：**

- `ConversationBrowser.tsx` 选择模式开关
- 选中后底部工具栏只有一个"删除"按钮
- 二次确认弹窗：count + checkbox + 红色按钮
- `AdminConversationList.tsx` 同样 — 但弹窗文案强调"跨用户"

**测试：**

- 后端：bulk-delete 越权（普通用户传别人的 conv_id）→ failed 中出现 forbidden 而非 deleted
- 后端：bulk-delete 含一个正在跑的会话 → 该条也进 deleted（fire-and-forget），引擎 post-process 命中 PR2a fail-soft；DB 干净

**影响文件：**

- `src/api/routers/chat.py` (新端点)
- `src/api/routers/admin.py` (新端点)
- `frontend/src/components/chat/ConversationBrowser.tsx` (选择模式)
- `frontend/src/components/sidebar/AdminConversationList.tsx` (选择模式)
- `frontend/src/components/dialogs/BulkDeleteConfirm.tsx` (PR5a 已建，复用)

---

## 跨 PR 验证清单

PR 全部落地后，端到端走一遍：

- [ ] 普通用户登录 → 改密码 → 重新登录验证
- [ ] admin 进入用户管理 → 右面板自动显示空状态；退出后右面板恢复 ArtifactPanel 之前的可见性
- [ ] admin 创建用户用级联部门选择器 + 现场新建一级 → 部门表正确联动
- [ ] admin 在部门管理 tab 改名某部门 → 用户行的部门显示自动更新（无需迁移用户数据）
- [ ] admin 把某部门搬到自己子孙下 → 后端 400 + 前端提示
- [ ] admin 删非空部门 → 后端 409 + 提示先迁
- [ ] admin CSV 批量导入 50 用户（含部门字段）→ 验证 Department 表自动建好；验证导入用户的 `department_id` 正确指向 resolve 出来的末级 dept_id；验证默认密码 = username 可登录
- [ ] admin 在用户搜索框输入根部门名 → 返回整个子树的所有用户（含子部门）；输入叶部门名 → 仅返直属
- [ ] admin 选 5 个用户批量改部门 → 验证更新；右面板批量动作面板正确显示
- [ ] admin 选 3 个用户批量禁用 → 被禁用户登录失败
- [ ] admin 尝试删自己 → 403；尝试删最后一个 admin → 403；尝试把最后一个 admin demote → 403
- [ ] admin 硬删一个有 active 执行的用户 → 删除请求成功 + 引擎自动 skip persistence（参考 PR2a 的 fail-soft 机制）
- [ ] 用户在自己的会话面板批量删除 10 条会话（其中一条正在跑）→ **10 条全部进 deleted**；后台日志显示正在跑的那条引擎 fail-soft 跳过持久化；DB 干净
- [ ] 引擎中途会话被删 → 日志显示 skip persistence；DB 干净；runtime_store 无孤儿 lease

---

## 不做的事（明确划定）

避免 PR review / 后续讨论时反复纠结：

- ❌ **强制改密 flag** — 默认密码 = username，靠口头通知
- ❌ **type-username 二次确认** — 用 checkbox 模式
- ❌ **xlsx 后端解析** — 一律 CSV
- ❌ **password 复杂度规则** — 完全放开
- ❌ **部门合并端点（merge）** — 用 PR5a 批量改部门 + 删空部门等价实现
- ❌ **协作式取消（cooperative cancellation）** — disable / delete 都不打断已在跑的 engine。如果未来要做，是独立大功能
- ❌ **JWT 黑名单** — 禁用用户依赖 `get_current_user` 的 per-request `is_active` 检查就够；token 自然过期由 `JWT_EXPIRY_DAYS` 兜底
- ❌ **principal 注入到 engine state** — KB 立项时再做
- ❌ **每用户自定义 agent md** — 想法记录在此，等需求成熟再设计

---

## 参考文件清单（讨论中查阅过）

- `src/api/routers/auth.py` — 现有认证 + 用户 CRUD
- `src/api/routers/chat.py` — 会话 CRUD（含 line:300 单条 delete）
- `src/api/routers/admin.py` — admin 视图 + `list_active_conversations` 用法（line:45）
- `src/api/dependencies.py:300-324` — `get_current_user` 每请求 `is_active` 校验
- `src/api/schemas/auth.py` — 现有 schema
- `src/repositories/user_repo.py`、`src/repositories/conversation_repo.py` — Repo 实现
- `src/db/models.py:41-76` — User 表
- `src/db/models.py:100,164,232,272,320` — FK ondelete 设置
- `src/core/conversation_manager.py:492-503` — `delete_conversation`（无 lease 检查）
- `src/core/controller.py` — turn 控制器（PR2a post-processing 入口加 exists check + IntegrityError catch）
- `src/api/services/runtime_store.py` — Lease 接口
- `frontend/src/components/chat/UserManagementPanel.tsx` — 当前用户面板（PR0 重构入口）
- `frontend/src/components/chat/ConversationBrowser.tsx` — 当前会话面板
- `frontend/src/components/sidebar/AdminConversationList.tsx` — admin 会话面板
- `frontend/src/components/sidebar/UserMenu.tsx` — sidebar 用户菜单（PR1 改密入口）
- `frontend/src/components/layout/ThreeColumnLayout.tsx` — 三栏布局（PR0 改造右面板可见性）
- `frontend/src/app/page.tsx` — 顶层页面（PR0 改造右面板内容动态选择）
- `frontend/src/stores/uiStore.ts` — UI 状态（PR0 加 `userManagementRightView`）
- `frontend/src/lib/api.ts` — 前端 API 调用层
- `CLAUDE.md` — 三层责任模型 / 事务边界 / 错误处理哲学

---

> 状态：本计划已和用户对齐，落地 PR 前不再回到设计讨论。如 PR 实施中发现新问题（典型如 SQLite 不支持某种 ALTER），PR 内自决并在 PR 描述中说明，必要时回写本文档。
