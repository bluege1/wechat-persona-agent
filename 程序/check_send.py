# -*- coding: utf-8 -*-
"""
达妮娅 · 后台发送自检
======================
验证「不抢焦点、不动鼠标」的后台发送到底通不通。

它会：
  1. 找到微信窗口
  2. OCR 认出当前打开的是哪个会话
  3. 和 config.yaml 里的 target 比对 —— 不一致就拒绝发送（防止发错人）
  4. 一致的话，后台把一条测试消息发出去
  5. 再截一次图 OCR 复核，确认消息真的出现在聊天里

⚠️ 这会真的发出一条消息（内容带「[测试]」字样，一看就知道是测试）。
   只会在当前会话 == target 时才发。

用法：
    python check_send.py            发一条测试消息并复核
    python check_send.py --dry      只检查环境和会话，不发送
"""

import sys
from pathlib import Path

import yaml

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
except Exception:
    pass

import ocr_reader
import wechat_window
import wx_input

TEST_TEXT_TMPL = "[测试 {t}] 达妮娅后台发送测试，可忽略"


def main() -> int:
    dry = "--dry" in sys.argv

    print("=" * 62)
    print("达妮娅 · 后台发送自检")
    print("=" * 62)
    print()

    cfg_file = ROOT / "config.yaml"
    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    target = str(cfg.get("target", "")).strip()
    if not target:
        print("[X] config.yaml 里的 target 是空的（要填你大号在小号里的显示名）")
        return 1
    print(f"目标会话（config.yaml）: {target!r}")

    win = wechat_window.find()
    if not win:
        print("[X] 没找到微信窗口。确认微信已登录、没有缩到托盘。")
        return 1

    # ── 1. 微信必须在前台吗？不需要。这里故意不激活它 ──
    fg = ctypes_get_foreground()
    try:
        hwnd = win.NativeWindowHandle
    except Exception:
        hwnd = 0
    print(f"微信窗口句柄            : {hwnd}")
    print(f"微信当前在前台吗        : {'是' if fg == hwnd else '否（正好用来验证后台发送）'}")
    print()

    img = ocr_reader.capture_window(win)
    if img is None:
        print("[X] 抓不到微信画面。确认窗口没有最小化到托盘。")
        return 1
    geo = ocr_reader.detect_geometry(img)
    print(f"窗口尺寸                : {img.size}")
    print(f"聊天区                  : {geo}")
    print("正在识别当前打开的会话名……")

    current = wx_input.current_chat(win)
    print(f"当前打开的会话          : {current!r}")
    print()

    if not current:
        print("[!] 认不出当前会话名。")
        print("    把微信窗口开大一点、切到有聊天气泡的会话，再跑一次。")
        return 1

    if not wx_input.chat_matches(current, target):
        print(f"[X] 当前打开的是「{current}」，不是目标「{target}」。")
        print()
        print("    后台发送只能操作「当前已经打开的那个会话」——")
        print("    微信在画布模式下没暴露控件树，脚本没法自己切换会话。")
        print()
        print("    请手动在微信里点开和「%s」的聊天，然后再跑一次本自检。" % target)
        return 1

    print("[OK] 当前会话就是目标会话。")
    print()

    if dry:
        print("（--dry 模式，不发送。环境检查通过。）")
        return 0

    # 测试文本必须带时间戳：否则和上一次跑出来的同一条消息长得一模一样，
    # OCR 复核时会拿旧消息当新的，报出假的成功。
    import time as _t
    test_text = TEST_TEXT_TMPL.format(t=_t.strftime("%H:%M:%S"))

    print("-" * 62)
    print("正在后台发送测试消息……")
    print(f"  内容: {test_text}")
    print("-" * 62)

    sent = wx_input.send(win, test_text)

    if not sent:
        print()
        print("[X] 后台投递没有生效：字进了输入框又没了 / 根本没进去。")
        print()
        print("    原因基本只有一个：微信的「焦点输入控件」丢了。")
        print("    微信被最小化到托盘、或者刚被恢复出来还没被点过，就会这样——")
        print("    Qt 程序在窗口没有被激活过的时候，会把投给它的字符静静丢掉。")
        print()
        print("    怎么修（选一个）：")
        print("      · 在微信窗口上点一下（点进输入框最好），然后重跑本自检")
        print("      · 别把微信最小化到托盘，让它留在桌面上")
        print()
        print("    另外：「一键启动.bat」跑起来之后，第一次发送会自动退回前台，")
        print("    那一下就把微信激活了 —— 之后所有文字就都是后台的。")
        return 1

    if wx_input.last_own_message(win, test_text, geo=geo):
        print()
        print("[OK] 后台发送成功，消息已经出现在聊天里。")
        print("     全程微信没有跳到前台，鼠标也没有动过。")
        return 0

    print()
    print("[!] 输入框回读通过了，但聊天区没认到这条消息。")
    print("    看一眼微信窗口就能确认到底发出去没有。")
    return 1


def ctypes_get_foreground():
    import ctypes
    return ctypes.windll.user32.GetForegroundWindow()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(1)
