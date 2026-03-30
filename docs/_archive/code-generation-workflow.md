# 代码生成流程设计

> 基于持久化改造（persistence-refactor-plan.md → ha-review-findings.md）的复盘。
> 核心问题：plan 写得很细但全是 happy path，review 发现的问题本应在设计阶段避免。

---

## 问题回顾

这次的实际流程：

```
persistence-refactor-plan.md（很细，但全是 happy path）
    ↓
写代码（照着 plan 写，plan 没提的就随手放）
    ↓
review 发现（fencing 没做、职责错位、reconnect 矛盾……）
    ↓
补文档 ha-review-findings.md
```

Review 发现的问题分类：

| 问题 | 表面原因 | 深层原因 |
|------|---------|---------|
| 路由层做了 lease/stream 管理 | 没有"谁负责执行生命周期"的规定 | 没有定义模块的 ownership 边界 |
| lease 丢失后无 fencing | 只想了 happy path | 没有要求"获取资源 → 必须定义丢失时的行为" |
| auto-deny 和 reconnect 自相矛盾 | 分开做的，没对齐 | 没有要求同一概念的语义一致 |
| 本地文件假装 fallback | 看起来比"什么都不做"好 | 没有"不能保证就别假装"的原则 |
| 绕过 manager 直接调 repo | 能 work，更短 | 规则写了但太具体，新边界出现时防不住 |

关键发现：CLAUDE.md 已经写了 "Routers must not bypass Manager to call Repo directly"，但还是会违反——因为这条规则太具体，只防了一个边界，新的边界（router 直接调 RuntimeStore）防不住。**需要的是高层原则，具体规则从原则推导。**

---

## 目标流程

```
                    ┌─────────────────┐
                    │     需求描述      │
                    └────────┬────────┘
                             ↓
                    ┌─────────────────┐
                    │   Constitution   │  高层原则（短，稳定，跨项目通用）
                    └────────┬────────┘
                             ↓
                    ┌─────────────────┐
                    │   技术总体方案    │  架构、语言、框架、模块划分
                    └────────┬────────┘
                             ↓
                    ┌─────────────────┐
                    │   Module Specs   │  每个模块的契约、保证、不保证、接口
                    └────────┬────────┘
                             ↓
                    ┌─────────────────┐
                    │ Implementation   │  按依赖排序，每步可独立验证
                    │      Plan        │
                    └────────┬────────┘
                             ↓
              ┌──────────────────────────────┐
              │  For each plan step:          │
              │  1. 读 constitution + spec    │
              │  2. 写 spec 级测试（先）       │
              │  3. 写代码                    │
              │  4. 跑单元测试                │
              │  5. 新 session review         │
              │     (通过 → 下一步)            │
              └──────────────────────────────┘
                             ↓
                    ┌─────────────────┐
                    │ Integration Test │  跨模块验证
                    └─────────────────┘
```

---

## 各阶段说明

### Constitution（高层原则）

- 跨模块、跨项目通用的约束，不随功能变化
- 应该很短（一页以内），写一次，偶尔修订
- **在技术方案之前写**，因为原则会影响技术选型
- 示例方向（待细化）：
  - 每个模块只通过上一层的公开接口操作，不穿透
  - 获取的资源必须定义丢失时的行为
  - 同一个概念在系统里只有一种语义
  - 不能保证的事情不要假装做了

### 技术总体方案

- 系统架构（分布式/单体）、语言/框架选型、模块划分
- 这一步产出的是系统的大骨架，不涉及实现细节

### Module Specs（模块契约）

- 每个模块的接口定义 + 不变量 + 失败行为
- 关键是**保证和不保证**都要写，比如：
  ```
  ExecutionRunner
    输入：conversation_id, message_id, coroutine
    保证：同一 conversation 同时只有一个执行
    保证：执行结束后 lease 一定被释放（正常/异常/crash）
    保证：lease 丢失时执行被终止，post-processing 不执行
    不保证：执行中任务跨 Worker 迁移
  ```
- 这次缺的就是这个——persistence-refactor-plan.md 写了"怎么实现"，但没写每个模块的保证/不保证。如果当时写了"lease 丢失时执行被终止"，实现时就不会忘 fencing

### Implementation Plan

- 按模块依赖关系排序
- 每一步可独立验证
- 每一步的范围应区分性质：配置修正、结构重构、语义变更不混在同一步（方便 bisect）

### 实施循环

**Spec 级测试先于代码**：

```python
# 从 spec 推导，不需要看实现
async def test_lease_lost_stops_execution():
    """lease 过期后，执行应被终止，不应写 DB"""

async def test_concurrent_execution_rejected():
    """同一 conversation 并发提交第二个执行，应返回 conflict"""
```

这些测试是 spec 的可执行版本，先写它们强制在写代码前想清楚边界。

实现细节的测试（如 Lua 脚本返回值）不需要先写，跟着代码一起写即可。

**新 session review**：

- 每个 plan step 完成后，拉新 session review
- 新 session 只给 constitution + spec + diff，不给实现背景
- clean context 是 review 的价值所在——写代码的 session 带着"我为什么这么做"的隐含假设，review session 没有这些假设，反而能发现问题
- 这也解释了为什么外部 reviewer 能抓到那么多问题——他没有"跟着 plan 一步步写过来"的上下文包袱
