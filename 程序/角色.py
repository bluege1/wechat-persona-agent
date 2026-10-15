#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""角色 —— 多角色的那一层：现在是谁、她的东西在哪、怎么换人。

一份代码 + 几个角色数据包：

    程序\\              代码（就这一份）+ 虚拟环境
    角色\\<角色名>\\      她的人设、世界书、记忆、素材、日程、音色……
                       （本仓库里带了：白佰岚）

以前每个模块开头都写着同一行：

    ROOT = Path(__file__).resolve().parent      # 「脚本自己所在的那个文件夹」

于是「一个进程 = 一个角色」被写死在了 19 个文件、170 处引用里。
现在那一行换成：

    from 角色 import ROOT

`ROOT` 不再是一个真文件夹，而是一个**惰性代理** —— 你写下 `ROOT / "config.yaml"`
的那一刻它才去问「现在是谁」，然后给 `角色\\<当前>\\config.yaml`。
所以：

  · 原代码里 `ROOT / "x"`、`os.path.join(ROOT, "x")`、`str(ROOT)`、`ROOT.iterdir()`
    这些写法**一行都不改**，但它们全落到当前角色身上了；
  · 换人只改一个指针（`角色\\当前.json`），下一次读 ROOT 就是新角色；
  · 于是**别人的人设根本不会被读进内存** —— 不是"让模型忽略她"，
    是那份文件压根没被打开过。这是省 token 最狠的一刀。

三个问题，三个答案：

  「数据模块怎么分？」
      persona.md / 你和我.md / config.yaml / 世界\\ / 记忆\\ / 生活\\ 这些是纯数据，直接放角色包。
      大事.py / 素材.py / 日计划.py 是「数据 + 一小段代码」，也放角色包 ——
      它们内部的 `ROOT = Path(__file__).resolve().parent` 正好指回角色包，所以这几百行一行不用改。
      但**不能用普通的 import 拿它们**：Python 会把模块缓存住，换人还给你旧的那份。
      所以走 `charmod()` —— 按文件路径加载，并且带角色名做 key，换人就是换模块。

  「切换时怎么保证不串？」
      切换 = 改指针 + 重建大脑（Brain）。大脑在构造时就把
      `self.persona_file = ROOT / "persona.md"` 存成了真路径，
      所以不重建的话它会继续读上一个人的文件。
      `大事.py` 里还有一批 import 时算死的常量（剧本索引），
      charmod 每次换角色都重新执行模块体，这些常量跟着重算 —— 不会串剧本。

  「为什么不做成酒馆那样的群聊？」
      酒馆的群聊是把所有角色卡都塞进同一个上下文，靠 {{char}} 变量让模型区分谁在说话 ——
      那是**提示词层面**的隔离，模型一迷糊就串。
      这里切过去之后，另一个角色的文件根本没被读过，是**文件层面**的隔离。

命令行：

    python 角色.py                看有哪些角色、现在是哪个
    python 角色.py --who          只打印当前角色名（给 bat / 脚本用）
    python 角色.py --list         一行一个名字（给脚本用）
    python 角色.py --use 白佰岚    切过去（只改指针，不重启）
    python 角色.py --fresh 白佰岚  把她退回「初遇」（清记忆和关系，不重启机器人）

换人和重置都**不用重启机器人**：它每轮会看一眼 `角色\\当前.json` 和
`角色\\重置戳.json`，看到变化就地重建（见 sync_from_pointer）。

只用标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import random
import re
import sys
from pathlib import Path

# ── 三个基准目录 ──────────────────────────────────────────────────────
#   __file__ = D:\wechat-persona-agent\程序\角色.py
PROG = Path(__file__).resolve().parent          # D:\wechat-persona-agent\程序      —— 代码和 .venv 在这
BASE = PROG.parent                              # D:\wechat-persona-agent           —— bat / 日志 / 小信使 在这
ROLES_DIR = BASE / "角色"                        # D:\wechat-persona-agent\角色      —— 每个角色一个子文件夹
POINTER = ROLES_DIR / "当前.json"                # 现在是哪一个，存这儿
RESET_STAMP = ROLES_DIR / "重置戳.json"           # 「刚被重置到初遇」的记号，存这儿

DEFAULT_CHAR = "白佰岚"                          # 没指针又挑不出来时的兜底

# 核心代码目录必须在 sys.path 里 —— 角色包里的模块（大事/素材/日计划）
# 万一要 import 世界书 这类核心模块，得找得到。
for _d in (str(PROG), str(BASE)):
    if _d not in sys.path:
        sys.path.insert(0, _d)


# ── 现在是谁 ──────────────────────────────────────────────────────────
_STATE: dict = {"name": None, "reset": None}


def _is_char_dir(d) -> bool:
    """一个文件夹算不算「一个角色」。

    ★ 全项目**只有这一个**判定，chars() / exists() / _read_pointer() 都走它。
      以前这里是分开写的：chars() 要求「有 config.yaml 或 persona.md」，
      而 _read_pointer() 只要求「是个文件夹」—— 于是就有一条窄缝：
      指针指向一个「目录刚建好、配置文件还没写」的半成品时，
      _read_pointer() 认它、exists() 不认它，current() 会把整个程序指到一个
      读不出人设的空目录上去。（2026-09-23 建守岸人数据包那会儿正踩过：
      那一刻发 /切换守岸人 会回「没有这个角色」。）
    """
    try:
        if not d.is_dir():
            return False
        n = d.name
        if n.startswith((".", "_")) or n == "__pycache__":
            return False
        return (d / "config.yaml").exists() or (d / "persona.md").exists()
    except OSError:
        return False


def chars() -> list:
    """所有角色名（是文件夹、且里面有 config.yaml 或 persona.md 才算一个角色）。

    顺序：默认角色（见 DEFAULT_CHAR）永远排第一，其余按**文件夹创建时间**。
    —— 直接按名字排中文会得到一串看不出先后的顺序，
       一眼看过去既不是默认角色、也不是你先建的那个，很别扭。
    """
    if not ROLES_DIR.is_dir():
        return []
    out = [d.name for d in sorted(ROLES_DIR.iterdir(), key=lambda x: x.name)
           if _is_char_dir(d)]

    def _order(n: str):
        try:
            born = (ROLES_DIR / n).stat().st_ctime_ns
        except Exception:
            born = 1 << 62
        return (n != DEFAULT_CHAR, born, n)

    out.sort(key=_order)
    return out


def _read_pointer() -> str:
    try:
        j = json.loads(POINTER.read_text(encoding="utf-8"))
        n = str(j.get("当前") or j.get("current") or "").strip()
        # ★ 必须是**真正算角色**的目录，不能只看「是不是文件夹」——
        #   不然一个建了一半的数据包会把整个程序拐过去（见 _is_char_dir 的说明）。
        if n and _is_char_dir(ROLES_DIR / n):
            return n
    except Exception:
        pass
    # 没指针 / 指针指了个不存在的：优先默认名，其次第一个角色
    ls = chars()
    if DEFAULT_CHAR in ls:
        return DEFAULT_CHAR
    return ls[0] if ls else DEFAULT_CHAR


def current() -> str:
    """当前角色名。第一次问的时候才去读盘，之后记在内存里。"""
    if _STATE["name"] is None:
        _STATE["name"] = _read_pointer()
    return _STATE["name"]


def char_dir(name: str | None = None) -> Path:
    """某个角色的数据包目录（真路径）。"""
    return ROLES_DIR / (name or current())


def exists(name: str) -> bool:
    name = (name or "").strip()
    return bool(name) and _is_char_dir(ROLES_DIR / name)


def why_not(name: str) -> str:
    """「这个人为什么不算角色」的一句话 —— **只给失败提示用**。

    为什么要有它：
        「没有这个角色：守岸人」这句话本身没法排查 —— 名字打错了？
        文件夹没建？还是文件夹建了但里面缺 config.yaml / persona.md？
        光看这句话，三种情况长得一模一样。
        （2026-09-23 真出过一次，事后完全说不清是哪一种。）
    """
    name = (name or "").strip().strip("/\\")
    if not name:
        return "没给名字"
    d = ROLES_DIR / name
    if not d.exists():
        return "角色文件夹里没有「%s」" % name
    if not d.is_dir():
        return "「%s」是个文件，不是文件夹" % name
    miss = [f for f in ("config.yaml", "persona.md") if not (d / f).exists()]
    if miss:
        return "文件夹在，但缺 %s" % "、".join(miss)
    return ""


def switch(name: str, persist: bool = True):
    """换人 —— 改内存里的指针（persist=True 时同时落盘）。

    返回 (成功?, 说明)。落盘是为了重启之后还记得是谁。
    注意：**光换指针不够**，调用方还得把大脑（Brain）重建一次 ——
    因为它构造时就把 ROOT 下的文件路径存下来了。
    见 bridge.py 的 切换角色()。

    ★ persist=False 是给「只读工具」用的（查角色.py 就是）：
      它要依次以每个角色的身份起一次 Brain，那就必须改内存指针
      （Brain 是照着 current() 去读 config / persona 的），
      但**绝不能落盘**。落盘会发生什么：
        机器人主循环每轮都会瞄一眼 当前.json，看到变了就
        ① 就地切人 ② 让新的人**真的往微信发一条开场白**。
      于是「跑一遍体检」这个纯只读动作，会在用户微信里凭空冒出消息。
      （2026-09-23 真踩过：跑了几次 查角色.py，用户收到了两条开场白。）
    """
    name = (name or "").strip().strip("/\\")
    if not name:
        return False, "没给角色名"
    if not exists(name):
        ls = chars()
        why = why_not(name)
        tail = ("，可选：" + "、".join(ls)) if ls else "（角色文件夹里一个都没有）"
        return False, "没有这个角色：%s%s%s" % (
            name, ("（%s）" % why) if why else "", tail)
    _STATE["name"] = name
    if not persist:
        return True, name
    try:
        POINTER.parent.mkdir(parents=True, exist_ok=True)
        POINTER.write_text(json.dumps({"当前": name}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        _audit("换人 -> %s" % name)
    except Exception as e:
        return False, "指针没写成：%s" % e
    return True, name


def _audit(what: str):
    r"""把「真的落了盘」的动作记一笔（谁干的、什么时候）。

    为什么要有这个：
        改内存指针是看得见的，写 当前.json 是**看不见的** ——
        而它间接会让微信里凭空冒出消息（机器人瞄到指针变了就切人 + 发开场白）。
        2026-09-23 出过一次：跑体检的工具偷偷落了盘，
        用户微信里多出两条开场白，查了半天才知道是谁干的。
        现在每次落盘都留一行带调用栈的记录，下次一眼就能看出是谁。
    """
    try:
        import datetime as _dt
        import traceback as _tb
        # 调用栈往上翻，跳过 角色.py 自己这几层
        frames = [f for f in _tb.extract_stack()[:-1]
                  if os.path.basename(f.filename) != os.path.basename(__file__)]
        if frames:
            who = " <- ".join("%s:%d" % (os.path.basename(f.filename), f.lineno)
                              for f in frames[-3:])
        else:
            # 没有外部调用者 —— 那就是本文件的命令行入口（--use）
            # 或模块内部的自动调整。写清楚，别让人猜「不明」是什么。
            _argv = " ".join(sys.argv[1:])[:58]
            who = ("命令行 " + _argv) if _argv else "角色.py 内部"
        line = "%s  pid=%-6d %-22s  %s\n" % (
            _dt.datetime.now().strftime("%m-%d %H:%M:%S"),
            os.getpid(), what, who)
        d = BASE / "日志"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "换人.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _peek_pointer():
    """只看「指针文件里明明白白写了谁」，**不做任何兜底**。

    跟 _read_pointer() 的区别（这个区别很重要）：
        _read_pointer() 是指针坏了也要给个能用的答案 —— 回退到默认角色
        （启动时用，这时候总得挑一个出来）。
        _peek_pointer() 是指针坏了就老实说「没读到」 ——
        运行中用它，否则「指针文件被写坏 / 指向一个已经删掉的文件夹」
        会被当成一次**合法的换人**，把正在跑的机器人悄悄换成默认角色。
    """
    try:
        j = json.loads(POINTER.read_text(encoding="utf-8"))
    except Exception:
        return None
    n = str(j.get("当前") or j.get("current") or "").strip()
    if n and (ROLES_DIR / n).is_dir():
        return n
    return None


def _peek_reset():
    """读「重置戳」：谁被重置过、什么时候。没读过或读不到就返回 None。"""
    try:
        j = json.loads(RESET_STAMP.read_text(encoding="utf-8"))
    except Exception:
        return None
    who = str(j.get("who") or "").strip()
    at = str(j.get("at") or "").strip()
    return (who, at) if who and at else None


def mark_reset(name: str):
    """在「重置戳」上记一笔 —— 告诉外面正在跑的那个进程「这人的经历被清空了」。

    控制台「退回首遇」和 `角色.py --fresh` 都会走到这儿。
    机器人在主循环里靠 sync_from_pointer() 看到它，就地重建大脑，
    不然它内存里还揣着老关系、老记忆，重置等于没重置。
    """
    try:
        RESET_STAMP.parent.mkdir(parents=True, exist_ok=True)
        RESET_STAMP.write_text(
            json.dumps({"who": name, "at": dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        _audit("重置 -> %s" % name)
        return True
    except Exception:
        return False


def sync_from_pointer():
    """重新读一遍盘上的指针；**变了就返回新角色名**，没变返回 None。

    为什么需要它：
        进程 A（机器人 bridge）启动时把当前角色记在内存里（_STATE）。
        进程 B（控制台 / 命令行）换人时只写了 当前.json ——
        A 不看盘就永远不知道自己该换成谁，于是「控制台点了切换，微信里没反应」。
        机器人主循环每轮调一次这个函数，控制台换人就即时生效，不用重启。

    两类变化都认：
        · 当前.json 换了人        —— 换了角色
        · 重置戳.json 记了新的一笔 —— 还是同一个人，但经历被清空了，得重读

    只读两个很小的 json，开销可以忽略。
    指针读不到（文件正被写、或指向不存在的角色）→ 当没变，绝不乱切。

    返回的是「该换成的角色名」；调用方拿到名字就重建一遍大脑即可
    （重置的情况名字不变，但重建之后就真的读到空记忆了）。
    """
    try:
        new = _peek_pointer()
    except Exception:
        return None
    if not new:
        return None

    changed = False
    if _STATE["name"] is None:
        # 内存里还没定过是谁（比如刚从别的模块 import 进来就问）——
        # 这不算「换人」，悄悄认下来就行，别触发一次多余的重建。
        _STATE["name"] = new
    elif new != _STATE["name"]:
        _STATE["name"] = new
        changed = True

    stamp = _peek_reset()
    if _STATE["reset"] is None:
        # 第一次看：只记下，不当成变化（启动时本来就是从盘上现读的）
        _STATE["reset"] = stamp
    elif stamp != _STATE["reset"]:
        _STATE["reset"] = stamp
        if stamp and stamp[0] == new:
            changed = True

    return new if changed else None


# ── 惰性 ROOT ─────────────────────────────────────────────────────────
class CharRoot:
    """指向「当前角色数据包」的代理对象。

    原代码里出现过这些用法，全部照顾到了：

        ROOT / "config.yaml"              → __truediv__       （最多，约 120 处）
        os.path.join(ROOT, "记忆")         → __fspath__
        str(ROOT) / f"{ROOT}" / "%s" % ROOT → __str__
        ROOT.iterdir() / ROOT.exists()    → __getattr__ 转发
        os.chdir(ROOT)                    → __fspath__
        (ROOT / "x").read_text()          → __truediv__ 返回真 Path
        ROOT == 某个路径                   → __eq__

    ⚠ 漏掉 __fspath__ 会「静默失败」：`os.path.join(ROOT, "记忆")` 不报错，
      但它会把代理对象变成字符串 `'CharRoot(...)'`，于是路径全错。
      这个坑踩过一次，所以三个协议一起实现。
    """

    __slots__ = ()

    def _p(self) -> Path:
        return char_dir()

    # 路径拼接
    def __truediv__(self, other):
        return self._p() / other

    def __rtruediv__(self, other):
        return Path(other) / self._p()

    # 变成路径 / 字符串
    def __fspath__(self) -> str:
        return str(self._p())

    def __str__(self) -> str:
        return str(self._p())

    def __repr__(self) -> str:
        return "ROOT(%s)" % current()

    # 其余一切（iterdir / exists / name / parent…）转发给真 Path
    def __getattr__(self, name):
        # 只挡代理自己的内部名和 dunder（pickle / copy / inspect 会来探），
        # 单下划线开头的名字照常转发 —— 有些调用方真的会用 Path 的私有属性。
        if name == "_p" or (name.startswith("__") and name.endswith("__")):
            raise AttributeError(name)
        return getattr(self._p(), name)

    # 比较与哈希
    def __eq__(self, other):
        try:
            return self._p() == Path(other)
        except Exception:
            return NotImplemented

    def __hash__(self):
        return hash(str(self._p()))

    def __bool__(self):
        return True


ROOT = CharRoot()


# ── 角色专属的数据模块（大事 / 素材 / 日计划） ──────────────────────────
_MOD_CACHE: dict = {}


def charmod(name: str):
    """按**当前角色**加载一个数据模块，返回模块对象。

    为什么不能直接 import：
        Python 把已导入的模块放进 sys.modules，第二次 import 直接给旧的。
        换角色之后 `import 大事` 还是达妮娅那份 —— 她会演出别人的剧本。

    所以按文件路径加载，并且 key 里带角色名：
        角色\\达妮娅\\大事.py  →  缓存键 "达妮娅@大事"
        角色\\爱弥斯\\大事.py  →  缓存键 "爱弥斯@大事"
    换角色就是换 key，天然不会串。

    顺带解决另一件事：这些模块里 import 时算死的常量（比如剧本索引 _INDEX），
    每次换角色都会重新 exec 一遍，所以跟着重算 —— 不会拿着旧角色的索引去查新角色的剧本。
    """
    who = current()
    key = "%s@%s" % (who, name)
    cached = _MOD_CACHE.get(key)
    if cached is not None:
        return cached

    path = ROLES_DIR / who / (name + ".py")
    if not path.exists():
        raise ImportError("角色「%s」里没有 %s.py（找的是 %s）" % (who, name, path))

    mod_name = "角色_%s_%s" % (who, name)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("加载不了 %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod          # 让模块内的自引用 / pickle 也能work
    spec.loader.exec_module(mod)
    _MOD_CACHE[key] = mod
    return mod


class CharModule:
    """惰性模块代理 —— `大事.Story` 每次都去问「当前角色的大事迹是什么」。

    daily_life.py 顶部写的是 `大事 = 角色.大事`（一个 CharModule），
    所以它内部所有 `大事.xxx` 的写法都不用改，换人之后自动指向新角色。
    """

    __slots__ = ("_modname",)

    def __init__(self, modname: str):
        object.__setattr__(self, "_modname", modname)

    def _m(self):
        return charmod(object.__getattribute__(self, "_modname"))

    def __getattr__(self, attr):
        # ⚠ 别把「代理自己的内部名」转发进死循环 —— 但**不能**一律拦下划线开头的：
        #   作息.py 里那两个真字段就叫 `_WD` / `_SUN`（周中表 / 周日表），
        #   一律拦掉的话 `作息._WD` 会直接 AttributeError。
        #   所以只拦 dunder（__x__，那是 copy / pickle / inspect 在探测），
        #   其余交给真模块去答 —— 模块里没有自然就抛 AttributeError。
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        return getattr(self._m(), attr)

    def __repr__(self):
        return "<角色.%s → %s>" % (object.__getattribute__(self, "_modname"), current())


大事 = CharModule("大事")
素材 = CharModule("素材")
日计划 = CharModule("日计划")
作息 = CharModule("作息")


# ══════════════════════════════════════════════════════════════════════════
#  接触的第一步：她的开场白 + 他叫什么
# ══════════════════════════════════════════════════════════════════════════
#
#  用户定的：每个角色都得有「接触的过程」—— 先打个招呼、再自报家门、
#  再问一句「你叫什么」。不是一上来就跟老熟人一样。
#
#  为什么开场白不用 LLM 现编：
#      第一印象只值一次。让模型自由发挥，它很可能写出
#      「你终于来啦」这种默认跟你很熟的话 —— 跟第 1 档「初遇」直接打架。
#      所以写成稿子（角色\<名>\开场.md），程序照着发：
#      零 token、不跑偏、不吃掉她的人设。
#
#  开场.md 的写法 —— 一个 `## 小标题` 开一段，段里的 **bullet 行**是候选：
#
#      ## 招呼
#      - 在吗
#      - 喂，这个号是本人吗
#
#      ## 自我介绍
#      - 我是达妮娅
#
#  程序**每段挑一句、按段的顺序发出去** —— 段 = 接触的步骤。
#  所以「打招呼 → 自我介绍 → 问名字」就是三段，不用写代码。
#  发过就在 生活\开场.json 记一笔，重启不会再发第二遍。
#
#  ⚠ 只认 bullet（- 或 1.）。`<!-- 说明 -->` 和段标题下的普通文字一律不算台词 ——
#    稿子旁边可以随便写注释，不会被发出去。
#    （踩过：第一版把说明文字也当候选，她的第一句话成了「怎么用这个文件」。）


def _life_dir(name: str | None = None) -> Path:
    """角色的「生活」目录（放各种运行时状态 json）。"""
    return char_dir(name) / "生活"


def 开场白(name: str | None = None) -> list:
    """她该主动说的那几句 —— 每段挑一句，按段的先后排好。

    返回 [] = 没有稿子（或稿子写空了）→ 调用方就当她没有开场白。
    """
    try:
        raw = (char_dir(name) / "开场.md").read_text(encoding="utf-8")
    except Exception:
        return []

    # ⚠ 先整段抠掉 HTML 注释（可能是多行的）。
    #   注释块里常写着"怎么写这个文件"的说明，那些行不带 # 也不带 -，
    #   早期版本的解析器会把它们当成台词发出去 —— 她第一句话就成了使用说明。
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)

    groups: list = []        # [[候选, ...], ...]  每组 = 稿子里的一个 ## 段
    for ln in raw.splitlines():
        t = ln.strip()
        if not t or t.startswith(("//", ">")):
            continue
        if t.startswith("#"):
            # `## xxx` 开新的一段；单个 `#` 只是标题，不开段
            if t.startswith("##"):
                groups.append([])
            continue
        # ★ 只认 bullet 行（- * • · 或 1. 2. 1、2、1) ）。
        #   段标题下面常有一两行说明文字（「她第一句一定是短的」），
        #   那些**不是台词**，不能发出去。所以裸行一律忽略 ——
        #   想让她说，就得写成 bullet。
        m = re.match(r"^(?:[-*•·]|\d+[.、)）])\s*(.+)$", t)
        if not m:
            continue
        body = m.group(1).strip()
        if not body:
            continue
        if not groups:
            groups.append([])
        groups[-1].append(body)

    return [random.choice(g) for g in groups if g]


def 发过开场(name: str | None = None) -> bool:
    """她那一串开场白是不是已经发过了。"""
    try:
        j = json.loads((_life_dir(name) / "开场.json").read_text(encoding="utf-8")) or {}
        return bool(j.get("已发") or j.get("sent"))
    except Exception:
        return False


def 记下开场(name: str | None = None) -> None:
    """记一笔「开场发过了」。"""
    p = _life_dir(name) / "开场.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"已发": True,
             "时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "角色": name or current()},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("  （开场没记上：%s）" % e)


def 清开场(name: str | None = None) -> None:
    """抹掉「发过开场」的记号 —— 下次跑起来她会重新来打招呼。

    控制台的「重新认识」用它；调开场白稿子时也用它。
    """
    try:
        (_life_dir(name) / "开场.json").unlink()
    except Exception:
        pass


# ── 他叫什么 ──────────────────────────────────────────────────────────
#
#  用户定的：「名字就我直接跟他们说，然后她们写入就行」。
#  所以不是程序替他取名，是**她问、他答、她自己记下来** ——
#  走的是她回复末尾那行 `[[名字:XX]]`（见 bridge.py 的 _take_name_mark）。
#  存 关系.json 的 his_name 字段。
#
#  ⚠ 必须走 relation 引擎写，不能直接改文件：
#     relation.Relation 在内存里存着一份 data，它每次 save() 都会整份覆盖 ——
#     绕过它写盘的话，下一次她随便记点什么就把它冲掉了。


def 他的名(name: str | None = None) -> str:
    """他叫什么。空串 = 她还没问过（第 1 档的常态）。"""
    try:
        j = json.loads((char_dir(name) / "关系.json").read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    v = str(j.get("his_name") or "").strip()
    return v


def 记名字(whom: str, name: str | None = None) -> str:
    """把「他叫什么」写进指针角色（或指定角色）的关系档案。

    优先借 relation 引擎写（内存和盘上一起改）；引擎不在就退回直接改文件。
    返回真正写进去的名字。
    """
    whom = (whom or "").strip().strip("　 \t")
    if not whom:
        return ""
    who = name or current()
    try:
        # 走引擎写：_load() 会补全默认字段，save() 用的是引擎自己那套格式。
        # 这里刻意**绕过 __init__** —— 它在构造时会跑 _decay / _recalc_stage /
        # advance_confession，那是「聊完一轮」才该发生的事，后台脚本不该顺手做。
        import relation
        rel = relation.Relation.__new__(relation.Relation)
        rel.path = char_dir(who) / "关系.json"
        rel.data = rel._load()
        rel.data["his_name"] = whom
        rel.data["named_on"] = dt.date.today().isoformat()
        rel.save()
        return whom
    except Exception:
        pass
    try:
        p = char_dir(who) / "关系.json"
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        d["his_name"] = whom
        d["named_on"] = dt.date.today().isoformat()
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return whom
    except Exception as e:
        print("  （名字没记上：%s）" % e)
        return ""


# ── 重新认识：把这个角色的所有「进度」抹回第一天 ─────────────────────
#
#  「重新做一版」用它 —— 记忆、关系档位、生活状态全部归零，
#  人设 / 世界书 / 素材不动（那些是她的"底子"，不是"经历"）。
def 重新认识(name: str | None = None, keep_backup: bool = True) -> list:
    """把这个角色的记忆和关系清零，返回做了哪些事的说明列表。

    注意：**只清"经历"，不动"底子"** —— persona.md / 世界书 / 素材 / 音色还在。
    """
    who = name or current()
    d = char_dir(who)
    done = []
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    def _reset_json(path: Path, new_value, why: str):
        if not path.exists():
            return
        try:
            if keep_backup:
                bak = d / "_历史备份"
                bak.mkdir(parents=True, exist_ok=True)
                (bak / ("%s.bak-重开-%s" % (path.name, stamp))).write_bytes(path.read_bytes())
            path.write_text(json.dumps(new_value, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            done.append(why)
        except Exception as e:
            done.append("%s 没清掉：%s" % (why, e))

    # 记忆
    _reset_json(d / "memory.json", [], "记忆清空")
    # 关系（档位回到 1 = 初遇）
    _reset_json(d / "关系.json",
                {"stage": 1, "intimacy": 0.0, "turns": 0, "first": dt.date.today().isoformat()},
                "关系回到「初遇」")

    # 生活状态：全部归零（今天是哪天、说过什么、用过哪些素材……）
    life = d / "生活"
    if life.is_dir():
        for f in sorted(life.glob("*.json")):
            if f.name == "开场.json":
                continue                      # 开场由 清开场() 单独管
            _reset_json(f, {}, "生活状态清空：%s" % f.name)

    # 长期记忆（journal.py 攒的）
    jdir = d / "记忆"
    if jdir.is_dir():
        try:
            if keep_backup:
                bak = d / "_历史备份"
                bak.mkdir(parents=True, exist_ok=True)
                import shutil
                dst = bak / ("记忆-重开-%s" % stamp)
                if not dst.exists():
                    shutil.copytree(jdir, dst)
            for f in jdir.glob("*.json"):
                f.unlink()
            for f in jdir.glob("*.md"):
                f.unlink()
            done.append("长期记忆清空")
        except Exception as e:
            done.append("长期记忆没清掉：%s" % e)

    清开场(who)
    done.append("开场白重置（下次她会重新打招呼）")

    # 打个戳：万一机器人正在跑，它下一轮就会看到、就地重读（见 sync_from_pointer）。
    # 不打这个戳的话，它内存里还揣着老关系、老记忆，重置等于没重置。
    if mark_reset(who):
        done.append("已通知正在跑的机器人重读（不用重启）")
    return done


# ── 命令行 ────────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="角色：看有哪些人、现在是谁、换一个")
    ap.add_argument("--who", action="store_true", help="只打印当前角色名")
    ap.add_argument("--list", action="store_true", help="一行一个角色名")
    ap.add_argument("--use", metavar="名字", help="切到某个角色（只改指针）")
    ap.add_argument("--dir", action="store_true", help="打印当前角色数据包的绝对路径")
    ap.add_argument("--fresh", metavar="名字", nargs="?", const="",
                    help="把这个角色退回「初遇」（清记忆和关系；不给名字就是当前那个）")
    a = ap.parse_args()

    if a.who:
        print(current())
        return 0
    if a.list:
        for n in chars():
            print(n)
        return 0
    if a.dir:
        print(char_dir())
        return 0
    if a.use:
        ok, msg = switch(a.use)
        print(("[OK] 切到 " + msg) if ok else ("[X] " + msg))
        return 0 if ok else 1
    if a.fresh is not None:
        who = a.fresh or current()
        if not exists(who):
            print("[X] 没有这个角色：%s，可选：%s" % (who, "、".join(chars())))
            return 1
        print("把「%s」退回初遇……" % who)
        for line in 重新认识(who):
            print("  - " + line)
        return 0

    ls = chars()
    if not ls:
        print("[X] 角色目录里一个角色都没有：%s" % ROLES_DIR)
        return 1
    print("角色目录：%s" % ROLES_DIR)
    print()
    cur = current()
    for n in ls:
        d = ROLES_DIR / n
        mark = "  ← 现在是她" if n == cur else ""
        print("  %s %-8s %s%s" % ("●" if n == cur else "○", n, d, mark))
    print()
    print("换人：python 角色.py --use <名字>")
    print("退回初遇：python 角色.py --fresh <名字>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
