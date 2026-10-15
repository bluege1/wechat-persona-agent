#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世界书 · 关键词检索式设定库

原来是「每轮都把一万多字的 世界观.md 整篇塞进系统提示」——
不管聊什么都得带上全部设定，token 就烧在这儿。

现在拆成两半：

  世界/核心.md   核心卡 —— 每轮都带，但很短（她那边的基本常识、高压线、
                 手机、吃的）。它保证她不会说错大方向。
  世界/词条.md   词条   —— **说到才展开**。他提到「老家」才给老家那张卡，
                 提到「档案室」才给档案室那张卡。没提到的就不占地方。

词条文件格式（自己加料照这个抄就行）：

    ## 老家
    @关键词: 老家, 小城, 老房子, 巷口那家店
    @标签: 地点
    @常驻: 否
    @权重: 3
    ----
    正文，想写多少写多少。可以多段。

    @关键词  必填。命中其中一个就把这张卡展开。中英文逗号、顿号都能分隔。
    @标签    分类用，控制台按这个分组显示。
    @常驻    写「是」→ 每轮都带上（慎用，等于退回老做法）。
    @权重    1~5，越大越容易被选中（分数乘以它）。

    ── 下面这几条是「不串人设」用的，普通卡片用不着 ──

    @组      互斥组名。**同组最多展开 @组上限 张**（默认 1 张）。
             用在「这几张是同一类，不该同时出现」的卡片上。
             例：三张「某个同学」的卡都写 @组: 同学 → 一次只给一张。
    @组上限  这一组最多取几张，默认 1。
    @需要    主关键词命中了**还不够** —— 必须再命中这儿的至少一个词，卡才展开。
             防的是误翻：写了「@需要: 高三|学长」之后，
             真聊到高三才翻；他随口说「我是你学长」不会把它翻出来。
    @排除    出现这儿任何一个词，这张卡整张不展开（比别的规则都优先）。
    @顺序    同分时谁在前（大的先）。默认 0，也就是按文件里的先后。

匹配规则（简单、可预期，没有玄学）：
  · 关键词直接在最近几句话里出现就算命中，子串匹配；
  · 关键词越长算得越重（「学校食堂」比「食堂」具体）；
  · 他说的话权重最高，其次是最近几轮历史，最后是她那边正在发生的事；
  · 命中太多时按分数排，装不下预算的就不要了（正文整个丢，不截半句）。

只用标准库。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
DEFAULT_DIR = "世界"
DEFAULT_CORE = "核心.md"
DEFAULT_BOOK = "词条.md"

# 标题必须【顶格】。这样文件开头那种缩进 4 格的示例写法不会变成真词条。
HEAD_RE = re.compile(r"^#{2,3}\s+(?!#)(.+?)\s*$")
INDENT_HEAD_RE = re.compile(r"^\s+#{2,3}\s+(?!#)\S")
ATTR_RE = re.compile(r"^\s*@\s*(\S+?)\s*[:：]\s*(.*)$")
RULE_RE = re.compile(r"^\s*-{4,}\s*$")
SPLIT_RE = re.compile(r"[,，、;；|/]+")
STRIP_RE = re.compile(r"[.·・\-_'\u2018\u2019\u201c\u201d\"]+")
SPACE_RE = re.compile(r"[\s\u3000]+")

YES = ("是", "true", "yes", "y", "1", "on", "always", "开")

# ★「光杆代词」——关键词里全是代词 / 语气助词，不带任何一个具体名词。
#   单字会被跳过（见 score），可**三个字就能过 6.0 的门槛**，于是
#   「你自己」「你的」这种词会把无关的话也翻出来。
#   实测（2026-09-21）：卡片「你自己长什么样」上挂了关键词「你自己」，
#   结果一句「用**你自己**的语气跟我打个招呼」把她自己的外貌卡翻了出来 ——
#   用户看到的就是「这卡是瞎翻的吧」。
GENERIC_KEY_RE = re.compile(r"^[你我他她它的们自己这那了吗呢吧啊么就都也还]+$")
GENERIC_MIN = 3


def norm(s: str) -> str:
    """归一化：小写、去空白、去连接符号。这样「S.I.G.M.A.」和「SIGMA」是一回事。"""
    s = (s or "").lower()
    s = SPACE_RE.sub("", s)
    return STRIP_RE.sub("", s)


class Entry:
    __slots__ = ("name", "keys", "tag", "pin", "weight", "order",
                 "group", "group_max", "need", "exclude", "pri", "_lines", "body")

    def __init__(self, name: str, order: int = 0):
        self.name = name
        self.keys: list[str] = []
        self.tag = "其他"
        self.pin = False
        self.weight = 1.0
        self.order = order
        # ★ 防串人设的四件（对应酒馆的 inclusion group / keysecondary / 反选 / priority）
        self.group = ""         # @组：互斥组名 —— 同组最多展开 group_max 张
        self.group_max = 1      # @组上限
        self.need: list[str] = []      # @需要：主关键词命中后，还得再命中这里的一个
        self.exclude: list[str] = []   # @排除：出现这些词就整张不展开
        self.pri = 0.0          # @顺序：同分时谁在前
        self._lines: list[str] = []
        self.body = ""

    @property
    def chars(self) -> int:
        return len(self.body)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "tag": self.tag, "pin": self.pin,
            "weight": self.weight, "keys": list(self.keys),
            "chars": self.chars, "body": self.body,
            "group": self.group, "group_max": self.group_max,
            "need": list(self.need), "exclude": list(self.exclude),
            "pri": self.pri,
        }


def _attr(e: Entry, key: str, val: str) -> None:
    k = key.strip().lower()
    val = (val or "").strip()
    if k in ("关键词", "关键字", "别名", "key", "keys", "kw"):
        for p in SPLIT_RE.split(val):
            p = p.strip()
            if p and p not in e.keys:
                e.keys.append(p)
    elif k in ("标签", "分类", "tag", "tags"):
        if val:
            e.tag = val
    elif k in ("常驻", "pin", "always"):
        e.pin = val.lower() in YES
    elif k in ("权重", "weight", "w"):
        try:
            e.weight = float(val)
        except ValueError:
            pass
    elif k in ("组", "分组", "互斥组", "group"):
        e.group = val
    elif k in ("组上限", "组最多", "group_max", "max"):
        try:
            e.group_max = max(1, int(float(val)))
        except ValueError:
            pass
    elif k in ("需要", "还要", "need", "keysecondary"):
        for p in SPLIT_RE.split(val):
            p = p.strip()
            if p and p not in e.need:
                e.need.append(p)
    elif k in ("排除", "不要", "exclude", "not"):
        for p in SPLIT_RE.split(val):
            p = p.strip()
            if p and p not in e.exclude:
                e.exclude.append(p)
    elif k in ("顺序", "优先", "pri", "priority"):
        try:
            e.pri = float(val)
        except ValueError:
            pass


def _finish(e: Entry) -> Entry:
    lines = list(e._lines)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    e.body = "\n".join(lines).strip()
    e._lines = []
    return e


def parse(text: str, warns: list | None = None) -> list[Entry]:
    """把词条文件解析成 Entry 列表。容错优先：解析不了的行就当正文。

    顺手检查「标题前面缩进了空格」——那是想开新卡却开不出来的常见写法失误。
    文件开头的说明区不算，那儿本来就爱缩进。
    """
    entries: list[Entry] = []
    cur: Entry | None = None
    mode = "head"
    order = 0
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.rstrip()
        m = HEAD_RE.match(line)
        if m:
            if cur is not None:
                entries.append(_finish(cur))
            order += 1
            cur = Entry(m.group(1).strip(), order)
            mode = "head"
            continue
        if cur is None:
            continue                       # 文件开头的说明文字，忽略
        if mode == "head" and INDENT_HEAD_RE.match(raw):
            if warns is not None:
                warns.append("第 %d 行的标题前有空格，会被当成正文（标题要顶格）：%s"
                             % (lineno, line.strip()[:28]))
        if mode == "head":
            if RULE_RE.match(line):
                mode = "body"
                continue
            a = ATTR_RE.match(line)
            if a:
                _attr(cur, a.group(1), a.group(2))
                continue
            if not line.strip():
                continue
            mode = "body"                  # 没写分隔线也认，直接进正文
        cur._lines.append(line)
    if cur is not None:
        entries.append(_finish(cur))
    return entries


def _resolve_share(d: Path, value) -> Path:
    """把 config 里的 `world.share_dir` 变成真路径。

    默认 `共享` → 角色自己的 `世界\\共享\\`（一人一份，各改各的）。

    但更多时候你想要的是**全项目就一份**：拉海洛、星炬学院、黑海岸、共鸣体系
    这些世界常识，三个角色看到的一字不差，改一处三处生效。那种写法是：

        world:
          share_dir: "世界/共享"        # ← 相对「微信机器人」根目录

    于是这里要试两个地方：先按「相对角色包」，不行再按「相对项目根」。
    （两者都存在时以角色包里的那份为准 —— 那是更近的一份，改错了也容易发现。）
    """
    if value is None:
        value = "共享"
    p = Path(str(value))
    if p.is_absolute():
        return p
    local = d / p
    if local.is_dir():
        return local
    rooted = BASE / p
    if rooted.is_dir():
        return rooted
    return local          # 两个都没有：返回角色包里的（is_dir() 会是 False，静默跳过）


class Book:
    """词条库。构造后随文件改动自动重载，改完不用重启。"""

    def __init__(self, cfg: dict | None = None, base=None):
        w = ((cfg or {}).get("world") or {}) if isinstance(cfg, dict) else {}
        self.enabled = bool(w.get("enabled", True))
        base = Path(base) if base else ROOT
        d = base / str(w.get("dir") or DEFAULT_DIR)
        self.core_file = base / str(w.get("core_file") or (DEFAULT_DIR + "/" + DEFAULT_CORE))
        self.book_file = d / str(w.get("book_file") or DEFAULT_BOOK)
        # ★ 共享词条目录：几个角色共用同一批设定卡（拉海洛、星炬学院、共鸣体系、
        #   罗伊冰原…）。放在这儿有三个好处：
        #     · 改一处、三个人都生效，不用抄三份；
        #     · 它们是"说到才展开"的按需层，**不占每轮的常驻额度**（省 token）；
        #     · 角色自己的私人卡仍然只在 世界/词条.md 里，物理上不会串。
        self.share_dir = _resolve_share(d, w.get("share_dir"))
        self.budget = int(w.get("budget_chars", 2500) or 0)
        self.max_entries = int(w.get("max_entries", 4) or 0)
        self.history_lines = int(w.get("history_lines", 6) or 0)
        # 入选门槛：一张卡至少得拿这么多分才展开。
        # 6.0 ≈「他这轮说的话里出现一个两字词」，或「前两轮里出现一个三字词」。
        # 调大 = 更省、更容易缺设定；调小 = 更容易满屏卡片。
        self.min_score = float(w.get("min_score", 6.0) or 0)
        self._entries: list[Entry] | None = None
        self._mtime: float | None = None
        self._parse_warns: list[str] = []
        self.last_hits: list[dict] = []

    # ── 读盘 ───────────────────────────────────────────────────────────
    def files(self) -> list:
        """要读的所有词条文件：角色自己那份 + 共享目录下的每一份。"""
        out = [self.book_file]
        try:
            if self.share_dir.is_dir():
                out += sorted(self.share_dir.glob("*.md"))
        except OSError:
            pass
        return out

    @property
    def entries(self) -> list[Entry]:
        files = self.files()
        mts = []
        for f in files:
            try:
                mts.append(f.stat().st_mtime)
            except OSError:
                pass
        if not mts:
            return self._entries or []
        mt = max(mts)                     # 任何一个文件改了都重读（改完不用重启）
        if self._entries is None or mt != self._mtime:
            self._parse_warns = []
            ents: list[Entry] = []
            for f in files:
                try:
                    ents += parse(f.read_text(encoding="utf-8"), self._parse_warns)
                except Exception:
                    continue
            if ents:
                # 重新编号 order：多个文件拼起来之后，"文件里的先后"就没有意义了，
                # 统一按读取顺序编号，@顺序 才是真正的排序手段。
                for i, e in enumerate(ents, 1):
                    e.order = i
                self._entries = ents
                self._mtime = mt
        return self._entries or []

    def core(self) -> str:
        try:
            return self.core_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # ── 检索 ───────────────────────────────────────────────────────────
    def score(self, texts) -> list[tuple]:
        """texts = [(文本, 权重)]，越重要权重越大。返回 [(分, Entry, [命中的关键词])]。

        门槛按「没乘 @权重 的原始分」算 —— 这样 @权重 只决定谁先被选中、
        谁抢到有限的预算，不会把一张卡的门槛偷偷降低。
        """
        # 先把这一轮的文本归一化一次，@需要 / @排除 都要拿它查
        normed = []
        for t, tw in texts:
            q = norm(t)
            if q and tw > 0:
                normed.append((q, tw))
        all_txt = " ".join(q for q, _ in normed)

        out = []
        for e in self.entries:
            if e.pin or not e.body:
                continue

            # ★ @排除：只要出现就不展开 —— 比别的规则都优先。
            #   例：某张卡写 @排除: 手机那头 → 你提到的场景就直接把它挡掉。
            if e.exclude and any(norm(x) and norm(x) in all_txt for x in e.exclude):
                continue

            total = 0.0
            why: list[str] = []
            for q, tw in normed:
                for k in e.keys:
                    nk = norm(k)
                    if len(nk) < 2:        # 单字太容易误命中，直接不要
                        continue
                    c = q.count(nk)
                    if c:
                        # 指数 1.8：关键词越长，权重给得越陡。
                        # 这样「他这轮随口说的两字词」就能展开，
                        # 而「很早以前的对话里恰好有个两字词」不会白白带出一张卡。
                        total += (len(nk) ** 1.8) * (1 + 0.2 * min(c - 1, 3)) * tw
                        why.append(k)
            if total < self.min_score:
                continue

            # ★ @需要：主关键词命中了还不够，得再命中这儿的至少一个。
            #   这是「防误翻」最有用的一条：把一张卡的触发条件收紧到"上下文对了才给"。
            if e.need:
                got = [x for x in e.need if norm(x) and norm(x) in all_txt]
                if not got:
                    continue
                why.extend(got)

            out.append((total * max(e.weight, 0.1), e, sorted(set(why))))
        # 同分：先看 @顺序（大的在前），再看卡片在文件里的先后
        out.sort(key=lambda x: (-x[0], -x[1].pri, x[1].order))
        return out

    def match(self, texts, budget_chars: int | None = None,
              max_entries: int | None = None) -> dict:
        """挑出这轮该展开的卡片，拼成一段可以塞进系统提示的文字。

        返回 {"text", "hits", "chars", "skipped"}。没命中就是空文本。
        """
        ents = self.entries
        if not ents:
            return {"text": "", "hits": [], "chars": 0, "skipped": []}

        budget = self.budget if budget_chars is None else budget_chars
        cap = self.max_entries if max_entries is None else max_entries

        picked, used, skipped = [], 0, []
        group_used: dict[str, int] = {}     # @组 → 已经取了几张
        for sc, e, why in self.score(texts):
            if cap and len(picked) >= cap:
                skipped.append(e.name)
                continue
            # ★ @组（互斥组）：同一组最多取 group_max 张。
            #   例：三张「同学」卡都写 @组: 同学，一次只给一张 —— 不会三个人一起冒出来。
            if e.group and group_used.get(e.group, 0) >= e.group_max:
                skipped.append("%s（%s 组已满）" % (e.name, e.group))
                continue
            if budget and used + e.chars > budget:
                skipped.append(e.name)
                continue
            picked.append((sc, e, why))
            used += e.chars
            if e.group:
                group_used[e.group] = group_used.get(e.group, 0) + 1

        pins = [e for e in ents if e.pin and e.body]
        blocks = []
        if pins:
            blocks.append("\n\n".join("## " + e.name + "\n" + e.body for e in pins))
        if picked:
            blocks.append("\n\n".join("## " + e.name + "\n" + e.body for _, e, _ in picked))
        if not blocks:
            self.last_hits = []
            return {"text": "", "hits": [], "chars": 0, "skipped": skipped}

        text = (
            "# 你翻到的卡片\n"
            "（这些是你那边的事，说到才展开的。别照着念，就当自己本来就记得，"
            "用平常的话说。）\n\n" + "\n\n".join(blocks)
        )
        hits = ([{"name": e.name, "tag": e.tag, "score": 0.0, "why": [],
                  "chars": e.chars, "pin": True} for e in pins]
                + [{"name": e.name, "tag": e.tag, "score": round(sc, 1),
                    "why": why, "chars": e.chars, "pin": False}
                   for sc, e, why in picked])
        self.last_hits = hits
        return {"text": text, "hits": hits, "chars": len(text), "skipped": skipped}

    def stats(self) -> dict:
        ents = self.entries
        tags: dict[str, int] = {}
        for e in ents:
            tags[e.tag] = tags.get(e.tag, 0) + 1
        groups: dict[str, list] = {}
        for e in ents:
            if e.group:
                groups.setdefault(e.group, []).append(e.name)
        return {
            "file": str(self.book_file),
            "files": [str(f) for f in self.files()],
            "groups": groups,
            "core_file": str(self.core_file),
            "count": len(ents),
            "chars": sum(e.chars for e in ents),
            "keys": sum(len(e.keys) for e in ents),
            "pins": [e.name for e in ents if e.pin],
            "tags": dict(sorted(tags.items())),
            "budget": self.budget,
            "max_entries": self.max_entries,
            "core_chars": len(self.core()),
            "entries": [e.to_dict() for e in ents],
        }

    def audit(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """自检。返回 (问题, 提示)。

        问题 = 真错，得修（没关键词、空正文、重名、单字关键词、标题缩进）。
        提示 = 正常但值得知道（一个关键词挂在两张卡上 → 说它俩都会被翻出来）。
        """
        issues: list[tuple[str, str]] = []
        notes: list[tuple[str, str]] = []
        for msg in self._parse_warns:
            issues.append((msg, "—"))
        seen: dict[str, str] = {}
        names: set[str] = set()
        for e in self.entries:
            if e.name in names:
                issues.append(("词条重名", e.name))
            names.add(e.name)
            if not e.keys:
                issues.append(("没有 @关键词，永远不会被命中", e.name))
            if not e.body.strip():
                issues.append(("正文是空的", e.name))
            for k in list(e.need) + list(e.exclude):
                if len(norm(k)) < 2:
                    issues.append(("@需要/@排除 里的「%s」只有一个字，永远匹配不上" % k, e.name))
            for k in e.keys:
                nk = norm(k)
                if len(nk) < 2:
                    issues.append(("「%s」只有一个字，会被忽略（改成两个字以上）" % k, e.name))
                elif len(nk) >= GENERIC_MIN and GENERIC_KEY_RE.match(nk):
                    issues.append(("「%s」全是代词/虚词 —— %d 个字就能过门槛，"
                                   "随便一句无关的话都能把它翻出来（删掉或换成带名词的说法）"
                                   % (k, GENERIC_MIN), e.name))
                if nk in seen and seen[nk] != e.name:
                    notes.append(("「%s」也挂在 %s 上" % (k, seen[nk]), e.name))
                seen[nk] = e.name
        return issues, notes


# ── 命令行 ─────────────────────────────────────────────────────────────
def _load_cfg(base: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load((base / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="世界书：关键词检索式的设定库")
    ap.add_argument("--list", action="store_true", help="列出所有词条")
    ap.add_argument("--tags", action="store_true", help="按标签统计")
    ap.add_argument("--show", metavar="名字", help="看某一张卡的全文")
    ap.add_argument("--hit", metavar="文本", help="试匹配：这几个字会翻出哪些卡")
    ap.add_argument("--audit", action="store_true", help="自检关键词撞车 / 太短")
    ap.add_argument("--stats", action="store_true", help="总量与省下的字数")
    a = ap.parse_args()

    book = Book(_load_cfg(ROOT))
    if not book.entries:
        print("[X] 没读到词条：%s" % book.book_file)
        return 1

    if a.show:
        for e in book.entries:
            if e.name == a.show or norm(e.name) == norm(a.show):
                print("【%s】  标签 %s  权重 %s  常驻 %s  %d 字"
                      % (e.name, e.tag, e.weight, "是" if e.pin else "否", e.chars))
                print("关键词：" + "、".join(e.keys))
                print("-" * 60)
                print(e.body)
                return 0
        print("[X] 没这张卡：" + a.show)
        return 1

    if a.hit:
        r = book.match([(a.hit, 3.0)])
        st = book.stats()
        print("「%s」→ 命中 %d 张，展开 %d 字（预算 %d）"
              % (a.hit, len(r["hits"]), r["chars"], st["budget"]))
        for h in r["hits"]:
            print("  %-14s [%s]  分 %-6s 命中：%s"
                  % (h["name"], h["tag"], h["score"], "、".join(h["why"]) or "常驻"))
        if r["skipped"]:
            print("  （装不下、这轮先不给：%s）" % "、".join(r["skipped"]))
        return 0

    if a.tags:
        st = book.stats()
        print("按标签（共 %d 条 / %d 字）：" % (st["count"], st["chars"]))
        for t, n in st["tags"].items():
            names = [e.name for e in book.entries if e.tag == t]
            print("  %-6s %2d 条  %s" % (t, n, "、".join(names)))
        if st["pins"]:
            print("常驻（每轮都带）：" + "、".join(st["pins"]))
        return 0

    if a.audit:
        issues, notes = book.audit()
        for why, who in issues:
            print("  [X] %-42s ← %s" % (why, who))
        for why, who in notes:
            print("  [·] 共用关键词：%-32s ← %s" % (why, who))
        if not issues:
            print("  [OK] 没有错的地方。")
        if notes:
            print("  共用关键词 %d 处是正常的 —— 提到它会同时翻出这几张卡。" % len(notes))
        return 0 if not issues else 1

    if a.stats:
        st = book.stats()
        old = 0
        try:
            old = len((ROOT / "世界观.md").read_text(encoding="utf-8"))
        except OSError:
            pass
        core = st["core_chars"]
        print("核心卡   %5d 字（每轮都带）   %s" % (core, Path(st["core_file"]).name))
        print("词条库   %5d 字 / %d 条 / %d 个关键词   %s"
              % (st["chars"], st["count"], st["keys"], Path(st["file"]).name))
        print("标签     " + "、".join("%s %d" % (k, v) for k, v in st["tags"].items()))
        if st["pins"]:
            print("常驻     " + "、".join(st["pins"]))
        print()
        print("每轮实际带上：核心 %d 字 + 命中的卡片（最多 %d 张 / %d 字）"
              % (core, st["max_entries"], st["budget"]))
        if old:
            print("改造前是整篇 %d 字，每轮都发。" % old)
        return 0

    # 默认 = --list
    st = book.stats()
    print("%d 条 / %d 字 / 预算 %d 字 / 最多 %d 张"
          % (st["count"], st["chars"], st["budget"], st["max_entries"]))
    print("%-4s %-6s %-8s %5s  %s" % ("#", "标签", "常驻", "字数", "名字"))
    for i, e in enumerate(book.entries, 1):
        print("%-4d %-6s %-8s %5d  %s" % (i, e.tag, "是" if e.pin else "", e.chars, e.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
