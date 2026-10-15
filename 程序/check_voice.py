# -*- coding: utf-8 -*-
r"""
达妮娅 · 语音体检
=================

2026-09-20 实测踩到的坑，这个脚本就是用来把它们一次性照出来的：

  坑 1  微信 4.1.9 之前，电脑端**根本不能发语音**（只能收）。
        版本不够的话，按什么快捷键都不会有语音条。

  坑 2  「发送语音消息」的快捷键默认是 Alt，可以在
        微信 → 左下角≡ → 设置 → 快捷键 → 「发送语音消息」 里改。
        一旦它不是 Alt，程序按住 Alt 就是【毫无反应】，
        但旧版代码照样打印"语音消息已发送" —— 语音条凭空消失。
        （实测：微信在前台、Alt 按住 1.6 秒，界面像素差异 0.0000）

  坑 3  微信要把 CABLE Output 当麦克风才录得到声音。

用法：
  python check_voice.py            只做环境检查，不动鼠标不抢前台
  python check_voice.py --probe    额外实测一次「按住快捷键」有没有反应
                                   （会短促抢一下前台，约 4 秒）
  python check_voice.py --send     真的发一条测试语音，并回读聊天区确认
                                   （需要小信使已经在跑、微信窗口开着）

退出码：0 = 就绪，1 = 有问题
"""

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
except Exception:
    pass

from 角色 import ROOT, BASE, PROG, current   # 惰性 ROOT：指向"当前角色"的数据包
OK, BAD, WARN = "[OK]", "[X] ", "[!] "

# 电脑端发语音是从 4.1.9 开始的
MIN_VER = (4, 1, 9)

# 必须和 小信使/src/voice_sender.py 的 _VK_NAMES 保持一致。
# Alt/Ctrl/Shift 的左右是【同一个虚拟键码】，靠 KEYEVENTF_EXTENDEDKEY 区分，
# 所以这里存 (vk, extended) 二元组。
VK_MENU, VK_CONTROL, VK_SHIFT = 0x12, 0x11, 0x10
VK_NAMES = {
    "alt": (VK_MENU, False), "lalt": (VK_MENU, False), "leftalt": (VK_MENU, False),
    "ralt": (VK_MENU, True), "rightalt": (VK_MENU, True),
    "左alt": (VK_MENU, False), "右alt": (VK_MENU, True),
    "ctrl": (VK_CONTROL, False), "control": (VK_CONTROL, False),
    "lctrl": (VK_CONTROL, False), "rctrl": (VK_CONTROL, True),
    "shift": (VK_SHIFT, False), "lshift": (VK_SHIFT, False), "rshift": (VK_SHIFT, True),
    "win": (0x5B, False), "lwin": (0x5B, False), "rwin": (0x5C, False),
    "space": (0x20, False), "空格": (0x20, False),
}
DEFAULT_HOTKEY = "RAlt"


def _parse_hotkey_env(spec: str):
    out = []
    for part in (spec or "").replace("+", ",").split(","):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if low in VK_NAMES:
            out.append(VK_NAMES[low])
            continue
        try:
            out.append((int(part, 0), False))
        except ValueError:
            print(f"  {WARN}环境变量 DANIYA_VOICE_HOTKEY 里看不懂 {part!r}，已忽略")
    return out or [VK_NAMES[DEFAULT_HOTKEY.lower()]]


def _fmt_keys(keys):
    """打成人看的样子（LAlt/RAlt/LCtrl+LWin），且这个字符串能直接回填给
    DANIYA_VOICE_HOTKEY —— 和 小信使/src/voice_sender.py 保持一致。"""
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


HOTKEY_KEYS = _parse_hotkey_env(os.environ.get("DANIYA_VOICE_HOTKEY", DEFAULT_HOTKEY))


# ══════════════════════════════════════════════════════════════
# 微信版本
# ══════════════════════════════════════════════════════════════

class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [("dwSignature", ctypes.c_ulong), ("dwStrucVersion", ctypes.c_ulong),
                ("dwFileVersionMS", ctypes.c_ulong), ("dwFileVersionLS", ctypes.c_ulong),
                ("dwProductVersionMS", ctypes.c_ulong), ("dwProductVersionLS", ctypes.c_ulong),
                ("dwFileFlagsMask", ctypes.c_ulong), ("dwFileFlags", ctypes.c_ulong),
                ("dwFileOS", ctypes.c_ulong), ("dwFileType", ctypes.c_ulong),
                ("dwFileSubtype", ctypes.c_ulong), ("dwFileDateMS", ctypes.c_ulong),
                ("dwFileDateLS", ctypes.c_ulong)]


def exe_version(path: str):
    """读 exe 的文件版本，返回 (版本号字符串, 版本元组)。读不到返回 ("", ())"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return "", ()
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return "", ()
        ptr = ctypes.c_void_p()
        ln = ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
            return "", ()
        info = ctypes.cast(ptr, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        t = (ms >> 16) & 0xFFFF, ms & 0xFFFF, (ls >> 16) & 0xFFFF, ls & 0xFFFF
        return ".".join(map(str, t)), t
    except Exception:
        return "", ()


# ══════════════════════════════════════════════════════════════
# 快捷键探测
# ══════════════════════════════════════════════════════════════

class _KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_ulonglong)]


class _MI(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_ulonglong)]


class _HI(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _U(ctypes.Union):
    _fields_ = [("mi", _MI), ("ki", _KI), ("hi", _HI)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _U)]


def _key(vk: int, up=False, extended=False):
    """投一次键盘事件。注意：结构体必须是 40 字节，写错会静默失败（返回 0）。

    extended=True 才发得出【右】Alt / 右 Ctrl / 右 Shift —— 它们和左边共用
    同一个 VK 码，只有 KEYEVENTF_EXTENDEDKEY(0x0001) 这一位能区分。
    """
    assert ctypes.sizeof(_INPUT) == 40, "INPUT 结构体必须是 40 字节"
    flags = (0x0002 if up else 0) | (0x0001 if extended else 0)
    inp = _INPUT(type=1, union=_U(ki=_KI(
        wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)))
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _mic_privacy():
    """读注册表看 Windows 有没有禁止桌面应用使用麦克风。返回 {hive: "Allow"/"Deny"}"""
    import winreg
    out = {}
    for hive, tag in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                      (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        path = (r"Software\Microsoft\Windows\CurrentVersion"
                r"\CapabilityAccessManager\ConsentStore\microphone")
        try:
            with winreg.OpenKey(hive, path) as k:
                v, _ = winreg.QueryValueEx(k, "Value")
                out[tag] = str(v)
        except Exception:
            pass
    return out


def _hold_and_diff(win, keys, hold=1.8, tag="probe"):
    """按住 keys 一段时间，返回输入区的像素变化比例。"""
    before = _grab(win, f"{tag}-1-按住前")
    for vk, ext in keys:
        _key(vk, up=False, extended=ext)
    time.sleep(hold)
    mid = _grab(win, f"{tag}-2-按住中")
    for vk, ext in reversed(keys):
        _key(vk, up=True, extended=ext)
    time.sleep(1.2)
    return _diff(before, mid)


def _grab(win, tag):
    from PIL import ImageGrab
    r = win.BoundingRectangle
    img = ImageGrab.grab(bbox=(int(r.left), int(r.top), int(r.right), int(r.bottom)),
                         all_screens=True)
    if tag:
        d = os.path.join(ROOT, "日志")
        os.makedirs(d, exist_ok=True)
        img.save(os.path.join(d, f"语音体检-{tag}.png"))
    return img


def _diff(a, b, bottom_ratio=0.70):
    """比较两张图【下半部分】的像素差异（语音录音条就出现在输入框那里）"""
    if a is None or b is None or a.size != b.size:
        return -1.0
    W, H = a.size
    y0 = int(H * bottom_ratio)
    da = a.crop((0, y0, W, H)).convert("L").tobytes()
    db = b.crop((0, y0, W, H)).convert("L").tobytes()
    return sum(1 for x, y in zip(da, db) if abs(x - y) > 25) / max(1, len(da))


def _check_voice_engine(problems):
    """【3】音色：当前角色用谁的声音，素材齐不齐。

    edge  = 微软内置音色（不是她本人），只要提示一句就够了。
    local = 用 音色\\ 里的素材，逐项检查：参考音频 / 参考文字 / 权重 / 引擎。
    """
    print()
    print("【3】音色（%s 用谁的声音）" % (current() or "她"))

    try:
        import yaml
        with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"  {WARN} 读不了 config.yaml：{e}")
        return

    v = cfg.get("voice") or {}
    eng = str(v.get("engine") or "edge").strip().lower()
    voice_dir = os.path.join(ROOT, "音色")
    local_engine = eng in ("local", "gptsovits", "gpt-sovits", "gsv", "clone", "本地", "自定义")

    print(f"  voice.engine = {eng}")

    if not local_engine:
        print(f"  {WARN} 现在用的是微软 Edge-TTS 的内置音色：{v.get('edge_voice')}")
        print("       那不是她本人的声音，只是随便一个播音女声。")
        print("       想要她自己的音色：把语音模型放进下面这个文件夹，")
        print("       再把 config.yaml 里 voice.engine 改成 local。")
        print(f"       素材文件夹：{voice_dir}")
        return

    def _p(raw):
        q = str(raw or "").strip()
        if not q:
            return None
        return q if os.path.isabs(q) else os.path.join(ROOT, q)

    loc = v.get("local") or {}

    # ── 参考音频 ──
    ref = _p(loc.get("ref_audio") or os.path.join("音色", "%s.wav" % (current() or "voice")))
    if ref and os.path.isfile(ref):
        print(f"  {OK} 参考音频：{os.path.basename(ref)}（{os.path.getsize(ref) / 1024:.0f} KB）")
        try:
            import wave
            with wave.open(ref) as w:
                sec = w.getnframes() / float(w.getframerate() or 1)
            if 3 <= sec <= 10:
                print(f"  {OK} 时长 {sec:.1f} 秒（3~10 秒正合适）")
            else:
                print(f"  {WARN} 时长 {sec:.1f} 秒 —— 建议 3~10 秒，太长太短都会走味")
        except Exception:
            print("       （不是 wav，或读不出时长；mp3 也能用，这里跳过）")
    else:
        print(f"  {BAD} 找不到参考音频：{ref}")
        print("       把%s说话的 3~10 秒切片放进去，命名成 %s.wav"
              % (current() or "她", current() or "voice"))
        problems.append("参考音频缺失")

    # ── 参考文字 ──
    if str(loc.get("ref_text") or "").strip():
        print(f"  {OK} 参考文字：已在 config.yaml 里填了")
    else:
        side = (ref[:-4] + ".txt") if ref and ref.lower().endswith(".wav") else None
        if side and os.path.isfile(side):
            print(f"  {OK} 参考文字：自动读 {os.path.basename(side)}")
        else:
            print(f"  {WARN} 没填参考文字（voice.local.ref_text），也没找到同名 .txt")
            print("       填上更稳，建议补一句「音频里说的那句话」")

    # ── 模型权重（可选） ──
    for key, label in (("gpt_weights", "GPT .ckpt"), ("sovits_weights", "SoVITS .pth")):
        raw = str(loc.get(key) or "").strip()
        if not raw:
            continue
        p = _p(raw)
        if p and os.path.isfile(p):
            print(f"  {OK} 模型权重（{label}）：{os.path.basename(p)}")
        else:
            print(f"  {BAD} 模型权重（{label}）找不到：{p}")
            problems.append(f"模型权重缺失({label})")

    # ── 本地引擎通不通 ──
    api = str(loc.get("api") or "http://127.0.0.1:9880").strip().rstrip("/")
    print(f"  本地引擎地址：{api}")
    try:
        import urllib.request
        with urllib.request.urlopen(api + "/docs", timeout=3) as r:
            _ = r.status
        print(f"  {OK} 引擎在跑（{api}/docs 有响应）")
    except Exception as e:
        print(f"  {BAD} 连不上本地引擎：{e.__class__.__name__}")
        print("       先把 GPT-SoVITS 的 api_v2.py 跑起来，")
        print(f"       步骤见 {voice_dir}\\说明.txt 的第三步")
        problems.append("本地语音引擎没启动")

    # ── 文件夹里都有啥 ──
    print()
    print(f"  素材文件夹：{voice_dir}")
    if os.path.isdir(voice_dir):
        names = [n for n in sorted(os.listdir(voice_dir)) if n != "说明.txt"]
        print(f"  里面现有：{'、'.join(names) if names else '（空的，还没放东西）'}")


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def main():
    probe = "--probe" in sys.argv
    do_send = "--send" in sys.argv
    problems = []

    print("=" * 66)
    print("  达妮娅 · 语音体检")
    print("=" * 66)

    # ── 1. 微信版本与窗口 ──
    print()
    print("【1】微信")
    import wechat_window
    win = wechat_window.find(timeout=3.0)
    if win is None:
        print(f"  {WARN}没找到微信窗口 —— 可能被最小化到托盘了，先把它打开成窗口。")
        problems.append("微信窗口不可见")
    else:
        exe = wechat_window.pid_image_name(win.ProcessId)
        print(f"  {OK} 窗口已就绪  类名={win.ClassName}  标题={win.Name!r}")
        if exe:
            v, t = exe_version(exe)
            if t:
                if t[:3] >= MIN_VER:
                    print(f"  {OK} 微信版本 {v}  ≥ 4.1.9，支持电脑端发语音")
                else:
                    print(f"  {BAD} 微信版本 {v}  < 4.1.9 —— 这个版本【电脑端不能发语音】！")
                    print("        去官网下载新版覆盖安装即可。")
                    problems.append("微信版本过低")
            else:
                print(f"  {WARN}读不到版本号，程序路径：{exe}")
                print("        手动确认：微信 → 左下角≡ → 设置 → 关于微信 → 要 ≥ 4.1.9")
        else:
            print(f"  {WARN}读不到微信 exe 路径")

    # ── 2. 虚拟声卡 ──
    print()
    print("【2】虚拟声卡（微信从这里「录」到达妮娅的声音）")
    try:
        from audio_device import (E_CAPTURE, E_RENDER, ROLE_NAMES,
                                  alive_endpoints, current_default, registry_endpoints)

        def name_of(flow, did):
            for d, n, _ in registry_endpoints(flow):
                if d.lower() == str(did).lower():
                    return n
            return ""

        renders = alive_endpoints(E_RENDER)
        caps = alive_endpoints(E_CAPTURE)
        cin = [x for x in renders if "cable" in x[1].lower() and "input" in x[1].lower()]
        cout = [x for x in caps if "cable" in x[1].lower() and "output" in x[1].lower()]
        print(f"  {OK if cin else BAD} 播放端 CABLE Input {'在' if cin else '缺失'}")
        print(f"  {OK if cout else BAD} 录音端 CABLE Output {'在' if cout else '缺失'}")
        if not (cin and cout):
            problems.append("虚拟声卡缺失")
        role_ok = True
        for role, rn in enumerate(ROLE_NAMES):
            did = current_default(E_CAPTURE, role)
            nm = name_of(E_CAPTURE, did)
            if "CABLE Output" in nm:
                print(f"  {OK} 默认录音设备[{rn}] -> {nm}")
            else:
                print(f"  {BAD} 默认录音设备[{rn}] -> {nm or did}  （必须是 CABLE Output）")
                role_ok = False
        if not role_ok:
            problems.append("默认录音设备不是 CABLE Output")
    except Exception as e:
        print(f"  {WARN}声卡检查失败：{e}")

    # ── 2b. Windows 麦克风隐私开关（很多人栽在这里）──
    print()
    print("【2b】Windows 麦克风权限（关掉的话微信根本录不了音）")
    res = _mic_privacy()
    if not res:
        print(f"  {WARN}读不到隐私设置，跳过。")
    else:
        for hive, val in res.items():
            if val.lower() == "allow":
                print(f"  {OK} {hive}：允许桌面应用访问麦克风")
            else:
                print(f"  {BAD} {hive}：{val} —— 这里被关了，微信读不到麦克风！")
                problems.append("Windows 麦克风权限被关")
        if any(v.lower() != "allow" for v in res.values()):
            print("        打开：设置 → 隐私和安全性 → 麦克风 →")
            print("              「让桌面应用访问你的麦克风」→ 开")

    # ── 3. 音色 ──
    _check_voice_engine(problems)

    # ── 4. 快捷键 ──
    print()
    print("【4】「发送语音消息」快捷键")
    print(f"  程序现在按的键： {_fmt_keys(HOTKEY_KEYS)}")
    print()
    print("  ⚠ 先别和「语音输入文字」搞混，这是两个不同的功能：")
    print("      · 语音输入文字 = 4.1.8 的，按住 Ctrl+Win 或点【输入框下方工具栏】")
    print("        里那个话筒，产物是【文字】。")
    print("      · 发送语音消息 = 4.1.9 的，点【聊天框右下角、发送按钮旁边】那个")
    print("        小话筒，或按住 Alt，产物是【语音条】。我们要的是这个。")
    print()
    print("  去哪儿对：微信 → 左下角≡ → 设置 → 快捷键 → 「发送语音消息」")

    if probe and win is not None:
        print()
        print("【5】实测：哪个键能让微信进录音态")
        print("      （会抢一下前台，每个键约 4 秒，请先别动键盘鼠标）")
        time.sleep(3)
        wechat_window.activate(win)
        time.sleep(0.8)

        # 候选：当前配置的键 + 左 Alt + 右 Alt（去重，按顺序测）
        cands = []
        for label, keys in (("当前配置(" + _fmt_keys(HOTKEY_KEYS) + ")", HOTKEY_KEYS),
                            ("左 Alt", [(VK_MENU, False)]),
                            ("右 Alt", [(VK_MENU, True)])):
            if keys not in [k for _, k in cands]:
                cands.append((label, keys))

        results = []
        for label, keys in cands:
            wechat_window.activate(win)
            time.sleep(0.5)
            d = _hold_and_diff(win, keys, hold=1.8, tag="probe-" + label.replace(" ", ""))
            results.append((label, keys, d))
            print(f"    {label:<22} 输入区变化 = {d:.4f}")

        good = [(l, k) for l, k, d in results if d > 0.04]
        if good:
            label, keys = good[0]
            print()
            print(f"  {OK} 【{label}】有效 —— 微信进录音态了，语音条能发出去。")
            if _fmt_keys(keys) != _fmt_keys(HOTKEY_KEYS):
                print(f"        把程序也改成这个键：在【小信使】的启动脚本里加一行")
                print(f'            set "DANIYA_VOICE_HOTKEY={_fmt_keys(keys)}"')
                print(f"        或者干脆在微信 设置→快捷键 里改成 {_fmt_keys(HOTKEY_KEYS)}。")
        elif all(d >= 0 for _, _, d in results):
            print()
            print(f"  {BAD} 三个键按住时界面【一点没变】—— 微信压根没进录音态。")
            print("        说明问题不在快捷键上，更可能是：")
            print("        · 微信「设置 → 语音视频 → 麦克风」没选 CABLE Output；")
            print("        · 或者这个微信版本把录音入口挪走了（右键输入框区域看看）；")
            print("        · 上一条【2b】的麦克风权限检查没过。")
            problems.append("快捷键无效")
            print(f"        截图在 {os.path.join(ROOT, '日志')}\\语音体检-probe-*.png")
        else:
            print(f"  {WARN} 抓图失败，没法判定。")
    elif probe:
        print(f"  {WARN} 微信窗口不在，跳过实测。")

    # ── 5. 真发一条 ──
    if do_send:
        print()
        print("【6】真发一条测试语音（需要小信使在跑）")
        try:
            import bridge
            cfg = bridge.load_config()
            sender = bridge.Sender(cfg)
            if not sender.health():
                print(f"  {BAD} 连不上小信使（{sender.url}）——先双击小信使的启动脚本。")
                problems.append("小信使没在跑")
            else:
                target = cfg["target"]
                text = "[语音体检] 这是一条测试语音，收到就说明语音条能发出去。"
                mp3 = sender.tts(text)
                print(f"  已合成本地语音：{mp3.name}")
                ok = sender.send_voice(target, mp3)
                if ok:
                    print(f"  {OK} 语音条已发出并回读确认 —— 语音功能正常！")
                else:
                    print(f"  {BAD} 语音没发出去（聊天区里没出现新语音气泡）。")
                    problems.append("语音条发不出去")
        except Exception as e:
            print(f"  {WARN} 发送测试失败：{e}")
            problems.append("发送测试异常")

    # ── 结论 ──
    print()
    print("=" * 66)
    if not problems:
        print("  [结论] 语音链路就绪。")
    else:
        print("  [结论] 发现这些问题： " + "、".join(dict.fromkeys(problems)))
        print()
        print("  常见修法：")
        print("    · 版本 < 4.1.9  → 装新版微信（电脑端发语音是 4.1.9 才有的）")
        print("    · 快捷键对不上  → 看上面【5】实测结果，哪个键有反应就用哪个：")
        print('                      set "DANIYA_VOICE_HOTKEY=RAlt"  （右 Alt，默认）')
        print('                      set "DANIYA_VOICE_HOTKEY=LAlt"  （左 Alt）')
        print('                      set "DANIYA_VOICE_HOTKEY=Ctrl+Win"（语音输入那个键）')
        print("    · 麦克风权限    → 设置 → 隐私和安全性 → 麦克风 → 让桌面应用访问")
        print("    · 录音设备不对  → 微信 → 设置 → 语音视频 → 麦克风 → CABLE Output")
        print("    · 音色素材缺失  → 见 音色\\说明.txt，把参考音频放进去，")
        print("                      再把 config.yaml 里 voice.engine 改成 local")
        print("    · 本地引擎没起  → 先跑 GPT-SoVITS 的 api_v2.py（默认 9880 端口）")
        print("    · 就是不想折腾  → 把 config.yaml 里 voice.enabled 改成 false，")
        print("                      达妮娅就只发文字，纯后台、零干扰。")
    print("=" * 66)
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(2)
    except Exception as e:
        print(f"[X] 体检脚本本身出错了：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
