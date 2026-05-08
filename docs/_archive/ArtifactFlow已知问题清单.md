# ArtifactFlow 已知问题清单

> 当前未修复的问题、未做完的功能、跨部署都可能撞上的坑。
> 部署环境特定的问题（CentOS 7 / 老 Docker / 跨架构构建等）见 `intranet`
> 分支上同名文档追加的章节。

## 功能缺失

### `.doc` 上传不支持

- **现状**：`src/utils/doc_converter.py:109` 的扩展名分发只识别 `.docx`（走 pandoc）和 `.pdf`（走 pymupdf），其他扩展名一律 fallback 到 `_convert_text` 用 charset-normalizer 当文本解码。`.doc` 是 Word 97-2003 的 OLE Compound File 二进制格式，charset-normalizer 必然解码失败，前端会弹 `Cannot decode file '...': not a valid text file`。
- **绕过**：用户在 Word / WPS 里把 `.doc` 另存为 `.docx` 后再上传。
- **修法**：`Dockerfile` 加 `antiword`（apt 包，~5MB），`doc_converter.py` 加 `.doc` 分支调 `antiword <filename>` 取纯文本。`antiword` 比 LibreOffice 小一两个数量级，缺点是不保留排版——但用户主要诉求是把内容喂给 LLM，文本就够。
- **优先级**：高。用户随手拖个老 `.doc` 就报错，体验差。

## 工程改进

<!-- 跨架构构建已修：release.sh 默认走 buildx + linux/amd64，PLATFORM 可覆盖。 -->
<!-- 对应改动见 commit b775b29 (build(release): three-tar packaging + amd64 default + portable sha256) -->
