# -*- coding: utf-8 -*-
"""
达妮娅 · 表情包
===============

小信使本来就支持发表情包（指令 type=send_sticker，走「文件进剪贴板 → Ctrl+V → 回车」），
只是达妮娅这边一直没接 —— 她只会发文字和语音条。
这个文件把那条线接上：**她回完话之后，按概率追一张**。

高兴、懒得打字、敷衍、想逗你一下的时候，表情包比多打几个字管用得多。

怎么用
------
1. 往【表情包\\】目录里丢 .gif（推荐，微信认这个格式最好），文件名随便。
2. 打开 config.yaml 里的 sticker.enabled。
3. 想看它认到几张：.venv\\Scripts\\python.exe 表情包.py --list

规矩（都在 config.yaml 的 sticker 段）
--------------------------------------
  enabled          总开关（默认关着 —— 你放好图再打开）
  chance           每回完一次话，有多大概率跟一张
  min_turns        至少聊过多少轮才开始发（刚认识就甩表情包很怪）
  min_gap_minutes  两次之间至少隔多久
  max_per_day      一天最多几张
  exts             认哪些后缀（默认只认 .gif，见下面的说明）

为什么默认只认 .gif
-------------------
小信使发的时候是把文件丢进剪贴板再粘贴的，而它内部固定存成 `.gif` 临时文件。
真 gif 一切正常；png/jpg 改后缀之后微信多半也能认（它是按内容判断的），
但没实测过，所以不放进默认值 —— 你想试就把 exts 加上 .png 看看。

★ 不随机挑，而是「随机 + 最近发过的排后面」——
  不然同一张图会连着出现两三回，一眼就假。
"""

import json
import random
import sys
import time
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
except Exception:
    pass

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
DEFAULT_EXTS = (".gif",)
DEFAULT_DIR = "表情包"
# 最近发过的这几张先不碰，让图轮得开一点
RECENT_KEEP = 12


class Sticker:
    """挑一张表情包，管额度和间隔。发出去的动作由 bridge.py 做。"""

    def __init__(self, cfg: dict = None, readonly: bool = False, root=None):
        s = ((cfg or {}).get("sticker") or {})
        if not isinstance(s, dict):
            s = {}
        self.enabled = bool(s.get("enabled", False))
        self.chance = float(s.get("chance", 0.15))
        self.min_turns = int(s.get("min_turns", 3))
        self.min_gap = float(s.get("min_gap_minutes", 12)) * 60
        self.max_day = int(s.get("max_per_day", 8))
        self.exts = tuple(
            (e if str(e).startswith(".") else "." + str(e)).lower()
            for e in (s.get("exts") or DEFAULT_EXTS))
        self.readonly = bool(readonly)

        base = Path(root) if root else ROOT
        d = Path(str(s.get("dir") or DEFAULT_DIR))
        self.dir = d if d.is_absolute() else (base / d)

        f = Path(str(s.get("state_file") or "生活/表情.json"))
        self.path = f if f.is_absolute() else (base / f)

        self.last_error = ""
        self.state = self._load()

    # ── 记账 ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {}

    def _save(self):
        if self.readonly:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception:
            pass

    def _roll_day(self):
        today = date.today().isoformat()
        if self.state.get("date") != today:
            self.state = {"date": today, "sent": [], "recent": self.state.get("recent") or []}
            self._save()

    def sent_today(self) -> list:
        self._roll_day()
        return list(self.state.get("sent") or [])

    def recent(self) -> list:
        return list(self.state.get("recent") or [])

    # ── 有什么图 ──────────────────────────────────────────────────────

    def files(self) -> list:
        """目录里能用的一共几张（按文件名排序，保证每次看到的是同一个顺序）。"""
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file():
                continue
            if p.name.startswith(".") or p.name.lower() in ("readme.txt", "说明.txt"):
                continue
            if p.suffix.lower() in self.exts:
                out.append(p)
        return out

    def count(self) -> int:
        return len(self.files())

    # ── 能不能发 ──────────────────────────────────────────────────────

    def why_not(self, turns: int = 0) -> str:
        """现在能不能发；不能返回一句人话理由（空串=能发）。"""
        if not self.enabled:
            return "表情包在 config 里关着（sticker.enabled）"
        if not self.dir.is_dir():
            return "还没有 %s 目录 —— 建一个、往里丢 .gif 再打开开关" % self.dir.name
        if not self.files():
            return "%s 目录里没有 %s 文件" % (self.dir.name, "/".join(self.exts))
        if turns < self.min_turns:
            return "才聊过 %d 轮，到 %d 轮才开始发（刚认识就甩表情包很怪）" % (
                turns, self.min_turns)
        self._roll_day()
        n = len(self.sent_today())
        if self.max_day and n >= self.max_day:
            return "今天已经发过 %d 张了（sticker.max_per_day）" % n
        gap = time.time() - float(self.state.get("last") or 0)
        if self.min_gap and gap < self.min_gap:
            return "距上一张才 %.0f 分钟（sticker.min_gap_minutes）" % (gap / 60.0)
        return ""

    def should(self, turns: int = 0) -> bool:
        if self.why_not(turns):
            return False
        return random.random() < self.chance

    # ── 挑一张 ────────────────────────────────────────────────────────

    def pick(self):
        """挑一张。返回 Path；没有可用的返回 None。"""
        pool = self.files()
        if not pool:
            return None
        recent = set(self.recent())
        fresh = [p for p in pool if p.name not in recent]
        candidates = fresh or pool          # 全都在「最近」里了就允许重复
        return random.choice(candidates)

    def mark(self, path):
        """这张发出去了，记一笔。"""
        name = getattr(path, "name", str(path))
        t = time.time()
        self._roll_day()
        self.state.setdefault("sent", []).append(t)
        self.state["last"] = t
        rec = [r for r in (self.state.get("recent") or []) if r != name]
        rec.append(name)
        self.state["recent"] = rec[-RECENT_KEEP:]
        self._save()

    # ── 给控制台看 ────────────────────────────────────────────────────

    def stats(self, turns: int = 0) -> dict:
        self._roll_day()
        pool = self.files()
        rec = set(self.recent())
        sent = self.sent_today()
        why = self.why_not(turns)
        return {
            "enabled": self.enabled,
            "dir": str(self.dir),
            "dir_exists": self.dir.is_dir(),
            "count": len(pool),

            "turns": int(turns),
            "can_send": not why,
            "why_not": why,
            "chance": self.chance,
            "min_turns": self.min_turns,
            "min_gap_minutes": round(self.min_gap / 60.0, 1),
            "max_per_day": self.max_day,
            "exts": list(self.exts),
            "sent_today": len(sent),
            "sent_times": [time.strftime("%H:%M", time.localtime(t)) for t in sorted(sent)],
            "recent": list(self.recent()),
            "files": [{"name": p.name,
                       "kb": round(p.stat().st_size / 1024.0, 1),
                       "recent": p.name in rec}
                      for p in pool[:60]],
            "state_file": str(self.path),
        }


# ══════════════════════════════════════════════════════════════
# 命令行
# ══════════════════════════════════════════════════════════════

def _main(argv):
    import yaml

    args = sys.argv[1:]
    with open(ROOT / "config.yaml", "rb") as f:
        cfg = yaml.safe_load(f) or {}
    st = Sticker(cfg)

    print("=" * 56)
    print("达妮娅 · 表情包")
    print("=" * 56)
    print("目录   : %s" % st.dir)
    print("开关   : %s" % ("开" if st.enabled else "关（config 里 sticker.enabled）"))
    print("格式   : %s" % "/".join(st.exts))
    print("认到的 : %d 张" % st.count())
    print()

    if "--list" in args or not args:
        pool = st.files()
        if not pool:
            if st.dir.is_dir():
                print("目录在，但里面一张图都没有。丢几个 .gif 进去就行（文件名随便）。")
            else:
                print("目录还没有。先建一个：")
                print('  mkdir "%s"' % st.dir)
            return 0
        rec = set(st.recent())
        for p in pool:
            print("  %s%-28s %8.1f KB%s"
                  % ("· " if p.name in rec else "  ", p.name,
                     p.stat().st_size / 1024.0,
                     "   （最近发过，暂时排后面）" if p.name in rec else ""))
        print()
        why = st.why_not(999)
        print("现在能不能发：%s" % (why if why else "能（条件都满足）"))
        print("今天已发     ：%d / %d 张" % (len(st.sent_today()), st.max_day))
        return 0

    if "--pick" in args:
        p = st.pick()
        print("这次会挑：%s" % (p.name if p else "（没有可用的）"))
        return 0

    if "--reset" in args:
        st.state = {"date": date.today().isoformat(), "sent": [], "recent": []}
        st._save()
        print("今天的记录已清空。")
        return 0

    print("用法：")
    print("  python 表情包.py           看目录里有几张、现在能不能发")
    print("  python 表情包.py --list    列出来（同上，更明确）")
    print("  python 表情包.py --pick    试一次「这次会挑哪张」")
    print("  python 表情包.py --reset   清掉今天的发送记录")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
