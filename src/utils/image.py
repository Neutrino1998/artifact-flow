"""图片处理(Pillow):识图 read 路径的降采样 + data-URI 构造。

与 ``doc_converter`` 的上传校验分开 —— 那是**入站**(存原件),这是**出站**(把图注入
LLM 上下文前的副本)。原始 blob 永不被改,这里只产生一个降采样的注入副本。

CPU 纪律(CLAUDE.md / 2026-05-14 事故):Pillow 是 C 扩展、resize 会解码,**务必由
调用方放 executor**;解压炸弹闸不靠 Pillow 默认 ``MAX_IMAGE_PIXELS``(89–178M 段只 warn
不抛),而是在 **解码前** 用 ``img.size`` 显式校验 ``w*h ≤ VISION_IMAGE_MAX_PIXELS`` →
超限抛 ``ValueError``,调用方 try 捕获转 loud-fail。这是上传校验(_probe_image)之外的
第二道防御 —— blob 已落盘后(如配置调小、或历史 blob)read 仍不被超大图打穿。
"""

import base64
import io

from PIL import Image

from config import config

# 识图白名单:只有这两种 MIME 的 blob 走 read_artifact 识图分支(plan A 决策,
# 上传翻转后重申:gif/webp 等异型图照收 blob 但**不**进识图 —— 语义坑太多,
# 动图取首帧/多页 tiff 取首页都是"试试看"启发式;要看就 mount 进沙盒转 PNG
# 再 persist)。read_artifact 契约文案与 inventory 标注同步引用此集合。
VISION_VIEWABLE_MIMES = frozenset({"image/png", "image/jpeg"})


def resize_to_vision_data_uri(data: bytes, max_edge: int) -> str:
    """把图片字节降采样到最长边 ≤ ``max_edge``,返回 ``data:<mime>;base64,<...>``。

    PNG/JPEG 且原图已小于上限 → 原样编码(不放大、不重编码丢质量)。识图分支
    已按 VISION_VIEWABLE_MIMES 限死 png/jpeg,正常不会有其它格式进来;万一进来
    (MIME 错标的历史 blob 等)也一律重编码成 PNG 兜住 —— 绝不把非 png/jpeg
    字节原样塞进 data-URI(MIME 错配),这是第二道防御非主闸。Pillow 解不了的
    在 open 即抛,调用方 loud-fail。**同步**函数,调用方放 executor。
    """
    with Image.open(io.BytesIO(data)) as img:
        fmt = (img.format or "").upper()
        w, h = img.size
        # 解码前的解压炸弹防御(见模块 docstring):超像素即拒,绝不进 resize/decode。
        # 异型图未经上传口的 _probe_image 闸,这道是它们唯一的像素闸。
        if w * h > config.VISION_IMAGE_MAX_PIXELS:
            raise ValueError(
                f"image too large to view: {w}x{h} exceeds "
                f"{config.VISION_IMAGE_MAX_PIXELS} pixel cap"
            )
        longest = max(w, h)
        if longest <= max_edge and fmt in ("PNG", "JPEG"):
            out_bytes = data
            mime = "image/jpeg" if fmt == "JPEG" else "image/png"
        else:
            scale = min(1.0, max_edge / longest)
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            if fmt == "JPEG":
                img = img.convert("RGB")
                out_fmt, mime = "JPEG", "image/jpeg"
            else:
                # PNG 不支持 CMYK/YCbCr 等模式(tiff/异型图可能带),先归一
                if img.mode not in ("1", "L", "LA", "I", "P", "RGB", "RGBA"):
                    img = img.convert("RGBA")
                out_fmt, mime = "PNG", "image/png"
            buf = io.BytesIO()
            img.resize(new_size, Image.LANCZOS).save(buf, format=out_fmt)
            out_bytes = buf.getvalue()
    return f"data:{mime};base64,{base64.b64encode(out_bytes).decode('ascii')}"
