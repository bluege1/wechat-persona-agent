# -*- coding: utf-8 -*-
r"""每个角色的数据包体检（多角色版）。

用法（在 程序 目录下）：
    .venv\Scripts\python.exe 查角色.py             全部角色
    .venv\Scripts\python.exe 查角色.py 爱弥斯        只检她

它盯的是**多角色改造后才会出问题的那几件事**
（达妮娅那套老回归在 查错.py 里，两个各管一摊）：

  · 每个角色该有的 10 份文件齐不齐；四个 .py 数据模块的接口对不对
    （素材 / 作息 / 大事 / 日计划 —— 换人时靠这几个自转）
  · 「你和我.md」的档位裁剪真不真（第 1 档读不到恋人的内容）
  · 世界书：自己的私有卡 + 世界\共享 的公用卡，两边都进得来、且不重名
  · **串人设**：真起 Brain 打 _system()，查她的提示里有没有别人的私线
  · **token 隔离**：各角色 system 提示之间的重合度（越低越说明没串）
  · 角色之间的素材 / 作息雷同度（雷同了就说明她们会说一样的话）
  · 禁词 / 模板语法 / 色情字段
 · **接线**：程序里每个「模块.属性」引用都还指得到东西
   （素材池从 daily_life 搬进角色包那次，控制台还在写 daily.POOL，
    页面整块白掉、运行时不点那个页面发现不了 —— 所以这里静态查一遍）
 · **控制台网页**：每个标签页点了会不会画东西（pane 函数漏写 `#main`
   就是"点了没反应"，而且 Python / JS / HTTP 全都不报错），
   外加前端 JS 的语法过一遍 node --check

只读，不改任何文件。全绿退出码 0，有失败退出码 1。
"""
import argparse
import ast
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tokenize

# 输出编码跟随控制台 —— 跟项目其它脚本一致（中文 Windows 上是 GBK）。
# 别写死 utf-8：cmd 里会变成乱码。
sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8",
                       errors="replace")

PROG = os.path.dirname(os.path.abspath(__file__))          # …\程序
BASE = os.path.dirname(PROG)                               # …\微信机器人
ROLES = os.path.join(BASE, "角色")
sys.path.insert(0, PROG)

import 角色 as R                       # noqa: E402
import relation                        # noqa: E402
import 世界书 as WB                    # noqa: E402

FILES = ["config.yaml", "persona.md", "你和我.md", "开场.md",
         "作息.py", "素材.py", "大事.py", "日计划.py",
         "世界/核心.md", "世界/词条.md"]

FAIL = []
WARN = []


# ── 体检开场先「照个相」：当前角色指针 + 重置戳 ────────────────────
#    为什么：这个脚本要依次以每个角色的身份起一次 Brain，那就必须改内存指针
#    （Brain 是照着 current() 去读 config / persona 的），所以它**看起来像**
#    在切人。万一哪次写成落盘（忘了 persist=False），正在跑的机器人就会
#    真的换人、并让新的人往用户微信发一条开场白 ——
#    一个纯只读的体检，凭什么能在人家微信里说话？
#    （2026-09-23 真踩过：跑了几次体检，用户收到三条开场白。）
#    所以：跑完必须证明这两个文件一个字都没动过。
def _snap(p):
    try:
        st = os.stat(p)
        with open(p, "rb") as fh:
            return (st.st_mtime_ns, st.st_size, fh.read())
    except Exception:
        return None


_PTR = str(R.POINTER)
_STAMP = str(R.RESET_STAMP)
_BEFORE = (_snap(_PTR), _snap(_STAMP))


def chk(cond, label, extra=""):
    print("    %s %s%s" % ("[OK]" if cond else "[X ]", label,
                           ("   " + str(extra)) if extra != "" else ""))
    if not cond:
        FAIL.append(label)
    return cond


def warn(cond, label, extra=""):
    if not cond:
        print("    [ !] %s%s" % (label, ("   " + str(extra)) if extra else ""))
        WARN.append(label)


def load_by_path(path):
    name = os.path.splitext(os.path.basename(path))[0]
    key = "_chk_%s_%s" % (name, abs(hash(path)) % 100000)
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _py_text(path):
    """返回一个 .py 里**真会进提示的文字** —— 字符串字面量，去掉 docstring。

    为什么不能用「读全文再剥注释」：模块开头那段三引号说明是写给人看的，
    永远不会被读进提示，而它为了讲清楚常常要点名别的角色
    （例：作息.py 的模板说明写着「没有周末概念的角色，比如守岸人」）。
    用 AST 把「模块/类/函数的第一个字符串」认出来排除掉，比正则可靠。
    """
    try:
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        return ""
    docs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docs.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(a <= node.lineno <= b for a, b in docs):
                continue
            out.append(node.value)
    return "\n".join(out)


def _drop_comments(src):
    """把 `#` 注释原地抹成空格，**保留字符串里的内容**。

    为什么要它：
      · 查「别人的名字有没有进我的人设」时，只看**真会进提示的文字** ——
        .py 里的字符串会进，注释不会（注释里为了讲清楚常常要提别人的名字）。
      · 查「控制台还在不在用搬走的旧属性名」时也用它：修完之后控制台里
        本来就留了一句注释说明旧名字，裸文本比对会把自己写的注释当成真引用。
    用 tokenize 而不是正则：控制台里有一大段 raw 字符串（整个网页，
    里面有 CSS 的 `#fff`），正则会把它们也当注释切掉。
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return src
    lines = src.splitlines(keepends=True)
    for t in toks:
        if t.type != tokenize.COMMENT:
            continue
        (sl, sc), (el, ec) = t.start, t.end
        if sl == el and sl - 1 < len(lines):
            ln = lines[sl - 1]
            lines[sl - 1] = ln[:sc] + " " * max(0, ec - sc) + ln[ec:]
    return "".join(lines)


# ── 每个角色「私有的、绝不该串给别人」的标志词 ──────────────────
#   只放**别人那边绝对不会出现**的名字/专名。
#   世界常识（拉海洛、星炬学院、黑海岸、泰缇斯、金枝地、虚质空间、共鸣…）
#   是所有角色共享的，**不能**放进这个表 —— 放进去就会误报。
PRIVATE = {
    # 注：**不把「达妮娅」本人列进来** —— 另外两人的 config.yaml 本来就借她的音色
    #     （ref_audio: "../达妮娅/音色/达妮娅.wav"），注释里也提到她。
    #     她名字有没有进别人的人设，由下面「别人的名字」那条单独把关。
    "达妮娅": ["西西", "西格莉卡", "娜斯塔霞", "娜波摩", "绯雪",
               "弗洛洛", "残星会", "黯原", "骗术师", "造梦者"],
    # 注：「飞行雪绒」不列进来 —— 达妮娅的剧情里本来就有这个名字
    #     （她实验室的输液架上贴着那张贴纸），那是世界层面的东西，不是串人设。
    "爱弥斯": ["埃拉拉", "诺娃", "塞莱斯特", "纸飞机", "隧群造物"],
    "守岸人": ["调律钢琴", "莫妮卡", "周期时计", "雪烩浓汤"],
    # 注：「雪衫帝」「嘤嘤鹦」不列进来 —— 前者共享卡的罗伊冰原里就写着，
    #     后者是「飞羽同好会」的观察对象，两个都算世界层面的东西，不是私线。
    "西格莉卡": ["怪力少女", "密涅瓦宝藏", "罗伊符文", "历史通识"],
    # 漂泊者：阿布是她右手声痕里的伴生声骸，两把剑的名字也只有她有。
    # 「黑海岸」「调律者」「残星会」「绯雪」「西西」这些要么在共享层里写着、
    # 要么是别人私线里的同伴，不是她的私线，放进来只会误报。
    "漂泊者": ["阿布", "千古洑流", "血誓盟约"],
    # ★ 2026-10-15 加白佰岚：**她的世界整个是原创的**（雾屿 / 幽潮 / 静默署 …），
    #   跟拉海洛那套世界观一点关系都没有。所以她这些专名几乎都「只属于她」——
    #   别人那边出现任何一个都算串了。已核实：这些词不在别的角色包 / 共享世界里。
    #   通用词（灯、雾、修书…）不列 —— 谁都能有一盏灯，放进来只会误报。
    "白佰岚": ["雾屿", "幽潮", "静默署", "引渡人", "半灵", "纪照", "白秀云"],
}

# ── 角色自己的名字 / 昵称（2026-09-23 建西格莉卡数据包时加）──────────
#   为什么非要有这张表：PRIVATE 里会出现**另一个角色自己的名字** ——
#   达妮娅的私线词表里就有「西格莉卡」和「西西」（那是她对挚友的称呼）。
#   于是「西格莉卡的提示里有没有别人的私线」这条会拿「西格莉卡」去查
#   西格莉卡自己的提示 —— 她当然会写自己的名字，那不是串人设。
#   所以算之前先把**我自己的名字/昵称**、以及**所有角色名**剔掉；
#   角色名那一层由「别人的名字」+ NAME_ALLOW 单独把关（原来就是这么分工的）。
ALIAS = {
    "达妮娅": ["达妮娅", "娅娅"],
    "爱弥斯": ["爱弥斯"],
    "守岸人": ["守岸人"],
    "西格莉卡": ["西格莉卡", "西西", "Sigrika"],
    "漂泊者": ["漂泊者"],
    # 白佰岚：真名三个字；别人多半叫她「佰岚」。
    "白佰岚": ["白佰岚", "佰岚"],
}
OTHERS = {w: [x for k, v in PRIVATE.items() if k != w
              for x in v
              if x not in PRIVATE and x not in ALIAS[w]]
          for w in PRIVATE}

# 名字层面的例外 —— 跟剧情设定一致，**不是漏**：
#   达妮娅的私人故事里本来就有爱弥斯的痕迹，来自用户给的世界观原始素材：
#     · 她实验室输液架上那张「飞行雪绒」贴纸，写着这个名字（一个唱歌的女孩）
#     · 虚境作训场是当年爱弥斯「失踪」之后被封的
#   删了这两处就等于把伏笔丢了，所以允许。
#   （反过来不行：爱弥斯 / 守岸人的世界里没有达妮娅的任何痕迹。）
#
#   ★ 西格莉卡 ↔ 达妮娅 是**双向**例外：这两个人在设定里是挚友 ——
#     西格莉卡叫她「娅娅」，达妮娅叫她「西西」。
#     达妮娅的词条里就有一张「西格莉卡（西西）」的卡，西格莉卡那边也得能提娅娅。
NAME_ALLOW = {("达妮娅", "爱弥斯"),
              ("达妮娅", "西格莉卡"), ("西格莉卡", "达妮娅"),
              # ★ 2026-10-05 加漂泊者：她的定位就是「串联所有角色」——
              #   核心卡和词条里写着她跟守岸人 / 爱弥斯 / 西西 / 娅娅的关系，
              #   所以四向全开。
              ("漂泊者", "达妮娅"), ("漂泊者", "爱弥斯"),
              ("漂泊者", "守岸人"), ("漂泊者", "西格莉卡"),
              # 反过来：达妮娅的人设里本来就有「漂泊者」——
              #   persona 写「救我的人就是那个漂泊者」，词条里还留着一张卡。
              ("达妮娅", "漂泊者")}

# 私线词层面的例外 —— 同样是设定本身的要求：
#   娜波摩是**西格莉卡的同族挚友**（两个人一起解密涅瓦宝藏的谜题），
#   但她同时挂在达妮娅的私线词表里（她是达妮娅故事里的关键人物）。
#   所以对西格莉卡放行；**别人（爱弥斯 / 守岸人）仍然不许出现这个词**。
WORD_ALLOW = {("西格莉卡", "娜波摩"),
              # 漂泊者要串联所有人，下面这些词她这边本来就该有：
              #   西西 / 西格莉卡 是她搭过档的人；绯雪是救娅娅那一趟的同伴；
              #   残星会挂在共享的拉海洛人物里，而它同时是达妮娅的私线词。
              ("漂泊者", "西西"), ("漂泊者", "西格莉卡"),
              ("漂泊者", "绯雪"), ("漂泊者", "残星会")}

BANNED = ["罩杯", "处女", "性经", "性偏", "乳头", "乳房", "做爱", "情欲",
          "肉棒", "裸体", "{{", "<%_", "getvar("]

ap = argparse.ArgumentParser(
    description="每个角色的数据包体检（只读，不改东西）")
ap.add_argument("谁", nargs="*", help="只检这几个（不写就全检）")
_a = ap.parse_args()
_miss = [w for w in _a.谁 if not R.exists(w)]
if _miss:
    print("[X] 没有这些角色：%s" % "、".join(_miss))
    print("    现有：%s" % "、".join(R.chars()))
    raise SystemExit(1)
_WHO = list(_a.谁) or R.chars()

print("=" * 68)
print("  角色数据包体检：%s" % "、".join(_WHO))
print("  数据在 %s" % ROLES)
print("  只读，不会动任何文件")
print("=" * 68)
print()

for who in _WHO:
    d = R.char_dir(who)
    print("┌─ %s ─────────────────────────────────" % who)

    # ── 1. 文件齐不齐 ──
    print("  ① 文件")
    for rel in FILES:
        p = d / rel
        chk(p.exists() and p.stat().st_size > 80, rel,
            "" if not p.exists() else "%d 字节" % p.stat().st_size)

    # ── 2. python 数据模块能不能加载、接口全不全 ──
    print("  ② 数据模块")
    mods = {}
    for f, need in [
        ("素材.py", ["WEATHERS", "MOODS", "HUNGERS", "PENDING",
                     "POOL", "QUIRKS", "RARE"]),
        ("作息.py", ["ROUTINE", "SLEEPY", "CLASS_MARKS"]),
        ("大事.py", ["ARCS"]),
        ("日计划.py", []),
    ]:
        p = d / f
        if not p.exists():
            chk(False, "%s 能加载" % f, "文件不在")
            continue
        try:
            m = load_by_path(str(p))
            mods[f] = m
        except Exception as e:
            chk(False, "%s 能加载" % f, "%s: %s" % (type(e).__name__, e))
            continue
        miss = [n for n in need if not hasattr(m, n)]
        chk(not miss, "%s 接口齐" % f, ("缺 " + "、".join(miss)) if miss else "")

    z = mods.get("素材.py")
    if z:
        pool = getattr(z, "POOL", None)
        chk(isinstance(pool, dict) and pool, "素材.POOL 是非空 dict",
            "分类 %d 个 / 条目 %d 条" % (len(pool or {}),
                                      sum(len(v) for v in (pool or {}).values())))
        chk(set(pool or {}) == {"dawn", "morning", "noon", "afternoon",
                                "dusk", "evening", "night"},
            "POOL 的 key 正好是那七个", sorted(pool or {}))
        bad_tuple = []
        for k, v in (pool or {}).items():
            for it in v:
                if not (isinstance(it, (tuple, list)) and len(it) == 2
                        and isinstance(it[0], str)
                        and isinstance(it[1], (int, float))):
                    bad_tuple.append("%s:%r" % (k, it))
        chk(not bad_tuple, "素材.POOL 条目都是 (文本, 话题度)", bad_tuple[:3])
        # 形状对齐达妮娅：天气/心情/想吃/打算 是纯字符串；怪事/罕见 是 (文本, 话题度)
        for nm, kind in [("WEATHERS", str), ("MOODS", str), ("HUNGERS", str),
                         ("PENDING", str), ("QUIRKS", tuple), ("RARE", tuple)]:
            v = getattr(z, nm, None)
            if not isinstance(v, list) or not v:
                chk(False, "素材.%s 是非空 list" % nm); continue
            if kind is str:
                bad = [x for x in v if not isinstance(x, str)]
            else:
                bad = [x for x in v if not (isinstance(x, (tuple, list))
                                            and len(x) == 2
                                            and isinstance(x[1], (int, float)))]
            chk(not bad, "素材.%s 形状对（%s）" % (nm, "纯文本" if kind is str
                                                else "文本+话题度"),
                "%d 条" % len(v) if not bad else bad[:2])

    x = mods.get("作息.py")
    if x:
        rt = getattr(x, "ROUTINE", None)
        chk(isinstance(rt, list) and rt, "作息.ROUTINE 非空",
            "%d 行" % len(rt or []))
        tags = sorted({r[0] for r in (rt or []) if isinstance(r, tuple) and r})
        chk("wd" in tags and "sun" in tags, "两套表都在（wd / sun）", tags)
        badrow = [r for r in (rt or [])
                  if not (isinstance(r, tuple) and len(r) == 6
                          and all(isinstance(r[i], int) for i in (1, 2))
                          and isinstance(r[3], str))]
        chk(not badrow, "每行都是 (表, 起, 止, 文本, talk, key)", badrow[:2])
        # 表要盖满一整天（跨午夜到次日 5 点 = 1740 分）
        for tag in tags:
            rows = sorted([r for r in rt if r[0] == tag], key=lambda r: r[1])
            gap = [i for i in range(1, len(rows)) if rows[i][1] != rows[i - 1][2]]
            chk(not gap and rows and rows[0][1] == 0 and rows[-1][2] >= 1440,
                "%s 表时间无缝盖满一天" % tag,
                "共 %d 行，0 → %d" % (len(rows), rows[-1][2] if rows else 0))
        sl = getattr(x, "SLEEPY", None)
        chk(isinstance(sl, (set, frozenset)) and sl, "作息.SLEEPY 是非空集合",
            "%d 条：%s" % (len(sl or ()), "、".join(sorted(sl or ()))[:60]))
        cm = getattr(x, "CLASS_MARKS", None)
        chk(isinstance(cm, (tuple, list)) and cm, "作息.CLASS_MARKS 非空",
            "%d 条" % len(cm or ()))
        if who == "守岸人":
            blob = " ".join(r[3] for r in rt if isinstance(r, tuple))
            bad = [c for c in ["多维意识图谱学", "隧者材质仿生学", "微重力农业",
                               "虚质逆向工程", "阀限压力心理", "载具训练",
                               "学分", "早课", "课表", "教室"]
                   if c in blob]
            chk(not bad, "守岸人作息里没有星炬学院那一套", bad or "")
        if who == "爱弥斯":
            blob = " ".join(r[3] for r in rt if isinstance(r, tuple))
            chk("早课" in blob or "翘" in blob, "爱弥斯的翘课写在表里")

    s = mods.get("大事.py")
    if s:
        arcs = getattr(s, "ARCS", None) or []
        chk(bool(arcs), "大事.ARCS 非空", "%d 条" % len(arcs))
        noid = [a for a in arcs if not (isinstance(a, dict) and a.get("id"))]
        chk(not noid, "每条剧本都有 id", "%d 条缺" % len(noid))
        # 至少一条是「初遇」
        first_ok = any(re.search(r"meet|初遇|first|见面", str(a.get("id", ""))
                                 + str(a.get("name", "")), re.I) for a in arcs)
        chk(first_ok, "有一条是「初遇」剧本",
            [a.get("id") for a in arcs][:6])
        stages = [a for a in arcs if isinstance(a.get("stages"), (list, dict))
                  and len(a.get("stages") or []) >= 2]
        chk(len(stages) >= len(arcs) * 0.7, "剧本大多有分阶段",
            "%d/%d" % (len(stages), len(arcs)))

    # ── 3. 你和我.md：档位裁剪 ──
    print("  ③ 关系档位")
    yp = d / "你和我.md"
    if yp.exists():
        txt = yp.read_text(encoding="utf-8")
        found = sorted({int(m) for m in re.findall(r"第\s*(\d+)\s*档", txt)})
        chk(set(range(1, 6)) <= set(found), "五个档的标题都在", found)
        for st in (1, 3, 5):
            cut = relation.trim_stages(txt, st)
            chk(0 < len(cut) < len(txt), "第 %d 档能裁出内容" % st,
                "%d → %d 字" % (len(txt), len(cut)))
        # 裁剪的正确表现：没到的档位会渲染成「（还没到这一档，先不看。）」
        ph = "还没到这一档"
        c1 = relation.trim_stages(txt, 1)
        c5 = relation.trim_stages(txt, 5)
        chk(c1.count(ph) >= 3, "第 1 档把后面几档挡住了",
            "占位符 %d 个" % c1.count(ph))
        chk(c5.count(ph) == 0, "第 5 档没有占位符了", "占位符 %d 个" % c5.count(ph))
        # 越往后读到的越多
        lens = [len(relation.trim_stages(txt, s)) for s in range(1, 6)]
        chk(lens[4] > lens[0], "第 5 档比第 1 档长", lens)
        # 隔离：开头那段「他不是你世界里的人」必须每档都在
        key = "手机那头"
        chk(all(key in relation.trim_stages(txt, st) for st in (1, 2, 3, 4, 5)),
            "「手机那头的人 ≠ 你世界里的人」每档都读得到")
        # 每个角色的「他」都得是家人，不是恋人那条线
        fam = ("家人" in txt) or ("家里人" in txt) or ("亲情" in txt)
        chk(fam, "写了「家人 / 亲情」这层关系")
        ok_fam = any(k in txt for k in
                     ["不是恋人", "不是爱情", "亲情，不是爱情",
                      "是亲情，不是爱情", "那是亲情"])
        chk(ok_fam, "明确写了「只有亲情、不是爱情」")
        # 这段声明必须在每一档都读得到（不能只写在某一档里）
        chk(all(("家里人" in relation.trim_stages(txt, s)
                 or "家人" in relation.trim_stages(txt, s))
                for s in range(1, 6)),
            "「家人」这段每档都读得到")

    # ── 4. 开场.md ──
    print("  ④ 开场白")
    old = R.current()
    try:
        R.switch(who, persist=False)
        got = R.开场白()
        chk(isinstance(got, list) and len(got) >= 2,
            "开场白至少两段", "%d 句" % len(got or []))
        for i, line in enumerate(got or [], 1):
            # 下限给 1：开场白本来就该短、试探（「在吗」「喂」「那个」都是好句子）——
            # 2026-09-23 把下限写成 3 字，结果达妮娅的「在吗」被自己判成不合格。
            chk(1 <= len(line) <= 120, "第 %d 句长度合适" % i,
                "%d 字：%s" % (len(line), line[:22]))
            chk(line.strip() == line, "第 %d 句没有首尾空格" % i)
        # 开场 → 发过 → 不再发
        chk(R.发过开场() in (True, False), "发过开场() 可读")
    finally:
        R.switch(old, persist=False)

    # ── 5. 世界书 ──
    print("  ⑤ 世界书")
    old = R.current()
    try:
        R.switch(who, persist=False)
        cfg = json.loads("{}")
        import yaml
        cfg = yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8"))
        book = WB.Book(cfg)
        ents = book.entries
        files = [str(f) for f in book.files()]
        chk(len(ents) >= 10, "词条总数 ≥ 10", "%d 张" % len(ents))
        names = [e.name for e in ents]
        dup = [n for n in set(names) if names.count(n) > 1]
        chk(not dup, "没有重名卡", dup[:5])
        chk(any("共享" in f for f in files) or "共享" in str(book.share_dir),
            "挂了共享目录", str(getattr(book, "share_dir", "")))
        core = book.core()
        chk(bool(core and len(core) > 500), "核心卡非空", "%d 字" % len(core or ""))
        # 共享目录里每一份文件的每张卡，都必须真的被合进 Book.entries
        share_dir = getattr(book, "share_dir", None)
        if share_dir and share_dir.is_dir():
            want = []
            for sf in sorted(share_dir.glob("*.md")):
                want += [e.name for e in WB.parse(sf.read_text(encoding="utf-8"))]
            miss = [n for n in want if n not in names]
            chk(bool(want) and not miss, "共享卡已合入",
                "%d 张共享卡，缺 %s" % (len(want), miss[:4]) if miss
                else "%d 张" % len(want))
            # 自己那份也要合进来
            own = [e.name for e in WB.parse(
                (d / "世界" / "词条.md").read_text(encoding="utf-8"))]
            miss2 = [n for n in own if n not in names]
            chk(bool(own) and not miss2, "自己的私有卡已合入",
                "%d 张，缺 %s" % (len(own), miss2[:4]) if miss2 else "%d 张" % len(own))
            # 私有不跟共享撞名
            clash = [n for n in own if n in want]
            chk(not clash, "私有卡不跟共享卡重名", clash[:5])
    finally:
        R.switch(old, persist=False)

    # ── 6. 泄漏与禁词 ──
    print("  ⑥ 隔离 / 禁词")
    banned_hits = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in
                   ("__pycache__", "_历史备份", "记忆", "日志", "音色",
                    "audio", "表情包", "世界观-原文")]
        for f in files:
            if not f.endswith((".md", ".py", ".yaml", ".yml", ".json", ".txt")):
                continue
            p = os.path.join(root, f)
            try:
                txt = open(p, encoding="utf-8").read()
            except Exception:
                continue
            bh = [w for w in BANNED if w in txt]
            if bh:
                banned_hits.append((os.path.relpath(p, d), bh))
    chk(not banned_hits, "没有禁词 / 模板语法", banned_hits[:3])

    # ── 「别人的名字」不许进自己的人设正文 ──
    #   跟上面那条不同：上面查的是**私线专名**（西西、残星会…），任何文件里都不许有；
    #   这条查的是**另外两个人叫什么**，只在「真会进提示的文本」里查：
    #      · .md 全文（persona / 开场 / 你和我 / 世界\*.md 是原样进提示的）
    #      · .py 去掉注释（数据模块的字符串会进提示，注释不会）
    #      · .yaml / .json / .txt 不查 —— config.yaml 借音色是有意的，
    #        使用说明里写谁的名字更没关系。
    other_hits = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in
                   ("__pycache__", "_历史备份", "记忆", "日志", "音色",
                    "audio", "表情包", "世界观-原文")]
        for f in files:
            if f.startswith("素材.py.bak"):
                continue
            p = os.path.join(root, f)
            try:
                if f.endswith(".md"):
                    txt = open(p, encoding="utf-8").read()
                elif f.endswith(".py"):
                    txt = _py_text(p)
                else:
                    continue
            except Exception:
                continue
            hit = [w for w in _WHO
                   if w != who and w in txt and (who, w) not in NAME_ALLOW]
            if hit:
                other_hits.append((os.path.relpath(p, d), hit))
    chk(not other_hits, "没有别人的名字混进自己的人设", other_hits[:3])

    print("└" + "─" * 60)
    print()

# ── 7. Brain 级：串人设 / token 隔离 ───────────────────────────────
print("=" * 68)
print("  ⑦ 真起 Brain，看 system 提示里有没有别人的东西")
print("=" * 68)
print()
old = R.current()
snap = {}
try:
    for who in _WHO:
        R.switch(who, persist=False)
        import importlib
        import bridge as B
        importlib.reload(B)
        cfg = B.load_config()
        brain = B.Brain(cfg)
        sysp = brain._system(extra="", user_text="你好")
        snap[who] = sysp
        print("  ── %s：system %d 字" % (who, len(sysp)))
        mine = [w for w in PRIVATE[who] if w in sysp]
        print("     自己的私线出现在提示里:", "、".join(mine[:6]) or "（无）")
        bad = [w for w in OTHERS[who]
               if w in sysp and (who, w) not in WORD_ALLOW]
        chk(not bad, "%s 的提示里没有别人的私线" % who, bad[:5])
        # 达妮娅在 persona 里自称「达妮娅」（游戏里的写法），两种都认
        alias = {"达妮娅": ["达妮娅", "达妮娅"]}.get(who, [who])
        chk(any(a in sysp for a in alias), "%s 自己的名字在提示里" % who)
        others = [x for x in _WHO if x != who]
        # 名字层面也认 NAME_ALLOW：西格莉卡 ↔ 达妮娅 是设定里的挚友，
        # 西格莉卡的提示里出现「达妮娅」不算串（她的私线词另有 OTHERS 把关）。
        leaked = [x for x in others
                  if x in sysp and (who, x) not in NAME_ALLOW]
        chk(not leaked, "%s 的提示里没提到别的角色" % who, leaked)
        # 他还不知道名字时，提示里必须明说"不知道"，不然模型会自己编一个
        # （历史上编出来的是「漂泊者」——用户明确说他不是）
        try:
            _rj = json.loads((R.char_dir(who) / "关系.json")
                             .read_text(encoding="utf-8")) or {}
        except Exception:
            _rj = {}
        _hn = str(_rj.get("his_name") or "").strip()
        print("     他知道的名字:", _hn or "（还不知道）")
        chk(_hn in sysp if _hn else
            any(k in sysp for k in ("不知道", "还没问", "叫不出")),
            "%s：名字那一节写对了" % who,
            ("提示里带了「%s」" % _hn) if _hn else "提示里交代了'不知道'")
        # 提示里要**教她**怎么把名字交上来 —— 这是有意写进去的
        chk("[[名字" in sysp or _hn, "%s：教了她怎么记名字" % who)
    print()
    print("  ── 各份提示的相似度（越低越说明没串）")
    for a in snap:
        for b in snap:
            if a < b:
                A, Bb = set(snap[a]), set(snap[b])
                inter = len(A & Bb)
                print("     %s ∩ %s = %.0f%%   %d / %d 字"
                      % (a, b, 100.0 * inter / max(1, len(A | Bb)),
                         inter, len(A | Bb)))
finally:
    R.switch(old, persist=False)

# ── 8. 角色之间：素材和作息不许雷同 ─────────────────────────────
print()
print("=" * 68)
print("  ⑧ 角色之间的雷同度")
print("=" * 68)
print()


def _mat_texts(mod, nm):
    v = getattr(mod, nm, None)
    if v is None:
        return set()
    if isinstance(v, dict):
        v = [x for vs in v.values() for x in vs]
    out = set()
    for it in v:
        t = it[0] if isinstance(it, (tuple, list)) else it
        if isinstance(t, str):
            out.add(t.strip())
    return out


MAT_NAMES = ["WEATHERS", "MOODS", "HUNGERS", "PENDING", "QUIRKS", "RARE", "POOL"]
mat = {}
rout = {}
for who in _WHO:
    d = R.char_dir(who)
    try:
        mat[who] = {n: _mat_texts(load_by_path(str(d / "素材.py")), n)
                    for n in MAT_NAMES}
    except Exception as e:
        mat[who] = {}
        print("  （%s 素材读不出来：%s）" % (who, e))
    try:
        m = load_by_path(str(d / "作息.py"))
        rout[who] = {r[3] for r in m.ROUTINE if isinstance(r, tuple)}
    except Exception:
        rout[who] = set()

names = list(mat)
for i, a in enumerate(names):
    for b in names[i + 1:]:
        A = set().union(*mat[a].values()) if mat[a] else set()
        B = set().union(*mat[b].values()) if mat[b] else set()
        inter = A & B
        pct = 100.0 * len(inter) / max(1, len(B))
        print("  ── %s ↔ %s" % (a, b))
        print("     素材：%s 共 %d 条，其中 %d 条跟 %s 一字不差（%.0f%%）"
              % (b, len(B), len(inter), a, pct))
        if inter:
            print("     样例：", "｜".join(sorted(inter)[:3])[:110])
        warn(pct < 5.0, "%s 的素材跟 %s 不雷同（重复 %.0f%%）" % (b, a, pct))
        RA, RB = rout.get(a, set()), rout.get(b, set())
        rp = 100.0 * len(RA & RB) / max(1, len(RB))
        print("     作息：%s 共 %d 段，跟 %s 重复 %d 段（%.0f%%）"
              % (b, len(RB), a, len(RA & RB), rp))
        warn(rp < 20.0, "%s 的作息跟 %s 不雷同（重复 %.0f%%）" % (b, a, rp))
        print()

# ── 9. 接线：模块属性还对不对得上 ──────────────────────────────────
#   这一节是拿真事故换来的（2026-09-23）：素材池从 daily_life 搬进角色包的
#   素材.py 之后，控制台还在写 `daily.POOL` —— 页面整块白掉，
#   「她那边现在」只剩一行红字 `module 'daily_life' has no attribute 'POOL'`。
#   运行时不点那个页面就发现不了，所以这里**静态**查一遍。
print()
print("=" * 68)
print("  ⑨ 接线：谁引用了哪个模块的哪个属性，都还在吗")
print("=" * 68)
print()

import importlib  # noqa: E402  （ast / tokenize / io 已经在文件头上导过）

_TRACK = {"daily_life", "世界书", "relation", "journal", "分享", "表情包", "约定",
          "修记忆", "重开", "bridge", "控制台", "角色", "读图", "表白"}
_LAZY = {"大事", "素材", "日计划", "作息"}
_SKIP = {"查错.py", "查角色.py"}
_MC = {}


def _mod_of(nm):
    if nm not in _MC:
        try:
            _MC[nm] = importlib.import_module(nm)
        except Exception as e:
            _MC[nm] = e
    return _MC[nm]


def _scan_file(path):
    """返回 [(行号, 说明), ...]。★ 必须按函数作用域分析：
    同一个文件里 `r` 在某个函数里是 角色 模块、在别的函数里是 HTTP 响应 ——
    按文件全局记别名会满屏误报。"""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError as e:
        return [(0, "语法错误：%s" % e)]
    bad = []

    def check(var, mod, attr, ln):
        if mod.startswith("__lazy__"):
            real = mod[8:]
            try:
                getattr(getattr(_mod_of("角色"), real), attr)
            except AttributeError:
                bad.append((ln, "%s.%s 不存在（角色包里的那个模块没这个名字）"
                            % (real, attr)))
            except Exception:
                pass
            return
        m = _mod_of(mod)
        if isinstance(m, Exception):
            return
        if not hasattr(m, attr):
            bad.append((ln, "%s.%s 不存在" % (mod, attr)))

    def bind(node, scope):
        if isinstance(node, ast.Assign):
            c = node.value
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id in ("_mod", "_load", "_imp") and c.args
                    and isinstance(c.args[0], ast.Constant)
                    and isinstance(c.args[0].value, str)
                    and c.args[0].value in _TRACK):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        scope[t.id] = c.args[0].value
        elif isinstance(node, ast.Import):
            for a in node.names:
                nm = a.name.split(".")[0]
                if nm in _TRACK:
                    scope[a.asname or nm] = nm
        elif isinstance(node, ast.ImportFrom):
            nm = (node.module or "").split(".")[0]
            if nm == "角色":
                for a in node.names:
                    if a.name in _LAZY:
                        scope[a.asname or a.name] = "__lazy__" + a.name

    def walk(body, scope):
        for node in body:
            bind(node, scope)
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(node.body, dict(scope))
                continue
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                    mod = scope.get(sub.value.id)
                    if mod:
                        check(sub.value.id, mod, sub.attr, sub.lineno)
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                    for fld in ("body", "orelse", "finalbody"):
                        for n2 in getattr(sub, fld, None) or []:
                            bind(n2, scope)

    walk(tree.body, {})
    return bad


_py = sorted(f for f in os.listdir(PROG)
             if f.endswith(".py") and f not in _SKIP and not f.startswith("_"))
_ref_n = 0
_problems = []
for f in _py:
    for ln, why in _scan_file(os.path.join(PROG, f)):
        _problems.append((f, ln, why))
        print("  [X] %-14s 行%-5s %s" % (f, ln, why))
print("  扫了 %d 个 .py，属性引用全都对得上" % len(_py)
      if not _problems else "  共 %d 处引用对不上（上面）" % len(_problems))
chk(not _problems, "程序\\ 里没有「引用了已搬走的属性」")

def _src(fn):
    """读一个核心 .py，返回「去掉注释」的源码（用文件头上那个 _drop_comments）。"""
    with open(os.path.join(PROG, fn), encoding="utf-8") as fh:
        return _drop_comments(fh.read())


# 实时换人这套接线（控制台/命令行换人 → 机器人不用重启）
_rsrc = _src("角色.py")
chk("def sync_from_pointer" in _rsrc, "角色.py 有 sync_from_pointer（运行中感知换人）")
chk("def _peek_pointer" in _rsrc, "角色.py 有 _peek_pointer（指针坏了不乱切）")
chk("def mark_reset" in _rsrc, "角色.py 有 mark_reset（重置也给机器人报信）")
_bsrc = _src("bridge.py")
chk("角色.sync_from_pointer()" in _bsrc, "bridge 主循环会看指针（控制台换人即时生效）")
_csrc = _src("控制台.py")
chk("daily.POOL" not in _csrc and "daily.QUIRKS" not in _csrc
    and "daily.RARE" not in _csrc,
    "控制台没有直取 daily_life 上已搬走的素材池")
chk("daily.素材.POOL" in _csrc, "控制台从 daily.素材 上取素材池")


print()
print("=" * 68)
print("  ⑩ 控制台网页：每个标签页点了都会画东西吗")
print("=" * 68)
print()
#
# 为什么单独查这个：
#   控制台的网页是一整段 JS，标签页切换长这样 ——
#       if(cur==="chars")    return paneChars();
#       if(cur==="overview") return paneOverview();
#   每个 pane 函数**自己**负责把 HTML 塞进 #main（写在函数末尾）。
#   所以漏写一句 `document.getElementById("main").innerHTML = h;`
#   的后果是：**点这个标签页毫无反应** —— 高亮变了、内容不变，
#   而且 Python 语法合法、JS 语法合法、HTTP 接口全 200，
#   **不报任何错**。2026-09-23 真踩过：paneChars() 结尾写成了 `return h;`。
#
# 判定规则：
#   · 每个 `function paneXxx()` 体内必须有 `getElementById("main").innerHTML`
#   · 例外：明确是「零件」的函数（返回字符串给别的 pane 拼）
_PANE_HELPERS = {"paneStickerCard", "paneConfessForm"}

_page_src = open(os.path.join(PROG, "控制台.py"), encoding="utf-8").read()
_pm = re.search(r'PAGE = r?"""(.*?)"""', _page_src, re.S)
if not _pm:
    chk(False, "控制台.py 里有 PAGE 网页字符串")
else:
    _page = _pm.group(1)
    chk(len(_page) > 10000, "网页字符串不是空的", "%d 字符" % len(_page))

    _lines = _page_src.splitlines()
    _i0 = _page_src[:_pm.start()].count("\n")
    _starts = []
    for _n, _l in enumerate(_lines, 1):
        _m = re.match(r"^\s*function\s+(pane[A-Za-z0-9_]*)\s*\(", _l)
        if _m and _n > _i0:
            _starts.append((_n, _m.group(1)))

    _no_paint = []
    for _k, (_n0, _nm) in enumerate(_starts):
        _n1 = _starts[_k + 1][0] - 1 if _k + 1 < len(_starts) else len(_lines)
        _body = "\n".join(_lines[_n0 - 1:_n1])
        if 'getElementById("main").innerHTML' not in _body \
                and _nm not in _PANE_HELPERS:
            _no_paint.append((_nm, _n0))

    chk(not _no_paint,
        "每个 pane 函数都会写 #main（否则点标签页没反应）",
        ["%s() 行%d" % (a, b) for a, b in _no_paint] if _no_paint else
        "%d 个 pane 函数" % len(_starts))

    # render() 的分发必须覆盖 PANES 里的每一项 —— 漏一个也是"点了没反应"
    _pm2 = re.search(r"var PANES = \[(.*?)\];", _page, re.S)
    _keys = re.findall(r'\["([a-z0-9_]+)"', _pm2.group(1)) if _pm2 else []
    _lookup = {
        "chars": "paneChars", "overview": "paneOverview", "brain": "paneConfig",
        "persona": "paneText", "opening": "paneText", "relation": "paneText",
        "core": "paneText", "book": "paneText", "world": "paneText",
        "worldbook": "paneWorldbook", "share": "paneShare", "favor": "paneFavor",
        "memory": "paneMemory", "life": "paneLife", "story": "paneStory",
        "proactive": "paneProactive", "vision": "paneVision", "tune": "paneConfig",
    }
    _orphan = [k for k in _keys if ('cur==="%s"' % k) not in _page]
    chk(not _orphan, "侧边栏每个标签页 render() 里都有分支",
        _orphan or "%d 个标签页" % len(_keys))
    chk(len(_keys) >= 15, "标签页数量正常", len(_keys))

    # 前端 JS 语法：Python 只管它是一个字符串，写坏了语法也不报错，
    # 但浏览器里整段脚本都不执行 —— 表现跟"点了没反应"一模一样。
    _scripts = re.findall(r"<script>(.*?)</script>", _page, re.S)
    _js = "\n;\n".join(_scripts)
    chk(bool(_js.strip()), "抽得出前端 JS", "%d 字符" % len(_js))
    _node = None
    # 找 node：优先环境变量 NODE_EXE（离线 / 非标准安装时用），否则就当它在 PATH 里。
    for _c in (os.environ.get("NODE_EXE") or "node", "node"):
        try:
            _r = subprocess.run([_c, "--version"], capture_output=True, text=True,
                                timeout=15)
            if _r.returncode == 0:
                _node = _c
                break
        except Exception:
            continue
    if not _node:
        warn(False, "没找到 node，跳过前端 JS 语法检查")
    else:
        _jsf = os.path.join(PROG, "_page_syntax_check.js")
        try:
            with open(_jsf, "w", encoding="utf-8") as _fh:
                _fh.write(_js)
            _r = subprocess.run([_node, "--check", _jsf], capture_output=True,
                                text=True, errors="replace", timeout=60)
            _err = (_r.stderr or "").strip().splitlines()
            chk(_r.returncode == 0, "前端 JS 语法没问题（node --check）",
                _err[-3:] if _err else "")
        finally:
            try:
                os.remove(_jsf)
            except Exception:
                pass


print()
print("=" * 68)
print("  ⑪ 体检自己没动过状态吧？（只读工具不许改盘上的东西）")
print("=" * 68)
print()
#
# 这一节是**自查**，查的是这个文件本身。
# 起因：它开头那两处/四处 `R.switch(who)` 一开始写成了落盘版本，
# 于是「跑一遍体检」= 悄悄换人 + 让新角色往用户微信发开场白。
# 防两层：
#   ① 静态 —— 本文件里每一处 R.switch( 都必须带 persist=False；
#   ② 运行时 —— 开跑前照过相，跑完指针/重置戳必须**一个字节都没变**。
# ② 才是真凭据：哪怕以后换个人来写、写法变了，只要它碰了盘就会红。

_self_src = open(os.path.abspath(__file__), encoding="utf-8").read()
_self_tree = ast.parse(_self_src)
_bad_switch = []
_reset_calls = []
for _node in ast.walk(_self_tree):
    if not isinstance(_node, ast.Call):
        continue
    _fn = _node.func
    _name = getattr(_fn, "attr", None) or getattr(_fn, "id", None)
    if _name == "switch":
        # 要么 persist=False（关键字），要么第二个位置参数是 False
        _ok = any(k.arg == "persist" and getattr(k.value, "value", None) is False
                  for k in _node.keywords)
        if not _ok:
            _bad_switch.append(_node.lineno)
    elif _name == "mark_reset":
        _reset_calls.append(_node.lineno)
chk(not _bad_switch, "本文件里每处 switch( 都没落盘（persist=False）",
    ["行 %d" % n for n in _bad_switch] if _bad_switch else
    "%d 处" % sum(1 for n in ast.walk(_self_tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "switch"))
chk(not _reset_calls, "本文件不会写「重置戳」（那会触动机器人重建）",
    ["行 %d" % n for n in _reset_calls] if _reset_calls else "")

_AFTER = (_snap(_PTR), _snap(_STAMP))
chk(_AFTER[0] == _BEFORE[0], "跑完指针文件没被动过",
    "角色\\当前.json" + ("" if _AFTER[0] == _BEFORE[0] else "  ← 被改过了！"))
chk(_AFTER[1] == _BEFORE[1], "跑完重置戳没被动过",
    "角色\\重置戳.json" + ("" if _AFTER[1] == _BEFORE[1] else "  ← 被改过了！"))

print()
print("=" * 68)
print("  失败 %d 项：%s" % (len(FAIL), "、".join(FAIL) if FAIL else "无"))
print("  提醒 %d 条：%s" % (len(WARN), "、".join(WARN[:8]) if WARN else "无"))
print("=" * 68)
raise SystemExit(1 if FAIL else 0)
