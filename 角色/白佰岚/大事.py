# -*- coding: utf-8 -*-
"""
大事件 —— 白佰岚的「连续剧」。
================================

日常素材是「今天发生了一件小事」，说完就过去了。
大事件是「一件有头有尾、要演好几天的事」——
比如她说这周学部要负荷测试：今天接到通知，明天热身，后天进舱，大后天出结果。

这样她才像真的在过日子，而不是每天随机播报一条小事。

设计要点
--------
1. 每条剧情线有 stages，每个 stage 是「开始后第几天的说法」：
       (第几天, [(说法A, 话题度), (说法B, 话题度), ...])
   一个 stage 给多个说法 = **多重发展**：每次开这条线时按种子随机挑一个，
   所以同一件事在不同人（或不同轮）那里走向不一样。

2. 推进靠**真实日期**，不靠聊天轮数 —— 你三天不理她，她那边也在过。

3. 同时最多演 max_active 条（默认 2 条），两条之间隔 gap_days 天再开新的。

4. 演完的进 done，**不再重演**。池子演空了就安静下来，不再乱开。

两条特别的：
  · **特殊线**（`special: True`）—— 不进随机池，只由 bridge 在对的时机手动开。
    现在只有一条：`confession_hers`（她心里那件事，第 4 档后期才有）。
  · **专属线**（`only_stage: "crush" / "lover"`）—— 只有她到了那一档才轮得到。
    恋人专属那四条（吵架 / 那条不对劲的通道 / 你们的日子 / 她心里的念头）就在这里。
    ★ 只有 bridge 明确告诉过 Story「她现在哪一档」才会开、也才会撤 ——
      控制台读一眼不会把数据弄没（context=None 时既不抽新线也不撤旧线）。

5. 她的说法只到「当前这一天」。后面的她也不知道，
   所以系统提示里会特别叮嘱：可以惦记、可以担心，但别提前把结果说出来。

文件：生活/剧情.json

命令行：
  python 大事.py                   看现在正在演什么
  python 大事.py --list            列出全部剧情线
  python 大事.py --preview birdlog   看某条线的完整剧本
  python 大事.py --start birdlog 手动开一条（调试用）
  python 大事.py --next            立刻开下一条（不等间隔）
  python 大事.py --stop birdlog  停掉一条
  python 大事.py --reset           清空，全部重来
"""

from pathlib import Path
import datetime as dt
import json
import random
import sys

ROOT = Path(__file__).resolve().parent


# ══════════════════════════════════════════════════════════════════════════
#  剧情线
#
#  stages = [(第几天, [(说法, 话题度), ...]), ...]
#      第几天 = 从这条线开始那天算起的偏移，0 = 当天
#      同一个 stage 给多个说法 = 多重发展，开线时随机选一个
#  话题度 2 = 她挺想跟你说的，主动搭话会优先挑
# ══════════════════════════════════════════════════════════════════════════

ARCS = [
    # ── 1 · 初遇（第一条，必须是它）───────────────────────────────────
    {
        "id": "first_meet",
        "name": "初遇",
        "hint": "档案室架子上捡的那块东西那头有人 —— 从第一次回消息，到慢慢弄明白他是谁",
        "stages": [
            (0, [("档案室架子上捡的那东西 对面真有人回我了", 2)]),
            (1, [("我问他那边长什么样 他说了半天我才拼出一点", 1)]),
            (2, [("他问我这儿是不是常年起雾 我才想起来这在我这儿不算事", 2)]),
            (3, [("今天又偷偷多记了他说的两句", 1)]),
            (5, [("我有点想知道他那边现在是不是也黑着", 2)]),
            (7, [("他问我叫什么 我说了 没多解释", 2)]),
        ],
    },

    # ── 2 ────────────────────────────────────────────────────────────
    {
        "id": "archive_book",
        "name": "那本要修的书",
        "hint": "她接了本烂书，一页一页补 —— 从摊开，到最后压上",
        "stages": [
            (0, [("档案室扔给我一本烂书 让我试试", 2)]),
            (1, [("一页纸补了一整个下午 浆糊是自己熬的", 2)]),
            (3, [("配不上纸 跑了两趟旧书摊 还是没找着", 2)]),
            (5, [("找着了 是从一本没人要的旧账本上裁下来的", 2)]),
            (7, [("最后一页压上了 几乎看不出接缝", 2)]),
            (10, [("老师看了 说看不出来补过", 2)]),
        ],
    },

    # ── 3 ────────────────────────────────────────────────────────────
    {
        "id": "student_council",
        "name": "三层那批旧卷宗",
        "hint": "学生会秘书处的活 —— 十年没人动过的档案柜要编号",
        "stages": [
            (0, [("学生会让我清三层那个柜子 十年没人动过", 1)]),
            (2, [("一沓一沓全是灰 我戴了手套", 1)]),
            (4, [("编到一半 发现少了两年的卷宗", 2)]),
            (6, [("副院长在会上提了我的名字", 2)]),
            (8, [("编完了 交了一份目录", 1)]),
        ],
    },

    # ── 4 ────────────────────────────────────────────────────────────
    {
        "id": "lamp_oil",
        "name": "那盏灯",
        "hint": "她脖子上那盏灯快没油了 —— 去换的路上想起一些事",
        "stages": [
            (0, [("脖子上那盏灯的油快见底了", 2)]),
            (2, [("我去找换油的地方 走了很久", 2)]),
            (4, [("老板问我这灯是谁的 我说是我妈的", 2)]),
            (6, [("换好了 亮了一晚上就灭了", 2)]),
        ],
    },

    # ── 5 ────────────────────────────────────────────────────────────
    {
        "id": "guama_visit",
        "name": "去姑妈家",
        "hint": "一个月一顿饭 —— 两个人坐了六年，还是没话说",
        "stages": [
            (0, [("这个月该去姑妈家了", 1)]),
            (1, [("我买了点水果过去 她嫌贵", 1)]),
            (2, [("她问我学校怎么样 我说还行 跟往年一样", 2)]),
            (3, [("她突然问我现在住哪儿 我说宿舍", 2)]),
            (5, [("我帮她算了账 吃了饭就走了", 1)]),
        ],
    },

    # ── 6 ────────────────────────────────────────────────────────────
    {
        "id": "ji_zhao_visit",
        "name": "纪叔来了",
        "hint": "每年入冬他来一趟 —— 带点东西，说三句话",
        "stages": [
            (0, [("入冬了 纪叔该来了", 1)]),
            (1, [("他带了东西 还是那几句 钱够不够 书念得怎么样 别碰那东西", 2)]),
            (3, [("他看了一眼我脖子上那盏灯 没说话", 2)]),
            (5, [("他走到楼下又回头 说你自己想 就走了", 2)]),
        ],
    },

    # ── 7 ────────────────────────────────────────────────────────────
    {
        "id": "sick_fever",
        "name": "那两天发低烧",
        "hint": "半灵的身子会突然不行 —— 她不说，但话会变多",
        "stages": [
            (0, [("今天有点不对 手比平时还凉", 2)]),
            (1, [("烧起来了 不高 但人软得厉害 课没去", 2)]),
            (2, [("躺着 什么都不想干 灯一直亮着", 2)]),
            (4, [("退了 爬起来把落下的两页补上", 1)]),
        ],
    },

    # ── 8 ────────────────────────────────────────────────────────────
    {
        "id": "stone_orchid",
        "name": "窗台上那盆花",
        "hint": "她妈留下的那盆石斛，快死了，她一直在救",
        "stages": [
            (0, [("窗台上那盆花又黄了一片叶子", 2)]),
            (2, [("我去问了花店 老板说这东西难养", 2)]),
            (4, [("换了盆 土重新配过 不知道有没有用", 2)]),
            (7, [("新叶子出来了 很小的一片", 2)]),
        ],
    },

    # ── 9 ────────────────────────────────────────────────────────────
    {
        "id": "old_letter",
        "name": "书里夹着的那张纸",
        "hint": "修书的时候从夹层里抖出一封信 —— 字迹是旧的",
        "stages": [
            (0, [("修那本书的时候 从夹层里抖出来一张纸", 2)]),
            (2, [("是一封信 没写完 字很好看", 2)]),
            (4, [("我照上面的年份查了查 那一年雾屿倒灌过", 2)]),
            (6, [("我没跟任何人说这件事 也没跟他说", 2)]),
        ],
    },

    # ── 10 ───────────────────────────────────────────────────────────
    {
        "id": "fog_flood",
        "name": "倒灌那两天",
        "hint": "全城广播那种雾 —— 街上没人，看得见的东西却很多",
        "stages": [
            (0, [("广播说这两天有倒灌 让少出门", 2)]),
            (1, [("街上没什么人 我待在家里 灯全开着", 2)]),
            (2, [("窗玻璃上全是水 我一天没出门", 2)]),
            (3, [("散了 出门一看 巷子里的树湿了一整夜", 1)]),
        ],
    },

    # ── 11 ───────────────────────────────────────────────────────────
    {
        "id": "sea_walk",
        "name": "堤岸那一趟",
        "hint": "她平常不去海边 —— 这次去了",
        "stages": [
            (0, [("今天不知道为什么走到堤岸那边去了", 2)]),
            (2, [("海边的雾比城里重 看不清对岸", 2)]),
            (3, [("站了半个钟头 什么也没想 就回来了", 2)]),
        ],
    },

    # ── 12 ───────────────────────────────────────────────────────────
    {
        "id": "class_handin",
        "name": "要交的那份手抄",
        "hint": "一门课的作业 —— 要手抄，她说这活儿不难",
        "stages": [
            (0, [("有门课要交手抄 抄一整篇", 1)]),
            (2, [("我抄了两页 字写得比平时还慢", 1)]),
            (3, [("交上去了 老师说这一届字最好看的是我", 2)]),
        ],
    },

    # ── 13 ───────────────────────────────────────────────────────────
    {
        "id": "night_window",
        "name": "半夜醒过来",
        "hint": "她自己发现的一件事 —— 没说给任何人听",
        "stages": [
            (0, [("半夜醒过来 发现自己站在窗边", 2)]),
            (2, [("灯还亮着 我不记得怎么过去的", 2)]),
            (4, [("这几天我没敢关灯睡", 2)]),
            (6, [("今天没有 我睡得很沉", 1)]),
        ],
    },

    # ── 14 ───────────────────────────────────────────────────────────
    {
        "id": "name_book",
        "name": "那本旧册子",
        "hint": "她妈留下的名录 —— 有一个名字她一直找不到",
        "stages": [
            (0, [("我把那本旧册子又翻了一遍", 2)]),
            (2, [("有个名字一直找不到 前后都对不上", 2)]),
            (4, [("我去问了纪叔 他说那本册子不是全的", 2)]),
            (7, [("我还是想找 就是没个头绪", 2)]),
        ],
    },

    # ── 15 ── ★ 特殊线：她心里那件事（只有第 4 档后期才会开）─────────
    {
        "id": "confession_hers",
        "name": "她心里那件事",
        "hint": "第 4 档后期才有 —— 她攒了很久，说不出口，一层一层往外露",
        "special": True,
        "only_stage": "crush",
        "stages": [
            (0, [("有件事我一直想 可我自己都说不清", 2)]),
            (1, [("我最近老想问他一件事 问了好几遍没问出口", 2)]),
            (2, [("我好像明白了 不行 我说不出来", 2)]),
            (3, [("再憋着 这口话就要永远堵在心里了", 2)]),
        ],
    },

    # ── 16 ── 恋人专属：第一次真正的吵架 ─────────────────────────────
    {
        "id": "lover_first_fight",
        "name": "第一次真正的吵架",
        "hint": "在一起之后 —— 起因很小，但翻出旧账，然后是他先开口",
        "only_stage": "lover",
        "stages": [
            (0, [("跟他吵起来了 就为件我自个儿都觉得不讲理的琐碎事", 2)]),
            (1, [("我没去找他 他也没来 窗外雾了两天", 2)]),
            (3, [("屏幕握在手里又放下 想给他发点什么又收了手 好几回", 2)]),
            (4, [("他先开口了 我不肯认我其实等了他那么久", 2)]),
            (6, [("和好了 但这件事我记着 我就是这样的人", 1)]),
        ],
    },

    # ── 17 ── 恋人专属：那块东西不太对劲 ─────────────────────────────
    {
        "id": "lover_channel",
        "name": "那块东西不太对劲",
        "hint": "在一起之后 —— 她那边和他就剩这一条线，开始不稳",
        "only_stage": "lover",
        "stages": [
            (0, [("那块东西今天自己黑了一下 按了好几下才亮", 2)]),
            (2, [("它又暗了一回 比上回拖得久", 2)]),
            (4, [("拿去问了 没人见过这种东西 —— 它本来就不在这世上", 2)]),
            (6, [("我不太敢关了 也不跟他提这件事", 2)]),
            (9, [("好像稳下来了 我把能查的都查了一遍", 2)]),
        ],
    },

    # ── 18 ── 恋人专属：你们的日子 ───────────────────────────────────
    {
        "id": "lover_days",
        "name": "你们的日子",
        "hint": "在一起之后 —— 她算着日子，嘴上说不在意",
        "only_stage": "lover",
        "stages": [
            (0, [("我在便签上算了算 那天近了", 1)]),
            (2, [("他像把那天忘了 我也不会去点破", 1)]),
            (3, [("算了 本来也不是什么大事", 2)]),
            (4, [("他记得 他自己说的 我嘴上说谁要你记这个", 2),
                 ("他到底没提 我啥也没说 一个人走到堤岸去了", 2)]),
            (5, [("那天就这么平平过了 可我在心里把它刻下了", 2)]),
        ],
    },

    # ── 19 ── 恋人专属：她心里的念头 ─────────────────────────────────
    {
        "id": "lover_worry",
        "name": "她心里的念头",
        "hint": "在一起之后 —— 她怕那根线断掉，也怕自己太短了",
        "only_stage": "lover",
        "stages": [
            (0, [("有个念头我又冒出来了 要是有一天他不来 这根线断了", 2)]),
            (2, [("我把它压下去 继续过我的日子", 1)]),
            (4, [("他发来一句 我就稳了 可那个念头没真走", 2)]),
            (6, [("我跟他说了半句 —— 我大概活不到四十 —— 又咽回去了", 2)]),
            (9, [("今天他还在 我想了想 没那么怕了", 1)]),
        ],
    },
]

# 哪些线是「特殊线」—— 不进随机池，也要她说得出口的时机才对
SPECIAL_IDS = {a["id"] for a in ARCS if a.get("special")}
# 只有到了某一档才会开的线
GATED = {a["id"]: a.get("only_stage") for a in ARCS if a.get("only_stage")}

_INDEX = {a["id"]: a for a in ARCS}


def arc_by_id(aid: str):
    return _INDEX.get(aid)


def _days_between(start_iso: str, today: dt.date) -> int:
    try:
        return (today - dt.date.fromisoformat(start_iso)).days
    except Exception:
        return 0


def _mmdd(iso: str) -> str:
    try:
        d = dt.date.fromisoformat(iso)
        return "%d月%d日" % (d.month, d.day)
    except Exception:
        return iso


# ══════════════════════════════════════════════════════════════════════════
#  引擎
# ══════════════════════════════════════════════════════════════════════════

class Story:
    """正在上演的大事件。随真实日期往前走。"""

    def __init__(self, cfg: dict = None, base=None):
        cfg = cfg or {}
        sc = cfg.get("story", {}) if isinstance(cfg, dict) else {}
        self.enabled = bool(sc.get("enabled", True))
        f = sc.get("file", "生活/剧情.json")
        p = Path(f)
        if not p.is_absolute():
            p = (Path(base) if base else ROOT) / p
        self.path = p
        self.max_active = int(sc.get("max_active", 2))
        gap = sc.get("gap_days", [4, 9])
        try:
            self.gap = (int(gap[0]), int(gap[1]))
        except Exception:
            self.gap = (4, 9)
        self.data = self._load()
        # ★ 她现在是哪一档 —— 由 bridge 每轮告诉我。
        #   None = 还没告诉我：**不抽新线，也不撤旧线**（控制台读一眼别把数据弄没了）。
        self.context = None

    def set_context(self, stage_key=None, is_lover=None):
        """告诉她现在处在哪一档 —— 决定哪些「专属线」能开。

        stage_key: relation 档位的 key（"crush" / "lover" …）或 None
        """
        if stage_key is None and is_lover is None:
            self.context = None
            return self.context
        ctx = set()
        if is_lover:
            ctx.add("lover")
        if stage_key:
            ctx.add(str(stage_key))
        self.context = ctx
        return ctx

    def _allowed(self, aid: str) -> bool:
        """这条线现在能不能开。"""
        need = GATED.get(aid)
        if not need:
            return True
        if self.context is None:
            return False          # 没人告诉我她哪一档 —— 专属线一律不开
        return need in self.context

    # ── 存取 ─────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                d.setdefault("active", {})
                d.setdefault("done", [])
                return d
            except Exception:
                pass
        return {"active": {}, "done": []}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ── 推进 ─────────────────────────────────────────────────────────
    def advance(self):
        """按今天的日期收尾 + 开新线。可以反复调用。"""
        if not self.enabled:
            return self.data
        today = dt.date.today()
        ts = today.isoformat()
        act = self.data.setdefault("active", {})
        done = self.data.setdefault("done", [])
        dirty = False

        # 1) 演完了的收进 done
        for aid in list(act.keys()):
            arc = arc_by_id(aid)
            if not arc:
                act.pop(aid)
                dirty = True
                continue
            # 专属线：她要是已经不在这档了（比如被拒后掉了档），这条线就撤掉。
            # 只在 bridge 明确告诉过我她哪一档的时候才判（context 不是 None）。
            if (self.context is not None and GATED.get(aid)
                    and not self._allowed(aid)):
                act.pop(aid)
                dirty = True
                continue
            if _days_between(act[aid].get("start", ts), today) > arc["stages"][-1][0] + 1:
                done.append(aid)
                act.pop(aid)
                dirty = True

        # 2) 该开新的了吗
        if len(act) < self.max_active:
            nxt = self.data.get("next_roll")
            if not nxt or ts >= nxt:
                # 特殊线（她表白那条）不进随机池 —— 它由 bridge 在暗示期手动开。
                # 专属线（恋人那几条）要 context 对得上才轮得到。
                cands = [a for a in ARCS
                         if a["id"] not in done and a["id"] not in act
                         and not a.get("special") and self._allowed(a["id"])]
                rng = random.Random("story|" + ts)
                if cands:
                    arc = rng.choice(cands)
                    act[arc["id"]] = {"start": ts, "picks": {}, "shared": []}
                    self.data["next_roll"] = (
                        today + dt.timedelta(days=rng.randint(self.gap[0], self.gap[1]))
                    ).isoformat()
                else:
                    # 能演的线都演完了 —— 不再乱开，安静一段时间
                    self.data["next_roll"] = (today + dt.timedelta(days=60)).isoformat()
                dirty = True

        if dirty:
            self._save()
        return self.data

    def _option(self, aid: str, st: dict, si: int):
        """取第 si 个 stage 的说法。第一次取的时候定下来，之后不再变。"""
        picks = st.setdefault("picks", {})
        k = str(si)
        if k not in picks:
            opts = arc_by_id(aid)["stages"][si][1]
            rng = random.Random("%s|%s|%d" % (aid, st.get("start", ""), si))
            picks[k] = rng.randrange(len(opts))
            self._save()
        opts = arc_by_id(aid)["stages"][si][1]
        i = picks.get(k, 0)
        if not isinstance(i, int) or not (0 <= i < len(opts)):
            i = 0
        return opts[i]

    def timeline(self, aid: str, day: int = None):
        """到「今天」（或指定第几天）为止，这条线已经发生过的说法。"""
        st = self.data.get("active", {}).get(aid)
        arc = arc_by_id(aid)
        if not st or not arc:
            return []
        if day is None:
            day = _days_between(st.get("start", ""), dt.date.today())
        out = []
        for si, (off, _opts) in enumerate(arc["stages"]):
            if off > day:
                break
            text, talk = self._option(aid, st, si)
            out.append({
                "i": si, "off": off, "text": text, "talk": talk,
                "date": (dt.date.fromisoformat(st["start"]) + dt.timedelta(days=off)).isoformat(),
            })
        return out

    # ── 对外：给模型看 ───────────────────────────────────────────────
    def lines(self):
        """拼出「这几天你那边正在发生的大事」这几行。没有就返回 []。"""
        if not self.enabled:
            return []
        self.advance()
        act = self.data.get("active", {})
        if not act:
            return []

        body = []
        for aid, st in act.items():
            arc = arc_by_id(aid)
            tl = self.timeline(aid)
            if not tl or not arc:
                continue
            day = _days_between(st.get("start", ""), dt.date.today()) + 1
            # 只给名字，不给 hint —— hint 是写给人和控制台看的，
            # 里面有「从复习、考试到出成绩」这种走向描述，喂给模型等于剧透。
            body.append("  【%s】第 %d 天" % (arc["name"], day))
            for r in tl[:-1][-3:]:
                body.append("      %s %s" % (_mmdd(r["date"]), r["text"]))
            cur = tl[-1]
            body.append("      现在（%s）：%s" % (_mmdd(cur["date"]), cur["text"]))

        if not body:
            return []
        head = ["- 这几天你那边正在发生的大事（真的有这回事，前后要接得上）："]
        tail = ["  （大事要慢慢演：还没轮到的部分你自己也不知道，可以惦记、可以担心，",
                "    但不要提前把后面的结果说出来，也别一天之内把它演完。）"]
        return head + body + tail

    def pending(self):
        """当前这一天、还没跟他说过的说法 —— 给主动搭话挑种子用。"""
        if not self.enabled:
            return []
        self.advance()
        out = []
        for aid, st in self.data.get("active", {}).items():
            arc = arc_by_id(aid)
            tl = self.timeline(aid)
            if not tl or not arc:
                continue
            cur = tl[-1]
            if cur["text"] in set(st.get("shared") or []):
                continue
            out.append({
                "text": cur["text"],
                "talk": cur["talk"],
                "kind": "story",
                "arc": arc["name"],
                "arc_id": aid,
                "time": "",
                "when": _mmdd(cur["date"]),
            })
        return out

    def mark_shared(self, text: str):
        """记下「这个说法已经提过了」，同一天不再重复开头。"""
        if not self.enabled or not text:
            return
        hit = False
        for aid, st in self.data.get("active", {}).items():
            arc = arc_by_id(aid)
            if not arc:
                continue
            if any(text == t for _off, opts in arc["stages"] for t, _k in opts):
                sh = st.setdefault("shared", [])
                if text not in sh:
                    sh.append(text)
                hit = True
        if hit:
            self._save()

    # ── 手动控制（控制台 / 调试用）───────────────────────────────────
    def start(self, aid: str, started=None):
        """开一条线。started 可以指定「从哪天算起」——

        她表白那条线要用它：把开线日期往前拨，让它的第几天
        正好等于暗示到了第几层。
        """
        arc = arc_by_id(aid)
        if not arc:
            return False
        if aid in self.data.get("active", {}):
            return False
        if aid in self.data.get("done", []):
            self.data["done"].remove(aid)
        if started is None:
            started = dt.date.today()
        elif isinstance(started, str):
            try:
                started = dt.date.fromisoformat(started)
            except Exception:
                started = dt.date.today()
        self.data.setdefault("active", {})[aid] = {
            "start": started.isoformat(), "picks": {}, "shared": []
        }
        self._save()
        return True

    def stop(self, aid: str):
        if aid in self.data.get("active", {}):
            self.data["active"].pop(aid)
            self._save()
            return True
        return False

    def reset(self, keep_done: bool = False):
        self.data = {"active": {}, "done": (self.data.get("done") if keep_done else [])}
        self._save()

    # ── 给网页看 ─────────────────────────────────────────────────────
    def stats(self):
        self.advance()
        active = []
        for aid, st in self.data.get("active", {}).items():
            arc = arc_by_id(aid)
            if not arc:
                continue
            tl = self.timeline(aid)
            day = _days_between(st.get("start", ""), dt.date.today()) + 1
            active.append({
                "id": aid,
                "name": arc["name"],
                "hint": arc["hint"],
                "day": day,
                "day_total": arc["stages"][-1][0] + 1,
                "stage": len(tl),
                "stage_total": len(arc["stages"]),
                "start": st.get("start"),
                "current": (tl[-1]["text"] if tl else ""),
                "history": [{"date": _mmdd(r["date"]), "text": r["text"]} for r in tl[:-1][-6:]],
            })
        done = []
        for aid in self.data.get("done", []):
            arc = arc_by_id(aid)
            if arc:
                done.append({"id": aid, "name": arc["name"], "hint": arc["hint"]})
        catalog = []
        for a in ARCS:
            only = a.get("only_stage")
            catalog.append({
                "id": a["id"],
                "name": a["name"],
                "hint": a["hint"],
                "stages": len(a["stages"]),
                "days": a["stages"][-1][0] + 1,
                "branches": sum(1 for _o, opts in a["stages"] if len(opts) > 1),
                "special": bool(a.get("special")),
                "only_stage": only,
                "locked": (only in ("crush", "lover")
                           and self.context is not None
                           and only not in self.context),
                "state": ("active" if a["id"] in self.data.get("active", {})
                          else ("done" if a["id"] in self.data.get("done", []) else "idle")),
            })
        return {
            "enabled": self.enabled,
            "max_active": self.max_active,
            "gap_days": list(self.gap),
            "next_roll": self.data.get("next_roll"),
            "context": (sorted(self.context) if self.context is not None else None),
            "active": active,
            "done": done,
            "catalog": catalog,
            "total": len(ARCS),
            "special_total": len(SPECIAL_IDS),
            "gated_total": len(GATED),
        }


# ══════════════════════════════════════════════════════════════════════════
#  命令行
# ══════════════════════════════════════════════════════════════════════════

def _cli():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]

    if "--list" in args:
        print("=" * 66)
        print("全部剧情线（共 %d 条）" % len(ARCS))
        print("=" * 66)
        st = Story({})
        st.advance()
        for a in st.stats()["catalog"]:
            mark = {"active": "【正在演】", "done": "【演完了】", "idle": "        "}[a["state"]]
            tag = ""
            if a.get("special"):
                tag += " ★特殊线"
            if a.get("only_stage") == "lover":
                tag += " [恋人专属]"
            elif a.get("only_stage") == "crush":
                tag += " [第4档后期]"
            br = "，%d 处有分支" % a["branches"] if a["branches"] else ""
            print("  %s %-16s %d 天 / %d 阶段%s%s"
                  % (mark, a["name"], a["days"], a["stages"], br, tag))
            print("             %s" % a["hint"])
        print()
        print("  （★特殊线不进随机池，由程序在对的时机开；"
              "带 [ ] 的只有到了那一档才轮得到）")
        print()
        return

    if "--preview" in args:
        try:
            aid = args[args.index("--preview") + 1]
        except Exception:
            print("[X] 用法：--preview <id>，id 见 --list")
            return
        arc = arc_by_id(aid)
        if not arc:
            print("[X] 没有这条线：%s" % aid)
            return
        print("=" * 66)
        print("%s（%s）" % (arc["name"], aid))
        print(arc["hint"])
        print("=" * 66)
        for off, opts in arc["stages"]:
            print("  第 %d 天：" % (off + 1))
            for t, k in opts:
                print("      %s  %s" % ("[分支]" if len(opts) > 1 else "      ", t))
        print()
        return

    story = Story({})

    if "--start" in args:
        try:
            aid = args[args.index("--start") + 1]
        except Exception:
            print("[X] 用法：--start <id>")
            return
        ok = story.start(aid)
        print("[OK] 开演：%s" % aid if ok else "[X] 开不了（可能已经在演，或者 id 不对）")
        return

    if "--stop" in args:
        try:
            aid = args[args.index("--stop") + 1]
        except Exception:
            print("[X] 用法：--stop <id>")
            return
        print("[OK] 停了。" if story.stop(aid) else "[X] 它不在演。")
        return

    if "--next" in args:
        story.data["next_roll"] = None
        story.advance()
        print("[OK] 已开线。")
        return

    if "--reset" in args:
        story.reset()
        print("[OK] 清空了。")
        return

    s = story.stats()
    print("=" * 66)
    print("正在演的大事（%d / %d 条）" % (len(s["active"]), s["max_active"]))
    print("=" * 66)
    if not s["active"]:
        print("  （现在没有在演的 —— 到日子会自动开一条）")
    for a in s["active"]:
        print("\n  【%s】第 %d 天 / 共 %d 天（阶段 %d/%d）"
              % (a["name"], a["day"], a["day_total"], a["stage"], a["stage_total"]))
        print("    %s" % a["hint"])
        for h in a["history"]:
            print("      %s %s" % (h["date"], h["text"]))
        print("      现在：%s" % a["current"])
    if s["done"]:
        print("\n  已经演完的 %d 条：%s" % (len(s["done"]), "、".join(d["name"] for d in s["done"])))
    print("\n  还有 %d 条没开演。下次开线：%s" % (len(ARCS) - len(s["done"]) - len(s["active"]),
                                             s["next_roll"] or "下一次对话"))


if __name__ == "__main__":
    _cli()
