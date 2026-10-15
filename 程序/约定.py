# -*- coding: utf-8 -*-
"""约定 · 她说出口的话，得算数
==================================================================

治的病：**她说了「我先去洗澡 待会再聊」，然后就没有然后了。**

聊天里冒出来的「以后」原来全掉在地上，分三类：

  ① 他提的：「明天早上七点半叫我起床」「七点叫我」「到时提醒我拿报告」——
     她嘴上答应了，但第二天早上她开不开口，取决于
     `生活/主动.json` 里那套「安静够久了 + 掷骰子」，
     跟「她答应过叫他起床」**一点关系都没有**。
  ② 她自己说的：「我先去洗澡 待会再聊」「等我一下」「吃完饭回来」——
     说完就忘，人不会回来。他只能自己再问一句「洗完了吗」。
  ③ **他定的约**：「上午九点我跟她说晚上六点去和西西吃饭」——
     这不是「待还的事」，是**一条日程**：晚上六点她真的在跟他吃饭。
     它进她的时间轴，任何「每天重排的日程」都**删不掉**它，
     只有他自己开口（`--drop`）才能取消。

现在这两类都落成一条**待办**，到点就由她主动开口（见 `bridge._maybe_约定`）。

文件：`生活/约定.json`
    {
      "seq": 3,
      "items": [
        {"id": 2, "kind": "wake", "due": "2026-09-23T07:30",
         "text": "叫他起床", "who": "他", "raw": "明天早上叫我起床",
         "made": "2026-09-22T23:10:00", "done": false, "fired": ""},
        {"id": 3, "kind": "appoint", "due": "2026-09-22T18:00", "dur": 120,
         "text": "去和西西吃饭", "who": "他", "raw": "晚上六点去和西西吃饭",
         "made": "2026-09-22T09:03:00", "done": false, "fired": ""}
      ]
    }

★ 解析只用**朴素规则**（正则 + 时间词表），一个字都不问模型。
  理由：这件事的价值就在于「一定发生」—— 靠她当天心情好不好，
  就又回到原来那个「说了不算」的状态了。

★ 两种到点之后的待遇不一样：
    · 「他求她的事」（wake / remind）—— 说好的时间就是闹钟，
      哪怕她说好 06:30 叫他，凌晨也算数（她定的是闹钟，不是心情）。
    · 「她自己说的回来」（comeback）—— 她睡觉的时段不发，
      谁也不会为了「洗完澡回来说一声」半夜把人吵醒。

命令行：
  python 约定.py                          看她现在还欠着哪些「待会」
  python 约定.py --due                    只看此刻已经到点的
  python 约定.py --parse "明天早上叫我起床"   试解析一句话（不动文件）
  python 约定.py --parse-her "我先去洗澡 待会再聊"
  python 约定.py --add "明天07:30 叫他起床"  手动加一条
  python 约定.py --done 2                 手动勾掉第 2 条
  python 约定.py --drop 3                 删掉第 3 条（他定的约也这么取消）
  python 约定.py --clear                  全清
"""

from pathlib import Path
import datetime as dt
import json
import re
import sys

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
# 超过这么久还没发出去的就作废 —— 他早就不等了，
# 现在补一条「我来叫你起床了」只会显得莫名其妙。
DUE_GRACE_MIN = 90

# 还差多少分钟就算「到点了」（见 prompt_block）——
# 比它更远的只列出来「别提前说」，不算这一轮该开口的事。
_HINT_LEAD_MIN = 10

# 「她自己说回来」这类，最长押多久。再长就不是「待会」了。
COMEBACK_MAX_MIN = 120

FILE = ROOT / "生活" / "约定.json"

# ── 「他定的约」：不是「待还的事」，是一条**日程** ──────────────────────
#   「上午九点我跟她说晚上六点去和西西吃饭」——
#   这不是叫她几点叫他，是**他真的占用了她那个时段**。
#   所以它：
#     · 进她的时间轴（见 daily_life.Life.diverts_today）
#     · **不**由 _maybe_约定 主动开口（`due_items` 跳过它）
#     · 每天重排日程也删不掉（它存在这个文件里，不在她的今天.json 里）
#     · 只有他自己要求才删（--drop）
SCHEDULE_KINDS = ("appoint",)
# 没写多久的话，一个约默认占两小时
APPOINT_DEFAULT_MIN = 120
# 认为是「他定了个约」的说法（够强才认，免得把随口一句当成日程）
_APPOINT_WORDS = (
    "去", "一起", "一块", "一块儿", "陪", "约", "见面", "见个面", "逛街",
    "散步", "看电影", "打游戏", "玩游戏", "吃饭", "找你", "来找我", "带我",
    "图书馆", "食堂", "买东西", "玩", "喝", "逛",
)
# 反悔 / 问句 —— 不是约定
_APPOINT_NEG_RE = re.compile(r"不去|别去|不能去|去不了|不想去|算了")


# ══════════════════════════════════════════════════════════════════════════
#  一、时间词 → 真的时间
# ══════════════════════════════════════════════════════════════════════════

_CN = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num(s):
    """中文数字 / 阿拉伯数字 → 整数。一 ~ 九十九够用。"""
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s == "半":
        return None
    if "十" in s:
        a, _, b = s.partition("十")
        hi = _CN.get(a, 1) if a else 1
        lo = _CN.get(b, 0) if b else 0
        return hi * 10 + lo if (a in _CN or not a) and (b in _CN or not b) else None
    if len(s) == 1:
        return _CN.get(s)
    return None


# 「明天早上七点半」= 日 word + 时段 word + 钟点，三样可以任意缺
_DAY_WORDS = [("大后天", 3), ("后天", 2), ("明天", 1), ("明日", 1), ("明早", 1),
              ("明晚", 1), ("今晚", 0), ("今早", 0), ("今天", 0), ("今日", 0)]
_AMPM = ("早上", "上午", "早上", "中午", "下午", "傍晚", "晚上", "夜里", "半夜",
         "凌晨", "早", "晚")
_PM = ("下午", "晚上", "傍晚", "夜里", "半夜", "晚")

_CLOCK_RE = re.compile(
    r"(?P<h>\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*[:：点]\s*"
    # ★ 分钟部分：阿拉伯数字随便认；**中文数字必须在词边界上**。
    #   不然「九点一起打游戏」会把「一」当成 1 分 —— 变成 21:01。
    r"(?P<mi>\d{1,2}|[零一二两三四五六七八九十]{1,3}"
    r"(?=$|[\s,，。、!！?？;；~～]|半|分|个|钟))?\s*"
    r"(?P<half>半)?")
# 「半个小时后」「20 分钟后」「过两个小时」（「过…」那种没有「后」）
_REL_RE = re.compile(
    r"(?:(?:过|再过)\s*(?P<n>\d{1,3}|[零一二两三四五六七八九十]{1,3})\s*"
    r"(?P<unit>分钟|分|个小时|小时|钟头|天))"
    r"|(?:(?P<n2>\d{1,3}|[零一二两三四五六七八九十]{1,3}|半)\s*"
    r"(?P<unit2>分钟|分|个小时|小时|钟头|天)\s*(?:之?后|以后|后))")


def _day_offset(text):
    """句子里有没有「明天 / 明早 / 今晚」这种日子的说法。返回天数或 None。"""
    for w, off in _DAY_WORDS:
        if w in text:
            return off
    return None


def resolve_time(text, now, default_clock=None):
    """从一句话里算出「那件事该在什么时候做」。

    返回 datetime 或 None。解析不出来就 None（宁可不记，也别记错时间）。

    认这几种：
        「20 分钟后」「半个小时后」「两小时后」
        「明天早上七点半」「七点」「7:30」「八点半」
        「明早」「明天早上」（没钟点 → 用 default_clock，起床类给 07:30）
    """
    # ① 相对时间
    m = _REL_RE.search(text)
    if m:
        raw = m.group("n") or m.group("n2")
        val = 0.5 if raw == "半" else _num(raw)
        unit = m.group("unit") or m.group("unit2") or ""
        if val:
            if unit.startswith("分"):
                return now + dt.timedelta(minutes=val)
            if unit == "天":
                return now + dt.timedelta(days=val)
            return now + dt.timedelta(hours=val)

    # ② 钟点
    m = _CLOCK_RE.search(text)
    off = _day_offset(text)
    if m:
        h = _num(m.group("h"))
        mi = _num(m.group("mi")) or 0
        if m.group("half"):
            mi = 30
        if h is None:
            return None
        # 时段词可能在钟点前面，也可能在后面（「晚上七点」/「七点晚上」都会认）
        win = text[max(0, m.start() - 6): m.end() + 4]
        if any(w in win for w in _PM) and h < 12:
            h += 12
        if "凌晨" in win and h >= 12:
            h -= 12
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        base = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if off is None:
            # 没日子：这个点今天已经过了（留 5 分钟余量）就顺到明天
            if base <= now - dt.timedelta(minutes=5):
                base += dt.timedelta(days=1)
            return base
        return (now + dt.timedelta(days=off)).replace(
            hour=h, minute=mi, second=0, microsecond=0)

    # ③ 只有日子，没有钟点 —— 只有带默认钟点时才认（起床类）
    if off is not None and default_clock:
        h, mi = default_clock
        return (now + dt.timedelta(days=off)).replace(
            hour=h, minute=mi, second=0, microsecond=0)
    return None


# ══════════════════════════════════════════════════════════════════════════
#  二、认句子
# ══════════════════════════════════════════════════════════════════════════

# 他求她做的事。
#   ★ 分两档，因为「叫我」这两个字太泛：
#       「叫我起床 / 叫醒我 / 叫我起来」= 强信号，一定是起床；
#       「叫我 / 喊我」= 弱信号 —— 只有配上「早上」或者一个早晨的钟点
#       （5~11 点）才算起床，否则当成「到点叫我一声」（remind）。
#       不然「晚上八点叫我一声」会被记成「叫他起床」，很傻。
_WAKE_STRONG_RE = re.compile(
    r"叫我起床|喊我起床|叫醒我|喊醒我|叫我起来|喊我起来|叫我醒")
_WAKE_WEAK_RE = re.compile(r"叫我|喊我")
_REMIND_RE = re.compile(
    r"提醒我|提醒一下我|到时提醒|记得提醒我|到点提醒|叫我提醒")
_MORNING_WORDS = ("早上", "早上", "明早", "今早", "凌晨", "起床", "早")
_MORNING_HOURS = (5, 6, 7, 8, 9, 10, 11)

# 起床类没有钟点时的默认时间（她作息里 07:30 起，他那边时间跟她一样）
WAKE_DEFAULT = (7, 30)

# 她自己说的「我去干嘛，待会回来」
#
#   四元组 =（命中词、大约多久、到点什么话、**她去做的那件事**）。
#   最后那个 `doing` 是 2026-09-22 补的：光有个 due 只是个闹钟，
#   到点她凭空冒一句「吃完了」，中间那 40 分钟是不存在的。
#   有了 doing，登记时就能把「她去食堂买了点东西」压进她今天的时间轴
#   （见 bridge._note_promises → life.divert），到点回来才有过程可讲。
#
#   ★ 第五项 `domain`（2026-09-23 补）是「一致性自检」的键（见 reconcile）：
#     同一类事在账本里只该挂**一条**，而且她已经做完的那一刻就该勾掉。
#     治的是 9-22 晚那串「左右脑互搏」：
#         18:21 她说「我眯一会儿」   → 记下一条 19:30「睡醒了跟他说一声」
#         19:08 她自己先说了「我刚眯了会儿」   ← 这件事其实已经兑现了
#         19:21 她又说「要去上课了」           ← 更不用说，人早醒了、都要走了
#         19:30 那条照旧到点开口「刚睡醒」     ← 屏幕上就是自相矛盾
#     有了 domain，19:08 那一句一进来就把「睡」这一类挂着的账当场勾掉，
#     19:30 什么都不会再开口。
_HER_TODO = [
    ("洗澡", 30, "洗完澡出来跟他说一声", "去洗澡", "澡"),
    ("冲个澡", 30, "洗完澡出来跟他说一声", "去冲个澡", "澡"),
    ("去吃饭", 40, "吃完饭回来跟他说一声", "去吃饭", "饭"),
    ("吃饭去", 40, "吃完饭回来跟他说一声", "去吃饭", "饭"),
    ("吃点东西", 40, "吃完回来跟他说一声", "去吃点东西", "饭"),
    ("睡一会", 70, "睡醒了跟他说一声", "去睡一会儿", "睡"),
    ("眯一会", 70, "睡醒了跟他说一声", "去眯一会儿", "睡"),
    ("上课去", 50, "下课了跟他说一声", "去上课", "课"),
    ("去上课", 50, "下课了跟他说一声", "去上课", "课"),
    ("写作业", 40, "忙完跟他说一声", "去写作业", "作业"),
    ("做作业", 40, "忙完跟他说一声", "去做作业", "作业"),
]

# ── 「这件事她已经做完了」的说法（2026-09-23 补）────────────────────────
#
#  她随口报一句「我刚眯了会儿」「下课了」「吃完了」，那就是**这件事办完了**。
#  从前没人接这句话：账本里那条定时器照旧挂着，到点还要再开一次口，
#  于是同一件事被她说两遍，而第二遍跟当时的事实对不上。
#
#  这是一张**朴素词表**，和上面那张一样，一个字都不问模型 ——
#  它只决定「勾掉哪笔账」，不决定她说什么，判错也只是少记/多记一笔。
_HER_DONE = (
    ("睡", re.compile(r"刚(醒|起)|醒(了|过来)|睡醒|起了床|起床了|"
                      r"眯了(一)?会儿|睡了(一)?会儿|睡了一觉|睡饱")),
    ("课", re.compile(r"下课|上完了|上完课|课上完|刚回来|回到(宿舍|寝室)|"
                      r"回(宿舍|寝室)|往回走|早退|翘课|翘了课")),
    ("饭", re.compile(r"吃完|吃过了|吃饱|吃好了|刚吃过")),
    ("澡", re.compile(r"洗完|洗好了|冲完了|洗完了")),
    ("作业", re.compile(r"写完了|做完了|忙完了|交完了")),
)

# 命中词前面紧跟着这些字 = 还没做完（「还没醒」「马上就去上课」），别当已经做完
_NOT_YET_RE = re.compile(r"(还|没|不|别|快|马上|待|等下|等会|一会)$")


def _done_domains(text: str) -> set:
    """这一句里，她报了**已经做完**的那些类。返回集合，空集合表示没说什么。"""
    if not text:
        return set()
    out = set()
    for dom, rx in _HER_DONE:
        for m in rx.finditer(text):
            # 「还没醒」「马上就去上课」—— 前面带这些字就不是「做完了」
            if _NOT_YET_RE.search(text[max(0, m.start() - 3):m.start()]):
                continue
            # ★ 还得是**她**做完的：「你刚醒啊」不算她自己醒了
            #   （和 parse_hers 用的是同一套主语判定，见 _is_about_her）
            if not _is_about_her(text, m.start()):
                continue
            out.add(dom)
            break
    return out


def _domain_of(it: dict) -> str:
    """这条待办属于哪一类（睡 / 课 / 饭 / 澡 / 作业）。认不出返回空串。

    新账本里有 `domain` 字段；老账本（手改过、或者从旧版本带上来的）没有，
    就从 `doing` 或那句「到点什么话」反推 —— 别让历史账本失灵。
    """
    dom = (it or {}).get("domain") or ""
    if dom:
        return dom
    doing = (it or {}).get("doing") or ""
    if doing:
        for _w, _m, _n, d2, dom2 in _HER_TODO:
            if d2 == doing:
                return dom2
    txt = (it or {}).get("text") or ""
    for _w, _m, note, _d, dom2 in _HER_TODO:
        if note == txt:
            return dom2
    return ""

# ★ 「是谁」这件事，2026-09-22 补 ─────────────────────────────────────
#   实测那天她自己发来：「报告比上次好点 别担心 / 你上班辛苦 有空吃点东西」。
#   词表照着「吃点东西」记下一条「她自己答应吃完回来说一声」，
#   40 分钟后准准地开口说「吃完了 报告单也拿了」—— 可那句是在**让他**去吃点东西，
#   而且那会儿她还在上课。人不会替别人许这种愿，所以命中之后得看主语：
#     词前面最近的一个代词是「我 / 咱」 → 说的是她自己，认；
#     是「你 / 他 / 她 / 大家…」      → 说的是别人，**不认**；
#     一个代词都没有（「先去洗澡了」）→ 照旧认，别把功能掐死。
_PRON_RE = re.compile(
    r"你们|他们|她们|咱们|我们|人家|别人|大家|自己|咱|我|你|您|他|她")
# 切句读用（只看标点，**不切空格** ——「你上班辛苦 有空吃点东西」中间那个
# 空格两边还是同一句话，切开了就看不见「你」了）
_CLAUSE_RE = re.compile(r"[。！？!?；;，,、\n\r]+")


def _is_about_her(text, pos):
    """text[pos] 开始的那个词，说的是她自己吗？

    看它前面最近的一个代词：
      「我 / 咱 / 自己」开头 → 她；「你 / 您 / 他 / 她 / 人家 / 大家」开头 → 别人。
    前面压根没有代词 → 算她的（「先去洗澡了」这种本来就没有主语）。
    """
    head = text[max(0, pos - 12):pos]
    hits = list(_PRON_RE.finditer(head))
    if not hits:
        return True
    first = hits[-1].group(0)[0]
    return first in ("我", "咱", "自")


def _clause_at(text, pos):
    """取 pos 所在的那一小句 —— 存 raw 用。

    原来 raw 存的是**她这一轮的整段话**，于是「报告比上次好点 别担心」
    被一起卷进待办，每轮又被 `prompt_block` 引一遍，她就照着复读了三次。
    现在只留命中词所在的那一句。
    """
    start = 0
    for m in _CLAUSE_RE.finditer(text):
        if m.end() > pos:
            break
        start = m.end()
    m = _CLAUSE_RE.search(text, pos)
    end = m.start() if m else len(text)
    return text[start:end].strip()
# 「待会再聊」这类 —— 必须带「聊 / 说 / 回来 / 找我」，不然
# 「等下要去上课」这种将来时也会被误认成「我马上回来」。
_HER_BACK_RE = re.compile(
    r"(待会|待会儿|一会儿|等一会|过一会|晚点|回头)"
    r"[^。！？]{0,5}(再?聊|再?说|再?讲|再?找他|回来|找你|找你聊)"
    r"|等我(一下|一会|会儿|几分钟|回来|哈)?"
    r"|我(马上|这就|一会儿)回来"
    r"|去去就回")
_HER_BACK_DELAY = 20

# ★ 她说的是「还没动身」还是「已经走了」（2026-09-22）
#   「我**待会儿**也去吃饭了」—— 说这话时人还在教室；
#   「我**先**去洗澡了」—— 说完就走了。
#   只影响一件事：bridge 把「她去做事」那一段压进今天的时间轴时，
#   段是从**现在**开始（已经走了），还是快到时才开始（还没动身）。
#   不区分的话，她 14:25 说「待会去吃饭」，14:26 他问「你在上课吗」，
#   她的表上却已经写着「在食堂」—— 又是一处对不上的地方。
_SOON_RE = re.compile(
    r"待会|待会儿|等下|等会儿|一会儿|一会|晚点|马上|这就|回头|过会")


def _mk(kind, due, text, who, raw, now, doing="", domain=""):
    rec = {
        "id": None,
        "kind": kind,
        "due": due.strftime("%Y-%m-%dT%H:%M") if due else "",
        "text": text,
        "who": who,
        "raw": (raw or "")[:60],
        "made": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "done": False,
        "fired": "",
    }
    if doing:
        # 她去做的那件事 —— bridge 拿它压进她今天的时间轴（divert）
        rec["doing"] = doing
    if domain:
        # 属于哪一类 —— 一致性自检靠它认「同一件事」（见 reconcile）
        rec["domain"] = domain
    return rec


def parse_his(text, now=None):
    """他发来的一句话 → 她欠他的一件事 / 一个约（0~1 条）。

    「明天早上七点半叫我起床」 → wake, 明天 07:30, 「叫他起床」
    「七点叫我」             → wake, 今天(或明天) 07:00
    「晚上八点叫我一声」      → remind, 明天 20:00（不是起床，是到点叫他）
    「明天提醒我拿报告」      → remind, 没钟点 → 悬着的（due 空）
    「晚上六点去和西西吃饭」   → appoint, 今天 18:00（他定的约，占她两小时）
    「好的」                 → 不认
    """
    now = now or dt.datetime.now()
    if not text:
        return []

    if _REMIND_RE.search(text):
        due = resolve_time(text, now)
        return [_mk("remind", due, "提醒他", "他", text, now)]

    strong = bool(_WAKE_STRONG_RE.search(text))
    has_wake = strong or bool(_WAKE_WEAK_RE.search(text))

    if has_wake:
        due = resolve_time(text, now)
        if due is None:
            # 没钟点。弱信号（光「叫我」）得再有一点线索才认：
            #   要么提了早上，要么提了哪天（「明天叫我」= 明早叫我起床）。
            #   什么都没有的「叫我一声」不知道是干嘛，不记。
            if not strong and not any(w in text for w in _MORNING_WORDS) \
                    and _day_offset(text) is None:
                return []
            h, mi = WAKE_DEFAULT
            off = _day_offset(text)
            if off is not None:
                due = (now + dt.timedelta(days=off)).replace(
                    hour=h, minute=mi, second=0, microsecond=0)
            else:
                due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                if due <= now - dt.timedelta(minutes=5):
                    due += dt.timedelta(days=1)

        # 有钟点但不是早晨那一段（「晚上八点叫我一声」）—— 那是到点叫他，
        # 不是叫他起床。强信号（带「起床 / 起来 / 叫醒」）不受这条限制。
        kind = "wake"
        if not strong and not any(w in text for w in _MORNING_WORDS) \
                and due.hour not in _MORNING_HOURS:
            kind = "remind"
        got = "叫他起床" if kind == "wake" else "到点叫他一声"
        return [_mk(kind, due, got, "他", text, now)]

    # 既不是叫她起床、也不是叫她提醒 —— 看看是不是「他定了个约」
    ap = _parse_appoint(text, now)
    return [ap] if ap else []


def _parse_appoint(text, now):
    """他随口定下的一个约 —— 「晚上六点去和西西吃饭」。

    认出返回一条 appoint，认不出返回 None。宁可不认，也别把随口一句
    （「明天上课吗」「你晚上六点干嘛」）当成日程写进她的一天。
    """
    if re.search(r"[?？]", text):                 # 问句不是约定
        return None
    if _APPOINT_NEG_RE.search(text):              # 反悔了
        return None
    if not any(w in text for w in _APPOINT_WORDS):
        return None
    if not (_CLOCK_RE.search(text) or _REL_RE.search(text)):
        return None
    due = resolve_time(text, now)
    if due is None:
        return None
    if due <= now + dt.timedelta(minutes=20):     # 已经过去 / 马上到，不算定约
        return None

    act = ""
    m = _CLOCK_RE.search(text)
    if m:
        act = text[m.end():]
    # 「七点十五分陪我去图书馆」—— 那个「分」在 lookahead 里没被吃掉
    act = re.sub(r"^(分|点钟|点|个钟|钟头|个?小时)", "", act)
    act = re.sub(r"^[\s,，。、:：;；]*", "", act)
    act = re.sub(r"^(我们|一起|一块儿?|你|我|俩|咱)", "", act)
    act = act.strip(" \u3000,，。、!！~;；")
    if len(act) < 2:
        # 钟点后面没东西（「六点去」这种）—— 把整句的时间词抠掉
        act = text
        for w, _off in _DAY_WORDS:
            act = act.replace(w, "")
        act = _CLOCK_RE.sub("", act)
        act = _REL_RE.sub("", act)
        act = re.sub(r"^[\s,，。、:：;；]*(跟我说|和我说|说)", "", act)
        act = act.strip(" \u3000,，。、!！~;；")
    if len(act) < 2:
        return None

    rec = _mk("appoint", due, act[:30], "他", text, now)
    rec["dur"] = APPOINT_DEFAULT_MIN
    return rec


def parse_hers(text, now=None):
    """她自己发出去的一句话 → 她欠他的一句话（0~1 条）。

    「我先去洗澡 待会再聊」 → comeback, 30 分钟后
    「等我一下」            → comeback, 20 分钟后
    「等下要去上课」        → 不认（那是将来时，不是「我马上回来」）
    「你上班辛苦 有空吃点东西」→ 不认（那是**让哥去**吃，不是她要去）

    ★ 两个「不认」的区别：最后那条不是时间认不出，是**主语不是她**。
      这条是 2026-09-22 补的 —— 那天她的一句话引出过一次「到点了」，
      他莫名其妙，因为那本来不构成任何约定。
    """
    now = now or dt.datetime.now()
    if not text:
        return []
    for word, mins, note, doing, dom in _HER_TODO:
        pos = text.find(word)
        if pos < 0:
            continue
        if not _is_about_her(text, pos):
            continue                    # 说的是别人，不是她要去做
        rec = _mk("comeback", now + dt.timedelta(minutes=mins),
                  note, "她", _clause_at(text, pos), now, doing=doing,
                  domain=dom)
        rec["soon"] = bool(_SOON_RE.search(text))
        return [rec]
    if _HER_BACK_RE.search(text):
        return [_mk("comeback", now + dt.timedelta(minutes=_HER_BACK_DELAY),
                    "刚才说待会再聊，现在回来跟他说一声", "她", text, now,
                    doing="")]
    return []


def parse(text, who, now=None):
    """who: "他" = 他发来的话；"她" = 她自己发出去的话。"""
    now = now or dt.datetime.now()
    return parse_his(text, now) if who == "他" else parse_hers(text, now)


# ══════════════════════════════════════════════════════════════════════════
#  三、账本
# ══════════════════════════════════════════════════════════════════════════

def _load_at(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("seq", 0)
            d.setdefault("items", [])
            return d
    except Exception:
        pass
    return {"seq": 0, "items": []}


def _save_at(path, d):
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except Exception:
        pass


def _load():
    return _load_at(FILE)


def _save(d):
    _save_at(FILE, d)


def _parse_due(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M")
    except Exception:
        return None


def add(kind, due, text, who="她", raw="", now=None, d=None, dur=None,
        doing="", soon=False, domain=""):
    """记一条。返回那条记录。

    ★ 同一件事不重复记：他连着说两遍「明天叫我起床」，只留一条；
      她自己连着两轮说「待会再聊」，也是刷新那条的时间，不叠。
    """
    now = now or dt.datetime.now()
    d = d or _load()
    items = d["items"]
    for it in items:
        if it.get("done") or it.get("kind") != kind:
            continue
        if it.get("text") != text:
            continue
        # 同一件事 —— 刷新时间，不新增
        if due:
            it["due"] = due.strftime("%Y-%m-%dT%H:%M")
        if dur:
            it["dur"] = int(dur)
        if doing:
            it["doing"] = doing
        if domain:
            it["domain"] = domain
        it["soon"] = bool(soon)
        if who == "他":
            it["raw"] = (raw or it.get("raw", ""))[:60]
        _save(d)
        return it
    d["seq"] = int(d.get("seq", 0)) + 1
    rec = {
        "id": d["seq"], "kind": kind,
        "due": due.strftime("%Y-%m-%dT%H:%M") if due else "",
        "text": text, "who": who, "raw": (raw or "")[:60],
        "made": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "done": False, "fired": "",
    }
    if kind in SCHEDULE_KINDS:
        rec["dur"] = int(dur or APPOINT_DEFAULT_MIN)
    if doing:
        rec["doing"] = doing
    if domain:
        rec["domain"] = domain
    if soon:
        rec["soon"] = True
    items.append(rec)
    items[:] = items[-40:]
    _save(d)
    return rec


def feed(text, who, now=None):
    """收一句话，该记就记。返回记下的那些条（给日志用）。

    ★ 这里**只记账**。她那一句里要是有「已经做完了」，把旧账勾掉、
      把作废的计划从她今天的表上抹掉 —— 那是**自检**的活（settle +
      reconcile），由 bridge._audit_state 每轮统一跑一遍。
      两件事分开，是为了让「记账」可重放、不依赖调用顺序。
    """
    now = now or dt.datetime.now()
    got = parse(text, who, now)
    recs = []
    for g in got:
        due = _parse_due(g["due"]) if g["due"] else None
        if due and due <= now and g["kind"] not in SCHEDULE_KINDS:
            continue                    # 说的时间已经过去了，没意义
        recs.append(add(g["kind"], due, g["text"], who=who, raw=g["raw"],
                        now=now, dur=g.get("dur"), doing=g.get("doing", ""),
                        soon=bool(g.get("soon")),
                        domain=g.get("domain", "")))
    return recs


# ══════════════════════════════════════════════════════════════════════════
#  三之二、一致性自检
#
#  「她说过的话要算数」这件事的另一半是：**说过的话不能互相打架。**
#
#  9-22 晚 19:08 ~ 19:30 那三十来分钟，屏幕上依次是：
#      19:08  我刚眯了会儿 现在还有点晕        ← 她自己说了，人已经醒了
#      19:21  要去上课了                        ← 人醒了、收拾好了、要走了
#      19:22  刚下课 正往宿舍走                  ← 提前 50 分钟报了下课
#      19:30  刚睡醒 你那边游戏好玩吗            ← 那条 18:21 记下的定时器到点了
#
#  三处病根，一条一条治：
#    ① 她报了「已经做完」→ 账还挂着（→ settle）
#    ② 同一类事挂着两条，旧的那条照旧开火（→ reconcile 的「同类留最新」）
#    ③ 还没到点的条目被写进提示词的「你还欠着」，她读成「现在就该说」
#       → 19:22 那句「刚下课」（→ prompt_block 分成两段）
# ══════════════════════════════════════════════════════════════════════════

def settle(text, now=None, d=None):
    """她这一句里报了「已经做完了」→ 同一类里还挂着的待办当场勾掉。

    返回被勾掉的那些条（bridge 拿它们把「之后那份已经作废的计划」也撤掉）。

    ★ 勾掉 ≠ 作废：这叫**兑现**。她把「睡醒了跟他说一声」自己先还上了，
      到点就不该再开口补一遍「刚睡醒」。
    """
    doms = _done_domains(text)
    if not doms:
        return []
    now = now or dt.datetime.now()
    d = d or _load()
    hit = []
    for it in d["items"]:
        if it.get("done") or it.get("kind") in SCHEDULE_KINDS:
            continue
        if _domain_of(it) not in doms:
            continue
        it["done"] = True
        it["fired"] = "她自己先说了"
        hit.append(it)
    if hit:
        _save(d)
    return hit


def _newer_key(it: dict):
    """哪条更新 —— 先看「什么时候说出口的」，同秒再看 id。"""
    return (str(it.get("made") or ""), int(it.get("id") or 0))


def reconcile(now=None, d=None):
    """★ 一致性自检：把互相打架的待办抹掉。返回被抹掉的那些条。

    同一类事在账本里只该挂一条 —— 她一天里能睡两次、上两次课，
    但**同时挂着两条**只会让她前后矛盾。做两件事：

      ① 同类的留**最新**那一条，旧的记成「被后来的话顶掉了」；
      ② 到点超过宽限的作废（和 `due_items` 同一把尺：DUE_GRACE_MIN）。

    ★ 只动 `comeback`（她自己说要去做的那种）。他定的约（appoint）
      和「他求她的事」（wake / remind）不碰 —— 那些是**他**说出口的，
      不能因为她又说了句别的就没了。
    """
    now = now or dt.datetime.now()
    d = d or _load()
    superseded, expired = [], []

    newest = {}
    for it in d["items"]:
        if it.get("done") or it.get("kind") in SCHEDULE_KINDS:
            continue
        dom = _domain_of(it)
        if not dom:
            continue
        old = newest.get(dom)
        if old is None or _newer_key(it) > _newer_key(old):
            if old is not None:
                superseded.append(old)
            newest[dom] = it
        else:
            superseded.append(it)

    for it in d["items"]:
        if it.get("done") or it.get("kind") in SCHEDULE_KINDS:
            continue
        due = _parse_due(it.get("due") or "")
        if due is None:
            continue
        if (now - due) > dt.timedelta(minutes=DUE_GRACE_MIN):
            expired.append(it)

    out, seen = [], set()
    for it in superseded + expired:
        if id(it) in seen:
            continue
        seen.add(id(it))
        it["done"] = True
        it["fired"] = ("被后来的话顶掉了" if it in superseded else "过期作废")
        out.append(it)
    if out:
        _save(d)
    return out


def due_items(now=None):
    """此刻到点该发的。到点后超过 DUE_GRACE_MIN 的顺手作废。

    返回 [(item, due_datetime)]，按到点时间排 —— 先欠的先还。

    ★ 「他定的约」（appoint）**不算**「待还的事」—— 那是日程，
      不靠她开口来还（进她的时间轴就行，见 daily_life.Life.diverts_today）。
      把它排除掉，也顺带保证了它不会被 mark_fired 标记成 done 而从日程里消失。

    ★ 开口之前先跑一遍一致性自检（reconcile，2026-09-23）：
      她已经自己做过/说过的事、被后来的话顶掉的旧账，到点就不该再开口。
      主循环那边每轮也会跑一次，这里是**第二道** —— 万一进程刚起、
      或者两轮之间隔了很久，也不至于把一句作废的话补说出去。
    """
    now = now or dt.datetime.now()
    try:
        reconcile(now)
    except Exception:
        pass
    d = _load()
    out = []
    touched = False
    for it in d["items"]:
        if it.get("done") or it.get("kind") in SCHEDULE_KINDS:
            continue
        due = _parse_due(it.get("due") or "")
        if due is None:
            continue
        if due > now:
            continue
        if (now - due) > dt.timedelta(minutes=DUE_GRACE_MIN):
            it["done"] = True
            it["fired"] = "过期作废"
            touched = True
            continue
        out.append((it, due))
    if touched:
        _save(d)
    out.sort(key=lambda x: x[1])
    return out


def appointments_on(day=None, path=None):
    """他定的约里，落在 day 这一天的 —— 返回 [(起, 止, 文本, item)]。

    起止是「从 0 点算起的分钟数」，喂给 daily_life 压到时间轴上。
    ★ 只看**没被删掉**的（done 的都是他自己取消的）——
      每天重排日程、--refresh、--undo 都动不了这里，因为这是另一个文件。
    """
    day = day or dt.date.today()
    d = _load_at(path or FILE)
    out = []
    for it in d["items"]:
        if it.get("kind") not in SCHEDULE_KINDS or it.get("done"):
            continue
        due = _parse_due(it.get("due") or "")
        if due is None or due.date() != day:
            continue
        a = due.hour * 60 + due.minute
        dur = int(it.get("dur") or APPOINT_DEFAULT_MIN)
        dur = max(15, min(dur, 8 * 60))
        b = min(a + dur, 29 * 60)
        if b <= a:
            continue
        out.append((a, b, it.get("text") or "他定的事", it))
    out.sort(key=lambda x: x[0])
    return out


def mark_fired(item_id, note=""):
    d = _load()
    for it in d["items"]:
        if it.get("id") == item_id:
            it["done"] = True
            # ★ 已经写过的理由（「她自己先说了」/「被后来的话顶掉了」）留着 ——
            #   别让「20:11 开了一次口」把「这条其实早就兑现了」盖掉。
            it["fired"] = it.get("fired") or \
                (note or dt.datetime.now().strftime("%H:%M"))[:40]
            _save(d)
            return True
    return False


def drop(item_id):
    d = _load()
    n = len(d["items"])
    d["items"] = [it for it in d["items"] if it.get("id") != item_id]
    _save(d)
    return n - len(d["items"])


def clear():
    d = _load()
    n = len(d["items"])
    d["items"] = []
    _save(d)
    return n


# ── 给提示词用 ────────────────────────────────────────────────────────────

def prompt_block(now=None, maxn=4):
    """她答应过、还欠着的事 —— 拼进系统提示（没有就返回空串）。

    两段：
      · 到点该做的（带钟点）：提醒她这是她自己答应的，别忘了。
      · 悬着的（没钟点）：不影响这一轮，但别否认自己答应过。

    ★ 故意写得短。它的作用是「她心里有事」，不是给她一份任务清单。
    """
    now = now or dt.datetime.now()
    d = _load()
    today = now.date()
    due_now, later, owe = [], [], []
    seen_dom = set()
    for it in d["items"]:
        if it.get("done"):
            continue
        due = _parse_due(it.get("due") or "")
        if due is None:
            owe.append(it)
            continue
        # 只看今天到明天早上的，再远的不必占着提示
        if due.date() > today + dt.timedelta(days=1):
            continue
        # ★ 同一类事只报一条（reconcile 之后本来就只该剩一条，
        #   这里再兜一道，免得手改过的账本把两条一起念出来）
        dom = _domain_of(it)
        if dom:
            if dom in seen_dom:
                continue
            seen_dom.add(dom)
        when = ("今天 " if due.date() == today else
                "明天 " if due.date() == today + dt.timedelta(days=1) else "")
        # 是谁先说的，写清楚 —— 他求的（叫他起床）和她自己说的（待会再聊）
        # 在她心里不是一回事。「他定的约」又是第三种：那是日程。
        if it.get("kind") == "appoint":
            src = "（**他跟你定好的**，到时候别忘了）"
        elif it.get("who") == "他" and it.get("raw"):
            src = "（他求你的：「%s」）" % it["raw"]
        elif it.get("who") == "她":
            # ★ 这里**故意不引她的原话**（2026-09-22）。
            #   原来写的是 「（你自己说的：「%s」）」 % raw —— 而 raw 是她那轮
            #   说过的句子。她每轮读到，就照着复读一遍：「比上次好点 别担心」
            #   一天说了三回，中间还夹一个「报告单也拿了」。
            #   她答应的是「吃完饭回来说一声」—— 那就只留这一句，别把当时
            #   那句寒暄再摆回她眼前。
            src = "（你自己说的）"
        else:
            src = ""
        line = "  · %s%s  %s%s" % (when, due.strftime("%H:%M"),
                                  it["text"], src)
        if due <= now + dt.timedelta(minutes=_HINT_LEAD_MIN):
            due_now.append((due, line))
        else:
            later.append((due, line))
    if not due_now and not later and not owe:
        return ""
    out = []
    if due_now:
        out.append("# 你答应过他的事（**到点了**）")
        for _due, s in sorted(due_now)[:maxn]:
            out.append(s)
    if later:
        out.append("# 你答应过他的事（还没到点 —— **别提前说**）")
        for _due, s in sorted(later)[:maxn]:
            out.append(s)
    for it in owe[:2]:
        # ★ 悬着的那种（他没给钟点）也要说清**求的是什么**（2026-09-22）：
        #   光写「提醒他」，她根本不知道要提醒啥，等于没记。
        _src = ("（他求你的：「%s」）" % it["raw"]
                if it.get("who") == "他" and it.get("raw") else "")
        out.append("  · %s%s（没定时间，你自己看着办）" % (it["text"], _src))
    out.append("（答应过的事要算数 —— 到点了你自己会先开口，不用等他问。"
               "这不是任务清单，别拿出来念；**还没到点的那几条更不许提前报**，"
               "现在就说会跟你眼下正在做的事对不上。）")
    return "\n".join(out)


def seed_for(item):
    """把一条待办变成「她主动开口」的引子（bridge.Brain.proactive 吃这个）。

    urgent=True 的（他求她的事：叫我起床 / 提醒我）—— 到点就得开口，
    内容就是这件事本身。
    urgent=False 的（她自己说「待会回来」）—— 是「她回来了，说一声」，
    可以顺着刚才的话题往下聊。
    """
    if not item:
        return None
    due = _parse_due(item.get("due") or "")
    try:
        made = dt.datetime.strptime(item.get("made") or "", "%Y-%m-%dT%H:%M:%S")
    except Exception:
        made = None
    return {
        "text": item.get("text", ""),
        "kind": "promise",
        "item": item.get("id"),
        "when": due.strftime("%H:%M") if due else "",
        "raw": item.get("raw", ""),
        # 她去做的那件事 + 什么时候出发的 —— bridge 拿这两样拼
        # 「你刚去食堂买了点东西，现在回来了」，而不是干巴巴一句「吃完了」。
        "doing": item.get("doing", ""),
        "since": made.strftime("%H:%M") if made else "",
        # urgent 的意思是「有准点、到点就得开口」——
        # 悬着的那种（他没给时间）不冒充闹钟，可以顺着话头说。
        "urgent": bool(due) and item.get("kind") in ("wake", "remind"),
    }


def seed(now=None):
    """挑一条给「她主动找他」当引子用（命令行/试生成用）。

    优先级：到点的 > 悬着的；到点的里面，「他求她的事」优先 ——
    那是有明确时间的承诺，比「忽然想起今天买了个蛋糕」要紧得多。
    """
    now = now or dt.datetime.now()
    fired = due_items(now)
    if fired:
        for it, _due in fired:
            if it.get("kind") in ("wake", "remind"):
                return seed_for(it)
        return seed_for(fired[0][0])
    d = _load()
    for it in d["items"]:
        if not it.get("done") and not it.get("due"):
            return seed_for(it)
    return None


def status(now=None):
    """控制台 / 命令行看的一眼总结。"""
    now = now or dt.datetime.now()
    d = _load()
    items = [it for it in d["items"] if not it.get("done")]
    return {
        "open": len(items),
        "due": len(due_items(now)),
        "items": items,
        "file": str(FILE),
    }


# ══════════════════════════════════════════════════════════════════════════
#  四、命令行
# ══════════════════════════════════════════════════════════════════════════

_KIND_CN = {"wake": "叫他起床", "remind": "提醒他", "comeback": "说好回来的",
            "appoint": "跟你定好的事", "owe": "悬着的"}

_FIRED_CN = {"她自己先说了": "她自己先说了（这件事已经兑现，别再补说一遍）",
             "被后来的话顶掉了": "被后来的话顶掉了",
             "过期作废": "过期作废"}


def _show_parse(what, got, raw):
    print("%s：「%s」" % (what, raw))
    if not got:
        print("  → 认不出来（不记）")
        return
    g = got[0]
    due = g.get("due") or ""
    print("  → 记成「%s」（%s），到点 %s"
          % (g["text"], _KIND_CN.get(g["kind"], g["kind"]),
             due.replace("T", " ") if due else "——（没定时间，悬着）"))


def _cli():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8",
                               errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    now = dt.datetime.now()

    if "--parse" in args:
        i = args.index("--parse")
        text = args[i + 1] if len(args) > i + 1 else ""
        _show_parse("他发的", parse_his(text, now), text)
        return
    if "--parse-her" in args:
        i = args.index("--parse-her")
        text = args[i + 1] if len(args) > i + 1 else ""
        _show_parse("她发的", parse_hers(text, now), text)
        return
    if "--add" in args:
        i = args.index("--add")
        raw = args[i + 1] if len(args) > i + 1 else ""
        got = parse_his(raw, now)
        if not got:
            print("认不出这句话，没记：%s" % raw)
            return
        g = got[0]
        due = _parse_due(g["due"]) if g["due"] else None
        rec = add(g["kind"], due, g["text"], who="他", raw=raw, now=now)
        print("记下了：", rec)
        return
    if "--done" in args:
        i = args.index("--done")
        try:
            iid = int(args[i + 1])
        except (IndexError, ValueError):
            print("用法：--done 3")
            return
        print("勾掉了" if mark_fired(iid, "手动") else "没找到这条")
        return
    if "--drop" in args:
        # 他定的约只能他自己取消 —— 这就是那个「自己主动要求删除」
        i = args.index("--drop")
        try:
            iid = int(args[i + 1])
        except (IndexError, ValueError):
            print("用法：--drop 3    （把他定的那条约删掉）")
            return
        print("删掉了" if drop(iid) else "没找到这条")
        return
    if "--clear" in args:
        print("清了 %d 条" % clear())
        return
    if "--check" in args or "--audit" in args:
        # 手动跑一遍一致性自检：她那些「待会」里，有没有已经互相打架的
        killed = reconcile(now)
        print("=" * 56)
        print("  一致性自检：抹掉 %d 条互相打架的待办" % len(killed))
        print("=" * 56)
        if not killed:
            print("  （没有 —— 她现在说的和欠着的对得上）")
        for it in killed:
            print("  [%s] %s  %s"
                  % (it["id"], it["text"],
                     _FIRED_CN.get(it.get("fired"), it.get("fired") or "")))
            if it.get("raw"):
                print("        起因：%s" % it["raw"])
        rest = status(now)
        print("")
        print("  剩下还挂着的 %d 条（此刻到点 %d 条）"
              % (rest["open"], rest["due"]))
        return
    if "--due" in args:
        got = due_items(now)
        print("此刻到点的 %d 条：" % len(got))
        for it, due in got:
            print("  [%s] %s  %s  %s"
                  % (it["id"], due.strftime("%m-%d %H:%M"),
                     _KIND_CN.get(it["kind"], it["kind"]), it["text"]))
        return

    st = status(now)
    print("=" * 56)
    print("  她欠着的 %d 条（此刻到点 %d 条）" % (st["open"], st["due"]))
    print("=" * 56)
    if not st["open"]:
        print("  （没有 —— 她说的话都还上了）")
    for it in st["items"]:
        due = it.get("due") or "——"
        extra = ""
        if it.get("kind") == "appoint":
            extra = "（占她 %d 分钟）" % int(it.get("dur") or APPOINT_DEFAULT_MIN)
        print("  [%s] %s  %s  %s%s"
              % (it["id"], due, _KIND_CN.get(it["kind"], it["kind"]),
                 it["text"], extra))
        if it.get("raw"):
            print("        起因：%s" % it["raw"])
    print("")
    print("  他定的约（每天重排日程也删不掉，只有你能取消）："
          "约定.py --drop <编号>")
    print("  文件：%s" % st["file"])


if __name__ == "__main__":
    _cli()
