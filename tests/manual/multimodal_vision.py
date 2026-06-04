"""
多模态识图验证 — litellm 透传 content-blocks 是否走通

地基 A 的最底层确认:LLM 调用层不用改一行就能吃图片。content 从字符串
扩成块列表(text + image_url)纯粹是调用方的事,astream_with_retry 原样
透传给 acompletion。这个脚本验证三件事:
  1. litellm 接受 OpenAI content-blocks 格式(list of {type,...})
  2. image_url 走 base64 data URI(私有部署/识图回写的真实形态,图来自
     artifact 二进制而非公网,不能用远程 URL)
  3. astream_with_retry 的流式 + usage 路径对多模态消息无碍

图片不外取:用 Pillow 现画一张含大号 "42" 的图,识图结果可自证
(模型读到 42 = 真的看见了,而不只是请求被接受)。

运行方式:
    python -m tests.manual.multimodal_vision                  # 自画 "42" 图
    python -m tests.manual.multimodal_vision path/to/img.png  # 用本地图片
    python -m tests.manual.multimodal_vision img.png qwen3.7-plus-no-thinking
"""

import asyncio
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.llm import astream_with_retry, get_model_info

DEFAULT_MODEL = "qwen3.7-plus"
SECRET = "42"  # 画进自生成图里的可识别内容,用于自证识图真的发生


def make_test_image_b64() -> tuple[str, str]:
    """现画一张含大号 SECRET 的 PNG,返回 (base64, mime)。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 120)
    except Exception:
        font = ImageFont.load_default()
    draw.text((90, 30), SECRET, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), "image/png"


def load_image_b64(path: str) -> tuple[str, str]:
    """读本地图片,返回 (base64, mime)。"""
    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
    return base64.b64encode(data).decode(), mime


async def main():
    args = sys.argv[1:]
    image_path = None
    model = DEFAULT_MODEL

    # 参数:可给 [图片路径] 和/或 [模型名](顺序无关:存在的文件当图片,其余当模型)
    for a in args:
        if Path(a).is_file():
            image_path = a
        else:
            model = a

    if image_path:
        img_b64, mime = load_image_b64(image_path)
        question = "用一句话描述这张图里有什么。"
        print(f"  image:    {image_path} ({mime}, {len(img_b64)} b64 chars)")
    else:
        img_b64, mime = make_test_image_b64()
        question = "这张图里写的是什么?只回答图上的内容,不要解释。"
        print(f"  image:    自生成 (含 '{SECRET}', {mime}, {len(img_b64)} b64 chars)")

    info = get_model_info(model)
    print(f"  model:    {model}  ({info['model_id']})")
    print(f"  question: {question}")
    print("=" * 60)

    # 关键:content 是块列表而非字符串。image_url.url 走 base64 data URI。
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
        ],
    }]

    content = ""
    usage = None
    in_reasoning = False
    try:
        async for chunk in astream_with_retry(messages, model=model):
            if chunk["type"] == "reasoning":
                if not in_reasoning:
                    print("[Reasoning] ", end="", flush=True)
                    in_reasoning = True
                print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "content":
                if in_reasoning:
                    print("\n[Content]  ", end="", flush=True)
                    in_reasoning = False
                print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "usage":
                usage = chunk["token_usage"]
            elif chunk["type"] == "final":
                content = chunk["content"]
        print()
    except Exception as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)

    print("-" * 60)
    print(f"  token_usage: {usage}")
    if not image_path:
        ok = SECRET in content
        print(f"  自证识图:   {'PASS — 模型读到了 ' + SECRET if ok else 'FAIL — 未读到 ' + SECRET}")
        sys.exit(0 if ok else 2)


if __name__ == "__main__":
    asyncio.run(main())
