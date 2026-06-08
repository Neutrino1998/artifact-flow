"""
多模态识图验证 — litellm 透传 content-blocks 是否走通

地基 A 的最底层确认:LLM 调用层不用改一行就能吃图片。content 从字符串
扩成块列表(text + image_url)纯粹是调用方的事,astream_with_retry 原样
透传给 acompletion。这个脚本验证:
  1. litellm 接受 OpenAI content-blocks 格式(list of {type,...})
  2. image_url 走 base64 data URI(私有部署/识图回写的真实形态,图来自
     artifact 二进制而非公网,不能用远程 URL)
  3. astream_with_retry 的流式 + usage 路径对多模态消息无碍

图片不外取:用 Pillow 现画含大号数字的图,识图结果可自证(模型读到正确
数字 = 真的看见了,而不只是请求被接受)。

三个模式:
  single      — 单轮单图,自证读出 "42"(默认)
  multiturn   — 多轮历史:每轮塞一张不同数字的图(11/22/33),终轮问最早
                那张是几,验证模型能在多轮历史里区分/引用过去某一轮的图
  multiimage  — 单条消息并排塞 3 张图(11/22/33),验证一条 content 内多图
                能按顺序区分

注:multiturn/multiimage 直叫 astream_with_retry,绕过真实引擎的
MessageEvent→history 复原链路 —— 验证的是 litellm 透传层,不是复原层。

运行方式:
    python -m tests.manual.multimodal_vision                  # single,自画 "42"
    python -m tests.manual.multimodal_vision path/to/img.png  # single,本地图片
    python -m tests.manual.multimodal_vision multiturn        # 多轮历史区分
    python -m tests.manual.multimodal_vision multiimage       # 单消息多图
    python -m tests.manual.multimodal_vision multiturn qwen3.7-plus-no-thinking
"""

import asyncio
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.llm import astream_with_retry, get_model_info

DEFAULT_MODEL = "qwen3.7-plus"
SECRET = "42"  # single 模式画进自生成图里的可识别内容,用于自证识图真的发生
MODES = {"single", "multiturn", "multiimage"}


def make_number_image_b64(text: str) -> tuple[str, str]:
    """现画一张含大号 text 的 PNG,返回 (base64, mime)。文字居中。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 120)
    except Exception:
        font = ImageFont.load_default()
    try:
        x = (img.width - draw.textlength(text, font=font)) / 2
    except Exception:
        x = 90
    draw.text((x, 30), text, fill="black", font=font)
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


def image_block(b64: str, mime: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


async def stream_once(messages: list, model: str, *, label: str = "") -> tuple[str, dict | None]:
    """跑一次调用,边流边打印 reasoning/content,返回 (final_content, usage)。"""
    if label:
        print(f"\n>>> {label}")
    content = ""
    usage = None
    in_reasoning = False
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
    return content, usage


async def run_single(model: str, image_path: str | None) -> int:
    """单轮单图。自生成图时自证读出 SECRET;本地图仅描述、不自证。"""
    if image_path:
        img_b64, mime = load_image_b64(image_path)
        question = "用一句话描述这张图里有什么。"
        print(f"  image:    {image_path} ({mime}, {len(img_b64)} b64 chars)")
    else:
        img_b64, mime = make_number_image_b64(SECRET)
        question = "这张图里写的是什么?只回答图上的内容,不要解释。"
        print(f"  image:    自生成 (含 '{SECRET}', {mime}, {len(img_b64)} b64 chars)")
    print(f"  question: {question}")
    print("=" * 60)

    # 关键:content 是块列表而非字符串。image_url.url 走 base64 data URI。
    messages = [{"role": "user", "content": [
        {"type": "text", "text": question},
        image_block(img_b64, mime),
    ]}]
    content, usage = await stream_once(messages, model)

    print("-" * 60)
    print(f"  token_usage: {usage}")
    if image_path:
        return 0
    ok = SECRET in content
    print(f"  自证识图:   {'PASS — 模型读到了 ' + SECRET if ok else 'FAIL — 未读到 ' + SECRET}")
    return 0 if ok else 2


async def run_multiturn(model: str) -> int:
    """多轮历史:逐轮塞不同数字的图并累积真实 assistant 回复,终轮问最早那张。

    历史里保留过去每轮的 image_url 块 —— 这正是被测点:模型要在含多张
    历史图片的对话里,按序号定位/区分到正确的那一张。
    """
    nums = ["11", "22", "33"]
    messages: list = []
    print(f"  数字序列: {' → '.join(nums)}(每轮一张)")
    print("=" * 60)
    for i, n in enumerate(nums, 1):
        b64, mime = make_number_image_b64(n)
        messages.append({"role": "user", "content": [
            {"type": "text", "text": f"这是第 {i} 张图,请记住它上面的数字。只回答数字。"},
            image_block(b64, mime),
        ]})
        content, _ = await stream_once(messages, model, label=f"第 {i} 轮 (实际图: {n})")
        messages.append({"role": "assistant", "content": content})

    # 终轮:不带图,纯文字引用最早(最难,最远、最易被近因覆盖)那一张。
    target_idx, target = 1, nums[0]
    messages.append({"role": "user", "content": [
        {"type": "text", "text": f"第 {target_idx} 张图里的数字是什么?只回答数字。"},
    ]})
    content, usage = await stream_once(messages, model, label=f"终轮提问 (应答: {target})")

    print("-" * 60)
    print(f"  token_usage: {usage}")
    ok = target in content
    # 区分性的反证:若把别的轮的数字也答出来则视为没真正区分
    others = [n for n in nums[1:] if n in content]
    note = "" if not others else f"(注意:回答里还出现了 {','.join(others)},可能未真正区分)"
    print(f"  自证多轮区分: {'PASS — 正确引用第 1 张图 ' + target if ok else 'FAIL — 未答出 ' + target} {note}")
    return 0 if ok else 2


async def run_multiimage(model: str) -> int:
    """单条消息并排塞 3 张图,验证一条 content 内多图能按顺序区分。"""
    nums = ["11", "22", "33"]
    print(f"  数字序列: {', '.join(nums)}(同一条消息)")
    print("=" * 60)
    blocks: list = [{"type": "text", "text":
                     "这条消息里有 3 张图,按出现顺序,它们上面的数字分别是什么?"
                     "只按顺序回答数字,用逗号分隔。"}]
    for n in nums:
        b64, mime = make_number_image_b64(n)
        blocks.append(image_block(b64, mime))
    messages = [{"role": "user", "content": blocks}]
    content, usage = await stream_once(messages, model, label="单消息 3 图")

    print("-" * 60)
    print(f"  token_usage: {usage}")
    missing = [n for n in nums if n not in content]
    ok = not missing
    ordered = ok and content.find("11") < content.find("22") < content.find("33")
    if ok:
        verdict = "PASS — 三个数字都识别" + (" 且顺序正确" if ordered else " 但顺序乱/无法判定")
    else:
        verdict = "FAIL — 缺数字 " + ",".join(missing)
    print(f"  自证单消息多图: {verdict}")
    return 0 if ok else 2


async def main():
    args = sys.argv[1:]
    mode = "single"
    image_path = None
    model = DEFAULT_MODEL

    # 参数顺序无关:已知模式关键字→mode,存在的文件→图片,其余→模型名。
    for a in args:
        if a in MODES:
            mode = a
        elif Path(a).is_file():
            image_path = a
        else:
            model = a

    info = get_model_info(model)
    print(f"  mode:     {mode}")
    print(f"  model:    {model}  ({info['model_id']})")

    try:
        if mode == "multiturn":
            code = await run_multiturn(model)
        elif mode == "multiimage":
            code = await run_multiimage(model)
        else:
            code = await run_single(model, image_path)
    except Exception as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    asyncio.run(main())
