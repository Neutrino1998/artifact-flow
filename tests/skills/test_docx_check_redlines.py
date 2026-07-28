import subprocess
import sys
import zipfile
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[2]
    / "config"
    / "skills-src"
    / "docx"
    / "scripts"
    / "check_redlines.py"
)


def _write_docx(path: Path, document_xml: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def test_success_explains_integrity_only_and_rejects_python_docx_text(tmp_path):
    original = tmp_path / "original.docx"
    edited = tmp_path / "edited.docx"
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _write_docx(
        original,
        f'''<w:document xmlns:w="{namespace}">
  <w:body><w:p><w:r><w:t>原标题</w:t></w:r></w:p></w:body>
</w:document>''',
    )
    _write_docx(
        edited,
        f'''<w:document xmlns:w="{namespace}">
  <w:body><w:p>
    <w:del w:author="审阅"><w:r><w:delText>原标题</w:delText></w:r></w:del>
    <w:ins w:author="审阅"><w:r><w:t>新标题</w:t></w:r></w:ins>
  </w:p></w:body>
</w:document>''',
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(original), str(edited), "--author", "审阅"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "未发现静默正文改写" in result.stdout
    assert "仅验证修订完整性" in result.stdout
    assert "不验证修改内容或页面布局" in result.stdout
    assert "Pandoc --track-changes=accept 或 --track-changes=all" in result.stdout
    assert "不要使用 python-docx Paragraph.text/Run.text" in result.stdout
    assert "无需重复运行本检查" in result.stdout
