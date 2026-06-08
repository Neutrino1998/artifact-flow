"""图片处理(Pillow):识图 read 路径的降采样 + data-URI 构造。

与 ``doc_converter`` 的上传校验分开 —— 那是**入站**(存原件),这是**出站**(把图注入
LLM 上下文前的副本)。原始 blob 永不被改,这里只产生一个降采样的注入副本。

CPU 纪律(CLAUDE.md / 2026-05-14 事故):Pillow 是 C 扩展、resize 会解码,**务必由
调用方放 executor**;Pillow 全局 ``Image.MAX_IMAGE_PIXELS`` 解压炸弹闸在 open 时对
超大像素图抛 ``DecompressionBombError`` → 调用方 try 捕获转 loud-fail。
"""

import base64
import io

from PIL import Image


def resize_to_vision_data_uri(data: bytes, max_edge: int) -> str:
    """把图片字节降采样到最长边 ≤ ``max_edge``,返回 ``data:<mime>;base64,<...>``。

    原图已小于上限则原样编码(不放大、不重编码丢质量)。PNG→PNG、JPEG→JPEG(read
    路径只会遇到这两种,上传已挡其它)。**同步**函数,调用方放 executor。
    """
    with Image.open(io.BytesIO(data)) as img:
        fmt = (img.format or "").upper()
        w, h = img.size
        longest = max(w, h)
        if longest <= max_edge:
            out_bytes = data
            mime = "image/jpeg" if fmt == "JPEG" else "image/png"
        else:
            scale = max_edge / longest
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            if fmt == "JPEG":
                img = img.convert("RGB")
                out_fmt, mime = "JPEG", "image/jpeg"
            else:
                out_fmt, mime = "PNG", "image/png"
            buf = io.BytesIO()
            img.resize(new_size, Image.LANCZOS).save(buf, format=out_fmt)
            out_bytes = buf.getvalue()
    return f"data:{mime};base64,{base64.b64encode(out_bytes).decode('ascii')}"
