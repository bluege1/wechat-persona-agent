# -*- coding: utf-8 -*-
"""
达妮娅 · 收消息（截图 + OCR）
==============================

为什么不用控件树？
------------------
微信 PC 从 4.0 起换成了 Qt Quick 自绘 UI，整个界面画在一张画布上，
默认不向 Windows 无障碍接口（UIAutomation）暴露控件。
社区实测：**微信 4.1 起连「开讲述人再重登」这个老办法也失效了**——
它只对历史上成功进过无障碍模式的老账号有效，从没用过的号怎么折腾都读不到。

所以「收消息」这条路改走 OCR：

  1. PrintWindow(flag=2) 后台抓窗口位图 —— **窗口被别的窗口挡住也能抓**，不用切前台
  2. 自动测出三条基准线：标题栏底边、输入框上边、聊天区左边界
  3. RapidOCR 只识别聊天区那一块
  4. 按「离左边近还是离右边近」判断消息是谁发的，取最下面那条对方消息

发消息不受影响（走 Ctrl+F + 剪贴板 + Enter），那条链路跟控件树无关。

依赖：Pillow、rapidocr-onnxruntime
"""

import ctypes
import re
import sys
import time
from ctypes import wintypes
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

try:
    import numpy as np
except Exception:
    np = None

try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
except Exception:
    pass


# ══════════════════════════════════════════════════════════════
# 一、抓图
# ══════════════════════════════════════════════════════════════

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_P = ctypes.c_void_p

# 64 位下句柄是 8 字节，不显式声明会 OverflowError
_user32.GetWindowDC.restype = _P
_user32.GetWindowDC.argtypes = [_P]
_user32.ReleaseDC.argtypes = [_P, _P]
_user32.PrintWindow.argtypes = [_P, _P, ctypes.c_uint]
_gdi32.CreateCompatibleDC.restype = _P
_gdi32.CreateCompatibleDC.argtypes = [_P]
_gdi32.CreateCompatibleBitmap.restype = _P
_gdi32.CreateCompatibleBitmap.argtypes = [_P, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = _P
_gdi32.SelectObject.argtypes = [_P, _P]
_gdi32.DeleteObject.argtypes = [_P]
_gdi32.DeleteDC.argtypes = [_P]
_gdi32.GetDIBits.argtypes = [_P, _P, ctypes.c_uint, ctypes.c_uint,
                            _P, ctypes.c_void_p, ctypes.c_uint]

# PW_RENDERFULLCONTENT：让 GPU 自绘的窗口也把内容画出来，关键就是它
PW_RENDERFULLCONTENT = 2


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def capture_window(win):
    """
    抓微信窗口画面，返回 PIL.Image；失败返回 None。

    用 PrintWindow(PW_RENDERFULLCONTENT)：微信是 Qt/OpenGL 自绘窗口，
    普通的 PrintWindow 会抓到全黑，必须带这个 flag。
    好处是窗口被挡住也照样能抓，用户不用管微信窗口在不在最前面。
    """
    try:
        hwnd = win.NativeWindowHandle
        r = win.BoundingRectangle
        left, top, right, bottom = int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        return None

    w, h = right - left, bottom - top
    if w <= 0 or h <= 0 or w > 10000 or h > 10000:
        return None

    hp = _P(hwnd)
    hdc_win = _user32.GetWindowDC(hp)
    if not hdc_win:
        return None
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_win)
    hbmp = _gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = _gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = _user32.PrintWindow(hp, hdc_mem, PW_RENDERFULLCONTENT)

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h        # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0    # BI_RGB

        buf = ctypes.create_string_buffer(w * h * 4)
        got = _gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
        if not got:
            return None
        img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
        img = img.convert("RGB")
        # 全黑说明 PrintWindow 没拿到内容（窗口被最小化等）
        if ok and _is_blank(img):
            return None
        return img
    except Exception:
        return None
    finally:
        _gdi32.SelectObject(hdc_mem, old)
        _gdi32.DeleteObject(hbmp)
        _gdi32.DeleteDC(hdc_mem)
        _user32.ReleaseDC(hp, hdc_win)


def _is_blank(img) -> bool:
    small = img.convert("L").resize((40, 30))
    px = list(small.tobytes())
    mean = sum(px) / len(px)
    var = sum((p - mean) ** 2 for p in px) / len(px)
    return var < 3


# ══════════════════════════════════════════════════════════════
# 二、测界面几何
# ══════════════════════════════════════════════════════════════

class Geometry:
    """聊天区在窗口图里的位置。"""

    def __init__(self, left, top, right, bottom, how=""):
        self.left = int(left)
        self.top = int(top)
        self.right = int(right)
        self.bottom = int(bottom)
        self.how = how

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def __repr__(self):
        return (f"Geometry(聊天区 x[{self.left},{self.right}] y[{self.top},{self.bottom}]"
                f" {self.width}x{self.height} · {self.how})")


def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _find_hline(img, x0, x1, y_from, y_to):
    """找出横贯 [x0,x1] 的浅灰分隔线所在的行号（整行颜色均匀且偏浅灰）。"""
    px = img.load()
    hits = []
    step = max(1, (x1 - x0) // 60)
    for y in range(max(1, y_from), min(img.size[1], y_to)):
        vals = [_lum(px[x, y]) for x in range(x0, x1, step)]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        if var < 5.0 and 195 < mean < 249:
            hits.append(y)
    return hits


def _find_chat_left(img, top, bottom, W, fallback):
    """
    找聊天区左边界（会话列表和聊天区之间那道亮度台阶）。

    为什么不用「连续 N 列偏白」的老办法：
      窗口窄的时候，对方的头像紧贴着聊天区左边缘，会把那串连续偏白打断，
      边界于是被误判到头像右边（实测 896 宽的窗口：真实 306，被判成 430）。
      而「这条消息谁发的」正是拿这个边界当基准算的 —— 边界一偏，
      长消息就会被认成「对方发的」，达妮娅就会自己回自己。

    改用「列中位数」：
      会话列表的底色（实测 230）和聊天区底色（实测 250）差得很明显，
      而头像、气泡这类小面积色块在某一列里只占几十个像素，
      相对几百行的高度，中位数仍然是背景色 —— 也就是说中位数对它免疫。
    """
    if np is None:
        return fallback

    try:
        g = np.asarray(img.convert("L"), dtype=np.uint8)
        y0, y1 = max(0, top + 8), min(img.size[1], bottom - 8)
        if y1 - y0 < 50:
            return fallback
        col = np.median(g[y0:y1, :].astype(np.float32), axis=0)

        lo, hi = int(W * 0.04), int(W * 0.75)
        for x in range(lo, hi):
            if col[x] < 246:
                continue
            if x + 30 > W or col[x:x + 30].min() < 246:
                continue                      # 要求后面有一段连续稳定的亮区
            if col[max(0, x - 6):x].max() >= 246:
                continue                      # 前面必须是明显的暗区（会话列表）
            return int(x)
    except Exception:
        pass
    return fallback


def detect_geometry(img) -> Geometry:
    """
    自动测出聊天区。测不出来就用实测出来的经验比例兜底。

    实测（1100x707 窗口）：
      左侧图标列 0~68 | 会话列表 68~254 | 聊天区 254~1088
      标题栏 32~80    | 消息区 80~550  | 输入区 550~695

    注意：左侧面板宽度基本是固定的，**不随窗口宽按比例缩放**，
    所以下面的比例只当兜底，真正的边界靠亮度台阶测。
    """
    W, H = img.size
    left = int(min(300, W * 0.36))     # 兜底：左侧面板约 300px 固定宽
    right = int(W * 0.989)
    top = int(H * 0.113)
    bottom = int(H * 0.778)
    how = "比例兜底"

    try:
        # 聊天区中段横向采样，避开左右边缘的滚动条/头像
        sx0, sx1 = int(W * 0.40), int(W * 0.92)

        # 标题栏底边：上半部分里最靠下的一条横线
        cand = _find_hline(img, sx0, sx1, int(H * 0.04), int(H * 0.35))
        if cand:
            top = cand[-1] + 1
            how = "自动"

        # 输入区上边：下半部分里最靠上的一条横线
        cand = _find_hline(img, sx0, sx1, int(H * 0.50), int(H * 0.95))
        if cand:
            bottom = cand[0] - 1

        left = _find_chat_left(img, top, bottom, W, left)
    except Exception:
        pass

    left = max(0, min(left, W - 100))
    right = max(left + 100, min(right, W))
    top = max(0, min(top, H - 100))
    bottom = max(top + 80, min(bottom, H))
    return Geometry(left, top, right, bottom, how)


# ══════════════════════════════════════════════════════════════
# 三、OCR
# ══════════════════════════════════════════════════════════════

_ENGINE = None


def engine():
    """RapidOCR 单例（首次加载约 1 秒）。"""
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


class Bubble:
    """一条消息气泡。"""

    __slots__ = ("text", "x0", "x1", "y0", "y1", "mine")

    def __init__(self, text, x0, x1, y0, y1, mine):
        self.text = text
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        self.mine = mine

    def __repr__(self):
        who = "我" if self.mine else "对方"
        return f"<{who} y={self.y0}-{self.y1} x={self.x0}-{self.x1} {self.text[:24]!r}>"


# 这些不是「别人说的话」
NOISE_PATTERNS = [
    re.compile(r"^\[?语音\]?$"),
    re.compile(r"^\d+'?\d*\"?$"),
    re.compile(r"^\d+\s*秒$"),
    re.compile(r"^\[(图片|视频|文件|动画表情|链接|位置|转账|红包|微信红包)\]$"),
    re.compile(r"^\d{1,2}:\d{2}$"),
    re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日"),
    re.compile(r"^\d{1,2}月\d{1,2}日(\s*\d{1,2}:\d{2})?$"),          # 9月20日 / 9月20日 19:41
    re.compile(r"^(昨天|前天|今天|星期[一二三四五六日天]|周[一二三四五六日天])"
               r"(\s*\d{1,2}:\d{2})?$"),                              # 昨天 / 昨天 19:41
    re.compile(r"^(上午|下午|凌晨|晚上|中午)\s*\d{1,2}:\d{2}$"),
    re.compile(r"^以下是新消息$"),
    re.compile(r"^以下为新消息$"),
    re.compile(r"^\d+\s*条新消息$"),        # 右上角那个悬浮角标，不是谁说的话
    re.compile(r"^查看更[多早]消息$"),      # 中间那条「查看更多消息」分隔条
    re.compile(r"^收起$"),
    re.compile(r"^对方正在输入"),
    re.compile(r"^(你|我)?撤回了一条消息$"),
    re.compile(r".*撤回了一条消息$"),
]


# 时间/日期的「骨架」——把这几样挖掉之后剩不下什么，才算是时间戳。
_STAMP_TOKENS = [
    re.compile(r"\d{1,2}:\d{2}(:\d{2})?"),        # 19:41 / 19:41:07
    re.compile(r"(\d{4}年)?\d{1,2}月\d{1,2}日"),  # 9月20日 / 2026年9月20日
    re.compile(r"昨天|前天|今天"),
    re.compile(r"星期[一二三四五六日天]|周[一二三四五六日天]"),
    re.compile(r"上午|下午|凌晨|晚上|中午"),
]


def looks_like_stamp(text: str) -> bool:
    """
    这条看着像【居中显示的时间戳 / 日期分隔条】吗。

    ★ 为什么要有这个函数（这里踩过大坑）：
      以前 read_bubbles 里的判据是「位置靠近正中 + 字数不超过 26 就丢」，
      想丢的是 `19:41` 这种居中的时间戳。可【对方发来的消息是居中的吗】——
      不是，它贴左边；但一条【长消息】照样能横跨屏幕中线！
      用户实测：`你们那边不是那个啥，赫利俄斯吗，它全天候亮着的吗`（24 字）
      中心离中线只有 28px，于是被当成时间戳【静悄悄扔掉】，
      她既没回这条，探针又把它捡去当图片，最后回了一句「我看不到图」。
      一条正常消息就这么两头落空。

    ➜ 所以判据不能是「在中间 + 短」，只能是「它长得就像时间戳」。
    """
    t = re.sub(r"\s+", "", text or "")
    if not t or len(t) > 20:
        return False
    hit = False
    for p in _STAMP_TOKENS:
        new = p.sub("", t)
        if new != t:
            hit = True
        t = new
    if not hit:
        return False
    # 剩下的一点标点/分隔符不算「内容」（OCR 常在时间戳后面带个点或竖线）；
    # 但只要剩下一个【实词】就不是时间戳了 —— `我10:30到` 里剩下「我到」，不能丢。
    return re.sub(r"[-–—.,，。、;；:：|/\\()（）\[\]【】{}~～!！?？\"'“”‘’]", "", t) == ""


def is_noise(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return any(p.match(t) for p in NOISE_PATTERNS)


# ── 语音条 ──
# 微信把语音条画成 `5"` 或 `[语音] 5"`；如果开了
# 「聊天中的语音消息自动转成文字」，气泡里还会跟着转出来的文字，
# OCR 拿到的就是 `5" 你好呀，你今天在干什么？` 这种样子。
#
# 注意时长后面那个引号是必须的：不能只看到数字就当成语音，
# 否则 `11:42` 这种时间戳会被剥成 `:42`。
_VOICE_LEAD = re.compile(r"^(?:\[?\s*语音\s*\]?[\s·]*)?"
                         r"\d+['′]?\d*\"[\s·]*")
_VOICE_ONLY = re.compile(r"^(?:\[?\s*语音\s*\]?[\s·]*)?"
                         r"\d+['′]?\d*\"\s*$")


def looks_like_voice(text: str) -> bool:
    """
    这条消息是不是一条语音条。

    三种形态都算：
      `[语音] 5"`            没开转文字
      `5"`                   没开转文字（有的版本不带方括号）
      `5" 转写出来的文字`     开了「语音消息自动转成文字」
    """
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("[语音]"):
        return True
    return bool(_VOICE_ONLY.match(t) or _VOICE_LEAD.match(t))


def strip_voice_prefix(text: str) -> str:
    """
    剥掉 `[语音] 5"` / `5"` 这个前缀，留下后面转文字的内容（没有就返回空串）。

    所以「对方发语音」有两种结局：
      · 开了自动转文字 → 这里能剥出文字，达妮娅直接读文字
      · 没开          → 剥出来是空的，达妮娅只知道「来了一条语音」
    """
    t = (text or "").strip()
    if not looks_like_voice(t):
        return t              # 不是语音条就原样返回，绝不乱剥
    return _VOICE_LEAD.sub("", t).strip()


def ocr_crop(img):
    """对整张窗口图跑一次 OCR，返回原始结果。"""
    import numpy as np
    arr = np.array(img)
    result, _ = engine()(arr)
    return result or []


def read_bubbles(img, geo: Geometry, min_score: float = 0.55):
    """
    识别聊天区里的消息，返回按从上到下排序的 Bubble 列表。

    判断是谁发的：比较「左边距」和「右边距」——离哪边近就是谁。
    对方的靠左，自己的靠右。
    """
    import numpy as np

    region = img.crop((geo.left, geo.top, geo.right, geo.bottom))
    rw, rh = region.size
    if rw < 40 or rh < 40:
        return []

    result, _ = engine()(np.array(region))
    if not result:
        return []

    rows = []
    for box, text, score in result:
        text = (text or "").strip()
        if not text or score < min_score:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        rows.append([min(xs), min(ys), max(xs), max(ys), text])

    rows.sort(key=lambda r: (r[1], r[0]))
    if not rows:
        return []

    # 同一气泡可能被拆成多行：纵向挨得近且左边界接近的，合并成一条
    merged = []
    for x0, y0, x1, y1, text in rows:
        if merged:
            p = merged[-1]
            if (y0 - p[3]) < 34 and abs(x0 - p[0]) < 32:
                p[4] = (p[4] + " " + text).strip()
                p[1] = min(p[1], y0)
                p[2] = max(p[2], x1)
                p[3] = max(p[3], y1)
                continue
        merged.append([x0, y0, x1, y1, text])

    # 居中显示的时间戳 / 系统提示：左右边距都很大
    mid = rw / 2.0
    bubbles = []
    for x0, y0, x1, y1, text in merged:
        left_gap, right_gap = x0, rw - x1
        center = (x0 + x1) / 2.0
        # ★ 这里必须【同时】满足「在中间」和「长得像时间戳」。
        #   以前只写了「在中间 + 不超过 26 字就丢」，结果对方发来的长消息
        #   一样横跨中线，被静悄悄扔掉（用户实测：24 字的一条没被回，
        #   还被探针捡去当了图片）。判据见 looks_like_stamp 的注释。
        if abs(center - mid) < 70 and looks_like_stamp(text):
            continue
        if left_gap < 25 and right_gap < 25:
            continue
        mine = _who_sent(region, x0, x1, y0, y1, rw)
        bubbles.append(Bubble(text, x0, x1, y0, y1, mine=mine))

    return bubbles


# 头像抽样区（相对聊天区左边界的像素偏移）。头像尺寸基本固定，
# 所以这里用固定像素而不是按宽度取比例。
_AVA_L = (14, 62)          # 对方头像：贴着聊天区左边缘
_AVA_R_PAD = (14, 62)      # 自己头像：贴着聊天区右边缘（从右边往左量）


def _who_sent(region, x0, x1, y0, y1, rw) -> bool:
    """
    判断这条消息是自己发的还是对方发的。返回 True = 自己发的。

    主判据用「头像」而不是「左右边距」：
      对方的头像固定贴在聊天区左边，自己的固定贴在右边。
      在气泡所在的那几行里量一下左右两侧头像区的「墨量」（偏离背景色的像素比例），
      哪边有头像就是哪边发的。实测分离度极高：

        对方消息  左 0.81  右 0.00
        自己的    左 0.00  右 0.47~0.69

      而「比左右边距」对长消息不靠谱 —— 换行后文本左边界会一直往左顶，
      左边距可能比右边距还小，于是自己的长消息被误判成对方发的，
      达妮娅就会自己回自己。所以边距只留作兜底。
    """
    if np is None or rw < 140:
        return (rw - x1) < x0

    try:
        lum = np.asarray(region.convert("L"), dtype=np.float32)
        h, w = lum.shape
        yy0, yy1 = max(0, int(y0)), min(h, int(y1))
        if yy1 - yy0 < 5:
            return (rw - x1) < x0

        bg = float(np.median(lum))

        def ink(a: int, b: int) -> float:
            a, b = max(0, a), min(w, b)
            if b - a < 4:
                return 0.0
            blk = lum[yy0:yy1, a:b]
            return float((np.abs(blk - bg) > 18).mean())

        left_ink = ink(*_AVA_L)
        right_ink = ink(w - _AVA_R_PAD[1], w - _AVA_R_PAD[0])

        if left_ink > 0.15 and right_ink < 0.10:
            return False
        if right_ink > 0.15 and left_ink < 0.10:
            return True
    except Exception:
        pass

    return (rw - x1) < x0          # 兜底：离哪边近就是谁


# ══════════════════════════════════════════════════════════════
# 三点五、「没有文字的消息」——图片 / 表情包
#
# OCR 只会认字。对方发来一张图片时，聊天区里就是一块缩略图，
# 一个字符都读不到 —— read_bubbles() 里根本没有这条消息，
# poll() 于是当「没人说话」，达妮娅既不吭声也不知道你发了图。
#
# 所以这里另找一条判据：看「最后一条消息下面」那块地方。
#   1. 有「墨」（不是纯背景色）          -> 底下多出东西了
#   2. 连着两次轮询长相一致（稳住了）     -> 是静态图，不是界面在动
#   3. 跟「上次已经读过的那张」不一样     -> 是新的一张，不是同一张
#
# 只看左边那一块：图片气泡是对方发的，贴左边；
# 右边是我们自己发的，不该触发。
# ══════════════════════════════════════════════════════════════

MEDIA_WIDTH_RATIO = 0.66      # 探针只取聊天区左边这么宽（对方消息在这一侧）
MEDIA_MIN_INK = 0.012         # 低于这个「墨量」= 纯背景，当没有东西
MEDIA_STABLE_DIFF = 0.02      # 两次轮询差异小于它 = 画面稳住了
MEDIA_GROW_PX = 15            # 有墨的最下沿往下长了这么多 = 又来了一张新的
MEDIA_SHIFT_PX = 30           # 基准线往上走了这么多 = 上面那张把下面顶下来了
MEDIA_ROW_SPREAD = 22         # 某一行内部明暗落差超过它 = 这一行压着东西（找最下沿用）
MEDIA_ANIMATED_WAIT = 9.0     # 一直稳不下来（动图/表情包在播）等够这么久就认
MEDIA_CROP_MAX = 480          # 送给视觉模型的那块最多裁这么高
MEDIA_ROWS = 64               # 找「最下沿」时纵向分多少行
MEDIA_BELOW_GAP = 20          # 从「最后一条气泡」往下先空这么高再开始找（避开气泡底边/头像）
MEDIA_MIN_ROOM = 56           # 空出来的地方至少要有这么高，否则根本塞不下图
MEDIA_MIN_BAND = 3            # 「有东西」的行数至少这么多行，才像一张图

# ── 「这块地方到底是图还是字」的第二道判据（探针开火前复核用）──
TEXTBLOCK_LIGHT = 0.85        # 浅色底占比：文字气泡几乎整块都是浅底
TEXTBLOCK_MID = 0.08          # 中间调占比：照片/表情包全是中间调，文字气泡几乎没有
TEXTBLOCK_DARK = 0.008        # 深色笔画占比：文字得有笔画，纯空白/一条边不算
TEXTBLOCK_MIN_CHARS = 4       # OCR 至少读出这么多字才算「一条消息」
# 实测（用户日志里的真实裁片）：
#   文字气泡  浅 0.907 / 中 0.029 / 深 0.064   → 判为文字 ✓
#   雨中街景  浅 0.771 / 中 0.054 / 深 0.175   → 判为图片 ✓（浅色不够、中间调超了）


class MediaMsg:
    """对方发来的一块「没有文字的东西」——图片 / 表情包 / 文件卡片。

    它和普通消息的区别只有一点：文本是空的，得交给视觉模型去看。
    """

    __slots__ = ("box", "png", "note")

    def __init__(self, box, png=None, note=""):
        self.box = box
        self.png = png
        self.note = note

    def __repr__(self):
        return f"<MediaMsg box={self.box} {len(self.png or b'')}字节>"


def _fp_diff(a, b) -> float:
    """两个指纹有多大差别（0~1）。长度不一样就直接算全变了。"""
    if not a or not b or len(a) != len(b):
        return 1.0
    n = len(a)
    return sum(1 for x, y in zip(a, b) if abs(x - y) > 10) / float(n)


def text_block_like(im) -> bool:
    """
    这块地方【长得像一条文字气泡】吗（纯看长相，不跑 OCR，几毫秒）。

    文字气泡 = 大片浅色底 + 一点点深色笔画，几乎没有中间调。
    照片 / 表情包 / 截图里的插图，颜色分布是散的，中间调一大片，过不了这关。

    ★ 为什么需要它：探针只认「有东西 + 占了好几行」，而这正好也是
      一条文字气泡的样子 —— 于是文字被当成图，交给视觉模型去「看」。
    """
    if im is None or Image is None:
        return False
    try:
        g = im.convert("L")
    except Exception:
        return False
    if g.size[0] < 20 or g.size[1] < 12:
        return False
    d = g.tobytes()
    n = float(len(d))
    if n <= 0:
        return False
    light = sum(1 for v in d if v > 205) / n
    mid = sum(1 for v in d if 110 <= v <= 205) / n
    dark = sum(1 for v in d if v < 110) / n
    return (light >= TEXTBLOCK_LIGHT and mid <= TEXTBLOCK_MID
            and dark >= TEXTBLOCK_DARK)


def block_text(img, box):
    """
    探针那块地方，用【本地 OCR】能不能读成一段文字。

    读得出 → 返回那段文字（说明这是条消息，不是图）；读不出 → None。

    这是给「底下到底是图还是字」上的第二道保险，也是最后一道：
    图片 OCR 是读不出字的，能读出字来的就一定是消息。
    用户实测过的那次：一条 24 字的消息被上面的居中判据误伤，
    探针把它当图片裁走 → 视觉模型答「只有文字」→ 她只好说
    「我这边打不开，你打字说给我听」。一条正常消息两头落空。
    现在这里既拦下误报，又顺手把这条消息捞回来交给正常流程。
    """
    if img is None or not box or Image is None:
        return None
    try:
        crop = img.crop(box)
    except Exception:
        return None
    # 先看长相（便宜）：不像文字气泡就不必浪费一次 OCR
    if not text_block_like(crop):
        return None
    try:
        import numpy as np
        res, _ = engine()(np.array(crop.convert("RGB")))
    except Exception:
        return None
    if not res:
        return None
    parts = []
    for _b, t, sc in res:
        t = (t or "").strip()
        if t and sc >= 0.55 and not is_noise(t):
            parts.append(t)
    text = " ".join(parts).strip()
    if len(_norm(text)) < TEXTBLOCK_MIN_CHARS:
        return None
    return text


def media_probe(img, geo, below_y=None, width_ratio=MEDIA_WIDTH_RATIO):
    """
    量一下「最后一条消息下面」那块地方。

    返回 (探针框, 指纹, 墨量, 最下沿的 y)：
      · 探针框是【整张窗口图】的坐标，可以直接 img.crop() 给视觉模型用；
      · 指纹是缩到 32×32 的彩色字节串，用来比「变了没有」；
      · 「最下沿」是那块地方有内容的最低一行（聊天区坐标）。

    ★ 为什么要「最下沿」：光比指纹分不出「同一个表情包在动」和
      「底下又来了一张新的」。而这两件事的差别很明显 ——
      动图再怎么播，最下面那一行都在原地；又来一张新的，最下沿一定往下走。
    """
    if Image is None:
        return None, None, 0.0, None

    region = img.crop((geo.left, geo.top, geo.right, geo.bottom))
    rw, rh = region.size
    if rw < 80 or rh < 80:
        return None, None, 0.0, None

    # 从「最靠下那条气泡的下边」再空一段，才开始找。
    #
    # ★ 这两个数是踩坑踩出来的（用户实测：根本没发图，她却说「又是图？」）：
    #   ① 气泡是有厚度的 —— OCR 给的 y 是【文字】的底边，气泡的圆角底边
    #      和旁边的头像都比它再低 10~15px，所以要先空出 MEDIA_BELOW_GAP；
    #   ② 以前这里还有一句 min(rh-24, y0) 兜底，本意是「实在没地方也留一条」，
    #      结果那 24px 里永远躺着【上一条消息的底边 + 头像下半截 +
    #      输入框上边框】，于是「底下永远有东西」—— 任何一次画面变化之后
    #      都会误报一张图片。翻日志里那两次误报的裁片，量到的正是
    #      「气泡底边 + 头像」（墨量 0.07 / 0.23，而阈值才 0.012）。
    #   现在改成：下面根本留不出地方（说明最后一条就贴在输入框上面），
    #   那就当没有图。真来图的时候界面会把上面的内容顶上去，地方自然就有了。
    y0 = int(rh * 0.45) if below_y is None else int(below_y) + MEDIA_BELOW_GAP
    y0 = max(0, y0)
    if rh - y0 < MEDIA_MIN_ROOM:
        return None, None, 0.0, None
    if rh - y0 > MEDIA_CROP_MAX:          # 太高了只留底下这一段，省 token
        y0 = rh - MEDIA_CROP_MAX

    x1 = max(40, min(rw, int(rw * float(width_ratio or MEDIA_WIDTH_RATIO))))
    probe = region.crop((0, y0, x1, rh))
    if probe.size[0] < 20 or probe.size[1] < 20:
        return None, None, 0.0, None

    grey = probe.convert("L").resize((40, MEDIA_ROWS))
    data = list(grey.getdata())
    mean = sum(data) / float(len(data))
    ink = sum(1 for v in data if abs(v - mean) > 18) / float(len(data))
    if ink < MEDIA_MIN_INK:
        return None, None, ink, None

    # 背景色 = 探针【最左边那两列】的中位数。
    # ★ 为什么不用「第一行」或「整块平均值」：那块地方大半是图的时候，
    #   这两个都会被图带跑，于是背景行也显得「有东西」—— 实测后果就是
    #   最下沿永远顶在聊天区底部，「底下又来一张」判不出来（连发两张图时
    #   第二张永远读不出来）。探针最左边那两列是留给头像的空白，
    #   永远都是聊天底色，拿它当基准最稳。
    col = [data[r * 40] for r in range(MEDIA_ROWS)]
    col += [data[r * 40 + 1] for r in range(MEDIA_ROWS)]
    bg = sorted(col)[len(col) // 2]

    # 「有东西」的行数：一张图会占掉一大片（十几到几十行），
    # 而气泡的一条边、一根分隔线只占一两行 —— 用行数把「边」和「图」分开。
    band = 0
    bottom_row = -1
    for r in range(MEDIA_ROWS):
        seg = data[r * 40:(r + 1) * 40]
        if sum(1 for v in seg if abs(v - bg) > 18) / 40.0 > 0.10:
            band += 1
            bottom_row = r
    if band < MEDIA_MIN_BAND:
        return None, None, ink, None

    # 最下沿：有东西的最低那一行（_maybe_media 靠它判断「底下又长出来一张」）
    bottom_y = int(y0 + (bottom_row + 1) * (rh - y0) / float(MEDIA_ROWS))

    # 指纹用【彩色】不用灰度：两张亮度差不多的图（比如两张不同照片）
    # 在灰度下几乎一样，会被当成「同一张」而漏掉。
    rgb = probe.convert("RGB").resize((32, 32))
    box = (geo.left, geo.top + y0, geo.right, geo.top + rh)
    return box, bytes(rgb.tobytes()), ink, bottom_y


# ══════════════════════════════════════════════════════════════
# 四、新消息跟踪
# ══════════════════════════════════════════════════════════════

SEEN_KEEP = 12 * 3600         # 「这条已经见过」记多久（秒）
                              # ★ 别改小（这里也踩过坑）：聊天界面【静止】的时候
                              #   程序连截图都不比对（画面没变就直接返回），压根没
                              #   机会刷新「见过」的记录。原来只记 30 分钟，于是
                              #   两小时前那句「好吧，晚安」过期了 —— 底下新消息一
                              #   冒出来，它会被当成新消息，跟新消息粘成一条递给她
                              #   （实测：她拿两小时前那句话接话，刚发的「睡着了么」
                              #   反而没人理）。
MAX_BURST = 6                 # 一次最多认几条新消息（再多就当是用户在翻历史记录）


def _norm(s: str) -> str:
    """比较前先把空白都去掉 —— OCR 对空格的读法飘得厉害，
    逐字比会把同一条消息判成两条（`嗯 在的` vs `嗯在的`）。"""
    return re.sub(r"\s+", "", s or "")


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _same_msg(a: str, b: str) -> bool:
    """两条气泡算不算「同一条消息」（容忍 OCR 把同一个气泡读歪一两个字）。

    ★ 这里只能做最保守的判断，因为纯文本先天分不出这两种情况：
        · 同一个气泡被 OCR 读歪了（`嗯晚安你也早点睡` / `嗯晚安你也早占晒`）
        · 他改了个字又发一条（`你不想和我聊天？` / `你不想和我聊天吗`）
      实测后者的相似度是 0.875，跟前者的 0.75~0.875 完全一个量级。
      真正的区分靠【屏幕结构】—— 见 IncomingTracker._seen_recently。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    return abs(len(a) - len(b)) <= 1 and _similar(a, b) > 0.85


def _signature(img):
    """把图缩成小灰度图当指纹，用来快速判断画面有没有变化。"""
    return img.convert("L").resize((72, 48)).tobytes()


class IncomingTracker:
    """
    负责「盯着聊天区，判断有没有新的对方消息」。

    去重思路 —— 靠【内容】而不是靠【位置】：
      界面对着底的时候，新消息一定出现在最底下，占的就是上一条消息
      原来的那个位置（实测 y1 都是聊天区高度减 20，前后两轮一模一样）。
      所以「y 有没有往下走」根本判不出新旧，必须把「最近见过哪些话」
      记下来：屏幕上不认识的、且连着排在底部的那一截，才是新消息。

      · 一次来好几条（她在忙 / 正在生成回复的时候，我们会漏扫几轮）→ 全交出去
      · 自己刚发完消息整屏上移                     → 内容没变，不算新
      · 用户往上翻历史记录                         → 上面冒出来的不认识的内容不算新
      · 同一条被 OCR 读得有点抖                    → 内容近似的当同一条
    """

    def __init__(self, win, cfg=None, debug_dir=None):
        self.win = win
        self.cfg = cfg or {}
        self.geo = None
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self._sig = None
        self._size = None
        self._last_text = None
        self._last_y = None
        self._replied = {}
        self._seen = {}               # 归一化文本 -> 最后见到的时间
        self._seen_refresh_at = 0.0   # 上次在「静止画面」上补登记「见过」的时刻
        self.primed = False
        # ── 图片 / 表情包检测（详见上面「三点五」那一节）──
        self._media_prev = None       # 上一次轮询的指纹
        self._media_sig = None        # 已经读过的那张的指纹
        self._media_bottom = None     # 已经读过的那张的最下沿（看有没有长出新的一张）
        self._media_dirty = 0.0       # 底下从什么时候开始「有东西」
        self._media_fired = False     # 这一张已经读过了，别重复读
        self._media_waiting = False   # 看见了但还没认（在等它稳住）
        self._media_anchor = None     # 上次探针的基准线（画面没变时接着用）
        self._media_anchor_at = None  # 认下那张图时，基准线在哪（看它有没有被顶上去）
        self._media_at = 0.0          # 上次读图的时间
        self._media_box = None        # 上次那块地方的坐标（调试用）

    # ---------- 内部 ----------

    def _apply_override(self, size):
        """
        手动钉死聊天区。只有「3-自检.bat」存出来的标注图明显框偏了才需要，
        在 config.yaml 的 screen 段里写。值 <1 当比例，>=1 当像素。
        """
        sc = (self.cfg or {}).get("screen") or {}
        if not isinstance(sc, dict):
            return
        W, H = size
        hit = False
        for key, attr, dim in (("chat_left", "left", W), ("chat_right", "right", W),
                               ("chat_top", "top", H), ("chat_bottom", "bottom", H)):
            v = sc.get(key)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            setattr(self.geo, attr, int(v * dim) if 0 < v < 1 else int(v))
            hit = True
        if hit:
            self.geo.how = "config 指定"

    def _grab(self):
        img = capture_window(self.win)
        if img is None:
            return None
        if self.geo is None or self._size != img.size:
            self.geo = detect_geometry(img)
            self._apply_override(img.size)
            self._size = img.size
            self._sig = None
        return img

    def _region(self, img):
        return img.crop((self.geo.left, self.geo.top, self.geo.right, self.geo.bottom))

    def _changed(self, img) -> bool:
        sig = _signature(self._region(img))
        if self._sig is None:
            self._sig = sig
            return True
        old = self._sig
        self._sig = sig
        diff = sum(1 for a, b in zip(old, sig) if abs(a - b) > 8)
        return diff > len(old) * 0.006

    def _last_incoming(self, img):
        bubbles = read_bubbles(img, self.geo)
        incoming = [b for b in bubbles if not b.mine and not is_noise(b.text)]
        if not incoming:
            return None
        return max(incoming, key=lambda b: b.y1)

    def last_turn(self, img=None):
        """最后一条气泡是谁发的 —— 用来判断「球在谁那边」。

        返回 (文本, 是不是自己发的)；一条都没读到返回 (None, None)。

        用途：启动时如果最后一条是【对方】发的，说明她还欠一句
        （消息发来的时候程序没在跑，prime() 会把它记成「已看过」直接略过），
        这时应该补一句，而不是装没看见。
        """
        if img is None:
            img = self._grab()
        if img is None:
            return None, None
        try:
            bubbles = [b for b in read_bubbles(img, self.geo) if not is_noise(b.text)]
        except Exception:
            return None, None
        if not bubbles:
            return None, None
        b = max(bubbles, key=lambda x: x.y1)
        return b.text, bool(b.mine)

    def mark_replied(self, text: str):
        """记下「这条已经回过了 / 已经见过了」。

        开机补一句时用 —— 补完那条不会走 poll() 的登记流程，
        所以这里手动登记，免得下一轮又把它当成新消息回一遍。
        多行（一次补了好几条）就逐行登记。
        """
        now = time.time()
        for line in (text or "").splitlines():
            t = _norm(line)
            if t:
                self._replied[t] = now
                self._seen[t] = now

    def _refresh_seen(self, img, every: float = 600.0):
        """画面长时间静止时，隔一阵子重新登记一遍「屏幕上这些都是见过的」。

        ★ 不补登记就会过期（踩过的坑）：界面不动的时候我们连 OCR 都不做
          （画面没变就直接返回），「见过」的时间戳一直不往前走 ——
          对方两小时没动静，屏幕最底下那句旧话就过期了；这时候他发来
          一条新的，旧话会被当成新消息，跟新消息粘成一条递给她
          （实测：她拿两小时前那句「好吧，晚安」接话，刚发来的晾着）。
          这里每 10 分钟补一次 OCR，一轮不到一秒，可以忽略。
        """
        now = time.time()
        if now - self._seen_refresh_at < every:
            return
        self._seen_refresh_at = now
        try:
            for x in read_bubbles(img, self.geo):
                if x.mine or is_noise(x.text):
                    continue
                t = _norm(x.text)
                if t:
                    self._seen[t] = now
        except Exception:
            pass

    def _seen_recently(self, t: str, now: float, above=()) -> bool:
        """这条内容最近是不是已经见过（顺手把过期的清掉）。

        above = 屏幕上【排在它上面】的对方气泡文本。这个参数是用来区分
        「同一个气泡读歪了」和「他改了个字又发一条」的 —— 见下面的坑。

        ★ 这里栽过一次，代价是连着三条消息全被吞（用户实测）：
          他问「你不想和我聊天？」，她没回（因为那条正撞在别的坑上），
          他又追了两条「你真的不想和我聊天？」「你不想和我聊天吗」。
          后两条跟第一条的相似度是 0.889 / 0.875，都越过 0.85 ——
          全被判成「第一条被 OCR 读歪了」，一条都没交出去。
          于是他又发、她更不回，恶性循环，用户看到的就是
          「她好像没看见我说啥一样的」。
          文本分不出这两种情况，但**屏幕分得出**：这条【上面】要是还挂着
          一条长得一样的，那「已经见过」说的是上面那条，眼前这条
          必然是另一条新消息（同一个气泡不可能在屏幕上出现两次）。
        """
        for k, ts in list(self._seen.items()):
            if ts < now - SEEN_KEEP:
                self._seen.pop(k, None)
                continue
            if not _same_msg(t, k):
                continue
            if any(_same_msg(o, k) for o in above):
                continue          # 见过的是上面那条，这条是新消息
            return True
        return False

    def _fresh_messages(self, incoming, now):
        """
        从这一屏的对方消息里挑出「这次该回的那几条」。

        ★ 为什么不能只看「纵向位置有没有往下走」：
          界面贴到底的时候，新消息一定冒在最底下 —— 它占的正是上一条
          消息原来的位置，y 几乎一动不动（实测两轮都是聊天区高度减 20）。
          所以位置判不出新旧，得反过来看【内容】：屏幕上一大半是刚才
          已经见过的话，从底部往上数、连着不认得的那一截，才是新来的；
          一撞到认识的（= 上面都是历史）就停。

        ★ 为什么要把「一截」全交出去：她「正在忙」那 40~150 秒里我们
          根本不在扫屏幕，对方这时候连着发两三句，回来只认最底下那条，
          上面几句就被静悄悄吞了（用户实测：发了两条，只回了第二条）。

        ★ 为什么每条都要带上「它上面还有哪些气泡」（above）：
          连着发几句意思相近的话是**很常见**的（尤其她没回的时候，
          人会越问越像）。近似的文本必须靠屏幕结构才分得清是「同一句
          被读歪」还是「他改了个字又发一句」，见 _seen_recently。
        """
        if not incoming:
            return []
        run = []
        for x in reversed(incoming):          # 从最底下往上收
            t = _norm(x.text)
            if not t:
                continue
            if self._replied.get(t, 0) > now - 300:
                break                          # 这条刚回过了
            above = [_norm(o.text) for o in incoming if o.y1 < x.y1]
            if self._seen_recently(t, now, above=above):
                break                          # 撞到认识的 = 上面都是旧内容
            run.append(x)
        run.reverse()
        if not run:
            return []
        if self._looks_like_paging(run, incoming):
            # 整屏都换成「不认识的」、而且一直顶到聊天区最上面 ——
            # 这是用户把窗口往上翻页了。全记成见过，别去回一串陈年消息。
            for x in run:
                self._seen[_norm(x.text)] = now
            return []
        return run

    def _looks_like_paging(self, run, incoming) -> bool:
        """这一大片「不认识的」，是不是用户在往上翻历史记录？

        ★ 2026-10-05 重写。老判据是

              len(run) > MAX_BURST or (len(run) == len(incoming) and len(run) >= 3)

          ——「不认识的凑够三条、而且满屏都是不认识的」就当翻页，**整批悄悄丢掉**，
          顺手登记成「见过」，于是她再也不会回。实测它吃掉过真消息：
          `日志\\win-000507.png` 那一屏里，一条很长的多行消息被聊天区上边缘切了一刀，
          同一条读成了两片（「5替死鬼 6鬼梦 7只是一只很可爱的可爱鬼也就是我呀」
          + 完整那一条），底下再加一条刚发来的 —— 正好三条全是「陌生人」
          → 整批被吞。用户看到的就是「我发的那条消息她一直读取不到」。

        翻页和「他连着发了好几条」的差别在**位置**：
          · 往上翻页 → 整屏都是陈年旧话，最上面那条也是不认识的，铺满一屏；
          · 他连发几条 → 新消息从底部往上堆，顶部还压着我们见过／回过的话。
        所以这里只认两条硬的：
          ① 量够大（4 条起；超过 MAX_BURST 的两倍直接算翻页）；
          ② 最上面那条已经顶到聊天区前 18%。
        再加一条反向豁免：**同一句话被上边缘切成两片** = 一条长消息，不是翻页
        （碎片和完整那片的文本互相包含）。
        """
        if not incoming:
            return False
        if len(run) > MAX_BURST * 2:
            return True
        if len(run) < 4:
            return False
        for x in run:                      # 同一条被顶部切成的碎片 → 不是翻页
            t = _norm(x.text)
            if len(t) < 6:
                continue
            for y in incoming:
                if y is x:
                    continue
                o = _norm(y.text)
                if o and (t in o or o in t):
                    return False
        height = max(1, (self.geo.bottom - self.geo.top))
        top = min(x.y0 for x in run)
        return top <= height * 0.18

    def _nodebug(self):
        pass

    def _save_debug(self, img, bubble):
        if not self.debug_dir:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%H%M%S")
        img.save(self.debug_dir / f"win-{ts}.png")
        if bubble:
            self._region(img).save(self.debug_dir / f"chat-{ts}.png")

    # ---------- 对外 ----------

    def prime(self):
        """启动时先跑一次：把聊天区里【所有】对方消息都记成「见过」，但不回复。

        ★ 以前只记最下面那一条。现在必须全记 —— 判新旧的依据整个换成
          「这条内容见过没有」了，漏记一条，下一轮就会把它当新消息回一遍。
        """
        img = self._grab()
        if img is None:
            return False
        now = time.time()
        try:
            for x in read_bubbles(img, self.geo):
                if x.mine or is_noise(x.text):
                    continue
                t = _norm(x.text)
                if t:
                    self._seen[t] = now
        except Exception:
            pass
        b = self._last_incoming(img)
        if b:
            self._last_text, self._last_y = b.text, b.y1
        self.primed = True
        self.reset_media()
        return True

    def poll(self):
        """
        扫一次。

        返回：
          一段文字   —— 对方发来的新消息
          MediaMsg   —— 对方发来一张图 / 表情包（没有文字，得让视觉模型去看）
          None       —— 没有新东西
        """
        img = self._grab()
        if img is None:
            return None
        changed = self._changed(img)

        if not changed:
            # 画面没动，平时什么都不用做。
            # ★ 但有个坑：静态图片在聊天区里是【不会动】的 —— 它冒出来那一轮
            #   我们只是记下「底下有东西」，下一轮整屏已经一模一样、_changed
            #   直接 False，于是永远等不到「稳住」那一步，这张图就永远读不出来。
            #   所以只要还在等，画面没变也得接着摸。
            self._refresh_seen(img)          # 静止时也要偶尔刷新「见过」的时间戳
            if self._media_waiting:
                return self._maybe_media(img, self._media_anchor)
            return None

        try:
            bubbles = [x for x in read_bubbles(img, self.geo) if not is_noise(x.text)]
        except Exception:
            return None

        incoming = [x for x in bubbles if not x.mine]
        b = max(incoming, key=lambda x: x.y1) if incoming else None
        # 探针的基准：聊天区里最靠下那条气泡（不管谁发的）。
        # 用「最后一条对方消息」当基准会出事 —— 我们自己回完一句，
        # 评论框就跑到那张图下面去了，探针会把我们自己的话当成新图片。
        anchor = max((x.y1 for x in bubbles), default=None)

        if not self.primed:
            if b is not None:
                self._last_text, self._last_y = b.text, b.y1
            self.primed = True
            return None

        now = time.time()

        # ── 这次该回的消息（可能不止一条，见 _fresh_messages）──
        fresh = self._fresh_messages(incoming, now)

        # ★ 屏幕上看得见的对方消息，这一轮**全部**记成「见过」——
        #   不只是这一轮交出去的那几条。
        #   为什么必须全记：界面静止的时候我们连截图都不比对，那句
        #   「好吧，晚安」挂了两小时早就过期了；等底下冒出新消息时，
        #   它会被当成一条新消息、跟新消息粘成一条递给她（实测：她拿
        #   两小时前那句话接话，刚发来的「睡着了么」反倒没人理）。
        #   顺手把「见过」的存留时间也刷新了一遍。
        for x in incoming:
            t = _norm(x.text)
            if t:
                self._seen[t] = now
        if len(self._seen) > 2000:                # 别让它无限涨
            self._seen = {k: v for k, v in self._seen.items()
                          if v > now - SEEN_KEEP}

        if fresh:
            text = "\n".join(x.text for x in fresh)
            last = fresh[-1]
            self._last_text, self._last_y = last.text, last.y1
            self.reset_media()                    # 来了新文字，图片检测重新开始
            self._save_debug(img, last)
            return text

        # 没读到新文字 —— 底下是不是多了一张图（图片 OCR 是瞎的，只能这么找）
        return self._maybe_media(img, anchor)

    # ---------- 图片 / 表情包 ----------

    def reset_media(self):
        """把「底下那张图」的状态清掉。来了新文字 / 开机时调。"""
        self._media_prev = None
        self._media_sig = None
        self._media_bottom = None
        self._media_dirty = 0.0
        self._media_fired = False
        self._media_waiting = False
        self._media_anchor = None

    def _media_ratio(self) -> float:
        sc = (self.cfg or {}).get("screen") or {}
        try:
            return float(sc.get("media_width_ratio", MEDIA_WIDTH_RATIO))
        except (TypeError, ValueError):
            return MEDIA_WIDTH_RATIO

    def _maybe_media(self, img, anchor):
        """底下那块地方有没有新冒出来的图片 / 表情包。有就返回 MediaMsg。"""
        if Image is None:
            return None
        if anchor is not None:
            self._media_anchor = anchor
        box, fp, _ink, bottom_y = media_probe(img, self.geo, self._media_anchor,
                                              self._media_ratio())

        if fp is None:
            # 那块地方空了 —— 图被顶上去或滚走了，下次从零开始认
            if self._media_dirty or self._media_fired or self._media_waiting:
                self.reset_media()
            return None

        now = time.time()
        if not self._media_dirty:
            self._media_dirty = now
        self._media_waiting = True

        prev, self._media_prev = self._media_prev, fp

        if self._media_fired:
            # 这一张已经读过了。底下又冒出新的一块才算新的，两种形态：
            #   · 最下沿往下走了（界面没贴底，新图接着往下排）
            #   · 基准线被顶上去一截（界面贴着底：新图挤进来，上面整体上移），
            #     而且指纹也跟着变了
            # ★ 为什么要加第二种：界面贴底的时候，新旧两张图的「最下沿」
            #   都在聊天区底部附近，第一个判据根本不成立，于是连发两张图时
            #   第二张永远读不出来。而「基准线被顶上去」是贴底时独有的信号 ——
            #   动图在播的话基准线一动不动，所以不会被误当成新图。
            grew = (bottom_y is not None and self._media_bottom is not None
                    and bottom_y >= self._media_bottom + MEDIA_GROW_PX)
            pushed = (self._media_anchor is not None
                      and self._media_anchor_at is not None
                      and self._media_anchor <= self._media_anchor_at - MEDIA_SHIFT_PX
                      and _fp_diff(self._media_sig, fp) > MEDIA_STABLE_DIFF)
            if not (grew or pushed):
                self._media_waiting = False
                return None
            self._media_fired = False

        stable = prev is not None and _fp_diff(prev, fp) <= MEDIA_STABLE_DIFF
        if not stable and (now - self._media_dirty) < MEDIA_ANIMATED_WAIT:
            # 还在变，再等等 —— 静态图一两轮就稳了，等不到的是动图
            return None

        # ── 开火之前，用本地 OCR 最后复核一次这块地方 ──
        # 读得出文字 = 这其实是一条【消息】，不是图。
        # 万一哪天读气泡那一步又漏了一条（上次就是这么栽的），
        # 这里能拦住误报，还能把这条消息捞回来交给正常流程。
        as_text = block_text(img, box)
        if as_text is not None:
            self._media_fired = True          # 记成「处理过了」，别重复读
            self._media_waiting = False
            self._media_sig = fp
            self._media_bottom = bottom_y
            self._media_anchor_at = self._media_anchor
            self._media_at = now
            self._media_box = box
            key = _norm(as_text)
            if key and not self._seen_recently(as_text, now) \
                    and self._replied.get(key, 0) < now - 300:
                self._seen[key] = now
                self._last_text, self._last_y = as_text, bottom_y
                if self.debug_dir:
                    try:
                        self.debug_dir.mkdir(parents=True, exist_ok=True)
                        img.crop(box).save(
                            self.debug_dir / ("astext-%s.png" % time.strftime("%H%M%S")))
                    except Exception:
                        pass
                print("  （探针那块读出来是文字，不是图 —— 当消息交上去）")
                return as_text
            return None

        self._media_fired = True
        self._media_waiting = False
        self._media_sig = fp
        self._media_bottom = bottom_y
        self._media_anchor_at = self._media_anchor
        self._media_at = now
        self._media_box = box

        png = None
        try:
            import io as _io
            buf = _io.BytesIO()
            img.crop(box).save(buf, format="PNG")
            png = buf.getvalue()
        except Exception:
            png = None

        if self.debug_dir:
            try:
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                img.crop(box).save(
                    self.debug_dir / ("media-%s.png" % time.strftime("%H%M%S")))
            except Exception:
                pass

        note = "稳定" if stable else "一直在动（多半是表情包/动图）"
        return MediaMsg(box, png, note)


# ══════════════════════════════════════════════════════════════
# 五、给自检脚本用的调试视图
# ══════════════════════════════════════════════════════════════

def annotate(img, geo: Geometry, bubbles):
    """在原图上画框，方便肉眼确认读得对不对。"""
    from PIL import ImageDraw
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.rectangle([geo.left, geo.top, geo.right - 1, geo.bottom - 1], outline=(255, 0, 0), width=2)
    for b in bubbles:
        color = (0, 120, 255) if b.mine else (0, 170, 0)
        d.rectangle(
            [geo.left + b.x0 - 2, geo.top + b.y0 - 2, geo.left + b.x1 + 2, geo.top + b.y1 + 2],
            outline=color, width=2,
        )
    return out
