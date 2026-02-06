# Rams-Inspired Design System

基于 Dieter Rams 设计哲学的 UI/UX 设计规范

---

## 核心原则

Dieter Rams 的设计哲学可以概括为："Less, but better"（少即是多，但要更好）

### 十大设计原则在 UI 中的应用

| 原则 | UI/UX 应用 |
|------|-----------|
| 创新但不花哨 | 用新技术解决实际问题，而非炫技 |
| 实用性优先 | 每个元素都有明确功能，无装饰性废物 |
| 美学克制 | 美来自比例与秩序，不是装饰 |
| 自解释 | 用户无需说明书即可理解如何操作 |
| 不打扰 | 界面退居幕后，让用户专注于任务 |
| 诚实 | 不伪装功能，不夸大能力，拒绝视觉欺骗* |
| 持久 | 避免流行趋势，追求经典 |
| 细节完整 | 每个像素都经过考量 |
| 环保 | 性能优化，减少资源消耗 |
| 极简 | 去除一切不必要的元素 |

*\* 视觉欺骗：不在非交互元素上使用按钮阴影；不让纯展示区域看起来可点击；用户的视觉预期必须与交互结果一致。*

---

## 色彩系统

### 基础色板

```
背景层 (Background)
├── Light: #f4f4f0 (温暖的米白，非纯白)
└── Dark:  #111111 (深黑，非纯黑)

容器层 (Container)
├── Light: #e8e8e5 (略深于背景，模拟塑料/金属质感)
└── Dark:  #222222 (哑光深灰)

文字 (Text)
├── Light: #111111 (高对比度墨黑)
└── Dark:  #eeeeee (柔和的白)
```

### 凹陷区域色（信息展示、输入框）

用于视觉上"低于"容器表面的区域：

```
凹陷区域背景 (Recessed Area)
├── Light: #d8d8d5 (略暗于容器，营造凹陷感)
└── Dark:  #1a1a1a (略暗于容器)

凹陷区域文字
├── Light: #111111
└── Dark:  #eeeeee

/* 可选：复古风格自发光文字（适用于仪表盘、监控面板等） */
├── Phosphor Green: #9eff9e (磷光绿)
└── Amber Glow:     #ffb347 (琥珀色)
```

### 功能色

```
强调色 (Accent)
└── Orange: #ea5b0c (Braun 标志性橙色)
    ├── Hover:  #ff7520
    └── Active: #d15009

主要操作 (Primary)
├── Light: #3a3a3a (深灰)
└── Dark:  #333333

次要操作 (Secondary)
├── Light: #dcdcdc (浅灰)
└── Dark:  #4a4a4a
```

### 状态色

模仿物理设备上的 LED 指示灯，保持极低饱和度：

```
成功/完成 (Success)
└── Green: #4d7a4d (低饱和深绿)
    └── Glow: rgba(77,122,77,0.3) (微发光)

进行中 (Processing)
└── Amber: #8a7a3d (低饱和琥珀)

错误 (Error)
└── Red: #8a4d4d (低饱和深红)
```

状态色使用原则：
- 仅用于小面积指示（圆点、边框、图标）
- 不作为大面积背景色
- 配合微弱发光效果增强"灯"的隐喻

### 色彩使用原则

1. **中性为主** - 90% 的界面使用灰度色
2. **强调色节制** - 橙色仅用于最重要的单一操作（如提交、确认）
3. **温度感** - 避免冷调灰，使用带微黄的暖灰
4. **对比度** - 确保文字与背景对比度 ≥ 4.5:1

---

## 排版系统

### 字体选择

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
```

选择原则：
- 无衬线字体（Sans-serif）
- 高 x-height，提升小字可读性
- 清晰的数字区分（0/O, 1/l/I）

### 字重

```
Light   300  - 辅助信息、标签
Regular 400  - 正文
Medium  500  - 按钮文字、重要信息
SemiBold 600 - 标题、数值显示
```

### 字号层级

```
Display:  text-4xl (36px) - 主要数值/大标题
Heading:  text-xl  (20px) - 区块标题
Body:     text-base(16px) - 正文
Caption:  text-sm  (14px) - 辅助说明
Micro:    text-xs  (12px) - 标签、状态
```

### 数值显示规则

当界面包含数据展示时：

```css
/* 数值区域使用等宽数字，确保垂直对齐 */
.data-value {
  font-family: 'Inter', ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
```

---

## 间距系统

基于 4px 网格：

```
--space-1:  4px   (微间距)
--space-2:  8px   (元素内间距)
--space-3:  12px  (相关元素间)
--space-4:  16px  (标准间距)
--space-6:  24px  (区块内间距)
--space-8:  32px  (区块间间距)
```

### 应用示例

```
按钮内边距:     space-4 (16px)
按钮间距:       space-4 (16px)
卡片内边距:     space-6 (24px)
区块间距:       space-8 (32px)
```

### 层级规则：Margin > Gutter

容器与屏幕边缘的间距（Margin）必须大于容器内部元素间距（Gutter）：

```
屏幕边缘 → 卡片:  space-8 (32px)  ← 外层
卡片 → 内容:      space-6 (24px)  ← 中层
元素 → 元素:      space-4 (16px)  ← 内层
```

这种由外向内递减的间距层级，创造出清晰的视觉包含关系。

---

## 圆角系统

```
--radius-sm:   8px   (按钮、输入框)
--radius-md:   12px  (卡片、弹窗)
--radius-lg:   20px  (大型容器)
--radius-xl:   30px  (设备外壳级别)
--radius-full: 9999px (圆形按钮、标签)
```

Rams 风格倾向于：
- 圆角而非直角（柔和感）
- 一致的圆角半径（秩序感）
- 圆形按钮用于图标操作
- 外壳级容器使用较大圆角（30px），营造"软工业"质感

---

## 阴影与深度系统

### 光源假设

光源来自左上方，阴影投向右下。这是 UI 设计的标准约定。

### 物理语义分类

阴影不是装饰，而是定义**几何形态**：

| 元素类型 | 物理隐喻 | 阴影方向 | 使用场景 |
|---------|---------|---------|---------|
| 按钮、开关 | **凸起 (Convex)** | 外阴影 + 顶部内高光 | 可点击/可操作的控件 |
| 输入框、展示区 | **凹陷 (Concave)** | 内阴影 | 信息展示、用户输入 |
| 卡片、面板 | **体积 (Volume)** | 双向新拟态阴影 | 内容容器、模块边界 |

### 阴影层级代码

```css
/* 按钮/控件 - 凸起状态 (Convex) */
shadow-button: 
  0 2px 4px rgba(0,0,0,0.15),           /* 底部投影 */
  0 1px 0 rgba(255,255,255,0.1) inset;  /* 顶部高光 */

/* 按钮/控件 - 按下状态 (Pressed) */
shadow-pressed: 
  inset 0 2px 4px rgba(0,0,0,0.2);      /* 内陷阴影 */

/* 输入框/展示区 - 凹陷 (Concave) */
shadow-inset:
  inset 0 2px 6px rgba(0,0,0,0.15);     /* 内陷深度 */

/* 卡片/容器 - 体积 (Volume) */
shadow-card: 
  20px 20px 60px #c5c5c2,               /* 右下阴影 */
  -20px -20px 60px #ffffff;             /* 左上高光 */
```

### 深度原则

1. **触觉隐喻** - 按钮应该"看起来可以按下"
2. **状态反馈** - 悬停浮起，按下陷入（配合 `translate-y-[1px]` 位移模拟行程感）
3. **层级清晰** - 最多 3 层深度（背景 → 容器 → 控件）
4. **克制新拟态** - 容器阴影追求"模具接缝感"而非"悬浮感"，模糊值不宜过大

---

## 玻璃反光效果

在需要"覆盖层"质感的区域（如屏幕、面板），叠加微弱渐变模拟真实反光：

```css
/* 玻璃反光层 */
.glass-surface::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.1) 0%,
    transparent 50%
  );
  pointer-events: none;
  border-radius: inherit;
}
```

使用原则：
- 透明度极低（0.1 左右），若隐若现
- 角度 135deg，与光源方向一致
- 适用于展示面板、图片容器、仪表盘等
- 深色模式下可略微降低透明度

---

## 组件规范

### 按钮

```
尺寸
├── 高度: 64px (大) / 48px (中) / 32px (小)
├── 最小宽度: 等于高度（方形/圆形）
└── 内边距: 16px 水平

状态
├── Default:  基础样式
├── Hover:    背景色 +10% 亮度
├── Active:   阴影内陷，下移 1px
├── Disabled: 透明度 50%
└── Focus:    2px 橙色外环

变体
├── Primary:   深灰背景，用于主要操作
├── Secondary: 浅灰背景，用于次要操作
└── Accent:    橙色背景，用于页面唯一核心操作
```

#### 按钮代码示例

```tsx
// Rams 风格主按钮
<button
  className="
    h-12 px-6 rounded-lg
    bg-[#3a3a3a] text-white
    shadow-rams-button
    active:shadow-rams-pressed
    active:translate-y-[1px]
    transition-all duration-75
  "
>
  确认
</button>

// 圆形图标按钮
<button
  className="
    w-12 h-12 rounded-full
    bg-[#dcdcdc] text-[#111]
    shadow-rams-button
    active:shadow-rams-pressed
    active:translate-y-[1px]
    transition-all duration-75
  "
  aria-label="设置"
>
  <SettingsIcon />
</button>
```

### 输入框

```
样式
├── 背景: 凹陷区域色（略暗于容器）
├── 边框: 2px solid，比背景略深
├── 内阴影: inset 0 2px 6px rgba(0,0,0,0.15)
└── 圆角: radius-sm (8px)

交互
├── Focus: 边框变为强调色
└── 禁用: 透明度 50%，无阴影
```

### 信息展示区

```
样式
├── 背景: 凹陷区域色
├── 内阴影: 凹陷效果
├── 圆角: radius-md (12px)
└── 可选: 玻璃反光层

内容
├── 数值右对齐（如适用）
├── 等宽数字（tabular-nums）
└── 动态字号（内容越长字越小，如适用）
```

### 卡片/容器

```
样式
├── 背景: 容器层色彩
├── 圆角: radius-lg 至 radius-xl (20-30px)
├── 边框: 1px solid rgba(255,255,255,0.4) (光泽感)
└── 阴影: 新拟态双向阴影
```

### 开关 (Toggle)

模拟物理滑动开关：

```
结构
├── 轨道 (Track): 凹陷样式，内阴影
├── 滑块 (Thumb): 凸起样式，外阴影
└── 激活状态: 轨道背景变为强调色

交互
├── 过渡: 150ms ease-out
└── 滑块位移: translateX
```

---

## 交互规范

### 过渡动画

机械设备的交互是**脆响的 (snappy)**，不是**漂浮的 (floaty)**：

```css
/* 按钮点击 - 机械即时感 */
transition-duration: 75ms;

/* 悬停状态变化 */
transition-duration: 100ms;

/* 状态切换（如背景色渐变） */
transition-duration: 200ms;

/* 大范围变化（如主题切换、页面过渡） */
transition-duration: 300ms;

/* 统一缓动函数 */
transition-timing-function: ease-out;
```

### 反馈原则

1. **即时性** - 点击后 100ms 内必须有视觉反馈
2. **物理感** - 模拟真实按键的按下/弹起
3. **克制性** - 无弹跳、无涟漪、无过度动画
4. **机械感** - 使用极短的过渡时间（75-100ms）模拟物理开关

### 禁止的交互效果

- ❌ Material Design 的涟漪效果 (Ripple)
- ❌ 弹性动画 (Bounce)
- ❌ 过长的缓动动画（>300ms）
- ❌ 装饰性的加载动画

---

## 深色模式

### 转换原则

| Light | Dark | 说明 |
|-------|------|------|
| #f4f4f0 (米白) | #111111 (深黑) | 背景反转 |
| #e8e8e5 (浅灰) | #222222 (深灰) | 容器反转 |
| #d8d8d5 (凹陷浅) | #1a1a1a (凹陷深) | 凹陷区域反转 |
| #3a3a3a (深灰) | #333333 (深灰) | 主按钮微调 |
| #dcdcdc (浅灰) | #4a4a4a (中灰) | 次按钮反转 |
| #ea5b0c (橙) | #ea5b0c (橙) | 强调色不变 |

### 阴影调整

深色模式下：
- 阴影加深（opacity 增加）
- 高光减弱（白色 inset 透明度降低）
- 整体更"平"但保留层次

### 自发光效果（可选）

深色模式可选择性添加自发光效果，适用于仪表盘、监控面板、数据展示等场景：

```css
/* 深色模式下的自发光文字 */
.dark .glow-text {
  color: #9eff9e;                                /* 磷光绿 */
  text-shadow: 0 0 2px rgba(158, 255, 158, 0.3); /* 微发光 */
}

/* 状态指示灯在深色模式下更明显 */
.dark .status-led {
  box-shadow: 0 0 4px currentColor;
}
```

---

## 图标规范

### 风格定义

Rams 风格的图标应具备**精密仪器**的特征：

```
结构
├── 线框图 (Stroke)，避免填充
├── 等线宽：1.5px (小) / 2px (标准) / 2.5px (大)
├── 几何感：基于圆形、方形、三角形的基本构成
└── 末端圆角：stroke-linecap: round

尺寸
├── 16px - 内联/紧凑场景
├── 20px - 标准按钮内
├── 24px - 独立图标按钮
└── 32px - 空状态/大面积展示
```

### 禁忌

- ❌ 不使用填充色图标
- ❌ 不使用渐变或多色
- ❌ 不使用过于复杂/写实的形状
- ❌ 不使用方形末端（锐利感过强）

### 示例对比

```
✓ 简单圆形 + 竖线 = 电源符号
✗ 复杂的手指点击图形

✓ 等边三角形 = 播放
✗ 带阴影的 3D 播放按钮

✓ 圆角矩形 + 对角线 = 删除/清除
✗ 带拟物垃圾桶盖的垃圾桶图标
```

---

## 无障碍规范

### 基础要求

```
对比度
├── 正文文字与背景: ≥ 4.5:1 (WCAG AA)
├── 大字/粗体与背景: ≥ 3:1
└── 交互元素边界: ≥ 3:1

触摸目标
├── 最小尺寸: 44px × 44px
└── 间距: 确保相邻目标不会误触
```

### 图标按钮

所有仅含图标的按钮必须提供无障碍标签：

```tsx
// ✓ 正确
<button aria-label="删除">
  <TrashIcon />
</button>

// ✗ 错误
<button>
  <TrashIcon />
</button>
```

### 键盘导航

确保所有交互元素可通过键盘访问：

- 使用语义化 HTML（`<button>`、`<a>`、`<input>`）
- 自定义控件需添加正确的 `role` 和 `tabindex`
- 提供可见的焦点状态

### 焦点可见性

```css
/* 键盘焦点环 - 使用强调色 */
:focus-visible {
  outline: 2px solid #ea5b0c;
  outline-offset: 2px;
}

/* 鼠标点击不显示焦点环 */
:focus:not(:focus-visible) {
  outline: none;
}
```

---

## Tailwind 配置参考

```javascript
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        'rams-bg': '#f4f4f0',
        'rams-bg-dark': '#111111',
        'rams-surface': '#e8e8e5',
        'rams-surface-dark': '#222222',
        'rams-recessed': '#d8d8d5',
        'rams-recessed-dark': '#1a1a1a',
        'rams-text': '#111111',
        'rams-text-dark': '#eeeeee',
        'rams-orange': '#ea5b0c',
        'rams-orange-hover': '#ff7520',
        'rams-orange-active': '#d15009',
      },
      boxShadow: {
        'rams-button': '0 2px 4px rgba(0,0,0,0.15), inset 0 1px 0 rgba(255,255,255,0.1)',
        'rams-pressed': 'inset 0 2px 4px rgba(0,0,0,0.2)',
        'rams-inset': 'inset 0 2px 6px rgba(0,0,0,0.15)',
        'rams-card': '20px 20px 60px #c5c5c2, -20px -20px 60px #ffffff',
        'rams-card-dark': '20px 20px 60px #0a0a0a, -20px -20px 60px #1a1a1a',
      },
      borderRadius: {
        'rams-sm': '8px',
        'rams-md': '12px',
        'rams-lg': '20px',
        'rams-xl': '30px',
      },
      transitionDuration: {
        '75': '75ms',
        '100': '100ms',
      },
    },
  },
};
```

---

## 设计检查清单

在设计任何界面时，问自己：

### 极简原则
- [ ] 能否删除这个元素？如果删除后功能不受影响，就删除
- [ ] 这个装饰是否有功能意义？
- [ ] 色彩是否超过 3 种？
- [ ] 强调色是否只用在一处？

### 自解释原则
- [ ] 用户是否能在 3 秒内理解如何操作？
- [ ] 视觉预期是否与交互结果一致？（诚实原则）

### 系统一致性
- [ ] 间距是否遵循 4px 网格？
- [ ] Margin 是否大于 Gutter？（层级规则）
- [ ] 圆角是否全局一致？
- [ ] 图标是否为等线宽线框图？

### 物理隐喻
- [ ] 按钮是否看起来可以按下？（凸起感）
- [ ] 输入/展示区域是否看起来是凹陷的？
- [ ] 按下动画是否足够快？（≤100ms）

### 无障碍
- [ ] 对比度是否达标？（≥4.5:1）
- [ ] 触摸目标是否 ≥ 44px？
- [ ] 图标按钮是否有 aria-label？
- [ ] 键盘导航是否可用？

### 深色模式
- [ ] 深色模式是否测试过？
- [ ] 阴影是否针对深色模式调整？

---

## 参考资源

- [Dieter Rams: 10 Principles of Good Design](https://www.vitsoe.com/us/about/good-design)
- [Braun Design Collection](https://www.braun-design.com)

---

*"Weniger, aber besser" — 少即是多，但要更好*