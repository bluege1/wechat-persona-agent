"""分享欲 · 联想系统

治的是两个病，都在「她刚认识你就开始倒自己的日常」上：

1. 【她太爱说自己】—— 第 1 档才聊十几轮，她就把今天干了什么全倒出来。
   真人不是这样。刚认识的人，会含糊、会敷衍、会「没干嘛」。
   所以按关系档位给一个**分享欲**：第 1 档基本不分享自己，
   他问起也一句带过；档位越高越愿意说，到第 5 档（恋人）什么都想跟他讲。

2. 【素材凭空蹦出来】—— 程序每天把「今天到现在」的清单塞进提示词，
   她看到就忍不住用，于是聊到一半突然说「我下午去买了三本旧书」。
   可真人是**被话题勾出来**的：你说到书，她才想起「我今天也去了趟书摊」。
   所以素材从「清单」改成「**联想池**」：
   他的话里出现某个话题（书 / 吃的 / 睡觉 / 西西 ……），
   才把今天相关的那件事递给她，一次最多一件。

两个病其实一个根：**素材的可见性**。
以前是「随时摆在眼前」，现在是「说到才想起来」。

──────────────────────────────────────────────────────────────

命令行：

  python 分享.py --show                看她现在第几档、今天还剩几次
  python 分享.py --topics              列全部话题和关键词
  python 分享.py --hit "我最近在看一本书"   试联想（会打印翻出什么）
  python 分享.py --events              列今天发生过、还没说过的事

想加话题就往 TOPICS 里加一行：话题名 → 关键词列表。
单字关键词（书、猫、雨）也算，只要够明确；「看」「想」「玩」这种
太泛的别放，会把什么话都勾出来。
"""

from pathlib import Path
import datetime as dt
import json
import re
import sys

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
# ══════════════════════════════════════════════════════════════════════
#  一、分享欲：每一档，她愿意露多少自己
# ══════════════════════════════════════════════════════════════════════
#
#  ★ 数字**只有一份**：relation.py 的 DEFAULT_STAGES（五档表）。
#    控制台「好感度」页改的就是那一份 —— 改完这里立刻跟着变。
#    （以前这里写死过一份，跟档位对不上就两边打架，2026-09-22 收掉的。）
#
#  这个文件只留「文字」：每一档她该怎么说话的两条规矩（RULES）。
#
#    · 说多少 —— max_chars（一条最多几个字）/ max_parts（最多几条）
#                 total_chars（一次总共多少字）/ max_tokens（生成时就掐住）
#    · 说不说 —— per_day（一天最多几次「顺口提自己」）
#                 proactive_per_day（一天最多主动找几次）
#                 idle_scale（安静多久就忍不住，越小越黏人）
#
#  ★ 第 1 档是刻意压得很狠的：一次总共 20 个字、最多 2 条、一天只准提
#    自己一件事、不主动发消息。这不是 bug —— 刚认识就该是这个样子。
#    你和她不熟的时候她发一堆日常，才是假的。
#
#  ★ 第 5 档（恋人）也是从同一张表来的 —— 它跟前面四档的区别只在于
#    数字全放开，真正的区别在 表白.py 的 lover_block() 和 你和我.md 里。
#
#  ⚠ max_chars 千万别设小（第 1 档给 15）。它管的是「别发一条二十几字的长条」，
#    不是逼她把话劈开 —— 设成 10 的话，一句「我不爱跟脾气好的人打交道」（12 字）
#    就会被硬切成「我不爱跟脾气好」/「的人打交道」，语义是断的，一眼假。
#    **控制总量靠 total_chars，不靠单条上限。** 中文没有词边界，单条切得越小
#    越容易切在词中间，切出来的半句话比发长一点难看得多。

# ── 每一档她该怎么说话（文字部分，数字在 relation.py）────────────────

RULES = {
    1: dict(
        asked_rule=(
            "他要是问你在干嘛、今天做了什么 —— **别报备**。"
            "「没干嘛」「老样子」「懒得说」就够了。"
            "你跟他还没到交代自己一天的地步。"
        ),
        share_rule=(
            "你**不主动说自己**。今天做过什么，一句都不用讲。"
            "除非他正好聊到那件事，你才顺口带半句 —— 而且就半句，不展开。"
        ),
    ),
    2: dict(
        asked_rule=(
            "他问起你今天，可以给一句实话，但只给一句 ——"
            "别从头讲到尾，讲完就没了。"
        ),
        share_rule=(
            "他会偶尔从你嘴里听到一点日常，但都是「顺便」漏出来的："
            "他聊到什么，你才搭一句自己那边的事。别主动交代。"
        ),
    ),
    3: dict(
        asked_rule="他问你在干嘛，可以正常答，说一两句就够。",
        share_rule=(
            "你会开始提自己那边的事了，但挑有点说头的提 ——"
            "「今天被西西拖去逛街」这种，而不是「我今天吃了饭」。"
        ),
    ),
    4: dict(
        asked_rule="他问你就说，说得比前几档细一点。",
        share_rule="你会主动说一点自己的事。说的时候不用先想「该不该说」。",
    ),
    5: dict(
        asked_rule=(
            "他问你什么都答，答得碎一点也无所谓 —— 你早就不挑拣了。"
        ),
        share_rule=(
            "今天发生的什么你都想告诉他。而且你会**为了讲给他听**"
            "而留意今天的事 —— 看到点什么，第一反应是「这个他会喜欢」。"
            "（但别变成流水账：挑有画面的那件说，不是列表。）"
        ),
    ),
}

# relation 的字段名 → 这个文件一直在用的老名字（别改：控制台和查错都在用）
_KEYMAP = {
    "name": "name", "short": "short", "key": "key",
    "say_per_day": "per_day",
    "say_chars": "max_chars",
    "say_parts": "max_parts",
    "say_total": "total_chars",
    "say_tokens": "max_tokens",
    "active_per_day": "proactive_per_day",
    "idle_scale": "idle_scale",
    "allow": "allow",
}

# ⚠ 这一份只是「relation.py 不在了」时的救命副本 —— **不是第二份真相**。
#   平时永远走 relation.stage_table(cfg)，控制台改的是那一份。
_FALLBACK = [
    dict(name="初遇", per_day=1, max_chars=15, max_parts=2, total_chars=20,
         max_tokens=60, proactive_per_day=0, idle_scale=1.8, allow=[]),
    dict(name="朋友", per_day=2, max_chars=16, max_parts=3, total_chars=36,
         max_tokens=90, proactive_per_day=1, idle_scale=1.4, allow=[]),
    dict(name="重要的人", per_day=3, max_chars=16, max_parts=3, total_chars=39,
         max_tokens=100, proactive_per_day=3, idle_scale=1.05, allow=["闹脾气"]),
    dict(name="暧昧", per_day=4, max_chars=20, max_parts=5, total_chars=80,
         max_tokens=170, proactive_per_day=5, idle_scale=0.8,
         allow=["闹脾气", "名字", "想你"]),
    dict(name="恋人", per_day=99, max_chars=22, max_parts=10, total_chars=220,
         max_tokens=400, proactive_per_day=15, idle_scale=0.3,
         allow=["闹脾气", "名字", "想你", "撒娇"]),
]


def build_stages(cfg: dict = None, table=None) -> dict:
    """五档表 = relation 给的数字 + 这个文件里的文字规矩。键是 1~5。

    table 直接给一份（relation 的五档表）时就不去读 config 了。
    """
    tab = table
    if not tab:
        try:
            import relation
            tab = relation.stage_table(cfg)
        except Exception:
            tab = []
    if not tab:
        tab = [dict(s) for s in _FALLBACK]
    out = {}
    for i, eff in enumerate(tab, start=1):
        d = {}
        for k, v in (eff or {}).items():
            d[_KEYMAP.get(k, k)] = v
        d.update(RULES.get(i, {}))
        d.setdefault("idle_scale", 1.0)
        d.setdefault("allow", [])
        out[i] = d
    return out


STAGES = build_stages()

STAGE_COUNT = len(STAGES)

# 兜底：档位算出界了就按这个来（中间那一档）
_DEFAULT = STAGES.get(min(3, STAGE_COUNT)) or list(STAGES.values())[0]



# ══════════════════════════════════════════════════════════════════════
#  二、话题表：他的话里出现哪个话题，就翻哪一类素材
# ══════════════════════════════════════════════════════════════════════
#
#  规则很简单：话题里任何一个关键词出现在他的话里，这个话题就算被提起。
#  双字以上关键词算 3 分，单字算 2 分，够 2 分就认 —— 说一个词组、
#  或者说出一个准词（「书」「猫」「雨」），都能把它勾出来。
#
#  ⚠ 单字关键词只放**指向明确**的：书、猫、雨、药、梦……
#    「看」「想」「玩」「好」这种一放进去，什么话都能勾出素材，等于没筛。
#
#  素材那边用同一张表打标签，所以两边永远是同一套词。

TOPICS = {
    "吃的": [
        "食堂", "甜点", "蛋糕", "面包", "冰淇淋", "奶茶", "咖啡", "巧克力",
        "夜宵", "早饭", "午饭", "晚饭", "早餐", "点心", "好吃", "嘴馋",
        "布丁", "饼干", "糖果", "零食", "餐能", "小吃", "水果", "橘子",
        "苹果", "甜", "吃", "饿", "馋", "饭",
        # ★ 2026-09-23 多角色：守岸人那边是「雪烩浓汤 / 椿塞的果汁」，
        #   一个「食堂/饭」都撞不上，整条素材永远翻不出来 —— 补几个词。
        "浓汤", "汤", "果汁", "饮料", "罐头", "膏", "尝",
    ],
    "书和作业": [
        "图书馆", "旧书", "书摊", "看书", "翻书", "论文", "笔记", "文献",
        "复习", "考试", "作业", "课本", "上课", "借书", "还书", "小说",
        "抄写", "报告", "开题", "截止",
        "书", "读", "课", "题", "写",
    ],
    "睡觉和累": [
        "睡不着", "赖床", "闹钟", "熬夜", "打盹", "睡了一", "睡了半",
        "没睡", "做梦", "梦到", "半夜", "凌晨", "天花板", "被窝", "枕头",
        "睡", "困", "累", "醒", "梦",
    ],
    "天气和天色": [
        "天气", "太阳", "晚霞", "下雨", "雨", "刮风", "风", "云", "天幕",
        "星辰", "星空", "月亮", "晒", "冷", "热", "凉", "雾",
    ],
    "西西": ["西西", "西格莉卡"],
    "小熊": ["小熊", "骗术师"],
    "走路和逛街": [
        "散步", "逛街", "绕远", "走了", "路过", "街", "广场", "校门",
        "天桥", "巷子", "走",
    ],
    "游戏和玩": [
        "方块", "拉海洛", "游戏", "打了两局", "风筝", "玩", "输", "赢",
    ],
    "拍照": [
        "摄影", "照片", "相册", "相机", "拍", "模特", "取景", "底片",
    ],
    "身体和虚质": [
        "虚质", "嗡鸣", "耳鸣", "头晕", "头疼", "复查", "吃药", "药",
        "手腕", "义肢", "体力", "发烧", "感冒", "不舒服",
    ],
    "手机": [
        "手机", "屏幕", "充电", "信号", "没电", "消息", "屏幕亮",
    ],
    "洗澡和收拾": [
        "洗澡", "毛巾", "洗漱", "衣服", "宿舍", "收拾", "洗衣", "晾",
        "换衣服", "镜子",
    ],
    "猫和动物": ["猫", "狗", "鸟", "动物", "流浪"],
    "想事和心情": [
        "发呆", "走神", "想事情", "心情", "烦躁", "烦", "闷", "坐着",
        "发愣", "出神", "情绪",
        # 「发了很久呆」里没有「发呆」两个字，单字「呆」兜住它。
        "呆", "忘",
    ],
    "音乐和歌": ["音乐", "歌", "耳机", "唱片", "哼", "调子", "旋律"],
    "旧东西和回忆": [
        "照片", "相册", "旧东西", "以前", "硬币", "信", "旧本子",
        "记不清", "想不起来",
    ],
    # ── ★ 2026-09-23 多角色：下面四个是「这个世界」的话题 ──────────────
    #   前三个人过的日子不一样，可达妮娅那张表全是校园生活词。
    #   测下来她（守岸人）240 条素材只有 66 条挂得上话题（27%），
    #   也就是七成素材永远翻不出来 —— 她一提「花房 / 星图 / 巡锚」，
    #   分享机制认不出这是话题，等于白写。
    #   这四个话题就是补这个洞；两边照旧共用一张表，加词只加表、不加角色。
    "花和蝴蝶": [
        "花", "蝴蝶", "蝶", "金枝地", "花房", "花海", "花种", "开花", "浇",
    ],
    "海和锚": [
        "海", "海岸", "海边", "浪", "潮", "沉雾岩", "风暴", "锚",
        "巡锚", "稳定锚", "岛", "雾",
    ],
    "调律和星图": [
        "调律", "频率", "音律", "星图", "嗡鸣", "钢琴", "弹琴", "回音",
        "唱片", "旋律",
    ],
    "值班和文书": [
        "报表", "名册", "登记", "档案", "巡查", "收容所", "预警",
        "执花", "交接", "值班",
    ],
}


def topic_score(text: str, kws) -> int:
    """一段话对某个话题的命中分。

    双字词 3 分、单字词 2 分，够 2 分就算命中 —— 也就是
    「说了一个词组」或「说了一个准词」都能勾出来。

    ⚠ 单字也给满 2 分是有意的。他要是说「我最近在看一本书」，
    表里只有单字「书」能对上，要是把它算轻了，这个最该被勾出来的场景
    反而勾不出来 —— 那就白做了。单字表是精挑过的，误命中很少，
    就算偶尔勾错一次（「你嘴真甜」勾出吃的），后果也只是她提一句吃的，
    反而贴题。
    """
    if not text:
        return 0
    s = 0
    for kw in kws:
        if kw in text:
            s += 3 if len(kw) >= 2 else 2
    return s


def topics_of(text: str, floor: int = 2):
    """这段话提到了哪些话题，按分数从高到低。返回 [(话题, 分数), ...]"""
    got = []
    for name, kws in TOPICS.items():
        s = topic_score(text, kws)
        if s >= floor:
            got.append((name, s))
    got.sort(key=lambda x: -x[1])
    return got


# ══════════════════════════════════════════════════════════════════════
#  三、联想引擎
# ══════════════════════════════════════════════════════════════════════

class Share:
    """按档位管她的分享欲，并按话题把她今天的事联想出来。

    她自己不知道「今天做过什么清单」—— 那是故意的。
    只有他的话撞上某个话题，这件事才会被递到她眼前，一次最多一件。
    """

    def __init__(self, cfg: dict = None):
        cfg = cfg or {}
        scfg = (cfg.get("share", {}) or {}) if isinstance(cfg, dict) else {}
        self.cfg = scfg
        self.enabled = bool(scfg.get("enabled", True))
        # 长度按档位走（关掉就用 config.behavior 里那几个固定值）
        self.stage_length = bool(scfg.get("length_by_stage", True))
        # 拿最近几轮的话一起去找话题（他上一轮说「书」，这轮说「你也有？」也接得上）
        self.query_lines = int(scfg.get("query_lines", 2))
        # 一次最多递几件（默认 1 —— 递多了她又开始倒）
        self.max_pick = int(scfg.get("max_pick", 1))
        # 额度：必须 ≥1 才允许联想
        self.quota_on = bool(scfg.get("quota", True))

        f = scfg.get("state_file") or "生活/分享.json"
        self.path = Path(f)
        if not self.path.is_absolute():
            self.path = ROOT / self.path
        self.state = self._load()
        # ★ 五档表从 relation 读（控制台「好感度」页改的那一份）。
        #   构造时读一次；档位变了 sync() 会重读，所以控制台上改完立刻生效。
        self.stages = build_stages(cfg)
        self.stage = 1

    # ── 记账（一天一清） ──────────────────────────────────────────────
    def _load(self) -> dict:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    return d
            except Exception:
                pass
        return {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass

    def _roll_day(self):
        today = dt.date.today().isoformat()
        if self.state.get("date") != today:
            self.state = {"date": today, "used": []}
            self._save()

    # ── 档位 ──────────────────────────────────────────────────────────
    def sync(self, stage: int, table=None):
        """跟着关系档位走。

        table 传进来（就是 relation 那份五档表）的时候顺手把数字刷一遍 ——
        这样控制台「好感度」页改完，机器人不用重启也能跟上。
        签名没变就什么都不做（每轮都调，不能白干活）。
        """
        if table:
            try:
                sig = json.dumps(table, ensure_ascii=False, sort_keys=True)
            except Exception:
                sig = None
            if sig and sig != getattr(self, "_sig", None):
                self._sig = sig
                self.stages = build_stages(table=table)
        try:
            s = int(stage)
        except Exception:
            s = 1
        cnt = len(self.stages) or 1
        # 超出范围就夹到边上（老存档里可能还留着旧版档位表的编号）
        self.stage = max(1, min(cnt, s))
        return self.stage

    @property
    def level(self) -> dict:
        return self.stages.get(self.stage, _DEFAULT)

    def reload_stages(self, table=None, cfg: dict = None):
        """强制重读五档表（控制台保存完可以直接调）。"""
        self.stages = build_stages(cfg=cfg, table=table)
        try:
            self._sig = json.dumps(table, ensure_ascii=False,
                                   sort_keys=True) if table else None
        except Exception:
            self._sig = None
        cnt = len(self.stages) or 1
        if self.stage > cnt:
            self.stage = cnt
        return self.stages

    def idle_scale(self) -> float:
        """这一档她安静多久就忍不住（越小越黏人）。主动消息那边在用。"""
        try:
            return max(0.0, float(self.level.get("idle_scale", 1.0)))
        except (TypeError, ValueError):
            return 1.0

    def allow(self) -> list:
        return list(self.level.get("allow") or [])

    def limits(self) -> dict:
        """这一档的字数预算。stage_length 关了就用 config 的固定值。"""
        return self.level

    def proactive_quota(self) -> int:
        """这一档一天允许她主动找几次（0 = 不主动）。"""
        if not self.enabled:
            return 99         # 分享欲关了就不管，交给 proactive 自己的上限
        return int(self.level.get("proactive_per_day", 0))

    # ── 额度 ──────────────────────────────────────────────────────────
    def used(self) -> list:
        self._roll_day()
        return list(self.state.get("used") or [])

    def left(self) -> int:
        """今天还能「顺口提自己」几次。"""
        if not self.quota_on:
            return 99
        return max(0, int(self.level.get("per_day", 3)) - len(self.used()))

    def mark(self, text: str):
        """这件事已经递给她了（递过就算用掉一次额度，不管她说没说）。"""
        if not text:
            return
        self._roll_day()
        u = self.state.setdefault("used", [])
        if text not in u:
            u.append(text)
            self._save()

    def clear_today(self):
        self.state = {"date": dt.date.today().isoformat(), "used": []}
        self._save()

    # ── 联想 ──────────────────────────────────────────────────────────
    def recall(self, texts, events, said=()):
        """他的这几句话里提到了什么话题 → 翻出今天相关的一件事。

        texts   : [他的话, 上一轮的话, ...]（越前面权重越高）
        events  : [{"text":..., "time":..., "kind":...}, ...] 今天发生的事
        said    : 已经跟他说过的素材文本（不再翻）

        返回 [{"topic":..., "event": {...}}, ...]，最多 max_pick 件。
        没命中就返回空 —— **空才是常态**，她不该老想着自己那点事。
        """
        if not self.enabled or not events:
            return []
        if self.quota_on and self.left() <= 0:
            return []

        # 1) 他的话里有哪些话题（越靠前的话权重越高：第一句 ×2）
        score = {}
        for k, t in enumerate(texts or []):
            if not t:
                continue
            w = 2 if k == 0 else 1
            for name, s in topics_of(t):
                score[name] = max(score.get(name, 0), s * w)
        if not score:
            return []

        # 2) 按话题强弱排，逐个找今天还没说过的事
        used = set(self.used()) | set(said or [])
        picked, seen = [], set()
        for name in sorted(score, key=lambda k: -score[k]):
            ev = self._pick(name, events, used, seen)
            if not ev:
                continue
            picked.append({"topic": name, "event": ev})
            seen.add(ev["text"])
            if len(picked) >= self.max_pick:
                break
        return picked

    def _pick(self, topic: str, events, used, seen):
        """这个话题下，今天还没说过、最靠后（最近发生）的那件事。"""
        kws = TOPICS.get(topic) or []
        cand = []
        for ev in events:
            t = ev.get("text") or ""
            if not t or t in used or t in seen:
                continue
            if topic_score(t, kws) < 2:
                continue
            if ev.get("said"):
                continue
            cand.append(ev)
        if not cand:
            return None
        return cand[-1]

    # ── 给模型看 ──────────────────────────────────────────────────────
    def stage_block(self) -> str:
        """每轮都发的一小段：这一档她愿意露多少自己。

        只有短短三行（几十个字），但很值 ——
        「他问你在干嘛别报备」这件事必须每轮都在场，
        光写在 你和我.md 里会被别的内容冲淡。
        """
        if not self.enabled:
            return ""
        lv = self.level
        lines = ["# 你现在愿意露多少自己（第 %d 档「%s」）"
                 % (self.stage, lv.get("name", ""))]

        # ★ 这一组是「说多少」真正生效的地方。只把档位写在 你和我.md 里是不够的：
        #   模型会照 persona 里那个通用数字（1~3 条 / 45 字）去写，写完再被硬砍，
        #   就砍出「我不爱跟脾气好…」这种半句话。每轮把当档的数字摆在眼前才拦得住。
        mc, mp, tc = lv.get("max_chars"), lv.get("max_parts"), lv.get("total_chars")
        if mp and tc:
            lines.append(
                "- 这一轮你**最多发 %d 条，加起来不超过 %d 个字**"
                "（一条最多 %s 个字）。" % (mp, tc, mc or "十几"))
            lines.append(
                "- 字数是硬上限，说完就没有了：话长就**换个短的说法**，"
                "要不就只说这一半、剩下的不说了。"
                "**绝对不要把一句话说一半留半截** ——"
                "宁可只说「算了」两个字，也不许出现「我不爱跟脾气好…」这种断掉的句子。")

        if lv.get("asked_rule"):
            lines.append("- " + lv["asked_rule"])
        if lv.get("share_rule"):
            lines.append("- " + lv["share_rule"])
        if self.quota_on:
            lines.append("- 今天这种「顺口说到自己」的机会，还剩 %d 次。" % self.left())
        return "\n".join(lines)

    def block(self, picked) -> str:
        """把联想结果拼成一段提示。没联想出东西就不该调这个。

        这里只管「你想起了什么」，不管「你能说多少」——
        后者由 stage_block() 每轮交代。
        """
        if not picked:
            return ""
        lines = [
            "# 他刚聊到的，让你想起一件事",
            "",
            "（这只是提醒你「你想起来了」，不是任务 ——"
            "想提就顺口带一句，不想提就当他没说到，别提得像在汇报。）",
            "",
        ]
        for p in picked:
            ev = p["event"]
            when = ev.get("time") or ""
            lines.append("- 他说到「%s」。你今天正好做过：%s%s"
                         % (p["topic"], (when + " ") if when else "", ev["text"]))
        return "\n".join(lines)

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "stage": self.stage,
            "stage_count": len(self.stages),
            "stage_name": self.level.get("name", ""),
            "per_day": self.level.get("per_day"),
            "used_today": len(self.used()),
            "left_today": self.left(),
            "proactive_per_day": self.proactive_quota(),
            "max_chars": self.level.get("max_chars"),
            "max_parts": self.level.get("max_parts"),
            "total_chars": self.level.get("total_chars"),
            "max_tokens": self.level.get("max_tokens"),
            "idle_scale": self.idle_scale(),
            "allow": self.allow(),
            "stages": [{"i": k, **v} for k, v in sorted(self.stages.items())],
            "topics": len(TOPICS),
            "state_file": str(self.path),
        }


# ══════════════════════════════════════════════════════════════════════
#  四、命令行
# ══════════════════════════════════════════════════════════════════════

def _cli():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]

    def _cfg():
        try:
            import yaml
            p = ROOT / "config.yaml"
            if p.exists():
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
        return {}

    def _stage(cfg):
        try:
            import relation
            r = relation.Relation(cfg)
            return r.stage
        except Exception:
            return 1

    cfg = _cfg()
    sh = Share(cfg)
    sh.sync(_stage(cfg))

    if "--topics" in args:
        print("=" * 62)
        print("话题表（%d 个话题）" % len(TOPICS))
        print("=" * 62)
        for name, kws in TOPICS.items():
            print("\n  【%s】%d 个关键词" % (name, len(kws)))
            print("    " + " ".join(kws))
        return

    if "--events" in args:
        try:
            import daily_life
            life = daily_life.Life(cfg)
            evs = life.today_events()
        except Exception as e:
            print("[X] 读不到今天的事：%s" % e)
            return
        print("=" * 62)
        print("今天发生过的事（%d 件）" % len(evs))
        print("=" * 62)
        for ev in evs:
            tags = "/".join(t for t, _ in topics_of(ev.get("text") or "")) or "（没标签）"
            print("  %-6s %-4s %s"
                  % (ev.get("time", ""), "已说" if ev.get("said") else "没说",
                     ev.get("text", "")))
            print("         └ 话题：%s" % tags)
        print("\n（注：没标签的事件联想不到 —— 想让它能被聊出来，"
              "往 分享.py 的 TOPICS 里加个词）")
        return

    if "--hit" in args:
        i = args.index("--hit")
        q = args[i + 1] if i + 1 < len(args) else ""
        print("=" * 62)
        print("试联想：「%s」" % q)
        print("=" * 62)
        hits = topics_of(q)
        print("  命中的话题：%s" % ("、".join("%s(%d)" % (n, s) for n, s in hits) or "无"))
        try:
            import daily_life
            life = daily_life.Life(cfg)
            evs = life.today_events()
        except Exception as e:
            print("  [X] 读不到今天的事：%s" % e)
            return
        got = sh.recall([q], evs)
        if not got:
            print("  → 没有翻出东西。%s"
                  % ("今天这种机会已经用完了（第 %d 档，一天 %d 次）"
                     % (sh.stage, sh.level.get("per_day", 0))
                     if sh.left() <= 0 else "这句话跟今天的事撞不上，她会正常聊天。"))
            return
        print()
        print(sh.block(got))
        return

    if "--reset" in args:
        sh.clear_today()
        print("[OK] 今天的分享额度已清空。")
        return

    # 默认：--show
    s = sh.stats()
    print("=" * 62)
    print("分享欲（第 %d 档「%s」）" % (s["stage"], s["stage_name"]))
    print("=" * 62)
    print("  今天顺口提自己：%d / %d 次（还剩 %d）"
          % (s["used_today"], s["per_day"], s["left_today"]))
    print("  主动找他的配额：%d 次/天%s"
          % (s["proactive_per_day"],
             "（这一档她不主动开口）" if s["proactive_per_day"] == 0 else ""))
    print()
    print("  字数预算（每条 / 最多几条 / 一次总共）")
    print("    %d 字 / %d 条 / %d 字，生成时掐在 %d token"
          % (s["max_chars"], s["max_parts"], s["total_chars"], s["max_tokens"]))
    print()
    print("  话题表：%d 个话题（--topics 看全部）" % s["topics"])
    print("  主动消息的耐心系数：%s（越小越黏人）" % s["idle_scale"])
    print("  这一档能说的：%s"
          % ("、".join(s["allow"]) if s["allow"] else "还不能撒娇 / 说想你 / 叫名字"))
    print("  状态文件：%s" % s["state_file"])
    print()
    print("  这一档她该怎么说话：")
    print("    · " + sh.level.get("asked_rule", ""))
    print("    · " + sh.level.get("share_rule", ""))
    print()
    print("  ★ 数字只有一份 —— 在 relation.py 的档位表里。")
    print("    改：控制台「好感度」页，或 python relation.py --stages")


if __name__ == "__main__":
    _cli()
