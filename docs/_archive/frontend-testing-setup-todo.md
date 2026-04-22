# Frontend Testing Setup — TODO

目前 `frontend/package.json` 只有 `dev / build / lint / generate-types`，**没有任何测试基础设施**。整个前端都靠人工打开浏览器 review。后端 compaction 重构那一轮最后前端只能跑 `tsc --noEmit` + `next build` 做类型 / 构建验证，行为正确性没有自动化回归保护。

等价地，在后端 `task #11` 的测试迁移之外缺一个对称的 "frontend task #11"。

## 推荐技术栈

```bash
cd frontend
npm install -D vitest @vitest/ui \
               @testing-library/react \
               @testing-library/jest-dom \
               @testing-library/user-event \
               jsdom
```

配套：
- `vitest.config.ts`：配 jsdom environment、react plugin、`@/` 路径解析复用 tsconfig
- `tsconfig.json`：`types` 里加 `@testing-library/jest-dom`
- `package.json`：加 `"test": "vitest"` + `"test:ui": "vitest --ui"`

## 最该先写的测试（最高 ROI）

按难度升序：

### 1. 纯函数单测

- **`lib/reconstructSegments.ts::reconstructNonAgentBlocks`**
  - 锁住 compaction 回放逻辑：给一组 fake `MessageEventItem[]`（含 compaction_start + compaction_summary 配对 / 单独 inject / 混合顺序），断言重建出的 `NonAgentBlock[]` 字段正确。
  - 特别关注多个 compaction 块配对、failure summary（`error != null`）、孤立的 start 没有 summary。

- **`stores/streamStore.ts::interleaveFlowItems`**
  - 给定 segments + blocks，断言输出顺序。含 position 边界、空输入、纯 agent 无 blocks 等。

### 2. Store action 测

- **`streamStore.updateNonAgentBlock`**：初始状态含一个 running compaction，发 patch 成 done，断言合并正确 + 其他 block 不变。
- **`streamStore.snapshotSegments`**：compaction 块现在进缓存了，验证这个不变量。

### 3. 组件测（React Testing Library）

- **`CompactionFlowBlock` 三态快照**
  - running: 脉动指示 + "compressing Nk tokens…" + 无 chevron
  - done: 绿勾 + 统计字符串 + 可展开出摘要
  - error: 红色 badge + error 文字在 body

- **`FlowBlock` 折叠行为**
  - 有 body + canToggle → 点 header 展开
  - 无 body → 不渲染 chevron，点击无效
  - `defaultExpanded` 初始态正确

- **`InjectFlowBlock` markdown 渲染**：确认注入内容里的 code block 被渲染出来。

### 4. useSSE 集成测

更难，但价值高：
- Mock `EventSource` / fetch 返回一串事件（包含 `compaction_start` → `compaction_summary` 配对）
- 断言 zustand store 里的 `nonAgentBlocks` 最终状态符合预期
- 特别测 compaction_summary 找最近 running 块的逻辑（乱序、多对并发不会发生但要测健壮）

这层要结合 MSW（Mock Service Worker）mock 网络或用自定义 EventSource polyfill。

## 配置示例

`vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    globals: true,
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
});
```

`vitest.setup.ts`:
```ts
import '@testing-library/jest-dom/vitest';
```

## 守得住什么

- Compaction UI 的 "从右往左找最近 running 块配对 summary" 语义被删 → 组件 / 集成测 fail
- `reconstructNonAgentBlocks` 遗漏某个 event type → 纯函数测 fail
- `NonAgentBlock` discriminated union 分支误处理（比如 UI 对 error 态渲染没覆盖）→ 组件测 fail

目前这些都靠人眼 + 运气。

## 关联

这次前端 commit `fe3ef85` 前前后后改了以下关键逻辑，它们都没有回归保护：
- `streamStore.ts`：discriminated union + updateNonAgentBlock action + snapshot 过滤器变化
- `useSSE.ts`：COMPACTION_START / COMPACTION_SUMMARY 两个新事件处理 + 配对逻辑
- `reconstructSegments.ts::reconstructNonAgentBlocks`：两个新事件类型的回放重建
- `FlowBlock.tsx` + `InjectFlowBlock.tsx` + `CompactionFlowBlock.tsx`：新 UI

做这个 task 的时候可以把这几块一次性覆盖完。

## 何时做

不紧急。触发点：
- 未来有人动上述任一模块时，顺手建环境 + 补当前模块的测试
- 或者下次遇到"UI bug 只能手动复现"的场景时，借机把测试栈搭起来
