# Native Tool Call 阶段 0 基线与 Cutover Runbook

> 状态：阶段 0 实现与开发环境验证完成
>
> 日期：2026-07-29
>
> 适用分支：`codex/native-tool-calls`

## 目的与边界

本文记录阶段 0 的可重复基线、Provider probe 运行方式、停机迁移规模盘点，以及阶段 5 cutover 的运维契约。

历史迁移采用**完全停机**模式：维护页阻断新请求，等待执行清空，停止全部 backend writer，完成数据库快照后，才开始 scan/generate/apply/verify。迁移程序不实现在线预生成、head fingerprint、二次重扫或变化 leaf 补算。

阶段 0 只提供 `scan` 和 `report`；不会生成摘要，也不会写 `MessageEvent`。

## 已冻结基线

基线修复提交：`57af7dc4 chore(skills): rebuild skill creator bundle`

当前开发环境：

- Conda env：`artifact-flow`
- Python：3.12.12
- pytest：9.0.2
- 相关主链路：372 passed
- 全量后端：1981 passed、42 skipped、1 个既有 SQLAlchemy warning

Provider probe 的协议结论不能使用该 Conda env，因为其中 LiteLLM 为 1.82.4，而部署 lockfile 固定为 1.86.0。Probe 必须在 Python 3.11、`requirements.lock` 构造的隔离环境执行。

## Provider probe

探针：`tests/manual/native_tool_call_probe.py`

必须从仓库根目录运行：

```bash
python -m tests.manual.native_tool_call_probe --models all \
  --output tmp/native-tool-call-probe.json
```

推荐使用由当前 commit 构建的 Python 3.11 容器，确保安装 `requirements.lock` 中的 LiteLLM 1.86.0。探针读取现有 `DASHSCOPE_API_KEY`，但报告不会保存 API key、完整 reasoning 或 image data URI。

协议 gate：

- 五个候选模型都必须通过最小文本 native tool-call 闭环；
- 返回 `reasoning_content` 时，第二次请求原样回放不得报错；
- `qwen3.7-plus`、`kimi-k2.6` 必须通过单图和同一 carrier 多图；
- usage 缺失、`__reason` 偶发遗漏、模型未主动产生可选 multi/content 形态只作 observation；
- 产生了 tool-call delta 却不能按 index/id 组装、JSON 无法解码或后续历史被拒绝，属于协议失败。

2026-07-29 的完整脱敏报告见
[`native-tool-call-provider-probe-report.json`](native-tool-call-provider-probe-report.json)。
运行环境为 Python 3.11.15、LiteLLM 1.86.0，lockfile 和 probe 源码哈希均写入报告；
五个候选模型的硬协议 gate 全部通过：

| 模型 | 文本闭环 | multi/content replay | image carrier | 结论 |
|---|---|---|---|---|
| `qwen3.7-plus` | pass | pass | 单图/多图消息均被接受 | 协议 pass；视觉内容准确率与调用遵循有观察项 |
| `deepseek-v4-flash` | pass | pass | 不适用 | pass |
| `glm-5.2` | pass | pass | 不适用 | pass |
| `kimi-k2.6` | pass | pass | 单图/多图 pass | pass |
| `MiniMax-M2.5` | pass | pass | 不适用 | pass |

Qwen 在两次前置运行中曾把可选 multi 场景的函数名生成为未声明的
`example_function_name`；最终完整运行可组装并回放，但这仍是模型行为稳定性观察项。
最终运行中 Qwen 接受了完整 assistant/tool/image/user 顺序并返回正常响应，但把合成图片
`42`/`17,29` 读成了 `29`/`17,28`。探针因此把视觉内容准确率与 wire protocol
兼容性分开：HTTP/流错误、空响应或 carrier 历史被拒绝仍会阻断，OCR 偏差只记录观察。
Kimi 在同一图片上返回了正确数字。

## 停机迁移规模盘点

CLI：`scripts/native_tool_history_migration.py`

Checkpoint 必须是应用数据库之外的独立 SQLite 文件：

```bash
python scripts/native_tool_history_migration.py scan \
  --checkpoint /secure/operator/native-tool-cutover.sqlite \
  --migration-id native-tool-cutover-2026-07

python scripts/native_tool_history_migration.py report \
  --checkpoint /secure/operator/native-tool-cutover.sqlite \
  --migration-id native-tool-cutover-2026-07 --json
```

`report` 只有在 `ready_for_apply=true` 时退出 0；尚有 pending/running task、候选
boundary 已耗尽、缺少 task row 或 reset 未成功时均退出 1。Semantic 单独失败仍可由
mechanical candidate 回退。

扫描器会响亮拒绝：

- active branch 缺失、悬空或不是 leaf；
- message parent 缺失或形成环；
- event 引用扫描结果中不存在的 message；
- checkpoint 与应用 SQLite DB 指向同一文件。

任务构造：

- 所有 leaf：`lead_agent/mechanical`；
- 每个 conversation 的 active leaf：额外 `lead_agent/semantic`；
- leaf 路径中出现的每个非 lead agent：`reset`。

同一 active lead 的 semantic 与 mechanical 是两个独立 checkpoint task。Semantic 失败本身不阻塞；只要 mechanical fallback 成功即可。非 active leaf 的 mechanical 失败、semantic/mechanical 同时失败或 reset 失败才会耗尽 boundary 候选。

本地阶段 0 验证使用正在运行的 SQLite 的一致性 backup，而非直接扫描 live DB：

```text
conversations=2, messages=2, leaves=2, active=2
mechanical tasks=2, semantic tasks=2, reset tasks=0
blocking=0
```

更新后的 checkpoint schema 再次扫描同一快照得到相同规模；新建任务均为 pending，
所以 `ready_for_apply=false` 且 `report` 按契约退出 1。阶段 0 不生成摘要，这是预期结果。

生产维护窗口要用实际 semantic task 数量估算；如果完全停机耗时不可接受，应在阶段 1 前重新决策迁移方式，不给停机脚本增加在线同步分支。

## 阶段 5 Cutover 顺序

### 1. 进入维护状态

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow \
  maintenance on "Native tool-call protocol migration"
```

确认业务、API 和 SSE 经 Caddy 返回维护页，健康端点仍可观测。

### 2. Drain active executions

通过管理面或直连 backend 的 `/api/v1/admin/runtime` 检查共享 RuntimeStore 的 `active_conversations`，等待其为空。设置明确的现场超时；超时任务先请求取消并等待取消终态完成，不在事件持久化过程中强杀进程。

### 3. 停止全部 backend writer

停止所有 backend replica，保留 Caddy、数据库和 Redis。多主机部署必须逐 app host 确认 backend 容器为 stopped；不能只停负载均衡当前命中的一个实例。

完成后再次确认：

- 没有 backend 进程持有应用 SQLite/数据库连接；
- RuntimeStore 不存在 active conversation lease；
- 维护页仍开启。

阶段 5 必须把目标部署的具体 stop/start 命令填入现场 runbook；不要让一次性迁移脚本通过进程扫描猜测 writer 状态。

### 4. 数据库快照

- SQLite：backend 停止后使用 SQLite backup API/`.backup`，不要只复制主 `.db` 而遗漏 WAL；
- PostgreSQL/MySQL：使用现场数据库备份体系生成一致性快照，并在隔离环境验证可恢复；
- 记录快照标识、时间和当前 release ID。

在快照完成前不得运行任何迁移写入。

### 5. Scan、generate、apply、verify

1. 执行一次 `scan`，创建 immutable manifest/checkpoint；
2. 生成全 leaf mechanical、active semantic 与 subagent reset；
3. Semantic 失败自动选择 mechanical；
4. 使用确定性 event ID，在一个事务中追加每个 START/SUMMARY pair；
5. 验证所有 leaf/agent 都有可用 boundary；
6. Backend 在全过程保持停止。

Checkpoint 只负责停机期间模型任务的中断恢复。若进程崩溃，保持服务停机并使用 `--resume`；不要重启旧 backend 后继续复用 checkpoint。

### 6. 部署 native runtime，保持维护页

使用阶段 0 已加入的：

```text
afctl apply TARGET --keep-maintenance
```

apply 仍会在 reconcile 前启用维护页；该选项让成功写入 release state 后（以及失败但成功回退后）不执行 maintenance off，不增加新的 release state 或 apply 路径。新 runtime 健康后，直连 backend 完成 native smoke，再显式执行：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow maintenance off
```

### 7. 回滚

- Native runtime 启动前失败：保持维护状态，修复后 resume；必要时恢复快照并继续旧版本。
- Native runtime 已启动但维护页尚未关闭：停止新 runtime，恢复数据库快照，再恢复旧 release。
- 维护页关闭后发现协议级故障：立即重新进入维护状态；由于可能已经产生 native events，必须按数据恢复方案处理，不能只执行镜像 rollback。

`afctl rollback` 不是数据库时间机器。

## 阶段 0 完成情况

- Python 3.11 + LiteLLM 1.86.0 下五模型报告齐全，硬协议 gate 全部通过；
- Qwen/Kimi 的 vision carrier 顺序均被接受，视觉准确率作为独立 observation；
- 扫描/checkpoint/report 单元测试和当前 SQLite 一致性快照盘点通过；
- 当前快照只有 2 个 semantic task，无阶段 0 规模阻塞；目标环境若数据规模不同，必须在
  cutover 停机扫描后以实际 task 数量重新核对维护窗口；
- generate 的并发默认值、429/5xx 重试参数、mechanical summary 上限及目标环境吞吐测量
  归入阶段 5；阶段 0 已冻结 checkpoint identity、resume、进度/ETA 输出边界；
- 目标 raw vLLM 版本、模型、chat template 仍是阶段 5 部署前 blocker；
- `--keep-maintenance` 已实现，覆盖成功 apply、失败回退和 CLI 参数验证。
