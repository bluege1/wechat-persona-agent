"""
语音消息发送模块
将 base64 编码的音频解码播放，同时模拟 Alt 按键以触发微信语音输入。
"""

import base64
import ctypes
import logging
import os
import sys
import tempfile
import threading
import time

logger = logging.getLogger("WeChatSender.Voice")

# 初始化 pygame.mixer（音频播放后端，同时用于获取音频时长）
#
# ★ 达妮娅懒人包修复 ★
# pygame 必须在【模块级】导入。send() 里是直接用 `pygame.mixer.Sound(...)`，
# 而原来只在 _init_mixer() 函数内部 `import pygame.mixer`，
# 模块作用域里根本没有 pygame 这个名字 —— 一旦真的走到发送语音那一步，
# 就会抛 `NameError: name 'pygame' is not defined`，语音条永远发不出去
# （而文字消息正常，所以很容易被误判成"语音功能没做"）。
pygame = None
try:
    import pygame
    import pygame.mixer
except Exception:
    pygame = None

AUDIO_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# 【达妮娅懒人包改动 1/3】把 TTS 只送进虚拟声卡 CABLE Input，
# 不去动系统默认播放设备 —— 这样她发语音的同时，你照样能听歌看视频。
# 想让播放跟着系统默认设备走：设环境变量 DANIYA_CABLE_OUT=off
# ─────────────────────────────────────────────────────────────────────────────
CABLE_DEVICE_HINT = os.environ.get("DANIYA_CABLE_OUT", "CABLE Input")

# 【达妮娅懒人包改动 2/3】先开口再出声：微信从按下 Alt 到真正开始录音有几百毫秒延迟，
# 如果同时开始播放，第一个字会被吞掉。所以先按住 Alt，等 LEAD_IN 秒后再放音频。
VOICE_LEAD_IN = float(os.environ.get("DANIYA_VOICE_LEAD_IN", "0.45"))

# 微信要求语音至少 2 秒
MIN_VOICE_DURATION = float(os.environ.get("DANIYA_VOICE_MIN", "2.0"))


# 【达妮娅懒人包改动 3/3】音频初始化超时上限（秒）。
# 背景：SDL/WASAPI 在音频设备异常时会**阻塞近 60 秒**才返回失败
#   （报错：WASAPI can't initialize audio client: 系统资源不足，无法完成请求的服务。）
#   _init_mixer() 是在 import 时同步跑的，于是整个小信使的启动被拖到两分钟，
#   一键启动那边等不到端口就误判「小信使没起来」。
# 所以每一步音频初始化都放进子线程并设超时，超时就放弃音频让程序照常起来 ——
#   宁可本次语音不可用，也不能卡住启动。
_INIT_TIMEOUT = float(os.environ.get("WEE_AUDIO_TIMEOUT", "15"))


def _guarded(label, fn, timeout=None):
    """在子线程里跑 fn，最多等 timeout 秒。

    返回 (结果, 错误)。错误为 None 表示成功；为 "timeout" 表示超时未返回。
    """
    timeout = timeout or _INIT_TIMEOUT
    box = {}

    def _run():
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as e:      # noqa: BLE001 - 音频初始化什么都可能抛
            box["ok"] = False
            box["err"] = e

    t = threading.Thread(target=_run, name="wee-audio", daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        logger.warning(
            f"[WeeMessenger - 警告] {label} 超过 {timeout:.0f} 秒仍未返回 —— "
            f"音频设备异常，放弃音频初始化（本次语音功能不可用，发送不受影响）"
        )
        return None, "timeout"
    if box.get("ok"):
        return box.get("value"), None
    return None, box.get("err")


def _init_mixer():
    """初始化混音器，优先绑定到虚拟声卡；任何一步失败都退回系统默认设备。"""
    import pygame.mixer

    wanted = None
    hint = (CABLE_DEVICE_HINT or "").strip()
    if hint and hint.lower() not in ("off", "0", "none", "default"):
        def _enum():
            import pygame._sdl2.audio as sdl2_audio
            pygame.mixer.init()                       # SDL 要先起来才能枚举设备
            names = list(sdl2_audio.get_audio_device_names(False))
            pygame.mixer.quit()
            return names

        names, err = _guarded("SDL 音频初始化/设备枚举", _enum)
        if err:
            logger.warning(f"[WeeMessenger - 警告] 枚举音频设备失败: {err}，改用系统默认设备")
        else:
            low = hint.lower()
            for n in names or []:
                if low in n.lower():
                    wanted = n
                    break
            if not wanted:
                logger.warning(
                    f"[WeeMessenger - 警告] SDL 设备里没有含「{hint}」的播放设备: {names}，"
                    f"改用系统默认设备"
                )

    if wanted:
        _, err = _guarded(
            f"绑定「{wanted}」",
            lambda: pygame.mixer.init(devicename=wanted),
        )
        if not err:
            logger.info(f"[WeeMessenger - 提示] 音频播放后端：pygame.mixer -> {wanted}")
            return
        logger.warning(f"[WeeMessenger - 警告] 绑定「{wanted}」失败: {err}，改用系统默认设备")

    _, err = _guarded("绑定系统默认设备", pygame.mixer.init)
    if err:
        # 抛给外层统一处理（AUDIO_AVAILABLE = False）
        raise RuntimeError(str(err) if err != "timeout" else "音频初始化超时")
    logger.info("[WeeMessenger - 提示] 音频播放后端：pygame.mixer（系统默认设备）")


# ─────────────────────────────────────────────────────────────────────────────
# 【达妮娅懒人包修复】音频初始化改成「后台慢慢来」
#
# 原来 _init_mixer() 是在 import 时同步跑的。SDL/WASAPI 在音频设备出问题时
# 会**阻塞近 60 秒**才返回失败（WASAPI can't initialize audio client:
# 系统资源不足），整个小信使的启动就被拖到两分钟，一键启动那边等不到
# 9877 端口就误判「小信使没起来」—— 表现为启动失败，其实只是慢。
#
# 现在：import 时只丢一个后台线程去初始化，主线程一秒都不等；
# 真正要发语音时才由 ensure_mixer() 等它收尾（同样有超时）。
# 于是启动永远是秒级的，音频设备坏掉也只是「语音发不出」而不是「起不来」。
# ─────────────────────────────────────────────────────────────────────────────
_MIXER = {
    "started": False,
    "done": threading.Event(),   # 初始化有结果了（成功或失败都置位）
    "ok": False,
    "err": None,
    "probed": False,             # 已经用子进程探测过设备了吗
    "dead": False,               # 设备被判定不可用 -> 之后一律不再尝试
}

# 子进程探测脚本：只做一次最小化的 mixer 初始化
_PROBE_SRC = "import pygame, pygame.mixer; pygame.mixer.init()"
_PROBE_TIMEOUT = float(os.environ.get("WEE_AUDIO_PROBE_TIMEOUT", "8"))


def _probe_audio_device():
    """在【独立子进程】里试一次音频初始化，判断设备能不能用。

    为什么非要开子进程：实测 SDL/WASAPI 初始化卡住时**冻结的是整个进程**
    （后台线程 + join 超时都拦不住，主线程照样一起停），
    只有放到另一个进程里，卡住才影响不到小信使自己。
    """
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, "-c", _PROBE_SRC],
            timeout=_PROBE_TIMEOUT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        # 超时 / 起不来 / 别的任何问题，一律当作设备不可用
        return False


def _start_mixer_async():
    """后台初始化混音器，只跑一次，主线程不等待。"""
    if _MIXER["started"]:
        return
    _MIXER["started"] = True

    def _run():
        try:
            _init_mixer()
            _MIXER["ok"] = True
        except Exception as e:      # noqa: BLE001
            _MIXER["err"] = e
            logger.warning(
                f"[WeeMessenger - 警告] pygame.mixer 初始化失败: {e}。语音功能不可用，"
                f"文字消息不受影响。"
            )
        finally:
            _MIXER["done"].set()

    threading.Thread(target=_run, name="wee-mixer-init", daemon=True).start()


def ensure_mixer(timeout=None):
    """发语音前调用：等后台音频初始化收尾，最多等 timeout 秒。

    返回 True 表示混音器可用；False 表示不可用（初始化失败或超时）。
    绝大多数机器上后台线程几百毫秒就搞定了，这里几乎不花时间。
    """
    # 已经判死刑的设备：直接拒绝，一次都不再碰（碰一次就冻一次）
    if _MIXER["dead"]:
        return False
    if _MIXER["ok"]:
        return True

    # 第一次：先用子进程探一下设备能不能用（卡住也只卡子进程，最多 _PROBE_TIMEOUT 秒）
    if not _MIXER["probed"]:
        _MIXER["probed"] = True
        if not _probe_audio_device():
            _MIXER["dead"] = True
            logger.warning(
                f"[WeeMessenger - 警告] 音频设备不可用（子进程 {_PROBE_TIMEOUT:.0f} 秒内没初始化成功），"
                f"语音功能已关闭；文字消息不受影响。"
            )
            return False

    _start_mixer_async()
    if not _MIXER["done"].wait(timeout or _INIT_TIMEOUT):
        _MIXER["dead"] = True
        logger.warning(
            f"[WeeMessenger - 警告] 音频设备异常：初始化 {timeout or _INIT_TIMEOUT:.0f} 秒"
            f" 都没好，本次语音放弃（文字消息不受影响）"
        )
        return False
    if not _MIXER["ok"]:
        _MIXER["dead"] = True
    return _MIXER["ok"]


# ★ 这里【故意不】启动初始化 —— 启动阶段一次都不碰音频设备。
#   实测：就算丢到后台线程，SDL 的 WASAPI 初始化（它走 COM）仍会把整个进程
#   卡住 —— 主线程连 uiautomation 的 COM 初始化都一起被拖住，
#   所以「后台慢慢来」救不了启动，只有「启动时不碰」才行。
#   第一次真要发语音时，ensure_mixer() 才会去初始化它。
AUDIO_AVAILABLE = True



# ── SendInput 键盘模拟结构 ──
# 必须使用联合体（union）匹配 Windows INPUT 结构体大小，否则 SendInput 静默失败

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.c_ushort),
        ("wScan",       ctypes.c_ushort),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg",    ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type",  ctypes.c_ulong),
        ("union", _INPUT_UNION),
    ]


INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001   # 右 Alt / 右 Ctrl / 右 Shift 靠这一位和左边区分
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12                   # 左/右 Alt 报的都是这个 VK，靠 extended 位区分左右
VK_CONTROL = 0x11
VK_SHIFT = 0x10


# ─────────────────────────────────────────────────────────────────────────────
# 【达妮娅懒人包】「发送语音消息」的快捷键 —— 别和「语音输入文字」搞混
#
#   微信 4.1.8 加了「语音输入文字」（按住 Ctrl+Win，或点输入框下方工具栏的小话筒），
#   产物是【文字】；微信 4.1.9 才加了「发送语音消息」（聊天框右下角的小话筒，
#   或按住 Alt），产物是【语音条】。我们要的是后者。
#
#   踩过的坑（2026-09-20 实测）：
#     用 SendInput 按【左 Alt】按住 1.8 秒 —— 微信是前台窗口、SendInput 返回 1，
#     但界面像素差异 0.0000，微信完全没进录音态。
#     而后来的资料显示「发送语音消息」默认绑的是【右 Alt】。
#     左 Alt 和右 Alt 在 Windows 里是同一个 VK(0x12)，
#     必须靠 KEYEVENTF_EXTENDEDKEY 这一位来区分 —— 老代码只发了左 Alt。
#     → 所以这里把 左/右 Alt 做成两个可选项，默认发【右 Alt】。
#
#   换键：设环境变量 DANIYA_VOICE_HOTKEY，可写人话或虚拟键码，逗号分隔多键：
#         "RAlt" / "LAlt" / "Ctrl+Win" / "0x11,0x5B"
#         多个键按顺序按下、逆序松开。
#
#   注意：send() 的返回值只代表"按键流程走完了"，不代表"发出去了"。
#   到底有没有产生语音条，必须由调用方回读微信聊天区来判断（见 bridge.py）。
# ─────────────────────────────────────────────────────────────────────────────
# 常用键的别名 -> (虚拟键码, 是否带 extended 位)。
# Alt / Ctrl / Shift 的左右是同一个 VK，靠 extended 位区分；
# Win 键本身就是两个不同的 VK(0x5B/0x5C)，不需要 extended。
_VK_NAMES = {
    "alt":  (VK_MENU, False), "lalt": (VK_MENU, False),
    "menu": (VK_MENU, False), "leftalt": (VK_MENU, False),
    "ralt": (VK_MENU, True),  "rightalt": (VK_MENU, True),
    "左alt": (VK_MENU, False), "右alt": (VK_MENU, True),
    "ctrl": (VK_CONTROL, False), "control": (VK_CONTROL, False),
    "lctrl": (VK_CONTROL, False), "leftctrl": (VK_CONTROL, False),
    "rctrl": (VK_CONTROL, True),  "rightctrl": (VK_CONTROL, True),
    "shift": (VK_SHIFT, False), "lshift": (VK_SHIFT, False),
    "rshift": (VK_SHIFT, True), "rightshift": (VK_SHIFT, True),
    "win": (0x5B, False), "lwin": (0x5B, False), "rwin": (0x5C, False),
    "space": (0x20, False), "空格": (0x20, False),
}

# 默认发【右 Alt】：微信「发送语音消息」默认绑的就是它
_DEFAULT_HOTKEY = "RAlt"


def _parse_hotkey(spec: str):
    """把 "RAlt" / "LAlt" / "Alt" / "Ctrl+Win" / "0x12" 解析成 [(vk, extended), ...]。"""
    out = []
    for part in (spec or "").replace("+", ",").split(","):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if low in _VK_NAMES:
            out.append(_VK_NAMES[low])
            continue
        try:
            out.append((int(part, 0), False))
        except ValueError:
            logger.warning(
                f"[WeeMessenger - 警告] 看不懂的快捷键片段 {part!r}，已忽略"
                f"（可用 RAlt/LAlt/Alt/Ctrl/Shift/Win，或像 0x11 这样的虚拟键码）"
            )
    return out or [_VK_NAMES[_DEFAULT_HOTKEY.lower()]]


def _fmt_keys(keys):
    """把 [(vk, ext), ...] 打成人看的样子，如 RAlt / LAlt / RWin+LCtrl。
    带左右前缀是为了无歧义，而且这个字符串本身可以直接回填给 DANIYA_VOICE_HOTKEY。"""
    label = {0x5B: "LWin", 0x5C: "RWin", 0x20: "Space"}
    parts = []
    for vk, ext in keys:
        if vk == VK_MENU:
            n = ("R" if ext else "L") + "Alt"
        elif vk == VK_CONTROL:
            n = ("R" if ext else "L") + "Ctrl"
        elif vk == VK_SHIFT:
            n = ("R" if ext else "L") + "Shift"
        else:
            n = label.get(vk, hex(vk))
        parts.append(n)
    return "+".join(parts)


VOICE_HOTKEY = _parse_hotkey(os.environ.get("DANIYA_VOICE_HOTKEY", _DEFAULT_HOTKEY))


def _send_key(vk_code: int, key_up: bool = False, extended: bool = False):
    """通过 SendInput 发送单个键盘事件。

    extended=True 时带上 KEYEVENTF_EXTENDEDKEY —— 这是把 Alt/Ctrl/Shift
    发成【右】边那一个的唯一办法（左右共用同一个 VK 码）。
    """
    flags = (KEYEVENTF_KEYUP if key_up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    inp = _INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUT_UNION(ki=_KEYBDINPUT(
            wVk=vk_code, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0,
        )),
    )
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
    if sent != 1:
        logger.warning(f"[WeeMessenger - 警告] SendInput 返回 {sent}，按键模拟可能未生效")


def _safe_hotkey_hold(hold_seconds: float, lead_in: float = 0.0, on_armed=None,
                      keys=None):
    """
    模拟按住一组快捷键指定时长，然后安全释放。
    使用 try/finally 确保即使异常发生也会松开按键，避免键盘卡死。

    keys: [(vk, extended), ...]，按顺序按下、逆序松开；默认取 VOICE_HOTKEY（右 Alt）。

    【达妮娅懒人包改动 3/3】
    lead_in: 按下快捷键后先等这么多秒（给微信录音流水线启动的时间），
             然后再调用 on_armed —— 用来在这个时刻才开始播放音频，
             避免开头第一个字被吞掉。
    """
    keys = list(keys or VOICE_HOTKEY)
    for vk, ext in keys:
        _send_key(vk, key_up=False, extended=ext)
    logger.info(
        f"[WeeMessenger - 提示] 已按住快捷键 {_fmt_keys(keys)}，"
        f"持续 {hold_seconds:.1f}s ……"
    )
    try:
        if lead_in > 0:
            time.sleep(lead_in)
        if on_armed is not None:
            on_armed()
        time.sleep(max(0.0, hold_seconds - max(lead_in, 0.0)))
    finally:
        for vk, ext in reversed(keys):
            _send_key(vk, key_up=True, extended=ext)
        logger.info("[WeeMessenger - 提示] 已松开快捷键")


class VoiceSender:
    """语音消息发送器，负责音频解码、播放和微信语音输入模拟"""

    def send(self, target_name: str, voice_data_b64: str, voice_format: str = "mp3",
             ensure_chat_fn=None, lock: threading.Lock = None) -> str:
        """
        发送语音消息：
        1. 解码 base64 音频 → 临时文件
        2. 确保聊天窗口已就绪（持有 UI 锁）
        3. 播放音频同时按住「发送语音消息」快捷键（微信 4.1.9+ 默认 Alt）
        4. 松开快捷键完成发送

        ⚠️ 返回值只代表「流程有没有走完」，【不代表微信真的产生了语音条】：
            微信侧到底发没发出去，只能由调用方回读聊天区来判断
            （见 达妮娅/bridge.py 的 Sender.send_voice）。

        返回：
          "ok"       流程走完了（音频已播放、快捷键已按住并松开）
          "no_audio" 音频库不可用，压根没发
          "busy"     没拿到 UI 锁，取消
          "error"    过程抛异常
        """
        # 【达妮娅懒人包】音频是后台初始化的，这里才第一次真正等它 ——
        #   设备正常时几乎不花时间；设备异常时最多等 _INIT_TIMEOUT 秒就放弃，
        #   绝不会把整个发送流程拖住。
        if not ensure_mixer() or pygame is None:
            logger.error(
                "[WeeMessenger - 错误] 音频不可用，无法发送语音。"
                " 常见原因是 WASAPI 初始化失败（系统资源不足）；文字消息不受影响。"
            )
            return "no_audio"

        tmp_path = None
        result = "error"

        # 获取 UI 锁（如果有），保护 ensure_chat_fn 及快捷键操作
        if lock:
            if not lock.acquire(timeout=10.0):
                logger.error("[WeeMessenger - 错误] 获取UI锁超时，取消语音发送")
                return "busy"

        try:
            # 解码音频并保存为临时文件
            audio_bytes = base64.b64decode(voice_data_b64)
            with tempfile.NamedTemporaryFile(suffix=f".{voice_format}", delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(audio_bytes)

            logger.info(f"[WeeMessenger - 提示] 语音文件已临时保存: {tmp_path}")

            # 确保聊天窗口就绪（在锁保护下执行）
            if ensure_chat_fn:
                ensure_chat_fn(target_name)

            # 通过 pygame 加载音频并获取时长
            sound = pygame.mixer.Sound(tmp_path)
            duration = max(sound.get_length(), 0.0)
            logger.info(f"[WeeMessenger - 提示] 音频时长: {duration:.2f} 秒")

            # 微信要求录音至少 2 秒，不足则延长按住时间
            hold_duration = max(duration, MIN_VOICE_DURATION)
            if hold_duration > duration:
                logger.info(
                    f"[WeeMessenger - 提示] 音频时长 {duration:.2f}s 不足 {MIN_VOICE_DURATION}s，"
                    f"[WeeMessenger - 提示] 按住时间延长至 {hold_duration:.2f}s"
                )

            # 【达妮娅懒人包】先按住快捷键开口，等 VOICE_LEAD_IN 秒、
            # 微信录音真正起来之后再开始播放，否则第一个字会被吞掉
            play_box = {}

            def _start_play():
                t = threading.Thread(target=sound.play, daemon=True)
                t.start()
                play_box["thread"] = t

            # 安全模拟按住（异常时自动松开）
            _safe_hotkey_hold(hold_duration + VOICE_LEAD_IN,
                              lead_in=VOICE_LEAD_IN, on_armed=_start_play)
            play_t = play_box.get("thread")
            if play_t is not None:
                play_t.join(timeout=max(duration, hold_duration) + 2.0)

            # 措辞刻意不说"已发送"：按住快捷键只是"敲了门"，
            # 微信开不开门（有没有真的产生语音条）得由调用方回读确认。
            logger.info(
                f"[WeeMessenger - 提示] 语音流程已完成（{target_name}）—— "
                f"快捷键 {_fmt_keys(VOICE_HOTKEY)} 已按下并松开，音频已播完。"
                f"微信端是否真的产生语音条，需由调用方回读聊天区校验。"
            )
            result = "ok"

        except Exception as e:
            logger.error(f"[WeeMessenger - 错误] 发送语音异常: {e}", exc_info=True)
            result = "error"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.warning(f"[WeeMessenger - 警告] 清理临时语音文件失败: {e}")
            if lock:
                lock.release()

        return result
