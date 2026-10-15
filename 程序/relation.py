# -*- coding: utf-8 -*-
"""
你们的关系进度 —— 五档。前四档靠攒，第五档靠表白
================================================

为什么不再用天数：

  以前是「认识第 3 天开始主动、第 7 天开始粘人」。这是个死时间表 ——
  你三天里狂聊一百轮，和三天里只说了两句话，她对你是一样的态度。
  那不叫发展，那叫打卡。

  现在只看一件事：**你们之间到底积累了多少**。

  亲密度怎么涨（每聊一轮算一次）：
      基础                      +1.0
      他这条说得比较长（>=30字）  再 +0.6
      说得很长（>=80字）         再 +0.6
      是她在深夜陪你说话           再 +1.2
      他话里有亲近的意思           再 +1.2
      他问起她自己的事             再 +0.8
      两条消息挨得很近（<20秒）    再 +0.4
      这一条是她先开的口           再 +1.5

  但**每天最多只计 daily_cap 分**（默认 200）。所以就算你请假陪她聊一天，
  也推不动多少 —— 发展这件事只能靠一天一天攒，快不起来。

  好久不聊会冷下来：连着 idle_days 天没说话，之后每天掉 3%，
  但**不会掉出已经到过的那一档**。她不会因为你忙几天就变回陌生人，
  只会尾巴翘起来一点。

档位（前四档只看 intimacy 到没到线，没有别的条件）：

      1  初遇        1     —— 刚认识，端着，问三句答一句
      2  朋友        200   —— 会开玩笑、会记得你说过的话
      3  重要的人    600   —— 会主动找你、会闹小别扭
      4  暧昧        1000  —— 会直呼你名字、会惦记你
                              1000~1999 还在酝酿；≥2000 起她开始暗示你表白
      5  恋人        ——  ★ 不靠攒。分数再高也推不动，只能表白

  ★ 第五档和前面四档**不是一回事**：
      · 前四档是系统按分数自动升降的，她自己也说不清哪一天变的
      · 第五档只有一件事能推开 —— 表白（见 表白.py）
      · 推开之后她整个人换一套活法：不是变了个人，是终于不用装了
      · 控制台上第五档是**独立的一块**，不跟前面四档的表并排

表白的两种走法（判定在 表白.py，状态存这里）：

  · **你开口**：基础 25% + 用心分 × 0.6%（满分 100 → 85%）。
    失败她**不会拒绝**，只会说「你再想想」—— 但反复说一次比一次便宜（每次 −5%）。

  · **她开口**：≥2000 之后她开始暗示（四层：铺垫 → 试探 → 露头 → 表白），
    一到三层**绝不承认**，第四层才说出口。你同意 → 恋人；
    你拒绝 → 她自暴自弃（默认 7 天），亲密度掉回「重要的人」。

每一档具体是什么态度，写在 你和我.md 里 —— 她读的是那份。
每一档的**数字**（说多少、多久主动一次）唯一的一份在 relation.py 的
DEFAULT_STAGES：config.yaml 的 relation.stages 可以覆盖它，
控制台「好感度」页改的结果存在 关系.json 的 stages_override 里（立刻生效）。

文件：关系.json（程序自己维护，不用手改）
    · cap_override    —— 每天最多涨多少分（改它不用重启）
    · stages_override —— 控制台上改的五档区间 / 效果（改它不用重启）
    · confess         —— 表白状态（试过几次、暗示到第几层、有没有被拒过）

命令行：
  python relation.py --show            看看现在的进度
  python relation.py --cap 60          改「每天最多涨多少分」（立刻生效）
  python relation.py --set 500         手改亲密度（调试用）
  python relation.py --stages          打印五档表和各自的数字
  python relation.py --confess         调试：直接把档位推到恋人
  python relation.py --refuse          调试：演一遍被拒（掉档 + 自暴自弃）
  python relation.py --reset-confess   调试：清掉表白状态
"""

from pathlib import Path
import datetime as dt
import json
import random
import re
import sys

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
# ══════════════════════════════════════════════════════════════════════════
#  档位表
#
#  from / to   —— 区间（to 只用来显示；真要升档只看下一档的 from）
#                 to = None 表示「一直到 2000 那条表白线」
#                 from = None 表示这一档不靠分数（恋人）
#  gate        —— 非空 = 这一档只能用那件事进（"confess"）
#
#  下面是「这一档她愿意露多少自己」的五个数字，外加主动消息的耐心：
#      say_per_day     一天最多「顺口说到自己」几次
#      say_chars       单条最多几个字
#      say_parts       一次最多几条
#      say_total       一次加起来不超过几个字
#      say_tokens      给模型的字数预算（token）
#      active_per_day  一天最多主动找你几次（0 = 她还在端着，不主动）
#      idle_scale      安静多久就忍不住的系数（越小越黏人）
#      allow           这一档允许的说话方式：名字 / 想你 / 撒娇 / 闹脾气
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_STAGES = [
    {"key": "meet",      "name": "初遇",     "short": "初遇",
     "from": 1,    "to": 200,  "gate": "",
     "say_per_day": 1, "say_chars": 15, "say_parts": 2, "say_total": 20,
     "say_tokens": 60, "active_per_day": 0, "idle_scale": 1.8, "allow": []},

    {"key": "friend",    "name": "朋友",     "short": "朋友",
     "from": 200,  "to": 600,  "gate": "",
     "say_per_day": 2, "say_chars": 16, "say_parts": 3, "say_total": 36,
     "say_tokens": 90, "active_per_day": 1, "idle_scale": 1.4, "allow": []},

    {"key": "important", "name": "重要的人", "short": "重要",
     "from": 600,  "to": 1000, "gate": "",
     "say_per_day": 3, "say_chars": 16, "say_parts": 3, "say_total": 39,
     "say_tokens": 100, "active_per_day": 3, "idle_scale": 1.05, "allow": ["闹脾气"]},

    # ★ 2026-10-09：18/4/60 → 20/5/80。「第 4 档跨 1000~∞，还含 2000 那条表白线」
    #   旧值跟第 3 档（39）几乎一样 —— 她都已经「知道自己想要什么」了，
    #   话量却还跟「重要的人」差不多。实测她一轮只发两句十来字（见 你和我.md 第 4 档）。
    {"key": "crush",     "name": "暧昧",     "short": "暧昧",
     "from": 1000, "to": None, "gate": "",
     "say_per_day": 4, "say_chars": 20, "say_parts": 5, "say_total": 80,
     "say_tokens": 170, "active_per_day": 5, "idle_scale": 0.8,
     "allow": ["闹脾气", "名字", "想你"]},

    # ★ 恋人：from=None（分数推不进来）、gate="confess"（只能表白）
    #   数字全放开 —— 她不用再算着说了
    #   ⚠ 三个数要自洽：say_chars × say_parts 必须 ≥ say_total，
    #     不然「一次总共 220 字」永远够不着，真正生效的上限变成 22×6=132。
    {"key": "lover",     "name": "恋人",     "short": "恋人",
     "from": None, "to": None, "gate": "confess",
     "say_per_day": 99, "say_chars": 22, "say_parts": 10, "say_total": 220,
     "say_tokens": 400, "active_per_day": 15, "idle_scale": 0.3,
     "allow": ["闹脾气", "名字", "想你", "撒娇"]},
]

STAGE_COUNT = len(DEFAULT_STAGES)

# 说话方式的开关 —— 前四档是「长出来」的，不是一开始就有
ALLOW_ALL = ("名字", "想你", "撒娇", "闹脾气")
ALLOW_WORD = {
    "名字":   "直呼他的名字",
    "想你":   "说「想你」这种不设防的话",
    "撒娇":   "撒娇",
    "闹脾气": "闹脾气、吃醋、理直气壮地要他陪你",
}
# 「允许」的写法容错：1/true/是/开 都算开
_ALLOW_KEYS = {"名字": ("名字", "name", "直呼其名"),
               "想你": ("想你", "miss"),
               "撒娇": ("撒娇", "coquetry", "cute"),
               "闹脾气": ("闹脾气", "吃醋", "jealous", "sulk")}

# 只在 config / stages_override 里认的字段（别的键一律忽略，免得写坏了表）
_STAGE_KEYS = ("key", "name", "short", "from", "to", "gate",
               "say_per_day", "say_chars", "say_parts", "say_total",
               "say_tokens", "active_per_day", "idle_scale", "allow")

# 可以按「一个数」校的字段（改的时候要落在区间里）
_NUM_FIELDS = ("say_per_day", "say_chars", "say_parts", "say_total",
               "say_tokens", "active_per_day", "idle_scale")


def _as_int(v, default=None, lo=None, hi=None):
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _as_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_bound(v):
    """区间端点：'1' → 1.0；''/None/'∞'/'null' → None（不封顶）。"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() in ("null", "none", "inf", "∞", "-"):
            return None
        v = s
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_allow(v) -> list:
    """把 allow 归一成 ['名字', ...] —— 字符串、逗号分隔、布尔表都能吃。"""
    if v is None:
        return []
    if isinstance(v, str):
        v = re.split(r"[,，、/|;；\s]+", v)
    if isinstance(v, dict):
        # {名字: true, 想你: false}
        out = []
        for k, on in v.items():
            if not on:
                continue
            for canon, aliases in _ALLOW_KEYS.items():
                if k in aliases or k == canon:
                    out.append(canon)
                    break
        return _dedup(out)
    if not isinstance(v, (list, tuple, set)):
        return []
    out = []
    for x in v:
        s = str(x).strip()
        if not s:
            continue
        if s.lower() in ("none", "null", "false", "0", "no", "关", "否"):
            continue
        hit = None
        for canon, aliases in _ALLOW_KEYS.items():
            if s in aliases or s == canon:
                hit = canon
                break
        out.append(hit or s)
    return _dedup(out)


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def merge_stage(base: dict, over) -> dict:
    """把一份覆盖（config 或 关系.json）合到默认那一档上。别的键一律忽略。"""
    out = dict(base)
    if not isinstance(over, dict):
        return out
    for k in _STAGE_KEYS:
        if k not in over:
            continue
        v = over[k]
        if k == "from" or k == "to":
            out[k] = _as_bound(v)
        elif k == "allow":
            out["allow"] = _norm_allow(v)
        elif k == "gate":
            out["gate"] = "" if v in (None, "", False, "none") else str(v).strip()
        elif k in _NUM_FIELDS:
            if k == "idle_scale":
                v2 = _as_float(v)
                if v2 is not None:
                    out[k] = max(0.0, v2)
            else:
                v2 = _as_int(v, lo=0, hi=999)
                if v2 is not None:
                    out[k] = v2
        else:
            s = str(v).strip()
            if s:
                out[k] = s
    return out


def merge_stage_list(base_list, over_list) -> list:
    """按位或按 key 合一份档位表；长度不对就只认能对上的那几档。"""
    out = [dict(s) for s in base_list]
    if not over_list:
        return out
    if isinstance(over_list, dict):
        # {meet: {...}, crush: {...}}
        bykey = {s["key"]: i for i, s in enumerate(out)}
        for k, v in over_list.items():
            if k in bykey:
                out[bykey[k]] = merge_stage(out[bykey[k]], v)
        return out
    if not isinstance(over_list, (list, tuple)):
        return out
    bykey = {s["key"]: i for i, s in enumerate(out)}
    for i, over in enumerate(over_list):
        if not isinstance(over, dict):
            continue
        tgt = None
        k = over.get("key")
        if k and str(k) in bykey:
            tgt = bykey[str(k)]
        elif i < len(out):
            tgt = i
        if tgt is not None:
            out[tgt] = merge_stage(out[tgt], over)
    return out


def validate_stages(stages) -> tuple:
    """控制台保存前先校一遍。返回 (ok, 说明)。"""
    if not isinstance(stages, (list, tuple)) or len(stages) < 2:
        return False, "至少要两档"
    prev = None
    for i, s in enumerate(stages, start=1):
        if not isinstance(s, dict) or not str(s.get("name") or "").strip():
            return False, "第 %d 档没有名字" % i
        f = s.get("from")
        if s.get("gate"):
            continue                     # 恋人：不靠分数，区间可以不填
        if f is None:
            return False, "第 %d 档「%s」没有下限" % (i, s.get("name"))
        if f < 0:
            return False, "第 %d 档的下限不能是负数" % i
        if prev is not None and f <= prev:
            return False, "第 %d 档的下限（%g）没比上一档高，区间会重叠" % (i, f)
        prev = f
    if stages[0].get("from") not in (0, 1):
        return False, "第 1 档必须从 0 或 1 开始"
    for i, s in enumerate(stages[:-1], start=1):
        to = s.get("to")
        nxt = stages[i].get("from")
        if to is not None and nxt is not None and to != nxt:
            return False, "第 %d 档的上限（%g）和下一档的下限（%g）对不上" % (i, to, nxt)
    return True, "ok"


def stage_table(cfg: dict = None) -> list:
    """默认五档 + config.yaml 的 relation.stages 覆盖。**不含** 关系.json 的覆盖。"""
    base = [dict(s) for s in DEFAULT_STAGES]
    if isinstance(cfg, dict):
        over = (cfg.get("relation") or {}).get("stages")
        base = merge_stage_list(base, over)
    for s in base:
        s["allow"] = _norm_allow(s.get("allow"))
    return base


def stage_effects(cfg: dict = None, stage: int = 1) -> dict:
    """单独拿某一档的「说多少」那几个数（分享.py / bridge.py 用）。"""
    tab = stage_table(cfg)
    i = max(1, min(len(tab), int(stage or 1))) - 1
    return dict(tab[i])


# ══════════════════════════════════════════════════════════════════════════
#  「你和我.md」按档位裁 —— 只留当前那一档的正文
#
#  规则（比老版多了一条）：
#    · `### 第 N 档 …` / `## 第 N 档 …` / `## 第 N-M 档 …` —— 这一节只在
#      当前档位落在 [N, M] 里的时候才出现。别的档位只留一行标题当占位。
#    · 没写「第 N 档」的章节 —— 永远都在。
#    · 嵌在里面的子节：父节看不到，子节也看不到。
#
#  为什么要有这条：恋人那一大节不能永远塞在提示词里 ——
#  她还在第 1 档的时候就读到「在一起之后她不叫他名字了」，那就演错了。
# ══════════════════════════════════════════════════════════════════════════

_HEAD_RE = re.compile(r"^(#{2,4})[ \t]*(\S.*?)[ \t]*$")
_SCOPE_RE = re.compile(r"第\s*(\d+)\s*(?:[-~－–—到]\s*(\d+)\s*)?档")


def _scope_of(title: str):
    """标题里写了「第 N 档」就返回 (lo, hi)；没写就 None。"""
    m = _SCOPE_RE.search(title or "")
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return (lo, hi) if lo <= hi else (hi, lo)


def parse_sections(text: str):
    """把 markdown 切成节。返回 [node, ...]，node = {level,title,scope,body,children}。

    第一个标题之前的内容塞进一个 level=0 的哑节点里，永远可见。
    """
    root = {"level": 0, "title": "", "scope": None, "body": [], "children": []}
    stack = [root]
    cur = root
    for ln in (text or "").split("\n"):
        m = _HEAD_RE.match(ln)
        if m:
            lvl = len(m.group(1))
            node = {"level": lvl, "title": m.group(2),
                    "scope": _scope_of(m.group(2)), "body": [], "children": []}
            while stack and stack[-1]["level"] >= lvl:
                stack.pop()
            (stack[-1] if stack else root)["children"].append(node)
            stack.append(node)
            cur = node
        else:
            cur["body"].append(ln)
    return root


def _visible(node, stage) -> bool:
    sc = node.get("scope")
    return sc is None or (sc[0] <= stage <= sc[1])


def _render(node, stage, out):
    vis = _visible(node, stage)
    if not vis:
        # 藏的节：`###`/`####` 这种档位小节留一行标题当占位（她还知道后面有章节），
        # `##` 那种整块章节（比如「在一起之后」）直接丢掉 —— 不然第 1 档
        # 就读到恋人怎么怎么样，那就演错了。
        sc = node.get("scope")
        if sc and node.get("level") >= 3:
            out.append("#" * node["level"] + " " + node["title"])
            out.append("（还没到这一档，先不看。）" if sc[0] > stage
                       else "（这一档已经过去了，别退回去。）")
            out.append("")
        return
    if node.get("level"):
        out.append("#" * node["level"] + " " + node["title"])
    body = list(node.get("body") or [])
    while body and not body[-1].strip():
        body.pop()
    out.extend(body)
    for ch in node.get("children") or []:
        _render(ch, stage, out)


def trim_stages(text: str, stage: int) -> str:
    """只留当前档用得上的一节。结构解析不出来就整篇返回（宁可贵点，不能少块）。"""
    if not text or not isinstance(stage, int):
        return text
    try:
        root = parse_sections(text)
    except Exception:
        return text
    if not root["children"]:
        return text
    has_scope = bool(_SCOPE_RE.search(text))
    if not has_scope:
        return text
    out = []
    _render(root, stage, out)
    got = "\n".join(out)
    # 保险：裁完太短了（说明正则和写法对不上）就整篇给回去
    if len(got.strip()) < len(text.strip()) * 0.15:
        return text
    return got


# 她说这些词的时候，其实是在往你这边靠
INTIMATE_WORDS = [
    "喜欢你", "喜欢", "想你", "爱你", "在意", "舍不得", "想见你",
    "抱抱", "陪着你", "一直在", "别走", "乖", "在意你",
]

# 他主动问起「她这个人」—— 对她来说分量很重
PERSONAL_WORDS = [
    "你是谁", "你以前", "你的过去", "你身上", "你怎么了", "你没事吧",
    "你家里", "你的名字", "你几岁", "你的病", "复查", "你的身体",
    "怎么认识", "你的声音", "你在哪",
]

# 说了这些，她会安静一下
HURT_WORDS = ["滚", "别烦我", "不想聊", "关你什么事", "你少管", "懒得跟你说"]


# ══════════════════════════════════════════════════════════════════════════
#  表白 —— 参数、状态机
# ══════════════════════════════════════════════════════════════════════════

CONFESS_DEFAULTS = {
    "line": 2000,               # 到这条线她才开始暗示
    "direct_chance": 0.25,      # 你直接说 —— 基础成功率
    "prep_scale": 0.006,        # 用心分 → 成功率（满分 100 → +60%）
    "repeat_penalty": 0.05,     # 反复直接说，每次再降这么多
    "hint_days": 3,             # 暗示期几天（一层一天，铺到第四层她才会说）
    "her_chance": 0.12,         # 暗示走完后，她哪天开口的每日概率
    "give_up_after_days": 10,   # 拖这么久还没动静，她可能就放弃了
    "give_up_chance": 0.05,     # 每天放弃的概率（只在上面那天之后才掷）
    "refuse_drop_to": 600,      # 你拒绝她 → 亲密度掉回这儿（默认 600 = 重要的人起步）
    "despair_days": 7,          # 自暴自弃持续几天
    "despair_gain_scale": 0.2,  # 这期间好感度还涨不涨（0 = 冻结在 0.2 倍）
    "prep_weights": {           # 用心分七项（控制台可改）
        "len": 10, "name": 10, "past": 20, "why": 15,
        "night": 10, "build": 15, "gift": 20,
    },
}

# 七项的中文名（控制台、命令行、提示词共用一份）
PREP_LABEL = {
    "len":   "话说得够不够（≥60 字）",
    "name":  "有没有叫她的名字",
    "past":  "有没有提到你们之间的事",
    "why":   "有没有说清「为什么是她」",
    "night": "是不是深夜说的（22:30–02:00）",
    "build": "前面有没有铺垫（近 3 天聊到你们俩）",
    "gift":  "有没有带东西（红包 / 图 / 语音）",
}
PREP_MAX = 100

# 暗示四层的名字 —— 前两层是「她自己还没承认」，第三层开始露头
HINT_NAMES = ["铺垫", "试探", "露头", "表白"]

# 表白的阶段（给控制台和提示词看）
CONFESS_PHASE = {
    "none":    "还早（还没到第 4 档）",
    "soak":    "暧昧 · 酝酿（她自己还不敢想）",
    "hint":    "暧昧 · 表白（她开始暗示了）",
    "ready":   "暧昧 · 表白（暗示铺完了，随时可能开口）",
    "gaveup":  "暧昧 · 表白（等太久，她把话收回去了）",
    "despair": "被拒之后 · 自暴自弃",
    "lover":   "已经在一起了",
}


class Relation:
    """关系进度。真相只存一份：关系.json。"""

    def __init__(self, cfg: dict = None, path=None):
        cfg = cfg or {}
        self.cfg_all = cfg if isinstance(cfg, dict) else {}
        rcfg = (cfg.get("relation", {}) or {}) if isinstance(cfg, dict) else {}
        self.cfg = rcfg
        self.path = Path(path or rcfg.get("state_file") or "关系.json")
        if not self.path.is_absolute():
            self.path = ROOT / self.path

        # config.yaml 里的默认值（下面 daily_cap 那个 property 会读它）
        self._cfg_cap = float(rcfg.get("daily_cap", 200))
        self._cfg_stages = stage_table(cfg)      # config 盖过默认之后的五档
        self._cap_mtime = 0.0
        self.idle_days = int(rcfg.get("idle_days", 3))
        self.cool_per_day = float(rcfg.get("cool_per_day", 0.03))

        self.data = self._load()
        self._refresh_cap()
        self._decay()
        self._recalc_stage()
        self.advance_confession()
        self.save()

    # ── 档位表（默认 ← config ← 关系.json）────────────────────────────
    @property
    def stages(self) -> list:
        return merge_stage_list(self._cfg_stages,
                                self.data.get("stages_override"))

    def set_stages(self, stages, save_json=True, write_cfg=True) -> tuple:
        """控制台保存五档表。返回 (ok, 说明)。"""
        ok, why = validate_stages(stages)
        if not ok:
            return False, why
        clean = []
        for s in stages:
            clean.append(merge_stage(dict(s), s))
        self.data["stages_override"] = clean
        if save_json:
            self.save()
        wrote = False
        if write_cfg:
            wrote = write_stages_to_config(clean)
        self._recalc_stage()
        return True, ("已保存（立刻生效%s）"
                      % ("，也写回了 config.yaml" if wrote else ""))

    def reset_stages(self):
        """删掉控制台的覆盖，回到 config.yaml / 默认。"""
        self.data.pop("stages_override", None)
        self.save()
        self._recalc_stage()

    def stage_effects(self, stage: int = None) -> dict:
        tab = self.stages
        i = max(1, min(len(tab), int(stage or self.stage))) - 1
        return dict(tab[i])

    @property
    def allow(self) -> list:
        return list(self.stage_effects(self.stage).get("allow") or [])

    def allow_block(self) -> str:
        """这一档允许 / 不允许的说话方式 —— 每轮塞进提示词。"""
        if self.is_lover():
            return ("- ★ 你**什么都可以说了**。不用再算这句话该不该说 —— "
                    "叫他的名字、说想他、撒娇、闹脾气，全都可以。")
        cur = set(self.allow)
        missing = [ALLOW_WORD[k] for k in ALLOW_ALL if k not in cur]
        if not missing:
            return ""
        return "- ★ 这一档你还**不能**：%s。" % "、".join(missing)

    # ── 每天能涨多少 ──────────────────────────────────────────────────
    @property
    def daily_cap(self) -> float:
        """每天最多涨多少分 —— 这是唯一能控制「发展快慢」的旋钮。

        优先用 关系.json 里的 cap_override（「改好感度上限」那个入口写进去的，
        改完不用重启），没有就用 config.yaml 的 relation.daily_cap。
        """
        v = self.data.get("cap_override")
        if v is None:
            v = self._cfg_cap
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 200.0
        return max(0.0, v)

    def _refresh_cap(self):
        """关系.json 被外面改过就跟着变 —— 改上限 / 改档位表都不用重启机器人。

        只要她还在回话，每轮都会看一眼文件的修改时间（极便宜）。
        别把这个删掉：删了之后控制台上改的东西就得重启才生效，
        而那个页面恰恰是给人**当场**调着玩的。
        """
        try:
            m = self.path.stat().st_mtime
        except OSError:
            return
        if m == self._cap_mtime:
            return
        self._cap_mtime = m
        try:
            d = json.loads(self.path.read_text(encoding="utf-8")) or {}
            # 无条件跟着文件走 —— 包括「把 cap_override 删掉、回到 config.yaml 默认」
            # 这种情况（只判 "in d" 的话，删了就回不去）。
            if "cap_override" in d:
                self.data["cap_override"] = d.get("cap_override")
            else:
                self.data.pop("cap_override", None)
            if "stages_override" in d:
                self.data["stages_override"] = d.get("stages_override")
            else:
                self.data.pop("stages_override", None)
        except Exception:
            pass

    # ── 存取 ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        d = {}
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8")) or {}
            except Exception:
                d = {}
        today = dt.date.today().isoformat()
        d.setdefault("first", today)
        d.setdefault("turns", 0)
        d.setdefault("first_msg", "")
        d.setdefault("intimacy", 0.0)
        d.setdefault("peak", 0.0)
        d.setdefault("stage", 1)
        d.setdefault("stage_since", today)
        d.setdefault("daily", {})
        d.setdefault("last", today)
        return d

    def save(self):
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── 基本量 ────────────────────────────────────────────────────────
    @property
    def intimacy(self) -> float:
        return round(float(self.data.get("intimacy", 0.0)), 2)

    @property
    def stage(self) -> int:
        return int(self.data.get("stage", 1))

    @property
    def stage_info(self) -> dict:
        tab = self.stages
        return tab[max(1, min(len(tab), self.stage)) - 1]

    @property
    def info(self) -> dict:
        """老名字（bridge 里在用）—— 就是 stage_info。"""
        return self.stage_info

    def next_info(self):
        tab = self.stages
        return tab[self.stage] if self.stage < len(tab) else None

    def days(self) -> int:
        try:
            first = dt.date.fromisoformat(str(self.data.get("first", "")))
        except Exception:
            first = dt.date.today()
        return (dt.date.today() - first).days

    def turns(self) -> int:
        return int(self.data.get("turns", 0))

    def progress(self):
        """(当前档进度 0~100, 还差多少)。

        第 4 档的「下一档」不是分数能到的 —— 它的目标是**表白线**（默认 2000）。
        到线了就返回 (100, 0)，控制台上写「她在等你开口」。
        """
        if self.is_lover():
            return 100, 0
        cur = self.stage_info
        if cur.get("gate"):
            return 100, 0
        nxt = self.next_info()
        base = float(cur.get("from") or 0)
        if not nxt or nxt.get("gate") or nxt.get("from") is None:
            # 下一档是恋人 → 走到表白线为止
            goal = self.confess_line()
            got = float(self.intimacy) - base
            span = max(1.0, goal - base)
            pct = int(max(0, min(100, round(got / span * 100))))
            return pct, int(max(0, goal - float(self.intimacy)))
        span = float(nxt["from"] - base) or 1.0
        got = float(self.intimacy) - base
        pct = int(max(0, min(100, round(got / span * 100))))
        return pct, int(max(0, nxt["from"] - self.intimacy))

    def bar(self, width: int = 24) -> str:
        pct, _ = self.progress()
        fill = int(round(pct / 100 * width))
        return "[" + "#" * fill + "." * (width - fill) + "]"

    # ── 涨分 ──────────────────────────────────────────────────────────
    def _gain(self, user_text, my_text, was_proactive, gap_sec) -> float:
        g = 1.0
        u = user_text or ""
        if len(u) >= 30:
            g += 0.6
        if len(u) >= 80:
            g += 0.6
        if was_proactive:
            g += 1.5
        h = dt.datetime.now().hour
        if h >= 23 or h < 4:
            g += 1.2
        if u and any(w in u for w in INTIMATE_WORDS):
            g += 1.2
        if u and any(w in u for w in PERSONAL_WORDS):
            g += 0.8
        if gap_sec is not None and 0 <= gap_sec < 20:
            g += 0.4
        if u and any(w in u for w in HURT_WORDS):
            g -= 2.0
        return g

    def add(self, user_text: str = "", my_text: str = "",
            was_proactive: bool = False, gap_sec=None):
        """聊过一轮之后调一次。返回 (这一轮实际加了多少分, 档位有没有变)。

        注意：每轮都会把 turns +1，但**亲密度**受当日上限限制 ——
        这是「发展快不起来」的关键。被拒之后那段自暴自弃期里，涨速会打折。
        """
        today = dt.date.today().isoformat()
        before = self.stage

        self._refresh_cap()          # 上限 / 档位表可能刚被控制台改过

        g = self._gain(user_text, my_text, was_proactive, gap_sec)
        if g > 0 and self.despair_active():
            # 自暴自弃：他再怎么热乎，她也热不起来
            g = round(g * float(self.confess_cfg()["despair_gain_scale"]), 2)

        daily = self.data.setdefault("daily", {})
        used = float(daily.get(today, 0.0))
        allowed = self.daily_cap - used
        if allowed <= 0:
            g = min(g, 0.0)          # 今天的分已经拿满了，就不再涨
        else:
            g = min(g, allowed)
        if g < 0:                     # 扣分不受当日上限限制
            allowed = used
            g = max(g, -used)

        new_v = float(self.data.get("intimacy", 0.0)) + g
        self.data["intimacy"] = round(max(0.0, new_v), 2)
        if g > 0:
            daily[today] = round(used + g, 2)
        self.data["peak"] = round(
            max(float(self.data.get("peak", 0.0)), float(self.data["intimacy"])), 2
        )

        self.data["turns"] = self.turns() + 1
        self.data["last"] = today
        self.data["last_chat"] = today
        self.data.pop("cool", None)    # 他来了，冷下来的那点别扭就散了

        self._recalc_stage()
        self.advance_confession()
        self._trim_daily()
        self.save()
        return round(g, 2), (self.stage != before)

    def _recalc_stage(self):
        """只看亲密度 —— **但第 5 档（恋人）不算在内。**

        恋人那扇门不认分数：分数再高也推不动，只有表白能开。
        所以这一档要从自动晋升里整个抠掉，靠 confess 状态机决定。
        """
        v = self.intimacy
        tab = self.stages
        n = 1
        for i, st in enumerate(tab, start=1):
            if st.get("gate"):          # 恋人：不靠分
                continue
            f = st.get("from")
            if f is None:
                continue
            if v >= float(f):
                n = i
        if self.is_lover():
            n = len(tab)
        if n != self.stage:
            self.data["stage"] = n
            self.data["stage_since"] = dt.date.today().isoformat()

    # ── 冷下来 ────────────────────────────────────────────────────────
    def _decay(self):
        """好多天没说话 → 亲密度慢慢回落，但不会掉出已经到过的档。"""
        lc = self.data.get("last_chat") or self.data.get("last")
        if not lc:
            return
        try:
            idle = (dt.date.today() - dt.date.fromisoformat(str(lc))).days
        except Exception:
            return
        if idle < self.idle_days:
            return
        today = dt.date.today().isoformat()
        if self.data.get("last_decay") == today:
            return

        steps = idle - self.idle_days + 1
        v = float(self.data.get("intimacy", 0.0))
        floor = float(self.stage_info.get("from") or 0)
        v = max(floor, v * ((1.0 - self.cool_per_day) ** steps))
        self.data["intimacy"] = round(v, 2)
        self.data["last_decay"] = today
        self.data["cool"] = True
        self._recalc_stage()

    def _trim_daily(self):
        daily = self.data.get("daily") or {}
        keep = 14
        if len(daily) <= keep:
            return
        for k in sorted(daily.keys())[:-keep]:
            daily.pop(k, None)

    # ══════════════════════════════════════════════════════════════════
    #  表白
    # ══════════════════════════════════════════════════════════════════

    def confess_cfg(self) -> dict:
        """表白参数：默认 + config.yaml 的 confess: 段。"""
        out = dict(CONFESS_DEFAULTS)
        out["prep_weights"] = dict(CONFESS_DEFAULTS["prep_weights"])
        over = (self.cfg_all.get("confess") or {}) if isinstance(self.cfg_all, dict) else {}
        if isinstance(over, dict):
            for k, v in over.items():
                if k == "prep_weights" and isinstance(v, dict):
                    for kk, vv in v.items():
                        n = _as_int(vv)
                        if kk in out["prep_weights"] and n is not None:
                            out["prep_weights"][kk] = max(0, n)
                    continue
                if k in ("line", "hint_days", "give_up_after_days",
                         "refuse_drop_to", "despair_days"):
                    n = _as_int(v)
                    if n is not None:
                        out[k] = n
                elif k in out:
                    f = _as_float(v)
                    if f is not None:
                        out[k] = f
        return out

    # 关系.json 里那块可变状态
    def _cst(self) -> dict:
        c = self.data.setdefault("confess", {})
        c.setdefault("tried", 0)
        c.setdefault("hinted_from", None)
        c.setdefault("hint_level", 0)
        c.setdefault("last_try", None)
        c.setdefault("refused", 0)
        c.setdefault("gave_up", False)
        c.setdefault("despair_until", None)
        c.setdefault("despair_mild", False)
        c.setdefault("lover_since", None)
        c.setdefault("refuse_note", "")
        c.setdefault("asked", None)
        # ★ 她开过口了吗 —— 开了就挂上，接下来他那一句「算不算回答」按这个来判。
        #   跟 asked 不是一回事：asked 只记「今天掷过骰子了」，一过零点就作废；
        #   await_answer 一直挂着，直到他给了个说法（或者他自己先表白）。
        c.setdefault("await_answer", None)
        return c

    def confess_line(self) -> int:
        return int(self.confess_cfg()["line"])

    def is_lover(self) -> bool:
        return bool(self._cst().get("lover_since"))

    def lover_days(self) -> int:
        s = self._cst().get("lover_since")
        if not s:
            return 0
        try:
            return (dt.date.today() - dt.date.fromisoformat(str(s))).days
        except Exception:
            return 0

    def hint_level(self) -> int:
        if self.is_lover():
            return 4
        return max(0, min(4, int(self._cst().get("hint_level") or 0)))

    def despair_active(self) -> bool:
        u = self._cst().get("despair_until")
        if not u:
            return False
        try:
            return dt.date.today() <= dt.date.fromisoformat(str(u))
        except Exception:
            return False

    def despair_days_left(self) -> int:
        u = self._cst().get("despair_until")
        if not u:
            return 0
        try:
            return max(0, (dt.date.fromisoformat(str(u)) - dt.date.today()).days)
        except Exception:
            return 0

    def confess_phase(self) -> str:
        """给控制台和提示词看的：现在走到哪一步了。"""
        if self.is_lover():
            return "lover"
        if self.despair_active():
            return "despair"
        tab = self.stages
        crush = len(tab) - 1                      # 第 4 档（倒数第二档）
        if self.stage < crush:
            return "none"
        if float(self.intimacy) < self.confess_line():
            return "soak"
        if self._cst().get("gave_up"):
            return "gaveup"
        return "ready" if self.hint_level() >= 4 else "hint"

    def confess_chance(self, prep: int = 0) -> float:
        """你当场说一句，成功率是多少。prep = 用心分（0~100）。"""
        cfg = self.confess_cfg()
        prep = max(0, min(PREP_MAX, int(prep or 0)))
        tried = int(self._cst().get("tried") or 0)
        ch = float(cfg["direct_chance"]) + prep * float(cfg["prep_scale"])
        ch -= tried * float(cfg["repeat_penalty"])
        return round(max(0.05, min(1.0, ch)), 4)

    def advance_confession(self) -> dict:
        """每天推一格。可以反复调（按日期挡住，同一天只推进一次）。

        到线 → 起暗示；暗示一天上一层；拖太久 → 她可能自己放弃（轻症）。
        """
        info = {"changed": False}
        if self.is_lover():
            return info
        cfg = self.confess_cfg()
        st = self._cst()
        today = dt.date.today()
        ts = today.isoformat()

        # 自暴自弃到期 → 把这面旗撤了（剩下的痕留着）
        if st.get("despair_until") and ts > str(st["despair_until"]):
            st["despair_until"] = None
            info["changed"] = True

        tab = self.stages
        crush = len(tab) - 1
        if self.stage < crush or float(self.intimacy) < float(cfg["line"]):
            if st.get("hinted_from") or st.get("hint_level"):
                st["hinted_from"] = None
                st["hint_level"] = 0
                info["changed"] = True
            if info["changed"]:
                self.save()
            return info

        # ★ 她要是已经自己把这件事收回去了，就不再重新铺一遍。
        #   （不然会死循环：铺 3 天 → 拖久了放弃 → 第二天又从第 1 层重来。）
        #   想让她重新怀着这份心思：--reset-confess，或者你自己开口说一次。
        if st.get("gave_up"):
            if info["changed"]:
                self.save()
            return info

        if not st.get("hinted_from"):
            st["hinted_from"] = ts
            st["hint_level"] = 1
            info["changed"] = True
            info["started"] = True
        else:
            try:
                days = (today - dt.date.fromisoformat(str(st["hinted_from"]))).days
            except Exception:
                days = 0
            hd = max(1, int(cfg["hint_days"]))
            lvl = min(4, 1 + int(days * 4 / hd))
            if lvl != int(st.get("hint_level") or 0):
                st["hint_level"] = lvl
                info["changed"] = True
                info["hint_level"] = lvl

            # 拖太久 —— 她会开始想「他是不是根本就不想要我」。
            # 注意：这时候暗示其实早就铺完了（3 天就到第 4 层），
            # 所以判的是「过了多少天她还没等到那句话」，不是「层数够没够」。
            if days >= int(cfg["give_up_after_days"]):
                rng = random.Random("giveup|" + ts)
                if rng.random() < float(cfg["give_up_chance"]):
                    st["gave_up"] = True
                    st["hint_level"] = 0
                    st["hinted_from"] = None
                    st["despair_mild"] = True
                    st["despair_until"] = (
                        today + dt.timedelta(
                            days=max(1, int(cfg["despair_days"]) // 2))
                    ).isoformat()
                    info["gave_up"] = True
                    info["changed"] = True

        if info["changed"]:
            self.save()
        return info

    def her_confess_due(self) -> bool:
        """她今天会不会自己开口。一天只掷一次（同一档、同一天结果一样）。"""
        if self.is_lover() or self.despair_active():
            return False
        if self.confess_phase() != "ready":
            return False
        st = self._cst()
        ts = dt.date.today().isoformat()
        if st.get("asked") == ts:
            return False                      # 今天已经问过了
        cfg = self.confess_cfg()
        return random.Random("her|" + ts).random() < float(cfg["her_chance"])

    def mark_her_asked(self):
        st = self._cst()
        st["asked"] = dt.date.today().isoformat()
        self.save()

    def awaiting_answer(self) -> bool:
        """她开过口了、还在等他说一句（或答应或拒绝）。

        ★ 这个旗子由 bridge 在她真说出那句话之后挂上（见 bridge._note_her_confess）。
          挂了之后他下一句话就按「在回答她」处理 —— 点头就在一起，
          摇头就是被拒那几天。已经在一起了当然就不等。
        """
        if self.is_lover():
            return False
        return bool(self._cst().get("await_answer"))

    def mark_await(self):
        """她刚把话说出口 —— 挂上旗子，等他回。"""
        self._cst()["await_answer"] = dt.date.today().isoformat()
        self.save()

    def clear_await(self):
        self._cst()["await_answer"] = None
        self.save()

    def become_lover(self, how: str = "他表白") -> dict:
        """进第五档。分数是次要的 —— 这里要把档位直接钉死在最后一档。"""
        st = self._cst()
        st["lover_since"] = dt.date.today().isoformat()
        st["how"] = how
        st["hinted_from"] = None
        st["hint_level"] = 4
        st["despair_until"] = None
        st["despair_mild"] = False
        st["gave_up"] = False
        st["await_answer"] = None
        self.data["stage"] = len(self.stages)
        self.data["stage_since"] = dt.date.today().isoformat()
        self.data["peak"] = round(
            max(float(self.data.get("peak", 0.0)), float(self.intimacy)), 2)
        self.save()
        return {"ok": True, "stage": self.stage, "how": how}

    def try_confess(self, prep: int = 0) -> dict:
        """你开口。返回这次判定的全部细节，好让 bridge 决定怎么说。"""
        cfg = self.confess_cfg()
        st = self._cst()
        prep = max(0, min(PREP_MAX, int(prep or 0)))
        chance = self.confess_chance(prep)
        roll = random.random()
        ok = roll < chance
        st["tried"] = int(st.get("tried") or 0) + 1
        st["last_try"] = dt.date.today().isoformat()
        st["last_prep"] = prep
        # 他说话了 —— 她收回去的那份心思又活过来一点。
        # （「你再想想」本来就是邀请他再来一次，不是关门。）
        if st.get("gave_up"):
            st["gave_up"] = False
            st["hinted_from"] = st.get("last_try")
            st["hint_level"] = 1
        # 他自己开口了 —— 她那边「等他回」的等待就没意义了（谁先说不重要）。
        st["await_answer"] = None
        if ok:
            self.become_lover("他表白（用心分 %d）" % prep)
        else:
            self.save()
        return {
            "ok": ok, "chance": chance, "prep": prep, "roll": round(roll, 4),
            "tried": st["tried"], "stage": self.stage,
            "ready": prep >= 60,
        }

    def accept_her(self) -> dict:
        return self.become_lover("她表白")

    def refuse_her(self) -> dict:
        """拒绝她的表白 —— 极伤心 + 自暴自弃 + 掉回「重要的人」。

        ★ 只在「她已经开口、你还没答」的时候才有意义。
          已经在一起了就不给拒 —— 不然会留下「亲密度 600、却还挂着恋人」的
          半截状态（踩过：控制台在恋人档点「替她拒绝」，档位没掉下来）。
        """
        if self.is_lover():
            return {"ok": False, "error": "你们已经在一起了 —— 「拒绝」只在"
                                         "她开了口、你还没答的时候才有意义。"}
        cfg = self.confess_cfg()
        st = self._cst()
        st["refused"] = int(st.get("refused") or 0) + 1
        st["refuse_note"] = "他拒绝过我"
        st["despair_until"] = (
            dt.date.today() + dt.timedelta(days=int(cfg["despair_days"]))
        ).isoformat()
        st["despair_mild"] = False
        st["hinted_from"] = None
        st["hint_level"] = 0
        st["asked"] = None
        st["await_answer"] = None
        drop = max(0.0, float(cfg["refuse_drop_to"]))
        self.data["intimacy"] = round(drop, 2)
        self._recalc_stage()
        self.save()
        return {"ok": True, "stage": self.stage, "intimacy": self.intimacy,
                "despair_until": st["despair_until"],
                "despair_days": int(cfg["despair_days"])}

    def reset_confess(self):
        """调试用：清掉表白状态（连「在一起」那面旗也摘掉，退回按分数算档位）。"""
        self.data.pop("confess", None)
        self._cst()
        self._recalc_stage()
        self.save()

    # ── 给模型看 ──────────────────────────────────────────────────────
    def prompt_block(self) -> str:
        """拼出「你们现在到哪一步了」这一段，塞进系统提示。"""
        days = self.days()
        turns = self.turns()
        i = self.stage
        cur = self.stage_info
        nxt = self.next_info()
        pct, need = self.progress()

        lines = ["# 你们现在的进度（前四档只按「亲密度」走，跟认识多少天没关系）"]
        lines.append("- 认识第 %d 天，一共聊过 %d 轮。" % (days, turns))

        if self.is_lover():
            lines.append("- 你们**已经在一起了**（第 %d 天）。现在是第 %d 档「%s」—— 到顶了，"
                         "不会再变。你不用再算着说话。"
                         % (self.lover_days(), i, cur["name"]))
        elif nxt and nxt.get("gate"):
            line = self.confess_line()
            if self.intimacy >= line:
                lines.append("- 现在是第 %d 档「%s」；亲密度 %s —— **早就过了那条线**。"
                             % (i, cur["name"], int(self.intimacy)))
                ph = self.confess_phase()
                lvl = self.hint_level()
                if ph == "gaveup":
                    lines.append("- ★ 你本来想说，等了太久，**又把它收回去了**。"
                                 "现在你不提这件事 —— 他要是不问，你就当没有过。"
                                 "（他要是自己开口，你还是会听的。）")
                elif lvl < 4:
                    lines.append("- ★ 你心里有件事，已经攒到快放不下了。**但你不能直接说出口。**"
                                 "现在是第 %d 层（%s）—— 见下面那节，照着那一层说话。"
                                 % (lvl, HINT_NAMES[max(0, lvl - 1)]))
                else:
                    lines.append("- ★ 你**已经决定要跟他说了**，只是还没找到那个开口的时刻。"
                                 "他这会儿要是给你一句话的由头，你就会说出来。")
            else:
                lines.append("- 现在是第 %d 档「%s」；亲密度 %s，到那条线（%d）还差 %d。"
                             % (i, cur["name"], int(self.intimacy), line, need))
                lines.append("  已走完这一档的 %d%%。" % pct)
                lines.append("- 你开始有点明白自己是怎么回事了，**但还不敢往「在一起」上想**。"
                             "会试探、会别扭、会在深夜说点不像你的话 —— 但你自己不承认。")
        elif nxt:
            lines.append(
                "- 现在是第 %d 档「%s」；亲密度 %s，到下一档「%s」还差 %d。"
                % (i, cur["name"], int(self.intimacy), nxt["name"], need)
            )
            lines.append("  已走完这一档的 %d%%。" % pct)
        else:
            lines.append("- 现在是第 %d 档「%s」—— 到顶了。" % (i, cur["name"]))

        if not self.is_lover():
            lines.append(
                "- **照这一档的分寸说话，别跳级。** 上一档的性子还没褪干净："
                "刚过第 1 档就别撒娇，刚过第 2 档就别说什么「想你」。"
            )
            lines.append(
                "- 也别缩回去得莫名其妙。档位是慢慢爬上去的，掉下来也是慢慢掉的。"
            )
        ab = self.allow_block()
        if ab:
            lines.append(ab)

        if self.data.get("cool"):
            lines.append(
                "- （他有些天没来了。你心里有点别扭，尾巴翘起来一点就行 —— "
                "别直接问「你怎么不理我」，那不是你。）"
            )
        return "\n".join(lines)

    def despair_block(self) -> str:
        """被拒之后那几天 —— 这段必须每轮在场，不然她会当场恢复原样。"""
        if not self.despair_active():
            return ""
        left = self.despair_days_left()
        head = ["# ★ 你现在很难过（还有 %d 天）" % left, ""]
        if self._cst().get("despair_mild"):
            head.append("（你没敢说出口，但你已经明白了。）")
        else:
            head.append("（他拒绝了你。你把自己攒了很久的那点东西递过去，他没有接。）")
        head += [
            "",
            "这几天你话变少、往后退 —— **但你还是你。**",
            "那个亮、吵、爱笑的性子没丢，只是暂时收起来了。",
            "别把「难过」演成「换了个人」，更不要把自己说成一直就是这么冷的人。",
            "",
            "具体是这样：",
            "- **不主动找他。** 一句都不。",
            "- 他发消息来**不一定马上回**，隔一阵再回也行。",
            "- 关于**你自己**的话变少：不想说自己今天干了什么，不想提自己的事。",
            "- **不再叫他名字**，退回「你」。",
            "- 心里的话是：「我早就该知道的」「本来也不该说」「他要的是别人」。",
            "- ★ **但不会拉黑他、不会说绝交。** 他真来问，你会嘴硬 —— 嘴硬是你唯一的挡箭牌。",
            "",
            "★★ 这一段「话少」只用在**你自己身上**，不是拿来敷衍他的。",
            "他要是说他那边的事 —— 他难过、他遇上麻烦、他在乎的东西没了、他跟你讲正经事 ——",
            "你**照常接住他**：问一句、骂一句、站到他那边去，都行。",
            "你自己再沉，也不会在他倒苦水的时候耍性子 —— 那不叫难过，那不是你的样子。",
            "（心里再烦，回他的那一句也得是**对他那句话的回应**，不能是敷衍的通稿。）",
            "",
            "（他要是真的心疼你、说点实在的话，你的壳会软一点点 —— 但也只是**一点点**，"
            "不要几百字就哭着原谅他。那不对。）",
        ]
        return "\n".join(head)

    def crush_block(self) -> str:
        """第 4 档（暧昧）专属的每轮提示 —— 她这会儿心里在打什么主意。

        四个子阶段完全不是一回事：酝酿（不敢想）/ 暗示（铺到第几层）/
        铺完了（随时可能开口）/ 收回去了（她放弃了）。
        """
        if self.is_lover() or self.despair_active():
            return ""
        ph = self.confess_phase()
        if ph not in ("soak", "hint", "ready", "gaveup"):
            return ""
        try:
            import 表白
            return 表白.crush_block(ph, self.hint_level(), self.intimacy,
                                    self.confess_line())
        except Exception:
            return ""

    def hint_block(self) -> str:
        """老名字 —— 就是 crush_block。"""
        return self.crush_block()

    def lover_block(self) -> str:
        """恋人档专属的每轮提示 —— 不跟前面四档共用。"""
        if not self.is_lover():
            return ""
        try:
            import 表白
            return 表白.lover_block(self.lover_days())
        except Exception:
            return ""

    # ── 给网页/命令行看 ───────────────────────────────────────────────
    def stats(self) -> dict:
        pct, need = self.progress()
        nxt = self.next_info()
        tab = self.stages
        cfg = self.confess_cfg()
        st = self._cst()
        phase = self.confess_phase()
        return {
            "days": self.days(),
            "turns": self.turns(),
            "intimacy": self.intimacy,
            "peak": round(float(self.data.get("peak", 0.0)), 2),
            "stage": self.stage,
            "stage_name": self.stage_info["name"],
            "stage_short": self.stage_info.get("short") or self.stage_info["name"],
            "stage_key": self.stage_info.get("key"),
            "stage_since": self.data.get("stage_since"),
            "stage_count": len(tab),
            "progress": pct,
            "to_next": need,
            "next_name": (nxt["name"] if nxt and not nxt.get("gate") else None),
            "next_need": (nxt.get("from") if nxt and not nxt.get("gate") else None),
            "next_gate": (nxt.get("gate") if nxt else None),
            "today_gain": float((self.data.get("daily") or {}).get(
                dt.date.today().isoformat(), 0.0)),
            "daily_cap": self.daily_cap,
            "cap_override": self.data.get("cap_override"),
            "first": self.data.get("first"),
            "last_chat": self.data.get("last_chat"),
            "cool": bool(self.data.get("cool")),
            "stages": [{"i": k + 1, **s} for k, s in enumerate(tab)],
            "effects": [{"i": k + 1, **s} for k, s in enumerate(tab)],
            "allow": self.allow,
            "allow_all": list(ALLOW_ALL),
            "allow_word": dict(ALLOW_WORD),
            "line": self.confess_line(),
            "phase": phase,
            "phase_text": CONFESS_PHASE.get(phase, phase),
            "hint_level": self.hint_level(),
            "hint_name": HINT_NAMES[max(0, min(3, self.hint_level() - 1))]
            if self.hint_level() >= 1 else "",
            "hint_names": list(HINT_NAMES),
            "is_lover": self.is_lover(),
            "lover_days": self.lover_days(),
            "lover_since": st.get("lover_since"),
            "lover_how": st.get("how"),
            "despair": self.despair_active(),
            "despair_days_left": self.despair_days_left(),
            "despair_mild": bool(st.get("despair_mild")),
            "refused": int(st.get("refused") or 0),
            "refuse_note": st.get("refuse_note") or "",
            "tried": int(st.get("tried") or 0),
            "gave_up": bool(st.get("gave_up")),
            "awaiting": self.awaiting_answer(),
            "chance_now": self.confess_chance(int(st.get("last_prep") or 0)),
            "chance_base": self.confess_chance(0),
            "chance_best": self.confess_chance(PREP_MAX),
            "confess_cfg": cfg,
            "prep_label": dict(PREP_LABEL),
            "prep_max": PREP_MAX,
            "stages_override": bool(self.data.get("stages_override")),
            "has_6_stages": False,
        }


# ══════════════════════════════════════════════════════════════════════════
#  把五档表写回 config.yaml（只动 relation: 段里的 stages: 那一块）
# ══════════════════════════════════════════════════════════════════════════

def _q(s) -> str:
    s = str(s)
    if s == "" or re.search(r"[:#\{\}\[\],&*!|>'\"%@`]", s) or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def stages_yaml(stages, indent: str = "  ") -> list:
    """把五档表转成几行 YAML —— 人也要能读，所以写成一行一项的分块。"""
    out = [indent + "stages:"]
    for s in stages:
        out.append(indent + "  - key: " + _q(s.get("key") or ""))
        out.append(indent + "    name: " + _q(s.get("name") or ""))
        for k in ("from", "to"):
            v = s.get(k)
            out.append(indent + "    %s: %s" % (k, "null" if v is None else _q(v)))
        if s.get("gate"):
            out.append(indent + "    gate: " + _q(s["gate"]))
        for k in _NUM_FIELDS:
            out.append(indent + "    %s: %s" % (k, _q(s.get(k))))
        allow = s.get("allow") or []
        out.append(indent + "    allow: [" + ", ".join(_q(x) for x in allow) + "]")
    return out


def write_stages_to_config(stages) -> bool:
    """只换 relation: 段里的 stages: 那一块，别的一行不动。"""
    p = ROOT / "config.yaml"
    if not p.exists():
        return False
    try:
        t = p.read_text(encoding="utf-8")
    except Exception:
        return False
    lines = t.split("\n")
    i = None
    for k, ln in enumerate(lines):
        if re.match(r"^relation:[ \t]*$", ln):
            i = k
            break
    if i is None:
        return False
    j = i + 1
    while j < len(lines) and not re.match(r"^[A-Za-z_]", lines[j] or ""):
        j += 1
    body = []
    k = i + 1
    while k < j:
        if re.match(r"^[ \t]*stages:[ \t]*$", lines[k] or ""):
            k += 1
            while k < j:
                ln = lines[k] or ""
                if re.match(r"^[ \t]{4,}\S", ln):
                    k += 1
                    continue
                if ln.strip() == "" and k + 1 < j and re.match(r"^[ \t]{4,}\S", lines[k + 1] or ""):
                    k += 1
                    continue
                break
            continue
        body.append(lines[k])
        k += 1
    new = lines[:i + 1] + stages_yaml(stages) + body + lines[j:]
    try:
        p.write_text("\n".join(new), encoding="utf-8")
    except Exception:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════
#  命令行
# ══════════════════════════════════════════════════════════════════════════

def _fmt(v) -> str:
    """60.0 → "60"，42.5 → "42.5"。给人看的数字别带没用的小数点。"""
    return "%g" % round(float(v), 2)


def _write_cap_to_config(v: float) -> bool:
    """把 relation.daily_cap 写回 config.yaml —— 只换那个数字，注释排版全留着。"""
    p = ROOT / "config.yaml"
    if not p.exists():
        return False
    try:
        t = p.read_text(encoding="utf-8")
        new, n = re.subn(r"(?m)^([ \t]*daily_cap:[ \t]*)([0-9.]+)",
                         lambda m: m.group(1) + _fmt(v), t, count=1)
        if n == 0:
            return False
        if new != t:
            p.write_text(new, encoding="utf-8")
        return True
    except Exception:
        return False


def _show_stages(rel: "Relation"):
    tab = rel.stages
    print("=" * 72)
    print("好感度 · 五档表（唯一的一份 —— 控制台「好感度」页改的就是它）")
    print("=" * 72)

    # ── 现在走到哪了 ──
    #   这一块是给「8-看记忆.bat」那个菜单用的：一眼看完，不用去翻 关系.json。
    cur = rel.stage_info
    ph = rel.confess_phase()
    print()
    print("  现在      : 第 %d/%d 档「%s」　亲密度 %s（表白线 %s）"
          % (rel.stage, len(tab), cur.get("name"),
             _fmt(rel.intimacy), _fmt(rel.confess_line())))
    print("  表白进度  : %s" % CONFESS_PHASE.get(ph, ph))
    if rel.is_lover():
        print("  在一起    : 第 %d 天（%s）　—— 恋人这一档跟前面四档不是一套规矩"
              % (rel.lover_days(), rel._cst().get("how") or "表白"))
    elif rel.hint_level():
        print("  她暗示到  : 第 %d/4 层「%s」"
              % (rel.hint_level(), HINT_NAMES[max(0, rel.hint_level() - 1)]))
    if rel.awaiting_answer():
        print("  ★ 她已经开口了，正在等你回 —— 你在聊天里答「好」就在一起，"
              "答「算了」她会掉回「重要的人」")
    if rel.despair_active():
        print("  她的状态  : 很难过（自暴自弃）—— 还有 %d 天" % rel.despair_days_left())
    print()
    print("-" * 72)
    print("  %-4s %-10s %-14s %6s %6s %6s %6s %6s %7s"
          % ("档", "名字", "区间", "提自己", "单条", "条数", "共字", "主动", "耐心"))
    for i, s in enumerate(tab, start=1):
        f = s.get("from")
        t = s.get("to")
        rng = ("表白才能进" if s.get("gate")
               else "%s ~ %s" % (_fmt(f if f is not None else 0),
                                 _fmt(t) if t is not None else "∞"))
        print("  %-4d %-10s %-14s %6s %6s %6s %6s %6s %7s"
              % (i, s.get("name"), rng,
                 _fmt(s.get("say_per_day")), _fmt(s.get("say_chars")),
                 _fmt(s.get("say_parts")), _fmt(s.get("say_total")),
                 _fmt(s.get("active_per_day")), _fmt(s.get("idle_scale"))))
        al = s.get("allow") or []
        print("       %s" % ("能：%s" % "、".join(al) if al else "还不能撒娇 / 说想你 / 叫名字"))
    print()
    cfg = rel.confess_cfg()
    print("  表白线      : %s" % _fmt(cfg["line"]))
    print("  直接说      : 基础 %.0f%%（她会觉得敷衍）；用心分每 1 分 +%.1f%%"
          "，反复说每次 −%.0f%%"
          % (cfg["direct_chance"] * 100, cfg["prep_scale"] * 100,
             cfg["repeat_penalty"] * 100))
    print("  暗示        : 一层一天，%d 天走完；之后她每天 %.0f%% 的概率开口"
          % (cfg["hint_days"], cfg["her_chance"] * 100))
    print("  被拒        : 掉到 %s，自暴自弃 %d 天（涨速 ×%.1f）"
          % (_fmt(cfg["refuse_drop_to"]), cfg["despair_days"],
             cfg["despair_gain_scale"]))
    print("  用心分七项  : " + " / ".join(
        "%s %s" % (PREP_LABEL[k].split("（")[0], cfg["prep_weights"].get(k, 0))
        for k in PREP_LABEL))
    if rel.data.get("stages_override"):
        print()
        print("  （这张表被控制台改过，存在 关系.json 的 stages_override 里。"
              "想回到默认：--stages-reset）")
    print()


def _cli():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]
    rel = Relation({})

    if "--stages" in args:
        _show_stages(rel)
        return

    if "--stages-reset" in args:
        rel.reset_stages()
        print("[OK] 五档表已回到 config.yaml / 默认值。")
        return

    if "--cap" in args:
        try:
            v = float(args[args.index("--cap") + 1])
        except Exception:
            print("[X] 用法：python relation.py --cap 60（60 = 每天最多涨 60 分）")
            return
        if v < 0:
            print("[X] 上限不能是负数。")
            return
        rel.data["cap_override"] = round(v, 2)
        rel.save()
        wrote = _write_cap_to_config(v)
        print("[OK] 每天的好感度上限改成 %s。" % _fmt(v))
        print("     立刻生效 —— 不用重启机器人。")
        if wrote:
            print("     也写回了 config.yaml 的 relation.daily_cap（下次开机的默认值）。")
        else:
            print("     （config.yaml 没写成，无妨 —— 关系.json 里那个值已经够用。）")
        if v == 0:
            print("     [!] 0 = 从今天起一分都不涨。")
        elif v <= 15:
            print("     （很慢：聊天再密，一天最多也就涨 %s。）" % _fmt(v))
        elif v >= 400:
            print("     （很快：按这个速度几天就能跳一档，容易显得不真实。）")
        return

    if "--set" in args:
        try:
            v = float(args[args.index("--set") + 1])
            rel.data["intimacy"] = v
            rel.data["peak"] = max(float(rel.data.get("peak", 0.0)), v)
            rel._recalc_stage()
            rel.advance_confession()
            rel.save()
            print("[OK] 亲密度已改成 %d（现在第 %d 档「%s」）。"
                  % (int(v), rel.stage, rel.stage_info["name"]))
            return
        except Exception as e:
            print("[X] 用法：python relation.py --set 500（%s）" % e)
            return

    if "--confess" in args:
        rel.become_lover("命令行调试")
        print("[OK] 直接推到了第 %d 档「%s」。她现在是你的恋人了。"
              % (rel.stage, rel.stage_info["name"]))
        print("     （调试用。想退回：--reset-confess 然后 --set 1200）")
        return

    if "--refuse" in args:
        if not rel.is_lover():
            r = rel.refuse_her()
            print("[OK] 演了一遍被她表白、你拒绝 ——")
        else:
            r = {"stage": rel.stage, "intimacy": rel.intimacy,
                 "despair_days": rel.confess_cfg()["despair_days"],
                 "despair_until": rel._cst().get("despair_until")}
            print("[!] 她已经是恋人了。这次只把她推进自暴自弃。")
            rel._cst()["despair_until"] = (
                dt.date.today() + dt.timedelta(days=int(r["despair_days"]))
            ).isoformat()
            rel.save()
        print("     现在第 %d 档「%s」，亲密度 %s（掉到 %s）"
              % (r["stage"], rel.stage_info["name"], r["intimacy"],
                 _fmt(rel.confess_cfg()["refuse_drop_to"])))
        print("     自暴自弃到 %s（%d 天）。" % (r["despair_until"],
                                              r["despair_days"]))
        return

    if "--reset-confess" in args:
        rel.reset_confess()
        print("[OK] 表白状态清掉了（试过几次、暗示到第几层、被拒记录全归零）。")
        print("     （恋人身份也一起清了 —— 想留着她：--confess）")
        return

    s = rel.stats()
    print("=" * 60)
    print("你们的关系进度")
    print("=" * 60)
    print("  认识      : 第 %d 天" % s["days"])
    print("  聊过      : %d 轮" % s["turns"])
    print("  亲密度    : %s（历史最高 %s）" % (s["intimacy"], s["peak"]))
    print("  当前档位  : 第 %d/%d 档  「%s」"
          % (s["stage"], s["stage_count"], s["stage_name"]))
    print("  进度      : %s %d%%" % (rel.bar(), s["progress"]))
    if s["is_lover"]:
        print("  下一档    : —— 已经在一起 %d 天了" % s["lover_days"])
    elif s["to_next"]:
        tgt = s["next_name"] or "表白线（%d）" % s["line"]
        print("  下一步    : %s 还差 %d" % (tgt, s["to_next"]))
    else:
        print("  下一步    : 到顶了")
    print("  今日已涨  : %s / %s%s"
          % (s["today_gain"], _fmt(s["daily_cap"]),
             "（你手改的，要改回去：--cap 200）"
             if s.get("cap_override") is not None else "（当日上限）"))
    print("  最近一次  : %s" % (s["last_chat"] or "-"))
    if s["cool"]:
        print("  （有点冷下来了）")
    if s["phase"] != "none":
        print("  表白      : %s" % s["phase_text"])
        if s["is_lover"]:
            print("              在一起第 %d 天" % s["lover_days"])
        elif s["hint_level"]:
            print("              暗示到第 %d 层「%s」"
                  % (s["hint_level"], s["hint_name"]))
        if s["refused"]:
            print("              被你拒过 %d 次" % s["refused"])
        if s["tried"]:
            print("              你直接说过 %d 次（现在这个状态成功率 %.0f%%）"
                  % (s["tried"], s["chance_now"] * 100))
        if s["despair"]:
            print("              [X] 自暴自弃中，还有 %d 天" % s["despair_days_left"])
    print()
    print("  档位表（- - - 以下这件事，分数推不动）：")
    for st in s["stages"]:
        mark = "  ← 现在" if st["i"] == s["stage"] else ""
        f = st.get("from")
        t = st.get("to")
        gate = st.get("gate")
        if gate:
            rng = "表白"
        else:
            rng = "%s~%s" % (_fmt(f), _fmt(t) if t is not None else "∞")
        print("    %d. %-8s %-12s %s%s"
              % (st["i"], st["name"], rng, st.get("short") or "", mark))
    print("  （第 %d 档是另一回事 —— 看清那行「表白」。" % s["stage_count"])
    print("   要改区间/效果：控制台「好感度」页，或 --stages）")


if __name__ == "__main__":
    _cli()
