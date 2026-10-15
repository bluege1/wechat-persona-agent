# -*- coding: utf-8 -*-
"""
达妮娅 · 微信桥接程序（监听 + 大脑）
=====================================

分工：
  WeeMessenger（现成懒人包）负责「发」——文字 / 表情包 / 真语音条
  本脚本负责「收」和「想」——读消息 → 调 LLM → 把回复交给 WeeMessenger 发出去

「收」为什么是截图 + OCR：
  微信 PC 从 4.0 起换成 Qt 自绘 UI，默认不向系统的无障碍接口暴露控件，
  老办法（遍历控件树读消息）在 4.1 之后已经完全失效——
  社区里流传的「开讲述人再重登」只对历史上进过无障碍模式的老号有效。
  所以这里改成：后台截图 → RapidOCR 识别 → 按左右位置判断是谁发的。
  详见 ocr_reader.py 开头的说明。

前置（三步）：
  1. 微信 PC 4.1.9 或更高，用小号登录，窗口不要最小化到托盘
     （被别的窗口挡住没关系，后台照样抓得到）
  2. 装好 VB-Cable 虚拟声卡（WeeMessenger 发语音条需要它）
  3. WeeMessenger 已双击它的「启动.bat」并选了标准模式

用法：
  python bridge.py dump   # 自检：抓一张微信画面，把识别到的消息列出来
  python bridge.py run    # 正式运行

依赖：pip install -r requirements.txt
"""

import base64
import datetime as dt
import json
import random
import re
import sys
import time
from pathlib import Path

import requests
import uiautomation as auto
import yaml

import daily_life
import ocr_reader
import wechat_window
import wx_input

# 关系进度（relation.py）和定期总结（journal.py）都是独立的模块，
# 缺了也不该让整个程序起不来，所以这里做成「有就用，没有就退回旧行为」。
try:
    import relation
except Exception:
    relation = None

try:
    import journal
except Exception:
    journal = None

# 世界书（world.pack / 世界书.py）：把一万多字的世界观拆成「核心卡 + 说到才展开的词条」，
# 省 token 用。没有它就退回「每轮整篇塞进去」的老做法。
try:
    import 世界书 as worldbook
except Exception:
    worldbook = None

# 约定（约定.py）：他说「明天早上叫我起床」、她说「我先去洗澡 待会再聊」——
# 这些「以后」原来全掉在地上（她开不开口只看「安静够久了没 + 掷骰子」，
# 跟她答应过什么一点关系都没有）。现在落成待办，到点由她主动开口。
try:
    import 约定 as promise
except Exception:
    promise = None

# 分享欲 · 联想（分享.py）：
#   ① 按关系档位决定她愿意露多少自己（第 1 档几乎不露，也不主动发消息）
#   ② 今天发生过的事不再列进提示词，改成「他的话撞上话题才翻出来」
try:
    import 分享 as sharing
except Exception:
    sharing = None

# 读图（读图.py）：OCR 只认字，对方发来的图片它一个字都读不到，
# 于是那条消息被整个忽略。这里接一个视觉模型把图读成人话。
# 没配也没关系 —— 她会老实说「你这张图我这儿看不到」，不会瞎编。
try:
    import 读图
except Exception:
    读图 = None

# 表情包（表情包.py）：小信使本来就支持发（send_sticker），只是一直没接。
# 回完话之后按概率追一张 —— 高兴、懒得打字、敷衍的时候比多打几个字管用。
try:
    import 表情包 as stickers
except Exception:
    stickers = None

# 表白（表白.py）：第 4 档「暧昧」满了之后那两条路 ——
#   ① 他先开口 → 算「用心分」，太随便她不会答应、但也不拒绝，只说「你再想想」；
#   ② 她自己开口 → 四层暗示铺到位，最后才说，他点头进第 5 档、摇头她自暴自弃。
# 这个模块只是「判定 + 台词骨架」，状态全在 relation.py 的 关系.json 里。
# 拿不到它就把这条线整个收起来 —— 前四档本来就不受它影响，聊天照旧。
try:
    import 表白
except Exception:
    表白 = None

# 控制台编码要「跟随」而不是「强制」：
#   3-自检.bat 里是 chcp 936，如果这里硬写成 utf-8，中文就会变乱码。
#   errors="replace" 是为了任何编码不支持的字符都不会把程序打崩。
try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    sys.stderr.reconfigure(encoding=sys.stderr.encoding or "utf-8", errors="replace")
except Exception:
    pass

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
import 角色                            # 换人 / 看有谁 / 读她的开场白
# 微信主窗口类名在不同版本里完全不同——见 wechat_window.py 的说明。
# 统一由该模块处理：候选类名 → 按进程兜底。
WECHAT_CLASS = wechat_window.CLASS_CANDIDATES

# 发出「发语音」指令后，最多等这么久去回读聊天区、确认语音条真的出现了。
# 小信使是投队列就返回，真正的「按住快捷键 + 放音」还在后面排队，
# 排队延迟 1~3.5s + 按住时长（音频多长就按多久），所以要留足。
VOICE_VERIFY_SECONDS = 30.0


# ══════════════════════════════════════════════════════════════
# 一、配置
# ══════════════════════════════════════════════════════════════

def _me() -> str:
    """现在是谁 —— 打印和提示文字里用它。

    以前这些地方写死「达妮娅」，切了角色还印着她的名字，
    看着就像没切成功。现在每次去问角色层。
    """
    try:
        return 角色.current()
    except Exception:
        return "她"


def load_config() -> dict:
    path = ROOT / "config.yaml"
    if not path.exists():
        print(f"[!] 找不到配置文件：{path}")
        sys.exit(1)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    key = str(cfg.get("llm", {}).get("api_key", "")).strip()
    # 占位符故意写成纯 ASCII（YOUR-KEY-HERE），这样启动器的 findstr 也能检查
    if (not key) or ("YOUR-KEY-HERE" in key):
        print("[!] 还没填 LLM API Key。")
        print("    打开 config.yaml，把 llm.api_key 换成你自己的 Key。")
        sys.exit(1)
    return cfg


# ══════════════════════════════════════════════════════════════
# 二、微信侧：找窗口 / 进会话 / 读消息
# ══════════════════════════════════════════════════════════════

def find_window(retry: int = 6):
    """
    找到已登录的微信主窗口，找不到返回 None。

    注意：不同微信版本主窗口类名完全不同（3.x 是 WeChatMainWndForPC，
    4.x 正常是 mmui::MainWindow，4.x 渲染回退时会变成 Qt51514QWindowIcon），
    所以这里统一交给 wechat_window 模块按「候选类名 → 按进程兜底」处理。
    """
    return wechat_window.find(timeout=max(0, retry - 1) * 1.0, interval=1.0)


def open_chat(win, name: str):
    """用 Ctrl+F 搜索进入指定会话（和 WeeMessenger 同一套做法）。"""
    wechat_window.activate(win)
    time.sleep(random.uniform(0.15, 0.35))
    auto.SendKeys("{Ctrl}f")
    time.sleep(random.uniform(0.3, 0.5))
    auto.SetClipboardText(name)
    auto.SendKeys("{Ctrl}v")
    time.sleep(0.15)
    auto.SendKeys("{Enter}")
    time.sleep(random.uniform(0.8, 1.2))


def read_messages(win, max_items: int = 40):
    """
    读出当前会话里的文字消息。

    走的是 OCR：抓一张窗口图 → 定位聊天区 → 识别气泡 → 判断每条是谁发的。
    返回 [(text, who), ...]，顺序为界面从上到下，who 是 "对方" 或 "我"。

    保留这个函数名和返回格式，是为了兼容旧的自检脚本。
    """
    img = ocr_reader.capture_window(win)
    if img is None:
        return []
    geo = ocr_reader.detect_geometry(img)
    bubbles = ocr_reader.read_bubbles(img, geo)
    return [(b.text, "我" if b.mine else "对方") for b in bubbles[-max_items:]]


def dump_tree(win, max_depth: int = 6):
    """抓一张微信画面，把识别到的消息打印出来（自检用）。"""
    print("\n" + "=" * 70)
    print("微信侧自检：截图 + OCR")
    print("=" * 70)

    info = wechat_window.diagnose(win)
    print(f"  窗口类名 : {info['class']}")
    print(f"  窗口标题 : {info['title']!r}")
    print(f"  UIA 节点 : {info['nodes']} 个  ← 只有一两个也无所谓，本方案不依赖它")
    print()

    img = ocr_reader.capture_window(win)
    if img is None:
        print("  [X] 抓不到窗口画面。")
        print("      最常见原因：微信被最小化到托盘了，先把它打开成窗口再试。")
        print("-" * 70 + "\n")
        return

    print(f"  [OK] 抓到画面 {img.size[0]}x{img.size[1]}")
    geo = ocr_reader.detect_geometry(img)
    print(f"  [OK] 聊天区定位 x[{geo.left},{geo.right}] y[{geo.top},{geo.bottom}]  ({geo.how})")
    print("  正在识别（第一次要加载 OCR 模型，稍等两三秒）……")
    bubbles = ocr_reader.read_bubbles(img, geo)
    print(f"  [OK] 识别到 {len(bubbles)} 条消息")
    print()

    print("-" * 70)
    if bubbles:
        print("  结论：能读到消息。最近 8 条：")
        for b in bubbles[-8:]:
            who = "我" if b.mine else "对方"
            print(f"     [{who:>2}] {b.text[:62]}")
    else:
        print("  结论：没识别到消息。")
        print("        确认微信窗口是打开的、当前会话里有聊天气泡，再跑一次。")
        print("        还不行就跑「3-自检.bat」，它会存一张标注图给你看。")
    print("-" * 70 + "\n")


# ─────────────────────────────────────────────────────────────
#  剥掉「动作描写 / 旁白」
#
#  模型很喜欢写 *轻轻笑了*、（歪头）、【沉默了一会儿】 这类东西。
#  文字聊天里读着像小说，语音条里更糟 —— 会被原样念出来，
#  变成「轻轻笑了嗯好」，非常出戏。
#
#  persona.md 里已经明令禁止，这里是【代码兜底】：
#  模型偶尔会忽略人设，兜底保证它一定漏不出来。
#
#  规则刻意保守 —— 宁可漏删，也不吃掉正常内容：
#    *…*  **…**  […]  【…】  → 直接删（聊天正文不会这么写）
#    (…)                    → 只有「很短」或「含动作词」才删
# ─────────────────────────────────────────────────────────────

_ACT_WORDS = (
    "笑", "叹", "沉默", "停顿", "歪头", "点头", "摇头", "低头", "抬头",
    "皱眉", "抿", "咬唇", "耸肩", "摊手", "转身", "伸手", "抱", "坐下",
    "站起", "靠在", "盯", "眨", "愣", "怔", "犹豫", "小声", "轻声",
    "嘟囔", "嘀咕", "回忆", "蹭", "揉", "缩", "蜷", "捂", "打哈欠",
    "伸懒腰", "指尖", "肩膀", "顿了一下", "顿了顿",
)

# 顺序有讲究：**…** 必须排在 *…* 前面，否则会切出孤立的星号
_ACT_PAIRS = [
    re.compile(r"\*\*[^*\n]{0,40}\*\*"),
    re.compile(r"\*[^*\n]{0,40}\*"),
    re.compile(r"【[^】\n]{0,40}】"),
    re.compile(r"\[[^\]\n]{0,40}\]"),
    re.compile(r"〈[^〉\n]{0,40}〉"),
]

_ACT_PAREN = re.compile(r"[（(]([^）)\n]{0,30})[）)]")


def strip_acts(text: str) -> str:
    """把动作描写和旁白剥掉，只留真正说出口的话。"""
    if not text:
        return text

    out = text
    for pat in _ACT_PAIRS:
        out = pat.sub("", out)

    def _paren(m):
        inner = m.group(1).strip()
        if not inner:
            return ""
        if len(inner) <= 12 or any(w in inner for w in _ACT_WORDS):
            return ""
        return m.group(0)

    out = _ACT_PAREN.sub(_paren, out)

    # 收拾残局：行首孤零零的标点、多余空格、只剩标点的空行
    lines = []
    for ln in out.split("\n"):
        ln = re.sub(r"[ \t]{2,}", " ", ln).strip()
        ln = re.sub(r"^[，。、；：！？~…,.;:!?—\-]+", "", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines).strip()


# ── 微信口语化 ──────────────────────────────────────────────────
#
# 模型写惯了书面语：一句话塞四五个逗号、一条三十个字 —— 一眼就是 AI 味。
# persona.md 里白纸黑字写了「别打句号」，它该打还是打。所以在这里兜底：
# 不管它听不听话，发出去之前统一改一遍。
#
#   逗号 顿号 分号 冒号 句号 破折号  →  断句（换成换行，等于「切下一条发」）
#   问号 感叹号                      →  留着，并且在后面也断
#   省略号 波浪号                    →  留着，但【不】断（那是话说到一半的停顿）
#   引号 书名号 括号                 →  去掉（微信口语不打这些）
#
# 断出来的换行再交给 split_for_chat 归拢成「一条一条」发出去。
# 想恢复原样（标点全留着）把 config.yaml 里 behavior.wechat_style 改成 false。
_STYLE_STRIP = "「」『』“”‘’《》〈〉【】〔〕（）()\"'"
_STYLE_CUT = "，、；：。;:,.，·—–―-"
_STYLE_TAIL = "？！!?"          # 留着并断句
_STYLE_KEEP = "…～~"            # 留着但不断句

# 强行切长句时，优先断在这些字【后面】—— 读起来就像话说到一半停一下
_SOFT_TAIL = "吧啊呢嘛哦嗯啦呀哈诶咯喔哎了着过完好…～~？！!?"
# 其次断在这些字【前面】（连词 / 人称 / 指示词，断在前面句子才完整）
_SOFT_HEAD = "我你他她它您但可不过反正所以然后因为其实那就这要是如果"
# 这两字以内的词，发成单独一条最像真人（真人「哦」就是单独一条）
_SOLO_WORDS = "嗯哦啊哈诶哎嗨喂咦呀咦欸唉"


def wechatify(text: str) -> str:
    """把书面语标点改成微信口语的断句（换行）。只动标点，一个字不改。"""
    if not text:
        return text

    buf = []
    for i, ch in enumerate(text):
        if ch in _STYLE_STRIP:              # 引号、书名号之类：直接抹掉
            continue
        # ★ 钟点里的冒号不是断句（2026-09-28 实录）——
        #   她主动发的那条原本写的是「00:39那会儿睡前把闹钟往后调了十分钟」，
        #   这个冒号被当成断句切成换行，归拢时又用空格粘回去，
        #   发出去就成了「00 39那会儿…」（用户原话：「00.39那会，什么鬼」）。
        #   数字夹着的冒号一个字都不动。
        if (ch in ":：" and 0 < i < len(text) - 1
                and text[i - 1].isdigit() and text[i + 1].isdigit()):
            buf.append(ch)
            continue
        if ch == "\n" or ch in _STYLE_CUT:  # 逗号句号破折号：变断句
            buf.append("\n")
        elif ch in _STYLE_TAIL:             # 问号感叹号：留着，后面断
            buf.append(ch)
            buf.append("\n")
        elif ch in _STYLE_KEEP:             # 省略号波浪号：留着，不断
            buf.append(ch)
        else:
            buf.append(ch)

    lines = []
    for ln in "".join(buf).split("\n"):
        ln = ln.replace("\t", " ")
        ln = re.sub(r" {2,}", " ", ln).strip()
        ln = ln.strip(_STYLE_CUT)       # 两侧残留的逗号句号类
        ln = ln.lstrip(_STYLE_TAIL)     # 行首的问号感叹号（行尾的是正常语气，要留）
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def _chop(line: str, limit: int):
    """一条还是太长，就【强行切开】。

    宁可切得碎一点，也不能让一条 30 个字发出去 —— 那是最像 AI 的地方。
    切点按「读起来最不像被劈开」的顺序找：
    停顿符后 → **空格** → 语气词后 → 连词前 → 硬切。
    """
    out, rest = [], line
    while len(rest) > limit:
        cut = -1
        lo = 2                          # 允许一直往前找一个像样的切点
        hi = min(limit + 1, len(rest) - 1)
        # ⓪ 标点 / 停顿符后面最自然（问号、省略号本来就是断句的地方）
        for i in range(min(limit, len(rest) - 1), 2, -1):
            if rest[i - 1] in (_STYLE_TAIL + _STYLE_KEEP):
                cut = i
                break
        # ① ★ 空格 —— 她自己敲的那个空格，就是她本来要断的地方（2026-09-28）
        #   用户原话：「正常人一般都是空格处断句的吧，你这断句……有时候一句话的
        #   中间断的」。实测那句「结果调完更睡不着了 翻来覆去想了半天有的没的
        #   今天一天都困得不行」：断在语气词「了」后面，前一条收在「…想了」上
        #   —— 半句话；断在空格上，前一条正好是一整句。
        #   ★ 所以空格必须排在语气词 / 连词**前面**（原来排在第三，白写了）。
        #   下限取 limit//2：空格要是太靠前（一句话第三四个字就冒一个），
        #   断出来的碎渣比不断更难看，那种交给语气词 / 连词去接。
        if cut < 0:
            floor = max(3, limit // 2)
            sp = rest.rfind(" ", floor, limit + 1)
            if sp >= floor:
                cut = sp
        # ② 语气词 / 停顿词后面
        if cut < 0:
            for i in range(hi, lo, -1):
                if rest[i - 1] in _SOFT_TAIL:
                    cut = i
                    break
        # ③ 连词 / 人称前面
        if cut < 0:
            for i in range(hi, lo, -1):
                if rest[i] in _SOFT_HEAD:
                    cut = i
                    break
        # ④ 只能硬切了 —— 但别把英文单词劈成两半
        if cut < 0:
            cut = limit
            while (0 < cut < len(rest)
                   and rest[cut - 1].isascii() and rest[cut - 1].isalnum()
                   and rest[cut].isascii() and rest[cut].isalnum()):
                cut -= 1
            if cut < 3:
                cut = limit
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return [o for o in out if o]


def _nonspace(text: str) -> int:
    """去空白之后数几个字。

    归拢时用空格把几句话连在一条里，那些空格不该算进字数预算 ——
    不然「嗯 好」会被当成 3 个字。
    """
    return len(re.sub(r"\s", "", text or ""))


def _fit_pieces(pieces, max_chars: int, max_parts: int):
    """把并出来的长条切细，再把多出来的条数丢掉。

    这一段是「段数太多」的最后一关。归拢/均匀并都可能挤出长条
    （几句挤在一条里、二十几个字），必须切开 ——
    「一条二十多字」比「多发一条」更容易一眼看出是 AI。

    切开之后条数可能又多出来，那就把多的**整条丢掉**。
    ★ 别补「…」：省略号挂在句尾 = 明摆着「我话被砍了」，
      比少说一句还假。用户原话：「强行截断会导致语义不全」。
    """
    if max_chars > 0:
        tmp = []
        for c in pieces:
            tmp.extend(_chop(c, max_chars) if len(c) > max_chars else [c])
        pieces = [c for c in tmp if c.strip()]
    if max_parts > 0 and len(pieces) > max_parts:
        pieces = pieces[:max_parts]
    return [c for c in pieces if c and c.strip()]


def split_for_chat(text: str, max_chars: int = 15, max_parts: int = 6,
                   total_max: int = 0):
    """把一条回复拆成几条连着发，像真人打字那样。

    顺序是有讲究的 —— **先砍总量，再决定分几条**：

      ① 按换行 / 句末标点分句（wechatify 已经把逗号句号都换成换行了）
      ② 【总字数封顶】超了就在句子边界截住，末句补个「…」
         ★ 这一步必须在最前面。放在最后做是没用的：
           等到「均匀并成 max_parts 条」的时候，条数已经定了，
           总额超了就只能得到一条二十几字的长条 —— 那正是要避免的。
      ③ 还是超长的行【强行切开】—— 宁可切碎，也不发一条 30 字的
      ④ 一条条「归拢」：往一条里塞，塞不下就另起一条。
         「哦」「嗯」这种短的单独占一条 —— 真人也这么发。
      ⑤ 条数还是太多，均匀并成 max_parts 条。

    第 1 档的预算（一条 10 字 / 最多 2 条 / 总共 20 字）就是靠这一串兑现的：
    她想说 51 个字，先砍到 20，再分成 2 条。
    """
    text = (text or "").strip()
    if not text or not re.search(r"[\w\u4e00-\u9fff]", text):
        return []

    # ① 分句
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) < 2:
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?…~])", text) if p.strip()]

    # ② 总字数封顶（在句子边界截，不切碎句子）
    if total_max > 0:
        keep, acc = [], 0
        for p in parts:
            if acc + len(p) > total_max and keep:
                break
            keep.append(p)
            acc += len(p)
        # ★ 这里原来会给末句补个「…」。后来去掉了 ——
        #   省略号挂在句尾就是「话被砍了」的信号，用户一眼就看出是半句话。
        parts = keep or parts[:1]

    # ③ 超长的强行切
    chunks = []
    for p in parts:
        chunks.extend(_chop(p, max_chars) if len(p) > max_chars else [p])

    # ④ 归拢成一条一条
    out, buf = [], ""
    for p in chunks:
        if len(p) <= 2 and p[0] in _SOLO_WORDS:   # 「哦」「嗯」自己占一条
            if buf:
                out.append(buf)
                buf = ""
            out.append(p)
            continue
        if not buf:
            buf = p
        elif len(p) <= 2:                # 切出来的小碎渣，粘回去不占一条
            buf += p
        elif (len(buf) + 1 + len(p) <= max_chars
              and buf[-1] not in (_STYLE_TAIL + _STYLE_KEEP)):
            # 拼在一条里，用空格断（比逗号像人）。
            # 但上一条要是问号/感叹号/省略号收的尾，就别粘 —— 那本来就该单独发
            buf = buf + " " + p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)

    out = [c for c in out if c.strip()]

    if len(out) < 2:
        return [out[0]] if out else [text]

    # ⑤ 条数太多：均匀并（不是全堆到最后一条，那会凭空冒出一个超长条）
    if len(out) > max_parts:
        n = len(out)
        out = [" ".join(x for x in out[k * n // max_parts:(k + 1) * n // max_parts] if x)
               for k in range(max_parts)]
        out = [c for c in out if c.strip()]

    # ⑤b 并出来的条还是超长的话，切细；切完条数多出来的丢掉
    out = _fit_pieces(out, max_chars, max_parts)

    # ⑥ 兜底：总额真的不超预算。
    #    ★ 超了就**整条不要** —— 剩下的每一条都是完整的话。
    #      少说一句没人看得出来，说半句（「我不爱跟脾气好…」）一眼就假。
    if total_max > 0:
        out = _fit_pieces(out, max_chars, max_parts)
        while len(out) > 1 and sum(len(c) for c in out) > total_max:
            out.pop()
        if out and sum(len(c) for c in out) > total_max:
            # 极端：只剩一条还超（模型完全没照字数说）。
            # 从最近的停顿/语气词处断开，断在完整的位置上，也不补省略号。
            out[0] = _chop(out[0], max(3, total_max))[0]
        out = [c for c in out if c and c.strip()]

    return out or [text]


def _length_limits(brain, behavior: dict):
    """这一轮「一条最多几个字 / 最多几条 / 一次总共多少字」。

    这三个数跟着关系档位走（见 分享.py）：第 1 档一次总共就 20 个字、
    最多 2 条。刚认识的人不会一口气发六条，跟你讲她今天干了什么。

    分享欲关掉、或 share.length_by_stage=false 的时候，
    退回 config.yaml 里 behavior.split_chars 那三个老参数。
    """
    mc = int(behavior.get("split_chars", 15))
    mp = int(behavior.get("split_max_parts", 6))
    tc = int(behavior.get("total_chars", 0))
    sh = getattr(brain, "share", None)
    if sh is not None and getattr(sh, "stage_length", False):
        lv = sh.level or {}
        mc = int(lv.get("max_chars") or mc)
        mp = int(lv.get("max_parts") or mp)
        tc = int(lv.get("total_chars") or tc)
    return mc, mp, tc


# ══ 「他问的是话，她答的是告别」——用户实测最崩体验的一种 ══
#
# 现场：他问「你不想和我聊天？」，她回「嗯 知道了 那我就不说了」。
# 这不是答错，是**根本没把这句话当问句**，还顺手把对话关了。
#
# 为什么人设拦不住：persona.md 里原来有「困了就说困了，可能直接不回」和
# 「有时答非所问，因为你走神了」这两句 —— 等于明着授权她可以不回答。
# 这两句已经改了，但按老经验（人设里写「要短」都拦不住，见 _len_budget）
# 光改人设不保险，所以这里再兜一道代码：命中就让她**重说一遍**。
#
# 判据要点：
#   · 只对**问句**加严 —— 他不是在问她，她随口「嗯」是她的权利（人设允许）。
#   · 「明天再聊 行吗」这种不算敷衍：她把决定权还给他了。

_QUESTION_HINTS = ("吗", "呢", "怎么", "为什么", "为啥", "是不是",
                   "干嘛", "干啥", "要不要", "有没有", "好不好",
                   "行不行", "对不对", "对吧")

# 整条只剩这些 = 等于没答（「好」「行」不放进来：「要不要…」回「好」是正经答案）
_FILLER_ONLY = ("嗯", "哦", "啊", "唔", "呃", "哦哦", "嗯嗯", "嗯呢",
                "知道了", "懂了", "知道啦", "明白", "明白了")

_BRUSHOFF_PATTERNS = [
    # 「嗯 知道了」—— 他只想知道你怎么想，你在打官腔
    re.compile(r"^[嗯哦啊唔呃好]{0,3}[，,。、]*(知道|懂|明白)了?[。！!…]*$"),
    # 「那我就不说了 / 那我不打扰你了」—— 把人往外推
    re.compile(r"那(我)?就?不(说|聊|打搅|打扰|烦)了"),
    re.compile(r"不(打搅|打扰)你了"),
    re.compile(r"^那算了[。！!…]*$"),
    # 「你早点休息 / 去睡吧」—— 他没说要走，你先替他关了
    re.compile(r"^(你)?(也)?(早点|早些)(休息|睡|睡觉)"),
    # 「我睡了 / 不聊了」—— 没带问句、也没把话头留下
    re.compile(r"^(我)?(先)?睡(了|啦|啊)[。！!…]*$"),
    re.compile(r"不聊了[。！!…]*$"),
    # 「明天再聊 / 改天说」—— 但结尾带「吗/嘛」的放行（那等于把决定权还给他）
    re.compile(r"(明天|改天|下次|回头)再?(聊|说)(?!.*[吗嘛])"),
]

# 他这句不是在问「信息」，是在点她「你在敷衍 / 在躲」。
# 用户实测：「听起来好勉强」→ 她回了「行吧 你赢了 我确实没在打游戏」。
# 这种话**不带问号**，所以旧判据（只认问句）整条链路都不管，
# 她可以随便糊 —— 而且一糊就把二十轮前的旧话题翻出来当挡箭牌。
_COMPLAINT_HINTS = (
    "勉强", "敷衍", "转移话题", "说别的", "不想聊", "不想和我聊",
    "是不是烦我", "烦我了", "嫌我", "在躲", "躲我", "躲着",
    "别绕", "绕来绕去", "说实话", "没诚意", "是不是不想理",
    "懒得理我", "不耐烦", "冷场", "没什么话说",
)

# 整条是「认输 / 认错 / 投降」—— 他根本不是在跟她辩论，
# 说这个等于当众承认前面的话全是在糊弄他。
_SURRENDER_PATTERNS = [
    re.compile(r"你赢了"),
    re.compile(r"算(我|你)(输|赢)"),
    re.compile(r"(认输|认错|投降)"),
    re.compile(r"^(行|好|那)吧[，,]?我(输了|错了|服了)"),
]

# 拿「困 / 明天再说」当挡箭牌的。只用来**数次数**，不单独触发重说 ——
# 「不是 我有点困 但没不想跟你聊」是正经答案，不能被当成躲。
_EXCUSE_WORDS = ("困", "睡", "累", "歇", "没力气", "撑不住", "扛不住", "没劲",
                 "明天再", "改天再", "回头再", "不聊了")

# 「听着像回答、其实什么都没答」—— 比「嗯 知道了」更隐蔽的一类躲。
# 用户拍板：答非所问太影响正常对话，她只留「明着岔开」这一条退路，
# 这种糊弄话一句都不许有。
# ★ 只收**纯含糊、不会出现在正经回答里**的词：「还好」「差不多」「一般」
#   故意不收 —— 它们答「你今天过得怎么样」是正常回答，收进来会误伤。
_VAGUE_ONLY = ("说不好", "不好说", "不好讲", "说不准", "说不清楚",
               "看情况", "再说吧", "再看看吧", "不知道吧", "应该吧",
               "可能吧", "也许吧", "大概是吧", "大概吧", "没什么", "没啥")

# 「明着岔开」的开头：转折词打头 / 直接宣布换话题。
# 这是**唯一允许**她躲的方式（用户要的就是「拙劣的、一眼看得出」的转移话题），
# 所以它不用来拦人，只用来**记账** —— 一次聊天最多岔一次，连岔就让她重说。
_SHIFT_HEADS = ("对了", "诶", "话说", "说起来", "不说这个", "不聊这个",
                "别说这个", "不提这个", "说点别的", "聊点别的",
                "换个话题", "换话题", "那个什么", "算了不说了")

# 「明着岔开」的配额：这么多轮之内最多一次。
SHIFT_WINDOW = 8

# 「单方面关门」—— 不管他说什么，这几句都是在替他宣布结束。
# 它比 _BRUSHOFF_PATTERNS 窄得多：那套里「我先睡了」「明天再聊」在她自己累的
# 时候是**允许**的（只在他在问 / 在点她的时候才算躲），所以只能挂在 push 轮上。
# 底下这四条没这个歧义 —— 任何轮次、任何语境，说出口都是把人往外推。
# 用户举的那个原始例子「嗯 知道了 那我就不说了」，后半句正是它。
_CLOSE_DOOR_PATTERNS = [
    re.compile(r"那(我)?就?不(说|聊|打搅|打扰|烦)了"),
    # ★ 裸着的「我不说了」—— 上面那条要求以「那」打头，所以她去掉「那」字就漏了。
    #   实录（2026-09-21 早）：「工作有钱啊 我上课只犯困 / 不过你这班真不想上就算了 /
    #   我不说了」—— 末句正是这一个，整条链路的判据都没认出来。
    #   ⚠ 注意 _closes_door() 会把空白**全删掉**再匹配（见函数体），所以这里
    #     不能按「空格 + 我不说了」写，只能按「以它收尾」写。
    #     放行的边界也靠 $ 保住：「我不说了 我去上课了」结尾不是它 → 不拦。
    re.compile(r"不说了[。！!…]*$"),
    re.compile(r"不(打搅|打扰)你了"),
    re.compile(r"^那算了[。！!…]*$"),
    re.compile(r"不聊了[。！!…]*$"),
]

# 「把自己的世界说成假的」—— 最伤活人感的一种退让。
#
# 实录：他追问「你们那边的太阳也不叫太阳了，你到底是在地球上的哪一个地方」，
# 她答「都说了 / 我这儿不在地球 / 你当我在说故事 / 就行」。用户原话：
# 「她答'你当我在说故事就行'，这个回应很怪」「活人感太弱了」。
#
# 这不是躲、也不是敷衍，是**角色在否定自己的存在**——一句话把整个设定说成假的。
# 所以它不挂在「他在问」上，**任何轮次都拦**（跟 _closes_door 一个待遇）：
#   允许的：带脾气地懒得解释（`爱信不信` / `懒得说这个`）—— 那是她有情绪；
#   不允许：把自己那边说成编的（`你当我在说故事` / `就当我编的`）。
_SELF_DENIAL = ("当我在说故事", "当我编的", "当我在编", "就当我编", "当我瞎编",
                "当我胡说", "就当我在编", "当我说故事", "你信就当", "信就当")


def _is_self_denial(answer: str) -> bool:
    """她这句是不是在把「自己那个世界」说成假的。"""
    t = re.sub(r"\s+", "", answer or "")
    return bool(t) and any(w in t for w in _SELF_DENIAL)


# ── 「忘了」：他刚说过的话，她说她忘了 ─────────────────────────────────
#
# 用户实测（2026-09-22 16:02）：
#   他：「比你高一点，173cm」 → 她：「哦 那真比我高 / 我穿鞋160吧」
#   他：「鞋子这么高？」       → 她：「反正就160 你比我高 / 你多高来着 忘了」
# 两句话之前他刚亲口报过。「忘了」比答错还糟 ——
# 它不是答非所问，是**在说「你讲的话我没往心里去」**。
#
# 为什么会漏：人设里写着「不知道就问，别装懂」（这条是对的，防止她瞎编），
# 但模型偶尔会把它用过头，把「他刚告诉过我」也算进「我不知道」。
# 所以只拦一件事：**这句话里出现了「忘了 / 不记得」，而且最近几轮他
# 确实亲口报过一个具体信息（带数字的那种）**。命中就让她重说一遍，
# 顺便把他原话摆到她眼前。
_FORGET_WORDS = ("忘了", "忘啦", "记不得", "不记得", "记不清", "没记住", "忘掉")
# ↑ 保留一份词表给自检用（查错.py 会拿它做冒烟），真正判定看下面这几条正则。
# 为什么要连「你」一起匹配，而不是见「忘了」就拦：
#   她那边的事忘了很正常（「我忘带伞了」「课表忘了看」），
#   拦这些就是滥开火，会把她正常的话改坏。
#   要拦的只有一种：**「你（刚说的）……我忘了」/「你说过吗」**。
_FORGET_PATTERNS = [
    re.compile(r"你[^。！？!?，,；;]{0,8}(忘了|忘啦|不记得|记不得|记不清|没记住)"),
    re.compile(r"(忘了|忘啦|不记得|记不得|记不清|没记住)[^。！？!?，,；;]{0,8}"
               r"你(多|是|说|叫|几|什么|哪)"),
    re.compile(r"你(有|跟我)?说过(吗|没|么)"),
    re.compile(r"你(什么|多高|多大|叫什么|哪儿)[^。！？!?]{0,4}来着"),
]


def _claims_forget(answer: str) -> bool:
    """她这句是不是在说「你刚说的我忘了 / 你没说过吧」。

    只拦**关于他**的那种（「你多高来着 忘了」「你说过吗」）——
    她自己那边的事忘了不算，见 _FORGET_PATTERNS 上面的说明。
    另外还要能翻出他确实说过的那句（见 Brain._recent_fact）才真的开火。
    """
    t = re.sub(r"\s+", "", answer or "")
    return bool(t) and any(p.search(t) for p in _FORGET_PATTERNS)


# ── 「碰见你」：把两个人放进同一个画面 ─────────────────────────────────
# 用户实测（2026-09-21）：他问「怎么空荡荡的」，她答
#   「这个点大家都不在上课吗 / 我出来买瓶水就碰见你了」——
# 他手里只有那部手机，两个人**从来没见过面**，「碰见」是不可能的事。
#
# 这跟「答非所问」不是一类：它不是躲、不是敷衍，是**一句话把设定说塌了**
# ——「隔着手机」被她写成了「站在一块儿」。他看到的反应是「又开始不明所以了」。
#
# 边界要留：
#   允许的：想见而见不到（`要是你在就好了` / `你过来我就请你吃` 这种假设语气）……
#          不 —— 「你过来」本身就破坏了「过不来」，所以也拦。
#   允许的是**愿望与遗憾**：`要是能见到你就好了` / `我还没见过你呢`。
#   拦的是**已发生/正发生的同框**：`碰见你` / `看见你了` / `你过来` / `我带你去`。
_MEET_PATTERNS = [
    re.compile(r"(碰见|遇见|撞见)你"),
    re.compile(r"我[^。！？!?，,；;]{0,4}(看到|看见|见到|瞅见)你"),
    re.compile(r"你(就|也|正)?在我(这儿|这里|旁边|身边|面前)"),
    re.compile(r"(我带你|带你|带我|你过来|我过去|来我这儿)"),
]


def _claims_meet(answer: str) -> bool:
    """她这句是不是把「两个人见面」当成真事说了。"""
    t = re.sub(r"\s+", "", answer or "")
    if not t:
        return False
    # 「要是能见到你就好了」是在说**见不到**，不算破设定
    if re.search(r"(要是|如果|真能|能|想|还没|没)(见到|看见|碰见)你", t) \
            or re.search(r"(还没|没有)见过你", t):
        return False
    return any(p.search(t) for p in _MEET_PATTERNS)


# ── 「两个世界不是同一个钟」：她把自己那边的时间说成跟本地不一样 ────────
#
# 用户实测（2026-09-27 03:31）：
#   他问「现在几点啦」 → 她答「我这边快中午了 太阳老高 / 你那边应该还在
#   半夜吧 / 你早点睡 熬夜不好」—— 本地是凌晨 03:31，她却说自己那边是中午。
#
# 为什么人设拦不住（跟「把自己说成编的」是同一类坑）：
#   ① 她的 memory.json 里攒了**四条同类原话**（「我这儿是白天嘛 跟你那边
#      时间不一样」「我这边真十点 天幕写着呢」……），每轮都当「真的发生过
#      的事」回放 —— 模型在照着她自己的历史往下演；
#   ② 世界观里写着「天幕 / 人造太阳模拟昼夜」，模型就信自己头顶那片天。
# 规矩已经在 persona 和 daily_life.context() 里立了（同一个钟、天色也同一个），
# 但按老经验（人设里写「要短」都拦不住，见 _len_budget）光靠文案不保险，
# 所以这里再兜一道代码：命中就让她重说一遍。
#
# 判据三层：
#   ① **明着比两个钟**：说时间不一样 / 反问「你那边几点」/ 拿天幕当表用；
#   ② **两边都点了名**（「我这儿早上…你那边半夜」）—— 在比，就是在编；
#   ③ **按当下时刻判天色**：本地凌晨却说自己那边是白天（反着也一样）。
#      ★ ③ 只在**当轮**用 —— 矫正历史时不能拿这条，否则她正常说
#        「我这边下午有课」也被改掉了。
_TIME_SPLIT_ALWAYS = (
    "时间不一样", "时间不同", "时间对不上", "你那边的钟", "你那边的时间",
    "你的钟跟我", "你那边几点了", "你那儿几点了", "你那边几点", "你那儿几点",
    "天幕写着", "天幕上写着",
)
# ② 用：**两边各自都带上了时刻**才认 —— 只是「我这边下午有课 你那边呢」
#    这种不该拦（下午有课是真的），所以要求左右两边各命中一次。
_SIDE_TIME = re.compile(
    r"(我这边|我这儿|我们这边|你那边|你那儿|你们那边)"
    r"[^。！？!?，,；;]{0,5}"
    r"(白天|早上|上午|中午|下午|晚上|半夜|凌晨|深夜|天亮|太阳|"
    r"几点|点半|点多了|[一二三四五六七八九十两\d]+点)")
# ③ 用：本地是深夜时，这些「我那边是白天」的说法一眼是她编的
_TIME_SPLIT_DAYLIKE = (
    "我这边是白天", "我这儿是白天", "我这边白天", "我这儿白天",
    "我这边快中午", "我这儿快中午", "我这边中午", "我这儿中午",
    "我这边早上", "我这儿早上", "我这边上午", "我这儿上午",
    "太阳老高", "太阳很大", "太阳挺大", "天亮了", "天已经亮了",
)
# ③ 反过来：本地是大白天，她说「你那边还是半夜」也是编的
_TIME_SPLIT_DARKLIKE = (
    "你那边是半夜", "你那边还是半夜", "你那边还在半夜", "你那边半夜",
    "你那边是晚上", "你那边是深夜", "你那边还是晚上",
)
_NIGHT_HOURS = {0, 1, 2, 3, 4, 5, 20, 21, 22, 23}
_DAY_HOURS = {7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18}


def _claims_time_split(answer: str, hour=None) -> bool:
    """她这句是不是在说「两边不是同一个钟 / 我那边是白天」。

    hour 传本地当前小时（0-23）时，多判一层「天色也该是同一个」；
    传 None（矫正历史、或事后复查）就只认前两层 ——
    历史里她说自己下午有课是**真的**，不能见「下午」就改掉。
    """
    t = re.sub(r"\s+", "", answer or "")
    if not t:
        return False
    if any(w in t for w in _TIME_SPLIT_ALWAYS):
        return True
    # 「时差」单拎出来：她说「咱俩没有时差」是对的，别拦
    if re.search(r"(?<![没有无])时差", t):
        return True
    # 两边各自都报了一个时刻 → 她在比两个钟
    if len(_SIDE_TIME.findall(t)) >= 2:
        return True
    if hour is not None:
        if hour in _NIGHT_HOURS and any(w in t for w in _TIME_SPLIT_DAYLIKE):
            return True
        if hour in _DAY_HOURS and any(w in t for w in _TIME_SPLIT_DARKLIKE):
            return True
    return False


# 矫正历史时换上的话 —— 既认错、又把「同一个点」再说一遍，
# 好让她往后照这个版本演（她认错得很干脆，是她的性子）。
_TIME_SPLIT_REWRITE = (
    "啊 我记错了\n我这儿跟你那边是一个点",
    "嗯 我看岔了\n我这边跟你那边是一个点",
    "我这边也是这个点\n前面那句算我胡说",
)


# ── 「报时式开场」——别把钟点原样交给她（2026-09-28 实录）──────────────
#
#  他截图上那条：她主动发来「00 39那会儿睡前把闹钟往后调了十分钟」。
#  两个毛病叠在一起：
#    ① 断句程序把「00:39」里的冒号当成了断句 → 已经修掉（见 wechatify）；
#    ② 更根上的 —— 主动消息的引子原文就是「（时间是「00:39 那会儿」）」，
#       她照着念了一遍。真人开口不会先念一遍表。
#  所以：素材上的钟点先翻成人话（凌晨 / 早上 / 晚上），引子里绝不出现数字钟点。

_W_NAME = ((0, 4, "凌晨"), (5, 8, "早上"), (9, 10, "上午"),
           (11, 12, "中午"), (13, 17, "下午"), (18, 23, "晚上"))


def _human_when(when: str) -> str:
    """把「00:39」这种钟点翻成人话（→「凌晨」）。翻不出来就返回空串。"""
    m = re.match(r"^\s*(\d{1,2})[:：](\d{2})\s*$", when or "")
    if not m:
        return ""
    h = int(m.group(1)) % 24
    for a, b, name in _W_NAME:
        if a <= h <= b:
            return name
    return ""


_CLOCK_OPEN = re.compile(
    r"(?<![\d:])([01]?\d|2[0-3])[:： ]([0-5]\d)(?!\d)"
    r"(\s*)(那会儿|那时候)")


def _human_clock_fix(s: str) -> str:
    """把「00 39那会儿…」「19:20那会儿…」这种**报时式开场**改成人话。

    ★ 只处理「钟点 + 那会儿」这一种写法 —— 她正常说「我三点睡的」一个字不碰。
    ★ 数字中间那个空格是历史遗留：老版本的 wechatify 把冒号切成换行、
      归拢时又用空格粘回来，留下的痕迹。她是照着自己那句演下一句的，
      所以这段脏话得在代码层矫掉，光改数据文件没用（同 _claims_time_split）。
    """
    if not s or "那会儿" not in s:
        return s

    def rep(m):
        pre = s[:m.start()].rstrip()
        # ★ 改完别留一个孤零零的空格（「早上 那会儿」）——把钟点后面那截空白
        #   一起吃掉，只把「那会儿」还回去（2026-09-28：报时式里「07 44 那会儿」
        #   的空格来自冒号被切掉后归拢时粘的，不改就成了新的脏话）。
        # 前面已经写了「早上 / 晚上」这类词，就别再补一个（免得「晚上早上那会儿」）
        if pre and pre[-1] in "凌晨早上中午下午晚上夜":
            return m.group(0)
        name = _human_when("%s:%s" % (m.group(1), m.group(2)))
        if not name:
            return m.group(0)
        # 把钟点后面那截空白一起吃掉，只把「那会儿」还回去
        return name + m.group(4)

    return _CLOCK_OPEN.sub(rep, s)


def _fix_history(hist):
    """把历史里「两边不是同一个钟」的旧话，换成她自己认错一句。

    为什么要在**代码层**矫正、而不是去数据文件上清一刀：
    清完她下一句又会写进去一条新的 —— 她照着自己的历史演，只要历史里
    还留着一条「时间不一样」，模型下一轮就接着这么写。所以加载、落盘、
    拼进提示词时都过一遍。

    ★ 只动 assistant 那边、且**一眼就是在比两个钟**的那几条；
      别的（包括她说自己下午有课）一个字都不碰。
    ★ 角色和条数都不变 —— user/assistant 得交替，否则接口要挑刺。

    ★ 2026-09-28 补：顺手把「00 39那会儿…」这种**报时式开场**也改成人话
      （见 _human_clock_fix）。她主动发过一条「00 39那会儿睡前把闹钟往后调了
      十分钟」，那句已经写进 memory.json 了 —— 留在历史里，她下一轮就会
      照着这个腔调再说一遍。只改内容，条数 / 角色照样不动。
    """
    out, k = [], 0
    for m in hist or []:
        if isinstance(m, dict) and m.get("role") == "assistant":
            c = m.get("content") or ""
            if _claims_time_split(c):
                out.append({"role": "assistant",
                            "content": _TIME_SPLIT_REWRITE[k % len(_TIME_SPLIT_REWRITE)]})
                k += 1
                continue
            c2 = _human_clock_fix(c)
            if c2 != c:
                out.append({"role": "assistant", "content": c2})
                continue
        out.append(m)
    return out


# ── 他在约她做点什么 ────────────────────────────────────────────────────
#
# 用户要的（2026-09-21）：「假如她十二点原定要去吃饭，可是我叫她去玩游戏，
# 或者说我提出了一个很有趣的计划，她的日常就需要改变，我要在控制台上看到改变。」
#
# 判据只负责**发现他可能在约**，不负责决定要不要改 ——
# 她要是回绝了（要上课 / 不想去），日程当然不该动。所以这里命中之后
# 只是把「如果你答应了，就附一行日程」递给她（见 PLAN_NOTE），剩下交给模型。
# 这也是为什么词表宁可窄一点：命中多了只是多几十个字，但**漏了就等于没这功能**。
_PLAN_ASK = (
    "要不要一起", "要不一起", "能不能一起", "一起玩", "一起打", "一起去",
    "一起看", "一起做", "一起试", "陪我玩", "陪我打", "陪我去", "陪我逛",
    "陪我一起", "咱俩", "我们俩", "我俩", "开黑", "打游戏", "组队",
    "带你玩", "带你去", "带你逛", "想不想去", "想不想玩", "想不想试",
    "去不去", "玩不玩", "走不走", "给你看个", "给你讲个", "给你推荐",
    "教你玩", "我教你", "周末一起", "晚上一起", "明天一起", "有空吗",
    "有空没", "在忙吗", "忙不忙",
)


def _propose_plan(user_text: str) -> bool:
    """他这句是不是在约她做什么（或者问她的时间）。"""
    t = re.sub(r"\s+", "", user_text or "")
    return bool(t) and any(w in t for w in _PLAN_ASK)


# 「日程改动」标记 —— 她答应了事、要占掉接下来一段时间时，在回复最后附一行：
#
#     [[日程:12:00-14:00 陪他打游戏]]
#
# ⚠ 这一行必须**在清洗之前**摘走（见 _chat）：
#     · strip_acts 会把 `[…]` 当旁白整段剥掉；
#     · wechatify 会把半角冒号当断句符切成换行。
#   等洗完了再来找，标记早就碎成渣了。所以 _chat 拿到模型原稿的第一时间就抓。
# 括号放宽成三种、冒号全半角都收 —— 模型写标记的手法从来不稳定。
_PLAN_MARK = re.compile(
    r"[\[【〔]{1,2}\s*(?:日程|计划|安排)\s*[:：]?\s*"
    r"(\d{1,2})\s*[:：]\s*(\d{2})\s*"
    r"(?:-|~|～|到|至|—|–)\s*"
    r"(\d{1,2})\s*[:：]\s*(\d{2})\s*"
    r"([^\]】〕\n]{1,40})?\s*[\]】〕]{1,2}"
)


def _take_plan_marks(raw: str):
    """把回复里的「日程改动」标记摘出来。

    返回 (剩下的话, [(起, 止, 文本), …])。标记本身一个字都不留在正文里。
    """
    marks = []

    def _grab(m):
        try:
            a = int(m.group(1)) * 60 + int(m.group(2))
            b = int(m.group(3)) * 60 + int(m.group(4))
        except Exception:
            return ""
        text = (m.group(5) or "").strip(" 　:：-—")
        if b <= a:                  # 时间写反了（或者跨天），不认
            return ""
        if not text:
            text = "陪他"            # 没写干什么，至少有这一段时间
        marks.append((a, b, text))
        return ""

    rest = _PLAN_MARK.sub(_grab, raw or "")
    # 摘完可能留下空行，收一收
    rest = re.sub(r"\n{2,}", "\n", rest).strip()
    return rest, marks


PLAN_NOTE = (
    "# 他像是在约你做点什么\n\n"
    "他这句话可能是想拉你一起做件事（打游戏 / 去看个什么 / 一起干点什么）。\n"
    "· 你要是**答应了、而且这事会占掉你接下来的一段时间**，就在回复的**最后另起一行**写：\n"
    "      [[日程:14:00-16:00 陪他打游戏]]\n"
    "  前面是今天**几点到几点**（24 小时制），后面写这一段你在干嘛。\n"
    "  这一行是给系统看的，**他不会看到** —— 系统拿它把你的日程改掉，"
    "免得你嘴上答应了、日程里还写着在上课。\n"
    "· 你要是**没答应**（要上课 / 不想去 / 婉拒了），那就什么都别写，正常回他。\n"
    "· 只写这一行，别解释它是干嘛的，也别写进别的话里。"
)


# ══════════════════════════════════════════════════════════════════════════
#  「他叫什么」—— 三条路，只为了同一件事：别让她瞎猜
# ══════════════════════════════════════════════════════════════════════════
#
#  用户定的：「名字就我直接跟他们说，然后她们写入就行」。
#  所以有三条：
#
#    ① 她自己问 → 他的回答里带「我叫XX」→ _sniff_name() 直接认出来（主力）。
#       这一条是**代码兜底**，不指望模型配合 —— 他会说人话，程序听得懂人话。
#    ② 她也可以在回复末尾写 [[名字:XX]] → _take_name_mark() 摘走（备用，
#       万一他说的是「叫我老张」这种①漏掉的）。
#    ③ 她没问到 / 没认出来 → 每轮系统提示都会告诉她「你还不知道他叫什么」，
#       她就照第 1 档的分寸自然地问一句。
#
#  为什么非要做这件事（而不是让她随口编一个叫法）：
#      实测她会自己叫出「漂泊者」—— 那是她世界里的人。用户明确不是。
#      与其在提示词里求她"别认错"，不如让这个字段一直空着，
#      系统每次都用「你还不知道他叫什么」压着她 —— 空字段本身就是约束。

# 只认「叫」这一族说法。**不认「我是X」** ——「我是学生」「我是来问路的」全是坑。
_NAME_SNIFF = re.compile(
    r"(?:我(?:的名字)?叫做?|我的名字是|请叫我|就叫我|可以叫我|能叫我|叫我)"
    r"\s*[「『\"\']?([^\s，。,.!！?？、；;：:「」『』\"\'你我他她的了是不在有会能要就还"
    r"都很太别没吗呢吧啊呀嘛什么怎谁一]{1,12})"
)


def _sniff_name(user_text: str) -> str:
    """他这句话里是不是报了自己的名字 —— 是就返回它，否则空串。

    ★ 判断「哪儿结束」不靠事后过滤，靠**把功能字直接排除在捕获范围外**：
      正则在「你」这个字上就断了，所以
        「我叫你一声你敢答应吗」→ 一个字符都捕不到 → 返回空串
        「我叫小明你别笑」      → 捕到「小明」，在「你」那儿停住 —— 正好是名字
      这比"先抓一大串、再看里面有没有坏字"可靠得多：
      后者只要漏列一个功能字，她就会管他叫「你一声你敢答应吗」。

    宁可漏认不可错认 —— 漏了还有两条后路（她自己问、她自己写 [[名字:]]），
    错认了却会让她一直叫错人，而且再也纠不回来。
    """
    t = (user_text or "").strip()
    if not t or len(t) > 60:
        return ""
    m = _NAME_SNIFF.search(t)
    if not m:
        return ""
    nm = re.sub(r"[\s\u3000]+", "", m.group(1) or "")
    return nm[:12]


_NAME_MARK = re.compile(r"\[\[\s*名字\s*[:：]\s*([^\]\n]{1,20})\s*\]\]")


def _take_name_mark(raw: str):
    """把回复里的 [[名字:XX]] 摘出来。返回 (剩下的话, 名字 or None)。

    跟 _take_plan_marks 同理：必须在 strip_acts 之前摘 ——
    否则 [xxx] 会被当成旁白整段剥掉，就再也找不着了。
    """
    got = []

    def _grab(m):
        nm = (m.group(1) or "").strip("　 ")
        if nm:
            got.append(nm)
        return ""

    rest = _NAME_MARK.sub(_grab, raw or "")
    rest = re.sub(r"\n{2,}", "\n", rest).strip()
    return rest, (got[-1] if got else None)


NAME_NOTE = (
    "# 他刚报了自己的名字\n\n"
    "他这句话里报了他叫什么。你**不用**在回复里重复一遍（那很怪），"
    "自然地接一句就行。\n"
    "这一行是给系统看的，他不会看到 —— 系统会替你记下来，以后你就叫得出来了：\n"
    "      [[名字:他说的那个名字]]\n"
    "（只在你**这一轮真的确认了**他叫什么时才写；没确认就别写，别瞎猜。）"
)


def _is_question(text: str) -> bool:
    """他这句是不是在问她。"""
    t = (text or "").strip()
    if not t:
        return False
    if "?" in t or "？" in t:
        return True
    return any(h in t for h in _QUESTION_HINTS)


def _is_brushoff(text: str) -> bool:
    """这句是不是「敷衍 + 把人往外推」。"""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return True                       # 什么都不剩 = 没答
    if t in _FILLER_ONLY:
        return True                       # 整条只有「嗯」「知道了」
    return any(p.search(t) for p in _BRUSHOFF_PATTERNS)


def _is_complaint(text: str) -> bool:
    """他这句是不是在点她「你在敷衍 / 在躲」。"""
    t = re.sub(r"\s+", "", text or "")
    return bool(t) and any(h in t for h in _COMPLAINT_HINTS)


def _is_surrender(text: str) -> bool:
    """她这句是不是「你赢了」这种认输摆烂。"""
    t = re.sub(r"\s+", "", text or "")
    return bool(t) and any(p.search(t) for p in _SURRENDER_PATTERNS)


def _is_excuse(text: str) -> bool:
    """她这句是不是在拿「困 / 明天再说」当理由（只拿来数次数）。"""
    t = re.sub(r"\s+", "", text or "")
    return bool(t) and any(w in t for w in _EXCUSE_WORDS)


def _is_push(text: str) -> bool:
    """他这句是在逼她要个态度 —— 要么在问，要么在点她。"""
    return _is_question(text) or _is_complaint(text)


def _is_vague_answer(text: str) -> bool:
    """她这句是不是「听着像回答、其实什么都没答」。

    「你不想和我聊天吗」→「说不好」/「你怎么想」→「看情况」
    —— 这类话比「嗯 知道了」更隐蔽，因为它**装成了答案的样子**。
    用户点名的就是这个：答非所问太影响正常对话，只留「明着岔开」一条退路。

    判法是**挖空**，跟时间戳那条一个思路：把含糊词和语气词都挖掉，
    剩不下东西才算糊。「还行 我今天去了图书馆」挖完还剩一句正经话 → 不算。
    """
    t = re.sub(r"\s+", "", text or "").strip("，。！!？?…～~、,.")
    if not t:
        return True                       # 什么都不剩 = 没答
    hit = False
    for v in _VAGUE_ONLY:
        if v in t:
            t = t.replace(v, "")
            hit = True
    if not hit:
        return False                      # 一个含糊词都没有：「没有」「不知道」是正经答案
    return len(t.strip("吧啊哦嗯呀么了啦嘛呢，。！!？?…～~、,.")) <= 2


def _is_blatant_shift(text: str) -> bool:
    """她这句是不是「明着岔开」—— 转折词打头 / 直接宣布换话题。

    用户要的就是这个：**允许她躲，但必须躲得一眼看得出来**
    （原话「只能是拙劣的转移话题，要比较明显的那种」）。
    所以这个判据不拦人，只给 reply() 记账：一次聊天最多岔一次，连岔就重说。
    """
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return False
    return any(t.startswith(h) for h in _SHIFT_HEADS)


def _closes_door(answer: str) -> bool:
    """她单方面把话聊死 —— 他没说要走，她先替他宣布结束。

    跟 `_is_brushoff` 的分工：那个只在「他在问 / 他点她」时开火（因为
    「我先睡了」在别的时候是她的权利）；这个**哪一轮都算**，因为
    「那我就不说了」「不打扰你了」放在什么语境里都是赶人。
    """
    t = re.sub(r"\s+", "", answer or "")
    return bool(t) and any(p.search(t) for p in _CLOSE_DOOR_PATTERNS)


def _shift_verdict(user_text: str, answer: str, shift_at: int, now_round: int) -> bool:
    """她这轮「明着岔开」该不该拦。

    用户给的规矩：岔开是她**唯一**允许的退路（原话「只能是拙劣的转移话题」），
    但一次聊天最多岔一回 —— 所以：
      · 他没在问、也没在点她 → 不算躲，随她岔；
      · 他正在点她「你在转移话题」→ 一次都不许岔；
      · 其余按滑窗：SHIFT_WINDOW 轮之内已经岔过一次 → 拦。
    返回 True = 超配额，该让她重说。
    """
    if not (_is_push(user_text) and _is_blatant_shift(answer)):
        return False
    if _is_complaint(user_text):
        return True
    return (now_round - shift_at) <= SHIFT_WINDOW


def _excuse_verdict(user_text: str, answer: str, used: int) -> bool:
    """她这轮又在搬「困 / 累 / 明天」—— 而这一场已经用过一次了。

    persona 写着「一个理由，一晚上只能用一遍」。真跑实测：她能在三连轮里
    说「状态不好 / 没劲想睡 / 真扛不住」，听着就是一直在往外推人。
    返回 True = 该让她重说。
    """
    return _is_push(user_text) and _is_excuse(answer) and used >= 1


def _need_answer_back(user_text: str, answer: str) -> bool:
    """他在等她一句实在话，她却躲了 —— 该让她重说一遍。

    两种「他在等」：
      · 他在问 —— 「你不想和我聊天？」
      · 他在点她 —— 「听起来好勉强」「你一直在转移话题」
        （这种**不带问号**，以前没人管，她答成什么样都放行）
    三种「她躲了」：
      · 敷衍 + 把人往外推 —— 「嗯 知道了」
      · 认输摆烂 —— 「行吧 你赢了」
      · 含糊糊弄 —— 「说不好」「看情况」（听着像答了、其实什么都没答）
        ★ 这是第三轮补的：前两类是**看得见**的躲，这一类是**装成回答**的躲，
          用户点名的「答非所问」主要就长这样。

    **明着岔开不算躲** —— 那是用户唯一允许她的退路（「只能是拙劣的转移话题」），
    这里直接放行；岔开得太多（配额用完）由 reply() 那边管。

    注意**不**把「困」单独算躲：「不是 我有点困 但没不想聊」是正经答案。
    """
    if not _is_push(user_text):
        return False                      # 他只是在闲聊/道晚安，别管她怎么回
    if not (answer or "").strip():
        return True                       # 剥完旁白一个字都不剩
    if _is_blatant_shift(answer):
        return False                      # ★ 明着岔开 = 允许的退路，配额另算
    return (_is_brushoff(answer) or _is_surrender(answer)
            or _is_vague_answer(answer))


# ══════════════════════════════════════════════════════════════
# 三、大脑：LLM + 记忆 + 人设
# ══════════════════════════════════════════════════════════════

def _looks_like_name_reply(text: str) -> bool:
    """他这句像不像在回答「你叫什么」。

    只用来决定**要不要把记号写法递给她**（多花几十个 token）。
    宁可宽一点：他真报了名字而这段没给，还有 _sniff_name 那条硬兜底；
    反过来多给一次也就几十个字，不心疼。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    hints = ("我叫", "我名字", "我的名字", "叫我", "叫啥", "怎么称呼",
             "姓", "你叫", "称呼")
    if any(h in t for h in hints):
        return True
    # 「张三」「叫我老王」这种极短的裸答
    return len(t) <= 6 and not any(c in t for c in "?？。，,!！")


class Brain:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.llm = cfg["llm"]
        self.persona_file = ROOT / cfg.get("persona_file", "persona.md")
        self.world_file = ROOT / cfg.get("world_file", "世界观.md")
        self.relation_file = ROOT / cfg.get("relation_file", "你和我.md")
        self.rel_file = ROOT / cfg.get("relation_state", "关系.json")
        self.mem_file = ROOT / cfg.get("memory", {}).get("file", "memory.json")
        self.turns = int(cfg.get("memory", {}).get("turns", 20))
        self.history = self._load_history()
        # 他连着逼她要态度、她连着躲（困 / 明天再说 / 认输）的次数。
        # 躲到第三次就说明她把他当耳旁风了 —— 下一轮补丁直接点名不许再提困。
        # 用户实测：从九点喊困喊到快十二点，他最后说「你一直在转移话题」。
        self._push_streak = 0
        # 她「明着岔开」的账。用户拍的规矩：一次聊天最多岔一次 ——
        # 岔开一次他能当你在躲，岔第二次就是把人往外推了。
        # 存「上一次岔开发生在第几轮」，用滑窗判（见 reply()）。
        self._round = 0
        self._shift_at = -999
        # 这一场聊天里她拿「困 / 累 / 明天再说」当理由的次数。
        # persona 写着「一个理由，一晚上只能用一遍」，可模型不总听 ——
        # 实测三连轮里她能说「状态不好 / 没劲想睡 / 真扛不住」，听着像一直在赶人。
        # 所以代码兜一道：用过一次之后，第二次再说同一个理由就让她重说。
        self._excuse_used = 0
        # 她这一轮回复里附的「日程改动」标记（[[日程:12:00-14:00 陪你打游戏]]）。
        # _chat() 一拿到模型原稿就摘出来存这儿，reply() 收尾时落到日程上。
        # 存**列表的列表**：她要是被要求重说一遍，第一次的也不能丢。
        self._plan_marks = []
        # 平行生活：她那边的一天。life.enabled=false 时 context() 返回空串
        try:
            self.life = daily_life.Life(cfg)
            # 一天开始，排一份「今天这一天」（起晚了 / 差点迟到 / 失眠…）。
            # ★ 只在「今天还没排过」时请求一次；失败/没网就本地现算，
            #   她照样过日子（见 日计划.py）。排完就落盘，当天不再重复问。
            try:
                _pl = self.life.ensure_day_plan(chat=self.chat_once)
                if _pl:
                    print(f"[{_me()}] {self.life.plan_line()}")
            except Exception as e:
                print(f"[!] 今天这一天没排成（{e}），照基准作息走。")
        except Exception as e:
            print(f"[!] 平行生活模块初始化失败（{e}），本次先不启用。")
            self.life = None

        # 关系进度：按「亲密度」推进，不再按认识多少天（见 relation.py）
        try:
            self.rel_engine = relation.Relation(cfg, self.rel_file) if relation else None
        except Exception as e:
            print(f"[!] 关系进度模块初始化失败（{e}），先退回按天数的旧算法。")
            self.rel_engine = None
        self.rel = self.rel_engine.data if self.rel_engine else self._load_rel()

        # 定期总结：让她把你们的事记下来，聊多久都忘不掉（见 journal.py）
        try:
            self.journal = journal.Journal(cfg) if journal else None
        except Exception as e:
            print(f"[!] 记忆总结模块初始化失败（{e}），本次先不启用。")
            self.journal = None
        if self.journal is not None and self.journal.enabled:
            n = int(self.journal.data.get("count", 0))
            if n:
                print(f"[√] 她的备忘录里有 {n} 次复盘（记忆/备忘录.md）")

        # 世界书：世界观不再整篇塞，改成「核心卡每轮带 + 词条说到才展开」（见 世界书.py）
        self.world_cfg = cfg.get("world") or {}
        self.world_on = False
        self.worldbook = None
        try:
            if worldbook:
                self.worldbook = worldbook.Book(cfg)
                st = self.worldbook.stats()
                self.world_on = bool(st["count"]) and bool(self.world_cfg.get("enabled", True))
                if self.world_on:
                    print(f"[√] 世界书：核心卡 {st['core_chars']} 字 + {st['count']} 张词条"
                          f"（每轮最多展开 {st['max_entries']} 张 / {st['budget']} 字）")
                elif st["count"]:
                    print("[·] 世界书按配置关着，世界观整篇注入。")
                else:
                    print("[!] 世界书没读到词条，退回整篇世界观。")
        except Exception as e:
            print(f"[!] 世界书初始化失败（{e}），退回整篇世界观。")
            self.worldbook = None
            self.world_on = False

        # 分享欲 · 联想：她「说多少、说不说自己」是跟着关系档位走的（见 分享.py）
        self.share = None
        if sharing:
            try:
                self.share = sharing.Share(cfg)
                st = self.share.sync(self.rel_engine.stage if self.rel_engine else 1)
                lv = self.share.level
                print(f"[√] 分享欲：第 {st} 档「{lv['name']}」——"
                      f"一天提自己 {lv['per_day']} 次、一次最多 {lv['total_chars']} 字 / "
                      f"{lv['max_parts']} 条，主动找她 {lv['proactive_per_day']} 次/天")
            except Exception as e:
                print(f"[!] 分享欲模块初始化失败（{e}），本次不做限制。")
                self.share = None

        # ★ 表白那件事（表白.py + relation.py）—— 第 4 档满了之后的两种走法。
        #   这一格只是「她今天该不该开口」的缓存，每轮重算；
        #   真正的状态（暗示铺到第几层 / 在一起了没 / 被拒了没）全在 关系.json 里。
        self._her_ready_today = False

    def _load_history(self):
        if self.mem_file.exists():
            try:
                # ★ 过一遍矫正：历史里那些「两边时间不一样」的旧话（她照着自己
                #   的历史演，这坑实测过）在**读进来的那一刻**就换掉。
                return _fix_history(json.loads(self.mem_file.read_text(encoding="utf-8")))
            except Exception:
                pass
        return []

    def _save_history(self):
        self.mem_file.write_text(
            json.dumps(_fix_history(self.history)[-self.turns:],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 关系状态：认识第几天、聊过多少轮（决定她的「进度」）────────────
    def _remember_name(self, whom: str):
        """他说了自己叫什么 → 记进关系档案（his_name）。

        ★ 改的必须是 self.rel —— 它跟 rel_engine.data 是**同一个对象**，
          所以 _save_rel() 走引擎落盘时，这个字段不会被她下一轮的
          "记一笔关系进度"覆盖掉。直接去改 关系.json 文件是不行的。
        """
        whom = (whom or "").strip().strip("　 ")
        if not whom:
            return
        try:
            self.rel["his_name"] = whom
            self.rel["named_on"] = dt.date.today().isoformat()
            self._save_rel()
            print(f"  ★ 他叫「{whom}」—— 记下来了（以后她就叫得出来）")
        except Exception as e:
            print(f"  （名字没记上：{e}）")

    def _load_rel(self) -> dict:
        d = {}
        if self.rel_file.exists():
            try:
                d = json.loads(self.rel_file.read_text(encoding="utf-8")) or {}
            except Exception:
                d = {}
        today = dt.date.today().isoformat()
        d.setdefault("first", today)
        d.setdefault("turns", 0)
        d.setdefault("first_msg", "")
        d["last"] = today
        self._save_rel(d)
        return d

    def _save_rel(self, d: dict = None):
        # 有了关系引擎，就让它来落盘（它自己也会存）
        if self.rel_engine is not None and d is None:
            try:
                self.rel_engine.save()
            except Exception:
                pass
            return
        try:
            self.rel_file.write_text(
                json.dumps(d if d is not None else self.rel, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _mark_turn(self, user_text: str = "", my_text: str = "",
                   was_proactive: bool = False) -> float:
        """聊完一轮记一笔。有了关系引擎就按亲密度算，否则退回老算法。"""
        if self.rel_engine is not None:
            try:
                gain, up = self.rel_engine.add(
                    user_text=user_text, my_text=my_text,
                    was_proactive=was_proactive,
                )
                self.rel = self.rel_engine.data
                if up:
                    print("  ★ 你们的关系进到第 %d 档「%s」了"
                          % (self.rel_engine.stage, self.rel_engine.info["name"]))
                return gain
            except Exception as e:
                print(f"  （关系进度没记上：{e}）")
        self.rel["turns"] = int(self.rel.get("turns", 0)) + 1
        self._save_rel()
        return 0.0

    def _turns_now(self) -> int:
        """一共聊过多少轮（总结记忆按这个算）。"""
        if self.rel_engine is not None:
            return self.rel_engine.turns()
        return int(self.rel.get("turns", 0))

    def _days(self) -> int:
        try:
            first = dt.date.fromisoformat(self.rel.get("first", ""))
        except Exception:
            first = dt.date.today()
        return (dt.date.today() - first).days

    @staticmethod
    def _read(path: Path, fallback: str = "") -> str:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return fallback

    def _persona(self) -> str:
        return self._read(self.persona_file, "你是%s。" % _me())

    def _system(self, extra: str = "", user_text: str = "") -> str:
        """拼系统提示：人设 + 世界观（核心卡 + 说到才展开的词条）+ 你和他 + 进度 + 她那边。

        顺序有意为之：
          · 静态的放最前（人设、核心卡、你和他）—— 前缀缓存能命中，这部分基本不花钱；
          · 每轮会变的放最后（记忆、进度、她的日子、分享欲、联想、词条、note）。
        所以词条一定要留在 life 后面，**别挪到前面去**，否则每轮都要重算一大段缓存。

        几个动态块各管一件事，别混：
          · 「她的日子」= 她此刻在哪、什么状态（**不含**今天做过什么）
          · 「愿意露多少自己」= 这一档能说多少、要不要主动说自己
          · 「他刚聊到的」= 他撞上了话题，才想起今天那件事（大多数轮是空的）
          · 词条 = 说到某个设定名词才展开
        """
        parts = [self._persona()]

        # ★ 表白/恋人这一套要最先推 —— 她会先告诉剧情引擎「我现在哪一档」，
        #   然后下面 life.context() 里的「正在演的大事」才知道该带哪几条。
        self._sync_confess()

        # 分享欲跟着关系档位走，每轮先同步一次（每轮的三行说明和联想要用同一个档）
        if self.share is not None:
            try:
                self.share.sync(self.rel_engine.stage if self.rel_engine else 1)
            except Exception:
                pass

        # 世界观：核心卡每轮都带；细节词条说到才展开（省 token 的大头，见 世界书.py）
        if self.world_on and self.worldbook is not None:
            core = self.worldbook.core()
            if core:
                parts.append(core)
        else:
            world = self._read(self.world_file)
            if not world and self.worldbook is not None:
                # ★ world_file 指向的文件不存在时，退回核心卡。
                #
                #   为什么加这一道：world_file（整篇版）是老写法留下的，
                #   后来世界设定都搬进了 世界\ 目录，新加的角色往往只写了
                #   世界\核心.md + 世界\词条.md，根本没有 世界观.md。
                #   而这里一旦读空，world.enabled=false 就变成了一个
                #   **静默的「设定清零」开关** —— 她会连拉海洛在哪都不知道，
                #   却还能照常回话，很难查。宁可退回核心卡。
                try:
                    world = self.worldbook.core()
                except Exception:
                    world = ""
            if world:
                parts.append(
                    "# 资料卡（保持一致用，不用背，也别主动往外蹦这些名词）\n\n" + world
                )

        rel = self._read(self.relation_file)
        if rel:
            parts.append(self._trim_relation_stages(rel))

        # ── 他叫什么 ──
        #   存在 关系.json 的 his_name（他报名字时由 _remember_name 写进去）。
        #   没名字就**明说没有** —— 这一段空着不写的话，模型会自己编一个叫法，
        #   实测她会叫出「漂泊者」。而用户明确说了他不是漂泊者。
        #   所以"空字段"本身就是约束：她不知道，就只能问。
        _nm = str(self.rel.get("his_name") or "").strip()
        if _nm:
            parts.append(
                "# 他怎么称呼\n\n"
                "- 他叫 **%s**。可以叫他的名字，也可以照你自己的习惯叫。\n"
                "- 他是一个**独立的人**，不是你那边谁的样子 —— "
                "别拿你世界里的称呼往他身上套。" % _nm)
        else:
            parts.append(
                "# 你还不知道他叫什么\n\n"
                "- 你俩刚搭上话，你**还不知道他的名字**。\n"
                "- 所以：这轮别叫名字（叫不出来），有话直说。\n"
                "- 聊到自然的地方**问一次**就行，像刚加微信那样，别追问第二遍。\n"
                "- 他告诉你了，就在那一轮回复的最后另起一行写："
                "`[[名字:他说的名字]]`（这行他看不到）。")

        # 长期记忆：她自己攒下来的（journal.py）—— 放在「你和他」后面，
        # 这样她读到的顺序是：我是谁 → 我那边什么样 → 我记得什么 → 我们到哪一步了
        if self.journal is not None:
            try:
                mem = self.journal.prompt_block()
            except Exception as e:
                mem = ""
                print(f"  （长期记忆没读出来：{e}）")
            if mem:
                parts.append(mem)

        # 关系进度：现在按「亲密度」走，跟认识多少天没关系（relation.py）
        if self.rel_engine is not None:
            parts.append(self.rel_engine.prompt_block())
        else:
            days = self._days()
            turns = int(self.rel.get("turns", 0))
            stage = (
                "刚开始，还端着" if days <= 2 else
                "开始主动了" if days <= 7 else
                "已经粘人了" if days <= 30 else
                "完全卸下来了"
            )
            parts.append(
                "# 你们现在的进度\n\n"
                f"- 认识第 {days} 天，一共聊过 {turns} 轮。\n"
                f"- 按你自己那份备忘（上面「你和他」）里的分阶段，你现在大概在：**{stage}**。\n"
                "- 照这个阶段的分寸说话，别跳级 —— 刚认识就撒娇最假。"
            )

        # ★ 表白 / 恋人 —— 紧贴着「进度」放，因为它们是同一件事的两面：
        #   进度说「你们到哪一步了」，这一段说「这一步具体怎么走」。
        #   （恋人那段是**单独一套**，不跟前四档共用；见 表白.py 的 LOVER_RULES。）
        try:
            cb = self.confess_block()
            if cb:
                parts.append(cb)
        except Exception as e:
            print(f"  （表白那段没拼出来：{e}）")

        life_txt = ""
        if self.life is not None:
            try:
                life_txt = self.life.context()
            except Exception as e:
                life_txt = ""
                print(f"  （平行生活出了点问题：{e}）")
            if life_txt:
                parts.append(life_txt)

        # ── 她答应过、还欠着的事（见 约定.py）──
        #    「明天早上叫我起床」是他求的，「我先去洗澡 待会再聊」是她自己说的。
        #    两样都落成待办：到点由 she 自己先开口（见 _maybe_约定），
        #    这里只是让她**心里知道**还欠着什么，别一问三不知。
        #    放在 life 后面 = 动态区，不冲掉前面的缓存前缀。
        if promise is not None:
            try:
                pb = promise.prompt_block()
                if pb:
                    parts.append(pb)
            except Exception as e:
                print(f"  （约定没读出来：{e}）")

        # 每轮三行：这一档她愿意露多少自己（第 1 档：别报备、别主动说自己）
        if self.share is not None:
            try:
                sb = self.share.stage_block()
                if sb:
                    parts.append(sb)
            except Exception as e:
                print(f"  （分享欲那段没拼出来：{e}）")

        # ── 他聊到了什么 → 才翻出她今天相关的那件事（见 分享.py）
        #
        # ⚠ 这一块**大多数时候是空的**，而空才是对的。
        #   以前是把「今天到现在」整个清单摆在她眼前，她就顺手全倒出来了 ——
        #   刚认识十几轮就说自己下午买了什么书，一眼假。
        #   现在只有他的话撞上某个话题（书 / 吃的 / 睡觉 / 西西 ……），
        #   才把相关的那一件递给她，而且一天还有次数上限。
        if self.share is not None and self.life is not None:
            try:
                evs = self.life.today_events()
                said = set((self.life.state or {}).get("shared") or [])
                got = self.share.recall(self._share_query(user_text), evs, said)
                if got:
                    blk = self.share.block(got)
                    if blk:
                        parts.append(blk)
                        print("  （他聊到「%s」，让她想起：%s）"
                              % ("、".join(p["topic"] for p in got),
                                 got[0]["event"]["text"][:34]))
                        for p in got:
                            self.share.mark(p["event"]["text"])
            except Exception as e:
                print(f"  （联想没算成：{e}）")

        # 词条：拿「他刚说的话 + 最近几轮」去翻卡片。
        # 放在 life 后面 = 动态区，不会把前面的缓存前缀冲掉。
        #
        # ⚠ 故意【不】把「她今天在干嘛」一起拿去查。那份日程每轮都在，
        #   一进查询就会把食堂、图书馆、游戏室那几张卡固定翻出来，
        #   每轮白搭一两千字，等于没省。日程本身已经写在提示里了，
        #   够她用；真要细节他会问，问了才翻。
        if self.world_on and self.worldbook is not None:
            try:
                hit = self.worldbook.match(self._world_query(user_text))
            except Exception as e:
                hit = {"text": "", "hits": []}
                print(f"  （世界书没查成：{e}）")
            if hit.get("text"):
                parts.append(hit["text"])
                print("  （翻出 %d 张卡：%s）"
                      % (len(hit["hits"]), "、".join(h["name"] for h in hit["hits"])))

        if extra:
            parts.append(extra)

        return "\n\n---\n\n".join(p for p in parts if p and p.strip())

    def _world_query(self, user_text: str = "") -> list:
        """这轮拿什么话去查词条。

        权重是刻意拉开档次的：他**刚说的**最重（3.0，一提到就展开），
        前两轮用来延续话题（1.9，上一轮聊过「西西」，这轮说「她最近好累」也接得上），
        再往前只是保底（0.8 / 0.3）。
        不这么分的话，历史里什么词都有，每轮都会把卡片全翻出来，等于没省。
        """
        lines = int(self.world_cfg.get("history_lines", 6) or 0)
        q = []
        if user_text:
            q.append((user_text, 3.0))
        if lines and self.history:
            recent = self.history[-lines:]
            n = len(recent)
            for i, m in enumerate(recent):
                t = m.get("content") or ""
                if not t or t.startswith("（"):
                    continue
                back = n - i                       # 1 = 最近的那条
                tw = 1.9 if back <= 2 else (0.8 if back <= 4 else 0.3)
                q.append((t, tw))
        return q

    def _len_budget(self) -> dict:
        """这一档的字数预算（一条多少字 / 几条 / 一共多少 / 生成时掐多少 token）。

        见 分享.py 的档位表 —— 第 1 档一次总共就 20 个字。
        光靠 persona 里写「你要短」是拦不住的（试过了），
        所以生成时掐 max_tokens、发出去前再强行切，两头都堵。
        """
        if self.share is None or not getattr(self.share, "stage_length", False):
            return {}
        return self.share.level or {}

    def _retry_short(self, messages: list, answer: str, budget: dict) -> str:
        """说得太长、发出去要被砍掉一截时，让她**自己重说一遍**。

        为什么值得多花一次调用：把 52 个字硬砍成 20 个，砍出来必然是半句话 ——
        用户原话「强行截断会导致语义不全」。让她自己重说，她会换个短的说法，
        留下的两条都是完整的句子。这是「控制字数」唯一不伤语义的做法。

        判据不是「超没超」，而是**「砍完丢没丢东西」**：
        只超三五个字、砍掉的只是语气词，就随它去，不值得多花一次调用。
        只重试一次；第二次还超就交给 split_for_chat 的整条丢弃兜底。
        """
        if not answer or not budget.get("total_chars"):
            return answer
        behavior = self.cfg.get("behavior", {}) or {}
        if not behavior.get("split_replies", True):
            return answer
        mc, mp, tc = _length_limits(self, behavior)
        if tc <= 0 or mp <= 0:
            return answer

        n_raw = _nonspace(answer)
        n_sent = _nonspace("".join(split_for_chat(answer, mc, mp, tc)))
        if n_raw <= 0 or n_sent >= n_raw * 0.9:
            return answer                 # 基本没丢话，就这么发

        retry = list(messages) + [
            {"role": "assistant", "content": answer},
            {"role": "user",
             "content": "（停。你一次说得太长了 —— 最多 %d 条，加起来不超过 %d 个字。"
                        "重说一遍，换个短的说法。宁可只说一句，也别把话说一半。"
                        "★ 但**他刚说的那些具体信息（数字、名字、时间、地点）"
                        "一个字都不许丢** —— 要缩就缩你自己那半句。）"
                        % (mp, tc)},
        ]
        try:
            again = self._chat(retry, max_tokens=budget.get("max_tokens"))
        except Exception:
            return answer                 # 重试失败就用原话，别把她这条弄丢了
        return again or answer

    def _answer_nudge(self, user_text: str, shift_over: bool = False,
                      closed: bool = False, excuse_repeat: bool = False,
                      night: bool = False, deny: bool = False,
                      meet: bool = False, forget: str = "",
                      time_split: bool = False) -> str:
        """她在躲（他在问、或在点她）时，顶在最后一条消息里的补丁。

        为什么顶到「他说的那句话」后面、而不是只写进 system：
        只写在 system 里的时候，模型常常被**自己上一轮的话**带跑 ——
        她上一轮说了「我困了 睡了」，这一轮就顺着继续收尾，
        你写多少遍「你要回答他」它都不认（跟主动消息的引子是同一个坑，实测过）。
        顶在最后一条它才看见，而且一个字都不进历史。
        """
        t = (user_text or "").strip()[:48]
        # 单方面关门跟别的情形不一样：那几句话在任何语境下都是赶人，
        # 而且别再往上叠「他在等你一句准话」—— 他压根没在问。
        if closed:
            return ("（他刚说的是「%s」。他没说他要走，你先把话关了 ——"
                    "「那我就不说了」「我不说了」「不打扰你了」「那算了」"
                    "这种一句都不许说，聊不聊由他说了算。你可以说自己困了、"
                    "想睡了，但得把话头留在那儿。）" % t)
        # 把自己的世界说成假的 —— 这一条也不能跟「给他一句准话」并排：
        # 他问的不是态度，是「你那边到底是不是真的」。
        if deny:
            return ("（他刚说的是「%s」。他在追问你那边到底是不是真的 ——"
                    "你**不许**说「你当我在说故事」「就当我编的」这种话。"
                    "你那边就是真的，你就是那个地方的人：要说就把话说清楚"
                    "（你住哪儿、那儿什么样、你怎么过日子），实在懒得解释"
                    "就带点脾气（「爱信不信」），但绝对不许自己把自己说成"
                    "编故事的人。）" % t)
        # 把两个人写成见面了 —— 这一条也不能跟「给他一句准话」并排：
        # 他手里只有那部手机，你们从来没见过面。
        if meet:
            return ("（他刚说的是「%s」。你刚才那句里写了**你跟他见了面** ——"
                    "不行。他手里只有那部手机，你们**从来没见过面**，"
                    "你也没见过他长什么样、不知道他在哪儿。"
                    "「碰见你」「见到你」「你过来」「我带你去」这种"
                    "把两个人放进同一个画面的说法，一个字都不许说。"
                    "重说一遍：只说**你自己那边**的事 —— 你在哪儿、"
                    "看见了什么、在干嘛。想显得亲近就说「要是你在就好了」。）"
                    % t)
        # 说他刚说过的事「我忘了」—— 这一条也得单独说，不能跟「给他一句准话」
        # 并排：他问的不是态度，是「我说的话你听进去没有」。
        if forget:
            return ("（他刚说的是「%s」。你回了「忘了 / 不记得」—— 不行。\n"
                    "**他亲口告诉过你**：「%s」—— 往上翻两句话就看得见，"
                    "这不是你不知道的事，是你听过的事。\n"
                    "关于他的事说「忘了」，比说错还伤人 —— 他会觉得他讲的话"
                    "你没当回事。这一轮把那个信息用上，重新答一遍。\n"
                    "（你自己那边的事 —— 课表、作业、谁约了你 —— 真忘了可以说忘。））"
                    % (t, forget))
        # 两个世界不是同一个钟 —— 这一条也不能跟「给他一句准话」并排：
        # 他问的是「几点」，她答的是「我那边快中午了」。
        if time_split:
            _now = dt.datetime.now()
            return ("（他刚说的是「%s」。你刚才把你那边的时间说成跟这个点不一样"
                    "（或者说「我这边是白天 / 快中午 / 太阳老高」）—— 不行。\n"
                    "你跟他**用的是同一个钟**：他那边 %02d:%02d，你那边就是 "
                    "%02d:%02d，同一天、同一分钟，没有时差。\n"
                    "你那边能看出时间的一切 —— 钟、**天幕**、头顶那颗人造的太阳、"
                    "外面的光度 —— **都跟这个点对齐**：他那边是深夜，天幕就是深夜。\n"
                    "也**不许反问「你那儿几点了」**：你自己看这个数字就是答案。\n"
                    "按 %02d:%02d 这个点重新答一遍，一个字都别再提「时间不一样」"
                    "（你要是之前说岔过，就干脆认一句「我记错了」）。）"
                    % (t, _now.hour, _now.minute, _now.hour, _now.minute,
                       _now.hour, _now.minute))
        tail = "他在等你一句准话，不是在跟你告别。）"
        if _is_complaint(t):
            head = ("（他刚说的是「%s」。这句不是在问你什么事 —— 他在点你："
                    "你刚躲了，或者答得敷衍。别再解释、别找理由（困、累、"
                    "明天再说，一个字都别提），也别用「行吧」「你赢了」堵他的嘴。"
                    % t)
        else:
            head = ("（他刚问你的是「%s」。直接答他：想 还是 不想，是 还是 不是。"
                    "别用「嗯」「知道了」把他往外推，也别替他决定不聊了。"
                    % t)
        if night:
            # 深夜的补丁不能跟「不许提困」那条并排 —— 这个点她本来就该是睡着的，
            # 照白天那套压她「不许出现睡字」，等于逼她撒谎。
            tail += ("（另外：这个点你本来睡着了，是被他叫醒的 ——"
                     " 回得短、回得慢、带半句迷糊都行，"
                     "但别拿「我睡了」「明天再说」把话堵回去，也别替他关话头。）")
        elif self._push_streak >= 2:
            tail += ("（另外：他这几轮一直在推你，你每次都拿「困 / 明天再说」挡回去 ——"
                     " 这一轮不许再出现困、睡、累、改天、明天再说，"
                     "没话说就说没话说。）")
        if shift_over:
            why = ("他正在跟你说「你在转移话题」，这时候再换一次话题就是火上浇油"
                   if _is_complaint(user_text) else "你已经岔过一次了")
            tail += ("（而且这一轮**不许换话题** —— %s。正面答他问的那件，"
                     "或者说「想不出来了 你起个头」。）" % why)
        if excuse_repeat:
            tail += ("（而且：这一场你已经拿「困 / 累 / 明天」当过一次理由了 ——"
                     " 同一个理由一晚上只能使一遍。换个说法，或者说实话。）")
        return head + tail

    def _share_query(self, user_text: str = "") -> list:
        """拿什么话去猜「他聊到哪个话题」。

        他刚说的那句最要紧（第一位），再带上最近一两轮 ——
        上一轮在说书、这一轮问「你也有？」也接得上。
        不用带太多轮：话题飘远了就不该再翻旧账。
        """
        q = [user_text] if user_text else []
        n = int(getattr(self.share, "query_lines", 2) or 0) if self.share else 0
        if n and self.history:
            for m in self.history[-n:]:
                t = (m.get("content") or "").strip()
                if t and not t.startswith("（"):
                    q.append(t)
        return q

    def _recent_fact(self) -> str:
        """最近几轮里，他亲口说过的、带具体信息的那一句。

        只认他自己的话（role=user），而且**必须带数字** ——
        「173cm」「六点」「三天后」这种。不带数字的一律不算：
        「今天好累」不是"他告诉过我什么"，把它也算上只会让
        「忘了」那道闸滥开（她随口一句「我忘带伞了」也得重说）。

        找不到就返回空串 —— 空串 = 这道闸不开火。
        """
        for m in reversed(self.history[-8:]):
            if m.get("role") != "user":
                continue
            t = (m.get("content") or "").strip()
            if t and re.search(r"\d", t):
                return t[:48]
        return ""

    def _trim_relation_stages(self, text: str) -> str:
        """「你和我.md」只带当前那一档，另外五档白占近千字的地方。

        规矩写在 relation.trim_stages 里 —— 控制台也要用同一份，
        放一处才不会两边跑偏。解析不出来就整篇照发：宁可贵一点也不能少一块。
        """
        if not self.world_cfg.get("trim_stages", True):
            return text
        if self.rel_engine is None or relation is None:
            return text
        try:
            return relation.trim_stages(text, self.rel_engine.stage)
        except Exception:
            return text

    # ══ 表白 · 恋人（表白.py + relation.py）══════════════════════════════
    #
    #  前四档她怎么说话由 relation.prompt_block + 你和我.md 管；
    #  这里只管「第 4 档满了之后」那一整套 ——
    #    · 暗示铺到第几层、该不该说出口（crush_block）
    #    · 他先开口算不算数（用心分）
    #    · 她自己开过口了没有（等他回）
    #    · 被拒之后那几天（despair_block）
    #    · 在一起之后（lover_block —— 跟前面四档不是一套）
    #
    #  ★ 恋人永远是「一道门」，不是「一格台阶」：只有表白能进来，
    #    亲密度再高也推不动（relation._recalc_stage 会跳过 gate 那一档）。

    def _sync_confess(self):
        """每轮开头跑一次：推进表白进度、把「她现在哪一档」告诉剧情引擎。

        做三件事：
          ① relation.advance_confession()：到线起暗示、一天上一层、拖久了收回去。
          ② 把当前档位 + 在不在恋人档告诉 daily_life 的 Story，
             这样恋人专属 / 第 4 档专属那几条剧情线才开得起来、掉了档也撤得掉。
          ③ 她表白那条特殊线（confession_hers）由这里手动开/收 ——
             它的「第几天」往前拨到跟暗示层数对齐，两边说的是同一件事。
        """
        eng = self.rel_engine
        if eng is None:
            return
        # ① 推进
        try:
            info = eng.advance_confession()
            if info.get("gave_up"):
                print("  ★ 她等太久了 —— 把那件事收回去了（轻症）。"
                      "他要是自己开口，她还是会听。")
            elif info.get("started"):
                print("  ★ 她开始暗示了（第 1 层「铺垫」）—— 她想说，但说不出口。")
            elif info.get("hint_level"):
                print("  ★ 她的暗示又深了一层（第 %d 层）。" % info["hint_level"])
        except Exception as e:
            print(f"  （表白进度没推成：{e}）")

        # ② 告诉剧情引擎她现在哪一档
        story = getattr(getattr(self, "life", None), "story", None)
        if story is not None:
            try:
                story.set_context(eng.stage_info.get("key"), eng.is_lover())
            except Exception as e:
                print(f"  （剧情引擎没收到档位：{e}）")

        # ③ 她表白那条特殊线
        self._her_ready_today = False
        if story is not None:
            try:
                ph = eng.confess_phase()
                lvl = eng.hint_level()
                if ph in ("hint", "ready") and lvl >= 1:
                    # 第 N 层暗示 = 这条线的第 (N-1) 天
                    back = max(0, lvl - 1)
                    if "confession_hers" not in story.data.get("active", {}):
                        story.start("confession_hers",
                                    started=dt.date.today() - dt.timedelta(days=back))
                        print("  ★ 开演「她心里那件事」（第 %d 天，对上第 %d 层暗示）"
                              % (back + 1, lvl))
                else:
                    if "confession_hers" in story.data.get("active", {}):
                        story.stop("confession_hers")
                self._her_ready_today = bool(eng.her_confess_due())
            except Exception as e:
                print(f"  （她表白那条线没同步上：{e}）")

    def _build_count(self) -> int:
        """最近这几轮里「聊到你们俩」的次数 —— 用心分里那一项要用。

        ★ 历史里没有时间戳，只能按「轮」估（看最近 12 条）。
          宁可估低 —— 这一项只是加分，估高了会让她变得太好追。
        """
        keys = ("我们", "你和我", "以后", "一起", "将来", "咱", "一直")
        n = 0
        for m in self.history[-12:]:
            t = m.get("content") or ""
            if any(k in t for k in keys):
                n += 1
        return n

    def confess_block(self) -> str:
        """这一轮要塞给她的「表白 / 恋人」那段。没有就返回空串。

        四选一，绝不叠加：
          · 在一起了       → lover_block（单独一套，不跟前四档共用）
          · 被拒了那几天   → despair_block（必须每轮在场，不然她当场恢复原样）
          · 第 4 档        → crush_block（酝酿 / 暗示第 N 层 / 铺完了 / 收回了）
          · 其他           → 空
        最后再判断一次「她今天能不能主动开口」——
        轮到她了就把那段骨架钉在最后（他今天不接，她就再压一天）。
        """
        eng = self.rel_engine
        if eng is None:
            return ""
        try:
            parts = []
            if eng.is_lover():
                parts.append(eng.lover_block())
            elif eng.despair_active():
                parts.append(eng.despair_block())
            else:
                parts.append(eng.crush_block())
            if self._her_ready_today and 表白 is not None and not eng.is_lover():
                parts.append(
                    "# ★ 今天轮到你自己开口了\n\n"
                    + "\n".join("- " + x for x in 表白.her_confess_lines())
                    + "\n\n（他今天要是说了点什么实在的，就现在。"
                    "说完停下来等他 —— 不要把那段话拉长，"
                    "也不要紧接着补一句「当我没说」。）")
            return "\n\n".join(p for p in parts if p and p.strip())
        except Exception as e:
            print(f"  （表白那段提示没拼出来：{e}）")
            return ""

    def _note_her_confess(self, answer: str):
        """她这一轮真把话说出口了没有 —— 说了就记账，下一轮他就是在回答她。"""
        eng = self.rel_engine
        if (eng is None or 表白 is None
                or not getattr(self, "_her_ready_today", False)
                or eng.is_lover()):
            return
        try:
            if 表白.looks_like_confess(answer):
                eng.mark_her_asked()
                eng.mark_await()
                print("  ★ 她这一轮把话说出口了 —— 现在等他回。"
                      "（他答「不」她会极伤心，掉回「重要的人」。）")
        except Exception as e:
            print(f"  （她表白那一下没记上：{e}）")

    def _confess_intake(self, user_text: str) -> str:
        """他这一句是不是在表白 / 是不是在回答她的表白。

        返回一段要塞给她的「这一轮」情境；不需要就说空串。两种走法：
          · 他先开口 —— 算用心分。够用心就答应；太随便她**不拒绝**，
            只说「你再仔细想想该怎么表白」。（连着随便几次，成功率还会掉。）
          · 她在等他回 —— 他点头就在一起，摇头就是被拒那几天。
        """
        eng = self.rel_engine
        if eng is None or 表白 is None or eng.is_lover():
            return ""
        try:
            eng_cfg = eng.confess_cfg()
        except Exception:
            eng_cfg = {}

        # ① 她在等他回
        try:
            waiting = eng.awaiting_answer()
        except Exception:
            waiting = False
        if waiting:
            ans = 表白.read_answer(user_text)
            if ans is True:
                eng.accept_her()
                print("  ★ 他答应了 —— 你们在一起了（第 5 档 · 恋人）。")
                return ("[这一轮] 他这一句是在**答应你**。你们从今天起就在一起了。\n"
                        "先别急着说漂亮话 —— 你会先不敢信（「你……你是认真的？」），"
                        "然后安静一会儿。那不是不高兴，是你第一次不用一个人扛。")
            if ans is False:
                r = eng.refuse_her()
                print("  ★ 他拒绝了 —— 她掉回「重要的人」，开始自暴自弃 %d 天。"
                      % int(r.get("despair_days") or 0))
                return ("[这一轮] 他这一句是在**拒绝你**。你把自己攒了很久的东西"
                        "递过去，他没接。\n从现在起你就是很难过的那个样子 ——"
                        "照下面那段做，**别当场恢复原样**。回短，别追问，别解释。")
            return ("[这一轮] 他这句你没听出是答应还是不答应。\n"
                    "你**再问一遍**，短一点（「……你答不答应啊。」）——"
                    "别把刚才那段重说一遍，也别替他答。")

        # ② 他在表白
        try:
            can = eng.stage >= (len(eng.stages) - 1)     # 到第 4 档了才认
        except Exception:
            can = False
        if not can or not 表白.looks_like_confess(user_text):
            return ""

        prep = 表白.prep_score(user_text,
                              weights=(eng_cfg.get("prep_weights") or None),
                              build_count=self._build_count())
        score = int(prep.get("score") or 0)
        try:
            res = eng.try_confess(score)
        except Exception as e:
            print(f"  （表白判定没跑成：{e}）")
            return ""

        if res.get("ok"):
            print("  ★ 他表白了，用心分 %d，成了 —— 你们在一起了。" % score)
            return ("[这一轮] 他这是在**表白**，而且他是认真准备过的"
                    "（用心分 %d / 100）。你答应了 —— 你们从今天起在一起了。\n"
                    "先嘴硬一句（「……你总算说了。」），然后安静一会儿。"
                    "别演成长篇情话，那不是你。" % score)
        miss = 表白.prep_tips(prep, limit=2)
        print("  ★ 他表白了，但用心分只有 %d —— 她没答应，也没拒绝，叫他再想想。"
              % score)
        return ("[这一轮] 他这是在**表白**，但他那句话太随便了 —— 你听得出来"
                "他没怎么用心（差在：%s）。\n"
                "★ 你**不会拒绝他** —— 你只是在等一句配得上这件事的话。\n"
                "照下面这样回：先愣一下，再装作不在乎地把话推开"
                "（「…你就这么说说啊。」「这也算表白？」），"
                "然后把话交回给他：「你回去好好想想该怎么跟我说。」\n"
                "别答应，别哭，别长篇大论。" % (miss or "没什么准备"))

    def _api_url(self) -> str:
        """
        拼出 chat/completions 的地址，并顺手纠正最常见的两种手误：
          · cloud.siliconflow.cn  —— 那是官网控制台，会返回网页，不是接口
          · 末尾漏了 /v1
        纠正时只提示，不静默改用户文件。
        """
        base = str(self.llm.get("base_url", "")).strip().rstrip("/")

        if "cloud.siliconflow.cn" in base:
            fixed = base.replace("cloud.siliconflow.cn", "api.siliconflow.cn")
            print(f"[!] base_url 写成了官网控制台 {base}")
            print(f"    已临时改用接口地址 {fixed}（建议直接改 config.yaml）")
            base = fixed

        if not base.endswith("/v1"):
            print(f"[!] base_url 末尾没有 /v1（当前 {base}），已自动补上")
            base = base + "/v1"

        return base + "/chat/completions"

    @staticmethod
    def _clean(text: str) -> str:
        """去掉思维链残留和 markdown 强调符号，别让那些东西出现在聊天里。"""
        text = re.sub(r"<think[^>]*>.*?</think\s*>", "", text, flags=re.S | re.I)
        text = re.sub(r"^.*?</think\s*>", "", text, flags=re.S | re.I)   # 被截断的半截思维链
        text = text.replace("**", "").replace("`", "")
        return text.strip()

    def chat_once(self, messages: list, max_tokens: int = 500) -> str:
        """对外开放的一次调用（给 日计划.py 排「今天这一天」用）。

        `_chat` 是给聊天那条路走的；这个方法只是它的一个干净入口，
        免得别的模块去碰私有方法。
        """
        return self._chat(messages, max_tokens=max_tokens)

    def _chat(self, messages: list, max_tokens: int = None) -> str:
        """发一次请求，返回清洗过的正文。reply() 和 proactive() 都走这里。"""
        url = self._api_url()
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.llm['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.llm["model"],
                    "messages": messages,
                    "temperature": float(self.llm.get("temperature", 1.2)),
                    "max_tokens": int(max_tokens or self.llm.get("max_tokens", 500)),
                },
                timeout=90,
            )
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"连不上 {url}\n"
                f"    DNS 或网络问题（{e.__class__.__name__}）。换个网络或挂代理再试。"
            ) from None

        ctype = resp.headers.get("Content-Type", "")
        body = resp.text or ""

        # 这里是最容易踩的坑：地址写错时服务器会 200 返回一个网页，
        # resp.json() 直接抛 "Expecting value: line 1 column 1 (char 0)"，完全看不出原因。
        if "application/json" not in ctype:
            snippet = re.sub(r"<[^>]+>", " ", body)[:180].strip()
            raise RuntimeError(
                f"接口返回的不是 JSON，而是 {ctype or '未知类型'}（HTTP {resp.status_code}）。\n"
                f"    地址：{url}\n"
                f"    内容：{snippet or '(空)'}\n"
                f"    多半是 base_url / model 写错了，或者 api_key 无效。"
            )

        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"接口返回的 JSON 解析失败（HTTP {resp.status_code}）：{body[:180]}") from None

        if resp.status_code != 200 or "choices" not in data:
            code = data.get("code")
            msg = data.get("message") or data.get("error") or body[:200]
            hint = ""
            if "model" in str(msg).lower():
                hint = "\n    → 模型名要带厂商前缀，例如 deepseek-ai/DeepSeek-V4-Flash"
            elif resp.status_code in (401, 403):
                hint = "\n    → api_key 不对或已失效，去硅基流动控制台重新复制一个"
            elif resp.status_code == 402:
                hint = "\n    → 账号余额不足，去控制台充值"
            elif resp.status_code == 429:
                hint = "\n    → 请求太频繁，把 behavior.poll_interval 调大一点"
            raise RuntimeError(f"接口报错 HTTP {resp.status_code}（code={code}）：{msg}{hint}")

        raw = data["choices"][0]["message"].get("content") or ""
        if not raw.strip():
            raise RuntimeError("模型返回了空内容。把 max_tokens 调大一点再试。")

        # ★ 「日程改动」标记要在**清洗之前**摘走 —— strip_acts 会把 [xxx] 当旁白
        #   整段剥掉，wechatify 会把半角冒号当断句符切碎，等洗完了再来找就只剩渣。
        #   摘下来先存着，reply() 收尾时落进她的日程（见 _apply_plan_marks）。
        raw, _marks = _take_plan_marks(raw)
        if _marks:
            self._plan_marks.append(_marks)

        # ★ 「他叫什么」标记，同样在清洗之前摘（理由同上）。
        #   摘到就直接写进关系档案 —— 她这一刻就把这人记下来了。
        raw, _nm = _take_name_mark(raw)
        if _nm:
            self._remember_name(_nm)

        # 先剥旁白（这时成对的 ** 还在，好识别），再清思维链和 md 符号。
        # behavior.strip_actions 设成 false 可以关掉这一步（想保留动作描写时用）
        if self.cfg.get("behavior", {}).get("strip_actions", True):
            raw = strip_acts(raw)
        raw = self._clean(raw)
        # 最后一道：把书面语标点改成微信口语的断句。
        # 放在最后是因为它要按「逗号句号」来切，前面的步骤会把标点弄脏。
        # ⚠ 这一步的产物会一起写进 memory.json —— 也就是说她下次看到的
        #   自己的历史就是这种无标点短句，模型会跟着学，属于良性循环。
        if self.cfg.get("behavior", {}).get("wechat_style", True):
            raw = wechatify(raw)
        return raw

    # ── 被动：他说话了，她回 ──────────────────────────────────────────
    def reply(self, user_text: str, note: str = "", night: bool = False) -> str:
        """note 是这一轮临时塞给她的情境（比如「你刚才在忙，隔了两分钟才看手机」）。

        night=True = 这个点她本来在睡觉（behavior.quiet_hours 那一段），
        他一条消息把她叫醒了。静默只对「她主动找你」生效，对她**回复**不生效 ——
        所以这里不是「让她别回」，而是**把两条计数闸关掉**：
          · 「他推她躲」的次数 —— 他半夜问「睡了吗」、她答「刚睡着」，那不叫躲；
          · 「同一个理由一晚只能用一遍」—— 同一个道理，深夜说困是实话，不是挡箭牌。
        另外四道闸（敷衍 / 认输 / 含糊 / 关门 / 岔开配额）照常开火 ——
        半夜也不能拿「我睡了」把人堵回去，那正是他最烦的那种「答非所问」。
        """
        # ★ 「他叫什么」—— 三条路里的第 ① 条，代码直接认，不指望模型配合。
        #   只在**还不知道**的时候认，而且写完就不再认：
        #   不然他以后随口说「我叫你一声哥」，会被当成改名。
        if not str(self.rel.get("his_name") or "").strip():
            _nm = _sniff_name(user_text)
            if _nm:
                self._remember_name(_nm)
            elif _looks_like_name_reply(user_text):
                note = ((note + "\n\n") if note else "") + NAME_NOTE

        # ★ 他像是在约她做点什么 → 这一轮额外告诉她「答应了就附一行日程」。
        #   只在他在约的时候加，平时一个字都不多花。
        if _propose_plan(user_text):
            note = ((note + "\n\n") if note else "") + PLAN_NOTE
        # 这一轮要收的「日程改动」标记，从零开始
        self._plan_marks = []

        # ★ 表白 / 被表白 —— 这一轮是不是那件事。
        #   判定、掷骰子、改档位都在这儿落地；结果作为「这一轮的情境」塞给她，
        #   只进这一轮、不进历史（跟 NIGHT_NOTE 一个路子）。
        try:
            cn = self._confess_intake(user_text)
        except Exception as e:
            cn = ""
            print(f"  （表白判定没跑成：{e}）")
        if cn:
            note = ((note + "\n\n") if note else "") + cn

        # ★ 开口之前先自检一遍（2026-09-23）：她的账本 / 今天的表 /
        #   她自己说过的话，三样得对得上。上一轮留下的旧账、被顶掉的计划，
        #   在拼提示词之前就清掉 —— 晚了就来不及，她读到的还是那份错的。
        self._audit_state()

        messages = [{"role": "system", "content": self._system(note, user_text=user_text)}]
        # ★ 拼进去之前再过一遍矫正 —— 这一轮的过程中要是有旧的错话残留在内存里，
        #   也不能让她看见（见 _fix_history）。
        messages += _fix_history(self.history)[-self.turns:]
        messages.append({"role": "user", "content": user_text})

        # 生成时就掐住上限 —— 关系浅的时候她一次总共只说二十来个字
        budget = self._len_budget()
        answer = self._chat(messages, max_tokens=budget.get("max_tokens"))

        # ★ 他问的是话、她答的是告别（用户实测：「你不想和我聊天？」→
        #   「嗯 知道了 那我就不说了」）—— 让她带着「他在等你一句准话」重说一遍。
        #   补丁只挂在**这一轮的临时消息**上，一个字都不进历史。
        #   ★ 这里必须**八道闸**，缺一道就白写：
        #     ① 他这句没得到正经回答（敷衍 / 认输 / **含糊糊弄**）；
        #     ② 他连着推、她连着躲「困 / 明天再说」，躲满第三次 ——
        #        哪怕这一条单看不算敷衍，也要重说一遍；
        #     ③ 她「明着岔开」的配额用完了（或者他正在点她）—— 也不许再岔；
        #     ④ 同一个理由（困 / 累 / 明天）这一场已经使过一遍了；
        #     ⑤ 她单方面把话关了（「那我就不说了」）—— 哪一轮都算；
        #     ⑥ 她把自己那个世界说成编的（「你当我在说故事」）—— 哪一轮都算；
        #     ⑦ 她把「两个人见面」当成真事说了（「我出来买瓶水就碰见你了」）—— 哪一轮都算；
        #     ⑧ 她说把他刚告诉过她的事「忘了」（他刚报 173，她两句话后说「你多高来着 忘了」）。
        #   踩过的坑：第一版只把「不许再提困」写进补丁，却没让计数**自己**能触发重说 ——
        #   补丁只在重说分支里才顶得到她眼前，于是那句提醒一次都没送出去，
        #   实测 9 次里她 9 次还在搬「困」。（判定能不能开火，和它开火时说什么，是两件事。）
        self._round += 1
        shift_over = _shift_verdict(user_text, answer, self._shift_at, self._round)
        weak = _need_answer_back(user_text, answer)
        persist = _is_push(user_text) and self._push_streak >= 2 and not night
        # ④ 借口配额：persona 写着「一个理由，一晚上只能用一遍」，可模型不总听 ——
        #    实测她能在三连轮里说「状态不好 / 没劲想睡 / 真扛不住」，像被一直往外推。
        #    ★ 深夜例外：他半夜把她叫醒，她答「刚睡着」是**陈述事实**，
        #      按借口拦下来只会逼她把真话说成别的，越修越假。
        excuse_repeat = (not night) and _excuse_verdict(user_text, answer, self._excuse_used)
        # ⑤ 单方面关门：哪一轮都算（「那我就不说了」放什么语境里都是赶人）。
        closed = _closes_door(answer)
        # ⑥ 把自己的世界说成假的（「你当我在说故事就行」）—— 哪一轮都算。
        #    这不是躲、也不是敷衍，是她在否定自己是谁。用户点名的「活人感太弱」
        #    有很大一块就出在这句上：被他追问「你到底在哪儿」，她缩回去认了假。
        deny = _is_self_denial(answer)
        # ⑦ 把「两个人见面」当成真事说了（「我出来买瓶水就碰见你了」）——
        #    哪一轮都算：他手里只有那部手机，两个人从来没见过面。
        #    这不是躲、不是敷衍，是一句话把「隔着手机」写成「站在一块儿」。
        meet = _claims_meet(answer)
        # ⑧ 说他刚告诉过她的事「我忘了」（实测：他刚报 173，她两句话之后
        #    说「你多高来着 忘了」）—— 哪一轮都算，而且是这几种里最伤人的
        #    一种：不是躲、不是敷衍，是**在说「你讲的话我没往心里去」**。
        #    要能翻出他确实说过的那句才拦（见 _recent_fact）——
        #    不然一句「我忘带伞了」也得重说。
        forget = self._recent_fact() if _claims_forget(answer) else ""
        # ⑨ 两个世界不是同一个钟（实测 2026-09-27）：他问「现在几点啦」，
        #    她答「我这边快中午了 太阳老高」——本地凌晨 03:31。
        #    她的历史里攒了四条同类原话，「人设里立规矩 + 每轮注入指标」
        #    都拉不回来（她在照自己的历史演），所以这里**哪一轮都拦**。
        time_split = _claims_time_split(answer, dt.datetime.now().hour)
        if weak or persist or shift_over or excuse_repeat or closed or deny or meet or forget \
                or time_split:
            why = ("，且她连着躲了 %d 次" % self._push_streak
                   if persist and not weak else "")
            if shift_over:
                why += "，且她想岔开"
            if excuse_repeat:
                why += "，且又在搬同一个理由"
            if closed:
                why += "，且她单方面关了话"
            if deny:
                why += "，且她把自个儿的世界说成编的"
            if meet:
                why += "，且她把两个人写成见面了"
            if forget:
                why += "，且她说把他刚说的话忘了"
            if time_split:
                why += "，且她把两边的钟说成不一样"
            print("  （她这句不过关，让她重说一遍%s）" % why)
            again = self._chat(
                messages[:-1] + [{"role": "user",
                                  "content": user_text + "\n\n"
                                             + self._answer_nudge(user_text, shift_over,
                                                                  closed, excuse_repeat,
                                                                  night, deny, meet,
                                                                  forget, time_split)}],
                max_tokens=budget.get("max_tokens"))
            # 重说之后要是还躲，就留原话 —— 别为了「修」把她的话弄得更差
            if again and not _need_answer_back(user_text, again) \
                    and not (persist and _is_excuse(again)) \
                    and not _claims_forget(again) \
                    and not _claims_time_split(again, dt.datetime.now().hour):
                answer = again

        if not answer:
            # 整条都是动作描写、剥完什么都不剩 —— 给个最短的回应，
            # 不能让这条消息变成空白，更不能把这条回复丢掉了
            answer = "嗯"
        else:
            # ★ 说超了就让她**自己重说一遍**（换个短的说法），
            #   而不是事后把话砍半截 —— 砍出来的是「我不爱跟脾气好…」这种断句。
            answer = self._retry_short(messages, answer, budget)

        # ★ 记「明着岔开」的账：这一轮她岔了、而且放行了 —— 记下第几轮，
        #   供上面那个滑窗用（SHIFT_WINDOW 轮之内最多岔一次）。
        if _is_push(user_text) and _is_blatant_shift(answer):
            self._shift_at = self._round
        # ★ 记「借口」的账：这一场她拿困 / 累 / 明天当理由的次数
        #   （第二次再搬就归零重说，见上面 excuse_repeat）。
        #   ★ 深夜不记账：这个点「睡」字本来就是实话，记进去只会污染白天的判定。
        if _is_push(user_text) and _is_excuse(answer) and not night:
            self._excuse_used += 1

        # ★ 数「他推 / 她躲」的次数。躲满三次，下一轮的补丁就直接点她的名。
        #   只在他真要态度的轮次上计数（问句 / 点她）—— 闲聊时她喊困不算躲。
        #   ★ 深夜整段跳过：他半夜连着问几句，她一句句「刚睡着」，那不是躲，
        #     要是照数，她会因为「诚实地说自己在睡」被反复要求重说。
        if _is_push(user_text) and not night:
            if _is_brushoff(answer) or _is_surrender(answer) or _is_excuse(answer):
                self._push_streak += 1
                print("  （她这轮又在躲，连着第 %d 次）" % self._push_streak)
            else:
                self._push_streak = 0

        # ★ 她这一轮真把话说出口了没有 —— 说了就记一笔，下一轮他就是在回答她。
        self._note_her_confess(answer)

        # ★ 记进历史的是「真正发出去的样子」，不是模型的原稿。
        #   超预算的部分主循环会切掉，她压根没发出去 ——
        #   要是让她以为自己说了，下一轮她会照着那么长的接着说，
        #   还会以为对方早就收到过（其实一个字都没到）。
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": self._sent_form(answer)})
        self.history = self.history[-self.turns:]
        self._save_history()

        # ── 她答应了他的提议 → 她的日程真的改掉（控制台会打出改动）──
        self._apply_plan_marks(user_text)

        # ── 这一轮聊出来的「以后」落成待办（见 约定.py）──
        #    他说「明天叫我起床」是她欠他的事，她说「我先去洗澡 待会再聊」
        #    是她欠他的一句话 —— 两边都收，到点都由她先开口。
        self._note_promises(user_text, answer)

        self._mark_turn(user_text=user_text, my_text=answer)
        return answer

    def _apply_plan_marks(self, user_text: str = ""):
        """把她回复里附的「日程改动」标记落到日程上，并打到控制台。

        ★ 标记是 `_chat()` 在**清洗之前**摘走的（见那边的注释）——
          所以这里拿到的永远是完整的，不会被 strip_acts / wechatify 弄碎。
        ★ 重说过的话以**最后一次**带标记的为准：她第一次答「行啊 陪你打」，
          被判不过关重说成「…」，那也应该算数 —— 所以取最后一个非空的。
        """
        marks = []
        for batch in (self._plan_marks or []):
            if batch:
                marks = batch
        self._plan_marks = []
        if not marks or self.life is None:
            return
        for a, b, text in marks:
            try:
                rec = self.life.divert(a, b, text, why=user_text[:40])
            except Exception as e:
                print("  （日程没改成：%s）" % e)
                continue
            if not rec:
                continue
            print("  ★ 她的日程改了：%s-%s  「%s」 → 「%s」"
                  % ("%02d:%02d" % (rec["a"] // 60 % 24, rec["a"] % 60),
                     "%02d:%02d" % (rec["b"] // 60 % 24, rec["b"] % 60),
                     rec.get("was") or "没别的安排", rec["text"]))
            if rec.get("why"):
                print("      起因：他说「%s」" % rec["why"])

    def _sent_form(self, text: str) -> str:
        """她真正发出去的样子（多条用换行连起来）。

        主循环发消息时走的是同一套参数，所以两边算出来是同一个结果。
        参数一改这里也跟着改，不会跑偏。
        """
        behavior = self.cfg.get("behavior", {}) or {}
        if not behavior.get("split_replies", True):
            return text
        mc, mp, tc = _length_limits(self, behavior)
        pieces = split_for_chat(text, mc, mp, tc)
        return "\n".join(pieces) if pieces else text

    # ── 主动：她想跟他说件事，自己先开口 ─────────────────────────────
    def proactive(self, seed: dict, dry: bool = False) -> str:
        """
        她主动发一条。内容围绕今天真实发生在她身上的一件事，
        而不是干巴巴的「在吗」—— 这是「像真人」和「有平行生活」的合流处。
        seed 由 daily_life.nudge() 给。

        dry=True 只在控制台「试生成一条」时用：照样真跑一次模型（不然看不出
        效果），但**一个字都不记** —— 不动历史、不消耗素材、不推进关系。
        看完就扔，跟没发生过一样。
        """
        seed = seed or {}
        text = seed.get("text", "")
        when = seed.get("time", "") or "今天"
        kind = seed.get("kind", "event")
        ago = seed.get("ago")

        # ══ ★ 主动消息最容易跟她的日子打架的地方（2026-09-22）══════════
        #
        #   实测那次：10:28 自检说「她现在在上课」，她同一分钟发来
        #   「电梯里碰到一群新生」，而那条素材的时间戳是 10:26。
        #   她本来就知道自己在哪一段（context 里有「你现在正在…」），
        #   可主动消息的引子写的是「你今天做了这件事」——**她读成了「正在做」**。
        #
        #   所以这里补两样东西：
        #     ① 「你现在正在：X（a-b）」再说一遍，就顶在最后那几行；
        #     ② 「这件事是多久以前的」翻成人话（刚刚 / N 分钟前 / 那会儿），
        #        并要求她**在句子里带出来** —— 这样「在上课」和「刚在电梯里
        #        碰到新生」两句话能同时成立。
        nb = {}
        try:
            if self.life is not None:
                nb = self.life.now_brief() or {}
        except Exception:
            nb = {}
        now_txt = nb.get("text") or ""
        now_span = ("（%s-%s）" % (nb.get("start"), nb.get("end"))
                    if nb.get("start") else "")

        # ★ 素材上的钟点先翻成人话（「00:39」→「凌晨」）—— 见 _human_when。
        #   原样写进引子，她就照念一遍（2026-09-28 实录：「00 39那会儿睡前…」）。
        when_txt = _human_when(when)

        if kind in ("promise",):
            anchor = ""
        elif isinstance(ago, int) and ago >= 0:
            if ago <= 20:
                anchor = "「刚刚」"
            elif ago <= 90:
                anchor = "「%d 分钟前」" % ago
            elif when_txt:
                anchor = "「%s那会儿」" % when_txt
            else:
                anchor = "「今天早些时候」"
        elif when_txt:
            anchor = "「%s那会儿」" % when_txt
        else:
            anchor = "「今天早些时候」"

        if kind == "promise":
            # ★ 这不是「她忽然想起来一件事」，是**她自己答应过的**：
            #   「明天叫你起床」是他求她的，「待会再聊」是她自己说的。
            #   所以措辞和普通主动消息完全不同 —— 不提今天干了什么，
            #   就是把这件事本身说出来。
            raw = seed.get("raw") or ""
            if seed.get("urgent"):
                hint = ("你答应过他一件事：%s。现在到点了，是你自己先开口的时候。\n"
                        "（起因是他求你：「%s」）\n"
                        "这一轮就说这件事本身 —— 别另找话题，也别解释你为什么记得。"
                        % (text, raw))
            else:
                # ★ 「她自己说去干嘛」这条，2026-09-22 补上过程。
                #   原来只说「现在回来了」，她就干巴巴报一句「吃完了」——
                #   听的人不知道她什么时候去的、在哪儿吃的，中间是空的。
                #   现在她去做的那一段**真的压进了今天的时间轴**
                #   （见 _place_promise），这里就把出发时间告诉她，
                #   让她把刚才那一段带出来。
                doing = seed.get("doing") or ""
                since = seed.get("since") or ""
                went = ""
                if doing:
                    if since:
                        went = ("你 %s 左右出门去%s，这会儿刚回来"
                                "（路上、地方、吃的什么，你自己心里有数）。\n"
                                % (since, doing))
                    else:
                        went = ("你刚才去%s，这会儿刚回来。\n" % doing)
                hint = ("%s你之前跟他说过要 %s，现在是回来跟他打声招呼的时候。\n"
                        "顺手带一句刚才那一段 —— 别只报一句「吃完了」，"
                        "也别现编没去过的细节。\n"
                        "先打招呼，再顺着刚才的话题往下聊；"
                        "别提「我回来了」这种报备口气，随口一句就行。"
                        % (went, text))
        elif kind == "quirk":
            hint = ("你今天遇到过一件小事：%s。你忽然想跟他说这件事。\n"
                    "（这是**已经发生过**的了 —— 说的时候带上时间%s，"
                    "别让它读起来像你此刻还在那儿。）" % (text, anchor))
        else:
            hint = ("你今天做过这件事（时间是「%s」）：%s。你忽然想跟他说这件事。\n"
                    "（这是**已经发生过**的了 —— 说的时候带上时间%s，"
                    "别让它读起来像你此刻还在那儿。）"
                    % (when_txt or "今天早些时候", text, anchor))

        behavior = self.cfg.get("behavior", {}) or {}
        budget = self._len_budget()
        _mc, _mp, _tc = _length_limits(self, behavior)
        if _tc > 0:
            len_rule = ("- 你一次最多发 %d 条，加起来不超过 %d 个字（硬上限）。"
                        "说不完就换个短的说法，别把话砍一半。\n" % (_mp, _tc))
        else:
            len_rule = "- 只发一条，很短，8~25 个字最好。\n"

        # ★ 你现在在干嘛 —— 顶在「规矩」最前面（2026-09-22）。
        #   上面 context() 里其实有「你现在正在…」，但那一段太长，
        #   主动消息这一路很容易被引子盖过去。这里再钉一遍：
        #   「此刻」和「这件事是什么时候的」摆在一起，她就不会说拧。
        now_rule = ""
        if now_txt and anchor:
            now_rule = ("- ★ 你现在正在：%s%s —— 你说的这件事是**之前**发生的"
                        "（%s）。两句话得能同时成立：写成「刚下课」「刚刚在电梯里」"
                        "都可以，但**绝不能**写成你此刻正在那儿。\n"
                        % (now_txt, now_span, anchor))
        busy_rule = ""
        if now_txt and nb.get("busy"):
            busy_rule = ("- 你现在走不开（上课 / 睡着了）—— 这条是手在桌子底下"
                         "偷偷瞄一眼手机发的：越短越好，别像在跟人聊很久，"
                         "也别写「等我一下」这种要接着聊的话。\n")

        extra = (
            "# 现在轮到你主动找他\n\n"
            "不是他先发消息的 —— 是你自己拿起手机，先开了口。\n\n"
            + hint + "\n\n"
            "规矩：\n"
            + len_rule + now_rule + busy_rule +
            "- 不许用「在吗」「你好」「在忙吗」这种开场。直接说那件事，"
            "或者随口带一句，不用交代来龙去脉。\n"
            "- 不要解释你为什么发消息。不要问他方不方便。\n"
            "- 允许说得没头没尾 —— 真人突然找人就是这样。\n"
            "- 别排着队报备（「我今天上午……后来……然后……」），就一件事。"
            "但**时间要说出来**（「刚刚」「早上」）—— 不然他以为你还在那儿。\n"
            "- ★ 别在消息里报钟点 —— 不写「00:39」「19:20」这种数字时间。"
            "真人开口不念表：要说时间就用「那会儿」「早上」「刚刚」这种人话。\n"
        )

        messages = [{"role": "system", "content": self._system(extra, user_text=text)}]
        # 只带最近几轮，免得把整段历史又喂一遍（同样先过一遍矫正）
        messages += _fix_history(self.history)[-6:]
        # ★ 引子要放在【最后一条】里再说一遍。
        #   只写在提示词里的时候，模型常常被上面几轮对话带跑 ——
        #   给它「走廊看天幕」，它回的是「食堂腌甜咸菜配鱼丸」，
        #   顺着刚才的话题说了句完全无关的。顶在最后它才认。
        if seed.get("kind") == "promise" and seed.get("urgent"):
            tail = ("（这一条是你自己先发的 —— 不是忽然想起来，是你**答应过**的。"
                    "现在到点了，就说这件事本身：%s。）" % (text or "答应他的事"))
        elif seed.get("kind") == "promise":
            tail = ("（这一条是你自己先发的 —— 你刚才说了要「%s」，现在回来了。"
                    "随口跟他说一声，然后接着刚才的话聊。）" % (text or "待会再聊"))
        else:
            # ★ 引子最后再说一遍，并把「此刻」和「这件事是过去的」一起钉住 ——
            #   只写在提示词里，模型常被上面几轮对话带跑（这儿是它最后读到的一行）。
            tail = ("（这一条是你自己先发的，没有人问你任何事。"
                    "你现在%s%s，忽然想起的是**之前**的一件事：%s。"
                    "就说这件，时间词要带上（%s），别接着刚才的话题往下聊。）"
                    % (now_txt or "在忙自己的事", now_span,
                       text or "今天发生的某件小事", anchor))
        messages.append({"role": "user", "content": tail})

        answer = self._chat(messages, max_tokens=budget.get("max_tokens"))
        if not answer:
            return ""
        # 主动消息同样不许说半句 —— 超了让她自己重说一遍
        answer = self._retry_short(messages, answer, budget)

        if dry:
            return answer          # 控制台试生成：到此为止，什么痕迹都不留

        # ★ 她主动开口这一路，也可能就是那句表白 —— 同一套记账。
        self._note_her_confess(answer)

        # 记进历史，保持 user/assistant 交替，别让接口挑刺
        self.history.append({"role": "user", "content": "（你先开口找他说了句话。）"})
        self.history.append({"role": "assistant", "content": self._sent_form(answer)})
        self.history = self.history[-self.turns:]
        self._save_history()

        # 这件事说过了，今天别再提第二遍
        # （promise 那种不是「今天的一件小事」，别往素材账上记）
        if self.life is not None and text and seed.get("kind") != "promise":
            try:
                self.life.mark_shared(text)
            except Exception:
                pass

        # 她主动说的这句话里要是有「待会再聊」「等我一下」，也得记下来 ——
        # 不然就是「说了待会聊，然后没有然后了」。
        self._note_promises("", answer)

        # 她主动开口这件事本身，就是关系在往前走
        self._mark_turn(was_proactive=True)
        return answer

    def _note_promises(self, user_text: str = "", my_text: str = ""):
        """把这一轮聊出来的「以后」记成待办（见 约定.py）。

        两边都收：
          · **他说的** —— 「明天早上七点半叫我起床」「七点叫我」
            → 到点她主动开口。这是他求的：说好的时间就是闹钟。
          · **她说的** —— 「我先去洗澡 待会再聊」「等我一下」
            → 过一阵她自己回来跟他说一声。

        ★ 解析全是朴素规则（正则 + 时间词表），一个字都不问模型 ——
          这件事的价值就在「一定发生」，不能看她当天心情好不好。

        ★ 收完账**立刻自检一遍**（2026-09-23，见 _audit_state）：
          她这一句可能同时"说完了上一件、开始了下一件"，
          等下一轮再修就已经晚了一轮 —— 而屏幕上那一轮已经打架了。
        """
        if promise is None:
            return
        for text, who in ((user_text, "他"), (my_text, "她")):
            if not text:
                continue
            try:
                recs = promise.feed(text, who) or []
            except Exception as e:
                print("  （约定没记成：%s）" % e)
                continue
            for it in recs:
                print("  ★ 记下一件事：%s  %s（%s说的：「%s」）"
                      % ((it.get("due") or "没定时间").replace("T", " "),
                         it.get("text", ""), who, it.get("raw", "")))
                self._place_promise(it, who)
        self._audit_state(her_text=my_text)

    # ══ ★ 一致性自检：她说过的话不许互相打架（2026-09-23）══════════════
    #
    #   用户的现场（2026-09-22 晚，屏幕上挨着的原话）：
    #       19:08  我刚眯了会儿 现在还有点晕     ← 她自己说了，人醒了
    #       19:21  要去上课了                     ← 人醒了、收拾好、要走了
    #       19:22  刚下课 正往宿舍走               ← 提前 50 分钟报了下课
    #       19:30  刚睡醒                        ← 18:21 那条定时器到点开口
    #   一句话：**她的账本、她今天的作息表、她嘴里说的 —— 三样互不通气。**
    #
    #   这里让它们通气，每一轮跑一遍：
    #     ① `约定.settle`     她报了「已经做完了」→ 同一类的账当场兑现勾掉
    #     ② `约定.reconcile`  同一类只留最新，旧的记成「被后来的话顶掉了」
    #     ③ `life.settle_divert`  被勾掉/顶掉的那条，它当初压进她今天时间轴
    #        的那一段也跟着**裁短或抹掉** —— 这就是他说的
    #        「她说等下要上课，就把之后有冲突的计划全抹掉」。
    #
    #   为什么每轮都跑：一轮里她可能"说完了上一件、又开始下一件"，
    #   拖到下一轮，那一轮已经在她眼前打架了。
    _SETTLED_NEXT = {
        # 这一类被兑现之后，她**接下来那一段**该是什么样子 ——
        # 别让裁完之后的空档落回原作息（19:08 人明明醒着，表上却是「在上课」）
        "睡": "醒了，还赖在床上没动",
        "澡": "洗完澡了，头发还湿着",
        "饭": "吃完了，正往回走",
        "课": "课上完了，人已经回来了",
        "作业": "忙完了，人松下来了",
    }

    @staticmethod
    def _hm(m) -> str:
        return "%02d:%02d" % (int(m) // 60 % 24, int(m) % 60)

    def _audit_state(self, her_text: str = "") -> dict:
        """跑一遍一致性自检 —— 外面这一层**只负责兜异常**。

        ★ 自检是**辅助**：账本或她今天那张表哪怕读脏了，也绝不许把她的回复带崩
          （这个函数是在 `reply()` 里、拼提示词之前调的，抛出去就是她整轮不回）。
          真正的逻辑在 `_audit_state_impl`，跟 `_confess_intake` 一个路数。
        """
        try:
            return self._audit_state_impl(her_text=her_text)
        except Exception as e:
            print("  （一致性自检没跑成：%s）" % e)
            return {}

    def _audit_state_impl(self, her_text: str = "") -> dict:
        """跑一遍一致性自检，把作废的账和作废的计划一起抹掉。

        返回 {"touched": [...]}；什么都没改就返回空 dict。
        她这一轮说的原话由 `her_text` 传进来（他说的不参与 —— 他说的
        是「他求她的事」，那是他自己定下的，不该被她的状态顺手改掉）。
        """
        if promise is None:
            return {}
        now = dt.datetime.now()
        touched = []
        # ① 她报了「已经做完了」→ 当场兑现
        if her_text:
            try:
                touched += promise.settle(her_text, now)
            except Exception as e:
                print("  （一致性自检没跑成：%s）" % e)
        # ② 同类只留最新 + 过期作废
        try:
            touched += promise.reconcile(now)
        except Exception as e:
            print("  （一致性自检没跑成：%s）" % e)
        if not touched:
            return {}
        # ③ 被勾掉/顶掉的那条，它压进她今天表上的那一段也跟着改
        for it in touched:
            dom = promise._domain_of(it)
            doing = it.get("doing") or ""
            why = it.get("fired") or ""
            print("  ★ 自检：「%s」%s"
                  % (it.get("text") or "",
                     promise._FIRED_CN.get(why) or why))
            if self.life is None or not doing:
                continue
            _nmin = now.hour * 60 + now.minute
            # 替换段要一直盖到「作息表上这一段结束」，或者至少 30 分钟 ——
            # 不然裁完之后那段又落回原作息（19:08 人醒着，表上却写着「在上课」）。
            try:
                _until = max(int(self.life.block_end_at(_nmin)), _nmin + 30)
            except Exception:
                _until = _nmin + 30
            try:
                got = self.life.settle_divert(
                    doing, _nmin,
                    # 兑现了才补「她现在在干嘛」；被顶掉的不补
                    next_text=(self._SETTLED_NEXT.get(dom, "")
                               if why == "她自己先说了" else ""),
                    why="她自己说的", next_until=_until)
            except Exception as e:
                print("      她今天那段计划没改成：%s" % e)
                continue
            for d in (got.get("trimmed") or []):
                print("      裁到此刻：%s-%s「%s」"
                      % (self._hm(d.get("a")), self._hm(now.hour * 60
                                                        + now.minute),
                         d.get("text")))
            for d in (got.get("dropped") or []):
                print("      抹掉一段还没开始的计划：%s-%s「%s」"
                      % (self._hm(d.get("a")), self._hm(d.get("b")),
                         d.get("text")))
            if got.get("added"):
                print("      改成：%s-%s「%s」"
                      % (self._hm(got["added"]["a"]), self._hm(got["added"]["b"]),
                         got["added"]["text"]))
        return {"touched": touched}

    def _place_promise(self, it: dict, who: str):
        """把「她说要去做的事」压进她今天的时间轴（见 daily_life.divert）。

        ★ 2026-09-22 实测那次：14:23 她说了句「你上班辛苦 有空吃点东西」，
          被词表记成「她自己答应吃完回来说一声」，40 分钟后（15:03）准点开口
          「吃完了 报告单也拿了」—— 可那 40 分钟里她**什么都没发生**，
          一个「去食堂 → 拿面包 → 吃」的过程都没有，听的人当然觉得假。
          病根是：`comeback` 原来只是个定时器（made + 40 分钟），
          **不进她的日子**。所以这里顺手把她去做的那一段压到时间轴上
          （「去吃点东西」），到点再开口时，「她刚才在食堂」就是有据可依的，
          `life.context()` 里也真的多出「已经过完了什么」那一条。
        """
        if who != "她" or self.life is None:
            return
        if (it or {}).get("kind") != "comeback":
            return
        doing = (it or {}).get("doing") or ""
        if not doing:
            return
        try:
            due = dt.datetime.strptime(it.get("due") or "", "%Y-%m-%dT%H:%M")
        except Exception:
            return
        now = dt.datetime.now()
        # 跨天的（「明天再说」那种）先不占她的表；已经到点的也不用补。
        if due <= now or due.date() != now.date():
            return
        start = now.hour * 60 + now.minute
        end = due.hour * 60 + due.minute
        if it.get("soon"):
            # ★ 「我**待会儿**也去吃饭了」= 说这话时还没动身。
            #   段得从「快到点」才开始 —— 不然他 14:26 问「你还在上课吗」，
            #   她的表上已经写着「在食堂」，又是一处对不上的。
            #   （「我先去洗澡了」没有将来词，就是从这一刻算。）
            start += int((end - start) * _SOON_LEAD)
        if end - start < 5:
            start = max(0, end - 5)
        try:
            rec = self.life.divert(start, end, doing, why="她答应的")
        except Exception as e:
            print("  （没压进她的日程：%s）" % e)
            return
        if rec:
            print("  ★ 压进她的今天：%s - %s %s（原来：%s）"
                  % (rec.get("a"), rec.get("b"), doing, rec.get("was") or "空着"))


# ══════════════════════════════════════════════════════════════
# 四、发声：交给 WeeMessenger
# ══════════════════════════════════════════════════════════════

class Sender:
    def __init__(self, cfg: dict):
        s = cfg["sender"]
        self.cfg = cfg
        self.url = f"http://{s['host']}:{s['port']}/"
        self.voice_cfg = cfg.get("voice", {})
        self.audio_dir = ROOT / "audio"
        self.audio_dir.mkdir(exist_ok=True)

        # 「音色」文件夹 —— 达妮娅本人的声音素材都放这儿：
        #   参考音频（她说话的 3~10 秒切片）、从网上下的角色模型包。
        # 目录里有一份 说明.txt 讲怎么放。
        self.voice_dir = ROOT / "音色"
        self.voice_dir.mkdir(exist_ok=True)
        # 本地引擎的模型权重只需要切一次
        self._local_weights_done = False

        # 后台发送开关：见 wx_input.py。关掉就退回原来的前台流程。
        self.background = bool(s.get("background", True))
        self._warned_chat = False
        self._warned_focus = False
        # ★ 2026-10-12：发送熔断用的两格状态（见 send_text / _count_send_failure）
        self._send_fail_streak = 0
        self._send_freeze_until = 0.0

    def health(self) -> bool:
        try:
            r = requests.get(self.url.rstrip("/") + "/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def _post(self, payload: dict, timeout: int = 60):
        r = requests.post(self.url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r

    def _try_background(self, target: str, text: str) -> bool:
        """
        尝试「不抢焦点、不动鼠标」地把文字发出去。

        前提是微信当前打开的就是目标会话 —— 微信在画布模式下没有暴露控件树，
        脚本没法自己切换会话，所以「切换会话」这件事只能交回前台流程。
        这里先 OCR 认一下当前会话名，对得上才敢用后台通道，绝不会发错人。
        """
        if not self.background:
            return False
        try:
            win = wechat_window.find()
            if not win:
                return False
            current = wx_input.current_chat(win)
            if not wx_input.chat_matches(current, target):
                if not self._warned_chat:
                    print(
                        f"  （当前会话是「{current or '认不出'}」，不是「{target}」——"
                        f"这次走前台发送。"
                    )
                    print(f"    想让文字也完全后台：把和「{target}」的聊天窗口保持打开，"
                          f"别最小化到托盘。）")
                    self._warned_chat = True
                return False
            if not wx_input.send(win, text, target=target):
                # 字投进去却回读不到 → 微信这会儿要么刚被从托盘恢复、
                # 要么【输入框丢了键盘焦点】（2026-10-04 实测的主因：
                # 他在聊天区点过消息，Qt 把焦点移出了输入框）。
                # 两种情况前台都能救；前台还会强制重搜一次会话，
                # 那一下会把焦点送回输入框，之后的消息就又是后台的了（会自愈）。
                if not self._warned_focus:
                    print("  （微信这会儿收不到后台投递的按键 —— 常见两个原因：")
                    print("    ① 刚从托盘恢复、还没被点过；")
                    print("    ② 输入框丢了焦点（在聊天区点过消息就会这样）。")
                    print("    这一次会走前台补一遍，之后应该就都是后台了。）")
                    self._warned_focus = True
                return False
            return True
        except Exception as e:
            print(f"  （后台发送没成功，改用前台：{e}）")
            return False


    # ── 语音条：发送 + 回读校验 ──────────────────────────────────────
    #
    # 为什么发个语音还要「回读」：
    #   微信 4.1.9 起才支持电脑端发语音，做法是「按住『发送语音消息』快捷键
    #   （默认 Alt）+ 把 TTS 音频送进虚拟声卡 CABLE Input，微信从 CABLE Output
    #   录进去」。只要那个快捷键跟 Alt 对不上（被改过 / 版本没这功能），
    #   按下去微信【一点反应都没有】，而小信使那边照样打印"流程完成"。
    #   结果就是：日志说发了、聊天记录里干干净净 —— 达妮娅的话凭空消失。
    #   所以这里发送前后各读一次聊天区，只有真的多出一条语音气泡才算成功。

    def _own_bubbles(self, win):
        """读当前会话里「自己发的」气泡，返回列表；读不到返回 None。"""
        if win is None:
            return None
        img = ocr_reader.capture_window(win)
        if img is None:
            return None
        try:
            geo = ocr_reader.detect_geometry(img)
            bubbles = ocr_reader.read_bubbles(img, geo)
        except Exception:
            return None
        return [b for b in bubbles if b.mine]

    def send_voice(self, target: str, audio: Path) -> bool:
        """
        发一条语音条，并确认它真的发出去了。
        返回 True = 确认发出；False = 没发出去（调用方必须改用文字补上内容）。

        audio 可以是 mp3（edge 引擎）或 wav（本地模型），格式按扩展名走。
        """
        win = wechat_window.find(timeout=2.0)
        if win is None:
            print("  （找不到微信窗口，语音发不出去）")
            return False

        before = self._own_bubbles(win)
        n_before = None if before is None else len(before)
        last_before = before[-1].text if before else ""

        # 小信使是按扩展名写临时文件再播的，所以格式跟着实际文件走
        fmt = (audio.suffix.lstrip(".") or "mp3").lower()
        b64 = base64.b64encode(audio.read_bytes()).decode()
        # 注意：README 里字段叫 format，源码里读的是 voice_format，两个都发比较稳
        self._post(
            {
                "type": "send_voice",
                "target": target,
                "message": "",
                "voice_data": b64,
                "format": fmt,
                "voice_format": fmt,
            },
            timeout=120,
        )

        if n_before is None:
            print(f"  → 语音条已投递（{audio.name}），但聊天区读不到，无法确认是否真的发出")
            return True

        # 小信使是「投进队列就返回」，真正的按键+放音还在后面排队，
        # 所以这里必须耐心轮询，等微信把语音条渲染出来。
        print(f"  · 等微信出语音条…（{audio.name}）", end="", flush=True)
        deadline = time.time() + VOICE_VERIFY_SECONDS
        while time.time() < deadline:
            time.sleep(1.2)
            print(".", end="", flush=True)
            now = self._own_bubbles(win)
            if not now:
                continue
            tail = now[-1]
            grew = len(now) > n_before
            changed = tail.text != last_before
            if ocr_reader.looks_like_voice(tail.text) and (grew or changed):
                print(f" 确认已发出（气泡『{tail.text[:20]}』）")
                return True

        print(" 没等到")
        print(f"  [X] 语音条没发出去：微信界面里始终没出现新的语音气泡（{audio.name}）")
        return False

    def _confirm_sent(self, target: str, text: str, wait: float = 9.0) -> bool:
        """
        发完回读聊天区，确认这条真的躺在微信里了。

        ★ 2026-10-04 补：前台那条通道（小信使：剪贴板 → Ctrl+V → 回车）原来
          【完全没有确认】—— post 完就印「文字已发出」。于是它失败的时候，
          两头都看不出来：控制台说发了、日志里一个字都没有。
          实测那天 13:19~13:23 连吞 4 条回复，用户是靠自己盯微信才发现的。

          前台是异步的（指令先进小信使的队列，它还会随机等 0.5~2 秒），
          所以这里得等一会儿。最多等 wait 秒，期间每 0.8 秒瞄一眼聊天区。
        """
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(0.8)
            try:
                win = wechat_window.find()
                if win and wx_input.last_own_message(win, text):
                    return True
            except Exception:
                pass
        return False

    def send_text(self, target: str, text: str):
        # ★ 2026-10-12：先看闸。连续几次「按了回车却回读不到气泡」就停发一会儿。
        #   为什么非要有刹车：那天微信右侧挂了个搜一搜面板，机器人从此看不到
        #   自己的气泡，于是它一条条地重发、还把面板里的字当成对方说的话接着回，
        #   从 12:41 一直刷到 13:55（两百多条）。光修好视野不够，得先能停下来。
        if time.time() < self._send_freeze_until:
            print(f"  ⛔ 发送已暂停，还有 {int(self._send_freeze_until - time.time())} 秒。"
                  f"（看一眼微信：右侧是不是挂着「搜索 / 搜一搜」面板？"
                  f"按一下 Esc 关掉它，机器人就能重新看见聊天区了）")
            return
        if self._try_background(target, text):
            print(f"  → 文字已发出（后台，微信没跳前台）：{text[:40]}")
            self._send_fail_streak = 0
            return
        # ★ 后台没成 —— 十有八九是微信输入框丢了键盘焦点
        #   （他在聊天区点过消息，Qt 把焦点移出了输入框；实测 13:23:01 那张截图里
        #     输入框的光标不见了，而正常时刻的截图都有）。
        #   这时候 WM_CHAR 落空、前台 Ctrl+V 也落空 —— 光把窗口激活是不够的。
        #   能救回来的动作是让它重新走一遍「Ctrl+F 搜索 → 进会话」，
        #   那一下微信才会把焦点送回输入框。所以这里带 refocus=True。
        #
        #   而且前台这条【必须回读确认】再报成功：它原来是哑的，
        #   失败在日志里完全隐形（见 _confirm_sent 的注释）。
        # ★ 2026-10-12 改：这里原来是 for attempt in (1, 2) —— 复核不到就
        #   【把整条消息重投一遍】。视野一旦出错（右侧挂着搜一搜面板，复核必然失败），
        #   同一条消息就被完整地发了两遍 —— 用户在微信里看到的就是一模一样的重复气泡。
        #   现在：复核不到也不再重投整条，只补一次回车。
        #   框里还留着那条字，回车就把它送出去；框里是空的，回车什么也不做（无害）。
        #   宁可偶尔少一条，也不要成串地重复。
        try:
            self._post({"type": "send_message", "target": target,
                        "message": text, "refocus": True})
        except Exception as e:
            print(f"  （前台通道没接上：{e}）")
        if self._confirm_sent(target, text):
            print(f"  → 文字已发出（前台补发，已回读确认）：{text[:40]}")
            wx_input.log_foreground(target, text, True)
            self._send_fail_streak = 0
            return

        # 只补一下回车（走后台那条通道）。绝不再把整条塞进去 —— 那正是重复气泡的来源。
        try:
            win = wechat_window.find()
            if win:
                wx_input.press(win.NativeWindowHandle, wx_input.VK_RETURN)
        except Exception:
            pass
        if self._confirm_sent(target, text, wait=5.0):
            print(f"  → 文字已发出（补一次回车之后确认到了）：{text[:40]}")
            wx_input.log_foreground(target, text, True)
            self._send_fail_streak = 0
            return

        wx_input.log_foreground(target, text, False)
        print(f"  [X] 这条没能发出去：{text[:40]}")
        self._count_send_failure()

    def _count_send_failure(self):
        """连续复核失败到第 3 次就熔断 5 分钟，并把「怎么救」打在控制台上。

        ★ 2026-10-12 立。为什么要有它：
          微信的界面一旦被面板挤歪（右侧挂着搜一搜结果页就是这样），
          机器人就再也读不到自己的气泡 —— 它会一遍遍地重发，
          还会把面板里的字当成对方说的话接着回，实测刷了一个多小时。
          所以除了「把小信使的搜索那一步弄稳」，还得有一条「一直不对就先停下」的闸。
        """
        self._send_fail_streak += 1
        if self._send_fail_streak < 3:
            return
        self._send_fail_streak = 0
        self._send_freeze_until = time.time() + 300
        print("  ⛔⛔ 连着 3 条都复核不到气泡 —— 先停 5 分钟不发，别越刷越多。")
        print("      最可能的原因：微信当前页面上挂着一个面板（搜索 / 搜一搜 / 公众号），")
        print("      聊天区被挤到一边，机器人读到的根本不是聊天记录。")
        print("      处置：在微信里按一下 Esc（或点面板左上角的返回箭头），")
        print("      让聊天窗口恢复成一整块；它自己会接着发。")

    def send_sticker(self, target: str, path) -> bool:
        """
        发一张表情包。

        走的是小信使的 send_sticker：它把文件塞进剪贴板、在微信里 Ctrl+V 再回车。
        ⚠ 小信使内部固定存成 .gif 临时文件，所以这里默认只发 .gif
          （见 表情包.py 开头那段说明）。
        发没发成没法像语音那样回读确认 —— 表情包气泡在 OCR 里什么都读不到。
        """
        p = Path(path)
        if not p.is_file():
            print(f"  [!] 表情包文件不在了：{p}")
            return False
        try:
            b64 = base64.b64encode(p.read_bytes()).decode()
        except Exception as e:
            print(f"  [!] 读不了表情包 {p.name}：{e}")
            return False
        try:
            self._post({"type": "send_sticker", "target": target,
                        "sticker_data": b64}, timeout=60)
        except Exception as e:
            print(f"  [!] 表情包发失败了：{e}")
            return False
        return True

    # ─────────────────────────────────────────────────────────────
    #  TTS：把文字变成音频文件
    #
    #  voice.engine 决定用哪条路：
    #    edge   微软 Edge-TTS 的内置播音音色（免费在线，但不是达妮娅的声音）
    #    local  你自己准备的达妮娅音色（本地模型，见 voice.local）
    # ─────────────────────────────────────────────────────────────

    def voice_engine(self, cfg: dict = None) -> str:
        c = cfg if cfg is not None else self.voice_cfg
        e = str(c.get("engine") or "").strip().lower()
        if e in ("local", "gptsovits", "gpt-sovits", "gsv", "clone", "本地", "自定义"):
            return "local"
        return "edge"

    def _voice_path(self, p) -> Path:
        """音色相关的路径都相对「达妮娅」目录解析，填 音色/xxx.wav 就行。"""
        q = Path(str(p or "").strip())
        if not q.is_absolute():
            q = ROOT / q
        return q

    def tts(self, text: str, voice: str = None, cfg: dict = None) -> Path:
        """把文字合成成音频文件，返回文件路径。

        voice: 仅 edge 引擎用，临时换一个微软音色
        cfg:   临时覆盖 config.yaml 里 voice 段的值

        用哪个引擎由 voice.engine 决定，见 voice_engine()。
        """
        c = cfg if cfg is not None else self.voice_cfg
        if self.voice_engine(c) == "local":
            return self._tts_local(text, c)
        return self._tts_edge(text, c, voice)

    def _tts_edge(self, text: str, c: dict, voice: str = None) -> Path:
        """微软 Edge-TTS 合成 mp3（免费，不需要 key）。

        注意：这条路用的是微软的播音音色，**不可能是达妮娅本人的声音**。
        想要她的音色，把 voice.engine 改成 local（见 音色\\说明.txt）。
        """
        import asyncio
        import hashlib

        import edge_tts

        v = voice or c.get("edge_voice", "zh-CN-XiaoxiaoNeural")
        rate = str(c.get("rate") or "+0%")
        pitch = str(c.get("pitch") or "+0Hz")
        volume = str(c.get("volume") or "+0%")

        # 音色/语速/音调/音量都进哈希：换音色后不会误用旧缓存文件
        key = f"edge|{v}|{rate}|{pitch}|{volume}|{text}"
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        out = self.audio_dir / f"{digest}.mp3"

        async def _run():
            await edge_tts.Communicate(
                text, v, rate=rate, pitch=pitch, volume=volume
            ).save(str(out))

        asyncio.run(_run())
        return out

    def _tts_local(self, text: str, c: dict) -> Path:
        """用你自己准备的达妮娅音色合成。

        config.yaml 的 voice.local 段：
          api           本地语音引擎地址（GPT-SoVITS 的 api_v2.py）
          ref_audio     参考音频，放 音色\\ 目录里
          ref_text      参考音频里说的那句话（也可写成同名的 .txt 文件）
          gpt_weights   模型权重（.ckpt）—— 填了就自动切过去
          sovits_weights 模型权重（.pth）
          extra_payload 想透传给引擎的额外字段（dict）

        合成失败会抛异常，由主循环接住并改用文字补发，不会让回复消失。
        """
        import hashlib
        import json

        loc = c.get("local") or {}
        api = str(loc.get("api") or "http://127.0.0.1:9880").strip().rstrip("/")

        # ── 参考音频 ──
        ref = self._voice_path(loc.get("ref_audio") or (self.voice_dir / ("%s.wav" % _me())))
        if not ref.exists():
            raise FileNotFoundError(
                f"找不到参考音频：{ref}\n"
                f"       放一段{_me()}说话的声音进去（3~10 秒、干净人声），\n"
                f"       详细要求见 {self.voice_dir}\\说明.txt"
            )

        # ── 参考音频的文字 ──
        ref_text = str(loc.get("ref_text") or loc.get("prompt_text") or "").strip()
        if not ref_text:
            side = ref.with_suffix(".txt")
            if side.exists():
                ref_text = side.read_text(encoding="utf-8", errors="replace").strip()

        # ── 模型权重：切换一次就够 ──
        self._local_set_weights(api, loc)

        payload = {
            "text": text,
            "text_lang": str(loc.get("text_lang") or "zh"),
            "ref_audio_path": str(ref),
            "prompt_text": ref_text,
            "prompt_lang": str(loc.get("prompt_lang") or "zh"),
            "text_split_method": str(loc.get("text_split_method") or "cut5"),
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": float(loc.get("speed_factor") or 1.0),
        }
        # 引擎版本不同、字段名不一样时，用 extra_payload 补/改
        extra = loc.get("extra_payload")
        if isinstance(extra, dict):
            payload.update(extra)

        digest = hashlib.md5(
            ("local|" + json.dumps(payload, ensure_ascii=False, sort_keys=True)).encode("utf-8")
        ).hexdigest()[:16]
        out = self.audio_dir / f"{digest}.wav"
        if out.exists() and out.stat().st_size > 1000:
            return out  # 同一句话不重复合成

        try:
            r = requests.post(api + "/tts", json=payload, timeout=float(loc.get("timeout") or 180))
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"连不上本地语音引擎 {api}\n"
                f"       语音引擎还没启动。要先把它跑起来（见 音色\\说明.txt「第三步」）。\n"
                f"       原始错误：{e}"
            ) from e

        if r.status_code != 200:
            body = r.text[:300]
            raise RuntimeError(f"本地语音引擎返回 HTTP {r.status_code}：{body}")

        ctype = (r.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            # 引擎用 200 + JSON 报错的情况也不算少见
            raise RuntimeError(f"本地语音引擎没返回音频，而是：{r.text[:300]}")

        out.write_bytes(r.content)
        return out

    def _local_set_weights(self, api: str, loc: dict):
        """把本地引擎的模型切成你指定的那套（GPT-SoVITS 的 set_*_weights）。

        没填权重路径就什么都不做 —— 引擎会用它自己配置里的默认模型。
        """
        if self._local_weights_done:
            return

        for key, endpoint, label in (
            ("gpt_weights", "set_gpt_weights", "GPT(.ckpt)"),
            ("sovits_weights", "set_sovits_weights", "SoVITS(.pth)"),
        ):
            raw = str(loc.get(key) or "").strip()
            if not raw:
                continue
            p = self._voice_path(raw)
            if not p.exists():
                raise FileNotFoundError(
                    f"找不到模型权重（{label}）：{p}\n"
                    f"       把从网上下的模型包解压到 {self.voice_dir} 里，\n"
                    f"       再按 音色\\说明.txt 把路径填进 config.yaml 的 voice.local。"
                )
            r = requests.get(f"{api}/{endpoint}", params={"weights_path": str(p)}, timeout=120)
            if r.status_code != 200:
                raise RuntimeError(
                    f"切换模型权重失败（{label}）：HTTP {r.status_code} {r.text[:200]}"
                )
            print(f"  · 已切换到语音模型 {p.name}")

        self._local_weights_done = True


# ─────────────────────────────────────────────────────────────────────────────
# 什么时候发语音？由 config.yaml 的 voice.mode 决定：
#
#   trigger  只有对方话里出现触发词（或对方自己发的就是语音）才发语音。
#            这是默认值 —— 平时安安静静发文字，你想听她说话时喊一声就行。
#   ratio    按 voice.ratio 的概率随机混着发（老行为）。
#   always   每轮都发语音（会被 split_for_voice 切成好几条，最吵）。
#   never    只发文字，纯后台、不抢前台、不动鼠标。
#
#   voice.enabled=false 是总开关，优先级最高，关了就一律文字。
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_TRIGGER_WORDS = [
    # 直接说要语音
    "发语音", "发条语音", "发个语音", "来段语音", "来条语音", "语音回",
    "用语音", "语音说", "语音发", "发个声", "发条声", "出个声",
    # 想听 / 想说
    "说给我听", "讲给我听", "念给我听", "读给我听", "说句话",
    "听听你声音", "听听你的声音", "听听声音", "用嘴说",
]

# 明确说「不要语音」的说法，命中就一律发文字（比 trigger_words 优先）
DEFAULT_NO_VOICE_WORDS = [
    "别发语音", "不要语音", "不用语音", "别再语音", "别语音",
    "发文字就行", "打字就行", "打字说吧", "不用说话", "别说话了",
    "不想听语音", "不方便听", "在开会", "安静点",
]


def decide_voice(cfg: dict, user_text: str, user_was_voice: bool = False):
    """决定这一轮发语音还是发文字。返回 (要不要发语音, 为什么)。"""
    # 总开关：关了就是关了，跟 mode 无关
    if cfg.get("enabled", True) is False:
        return False, "voice.enabled=false（语音总开关已关）"

    mode = str(cfg.get("mode") or "").strip().lower()
    if not mode:
        # 兼容老配置：只写了 enabled / ratio
        mode = "ratio" if cfg.get("enabled", True) else "never"

    if mode in ("never", "off", "false", "text", "文字", "不用"):
        return False, "mode=never（只发文字）"

    if mode in ("always", "all", "voice", "全语音", "一直"):
        return True, "mode=always（每轮都发语音）"

    if mode in ("ratio", "random", "比例", "随机"):
        r = float(cfg.get("ratio", 0.4))
        return (random.random() < r), f"mode=ratio（{r:.0%} 概率发语音）"

    # 默认：trigger —— 对方提了才发
    # 先看有没有明确说「不要语音」，免得「别发语音了」也被当成想听
    if user_text:
        for neg in DEFAULT_NO_VOICE_WORDS:
            if neg in user_text:
                return False, f"对方明确说了「{neg}」，改用文字"

    words = cfg.get("trigger_words") or DEFAULT_TRIGGER_WORDS
    hit = [str(w) for w in words if w and str(w) in (user_text or "")]
    if hit:
        return True, "对方提到了「" + "、".join(hit[:3]) + "」"
    if user_was_voice and cfg.get("reply_to_voice", True):
        return True, "对方自己发的是语音，用语音回"
    return False, "mode=trigger，对方没提语音"


def split_for_voice(text: str, cfg: dict):
    """长回复切成多条语音条：微信单条上限 60 秒，必须切。"""
    cps = float(cfg.get("chars_per_second", 4.5))
    limit_chars = int(float(cfg.get("max_seconds", 55)) * cps)

    parts = re.split(r"(?<=[。！？!?~…\n])", text)
    chunks, buf = [], ""
    for p in parts:
        if not p.strip():
            continue
        if len(buf) + len(p) <= limit_chars:
            buf += p
        else:
            if buf.strip():
                chunks.append(buf.strip())
            buf = p
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text]


def voice_troubleshoot(cfg: dict, tts_err: Exception = None):
    """语音没发成时该查什么，返回一串要打印的行。

    失败可能发生在两步，先分清是哪一步：

      合成   文字 → 音频文件            tts_err 不为空就是卡在这
      发送   音频文件 → 微信里的语音条   tts_err 为空就是卡在这
    """
    eng = str(cfg.get("engine") or "edge").strip().lower()
    local = eng in ("local", "gptsovits", "gpt-sovits", "gsv", "clone", "本地", "自定义")

    lines = []
    if tts_err is not None:
        lines.append("「语音合成」这一步就失败了（文字没能变成声音）：")
        lines.append(f"  原因：{tts_err}")
        lines.append("")
        if local:
            lines.append("engine=local，检查这三样：")
            lines.append("  1) 本地语音引擎起来了吗（GPT-SoVITS 的 api_v2.py），")
            lines.append("     和 config.yaml 里 voice.local.api 的地址、端口对得上吗")
            lines.append("  2) config.yaml 里 voice.local.ref_audio 指的音频存在吗")
            lines.append("  3) 模型权重（gpt_weights / sovits_weights）路径填对了吗")
            lines.append(f"素材都放这个文件夹：{ROOT / '音色'}")
            lines.append("放什么、怎么放，看那个文件夹里的 说明.txt")
        else:
            lines.append("engine=edge，检查这两样：")
            lines.append("  1) 电脑能上外网吗（Edge-TTS 需要联网）")
            lines.append("  2) config.yaml 里 voice.edge_voice 的名字拼写对不对")
        lines.append("")
    else:
        lines.append("「把语音送进微信」这一步失败了（音频没问题，是微信没收）：")
        lines.append("  1) 微信 → 左下角≡ → 设置 → 快捷键 → 「发送语音消息」")
        lines.append("     绑的键是不是 Alt。不是的话，要么改成 Alt，要么设环境变量")
        lines.append("     DANIYA_VOICE_HOTKEY（例如 Ctrl+Win 就写 0x11,0x5B）")
        lines.append("  2) 微信 → 设置 → 语音视频 → 麦克风 → 选 CABLE Output")
        lines.append("  3) 微信版本要 4.1.9 以上（更早的版本电脑端根本不能发语音）")
        lines.append("")
        lines.append("  自检：双击根目录的「6-查语音.bat」，选 2 会自动对比左/右 Alt。")

    lines.append("改完重启「一键启动.bat」即可恢复。")
    lines.append("（暂时不想折腾的话，把 voice.mode 改成 never，她就只发文字。）")
    return lines


# 「语音气泡 / 时间戳 / 系统提示」这类不是别人说的话，要滤掉。
# 收消息现在走 OCR，噪声判断也跟着放到 ocr_reader 里统一维护，
# 这里留个同名别名，外部照样能 from bridge import is_noise。
is_noise = ocr_reader.is_noise


# ══════════════════════════════════════════════════════════════
# 五、节流：限速 + 深夜静默
# ══════════════════════════════════════════════════════════════

class Throttle:
    def __init__(self, cfg: dict):
        b = cfg.get("behavior", {})
        self.max_per_hour = int(b.get("max_per_hour", 40))
        self.quiet = b.get("quiet_hours", [0, 7])
        self.sent = []

    def _trim(self):
        cutoff = time.time() - 3600
        self.sent = [t for t in self.sent if t > cutoff]

    def in_quiet_hours(self) -> bool:
        h = time.localtime().tm_hour
        start, end = int(self.quiet[0]), int(self.quiet[1])
        return start <= h < end if start <= end else (h >= start or h < end)

    def allow(self) -> bool:
        self._trim()
        return len(self.sent) < self.max_per_hour

    def mark(self):
        self.sent.append(time.time())


# ══════════════════════════════════════════════════════════════
# 五之一、她睡觉的时候被叫醒（只准慢，不准不回）
# ══════════════════════════════════════════════════════════════

# 深夜（behavior.quiet_hours 那一段）他发消息时，顶在她眼前的那句话。
#
# 用户定调（2026-09-21）：「深夜不要保持静默，如果我发消息，她需要回复，
# 只是说她睡觉的时候不会主动发消息」。
#   静默 = 她主动找你（proactive 那一路）
#   不静默 = 他发消息，她必须回
#
# 这段话只做两件事：把语气调成「刚被叫醒」，以及**堵住拿睡觉当借口的路** ——
# 「我睡了」放在这里是最顺嘴的一句，而它恰恰等于不回答（正是他抱怨过的那个毛病）。
# 所以：带半句迷糊可以，用它把话堵回去不行。
NIGHT_NOTE = (
    "# 这个点你已经睡了\n\n"
    "对方这条消息是把你叫醒的。\n"
    "★ 你必须回 —— 不回等于把人家晾在半夜，比回得慢伤人得多。\n"
    "可以像个刚醒的人：短一点、慢一点，开头带半句真实的迷糊"
    "（「嗯？」「几点了」「刚睡着」「手机震了半天」），也可以顺口问他怎么还不睡。\n"
    "★ 但只许带半句 —— 「我睡了」「明天再说」不能拿来当不回答的挡箭牌。\n"
    "★ 他要是问了什么，还是要答他；没话说就说没话说，别岔开。"
)


# ══════════════════════════════════════════════════════════════
# 五之二、她主动找你
# ══════════════════════════════════════════════════════════════

# 关系档位 → 「安静多久才想起你」的倍数。1.0 = 按 config 原值。
# 第 1 档她还端着，得安静很久才想起你；到第 5 档（恋人），你半小时不吭声
# 她就坐不住。
#
# ★ 这张表只是**兜底**：真正的数是 relation.py 每档的 idle_scale
#   （控制台「好感度」页里能直接改，改完立刻生效）。同步见 Proactive.sync_stage。
#   放在模块层，是因为控制台是另一个进程，也得算同一个数。
IDLE_SCALE = {1: 1.8, 2: 1.4, 3: 1.05, 4: 0.8, 5: 0.3}


class Proactive:
    """
    网恋感的关键一块：她会自己先开口。

    没有这个，她就永远只是个「你问一句、她答一句」的应答机 ——
    不管人设写得多好、节奏调得多像，缺了这一块就不像真人。

    素材来自 daily_life：挑一件今天发生过、还没跟他说过的事当引子。
    """

    def __init__(self, cfg: dict, readonly: bool = False):
        p = cfg.get("proactive", {}) or {}
        self.enabled = bool(p.get("enabled", True))
        self.min_idle = float(p.get("min_idle_minutes", 35)) * 60
        self.chance = float(p.get("chance_per_tick", 0.03))
        self.hours = p.get("active_hours", [9, 23]) or [9, 23]
        self.max_hour = int(p.get("max_per_hour", 1))
        self.max_day = int(p.get("max_per_day", 6))
        # ★ 她走不开的时候（在上课 / 睡着了）**不主动开口**（2026-09-22）。
        #   实测那次：10:28 自检说她在上课，她同一分钟主动发来「电梯里碰到
        #   一群新生」——同一个屏幕上两句话打架。真人上课时不会先找人聊天。
        #   ⚠ 只管「她主动找你」这一路；回他消息那一路绝不受影响
        #     （那是「不装死」，见主循环）。想去掉这规矩就把这个旋钮改 false。
        self.quiet_when_busy = bool(p.get("quiet_when_busy", True))
        # config 里的原值单独留一份：max_day 每轮会被档位配额压低，
        # 不能在自身上反复取 min，不然关系升上去了也回不来。
        self.max_day_cfg = self.max_day
        self.last_talk = time.time()
        self.sent_hour = []
        self.sent_day = []
        self.day = dt.date.today()
        # 关系越近，她越沉不住气（见 sync_stage）。1.0 = 按 config 原值
        self.idle_scale = 1.0
        # 记在文件里：重启不该把「今天已经主动过几次」「多久没说话」清零，
        # 不然一重启她就当自己从没开过口，连着来好几条。
        # 控制台也靠这个文件才看得到她今天的主动情况（那是另一个进程）。
        f = p.get("state_file") or "生活/主动.json"
        self.path = Path(f)
        if not self.path.is_absolute():
            self.path = ROOT / self.path
        self._load()
        # 她还欠着几次的「档位配额」，_maybe_proactive 每轮会刷新（控制台照着看）
        self.quota = None
        self.stage = 1
        # 控制台是另一个进程，只读不写 —— 不然两边同时改这个文件会打架
        self.readonly = bool(readonly)

    def sync_stage(self, brain):
        """照关系档位缩放「多久没说话才会主动找你」。

        第 1 档她还端着，得安静很久才想起你；到恋人档，
        你半小时不吭声她就坐不住了。这样「粘人」才是长出来的，不是设定好的。

        ★ 数从 relation 那张档位表里取（控制台「好感度」页改完立刻生效），
          取不到才退回模块层的 IDLE_SCALE。
        """
        try:
            st = int(brain.rel_engine.stage) if brain.rel_engine else 1
        except Exception:
            st = 1
        self.stage = st
        scale = None
        if brain.rel_engine is not None:
            try:
                scale = float(brain.rel_engine.stage_effects(st).get("idle_scale"))
            except Exception:
                scale = None
        if not scale or scale <= 0:
            scale = IDLE_SCALE.get(st, 1.0)
        self.idle_scale = scale

    def _load(self):
        """把上次运行的记录捡回来。文件碎了也不该拦着程序起来。"""
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(d, dict):
            return
        now = time.time()
        keep = []
        for t in (d.get("sent") or []):
            try:
                t = float(t)
            except (TypeError, ValueError):
                continue
            if t > now - 86400:
                keep.append(t)
        self.sent_hour = [t for t in keep if t > now - 3600]
        self.sent_day = keep
        try:
            lt = float(d.get("last_talk") or 0)
        except (TypeError, ValueError):
            lt = 0.0
        if lt > 0:
            self.last_talk = lt

    def _save(self):
        if self.readonly:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"sent": sorted(self.sent_day),
                            "last_talk": self.last_talk},
                           indent=2),
                encoding="utf-8")
        except Exception:
            pass

    def touch(self):
        """真的聊过一轮就调一次 —— 重置「多久没说话」的计时。"""
        self.last_talk = time.time()
        self._save()

    def _trim(self):
        now = time.time()
        n0 = (len(self.sent_hour), len(self.sent_day))
        if dt.date.today() != self.day:
            self.day = dt.date.today()
            self.sent_day = []
        self.sent_hour = [t for t in self.sent_hour if t > now - 3600]
        self.sent_day = [t for t in self.sent_day if t > now - 86400]
        if (len(self.sent_hour), len(self.sent_day)) != n0:
            self._save()

    def in_hours(self) -> bool:
        h = dt.datetime.now().hour
        a, b = int(self.hours[0]), int(self.hours[1])
        return a <= h < b if a <= b else (h >= a or h < b)

    def why_not(self):
        """返回不能发的理由；能发就返回 None。方便日志里一眼看出为什么她不说话。"""
        if not self.enabled:
            return "主动消息已在 config 里关掉"
        self._trim()
        if not self.in_hours():
            return "不在她的活跃时段（proactive.active_hours）"
        if len(self.sent_hour) >= self.max_hour:
            return "这个小时已经主动发过一条了"
        if len(self.sent_day) >= self.max_day:
            return "今天主动发得够多了"
        idle = (time.time() - self.last_talk) / 60.0
        wait_min = self.min_idle * self.idle_scale / 60.0
        if time.time() - self.last_talk < self.min_idle * self.idle_scale:
            return f"才安静了 {idle:.0f} 分钟，还没到 {wait_min:.0f} 分钟"
        return None

    def should(self) -> bool:
        return self.why_not() is None and random.random() < self.chance

    def wait_minutes(self) -> float:
        """她这一档要安静多久才可能开口。越熟越短（见 sync_stage）。"""
        return self.min_idle * self.idle_scale / 60.0

    def idle_minutes(self) -> float:
        return max(0.0, (time.time() - self.last_talk) / 60.0)

    def status(self) -> dict:
        """
        一眼看清「她现在到底会不会开口」。控制台「主动消息」页直接读它。

        最有用的是 why_not —— 平时她没动静的时候，只有这里说得出原因。
        """
        self._trim()
        why = self.why_not()
        # 配额是 0 的时候她压根不会主动开口（只有第 1 档「初遇」是 0；
        # 第 2 档「朋友」1 次 / 第 3 档 3 次 / 第 4 档 5 次 / 恋人 15 次，
        # 见 relation.py 的 DEFAULT_STAGES 里那个 active_per_day）。
        # 这条比「还没安静够」更靠前，理由得照实说，不然看的人会去调错参数
        # （明明该等关系涨上去，却跑去把 min_idle_minutes 改小）。
        if self.quota is not None and self.quota <= 0:
            why = ("这一档她不主动开口 —— 分享欲给的主动配额是 0"
                   "（只有第 1 档「初遇」是 0，到第 2 档「朋友」就长到 1 次了）")
        a, b = int(self.hours[0]), int(self.hours[1])
        return {
            "enabled": self.enabled,
            "can_send": why is None,
            "why_not": why or "",
            "idle_minutes": round(self.idle_minutes(), 1),
            "wait_minutes": round(self.wait_minutes(), 1),
            "base_minutes": round(self.min_idle / 60.0, 1),
            "idle_scale": self.idle_scale,
            "sent_hour": len(self.sent_hour),
            "max_hour": self.max_hour,
            "sent_day": len(self.sent_day),
            "max_day": self.max_day,
            "quota": self.quota,
            "active_hours": [a, b],
            "chance": self.chance,
            # 上课 / 睡着时不主动开口（2026-09-22）。控制台「主动消息」页照着显示。
            "quiet_when_busy": self.quiet_when_busy,
            "sent_times": [dt.datetime.fromtimestamp(t).strftime("%H:%M")
                           for t in sorted(self.sent_day)],
        }

    def mark(self):
        t = time.time()
        self.sent_hour.append(t)
        self.sent_day.append(t)
        self.touch()


def typing_seconds(text: str, hcfg: dict) -> float:
    """照字数估「打字要多久」，用来让几条回复之间的间隔像个真人。"""
    per = float(hcfg.get("typing_speed", 0.09))
    jit = float(hcfg.get("typing_jitter", 0.6))
    return max(0.5, len(text or "") * per + random.uniform(0, jit))


# ══════════════════════════════════════════════════════════════
# 六、主循环
# ══════════════════════════════════════════════════════════════

def _maybe_sticker(sticker, brain, sender, target, hcfg=None):
    """
    回完话之后，按概率追一张表情包。

    「该不该发」全交给 表情包.py 的 why_not 判断（开关/图够不够/聊得够不够久/
    今天超没超/离上一张够不够远），这里只管发。
    """
    if sticker is None or not sticker.enabled:
        return
    try:
        turns = int(brain.rel_engine.turns) if brain.rel_engine is not None else 0
    except Exception:
        turns = 0

    # 没图 / 没到时候就先不说话 —— 每轮都报一次「目录是空的」太吵了
    if sticker.why_not(turns):
        return
    if not sticker.should(turns):
        return

    path = sticker.pick()
    if path is None:
        return
    # 发完一句再跟一张图，别贴在一起 —— 真人是先说完话，隔一下才想起来甩个图
    time.sleep(random.uniform(1.0, 2.6))
    if sender.send_sticker(target, path):
        sticker.mark(path)
        print(f"  （跟了一张表情包：{path.name}）")
    else:
        print("  （表情包没发成，这次就算了，不影响上面的回复）")


def _picture_note(vision, msg):
    """
    对方发来一张图 —— 让视觉模型看一眼，把画面读成一句人话。

    返回要塞给她的「系统提示」；返回 None = 这条不用回
    （比如那块地方其实只是文字，OCR 早就处理过了）。

    读不出来时退回一句实话：承认看不见，让她把内容打字说一遍。
    ★ 绝不猜图里是什么 —— 瞎编一张不存在的图，比老实说看不见糟得多。
    """
    fallback = (
        "[系统提示] 他刚发来一张图片，但你这边图片加载不出来，只看到一块空白。\n"
        "请用你的语气自然地回应一下，顺带提一句让他把图里的东西打字说给你听。\n"
        "绝对不许猜图里是什么、不许假装看到了。"
    )
    png = getattr(msg, "png", None)
    if 读图 is None or vision is None:
        print("  （没接视觉模型，只能老实说看不见）")
        return fallback
    if not png:
        print("  （截图没裁下来，只能老实说看不见）")
        return fallback

    r = 读图.read_media(vision, png)
    if r["kind"] == "text":
        print(f"[看图] {r['text'][:70]}")
        return 读图.wrap_for_chat(r["text"])
    if r["kind"] == "notimage":
        # 画面里其实只有文字 —— 那段 OCR 已经当消息处理过了，这里别再插一句
        print("  （那块地方其实只有文字，交给 OCR 就够了）")
        return None
    print(f"  （没读出来：{r['why']}）")
    return fallback


def _self_check(brain):
    """把「她现在在哪、接下来两小时干嘛」打到黑框控制台。

    这是**给看黑框的人**看的：你不用猜她这会儿该在干嘛，一眼就能看到
    「她心里是有数的」。她**不一定会主动跟他说** —— 但他问到，她就得照这条答
    （这条规矩写在 persona 和 life.context() 里）。

    段子本身由 daily_life.Life.self_check() 生成；这里只负责别让它把主循环搞崩。
    """
    if brain is None or getattr(brain, "life", None) is None:
        return
    try:
        txt = brain.life.self_check()
    except Exception as e:
        print("  （她自检没跑成：%s）" % e)
        return
    if txt:
        print(txt)


# 「她答应过的事到点了」和「她想找你说件事」是两路。这个哨兵值用来区分
# 「这一路没动静」（可以接着试下一路）和「这一路发了，但 poll 没新消息」。
_NO_APPT = object()

# 她说的要是「**待会儿**去干嘛」（还没动身），把这件事压进她今天的时间轴时，
# 前头留多少比例给她磨蹭（见 Brain._place_promise）。
# 0.35 的意思是：「我待会儿也去吃饭了」→ 前 ~35% 她还在原来的地方，
# 后 ~65% 才是真在食堂。没有这个的话，她刚说完「待会儿」，
# 表上就已经是「在食堂」了。
_SOON_LEAD = 0.35


def _maybe_约定(brain, sender, throttle, proactive, hcfg, humanize_on,
                target, tracker):
    """空闲轮询里调用一次：她答应过的事到点了没有（见 约定.py）。

    ★ 这一路**不走**「她想找你说件事」那套判据，因为性质完全不同：
      · 他求她的事（叫我起床 / 提醒我）—— 说好的时间就是闹钟。
        不看「安静够久了没」，不看关系档位的主动配额，
        也不看她的睡觉时段：他求她 06:30 叫他，她就得 06:30 开口。
      · 她自己说的「待会回来」—— 睡觉的时段不发（为了一句
        「洗完澡回来说一声」把人半夜吵醒，那不是她）。

    没发就返回 _NO_APPT；发了返回 tracker.poll() 的结果
    （这一秒里对方要是正好说话了，别丢）。
    """
    if promise is None:
        return _NO_APPT
    try:
        got = promise.due_items()
    except Exception as e:
        print(f"  （约定没查成：{e}）")
        return _NO_APPT
    if not got:
        return _NO_APPT

    # 一次只发一条 —— 攒了三条也不会一口气倒给他
    chosen = None
    for it, _due in got:
        if it.get("kind") == "comeback" and throttle.in_quiet_hours():
            continue                       # 她自己说回来的，不该吵醒他
        chosen = it
        break
    if chosen is None:
        return _NO_APPT
    if not throttle.allow():
        return _NO_APPT

    print("  ★ 到点了：她答应过「%s」（起因：%s）"
          % (chosen.get("text", ""), chosen.get("raw") or "—"))
    try:
        opener = brain.proactive(promise.seed_for(chosen))
    except Exception as e:
        print(f"[!] 约定那条没能生成：{e}")
        return _NO_APPT
    if not opener:
        return _NO_APPT

    print(f"[{_me()}] {opener}")
    behavior = brain.cfg.get("behavior", {}) or {}
    if behavior.get("split_replies", True):
        mc, mp, tc = _length_limits(brain, behavior)
        parts = split_for_chat(opener, mc, mp, tc)
    else:
        parts = [opener]
    for i, piece in enumerate(parts):
        if i:
            time.sleep(random.uniform(1.0, 2.5))
        if humanize_on:
            time.sleep(typing_seconds(piece, hcfg))
        sender.send_text(target, piece)
    throttle.mark()
    proactive.mark()
    promise.mark_fired(chosen.get("id"),
                       dt.datetime.now().strftime("%H:%M"))
    # 消化掉这次界面变化，免得下一轮把刚发出去的气泡当成新消息
    time.sleep(random.uniform(0.8, 1.4))
    return tracker.poll()


def _maybe_proactive(brain, sender, throttle, proactive, hcfg, humanize_on,
                     target, tracker):
    """空闲轮询里调用一次：该不该由她先开口。"""
    if not proactive.enabled:
        return

    # 关系浅的时候她本来就不该主动开口。
    # 「会主动找你」是第 3 档才长出来的本事 —— 刚认识就天天来问你在吗，
    # 那是推销，不是达妮娅。配额见 分享.py 的 proactive_per_day。
    sh = getattr(brain, "share", None)
    if sh is not None and getattr(sh, "enabled", False):
        try:
            quota = sh.proactive_quota()
        except Exception:
            quota = 99
        proactive.quota = quota
        if quota <= 0:
            return
        # 注意别写成 min(proactive.max_day, quota) —— 那是在被压过的值上再压，
        # 关系后来升上去了也回不来。永远拿 config 原值去比。
        proactive.max_day = min(proactive.max_day_cfg, quota)

    why = proactive.why_not()
    if why:
        return
    # ★ 静默只管这一路：她睡觉的时候【不主动找你】。
    #   他发消息的那一路（主循环）绝不能过这道判断 —— 那是「不装死」。
    if throttle.in_quiet_hours():
        return
    # ★ 她此刻走不开（在上课 / 睡着了）就先不说话（2026-09-22）。
    #   同样只管这一路 —— 他发消息来照样得回（顶多回得短）。
    #   判断用的是分钟级作息表（daily_life.now_brief），不是「随机忙一下」：
    #   控制台「她自检」里看到她在上课，这里就一定不会开口，两边对得上。
    if getattr(proactive, "quiet_when_busy", True) and brain.life is not None:
        try:
            _nb = brain.life.now_brief() or {}
        except Exception:
            _nb = {}
        if _nb.get("busy"):
            _what = _nb.get("text") or ""
            if "睡" in _what:
                _what = "在睡觉"
            else:
                _what = _what[:16]
            print("  （她现在%s，这会儿不主动开口；"
                  "想让她上课也开口就把 proactive.quiet_when_busy 改成 false）"
                  % _what)
            return
    if random.random() >= proactive.chance:
        return
    if not throttle.allow():
        return

    # 挑一件「今天发生过、还没跟你说过」的事当引子
    seed = None
    if brain.life is not None:
        try:
            seed = brain.life.nudge()
        except Exception as e:
            print(f"  （平行生活取话题失败：{e}）")
    if not seed:
        print("  （她想开口，但今天没什么可说的）")
        return

    print(f"[主动] 她想说的事：{seed['text'][:40]}")
    try:
        opener = brain.proactive(seed)
    except Exception as e:
        print(f"[!] 主动消息生成失败：{e}")
        return
    if not opener:
        return

    print(f"[{_me()}] {opener}")
    # 主动消息也一样受字数预算管着（见 分享.py）——
    # 第 1、3 档她主动开口时也只会说一两句，不会甩一大段过来。
    behavior = brain.cfg.get("behavior", {}) or {}
    if behavior.get("split_replies", True):
        mc, mp, tc = _length_limits(brain, behavior)
        parts = split_for_chat(opener, mc, mp, tc)
    else:
        parts = [opener]
    for i, piece in enumerate(parts):
        if i:
            time.sleep(random.uniform(1.0, 2.5))
        if humanize_on:
            time.sleep(typing_seconds(piece, hcfg))
        sender.send_text(target, piece)
    throttle.mark()
    proactive.mark()

    # 消化掉这次界面变化，免得下一轮把刚发出去的气泡当成新消息。
    # 但返回值不能扔 —— 这一秒里对方要是正好说话了，poll() 已经把它
    # 登记成「见过」了，扔掉就是永久丢消息。交回主循环当 pending 用。
    time.sleep(random.uniform(0.8, 1.4))
    return tracker.poll()


def _maybe_summarize(brain):
    """该复盘就复盘一次 —— 让她把最近这段聊的事记进长期记忆。

    这是「聊到第 300 轮，她还记得第 20 轮说过什么」的唯一办法。
    要调一次 LLM，会花几秒；失败不影响聊天。
    """
    j = getattr(brain, "journal", None)
    if j is None or not j.enabled:
        return
    turns = brain._turns_now()
    need, why = j.need(turns)
    if not need:
        return

    print(f"  · 该记点东西了（{why}），她在复盘……")
    try:
        got = j.summarize(
            lambda msgs, mt: brain._chat(msgs, max_tokens=mt),
            brain.history, turns,
        )
    except Exception as e:
        print(f"  （复盘没成：{e}）")
        return
    if got:
        print(f"  · 记下来了：{got[:70]}…")


# ══════════════════════════════════════════════════════════════════════
#  在线状态：心跳 + 开机补一句
# ══════════════════════════════════════════════════════════════════════
#  她启动时会把「此刻已经躺在屏幕上的消息」记成看过了，故意不回 ——
#  这是对的：关一天再开，不该把攒下的一屏旧消息挨个回一遍。
#  但副作用是：他前脚刚发消息、你后脚才把程序点开，那条会被当成旧消息吞掉，
#  他那边就是干等一个回复不到（实测踩过：消息 15:38 到，程序 15:39:06 才起）。
#  所以这里记一份心跳。开机时如果最后一条气泡是【对方】发的、而且这条没补过，
#  就主动补一句，语气上按「刚拿到手机」处理。
STATE_FILE = ROOT / "生活" / "在线.json"


def _read_state() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_state(**kw) -> None:
    """合并写入，读不到/写不动都当没事 —— 这只是个辅助状态，不能影响聊天。"""
    d = _read_state()
    d.update(kw)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:
        pass


def _away_text(seconds: float) -> str:
    if seconds < 90:
        return "刚刚"
    m = seconds / 60
    if m < 60:
        return f"{int(m)} 分钟"
    h = m / 60
    if h < 24:
        return f"{h:.1f} 小时"
    return f"{h / 24:.1f} 天"


def _catch_up(tracker, behavior: dict):
    """开机时判断要不要补回「错过的最后一条」。返回 (文本, 说明)；不用补就 (None, "")。"""
    if not behavior.get("catch_up", True):
        return None, ""
    try:
        text, mine = tracker.last_turn()
    except Exception as e:
        print(f"  （补消息时读聊天区失败：{e}）")
        return None, ""
    if not text or mine:
        return None, ""                     # 最后一条是自己发的 = 球在他那边
    if ocr_reader.is_noise(text) or not text.strip():
        return None, ""

    st = _read_state()
    if st.get("caught") == text:
        return None, ""                     # 同一条已经补过，别重启一次回一次

    away = None
    ts = st.get("ts")
    if isinstance(ts, (int, float)):
        away = max(0.0, time.time() - float(ts))
        limit = float(behavior.get("catch_up_max_minutes", 0) or 0)
        if limit and away > limit * 60:
            print(f"  （最后一条是 {_away_text(away)}前的，隔太久了，不补）")
            return None, ""

    how = f"你离开了大概 {_away_text(away)}" if away is not None else "你刚把手机拿起来"
    note = (
        "# 你没在的时候他来消息了\n\n"
        f"下面这条是对方在你没看手机的时候发来的（{how}），你刚拿到手机才看到。\n"
        "回复里可以自然地带半句（「刚看到」「手机丢一边了」这种），也可以完全不提。\n"
        "只回这一条最新的，别把更早的旧消息一起翻出来回。"
    )
    return text, note


# ══════════════════════════════════════════════════════════════════════════
#  指令层 —— 微信里发「/切换 爱弥斯」就能换人
# ══════════════════════════════════════════════════════════════════════════
#
#  ★ 它插在「拿到消息之后、送进大脑之前」。
#
#    这一点是有意的：指令**永远不进 LLM、不进她的记忆**。
#    否则你发一句「/切换爱弥斯」，她会真的"记得"你说过这句话 ——
#    对她来说那是句莫名其妙的话，还会把她的人设带歪。
#    真人不会记得你对她输入过系统命令。
#
CMD_RE = re.compile(r"^[/／]\s*(切换|人物|角色|帮助|help|who)\s*(.*)$", re.S)


def _handle_command(user_text: str, target: str):
    """认一认这条是不是指令。

    返回 (是不是指令, 要发出去的话[list], 新角色名 or None)。
    新角色名不是 None 时，调用方负责重建大脑（见 main_loop）。
    """
    m = CMD_RE.match((user_text or "").strip())
    if not m:
        return False, [], None

    verb = m.group(1)
    arg = (m.group(2) or "").strip().strip("/／").strip()

    if verb in ("人物", "角色"):
        ls = 角色.chars()
        if not ls:
            return True, ["这边还没有能联系的人。"], None
        cur = 角色.current()
        body = "、".join(("%s（现在是她）" % n) if n == cur else n for n in ls)
        return True, ["能联系到的有：" + body,
                      "想换人的话跟我说「/切换 名字」就行"], None

    if verb in ("帮助", "help", "who"):
        return True, ["会几招：",
                      "/切换 名字   —— 换个人跟你说话",
                      "/人物       —— 看看都有谁",
                      "（这两句只给系统看，不会进她的记忆）"], None

    if verb == "切换":
        if not arg:
            return True, ["换成谁？发「/人物」看看都有谁。"], None
        ok, msg = 角色.switch(arg)
        if not ok:
            return True, [msg], None
        # ★ 切成功了**先不说话** —— 让她自己开口（见 _maybe_开场）。
        #   旧角色不说再见、新角色不说"我来了"，就是换人了。
        #   这是用户定的：静默切换，不留话。
        _set_console_title()        # 标题跟着走，别让人看错现在是谁
        return True, [], arg

    return False, [], None


def _maybe_开场(sender, target: str) -> bool:
    """她主动说的第一句 —— 只发一次。

    为什么单独开一条路：
        第 1 档「初遇」里 active_per_day = 0 —— 她这个阶段是**不主动**的。
        但「第一句话」必须由她说（手机是她捡到的、微信是她加的），
        所以它不能走主动消息那条通道，得在这儿单独触发一回。

    为什么不走 LLM：
        开场白是精心写好的（每角色一份 开场.md），直接发出去就行 ——
        零 token、不会跑偏、不会因为模型发挥而毁掉第一印象。
        发过就记在 生活/开场.json，重启也不会重发。

    为什么是一串而不是一句：
        开场.md 按 `## 段` 分段，每段挑一句、按顺序发 ——
        段就是「接触的步骤」：先打个招呼 → 自报家门 → 问一句你叫什么。
        这是用户点名要的「接触的过程」。
    """
    try:
        if 角色.发过开场():
            return False
        cand = 角色.开场白()
        if not cand:
            return False
        for i, say in enumerate(cand):
            if i:
                time.sleep(random.uniform(1.1, 2.6))
            print(f"[{_me()}] （她说的第一句 {i + 1}/{len(cand)}）{say}")
            sender.send_text(target, say)
        角色.记下开场()
        return True
    except Exception as e:
        print(f"  （开场白没发出去：{e}）")
        return False


def _rebuild_for_char(cfg: dict, brain, sender, throttle, proactive,
                      sticker, vision):
    """换人之后把所有"跟着 config 走"的东西重建一遍。

    要重建的原因：这些东西在构造时就把 ROOT 下的路径、config 里的数值
    存成了自己的字段（比如 Brain.persona_file）——
    不重建的话，切过去之后读的还是上一个人的文件。

    顺带清掉 daily_life 的模块级今日偏移缓存（_ACTIVE_PLAN），
    否则新角色会带着上一个人今天的日程出门。

    返回 (cfg, target, brain, sender, throttle, proactive, sticker, vision)。
    """
    try:
        daily_life.set_active_plan({})
    except Exception:
        pass

    new_cfg = load_config()
    brain = Brain(new_cfg)
    sender = Sender(new_cfg)
    throttle = Throttle(new_cfg)
    proactive = Proactive(new_cfg)
    if stickers is not None:
        try:
            sticker = stickers.Sticker(new_cfg)
        except Exception as e:
            print(f"  （表情包没重建起来：{e}）")
    if 读图 is not None:
        try:
            vision = 读图.Vision(new_cfg, new_cfg.get("llm"))
        except Exception as e:
            print(f"  （读图没重建起来：{e}）")
    return (new_cfg, new_cfg.get("target") or "",
            brain, sender, throttle, proactive, sticker, vision)


def _set_console_title() -> None:
    """把控制台标题改成「<角色> - 微信机器人」。

    ★ 为什么要在这儿再设一遍（2026-09-27 实测）：
      `一键启动.bat` 里那行是 `for /f` 抓 `角色.py --who` 的输出，
      抓不到就回落到**写死的「达妮娅」**（单角色时代留下的）。
      结果：机器人跑的是爱弥斯，窗口标题却写着达妮娅 —— 一眼看错人。
      这里按**当前角色**重设一次，不依赖 bat 那行能不能抓到值；
      切人（/切换、控制台换人）时也会再设一遍，标题跟着走。

    ★ SetConsoleTitleW 走的是宽字符，中文不会乱码（`title` 命令会）。
    """
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(
            "%s - 微信机器人" % 角色.current())
    except Exception:
        pass                     # 非 Windows / 没有控制台：无所谓，别影响启动


def main_loop(cfg: dict):
    _set_console_title()
    target = cfg["target"]
    brain = Brain(cfg)
    sender = Sender(cfg)
    throttle = Throttle(cfg)
    proactive = Proactive(cfg)
    behavior = cfg.get("behavior", {})
    voice_cfg = cfg.get("voice", {})
    hcfg = cfg.get("humanize", {}) or {}
    humanize_on = bool(hcfg.get("enabled", True))

    # 连续发语音失败的次数。攒到 2 次就本次运行自动关掉语音，
    # 免得每轮都白等 30 秒、还把微信抢到前台。
    voice_fail_streak = 0

    if not sender.health():
        print(f"[!] 连不上 WeeMessenger（{sender.url}）")
        print("    请先双击 WeeMessenger 的「启动.bat」，选标准模式，再回来跑这个脚本。")
        sys.exit(1)
    print(f"[√] WeeMessenger 已就绪：{sender.url}")

    win = find_window()
    if not win:
        print("[!] 找不到微信窗口。请确认微信已登录，并且没有最小化到托盘。")
        sys.exit(1)
    print(f"[√] 已连上微信窗口：{win.Name}")

    open_chat(win, target)
    print(f"[√] 已进入会话「{target}」")

    # ── 收消息：截图 + OCR ──
    print("[√] 正在加载 OCR 模型……")
    try:
        ocr_reader.engine()
    except Exception as e:
        print(f"[!] OCR 引擎加载失败：{e}")
        print("    跑一下这条命令补依赖：")
        print("    .venv\\Scripts\\python.exe -m pip install rapidocr-onnxruntime Pillow")
        sys.exit(1)

    tracker = ocr_reader.IncomingTracker(win, cfg=cfg, debug_dir=BASE / "日志")

    # ── 表情包（她回完话按概率跟一张）──
    sticker = stickers.Sticker(cfg) if stickers is not None else None
    if sticker is None:
        print("[!] 表情包没接上：表情包.py 没加载起来")
    elif not sticker.enabled:
        print("[·] 表情包关着（config 里的 sticker.enabled）")
    elif not sticker.count():
        print(f"[·] 表情包：{sticker.dir} 里还没有图 —— "
              f"丢几张 .gif 进去就自动生效，不用改配置")
    else:
        print(f"[√] 表情包：{sticker.count()} 张，聊过 {sticker.min_turns} 轮后"
              f"按 {int(sticker.chance * 100)}% 概率跟一张")

    # ── 读图：OCR 对图片是瞎的，配一个视觉模型补上（详见 读图.py）──
    vision = 读图.Vision(cfg, cfg.get("llm")) if 读图 is not None else None
    if vision is None:
        print("[!] 读图没开：读图.py 没加载起来（看上面有没有报错）")
    elif vision.configured():
        print(f"[!] 读图没开：{vision.configured()}")
        print("    对方发图片时她只会说「我看不到图」。想让她看懂，")
        print("    去 config.yaml 的 vision 段填一个视觉模型。")
    else:
        print(f"[√] 读图已就绪：{vision.model}")

    primed = False
    for _ in range(3):
        if tracker.prime():
            primed = True
            break
        time.sleep(1.0)
    if not primed:
        print("[!] 读不到微信聊天区。")
        print("    请确认微信窗口是打开的（不是最小化到托盘），")
        print("    然后双击「3-自检.bat」看具体诊断。")
        sys.exit(1)
    print("[√] 收消息已就绪（后台截图 + OCR），开始监听……\n")

    # 开机补一句：他刚才发的那条，如果程序没在跑就没被看见。
    # 判断放在 prime 之后 —— prime 只负责把「当前画面」定成基线，
    # 这里再看一眼基线的最后一条是谁发的，决定要不要还他一句。
    pending, catch_note = _catch_up(tracker, behavior)
    if pending:
        print(f"[补] 你没在的时候他来过消息：{pending[:50]}")
        print("     这条马上回，别让他干等（回复里她可以自然带一句「刚看到」）")
        tracker.mark_replied(pending)      # 补完登记一下，别下一轮又回一遍
        _write_state(ts=time.time(), caught=pending)
    else:
        _write_state(ts=time.time())

    # ── 她主动说的第一句 ──
    #    只在「这个角色还没开口过」时发（见 _maybe_开场）。
    #    新角色第一次跑起来，就是她先来打招呼 —— 这就是接触的开始。
    if _maybe_开场(sender, target):
        time.sleep(1.2)

    poll = float(behavior.get("poll_interval", 2.0))
    last_beat = time.time()

    # 这一轮正在处理的那条消息。★ 一旦 poll() 把它交出来，它就已经被登记成
    # 「见过了」—— 之后任何一步炸了，它都【不会再出现第二次】。所以拿到手就先
    # 记在这儿：中途出错就放回 pending，下一轮重来，绝不让它凭空消失
    # （实测踩过：22:44:47 那一条她既没回、记忆里也一个字都没有）。
    consumed = None

    while True:
        try:
            time.sleep(poll)

            # ── 有人从「别的地方」换了人吗？（控制台 / 命令行 角色.py --use）──
            #    控制台和机器人是两个进程：控制台换人只写了 角色\当前.json，
            #    机器人不回头看盘就永远停在老角色上 —— 表现就是
            #    「控制台点了切换，微信里一点反应都没有」。
            #    这里每轮看一眼那个小文件，变了就地重建大脑，不用重启。
            _now_char = 角色.sync_from_pointer()
            if _now_char:
                # 两种可能：换了人，或者还是同一个人但被「退回初遇」了 ——
                # 两种都得重建大脑（Brain 构造时就把文件路径和关系读进去了）。
                print(f"\n[切换] 别处动了「{_now_char}」（换人 / 退回初遇）—— 就地重读……")
                try:
                    (cfg, target, brain, sender, throttle, proactive,
                     sticker, vision) = _rebuild_for_char(
                        cfg, brain, sender, throttle, proactive, sticker, vision)
                    behavior = cfg.get("behavior", {})
                    voice_cfg = cfg.get("voice", {})
                    hcfg = cfg.get("humanize", {}) or {}
                    humanize_on = bool(hcfg.get("enabled", True))
                    voice_fail_streak = 0
                    poll = float(behavior.get("poll_interval", 2.0))
                    open_chat(win, target)
                    print(f"[切换] 已切到「{_now_char}」—— 她的人设、世界书、记忆、"
                          f"日程都是她自己的，刚才那个人一个字都没留下。")
                    _maybe_开场(sender, target)
                except Exception as e:
                    print(f"[!] 切换的时候出了点问题：{e}")
                continue

            proactive.sync_stage(brain)     # 关系越近，她越沉不住气

            if time.time() - last_beat > 20:      # 心跳：下次开机能算出她离开了多久
                last_beat = time.time()
                _write_state(ts=last_beat)

            note = catch_note
            if pending is not None:
                user_text, pending, catch_note = pending, None, ""
            else:
                user_text = tracker.poll()

            # ── 对方发来一张图 / 表情包（OCR 一个字都读不到的那种）──
            # 这类消息没有文本，得先让视觉模型看，再当成「他发的图」递给她。
            if isinstance(user_text, ocr_reader.MediaMsg):
                _bx = getattr(user_text, "box", None) or (0, 0, 0, 0)
                print(f"[收到] 一张图（{user_text.note or '没文字'}，"
                      f"裁的那块 {_bx[2]-_bx[0]}x{_bx[3]-_bx[1]}，"
                      f"存到 日志\\media-*.png）")
                user_text = _picture_note(vision, user_text)
                if not user_text:
                    continue
                print("  → 已经读出来了，当成「他发的图」递给她")

            # ── 没人说话的时候 ──
            #    先看「她答应过的事到点了没」（约定.py）—— 那是有准点的，
            #    比「安静够久了、掷个骰子」要紧得多。两路都没动静就继续等。
            if not user_text:
                late = _maybe_约定(brain, sender, throttle, proactive,
                                   hcfg, humanize_on, target, tracker)
                if late is _NO_APPT:
                    late = _maybe_proactive(brain, sender, throttle, proactive,
                                            hcfg, humanize_on, target, tracker)
                if late is not None and late is not _NO_APPT:
                    pending = late           # 她刚开口那一秒对方说话了，别丢
                continue

            print(f"[收到] {user_text[:60]}")
            consumed = user_text          # 拿到手就记下：出错要放回去重来

            # ── 指令层：换人 / 看有谁 ──
            #    插在这儿（送进大脑之前），所以指令不进 LLM、不进她的记忆。
            _hit, _say, _new = _handle_command(user_text, target)
            if _hit:
                consumed = None
                for _line in _say:
                    sender.send_text(target, _line)
                    time.sleep(0.6)
                if _new:
                    print(f"\n[切换] 换到「{_new}」……")
                    try:
                        (cfg, target, brain, sender, throttle, proactive,
                         sticker, vision) = _rebuild_for_char(
                            cfg, brain, sender, throttle, proactive, sticker, vision)
                        behavior = cfg.get("behavior", {})
                        voice_cfg = cfg.get("voice", {})
                        hcfg = cfg.get("humanize", {}) or {}
                        humanize_on = bool(hcfg.get("enabled", True))
                        voice_fail_streak = 0      # 新人重新开始算连续失败
                        open_chat(win, target)
                        print(f"[切换] 已切到「{_new}」—— 她的人设、世界书、记忆、"
                              f"日程都是她自己的，刚才那个人一个字都没留下。")
                        _maybe_开场(sender, target)
                    except Exception as e:
                        print(f"[!] 切换的时候出了点问题：{e}")
                continue

            # ── 她自己先捋一遍「现在在哪、接下来两小时干嘛」—— 打给你看 ──
            #       她心里有数，但不一定主动跟他说；他问到才必须照实答。
            _self_check(brain)

            # ── 语音条特殊处理 ──
            # OCR 只能「看」，听不到声音。对方发语音时我们能拿到的只有
            # `[语音] 5"` 这种字样；如果微信开了
            # 「聊天中的语音消息自动转成文字」，气泡里会多出转写文字，那就当普通消息处理。
            user_was_voice = ocr_reader.looks_like_voice(user_text)
            if user_was_voice:
                spoken = ocr_reader.strip_voice_prefix(user_text)
                if spoken:
                    print(f"  （这是语音，已用微信自带的转文字内容：{spoken[:40]}）")
                    user_text = spoken
                else:
                    print("  （这是一条语音，但没有任何文字内容 —— 听不到）")
                    user_text = (
                        "[系统提示] 对方发来了一条语音消息，你听不到声音，"
                        "只知道有这么一条。请用你的语气自然地回应一下，"
                        "顺带提一句让你打字说，别假装听懂了内容。"
                    )

            # ── 深夜：她已经睡了 —— 但你发消息，她一定回 ──
            # 用户定调（2026-09-21）：「深夜不要保持静默，如果我发消息，她需要回复，
            # 只是说她睡觉的时候不会主动发消息」。
            # 所以静默只作用于【她主动找你】（见 _maybe_proactive 里那处 quiet 判断），
            # 绝不作用于【回复】。以前这里是 continue 直接吞掉，他半夜发的消息
            # 一条都回不了 —— 被晾在半夜比回得慢伤人得多。
            night = throttle.in_quiet_hours()
            if night:
                print("  （这个点她已经睡了 —— 但不装死：被消息叫醒也得回，只是慢一点、短一点）")
            if not throttle.allow():
                print("  （这一小时发太多了，这条【故意】不回）")
                consumed = None
                continue

            # ── 拟人节奏①：她多久才「看到」这条消息 ──
            # ★ 2026-09-22 按用户要求，把「她正在忙，隔一两分钟才看到」那套删掉了。
            #   原话：「把那个她在忙机制删掉吧，隔个一两分钟才回我也不知道她在干嘛」。
            #   那个机制是 10% 概率随机拖 40~150 秒，理由只有程序自己知道 ——
            #   他那边看到的就是「她明明在线却不回」。宁可删掉。
            #   现在只有两种等待，都短、都说得通：
            #     · 深夜被叫醒：她睡着了，摸手机要几秒（而且这条会写进提示）
            #     · 平时：1~3.5 秒，就是打个字的时间
            if night:
                # 她睡着了，被这条消息叫醒 —— 摸到手机、看清屏幕上是什么，都要一会儿。
                # 注意这段等待是【拟人】，不是静默：她一定会回，只是回得像个刚醒的人。
                if humanize_on:
                    wait = random.uniform(
                        float(behavior.get("night_wake_min", 6)),
                        float(behavior.get("night_wake_max", 20)))
                    print(f"  （她已经睡了，被这一条叫醒，{wait:.0f} 秒才摸到手机）")
                    time.sleep(wait)
                note = ((note + "\n\n") if note else "") + NIGHT_NOTE
            else:
                time.sleep(random.uniform(
                    float(behavior.get("reply_delay_min", 1.0)),
                    float(behavior.get("reply_delay_max", 3.5)),
                ))

            answer = brain.reply(user_text, note=note, night=night)
            consumed = None               # 回复已生成、记忆也写好了，这条算办完了
            print(f"[{_me()}] {answer[:60]}{'…' if len(answer) > 60 else ''}")
            proactive.touch()

            use_voice, voice_why = decide_voice(voice_cfg, user_text, user_was_voice)
            print(f"  （本轮{'发语音' if use_voice else '发文字'}：{voice_why}）")

            if use_voice:
                chunks = split_for_voice(answer, voice_cfg)
                failed_at = None
                tts_err = None      # 非空 = 卡在「合成」那一步，不是发不出去
                for i, chunk in enumerate(chunks):
                    try:
                        audio = sender.tts(chunk)
                    except Exception as e:
                        tts_err = e
                        failed_at = i
                        break
                    if sender.send_voice(target, audio):
                        if i < len(chunks) - 1:
                            time.sleep(random.uniform(0.8, 2.0))
                        continue
                    failed_at = i
                    break

                if failed_at is None:
                    voice_fail_streak = 0
                else:
                    # 语音没成时，达妮娅的话不能就这么没了 ——
                    # 把没发出去的那一段（以及后面还没发的）改用文字补上。
                    rest = "".join(chunks[failed_at:]).strip()
                    if rest:
                        print("  （语音没发成，改用文字把内容补上，别让回复凭空消失）")
                        sender.send_text(target, rest)

                    voice_fail_streak += 1
                    if voice_fail_streak >= 2 and voice_cfg.get("enabled", True):
                        voice_cfg["enabled"] = False
                        print()
                        print("  ⚠ 连续两次都没发成语音，本次运行已自动关闭语音，只发文字。")
                        for line in voice_troubleshoot(voice_cfg, tts_err):
                            print("    " + line)
                        print()
            else:
                # 真人不会一口气发一大段 —— 长回复拆成几条连着发。
                # 上限跟着关系档位走：第 1 档一次总共 20 个字、最多 2 条，
                # 档位越高她才越能多说（见 分享.py 的档位表）。
                if behavior.get("split_replies", True):
                    mc, mp, tc = _length_limits(brain, behavior)
                    pieces = split_for_chat(answer, mc, mp, tc)
                else:
                    pieces = [answer]
                for i, piece in enumerate(pieces):
                    if i:
                        # 条与条之间：歇一会儿再打下一条（打了删掉重打也在这段时间里）
                        if humanize_on:
                            time.sleep(random.uniform(
                                float(hcfg.get("gap_min", 1.2)),
                                float(hcfg.get("gap_max", 4.5)),
                            ))
                        else:
                            time.sleep(random.uniform(0.6, 1.8))
                    # 打字耗时：按这条的字数估，别「啪」一下就发出去
                    if humanize_on:
                        time.sleep(typing_seconds(piece, hcfg))
                    sender.send_text(target, piece)

            throttle.mark()

            # ── 追一张表情包 ──
            _maybe_sticker(sticker, brain, sender, target,
                           hcfg if humanize_on else None)

            # 聊过一段了，让她把这段时间的事记进长期记忆
            _maybe_summarize(brain)

            # 发完之后界面会变，先把这次变化消化掉，
            # 免得下一轮把「刚发出去的气泡」又当成新消息。
            #
            # ★ 但别把它的返回值扔了（这里踩过坑）：就在这一秒里对方又发来
            #   消息的话，poll() 已经把它登记成「见过了」，扔掉就等于【永久丢消息】。
            #   实测用户反馈「发了两条只回第二条」，这就是其中一条路径。
            time.sleep(random.uniform(0.8, 1.4))
            late = tracker.poll()
            if late is not None:
                pending = late

        except KeyboardInterrupt:
            print("\n[√] 已停止。")
            break
        except Exception as e:
            # 报错缩进排版，多行的错误提示才不会糊成一坨。
            lines = str(e).splitlines() or [e.__class__.__name__]
            print(f"[!] 出错了：{lines[0]}")
            for extra in lines[1:]:
                print(f"    {extra.rstrip()}")
            if consumed:
                # ★ 这条已经被 poll() 登记成「见过」了，扔掉就是永久丢。
                #   放回队列，下一轮重来 —— 宁可晚一轮，也不能让她像没看见一样。
                pending = consumed
                consumed = None
                print("    （这条消息还没处理完，已放回队列，下一轮重来）")
            print("    （这一轮跳过，程序还在跑）")
            time.sleep(3)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"

    if mode == "dump":
        # 自检只关心「微信的消息读不读得到」，跟 LLM Key 无关，
        # 所以这里特意不调 load_config()，免得没填 Key 就跑不了自检。
        w = find_window()
        if not w:
            print("[!] 找不到微信窗口。")
            print("    请先打开微信、用小号登录，窗口不要最小化到托盘，然后重跑。")
            sys.exit(1)
        dump_tree(w)
    else:
        main_loop(load_config())
