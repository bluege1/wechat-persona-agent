# -*- coding: utf-8 -*-
"""
达妮娅 · 微信侧自检（收消息）
==============================

回答一个问题：**微信里别人发来的消息，现在读不读得到？**

背景（踩过的坑）：
  微信 PC 从 4.0 起换成 Qt 自绘 UI，默认不把控件树暴露给系统，
  所以老办法（找控件、读控件文本）全部失效。
  社区里流传的「开讲述人再重登微信」在 4.1 之后对新号也已失效。

  本项目改成「截图 + OCR」收消息：不碰控件树，只要微信窗口没被最小化就行。
  本脚本把这条链路从头到尾跑一遍，并把识别到的消息画框存成图片，肉眼可验。

用法：
  python check_wechat.py
"""

import sys
import time
from pathlib import Path

import ocr_reader
import wechat_window

LINE = "=" * 66
from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
def _pause(msg: str = "按回车结束……"):
    """等用户按键。被管道/重定向调用时 input() 会抛 EOFError，这里兜住。"""
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        print()


def main():
    print(LINE)
    print("  达妮娅 · 微信侧自检（收消息）")
    print(LINE)
    print()

    # ── 1. 找窗口 ──
    print("【1】找微信窗口")
    win = wechat_window.find(timeout=5, interval=1.0)
    if win is None:
        print("  [X] 没找到微信窗口。")
        print()
        print("      请确认：")
        print("        · 微信已经打开并且已经登录（小号）")
        print("        · 没有最小化到托盘（点托盘图标把它打开）")
        print("      然后重跑本脚本。")
        print()
        _pause()
        return 1
    print("  [OK] 找到了")
    print(f"       类名 = {win.ClassName}")
    print(f"       标题 = {win.Name!r}   ← 应该是你当前打开的那个会话名")
    print()

    # ── 2. 抓画面 ──
    print("【2】抓取窗口画面")
    img = ocr_reader.capture_window(win)
    if img is None:
        print("  [X] 抓不到画面。")
        print()
        print("      最常见的原因：微信被最小化了。")
        print("      微信必须是「窗口」状态，最小化到托盘就抓不到了。")
        print("      （不过被其它窗口挡住没关系，后台一样能抓。）")
        print()
        _pause()
        return 2
    print(f"  [OK] 抓到 {img.size[0]}x{img.size[1]} 的画面")
    print()

    # ── 3. 定位聊天区 ──
    print("【3】在画面里定位聊天区")
    geo = ocr_reader.detect_geometry(img)
    print(f"  [OK] {geo}")
    print("       （要框住右边那一大块消息区域，不含左边的会话列表）")
    print()

    # ── 4. 识别 ──
    print("【4】识别聊天区里的消息")
    print("      第一次会加载 OCR 模型，等两三秒……")
    t0 = time.time()
    try:
        bubbles = ocr_reader.read_bubbles(img, geo)
    except Exception as e:
        print(f"  [X] 识别失败：{e}")
        print("      如果是缺依赖，跑一下：")
        print("      .venv\\Scripts\\python.exe -m pip install rapidocr-onnxruntime Pillow")
        print()
        _pause()
        return 4
    dt = time.time() - t0
    print(f"  [OK] 用时 {dt:.1f} 秒，识别到 {len(bubbles)} 条消息")
    print()

    if bubbles:
        print("       最近几条（“对方”= 微信里别人发的，“我”= 自己发的）：")
        for b in bubbles[-12:]:
            who = "我" if b.mine else "对方"
            print(f"         [{who:>2}] {b.text[:62]}")
        print()

    # ── 5. 存标注图 ──
    log_dir = BASE / "日志"          # 统一放在 D:\wechat-persona-agent\日志
    log_dir.mkdir(exist_ok=True)
    out_png = log_dir / "消息自检.png"
    full_png = log_dir / "消息自检-整屏.png"
    try:
        ocr_reader.annotate(img, geo, bubbles).save(full_png)
        img.crop((geo.left, geo.top, geo.right, geo.bottom)).save(out_png)
    except Exception as e:
        print(f"  [!] 标注图保存失败：{e}")
    else:
        print(f"       标注图已存：{full_png.name}（红框=聊天区，绿框=对方消息，蓝框=我发的）")
        print()

    # ── 6. 结论 ──
    incoming = [b for b in bubbles if not b.mine and not ocr_reader.is_noise(b.text)]
    print(LINE)
    if incoming:
        last = max(incoming, key=lambda b: b.y1)
        print("  [结论] 能读到消息，微信侧就绪。")
        print()
        print(f"          当前最后一条对方消息：{last.text[:56]}")
        print()
        print("          下一步：双击「2-改配置.bat」填 API Key 和对方名字，")
        print("                  再双击「一键启动.bat」。")
        ok = True
    elif bubbles:
        print("  [!] 能识别文字，但没认出「对方发来的消息」。")
        print()
        print("      原因通常是：当前聊天区里只有你自己发的消息。")
        print("      用大号给小号发一条文字，再跑一次本自检。")
        print()
        print("      如果还是认不出，把「日志\\消息自检-整屏.png」发给我。")
        ok = False
    else:
        print("  [!] 一个字都没识别到。")
        print()
        print("      请确认：")
        print("        · 微信窗口是打开的（不是最小化）")
        print("        · 当前会话里有聊天气泡")
        print("      然后把「日志\\消息自检-整屏.png」发给我。")
        ok = False
    print(LINE)
    print()

    _pause()
    return 0 if ok else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
