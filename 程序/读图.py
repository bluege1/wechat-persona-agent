# -*- coding: utf-8 -*-
"""
达妮娅 · 读图（视觉模型）
========================

为什么需要这个
--------------
「收消息」走的是【截图 + OCR】。OCR 只会认字 —— 对方发来一张图片时，
聊天区里就是一块缩略图，一个字符都读不到。于是：

  · read_bubbles() 里根本没有这条消息
  · poll() 也就当「没人说话」，一直不吭声

达妮娅既不知道你发了图，也不会回你。这个文件补上另一条路：

  截图 → 裁出「最后一条消息下面」那块 → 转 base64 → 交给视觉模型 → 读成一段文字

然后这段文字会被包装成「他刚发来一张图片，上面是……」喂给她，
她就真的看懂了，能接话、能吐槽、能顺着聊。

配置见 config.yaml 的 vision 段。用的是 OpenAI 兼容的多模态接口，
硅基流动、通义、OpenAI 都行，换个 base_url / model 就行。

本项目实测（2026-09-20，硅基流动）：**Qwen/Qwen3-VL-32B-Instruct** 最好用 ——
3 秒出结果、说的是完整句子、纯文字截图也能正确回答「只有文字」。
同批测的 Qwen3-VL-30B-A3B 会返回空内容、Qwen3-VL-8B 直接 60 秒超时，
GLM-4.5V 很快但只给「湖泊、山脉、芦苇」这种词表。

⚠ 读不出来时**绝不编**。没配模型、调用失败、额度用完，
一律返回 None，由调用方退回「我看不到图」的自然回应 ——
让达妮娅承认自己看不见，比让她瞎描述一张不存在的图好得多。
"""

import base64
import io
import re
import time
from datetime import date

try:
    import requests
except Exception:                     # pragma: no cover
    requests = None

try:
    from PIL import Image
except Exception:                     # pragma: no cover
    Image = None


# 给视觉模型的提示词。
#
# 这段文字一定要短、要死板 —— 它的输出会被当成聊天内容喂给达妮娅，
# 写得花哨（分点、写作文、加评价）会把她的回复也带成那股 AI 味。
PROMPT = (
    "这是微信聊天窗口左下角的一小块截图，对方发的消息靠左边。\n"
    "只看对方那条消息：\n"
    "· 如果是图片 / 照片 / 截图 / 表情包，用一两句话说出它是什么、上面有什么。"
    "图上有文字就把关键文字照抄下来。不要评价，不要说「这张图展示了」。\n"
    "· 如果对方那条消息是纯文字（这个画面里根本没有图片），"
    "只回答四个字：只有文字\n"
    "不要输出 JSON，不要分点，不要解释你在做什么。"
)

# 视觉模型回答「画面里没有图片」时的识别词。
NO_IMAGE_WORDS = ("只有文字", "没有图片", "无图片", "没有图", "纯文字")


class Vision:
    """把一小块截图交给视觉模型，换回一段人话。"""

    def __init__(self, cfg: dict, llm: dict = None):
        v = (cfg or {}).get("vision") or {}
        if not isinstance(v, dict):
            v = {}
        l = llm or (cfg or {}).get("llm") or {}
        self.enabled = bool(v.get("enabled", True))
        self.model = str(v.get("model") or "").strip()
        # 留空 = 跟说话那个模型用同一套地址和 Key（同厂商时最省事）
        self.base_url = str(v.get("base_url") or "").strip() or str(l.get("base_url") or "")
        self.api_key = str(v.get("api_key") or "").strip() or str(l.get("api_key") or "")
        self.max_tokens = int(v.get("max_tokens", 300) or 300)
        self.max_per_day = int(v.get("max_per_day", 30) or 0)
        self.min_gap = float(v.get("min_gap_seconds", 15) or 0)
        self.max_side = int(v.get("max_side", 1024) or 1024)
        self.timeout = float(v.get("timeout", 60) or 60)
        self.last_error = ""
        self.last_text = ""
        self.last_at = 0.0
        self._day = date.today()
        self._used = 0
        self._last = 0.0

    # ── 配置 / 额度 ────────────────────────────────────────────────

    def configured(self) -> str:
        """能用返回空串；不能用返回一句人话理由。"""
        if not self.enabled:
            return "读图已在 config 里关掉（vision.enabled）"
        if requests is None:
            return "没装 requests（.venv\\Scripts\\python.exe -m pip install requests）"
        if not self.model:
            return "vision.model 没填 —— 说话那个模型（DeepSeek）不会看图，得单独填一个视觉模型"
        if not self.api_key:
            return "没有 api_key（vision.api_key 和 llm.api_key 都是空的）"
        return ""

    def _roll(self):
        if date.today() != self._day:
            self._day = date.today()
            self._used = 0

    def why_not(self) -> str:
        """现在能不能读图；不能就返回理由。方便日志和控制台一眼看出原因。"""
        bad = self.configured()
        if bad:
            return bad
        self._roll()
        if self.max_per_day and self._used >= self.max_per_day:
            return "今天读图次数用完了（vision.max_per_day = %d）" % self.max_per_day
        gap = time.time() - self._last
        if self.min_gap and gap < self.min_gap:
            return "距上次读图才 %.0f 秒，缓一下（vision.min_gap_seconds）" % gap
        return ""

    def stats(self) -> dict:
        self._roll()
        return {
            "enabled": self.enabled,
            "model": self.model,
            "ready": not self.configured(),
            "why_not": self.why_not(),
            "used_today": self._used,
            "max_per_day": self.max_per_day,
            "last_at": self.last_at,
            "last_text": self.last_text,
            "last_error": self.last_error,
        }

    # ── 真正干活 ──────────────────────────────────────────────────

    def _api_url(self) -> str:
        base = (self.base_url or "").strip().rstrip("/")
        if not base:
            return ""
        if not base.endswith("/v1"):
            base += "/v1"
        return base + "/chat/completions"

    def _shrink(self, png_bytes: bytes) -> bytes:
        """太大就缩一下再发 —— 一张 4K 截图直接喂过去又慢又贵。"""
        if Image is None:
            return png_bytes
        try:
            im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        except Exception:
            return png_bytes
        w, h = im.size
        side = max(w, h)
        if side > self.max_side:
            k = self.max_side / float(side)
            im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def describe(self, png_bytes: bytes):
        """
        让视觉模型看一眼这块截图。

        返回：
          一段文字  —— 读出来的内容（可能带换行）
          ""        —— 画面里其实没有图片（只有文字），别用这段结果
          None      —— 调不通；理由放在 self.last_error
        """
        why = self.why_not()
        if why:
            self.last_error = why
            return None
        if not png_bytes:
            self.last_error = "没有图像数据"
            return None

        url = self._api_url()
        if not url:
            self.last_error = "vision.base_url 和 llm.base_url 都是空的"
            return None

        data = self._shrink(png_bytes)
        b64 = base64.b64encode(data).decode()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64," + b64}},
                ],
            }],
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
        }

        # 记一次「用掉了」——不管成没成功都算，不然失败会被反复重试刷屏
        self._used += 1
        self._last = time.time()

        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": "Bearer " + self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except Exception as e:
            self.last_error = "%s（%s）" % (e.__class__.__name__, str(e)[:90])
            return None

        ctype = resp.headers.get("Content-Type", "")
        body = resp.text or ""
        if "application/json" not in ctype:
            self.last_error = "接口返回的不是 JSON（HTTP %s）：%s" % (
                resp.status_code, re.sub(r"<[^>]+>", " ", body)[:120].strip())
            return None

        try:
            d = resp.json()
        except ValueError:
            self.last_error = "JSON 解析失败（HTTP %s）：%s" % (resp.status_code, body[:120])
            return None

        if resp.status_code != 200 or "choices" not in d:
            msg = d.get("message") or d.get("error") or body[:150]
            self.last_error = "HTTP %s：%s" % (resp.status_code, str(msg)[:150])
            return None

        try:
            content = d["choices"][0]["message"]["content"]
        except Exception:
            self.last_error = "返回结构看不懂：%s" % str(d)[:150]
            return None

        # 有些厂商会把内容塞成 [{type:text,text:...}]
        if isinstance(content, list):
            content = " ".join(
                str(x.get("text") or "") if isinstance(x, dict) else str(x)
                for x in content)
        text = re.sub(r"<think[^>]*>.*?</think\s*>", "", str(content or ""),
                      flags=re.S | re.I)
        text = re.sub(r"^\s*[\[（(]?\s*只有文字\s*[\]）)]?\s*$", "只有文字", text.strip())
        text = text.strip().strip('"').strip()

        if not text:
            self.last_error = "模型返回了空内容"
            return None
        if any(w in text[:12] for w in NO_IMAGE_WORDS):
            return ""                      # 画面里没有图片

        self.last_text = text
        self.last_at = time.time()
        self.last_error = ""
        return text


# ══════════════════════════════════════════════════════════════
# 给 bridge / 控制台共用的一个薄封装
# ══════════════════════════════════════════════════════════════

def read_media(vision: "Vision", png_bytes) -> dict:
    """
    读一块「没有文字的东西」。

    返回 dict：
      {"kind": "text"|"none"|"unavailable"|"notimage", "text": str, "why": str}
        text        读出来了，把 text 伪装成她看到的画面喂给她
        notimage    画面里其实只有文字，别用这段结果
        unavailable 读不了（含没配模型 / 失败 / 超额），why 里是理由
    """
    if vision is None:
        return {"kind": "unavailable", "text": "", "why": "没接视觉模型"}
    why = vision.why_not()
    if why:
        return {"kind": "unavailable", "text": "", "why": why}
    out = vision.describe(png_bytes)
    if out is None:
        return {"kind": "unavailable", "text": "", "why": vision.last_error or "读图失败"}
    if out == "":
        return {"kind": "notimage", "text": "", "why": "画面里只有文字"}
    return {"kind": "text", "text": out, "why": ""}


def wrap_for_chat(text: str) -> str:
    """
    把读出来的画面描述包成一句「系统提示」塞给她。

    关键是最后一句：不许提「系统」「识别」这类字眼 ——
    不然她会回一句「我帮你识别了一下」，出戏得很。
    """
    return (
        "[系统提示] 他刚发来一张图片。你看到的就是下面这些东西，"
        "除此之外没有别的了：\n"
        f"{text}\n"
        "请按你的性格自然回应（可以接话、可以吐槽、可以只回一个字）。"
        "绝对不要提「识别」「系统」「图片内容」这类字眼，就当是你自己眼睛看到的。"
    )


# 读不出来的时候给她的兜底提示 —— 承认看不见，但别像报错。
CANT_SEE = (
    "[系统提示] 他刚发来一张图片，但你这边图片加载不出来，只看到一块空白。\n"
    "请用你的语气自然地回应一下，顺带提一句让他把图里的东西打字说给你听。\n"
    "绝对不许猜图里是什么、不许假装看到了。"
)


# ══════════════════════════════════════════════════════════════
# 命令行：单独试一张图
# ══════════════════════════════════════════════════════════════

def _main(argv):
    import sys
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parent
    args = [a for a in argv[1:] if not a.startswith("-")]
    with open(root / "config.yaml", "rb") as f:
        cfg = yaml.safe_load(f) or {}

    v = Vision(cfg, cfg.get("llm"))
    print("模型：%s" % (v.model or "(没填)"))
    print("地址：%s" % (v.base_url or "(没填)"))
    why = v.configured()
    if why:
        print("[X] 不能用：%s" % why)
        return 2
    print("[OK] 配置齐全，今日已读 %d 张" % v._used)
    if not args:
        print("\n用法：python 读图.py 图片.png [更多图片...]")
        print("     什么都不带就只体检配置，不调用接口。")
        return 0

    for p in args:
        f = Path(p)
        if not f.exists():
            print("[X] 找不到 %s" % p)
            continue
        print("\n=== %s（%d 字节）===" % (f.name, f.stat().st_size))
        out = v.describe(f.read_bytes())
        if out is None:
            print("[X] 读失败：%s" % v.last_error)
        elif out == "":
            print("（画面里没有图片，只有文字）")
        else:
            print(out)
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(_main(sys.argv))
