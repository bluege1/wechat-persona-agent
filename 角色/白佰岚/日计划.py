# -*- coding: utf-8 -*-
"""日计划 · 她的这一天的「偏移」
==================================================================

治的病：**作息表是死的，每天一模一样。**

原来的 `daily_life.ROUTINE` 是一张固定的分钟级课表 —— 6:10 起、8:20 到教室，
天天如此。可真人不是：今天起晚了、明天差点迟到、后天失眠一整天发飘。
她要是天天一样，聊三天就能看出是个表。

所以在这一层加一个**当天偏移**：

    base 作息（ROUTINE，不变）
        +  今天的偏移（这一份，每天一份）
        =  她今天真正的一天

偏移长这样（`生活/日计划.json`）：

    {
      "date": "2026-09-22", "source": "local", "kind": "late",
      "note": "昨晚睡晚了，今天起得比平时迟，整个人发飘",
      "wake_shift": 15,
      "edits": [                        # 拿这些段**替换**原作息的 [a, b) 区间
        {"a": 370, "b": 500, "segs": [[370, 385, "睡着", 0, false], ...]}
      ]
    }

★ 每天最多**一份**：有 `生活/日计划.json` 里今天那份就用它，没有就现算
  （本地现算是**离线**的，按日期起种子 —— 不花 token、不发请求）。

★ API 生成是**可选**的：`life.day_plan.source` 设成 `auto`/`api`，并且
  启动时给了大脑，才会在「一天开始」请求一次。请求失败、返回不合法、
  或者压根没网 —— 一律**回落到本地那份**，她照样过日子。

★ 偏移**只改她的作息**，永远不动「他定的约」：他说的「晚上去赴一个约」
  存在 `生活/约定.json` 里，压在最上层（见 `daily_life.Life.diverts_today`），
  这里生成的任何新日程都**删不掉**它。

★ 她是**雾屿大学历史系大一学生，辅修古籍修复，还在学生会秘书处** ——
  **不住校**：自己在旧城区租了间老房子，早上走路二十分钟到学校；
  早上那一段就是「起床到坐进教室」。她起得比一般学生早一点（6:40）——
  不是勤快，是受不了宿舍里那些人动静，宁可早点出来慢慢走。

★ 她跟别人最不一样的地方：**傍晚那一段是「今天去哪儿」的窗口。**
  课到 18:30 就松了，18:30~20:00 这一段是按**星期几**轮换的 ——
  去档案馆翻旧卷宗 / 回家修书 / 去秘书处清档案柜 / 上姑妈家坐一会儿 /
  一个人走到堤岸 / 逛旧书摊和菜场。表在下面的 `_EVENING`，`local_plan()`
  每天照星期取一条盖上去。周日不在里面 —— 周日整段本来就是在家待着。
"""

from pathlib import Path
import datetime as dt
import json
import random
import re
import sys

ROOT = Path(__file__).resolve().parent

FILE_NAME = "日计划.json"

# 今天这一天属于哪一类
KINDS = ("normal", "late", "almost_late", "insomnia", "early")

# 本地摇的时候，各种情况占的比重（合计 1.0）
_WEIGHTS = [("normal", 0.42), ("late", 0.22), ("almost_late", 0.14),
            ("insomnia", 0.12), ("early", 0.10)]

_NOTE = {
    "normal": "",
    "late": "昨晚睡晚了，今天起得比平时迟，整个人发飘",
    "almost_late": "今天差点迟到，一路从巷子跑到学校，气还没喘匀",
    "insomnia": "昨晚翻来覆去没睡好，今天头晕，一整天都想趴桌上补一觉",
    "early": "今天自然醒得早，难得不困，脑子比平时清楚",
}

# 睡觉段文案必须落在这个集合里，每天的「醒着多久」才算得对
# （和 作息.py 的 SLEEPY 对齐 —— 那边只有 "睡着" 这一种写法）
_SLEEPY_TEXT = {"睡着"}

# 周一到周六：早上那一段（6:40 起床 ~ 8:00 坐进教室）可以被整段替换掉
_WD_MORNING_A = 6 * 60 + 40
_WD_MORNING_B = 8 * 60
# 周日：8:30 起床 ~ 10:30 该干的都干完（不赶课，偏移只在「几点起」上）
_SUN_MORNING_A = 8 * 60 + 30
_SUN_MORNING_B = 10 * 60 + 30

# 傍晚那一段 [18:30, 20:00)：按星期几轮换「今天去哪儿」
#   weekday: 0=周一 … 5=周六（周日不走这里，周日整天都在家）
_EVENING_A = 18 * 60 + 30
_EVENING_B = 20 * 60
_EVENING = {
    0: [(_EVENING_A,       _EVENING_A + 20, "去了趟档案馆，把上周查到的那本卷宗又借出来", 1, True),
        (_EVENING_A + 20,  _EVENING_A + 45, "在灯下照着一页页查，眼睛看得发涩", 1, True),
        (_EVENING_A + 45,  _EVENING_B,      "把卷宗还了，跟管理员点了下头", 1, False)],
    1: [(_EVENING_A,       _EVENING_A + 15, "收拾了桌面，把昨晚没修完的那页铺开", 1, True),
        (_EVENING_A + 15,  _EVENING_A + 45, "对着那页纸补了一个多钟头，手指上全是浆糊", 1, True),
        (_EVENING_A + 45,  _EVENING_B,      "补完压上，用镇纸压了压", 1, True)],
    2: [(_EVENING_A,       _EVENING_A + 25, "去秘书处把三层那口旧档案柜又清了半格", 1, True),
        (_EVENING_A + 25,  _EVENING_A + 45, "一沓一沓地编号，戴着手套，动作很慢", 1, True),
        (_EVENING_A + 45,  _EVENING_B,      "锁上柜门，把新抄的目录收进包里", 1, False)],
    3: [(_EVENING_A,       _EVENING_A + 25, "上姑妈家坐了一会儿，她把水果塞给我", 1, True),
        (_EVENING_A + 25,  _EVENING_A + 45, "她照旧问了两句学校的事，我都说还行", 1, True),
        (_EVENING_A + 45,  _EVENING_B,      "出来时天已经黑了，这条巷子没什么人", 1, False)],
    4: [(_EVENING_A,       _EVENING_A + 20, "不知道为什么，又走到堤岸那边去了", 1, True),
        (_EVENING_A + 20,  _EVENING_A + 45, "海边的雾比城里重，看不清对岸", 1, True),
        (_EVENING_A + 45,  _EVENING_B,      "站了一会儿就回来，风把白头发吹乱了", 1, False)],
    5: [(_EVENING_A,       _EVENING_A + 20, "去了趟旧书摊，翻了半天没找着要配的那种纸", 1, True),
        (_EVENING_A + 20,  _EVENING_A + 40, "又在菜场买了点青菜和一块豆腐", 1, True),
        (_EVENING_A + 40,  _EVENING_B,      "拎着东西往回走，路上给他发了一条", 1, True)],
}


# ══════════════════════════════════════════════════════════════════════════
#  本地那一份（离线、确定性）
# ══════════════════════════════════════════════════════════════════════════

def _wd_morning(kind: str) -> list:
    """周一到周六早上 6:40~8:00 的替换段。返回 [(a,b,text,talk,key)]。

    这些是**和 base 不同**的写法 —— kind == "normal" 时返回空，表示照旧。
    """
    a = _WD_MORNING_A
    if kind == "normal":
        return []
    if kind == "late":
        return [
            (a,        a + 45, "睡着", 0, False),
            (a + 45,   a + 50, "闹钟响到第二遍才醒，坐起来愣了会儿", 1, True),
            (a + 50,   a + 58, "洗漱，把白头发拢起来随便扎了", 1, False),
            (a + 58,   a + 62, "披上外套，把那盏灯塞进衣领", 1, False),
            (a + 62,   a + 68, "出门，巷子里的雾还没散尽", 1, False),
            (a + 68,   a + 80, "一路快走到学校，赶在铃响前坐进教室", 1, False),
        ]
    if kind == "almost_late":
        return [
            (a,        a + 10, "睡着", 0, False),
            (a + 10,   a + 15, "闹钟响到第二遍才醒，坐起来愣了会儿", 1, True),
            (a + 15,   a + 25, "洗漱，把白头发拢起来随便扎了", 1, False),
            (a + 25,   a + 30, "披上外套，把那盏灯塞进衣领", 1, False),
            (a + 30,   a + 40, "在巷口抓了两个包子，边走边吃", 1, True),
            (a + 40,   a + 80, "一路小跑到学校，还是差点迟到", 1, False),
        ]
    if kind == "insomnia":
        return [
            (a,        a + 15, "睡着", 0, False),
            (a + 15,   a + 20, "闹钟响了好久才听见，头沉得厉害", 1, True),
            (a + 20,   a + 30, "冷水拍脸，眼睛还是睁不开", 1, False),
            (a + 30,   a + 38, "洗漱，把白头发拢起来随便扎了", 1, False),
            (a + 38,   a + 55, "出门，在巷口买了杯热的", 1, True),
            (a + 55,   a + 80, "走到学校，脚步发飘", 1, False),
        ]
    if kind == "early":
        return [
            (a,        a + 10, "比闹钟早醒了一个钟头，躺着看了会儿手机", 1, True),
            (a + 10,   a + 20, "洗漱，顺手把窗推开一条缝", 1, False),
            (a + 20,   a + 28, "把白头发拢起来扎好", 1, False),
            (a + 28,   a + 50, "在巷口慢慢吃完一顿早饭", 1, True),
            (a + 50,   a + 80, "走到学校，今天不用赶", 1, False),
        ]
    return []


def _sun_morning(kind: str) -> list:
    """周日早上那一段（8:30~10:30）。周日不赶课，偏移只在「几点起」上。"""
    a = _SUN_MORNING_A
    if kind == "normal":
        return []
    if kind in ("late", "almost_late", "insomnia"):
        return [
            (a,        a + 60, "睡着", 0, False),
            (a + 60,   a + 70, "自然醒了，赖在床上不肯起", 1, True),
            (a + 70,   a + 85, "慢吞吞洗漱，把白头发扎起来", 1, False),
            (a + 85,   a + 105, "把昨晚没看完的那页旧书翻了两眼", 1, False),
            (a + 105,  a + 120, "抓上手机出门，往学校那边绕了一圈", 1, False),
        ]
    if kind == "early":
        return [
            (a,        a + 10, "比闹钟早醒了一个钟头，躺着看了会儿手机", 1, True),
            (a + 10,   a + 25, "慢吞吞洗漱，把白头发扎起来", 1, False),
            (a + 25,   a + 45, "给窗台上那盆石斛浇了水", 1, False),
            (a + 45,   a + 70, "早饭慢慢吃完，把包收好", 1, True),
            (a + 70,   a + 120, "出门往堤岸那边走，路上绕了一圈", 1, False),
        ]
    return []


def _swaps(kind: str) -> list:
    """白天的小改动（可选）。返回 [{"a","b","segs"}]。

    只在有说头的日子加 —— 「一部分事件会有改变」说的就是这个。
    改动时刻都落在她作息表的段边界上（见 作息.py）。
    """
    out = []
    if kind == "insomnia":
        # 第三节顶不住，半节课都在点头
        out.append({"a": 10 * 60, "b": 10 * 60 + 50,
                    "segs": [[10 * 60, 10 * 60 + 50,
                              "第三节撑不住，手撑着下巴还是直点头", 0, True]]})
    if kind == "late":
        # 午休不补觉了，改成把落下的笔记补上（起晚了心里不安）
        out.append({"a": 13 * 60, "b": 13 * 60 + 50,
                    "segs": [[13 * 60, 13 * 60 + 50,
                              "中午没躺，在图书馆把落下的笔记补上了", 0, True]]})
    if kind == "almost_late":
        # 课间缓一口气
        out.append({"a": 9 * 60 + 40, "b": 10 * 60,
                    "segs": [[9 * 60 + 40, 10 * 60,
                              "课间趴在桌上缓了缓气，还没喘匀", 1, False]]})
    if kind == "early":
        # 精神好，提前把修复课要用的工具理了一遍
        out.append({"a": 15 * 60 + 40, "b": 16 * 60,
                    "segs": [[15 * 60 + 40, 16 * 60,
                              "精神好，提前把修复课要用的工具理了一遍", 1, True]]})
    return out


def _evening(weekday: int) -> list:
    """周一到周六傍晚 [18:30, 20:00) 那一段：「今天去哪儿」。

    ★ 这是她跟别的角色最不一样的地方 —— **她的位置不固定**：
      去档案馆 / 回家修书 / 去秘书处清档案柜 / 上姑妈家 / 走到堤岸 /
      逛旧书摊和菜场。按星期几轮着换（表在上面的 `_EVENING`）。
      周日不走这里（周日整天本来就在家）。同一天永远同一条。
    """
    return list(_EVENING.get(int(weekday), []))


def _pick_kind(date_iso: str) -> str:
    """按日期起种子摇一个 kind —— 同一天永远是同一个（确定性）。"""
    rng = random.Random("dayplan|%s" % date_iso)
    r = rng.random()
    acc = 0.0
    for k, w in _WEIGHTS:
        acc += w
        if r <= acc:
            return k
    return "normal"


def local_plan(date_iso: str, weekday: int) -> dict:
    """离线现算一份今天的偏移。同一天永远同一份（不给随机留口子）。"""
    kind = _pick_kind(date_iso)
    school = weekday != 6
    if not school and kind == "almost_late":
        # 周日没有固定出门点，「差点迟到」说不通 —— 归到「起晚了」
        kind = "late"
    edits = []
    if school:
        segs = _wd_morning(kind)
        if segs:
            edits.append({"a": _WD_MORNING_A, "b": _WD_MORNING_B, "segs": segs})
        # 白天的小改动只在校日有意义（第三节 / 午休 / 课间都是上课日的事）
        edits.extend(_swaps(kind))
        # ★ 「今天去哪儿」的窗口：按星期几取一条盖上去（节次的边界见 作息.py）
        segs = _evening(weekday)
        if segs:
            edits.append({"a": _EVENING_A, "b": _EVENING_B, "segs": segs})
    else:
        segs = _sun_morning(kind)
        if segs:
            edits.append({"a": _SUN_MORNING_A, "b": _SUN_MORNING_B, "segs": segs})
    return {
        "date": date_iso,
        "source": "local",
        "kind": kind,
        "note": _NOTE.get(kind, ""),
        "wake_shift": _wake_shift_of(edits),
        "edits": edits,
    }


def _wake_shift_of(edits: list) -> int:
    """偏移在「几点起」上体现成多少分钟（正数 = 起得比平时迟）。"""
    for e in edits:
        if not e.get("segs"):
            continue
        first = e["segs"][0]
        if first[2] in _SLEEPY_TEXT:            # 前面还是睡
            if len(e["segs"]) > 1:
                return int(e["segs"][1][0]) - int(e["a"])
            continue
        return int(first[0]) - int(e["a"])
    return 0


# ══════════════════════════════════════════════════════════════════════════
#  读写（每天一份，落在她那边的生活目录）
# ══════════════════════════════════════════════════════════════════════════

def path_in(dir_path) -> Path:
    return Path(dir_path) / FILE_NAME


def load(dir_path, date_iso: str):
    """读缓存里今天那一份。没有 / 不是今天的 → None。"""
    try:
        d = json.loads(path_in(dir_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("date") != date_iso:
        return None
    if not isinstance(d.get("edits"), list):
        return None
    return d


def save(dir_path, plan: dict):
    try:
        p = path_in(dir_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
#  校验 / 压到作息上
# ══════════════════════════════════════════════════════════════════════════

def _hhmm_to_min(s):
    """'07:30' / '7:30' → 450。认不出来返回 None。"""
    if isinstance(s, int):
        return s if 0 <= s <= 30 * 60 else None
    m = re.match(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$", str(s))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 29 and 0 <= mi <= 59):
        return None
    return h * 60 + mi


def normalize(raw, date_iso: str, weekday: int) -> dict:
    """把一份（可能是模型吐出来的）偏移**校验并规整**。

    认不出、越界、留空档、时间倒着写 —— 一律丢掉那一处。
    整份都不可信时返回 None（调用方回落本地）。
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind not in KINDS:
        kind = "normal"
    note = str(raw.get("note") or "")[:80]

    edits = []
    for e in (raw.get("edits") or []):
        if not isinstance(e, dict):
            continue
        a = _hhmm_to_min(e.get("a"))
        b = _hhmm_to_min(e.get("b"))
        if a is None or b is None or b <= a or (b - a) > 4 * 60:
            continue
        segs = []
        for s in (e.get("segs") or []):
            if not isinstance(s, (list, tuple)) or len(s) < 3:
                continue
            sa = _hhmm_to_min(s[0])
            sb = _hhmm_to_min(s[1])
            text = str(s[2] or "").strip()
            if sa is None or sb is None or sb <= sa or not text:
                continue
            if sa < a or sb > b:                 # 越出这一段，丢掉
                continue
            talk = int(s[3]) if len(s) > 3 and str(s[3]).lstrip("-").isdigit() else 0
            key = bool(s[4]) if len(s) > 4 else False
            segs.append((sa, sb, text[:40], max(0, min(2, talk)), key))
        if not segs:
            continue
        segs.sort(key=lambda x: x[0])
        # 不许留空档、不许叠：必须严丝合缝盖住 [a, b)
        ok = segs[0][0] == a and segs[-1][1] == b
        if ok:
            for i in range(1, len(segs)):
                if segs[i][0] != segs[i - 1][1]:
                    ok = False
                    break
        if not ok:
            continue
        edits.append({"a": a, "b": b, "segs": [list(x) for x in segs]})

    if not edits and not note:
        return None
    return {
        "date": date_iso,
        "source": str(raw.get("source") or "api")[:8],
        "kind": kind,
        "note": note,
        "wake_shift": _wake_shift_of(edits),
        "edits": edits[:2],
    }


def apply(segs: list, plan) -> list:
    """把今天的偏移压到作息段上。segs = [(a,b,text,talk,key)]。

    plan 为空 → 原样返回（这是 `查错.py` 里那些「按基准作息」的断言的前提）。
    """
    if not plan or not plan.get("edits"):
        return list(segs)
    out = [tuple(x) for x in segs]
    for e in plan["edits"]:
        a, b = int(e["a"]), int(e["b"])
        if b <= a:
            continue
        new = []
        for sa, sb, text, talk, key in out:
            if sb <= a or sa >= b:              # 不搭界
                new.append((sa, sb, text, talk, key))
                continue
            if sa < a:                          # 左半截留着
                new.append((sa, a, text, talk, key))
            if sb > b:                          # 右半截留着
                new.append((b, sb, text, talk, key))
        for sa, sb, text, talk, key in e["segs"]:
            new.append((int(sa), int(sb), text, int(talk), bool(key)))
        out = new
    out.sort(key=lambda x: x[0])
    return out


def describe(plan) -> str:
    """给控制台 / 自检看的一行话。"""
    if not plan:
        return ""
    kind = plan.get("kind", "normal")
    cn = {"normal": "和平时一样", "late": "起晚了", "almost_late": "差点迟到",
          "insomnia": "失眠，没睡好", "early": "起得早"}.get(kind, kind)
    ws = int(plan.get("wake_shift") or 0)
    bit = "（起床 %s%d 分钟）" % ("迟了" if ws > 0 else "早了", abs(ws)) if ws else ""
    src = {"api": "API 生成", "local": "本地现算"}.get(plan.get("source"), plan.get("source"))
    note = ("：%s" % plan["note"]) if plan.get("note") else ""
    return "今天 %s%s%s　[%s]" % (cn, bit, note, src)


# ══════════════════════════════════════════════════════════════════════════
#  API 生成（可选；失败一律回落本地）
# ══════════════════════════════════════════════════════════════════════════

_PROMPT = """你在给她排今天的一天。

她的固定作息（这是底子，不要重写）：
---
%s
---

请只输出一个 JSON（不要解释、不要代码围栏、不要多余的字），描述今天和平时**不一样**的地方：
{
  "kind": "normal | late | almost_late | insomnia | early",
  "note": "一句话说今天什么状况，比如「昨晚失眠，今天整个人发飘」",
  "edits": [
    {"a": "06:50", "b": "08:00",
     "segs": [["06:50", "07:05", "睡着", 0, false],
              ["07:05", "07:10", "闹钟响到第二遍才醒，坐起来愣了会儿", 1, true]]}
  ]
}

硬要求：
1. 时间一律写成 "HH:MM"；segs 必须**严丝合缝铺满** [a, b)，中间不许留空档、不许重叠；
2. segs 每项是 [起, 止, 文本, 话题度, 是不是里程碑]；
   话题度 0/1/2；里程碑 true 只给「起床 / 出门 / 到教室 / 吃饭 / 洗澡 / 睡」这种节点；
3. 只在**早上那一段**（起床到坐进教室）和最多**一处白天的小改动**上做文章；
   **傍晚 18:30~20:00 那一段不要动** —— 那是「今天去哪儿」的窗口，程序自己排；
4. 睡觉段的文本必须原样写 "睡着"，别改 —— 程序靠它算「醒了多久」；
5. 今天要是很平常，就 "kind": "normal" 且 "edits": []。"""


def _base_text(segs) -> str:
    out = []
    for a, b, text, _talk, key in segs:
        out.append("%s-%s %s%s" % (_hhmm(a), _hhmm(b), text,
                                   "（节点）" if key else ""))
    return "\n".join(out)


def _hhmm(m: int) -> str:
    return "%02d:%02d" % (int(m) // 60 % 24, int(m) % 60)


def _extract_json(s: str):
    """从模型的回答里抠出第一个 JSON 对象（怕它带围栏或废话）。"""
    if not s:
        return None
    t = s.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


def _with_evening(plan, weekday: int, base_segs=None):
    """把「今天去哪儿」那一段合进今天这一份。

    ★ 本地现算的那份本来就带这一段；**API 生成的那份也得带上** ——
      不然开着 `source: auto` 的日子里（API 一成功就走它），
      她就天天待在学院、不回家了，那就不像她了。
    已经有东西盖着这一段（本地那份）就不动，避免叠两层。
    """
    if not isinstance(plan, dict):
        return plan
    segs = _evening(weekday)
    if not segs:
        return plan
    for e in (plan.get("edits") or []):
        try:
            if int(e["a"]) < _EVENING_B and int(e["b"]) > _EVENING_A:
                return plan
        except Exception:
            continue
    plan.setdefault("edits", []).append(
        {"a": _EVENING_A, "b": _EVENING_B,
         "segs": [list(s) for s in segs]})
    return plan


def generate(date_iso: str, weekday: int, base_segs=None,
             chat=None, dir_path=None, force: bool = False) -> dict:
    """拿今天那一份：有缓存用缓存；没有就试 API；API 不行回落本地。

    chat 是一个 `chat(messages, max_tokens) -> str` 的可调用对象（bridge 传大脑）。
    传 None（自检 / 命令行）就**绝不发请求**，直接用本地那份。
    """
    if not force and dir_path is not None:
        got = load(dir_path, date_iso)
        if got is not None:
            return got

    plan = None
    if chat is not None:
        try:
            raw = chat([{"role": "user", "content":
                         _PROMPT % _base_text(base_segs or [])}], 700)
            plan = normalize(_extract_json(raw), date_iso, weekday)
        except Exception:
            plan = None

    if plan is None:
        plan = local_plan(date_iso, weekday)

    plan = _with_evening(plan, weekday, base_segs)

    if dir_path is not None:
        save(dir_path, plan)
    return plan


# ══════════════════════════════════════════════════════════════════════════
#  命令行
# ══════════════════════════════════════════════════════════════════════════

def _cli():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8",
                               errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    today = dt.date.today()
    iso = today.isoformat()
    live_dir = ROOT / "生活"

    if "--local" in args or "--roll" in args:
        n = int(args[args.index("--local") + 1]) if "--local" in args and \
            len(args) > args.index("--local") + 1 and \
            args[args.index("--local") + 1].isdigit() else 0
        print("她今天这一份（本地现算，不落盘）：")
        print("  " + describe(local_plan(iso, today.weekday())))
        if n:
            print("")
            print("往后 %d 天的偏移长什么样（都是本地那份）：" % n)
            for i in range(n):
                d = today + dt.timedelta(days=i)
                print("  %s 周%s  %s"
                      % (d.isoformat(), "一二三四五六日"[d.weekday()],
                         describe(local_plan(d.isoformat(), d.weekday()))))
        return

    got = load(live_dir, iso)
    print("=" * 58)
    if got:
        print("  今天那一份（已落盘）：")
        print("  " + describe(got))
        for e in got.get("edits", []):
            print("  · %s-%s 换掉 %d 段"
                  % (_hhmm(e["a"]), _hhmm(e["b"]), len(e["segs"])))
            for sa, sb, t, talk, key in e["segs"]:
                print("      %s-%s %s%s" % (_hhmm(sa), _hhmm(sb), t,
                                            "  ★" if key else ""))
    else:
        print("  今天还没有落盘的那一份 —— 现在会用本地现算的：")
        print("  " + describe(local_plan(iso, today.weekday())))
    print("=" * 58)
    print("  文件：%s" % path_in(live_dir))


if __name__ == "__main__":
    _cli()
