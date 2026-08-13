# Design System

参考 Claude App 视觉风格：简约、人文、温暖。

---

## 核心原则

1. **留白即呼吸** — 充裕的间距让内容自然呈现，不拥挤
2. **温暖而非冰冷** — 暖色调背景，避免纯白纯黑
3. **内容优先** — 界面退居幕后，用户专注于对话与结果
4. **克制装饰** — 无多余阴影、渐变、动效，一切服务于功能

---

## 色彩

```
背景 (Background)
├── Light: #f5f0e8  (暖米色)
└── Dark:  #1a1915  (暖深色)

容器 (Surface)
├── Light: #ffffff  (纯白卡片)
└── Dark:  #262520  (深灰卡片)

文字 (Text)
├── Primary:   #1a1a1a / #e8e4dc
├── Secondary: #6b6560 / #9b9590
└── Tertiary:  #9b9590 / #6b6560

强调色 (Accent)
└── #c96442  (温暖的赭褐色)
    ├── Hover:  #b5573a
    └── Light BG: #fdf4f0

边框 (Border)
├── Light: #e5ddd3
└── Dark:  #3a3530
```

用色原则：
- 界面 90% 以上为中性暖色调
- 强调色仅用于主要操作按钮和关键链接
- 状态色保持低饱和：成功 `#4a8c6f`、错误 `#c25d4e`、警告 `#c49a3c`

---

## 排版

```
字体: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif
代码: 'SF Mono', 'Fira Code', ui-monospace, monospace

字号
├── 标题:   text-lg (18px)  font-semibold
├── 正文:   text-sm (14px)  font-normal
├── 辅助:   text-xs (12px)  text-secondary
└── 行高:   leading-relaxed (1.625)
```

---

## 间距

基于 4px 网格，偏好宽松：

```
元素内边距:   12–16px
卡片内边距:   20–24px
区块间距:     24–32px
页面边距:     32–48px
```

---

## 圆角

```
按钮/输入框:  8px   (rounded-lg)
卡片/面板:    12px  (rounded-xl)
对话气泡:     16px  (rounded-2xl)
头像/标签:    9999px (rounded-full)
```

---

## 阴影与边框

以细边框代替阴影来区分层级，保持平面感：

```
默认容器:  border 1px solid border-color
悬浮层:    shadow-sm (0 1px 2px rgba(0,0,0,0.05))
弹窗:      shadow-md (0 4px 12px rgba(0,0,0,0.08))
```

---

## 交互

```
过渡时长:     150ms ease-out
悬停反馈:     背景色微变 (opacity 或 略深一级)
按钮按下:     opacity-90
焦点环:       2px solid accent, offset 2px
禁用状态:     opacity-40, cursor-not-allowed
```

避免：弹跳动画、涟漪效果、过度的悬浮阴影。

---

## 深色模式

| Light | Dark | 用途 |
|-------|------|------|
| #f5f0e8 | #1a1915 | 背景 |
| #ffffff | #262520 | 容器 |
| #e5ddd3 | #3a3530 | 边框 |
| #1a1a1a | #e8e4dc | 主文字 |
| #c96442 | #c96442 | 强调色不变 |

深色模式下阴影进一步弱化，依靠边框和微妙的背景色差区分层级。

---

## 无障碍

- 文字对比度 ≥ 4.5:1 (WCAG AA)
- 可点击目标 ≥ 44px
- 图标按钮提供 `aria-label`
- 所有交互支持键盘导航
