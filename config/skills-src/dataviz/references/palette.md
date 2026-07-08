# 默认调色板

这是一套中性、适合 ArtifactFlow 静态图表的起步 palette。项目或品牌有自己的
design tokens 时，替换这些值后重新跑 validator。

## Surfaces 与文字

| role | light | dark |
|---|---|---|
| chart surface | `#ffffff` | `#111827` |
| page surface | `#f8fafc` | `#030712` |
| primary text | `#111827` | `#f9fafb` |
| secondary text | `#4b5563` | `#d1d5db` |
| muted text | `#6b7280` | `#9ca3af` |
| grid | `#e5e7eb` | `#374151` |
| axis | `#cbd5e1` | `#4b5563` |

## 分类色

按顺序分配，不能循环。超过 7 个重要系列时改结构。

| slot | light | dark |
|---|---|---|
| 1 | `#2563eb` | `#60a5fa` |
| 2 | `#d97706` | `#f59e0b` |
| 3 | `#7c3aed` | `#a78bfa` |
| 4 | `#059669` | `#34d399` |
| 5 | `#dc2626` | `#f87171` |
| 6 | `#0891b2` | `#22d3ee` |
| 7 | `#db2777` | `#f472b6` |

校验示例：

```bash
python3 $SKILL/scripts/validate_palette.py "#2563eb,#d97706,#7c3aed,#059669,#dc2626,#0891b2,#db2777" --mode light
python3 $SKILL/scripts/validate_palette.py "#60a5fa,#f59e0b,#a78bfa,#34d399,#f87171,#22d3ee,#f472b6" --mode dark --surface "#111827"
```

这组顺序面向柱、线、堆叠等“相邻 slot 接触”的图形。散点、气泡、地图、小多图
要跑 `--pairs all`；如果失败，减少系列数、改小多图、加形状/纹理，或只高亮重点系列。

## 连续、有序、发散

- 连续大小：用一个 hue 的浅到深色阶。低值接近 surface，高值更深或更亮。
- 有序类别：也用单 hue 色阶，但最浅一步仍要能看见；用 `--ordinal` 校验。
- 发散：两端选冷/暖或负/正语义，中点用中性灰。中点不要是第三个饱和 hue。

有序蓝色阶示例：

```bash
python3 $SKILL/scripts/validate_palette.py "#60a5fa,#3b82f6,#2563eb,#1d4ed8" --mode light --ordinal
```

## 状态色

| role | light | dark | rule |
|---|---|---|---|
| good | `#16a34a` | `#4ade80` | 只用于明确好状态 |
| warning | `#f59e0b` | `#fbbf24` | 必须配文字或图标 |
| danger | `#dc2626` | `#f87171` | 用于错误、风险、超限 |
| neutral | `#64748b` | `#94a3b8` | 用于未知、暂停、无变化 |

状态色不要拿来当“系列 4”。如果 series 本身就是成功/失败，那它使用状态色；
否则使用分类色。
