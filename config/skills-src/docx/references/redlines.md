# OOXML 修订(track changes)手术参考

直接编辑 `word/document.xml` 产生修订时按本页规则写标记。做完手术**必跑**
`check_redlines.py`(原文完整性)+ `pack.py`(XML/结构完整性)。

## 通用规则

- 每个修订元素带三个属性:`w:id`(全文档唯一整数,取当前最大值递增)、
  `w:author`(统一用同一个名字,校验脚本按它回滚)、`w:date`(ISO 格式,
  如 `2026-07-03T10:00:00Z`)。
- 只做**增量标注**:原文任何字符都不许静默消失或改写——删除也是"包进 w:del",
  不是"移除文本"。
- **移动内容一律表达为 w:del(原位置)+ w:ins(新位置)两步**,不要使用
  `w:moveFrom`/`w:moveTo` —— `check_redlines.py` 不识别 move 元素,会误报 FAIL。
- `<w:delText>` 与 `<w:t>` 一样,首尾有空格时要加 `xml:space="preserve"`。

## 插入文本

把新增内容的 run 包进 `<w:ins>`:

```xml
<w:ins w:id="101" w:author="审阅" w:date="2026-07-03T10:00:00Z">
  <w:r><w:t>新插入的文字</w:t></w:r>
</w:ins>
```

## 删除文本

把被删内容的 run 包进 `<w:del>`,且 run 里的 `<w:t>` 改名为 `<w:delText>`:

```xml
<w:del w:id="102" w:author="审阅" w:date="2026-07-03T10:00:00Z">
  <w:r><w:delText>被删除的文字</w:delText></w:r>
</w:del>
```

## 替换 = 删 + 插

先 `<w:del>`(旧文字)紧跟 `<w:ins>`(新文字),两个标记相邻放置。

## 只改 run 的一部分

先把原 run 拆成多个 run(逐个复制原 `<w:rPr>`,保持格式),再对需要改动的
那段套删/插标记。拆分本身不是修订,不需要任何标记。

## 删除整个段落

除了正文包 `<w:del>`,**段落标记本身**也要标删——在该段 `<w:pPr>` 里加:

```xml
<w:pPr>
  <w:rPr>
    <w:del w:id="103" w:author="审阅" w:date="2026-07-03T10:00:00Z"/>
  </w:rPr>
</w:pPr>
```

没有这一步,接受修订后会留下一个空段落。新插入整段则同位置用 `<w:ins/>`
(段落标记插入)+ 正文包 `<w:ins>`。

## 对他人修订的再修订

- 拒绝他人的插入:把 `<w:del>`(你的名字)嵌进对方的 `<w:ins>` 里,内层
  `<w:t>` 改 `<w:delText>`。
- 恢复他人删除的内容:在对方 `<w:del>` 之后追加你的 `<w:ins>`,内容为被删文字。
- 不要直接改动或移除他人的修订元素——那等于替对方做了接受/拒绝。

## 批注

用 [scripts/add_comment.py](../scripts/add_comment.py),不要手写——批注涉及
comments.xml、rels、Content_Types 三处样板,脚本一次做对。

## 事后处置

- 接受/拒绝(全部或按作者):[scripts/accept_changes.py](../scripts/accept_changes.py)。
- 快速人查:`pandoc 文件.docx --track-changes=all -t markdown` 看标注是否符合预期。
