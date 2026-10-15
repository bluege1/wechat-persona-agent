# -*- coding: utf-8 -*-
"""把记忆里那几句「她说错的话」换掉 —— 短期和长期，两层都得清。

为什么需要它
------------
她每轮回你之前，程序会把两处旧账当成「真的发生过的事」再读一遍：

  短期  memory.json        最近 40 条对话，**原文整条**回放（config.yaml 的 memory.turns）
  长期  记忆/总结.json     历次复盘，每篇取**开头 90 字**进提示（journal.py 的 prompt_block）

所以只要那儿还留着她亲口说错的句子（灰头发 / 西西红头发 / 你当我在说故事 /
我不说了 / 研究奇怪现象的 / 最后我认输），就等于每轮都在提醒她
「你就是这么说的」—— 光改人设和世界书没用，这里不清，她早晚照着错的复述一遍。

前提：先关掉机器人
------------------
程序跑着的时候，它手里攥着一份内存里的历史，下一次回话就会把 memory.json
整个覆盖回去；攒够轮数还会重写复盘（`journal.py` 的 `save()` 一次写两个文件）。
那两下都会把你的改动盖掉。所以这个脚本会先看一眼心跳（生活\\在线.json）：
心跳还新就等于机器人还在跑，它会直接拒绝，什么都不动。

用法
----
  python 修记忆.py                   看一遍要改什么，然后改（原文件先备份）
  python 修记忆.py --dry             只看，不改
  python 修记忆.py --force           心跳还新也照改（不推荐，容易白改）
  python 修记忆.py --sandbox <目录>  改一份副本（自检用：目录里放 memory.json
                                     和 记忆/总结.json、记忆/备忘录.md）
"""

import io
import json
import os
import shutil
import sys
import time

try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
except Exception:
    pass

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
STATE = os.path.join(ROOT, "生活", "在线.json")
JOURNAL = os.path.join(ROOT, "记忆", "总结.json")
MEMO = os.path.join(ROOT, "记忆", "备忘录.md")

# 心跳多新算「机器人还在跑」
ALIVE_SECONDS = 120


# ── 短期记忆里要改的句子 ──────────────────────────────────────────────
# 左边是她实际说过的原文（memory.json 里那一条），右边换成健康说法。
# 位置是按「她答的是哪一句」对齐的，所以换完对话还是接得上。
FIX = [
    # 他嫌她话里带刺 —— 她甩了一句「我不说了」摆烂
    ("行 你问 我不说了 你问就好",
     "行 你问\n我又没说不答"),

    # 他问「你长什么样子啊」—— 她答的是灰头发，全错
    ("灰头发 眼睛不大 一米六几吧\n就那样 没特别",
     "桃粉色 到腰 发梢那截有点发蓝\n头上翘着一撮呆毛 压不下去\n一米五六吧 挺矮的"),

    # 他问「那你朋友呢？」—— 西西是橙头发，不是红的
    ("西西红头发 挺好认的\n她嫌麻烦 不常打理",
     "西西橙头发 挺好认的\n发梢扎成好几根粗辫子 拖着走"),

    # 他报「早，今天还要上班」—— 她只盯着他那边的天，没报自己的
    ("早 你那边天亮了\n我们这儿也有闹钟 烦得很",
     "早\n我也刚起 在洗脸 眼睛睁不开\n今天头一节是早八 烦"),

    # 前一格才说「我也刚起 在洗脸」，这一格又说「刚爬下床」——
    # 同一件事说两遍，而且「下床」在「洗脸」后面，顺序是倒的。
    # 更糟的是下一句她就该在教室了，留着这句会读成「她还在床上」。
    ("请假呗 我这边也刚爬下床",
     "请假呗 我懂\n我这边没假可请 困也得往教室挪"),

    # 他说请假 —— 「中午再聊」得先给理由，别让他追问
    ("行吧 那中午再聊 我补个觉",
     "行 那你路上小心\n我头一节上到九点四十 下了课趴一会儿，中午再聊"),

    # 结尾挂着光秃秃的一句「我不说了」—— 这是把人往门外推
    ("工作有钱啊 我上课只犯困\n不过你这班真不想上就算了\n我不说了",
     "工作有钱啊 我上课只犯困\n不过你这班真不想上就算了\n行 说别的"),

    # 他问「虚质科学部是什么东西」—— 一句「反正就那样」把天聊死了
    ("研究奇怪现象的 反正就那样",
     "研究虚质的 地底下那种又像雾又像沙的东西\n我天天在那儿上课 基本都在睡"),

    # 他问「你到底是在地球上的哪一个地方」—— 她把自己那边说成了故事
    ("都说了\n我这儿不在地球 你当我在说故事\n就行",
     "我这儿真不在地球\n我住拉海洛 地底下的学园都市 抬头是人造太阳\n我们那儿不叫大学 叫学部"),

    # 他说碰见超载现象 —— 「你信就当」= 又在自我否定
    ("嗯 就当是超载吧 你信就当",
     "嗯 超载 就是虚质涨太满了会溢出来\n你那边也能碰上 说明你那儿也不太平"),

    # 她说「我出来买瓶水就碰见你了」—— 他手里只有那部手机，两个人没见过面。
    # 这句破的是**设定**（「隔着手机」被写成「站在一块儿」），
    # 而且她答的是「他问校园怎么空荡荡的」—— 换成「自己那边的事」正好接得上。
    ("这个点大家都不在上课吗\n我出来买瓶水就碰见你了",
     "这个点都在上课呢\n我出来买瓶水 一路上一个人都没有"),

    # ── 时钟（2026-09-27）──────────────────────────────────────────
    # 两边是**同一个钟**（见 daily_life.context 的时钟同步指标 + persona 里的
    # 「你的钟跟他那边的钟是同一个」）。这几句是她编出「我这边十点」、
    # 还反问他几点 —— 留在历史里她会照着往下演（实测重启后照样硬顶）。
    # 换成「我记错了 / 天幕也是黑的」，历史就干净了，也接得上他后面的话。
    ("啊 我这儿是白天嘛\n跟你那边时间不一样\n我这边十点多了 刚上完早课",
     "啊 我记错了\n我这儿也是凌晨\n天黑着 就手机这点光"),

    ("你那边现在几点了，你先说",
     "我这儿凌晨三点多\n你怎么还没睡"),

    ("我这边十点半 你呢 你那儿几点了",
     "我这边也是凌晨\n跟你是同一个钟 三点多"),

    ("我这边真十点 天幕写着呢\n太阳还没完全透 灰蒙蒙的\n"
     "你那边的钟跟我这边不一样嘛\n你困糊涂了 回去睡会",
     "我这边天幕也是黑的\n上面就写着凌晨\n跟你是同一个钟\n你困糊涂了 回去睡会"),

    # ── 报时式开场（2026-09-28）────────────────────────────────────
    # 她主动发的那条：「00 39那会儿睡前把闹钟往后调了十分钟」。
    # 两个毛病：① 钟点里的冒号被老版断句程序切成了换行、归拢时又用空格粘回来，
    # 于是变成「00 39」（用户原话：「00.39那会，什么鬼」）；
    # ② 引子里给的就是裸钟点，她照念了 —— 真人开口不念表。
    # ①已在 bridge.wechatify / _chop 修掉；这两条是历史里已经落下的脏话，
    # 换成「凌晨那会儿 / 早上那会儿」这种说法，她就照不着错的演了。
    ("00 39那会儿睡前把闹钟往后调了十分钟",
     "凌晨那会儿睡前把闹钟往后调了十分钟"),

    ("06 57那会儿 蓝蝴蝶落在我手背上",
     "早上那会儿 蓝蝴蝶落在我手背上"),

    # 同类的第三条（同日复查磁盘记忆时又抓到一条）：
    # 「07 44 那会儿…」——钟点被切、粘回来还多带一个空格；
    # 同一条里「挺好/看的」也被劈成两行。改法同上，只动报时的那截。
    ("07 44 那会儿在实验室抓了个挺好",
     "早上那会儿在实验室抓了个挺好"),
]


# ── 长期记忆（历次复盘）里要改的句子 ──────────────────────────────────
# 复盘是「她自己的总结」，也就是她的自我认知。
# 这里写错的，等于她自己认定「我就是这么做的」，下回还这么做。
FIX_LONG = [
    # 复盘把「认输」记成了收场方式 —— 这正是「答非所问」那轮要掐掉的行为
    ("今天他问我是不是在点他\n我说实话他又反过来咬我一口\n最后我认输他才消停",
     "今天他问我是不是在点他\n我说实话他不信\n来回揪了几轮\n我把话摆平了才过去"),

    # 复盘记着她用「反正就那样」把虚质科学部糊过去 —— 公开的事她本该大方讲
    ("我没正面回答\n只说研究奇怪现象的\n反正就那样\n他没追问\n我就没往下说",
     "我说了\n研究虚质的东西\n地下那种又像雾又像沙的\n我天天在那儿上课\n基本都在睡\n他没追问\n我就没再展开"),

    ("最后问我虚质科学部的事\n我含糊带过",
     "最后问我虚质科学部的事\n我讲了是干什么的\n没往更深处讲"),
]


# ── 整对删掉的轮次 ────────────────────────────────────────────────────
# 有些「对话」压根不是对话，是**自检脚本的测试词**被写进了历史。
# 实测（2026-09-21）：`check_llm.py`（5-测大脑）的测试词落进了 memory.json，
# 而那句话里带着「你自己」三个字 —— 正好是世界书卡片「你自己长什么样」的
# 关键词之一（3 个字就能过 6.0 的门槛）。后果：之后好几轮，他随便说点什么，
# 控制台都会翻出她的外貌卡。用户原话：「这张卡是瞎几把翻的？」
# 这种轮次**不是改写，是整对删掉**（他一句 + 她一句）。
# （根上已经在 check_llm.py 里堵了：自检改用临时记忆，跑完就删。）
DROP = [
    ("在吗？用你自己的语气跟我打个招呼，一句话就好。", "早 在呢 今天课好多"),
]


def mem_file() -> str:
    """从 config.yaml 里读短期记忆文件名（读不到就用默认的）。"""
    name = "memory.json"
    cfg = os.path.join(ROOT, "config.yaml")
    if os.path.exists(cfg):
        try:
            import yaml  # type: ignore
            with io.open(cfg, encoding="utf-8") as f:
                c = yaml.safe_load(f) or {}
            name = (c.get("memory") or {}).get("file") or name
        except Exception:
            pass
    return name if os.path.isabs(name) else os.path.join(ROOT, name)


def journal_dir() -> str:
    """从 config.yaml 里读复盘目录（读不到就用 记忆）。"""
    d = "记忆"
    cfg = os.path.join(ROOT, "config.yaml")
    if os.path.exists(cfg):
        try:
            import yaml  # type: ignore
            with io.open(cfg, encoding="utf-8") as f:
                c = yaml.safe_load(f) or {}
            d = (c.get("journal") or {}).get("dir") or d
        except Exception:
            pass
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def heartbeat_age():
    """机器人多久没心跳了。读不到就返回 None（当成没在跑）。"""
    try:
        with io.open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        ts = d.get("ts")
        if isinstance(ts, (int, float)) and ts > 0:
            return max(0.0, time.time() - float(ts))
    except Exception:
        pass
    return None


def _head(s: str) -> str:
    """一句话的头一行 —— 用来判断「已经改过了没」。"""
    return (s or "").split("\n")[0]


def _split(pairs, present):
    """把规则分成「已经改过」和「找不到了」两堆。"""
    done = [o for o, n in pairs if _head(n) in present]
    gone = [o for o, n in pairs if _head(n) not in present]
    return done, gone


def _dump(hits, indent="  "):
    """打印一处要改的东西。hits 是 [(定位, 旧, 新)]。"""
    for where, old, new in hits:
        print("%s· %s" % (indent, where))
        print("%s    改前：%s" % (indent, old.replace("\n", " / ")))
        print("%s    改后：%s" % (indent, new.replace("\n", " / ")))
        print()


def _tails(done, gone, indent="  "):
    if done:
        print("%s（另有 %d 条以前已经改过了，现在是对的。）" % (indent, len(done)))
    if gone:
        print("%s（另有 %d 条找不到了 —— 多半是记忆已经往后翻页，跳过。）"
              % (indent, len(gone)))
    if done or gone:
        print()


def scan_hist(hist, pairs):
    """在一份对话列表里找。返回 hits=[(条目号, 旧, 新)], done, gone。"""
    hits, rest = [], []
    for old, new in pairs:
        idx = None
        for i, m in enumerate(hist):
            if m.get("role") != "assistant":
                continue
            c = m.get("content") or ""
            if c == old or old in c:
                idx = i
                break
        (hits.append((idx, old, new)) if idx is not None
         else rest.append((old, new)))
    present = "\n".join((m.get("content") or "") for m in hist
                        if m.get("role") == "assistant")
    done, gone = _split(rest, present)
    return hits, done, gone


def scan_text(text, pairs):
    """在一段自由文本里找。返回 hits=[(旧, 新)], done, gone。"""
    hits = [(old, new) for old, new in pairs if old in text]
    rest = [(old, new) for old, new in pairs if old not in text]
    done, gone = _split(rest, text)
    return hits, done, gone


def apply_text(text: str, hits) -> str:
    for old, new in hits:
        text = text.replace(old, new)
    return text


def main() -> int:
    argv = sys.argv[1:]
    dry = "--dry" in argv
    force = "--force" in argv

    sandbox = None
    if "--sandbox" in argv:
        i = argv.index("--sandbox")
        if i + 1 >= len(argv):
            print("  [X] --sandbox 后面要跟一个目录")
            return 2
        sandbox = os.path.abspath(argv[i + 1])

    # ── ① 要改哪三个文件 ──────────────────────────────────────────────
    jdir = journal_dir()
    live = {
        "短期": mem_file(),
        "长期": os.path.join(jdir, "总结.json"),
        "备忘录": os.path.join(jdir, "备忘录.md"),
    }
    if sandbox:
        # 全是副本：不查心跳，也不碰项目里的真文件
        targets = {
            "短期": os.path.join(sandbox, os.path.basename(live["短期"])),
            "长期": os.path.join(sandbox, os.path.basename(jdir), "总结.json"),
            "备忘录": os.path.join(sandbox, os.path.basename(jdir), "备忘录.md"),
        }
        is_live = False
    else:
        targets = dict(live)
        is_live = True

    print("=" * 62)
    # ★ 别再写死「达妮娅」——这个脚本改的是**当前角色**的数据包，
    #   抬头写错会让人以为动的是别人。名字直接从 ROOT 目录名取。
    _who = os.path.basename(str(ROOT).rstrip("\\/")) or "当前角色"
    print("  %s · 修记忆" % _who)
    print("=" * 62)
    if sandbox:
        print("  改的是副本：%s" % sandbox)
    else:
        print("  短期记忆：%s" % targets["短期"])
        print("  长期记忆：%s" % targets["长期"])
        print("            %s" % targets["备忘录"])
    print()

    if not os.path.exists(targets["短期"]):
        print("  [X] 找不到 %s。" % os.path.basename(targets["短期"]))
        print("      先在「达妮娅」目录里跑一次机器人，它自己会建出来。")
        return 1

    # ── ② 机器人还在跑吗 ──────────────────────────────────────────────
    if is_live:
        age = heartbeat_age()
        if age is None:
            print("  [ ] 没有心跳文件，当成机器人没在跑。")
        elif age < ALIVE_SECONDS:
            print("  [!] 机器人好像还在跑（心跳 %d 秒前）。" % int(age))
            print("      现在改会被它下一次回话整个覆盖掉。")
            if not force:
                print()
                print("      先双击「停止」或者关掉它的黑窗口，再跑一次这个脚本。")
                print("      （真要硬改，加 --force。）")
                return 1
            print("      --force 给了，继续。")
        else:
            print("  [OK] 机器人没在跑（心跳 %d 分钟前），可以改。"
                  % int(age // 60))
        print()

    # ── ③ 短期：一份一份对 ────────────────────────────────────────────
    try:
        with io.open(targets["短期"], encoding="utf-8") as f:
            hist = json.load(f)
    except Exception as e:
        print("  [X] 读不出短期记忆：%s" % e)
        return 1
    if not isinstance(hist, list):
        print("  [X] 短期记忆格式不对（应该是一个列表）。先备份出来看看。")
        return 1

    print("── 短期记忆（每轮原文回放）── %s" % os.path.basename(targets["短期"]))
    print("  现在有 %d 条对话。" % len(hist))
    print()
    h_hits, h_done, h_gone = scan_hist(hist, FIX)
    _dump([("第 %d 条" % i, o, n) for i, o, n in h_hits])
    _tails(h_done, h_gone)

    # 整对删掉的自检轮次（见上面 DROP）
    drops = []
    for i in range(len(hist) - 1):
        if hist[i].get("role") != "user" or hist[i + 1].get("role") != "assistant":
            continue
        u = (hist[i].get("content") or "").strip()
        a = (hist[i + 1].get("content") or "").strip()
        for du, da in DROP:
            if u == du.strip() and a.startswith(da.strip()[:8]):
                drops.append(i)
                print("  · 第 %d–%d 条：自检脚本的测试词混进来了，整对删掉"
                      % (i, i + 1))
                print("      %s" % du)
                print("      %s" % a.replace("\n", " / ")[:60])
                print()

    if not h_hits and not h_done and not h_gone and not drops:
        print("  [OK] 短期记忆里没有要改的东西。")
        print()

    # ── ④ 长期：复盘 + 备忘录 ─────────────────────────────────────────
    j_hits, j_done, j_gone = [], [], []
    journal = None
    jpath = targets["长期"]
    if os.path.exists(jpath):
        try:
            with io.open(jpath, encoding="utf-8") as f:
                journal = json.load(f)
        except Exception as e:
            print("  [!] 读不出长期记忆（%s），这一层跳过。" % e)
            journal = None

    print("── 长期记忆（每轮取最近 3 篇复盘的开头 90 字）──")
    if journal is None:
        print("  [ ] 没有 %s，这一层不用管。" % os.path.basename(jpath))
        print()
    else:
        entries = journal.get("entries") or []
        core = journal.get("core") or ""
        print("  %d 篇复盘%s。" % (len(entries),
                                "，外加一段「长期记忆」总账" if core.strip() else ""))
        print()
        # 每篇复盘各自找 —— 报的时候要说清是哪一篇
        for e in entries:
            _where = "复盘【%s】第 %s 轮" % (e.get("time", "?"),
                                          e.get("at_turn", "?"))
            h, _dd, _gg = scan_text(e.get("text") or "", FIX_LONG)
            for old, new in h:
                j_hits.append((_where, old, new))
        if core.strip():
            h, _dd, _gg = scan_text(core, FIX_LONG)
            for old, new in h:
                j_hits.append(("「长期记忆」总账", old, new))

        # 「已经改过 / 找不到」按整份文件算（一条可能落在不同篇里）
        _done_olds = {o for _w, o, _n in j_hits}
        _rest = [(o, n) for o, n in FIX_LONG if o not in _done_olds]
        _all = core + "\n" + "\n".join((e.get("text") or "") for e in entries)
        j_done, j_gone = _split(_rest, _all)

        _dump(j_hits)
        _tails(j_done, j_gone)
        if not j_hits and not j_done and not j_gone:
            print("  [OK] 长期记忆里没有要改的东西。")
        print()

    memo_txt = None
    mpath = targets["备忘录"]
    if os.path.exists(mpath):
        memo_txt = io.open(mpath, encoding="utf-8").read()
        m_hits = [old for old, new in FIX_LONG if old in memo_txt]
        if m_hits:
            print("  （备忘录.md 里也有 %d 处，一起换掉。）" % len(m_hits))
            print()

    total = len(h_hits) + len(j_hits) + len(drops)

    if total == 0:
        print("  [OK] 该改的地方都已经不是错的写法了，不用动。")
        return 0

    if dry:
        print("  --dry：只看不动。去掉 --dry 就会真改（改之前会备份）。")
        return 0

    # ── ⑤ 备份（只备真要动的文件）────────────────────────────────────
    stamp = time.strftime("%Y%m%d-%H%M%S")
    todo = []
    if h_hits or drops:
        todo.append(targets["短期"])
    if journal is not None and j_hits:
        todo.append(targets["长期"])
    if memo_txt is not None and any(old in memo_txt for old, _n in FIX_LONG):
        todo.append(targets["备忘录"])

    for path in todo:
        bak = "%s.bak-清记忆-%s" % (path, stamp)
        try:
            shutil.copy2(path, bak)
            print("  [OK] 备份：%s" % os.path.basename(bak))
        except Exception as e:
            print("  [X] 备份 %s 失败（%s），不改了 —— 别拿没备份的文件冒险。"
                  % (os.path.basename(path), e))
            return 1
    print()

    # ── ⑥ 改 ─────────────────────────────────────────────────────────
    try:
        # 短期：按条目号定位，只动命中的那一条
        for i, old, new in h_hits:
            c = hist[i].get("content") or ""
            hist[i]["content"] = new if c == old else c.replace(old, new)
        # 自检轮次：整对删掉（倒着删，免得前面的下标错位）
        for i in sorted(drops, reverse=True):
            del hist[i:i + 2]
        with io.open(targets["短期"], "w", encoding="utf-8") as f:
            f.write(json.dumps(hist, ensure_ascii=False, indent=2))

        # 长期：把命中的规则套到每篇复盘上（一篇里可能命中多条）
        if j_hits and journal is not None:
            _pairs = [(o, n) for _w, o, n in j_hits]
            for e in (journal.get("entries") or []):
                e["text"] = apply_text(e.get("text") or "", _pairs)
            if (journal.get("core") or "").strip():
                journal["core"] = apply_text(journal["core"], _pairs)
            with io.open(targets["长期"], "w", encoding="utf-8") as f:
                f.write(json.dumps(journal, ensure_ascii=False, indent=2))

        # 备忘录
        if memo_txt is not None:
            new_memo = memo_txt
            for old, new in FIX_LONG:
                new_memo = new_memo.replace(old, new)
            if new_memo != memo_txt:
                io.open(targets["备忘录"], "w", encoding="utf-8").write(new_memo)
    except Exception as e:
        print("  [X] 写回去失败：%s" % e)
        print("      原文件可能只改了一半，用 *.bak-清记忆-%s 换回来。" % stamp)
        return 1

    print("  [OK] 改好了 %d 处（短期 %d + 长期 %d%s）。"
          % (total, len(h_hits), len(j_hits),
             "，另删掉 %d 对自检轮次" % len(drops) if drops else ""))
    print()
    print("  现在双击「一键启动.bat」重启，她就照新的记了。")
    print()
    print("  想反悔：把 *.bak-清记忆-%s" % stamp)
    print("          改名回原来的文件名就行。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  （取消了）")
        sys.exit(130)
