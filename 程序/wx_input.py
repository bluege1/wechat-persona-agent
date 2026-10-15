# -*- coding: utf-8 -*-
"""
微信 · 后台输入模块
====================
不抢焦点、不动鼠标，直接把文字送进当前会话并发送。

为什么需要它：
  常规做法是 SetForegroundWindow + SendInput(Ctrl+V / Enter)，
  这会把微信窗口抢到最前面 —— 你在别的程序里打字会被硬生生打断。

  微信 4.x 是 Qt 程序，Qt 的按键处理走的是窗口消息，所以
  **PostMessage(WM_CHAR) 可以直接把字符投递进去，不需要窗口在前台**。
  实测（2026-09-20，微信 4.1.15.9 / Qt51514QWindowIcon）：

    PostMessage(WM_CHAR)      可用   ← 中文、英文都能进
    PostMessage(WM_KEYDOWN)   可用   但会和 WM_CHAR 叠加成重复字符，别用
    PostMessage(WM_PASTE)     不可用  Qt 不处理这条
    退格(WM_KEYDOWN VK_BACK)  可用   ← 用来清空输入框

  ⚠️ 重要前提：只能操作**当前已经打开的那个会话**。
  微信没有曝露控件树（画布模式），所以「切换到目标会话」这件事
  仍然只能靠前台的 Ctrl+F 搜索。本模块的做法是：
  先 OCR 认出当前会话名，只有和目标一致时才走后台通道，
  否则交回原来的前台流程 —— 绝不会把消息发错人。

⚠️ 使用前必须确认当前会话名。窗口标题（GetWindowText）**不是**会话名
   （实测标题是登录账号的昵称，比如「爱弥斯」），
   真正的会话名在聊天区上方那条标题带里，只能靠 OCR 读。

★ 2026-09-21 实录：她发的「眼睛是紫的 你猜猜」，微信里显示成「睛是紫的 你猜猜」——
  第一个字被吞了。查了一圈：
    · memory.json 里存的是完整的（清洗那一段没问题）
    · 逐字投递本身也没问题（对照实验 8/8、40/40 一字不差）
    · 真正的问题是【回读校验太松】：原来是「前 10 个字都在」或者「整段相似度 > 0.5」
      就算过。少一个字的相似度是 0.933，远大于 0.5 —— 于是丢字的句子被放行、回车发了出去。
  现在改成【首字锚定】（见 _landed）：必须从第一个字起就对得上；
  对不上就清干净重投一次，两次都不行就交回前台那条原子粘贴的路。
  另外第一个字单独多留一点时间（FIRST_GAP），清完输入框先等一等（SETTLE）。
"""

import ctypes
import os
import re
import time

import ocr_reader

u32 = ctypes.windll.user32

WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_END = 0x23         # 光标顶到行尾 —— 清空输入框前必须先按它（2026-10-05）

# 相邻键/字符之间的间隔。太小微信会丢字，太大发送变慢。
CHAR_GAP = 0.02
KEY_GAP = 0.05
FIRST_GAP = 0.06     # 第一个字多留一点 —— 它是最容易被吞的那个（2026-09-21）
BACK_GAP = 0.02      # 退格之间的间隔（原 0.01 太密：一秒砸 120 条按键消息）
SETTLE = 0.30        # 清空之后、投第一个字之前：等 Qt 把那一串退格啃完
POST_TYPE = 0.35     # 投完到回读之间的等待

# 输入框读回时要去掉的界面文字：
#   「发送」是右下角那个按钮；另一串是空框时的灰字提示。
#   ★ 短消息（「嗯」「好」）一混进「发送」两个字，回读就没法用来判断了（2026-09-21 实录）。
UI_NOISE = ("发送", "按住鼠标语音输入文字")


def post(hwnd, msg, wp=0, lp=0):
    return u32.PostMessageW(ctypes.c_void_p(int(hwnd)), msg, wp, lp)


def _norm(s: str) -> str:
    """把名字归一化再比较：去空白、统一全角、去掉表情和不可见字符。"""
    s = str(s or "").strip()
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", "", s)
    # 去掉 emoji 和各类符号，只留下文字和数字，避免「对方昵称😄」这类匹配失败
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    return s.lower()


def _is_noise_item(s: str) -> bool:
    """这一条 OCR 结果是界面上的字（按钮 / 灰字提示）而不是输入框里的内容吗？

    灰字提示偶尔只被读成半截（「按住鼠标」「语音输入文字」），
    所以判据是「归一化之后是提示语的一部分」，而不是逐字相等。
    真打的字里出现「按住…输入文字」整串的可能性可以忽略。
    """
    n = _norm(s)
    if not n:
        return True
    if n == _norm(UI_NOISE[0]):          # 「发送」按钮
        return True
    return len(n) >= 3 and n in _norm(UI_NOISE[1])


def current_chat(win) -> str:
    """
    读当前会话名。只能靠 OCR：截图 → 裁聊天区上方的标题带 → 识别 → 取最长的一条。

    注意不做缓存：用户随时可能切换会话，每次发送前都要重新读一遍。
    """
    img = ocr_reader.capture_window(win)
    if img is None:
        return ""
    geo = ocr_reader.detect_geometry(img)

    # 标题带：聊天区左边界的右侧，聊天区上边界之上
    left = int(geo.left) + 4
    strip = img.crop((left, 16, min(left + 420, img.width), int(geo.top) - 4))
    res, _ = ocr_reader.engine()(strip)
    if not res:
        return ""

    cands = []
    for item in res:
        text = (item[1] or "").strip()
        score = float(item[2]) if len(item) > 2 else 0.0
        if _norm(text):
            cands.append((score, text))
    if not cands:
        return ""
    cands.sort(key=lambda x: (-x[0], -len(x[1])))
    return cands[0][1]


def chat_matches(current: str, target: str) -> bool:
    """当前会话名和目标名是否算同一个（互相包含即可，容忍表情/空格差异）。"""
    a, b = _norm(current), _norm(target)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def clear_input(hwnd, times: int = 60, gap: float = BACK_GAP):
    """用退格键清空输入框（后台）。发送前兜底，避免把上次的残留一起发出去。

    ★ 间隔原来是 0.01 —— 120 条按键消息几乎一秒全砸进去，太密了（2026-09-21 调成 0.02）。

    ★ 2026-10-05 补：先按一下 **End** 把光标顶到行尾。
      退格只删光标**左边**的字 —— 光标要是停在行首（上一条前台粘贴留下的），
      砸六十下退格一个都删不掉，紧接着 type_text 往里投字，新字就会
      **接在前一条前面**，微信里冒出一条缝合怪：
      「吹得我头发乱乱的刚从天台下来 风好大」（用户 2026-10-05 截图实证）。
      先把光标挪到行尾，退格才真的清得干净。
    """
    post(hwnd, WM_KEYDOWN, VK_END)
    post(hwnd, WM_KEYUP, VK_END)
    time.sleep(0.05)
    for _ in range(times):
        post(hwnd, WM_KEYDOWN, VK_BACK)
        post(hwnd, WM_KEYUP, VK_BACK)
        time.sleep(gap)


def type_text(hwnd, text: str, gap: float = CHAR_GAP):
    """
    把文本逐字符投进输入框（后台）。

    走 WM_CHAR，wParam 是 UTF-16 码元 —— 中文直接用码点就行，不需要剪贴板。
    换行统一成回车事件（WM_CHAR 送 \n 在微信里不会换行）。

    ★ 第一个字单独多留一点时间（FIRST_GAP）：刚清完输入框那会儿，
      按键队列里还压着一串退格，第一个字是最容易被吞掉的（2026-09-21 实录）。
    """
    for i, ch in enumerate(text):
        if ch == "\n":
            press(hwnd, VK_RETURN)
        else:
            post(hwnd, WM_CHAR, ord(ch))
        time.sleep(FIRST_GAP if i == 0 else gap)


def press(hwnd, vk: int):
    """后台按一下某个键（按下+抬起）。"""
    post(hwnd, WM_KEYDOWN, vk)
    time.sleep(0.02)
    post(hwnd, WM_KEYUP, vk)
    time.sleep(KEY_GAP)


def input_text(win, geo=None) -> str:
    """OCR 读一遍输入框里的内容，用来确认刚刚投进去的字符真的落下了。

    ★ 只裁【上半截】：输入框下面还压着一排按钮（表情 / 文件 / 「发送」），
      整块读进去的话，「发送」两个字会混进结果里 ——
      短消息（「嗯」「好」）的回读就被这两个字带偏了（2026-09-21 实录）。
    ★ 空框时的灰字提示（「按住鼠标语音输入文字」）也不是她打的字，一并丢掉。
    """
    img = ocr_reader.capture_window(win)
    if img is None:
        return ""
    if geo is None:
        geo = ocr_reader.detect_geometry(img)
    top = int(geo.bottom) + 4
    bottom = int(geo.bottom) + int((img.height - int(geo.bottom)) * 0.55)
    strip = img.crop((int(geo.left) + 2, top, img.width - 6, bottom))
    res, _ = ocr_reader.engine()(strip)
    return " ".join((it[1] or "").strip() for it in (res or [])
                    if not _is_noise_item(it[1] or ""))


def _landed(want: str, got: str) -> bool:
    """投进去的字，是不是【从第一个字开始】就落在输入框里了。

    ★ 2026-09-21 那次「首字被吞」的正面修补：
      原来的判据是 `want[:10] in got or 相似度 > 0.5`。
      少一个字的相似度是 0.933 —— 远远大于 0.5，于是【丢了首字的句子照样被放行】，
      回车一发，微信里就少了一个字。
      现在首字锚定：必须从第一个字起就对得上才算过。

    ★ 2026-10-04 又补一层：光「首字锚定」会【误判】。
      行首那个语气字（诶 / 哎 / 嗯）笔画少、又在行首，OCR 最爱读错。
      实测 09-23~10-04 共 1554 次成功回读，长度差几乎全是 0（1538 次），
      而 5 次「判没落进去」的真身是：`诶对了…` 被读成 `送对了…`/`喉…`/`误…`/`埃…`
      —— 后面每一个字都对上了，字明明好好地躺在输入框里。
      旧判据把它们判成「没落进去」→ 清空重投 → 把好端端的字删了再打一遍，
      重投又读错一次 → 交回前台抢焦点。白折腾，还多半把这条弄丢。

      区分「首字被认错」和「首字真被吞」，靠的是【长度】：
        · 真被吞 → got 正好是 want 去掉第一个字（短 1，且后面一字不差）
        · 认错了 → 等长，只是第一个字不同，后面一字不差
      两者相似度都是 0.933，分不开；长度一量就分开了。
    """
    if not want or not got:
        return False
    if got.startswith(want[:4]):
        return True
    # ★ 首字真被吞：正好少第一个字 —— 这条死也不能放行（2026-09-21 的事故）
    if got == want[1:]:
        return False
    # ★ 首字被 OCR 认错：等长、第二个字起一字不差
    #   等长这一条本身就挡住了「首字真丢」——真丢了会短一个字。
    if len(want) >= 2 and len(got) == len(want) and got[1:] == want[1:]:
        return True
    # 首字认错 + 中间也读歪一两个：去掉首字后仍然高度一致
    if len(want) >= 4 and len(got) == len(want) \
            and ocr_reader._similar(want[1:], got[1:]) >= 0.9:
        return True
    return got.startswith(want[:2]) and ocr_reader._similar(want, got) >= 0.85


def _write_log(status: str, target: str, text: str, readbacks, tries: int):
    """把一行记录追加到 日志\\发送记录.log（GBK，跟这个项目的其它日志一致）。"""
    try:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "日志")
        os.makedirs(d, exist_ok=True)
        line = "%s | %s | 目标=%s | 想发=%s | 回读=%s | 投了%d次\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"),
            status,
            target or "?",
            (text or "").replace("\n", " "),
            " / ".join(readbacks) if readbacks else "（没回读）",
            max(1, int(tries)),
        )
        with open(os.path.join(d, "发送记录.log"), "a",
                  encoding="gbk", errors="replace") as f:
            f.write(line)
    except Exception:
        pass


def _log_send(target: str, text: str, readbacks, ok: bool):
    """每次发送记一行到 日志\\发送记录.log。

    为什么要留这个：首字被吞这类毛病是【偶发】的 ——
    控制台那几行滚过去就没了，事后完全没法查。
    记下「想发什么 / 回读到了什么 / 投了几次」，下次再出怪事一眼就能看出来。
    """
    _write_log("已发出" if ok else "没发成（交回前台）",
               target, text, readbacks, max(1, len(readbacks)))


def log_foreground(target: str, text: str, ok: bool, note: str = ""):
    """前台通道（小信使：剪贴板 → Ctrl+V → 回车）发完之后【也记一笔】。

    ★ 2026-10-04 补：这条通道原来一个字都不记。
      于是它失败的时候，在 发送记录.log 里【完全隐形】——
      而控制台照样印着「→ 文字已发出」，两头都看不出来。
      实测那天 13:19~13:23 连吞 4 条回复，日志里查无此事。
      现在前台也占一行，第二列写「前台已发出 / 前台也没发出去」，
      回读那列写「（前台靠聊天区回读确认）」，一眼能分清是哪条通道发出去的。
    """
    _write_log("前台已发出" if ok else "前台也没发出去",
               target, text, ["（前台靠聊天区回读确认）" + (("：" + note) if note else "")],
               1)


def send(win, text: str, clear_first: bool = True, verify: bool = True,
         geo=None, tries: int = 2, target: str = "") -> bool:
    """
    后台发送一条文字消息。返回 True 表示「确认发出去了」。

    ⚠️ 一定要做 verify，不能想当然。踩过的坑：
      微信被最小化到托盘再显示回来后，Qt 内部的「焦点输入控件」就没了，
      WM_CHAR 投进去会静静地丢掉 —— 程序看起来一切正常，消息却没发出去。
      所以这里投完字【先回读一遍输入框】，确认字真的在里面，再按回车。
      读不到就把输入框清干净、返回 False，让上层退回前台流程，
      绝不会出现「以为发了其实没发」或者「发两遍」。

    ★ 2026-09-21 加了一层【首字锚定】（见 _landed）：半对半错（丢首字）那种，
      原来那个 0.5 的相似度阈值是拦不住的。对不上就清干净重投一次；
      两次都不行就返回 False，交回前台「进剪贴板 → Ctrl+V」那条路 ——
      那条是原子操作，不会只丢一个字。
      每次发送都记一行到 日志\\发送记录.log。

    返回 False 的常见原因：
      · 微信刚从托盘恢复，还没有过任何一次真实交互
        → 在微信窗口上点一下（点进输入框最好），之后就恢复正常了
      · 微信被最小化到托盘了
    """
    hwnd = win.NativeWindowHandle
    want = _norm(text)
    if not want:
        return False

    readbacks = []
    for k in range(max(1, int(tries))):
        # 清空：第一次也清（框里可能留着他打了一半的话）；
        # 重投那一次按上一遍读到的字数清，宁可多按几下。
        if clear_first or k:
            n = 60 if k == 0 else min(120, max(60, len(readbacks[-1]) + 10))
            clear_input(hwnd, n)
        time.sleep(SETTLE + 0.12 * k)     # 等 Qt 把这一串退格啃完，再投第一个字
        type_text(hwnd, text)
        time.sleep(POST_TYPE)

        if not verify:
            break

        got = _norm(input_text(win, geo))
        readbacks.append(got)
        if _landed(want, got):
            break
        if k + 1 < int(tries):
            if not got:
                # ★ 回读【空】和回读【不对】是两回事：
                #   空 = 输入框里一个字都没读到 → 多半是它丢了键盘焦点
                #        （他在聊天区点过消息，焦点跑到那边去了），WM_CHAR 全落空。
                #   这时重投也是白投，得让上层去把焦点找回来（见 bridge.send_text）。
                print("  ★ 回读是空的（输入框没读到字）——多半是输入框丢了焦点，"
                      "再投一次看看")
            else:
                print("  ★ 首字没落进去（回读「%s」），清干净重投一次" % got[:24])

    else:
        # 投了两次都没落全：清干净，交回上层走前台那条原子粘贴的路
        clear_input(hwnd, 60)
        _log_send(target, text, readbacks, ok=False)
        return False

    press(hwnd, VK_RETURN)
    time.sleep(0.9)

    # ★ 2026-10-05 加：回车按完之后，回看聊天区里**到底有没有这一条**。
    #   以前「回读输入框对得上」就写「已发出」，可那只证明字进了输入框，
    #   不证明回车把它送出去了。实测 2026-10-05 00:05:29 那条「前辈你别闹」：
    #   输入框回读一字不差、日志写着「已发出」，聊天里却根本没有这一条
    #   （用户截图实证）。回读气泡是唯一说得清的判据。
    if not verify:
        _log_send(target, text, readbacks, ok=True)
        return True

    seen = True
    try:
        seen = last_own_message(win, text, geo)
    except Exception:
        seen = True                    # 复核本身出错不算发送失败
    if not seen:
        # 气泡没看到 —— 先看看框里还留着没有：留着就说明那一下回车没生效，
        # 补按一次（框里只有这一条，不会再发一遍）。
        left = ""
        try:
            left = _norm(input_text(win, geo))
        except Exception:
            left = ""
        if left and _landed(want, left):
            print("  ★ 回车没生效（框里还留着字），补按一次回车")
            press(hwnd, VK_RETURN)
            time.sleep(0.9)
            try:
                seen = last_own_message(win, text, geo)
            except Exception:
                seen = True
    if seen:
        _log_send(target, text, readbacks, ok=True)
    else:
        _write_log("已发出（气泡没复核到）", target, text, readbacks,
                   max(1, len(readbacks)))
        print("  ！回车按了，但聊天区里没看到这一条 —— 日志已标成"
              "「气泡没复核到」，别当成发出去了")
    return True


def last_own_message(win, expect: str, geo=None) -> bool:
    """
    复核：截图后 OCR 认出「我说的话」里有没有刚发出去的内容。
    用来确认后台发送真的生效了，不是自我感觉良好。

    ★ 2026-10-04：前台通道（剪贴板 → Ctrl+V → 回车）也要用它做确认
      —— 以前那条路完全没确认，吞了消息也不知道。
      比对放宽到「七成相似 / 前六个字对得上」：气泡里的字比输入框长，
      OCR 掉一两个字的概率更高，卡太死会天天误报「没发出去」。
    """
    img = ocr_reader.capture_window(win)
    if img is None:
        return False
    if geo is None:
        geo = ocr_reader.detect_geometry(img)
    bubbles = ocr_reader.read_bubbles(img, geo)
    want = _norm(expect)
    if not want:
        return False
    for b in reversed(bubbles[-8:]):
        if not b.mine:
            continue
        n = _norm(b.text)
        if not n:
            continue
        if n in want or want in n:
            return True
        if len(n) >= 6 and want.startswith(n[:6]):
            return True
        try:
            if ocr_reader._similar(n, want) >= 0.7:
                return True
        except Exception:
            pass
    return False
