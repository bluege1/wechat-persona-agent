# -*- coding: utf-8 -*-
"""
定期总结 —— 让她把你们的事记下来
=================================

问题：memory.json 只存最近 20 轮。聊到第 200 轮的时候，
第 30 轮说过的那句「我小时候只有压缩食物吃」早就被挤出去了。
她就会犯那种最出戏的错 —— 你提起来，她一脸茫然。

做法：每隔一段时间，让她**自己复盘一次**。

    攒够 every_turns 轮（默认 25 轮）、或者距上次过了 every_hours 小时，
    就把最近这段对话交给模型，让它以「达妮娅本人写备忘录」的口气，产出两样东西：

      长期记忆   她对他的认识、你们之间发生过的事、约定、她在意什么。
                 （滚动更新，篇幅有上限，永远是「到现在为止的总账」）
      这次的复盘  这一段时间里**值得记住的**几件事，一条一行。

    然后这两样都会被塞回系统提示 —— 于是她记得住的，就不再只是最近 20 轮。

★ 2026-09-22 收窄：**这不是日记，是记忆**
--------------------------------------------
用户看完第 108 轮那篇复盘之后说：

    「你这个历史复盘是每一句话都复盘的吗？我觉得只要记录一下关系的进展
      和一些重要的事情就行了吧，重要的事情包括我的信息，我们的约定，
      我的性格，我的态度之类的，模拟人类的记忆系统，不重要的东西就忘掉，
      不然这样聊个几百轮 token 直接爆炸了」

他说得对。原来那篇是 52 行「他是另一个世界的人／他那边要上班／大学住四人间
／我这边叫上学／住单人间……」，等于把整段对话换行重排了一遍 ——
一个字都没忘，那就不是记忆，是备份。

人的记忆是**有损**的：留下来的只有「他是什么人、我们说好了什么、
他对我什么态度、我还有什么事没弄明白」。吃了什么、他打了多久游戏、
谁先说了晚安，两天就没了。

所以现在两头一起收：
  · 提示词里把「值得记的」列成五类白名单，并明说「一件都没有就整段留空」；
  · 程序里再硬兜一道（`_tidy_now`）：最多 5 条、120 字、去重、超了截断 ——
    光写「请写短一点」是拦不住的，实测它照样交 52 行回来。
  · 一段里要是真没值得记的，**就不落这一条复盘**（长期记忆照常更新）。
    这就是「不重要的东西就忘掉」：宁可不记，也不把流水账攒起来。

文件：
  记忆/总结.json    长期记忆 + 历次复盘（程序维护）
  记忆/备忘录.md    同上，但写成给人看的样子（你想读的时候读这个）

命令行：
  python journal.py --show        看看她记住了什么
  python journal.py --list        只看历次复盘的目录
  python journal.py --forget      忘掉历次复盘里的流水账（长期记忆不动）
"""

from pathlib import Path
import datetime as dt
import json
import re
import sys

from 角色 import ROOT, BASE, PROG, current   # 惰性 ROOT：指向"当前角色"的数据包
TAG_CORE = "【长期记忆】"
TAG_NOW = "【这次的复盘】"

# 「长期记忆」正文里那四个小标题。★ 模型偶尔会**忘了写方括号标记**，
#   只把这几行小标题摆出来（2026-09-22 实测：连跑 6 次，6 次都没写标记）。
#   那就按结构认它 —— 见 _split。
_CORE_HEADS = ("他这个人", "我们之间", "说好的事", "我还没弄明白")
# 收拾「这次的复盘」时要扔掉的**纯标题行** —— 它们是小节名，不是记忆本身。
#   以前没扔，结果「他这个人」被当成一条记忆存进了复盘（实测）。
_BARE_HEADS = {"他这个人", "我们之间", "说好的事", "我还没弄明白的",
               "长期记忆", "这次的复盘"}


class Journal:
    """达妮娅的私人备忘录。"""

    def __init__(self, cfg: dict = None):
        cfg = cfg or {}
        jcfg = cfg.get("journal", {}) or {}
        self.cfg = jcfg
        self.enabled = bool(jcfg.get("enabled", True))

        self.dir = Path(jcfg.get("dir") or "记忆")
        if not self.dir.is_absolute():
            self.dir = ROOT / self.dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "总结.json"
        self.md_file = self.dir / "备忘录.md"

        self.every_turns = int(jcfg.get("every_turns", 25))
        self.every_hours = float(jcfg.get("every_hours", 8))
        self.min_turns = int(jcfg.get("min_turns", 6))
        self.core_chars = int(jcfg.get("core_chars", 900))
        # 历次复盘留多少条。★ 2026-09-22 从 200 收到 24 ——
        # 「模拟人类的记忆系统」：老的那些本来就该被长期记忆吸收掉，
        # 留着 200 条 90 字进提示的旧账，只会把要紧的几句淹掉。
        self.keep_entries = int(jcfg.get("keep_entries", 24))

        self.data = self._load()

    # ── 存取 ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        d = {}
        if self.file.exists():
            try:
                d = json.loads(self.file.read_text(encoding="utf-8")) or {}
            except Exception:
                d = {}
        d.setdefault("core", "")
        d.setdefault("entries", [])
        d.setdefault("last_turn", 0)
        d.setdefault("last_time", "")
        d.setdefault("count", 0)
        return d

    def save(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.file.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._write_md()
        except Exception:
            pass

    def _write_md(self):
        """另外写一份给人看的。"""
        # ★ 2026-10-05：标题原来写死「达妮娅的备忘录」，所以 角色\西格莉卡\记忆\
        #   备忘录.md 第一行也是「达妮娅的备忘录」—— 新角色全在替达妮娅写日记。
        #   改成按当前角色取名。
        try:
            _who = current()
        except Exception:
            _who = "她"
        lines = ["# %s的备忘录" % _who, "",
                 "（这是程序自动整理的。上面是「长期记忆」——她心里对他的总账；",
                 "  下面是历次复盘。每次总结都会重写这一页。）",
                 "（复盘只记值得留的：关系进展 / 他的信息 / 约定 / 他的性子 /",
                 "  她还没弄明白的。吃了什么、谁先说了晚安这种流水账不记 ——",
                 "  人只记得住有分量的事。）", ""]
        lines.append("## 长期记忆")
        lines.append("")
        lines.append(self.data.get("core") or "（还没攒出来）")
        lines.append("")
        lines.append("## 历次复盘")
        lines.append("")
        entries = self.data.get("entries") or []
        if not entries:
            lines.append("（还没有）")
        for e in reversed(entries):
            lines.append("### %s ── 第 %s 轮"
                         % (e.get("time", ""), e.get("at_turn", "?")))
            lines.append("")
            lines.append(e.get("text", ""))
            lines.append("")
        self.md_file.write_text("\n".join(lines), encoding="utf-8")

    # ── 该不该总结 ────────────────────────────────────────────────────
    def need(self, turns: int):
        """返回 (要不要总结, 为什么)。"""
        if not self.enabled:
            return False, "记忆总结已关（journal.enabled=false）"

        since = int(turns) - int(self.data.get("last_turn", 0))
        if since < self.min_turns:
            return False, "才聊了 %d 轮，还不用记" % since

        if since >= self.every_turns:
            return True, "攒够 %d 轮了" % since

        t = self.data.get("last_time")
        if t:
            try:
                hours = (dt.datetime.now()
                         - dt.datetime.fromisoformat(t)).total_seconds() / 3600.0
                if hours >= self.every_hours:
                    return True, "距上次复盘过了 %.0f 小时" % hours
            except Exception:
                pass
        return False, "还没到点（%d/%d 轮）" % (since, self.every_turns)

    # ── 复盘 ──────────────────────────────────────────────────────────
    def summarize(self, chat_fn, history: list, turns: int, note: str = "") -> str:
        """让模型复盘一次。

        chat_fn(messages, max_tokens) -> str，由 Brain 提供（复用它的接口和 key）。
        返回写进去的那条复盘；失败返回空串。
        """
        convo = []
        for m in (history or [])[-60:]:
            role = m.get("role")
            who = "他" if role == "user" else "我"
            text = str(m.get("content") or "").strip().replace("\n", " ")
            if not text:
                continue
            convo.append("%s：%s" % (who, text[:220]))
        if not convo:
            return ""
        convo_txt = "\n".join(convo)

        prev_core = (self.data.get("core") or "").strip() or "（还没有，这是第一次记）"

        sys_p = (
            ("你就是%s。现在不是聊天 —— 是你在睡前，把这段时间跟他的事"
             % (current() or "她"))
            + "记进自己的私人备忘录。\n"
            "用第一人称「我」写，写给你自己看，别写成报告。\n"
            "语气可以懒得精致，但内容要准。不要写「用户」「对方」「对话」这种词，"
            "他就是「他」。\n"
            "★ 记住：这是**记忆**，不是聊天记录。人只记得住有分量的事，"
            "鸡毛蒜皮两天就自己忘了。挑的时候问自己一句："
            "「这件事过一个月还值得想起来吗？」答不上来的，就别写。"
        )

        user_p = (
            "下面是我们的聊天记录（按时间从早到晚）：\n"
            "────────────────────────\n"
            + convo_txt
            + "\n────────────────────────\n\n"
            "我上一次写的长期记忆是这样的：\n"
            "────────────────────────\n"
            + prev_core
            + "\n────────────────────────\n"
            + (("\n另外，" + note + "\n") if note else "")
            + "\n请输出两段，格式严格照下面来（两个方括号标记都要原样写上）：\n\n"
            + TAG_CORE + "\n"
            "（把「长期记忆」重新写一遍 —— 不是只写这次的新内容，"
            "而是把老的和新的合成一份总账。涵盖：他是个什么样的人、"
            "我们之间发生过什么值得记的事、我答应过什么／他答应过什么、"
            "我在意什么、我还没弄明白什么。"
            "控制在 %d 字以内，写不下就挑要紧的 ——"
            "**旧的要是已经不重要了就直接删掉腾地方**，别当流水账攒着。\n"
            "按「他这个人 / 我们之间 / 说好的事 / 我还没弄明白的」分着写，"
            "别按时间排。只写有分量的事。他没说过、我没确认过的东西，别编。）\n\n"
            % self.core_chars
            + TAG_NOW + "\n"
            "（只挑**值得留下来**的写。一件都没有就**整段留空** ——"
            "连「【这次的复盘】」这几个字都别写，这很正常："
            "一天下来没什么值得记的，才是常态。\n"
            "够格的只有这五类：\n"
            "  · 关系 —— 咱们之间往前走了哪一步（第一次……、他终于说了……）\n"
            "  · 他的信息 ——他亲口讲过的、关于他自己的事（在哪儿上班、身体、"
            "家里、习惯、好恶）。**这一类最要紧**\n"
            "  · 约定 —— 他答应我的 / 我答应他的 / 说好一起做的事\n"
            "  · 他的性子 —— 他怎么对我、什么态度、我看出来他是什么脾气\n"
            "  · 我还没弄明白的 —— 他讲过、我还记着的疑问\n"
            "最多 5 条，一条一行，加起来别超过 120 字，**要紧的写前面**。\n"
            "下面这些**一律不写**：吃了什么、他打了多久游戏、谁先说了晚安、"
            "我催了他几次、他回得快不快。两天后我自己就忘了，"
            "记下来只是占地方。）\n"
            "\n★ 两个方括号标记**必须原样写出来**，一个字都不能省 ——"
            "上一版就是漏了标记，整篇长期记忆被当成流水账切掉，"
            "我等于一整天什么都没记住。照这个模子来：\n"
            "\n" + TAG_CORE + "\n"
            "他这个人：\n- ……\n"
            "我们之间：\n- ……\n"
            "说好的事：\n- ……\n"
            "我还没弄明白的：\n- ……\n"
            "\n" + TAG_NOW + "\n"
            "- ……（没什么值得记的就整段不写）\n"
        )

        out = ""
        try:
            out = chat_fn(
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": user_p}],
                1200,
            )
        except Exception as e:
            print("  （复盘失败：%s）" % e)
            return ""

        core, now = self._split(out)
        core = (core or "").strip()
        # ★ 硬兜一道：模型嘴上答应写短，手上照样交 52 行回来（实测过）。
        now = self._tidy_now(now)
        if not core and not now:
            return ""

        self.data["last_turn"] = int(turns)
        self.data["last_time"] = dt.datetime.now().isoformat(timespec="seconds")
        self.data["count"] = int(self.data.get("count", 0)) + 1
        if core:
            self.data["core"] = core

        # ★ 这一段里没有值得记的事 → **不落这条复盘**，长期记忆照常更新。
        #   以前的兜底是「now 空就拿 core 顶上」，那是错的：
        #   等于每次都在重记同一篇总账。没东西可记就不记 ——
        #   这就是用户要的「不重要的东西就忘掉」。
        if not now:
            self.save()
            return ""

        entry = {
            "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "at_turn": int(turns),
            "text": now,
        }
        self.data.setdefault("entries", []).append(entry)
        self.data["entries"] = self.data["entries"][-self.keep_entries:]
        self.save()

        # 复盘是私事，不该把整段历史从上下文里删掉 —— 只把它标出来，
        # 这样主循环能接着往下跑，记忆也不会重复记同一段。
        return entry["text"]

    @staticmethod
    def _tidy_now(text: str, max_lines: int = 5, max_chars: int = 120) -> str:
        """把「这次的复盘」收拾成记忆该有的样子：短、去重、只留前几条。

        为什么非得程序来兜（2026-09-22）：提示词里写「150 字以内、不要流水账」，
        模型照样交回来 52 行「他是另一个世界的人／他那边要上班／大学住四人间
        ／我这边叫上学／住单人间……」—— 那就是把整段对话换行重排了一遍。
        光靠「请写短一点」是靠不住的，所以这里硬性截断。
        """
        t = (text or "").strip()
        if not t:
            return ""
        out, seen = [], set()
        for ln in t.splitlines():
            s = ln.strip().lstrip("-—–·*•●○　 \t")
            s = re.sub(r"^\d+\s*[.、)）]\s*", "", s).strip()
            s = re.sub(r"^[【\[（(].{0,10}?[】\]）)]\s*", "", s).strip()
            if len(s) < 2:
                continue
            # ★ 纯标题行不算记忆（「他这个人」这种是小节名）。
            #   以前没这道闸，它会被当成一条记忆存下来 —— 实测踩过。
            if s.rstrip("：:") in _BARE_HEADS:
                continue
            key = re.sub(r"[\s，。、！？；~～,.!?;]", "", s)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= max_lines:
                break
        got = "\n".join(out)
        if len(got) > max_chars:
            cut = got[:max_chars].rstrip()
            # 别切在半个句子里 —— 退到最后一个断点
            for ch in ("\n", "；", "。", "，", "、", " "):
                i = cut.rfind(ch)
                if i >= int(max_chars * 0.6):
                    cut = cut[:i].rstrip()
                    break
            got = cut
        return got

    @staticmethod
    def _looks_like_core(text: str) -> bool:
        """这段是不是「长期记忆」那一篇（而不是某一次的复盘）。

        判据是**结构**：长期记忆按「他这个人 / 我们之间 / 说好的事 /
        我还没弄明白的」分着写，一段里出现两个以上小标题，就是它。
        复盘那边是「一条一行」的散句，不会有这些小标题。
        """
        t = re.sub(r"\s+", "", text or "")
        return sum(1 for h in _CORE_HEADS if h in t) >= 2

    @staticmethod
    def _split(text: str):
        text = (text or "").strip()
        core, now = "", ""
        if TAG_CORE in text:
            rest = text.split(TAG_CORE, 1)[1]
            if TAG_NOW in rest:
                core, now = rest.split(TAG_NOW, 1)
            else:
                core = rest
        elif TAG_NOW in text:
            now = text.split(TAG_NOW, 1)[1]
            # 只写了复盘标记、长期记忆标记漏了 —— 前面那半段多半就是长期记忆
            head = text.split(TAG_NOW, 1)[0]
            if Journal._looks_like_core(head):
                core = head
        elif Journal._looks_like_core(text):
            # ★ 两个标记都没写（实测：连续 6 次复盘都是这样）。
            #   整篇按长期记忆收下 —— 宁可多记一点，也不能把
            #   「他是什么人」整篇丢掉。上一版就是在这儿把长期记忆
            #   扔进了「这次的复盘」，于是长期记忆空了整整一天，
            #   她对他就只剩最近 20 轮，看着就像「完全没有上下文」。
            core = text
        else:
            now = text
        return core.strip(), now.strip()

    # ── 给模型看 ──────────────────────────────────────────────────────
    def prompt_block(self, max_entries: int = 3) -> str:
        """拼出「你记得的事」这一段。"""
        if not self.enabled:
            return ""
        core = (self.data.get("core") or "").strip()
        entries = self.data.get("entries") or []
        if not core and not entries:
            return ""

        lines = ["# 你记得的事（这是你自己攒下来的，不是别人给你的资料）"]
        if core:
            lines.append("")
            lines.append(core)
        if entries:
            lines.append("")
            lines.append("最近这几段，你还记着：")
            for e in entries[-max_entries:]:
                first = str(e.get("text", "")).strip().split("\n")[0]
                lines.append("  · %s（%s）" % (first[:90], e.get("time", "")))
        lines.append("")
        lines.append("（这些是你真实记得的事。可以自然地用它，"
                     "但别一条条背出来，也别提到「记录」这两个字。）")
        return "\n".join(lines)

    # ── 给网页/命令行看 ───────────────────────────────────────────────
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "core": self.data.get("core") or "",
            "entries": self.data.get("entries") or [],
            "count": int(self.data.get("count", 0)),
            "last_turn": int(self.data.get("last_turn", 0)),
            "last_time": self.data.get("last_time") or "",
            "every_turns": self.every_turns,
            "every_hours": self.every_hours,
            "md_file": str(self.md_file),
        }


# ══════════════════════════════════════════════════════════════════════════
#  命令行
# ══════════════════════════════════════════════════════════════════════════

def _cli():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    j = Journal({})
    args = sys.argv[1:]
    s = j.stats()

    if "--forget" in args:
        # 「模拟人类的记忆系统，不重要的东西就忘掉」（用户原话）。
        # 改规矩之前攒下的那几篇是按老标准写的（一句一行，等于把对话抄了一遍），
        # 想说一句「这些忘了吧」就用这个。长期记忆（他是什么人）一个字不动。
        n = len(j.data.get("entries") or [])
        j.data["entries"] = []
        j.save()
        print("[OK] 忘掉了 %d 篇旧复盘 —— 长期记忆（他是什么人）一个字没动。"
              % n)
        print("     往后只记这五类：关系进展 / 他的信息 / 约定 / 他的性子 /"
              " 她还没弄明白的；流水账不再落笔。")
        return

    if "--list" in args:
        if not s["entries"]:
            print("（还没有复盘）")
            return
        for e in reversed(s["entries"]):
            print("[%s] 第 %s 轮  %s"
                  % (e.get("time"), e.get("at_turn"), str(e.get("text", ""))[:60]))
        return

    print("=" * 62)
    print("达妮娅的备忘录")
    print("=" * 62)
    print()
    print("长期记忆（她心里对他的总账）")
    print("-" * 62)
    print(s["core"] or "（还没攒出来 —— 多聊几轮，程序会自动让她记）")
    print()
    print("历次复盘：%d 次，最近一次 %s" % (s["count"], s["last_time"] or "还没"))
    print("-" * 62)
    for e in (s["entries"] or [])[-5:]:
        print("[%s · 第%s轮] %s" % (e.get("time"), e.get("at_turn"),
                                    str(e.get("text", ""))[:100]))
        print()
    print("（完整内容看 记忆\\备忘录.md）")


if __name__ == "__main__":
    _cli()
