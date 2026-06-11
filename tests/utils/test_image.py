"""
resize_to_vision_data_uri — 识图 read 路径的降采样 + 解压炸弹防御回归。

覆盖:
- 小图原样编码(不放大、保持尺寸)
- 超 max_edge 的图降采样到最长边 ≤ max_edge
- 超 VISION_IMAGE_MAX_PIXELS 的图在 **解码前**(open 只读头)就 loud-fail —— Pillow
  默认 89–178M 像素段只 warn 不抛,会漏过小文件大像素炸弹,故应用侧显式校验。
"""

import base64
import io

import pytest
from PIL import Image

from config import config
from utils.image import resize_to_vision_data_uri


def _png_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (123, 50, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _decode_data_uri(uri: str) -> bytes:
    return base64.b64decode(uri.split(",", 1)[1])


def test_small_image_encoded_without_resize():
    uri = resize_to_vision_data_uri(_png_bytes(100, 80), max_edge=1568)
    assert uri.startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(_decode_data_uri(uri))) as img:
        assert img.size == (100, 80)  # untouched


def test_oversize_edge_is_downsampled():
    uri = resize_to_vision_data_uri(_png_bytes(3000, 200), max_edge=1568)
    with Image.open(io.BytesIO(_decode_data_uri(uri))) as img:
        assert max(img.size) <= 1568


def test_pixel_bomb_rejected_before_decode(monkeypatch):
    """超像素上限即抛 ValueError(loud-fail),read_artifact 据此改道占位。"""
    monkeypatch.setattr(config, "VISION_IMAGE_MAX_PIXELS", 100)  # 10x10 ok, 20x20(400px) 超
    with pytest.raises(ValueError, match="too large"):
        resize_to_vision_data_uri(_png_bytes(20, 20), max_edge=1568)


def test_small_non_png_jpeg_is_reencoded_to_png():
    """上传翻转后异型图 blob 会流到 read 路径:小尺寸 gif/webp 不得走原样
    passthrough(原字节 + image/png 标签 = MIME 错配的 data-URI),必须重编码 PNG。"""
    buf = io.BytesIO()
    Image.new("P", (40, 30)).save(buf, format="GIF")
    uri = resize_to_vision_data_uri(buf.getvalue(), max_edge=1568)
    assert uri.startswith("data:image/png;base64,")
    decoded = _decode_data_uri(uri)
    with Image.open(io.BytesIO(decoded)) as img:
        assert img.format == "PNG"          # 真 PNG 字节,不是改标签的 GIF
        assert img.size == (40, 30)         # 小图不放大


def test_cmyk_tiff_mode_normalized():
    """PNG 不支持 CMYK:重编码前须归一模式,否则 save 抛错打断识图。"""
    buf = io.BytesIO()
    Image.new("CMYK", (50, 40)).save(buf, format="TIFF")
    uri = resize_to_vision_data_uri(buf.getvalue(), max_edge=1568)
    assert uri.startswith("data:image/png;base64,")
