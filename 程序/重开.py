# -*- coding: utf-8 -*-
"""
重开 —— 把她的记忆清空，从第一天重新认识
========================================

「重新开聊」要清的**不止一份文件**。她的记忆分好几层，少清一层，
第一轮她就会照样把旧话复述回来（这坑真踩过，见 README 的「坑十一」）。

  [清] memory.json          最近 40 条对话 —— 每一轮都当原文回放给她，旧话会复读
  [清] 记忆/总结.json       她的复盘 + 长期记忆 —— 每轮取最近 3 篇的开头 90 字
  [清] 记忆/备忘录.md       同上，写成给人看的那一份
  [清] 关系.json            亲密度 / 聊过多少轮 / 档位 —— 重新开聊就是从第 1 档开始
  [清] 生活/约定.json       她答应过他的事（「明天叫你起床」「待会再聊」）——
                            人的约定是跟着关系走的，重新认识就不该再欠着

  [留] 生活/用过的.json     素材消耗账本（她那边发生的事，跟你们聊过什么无关）
  [留] 生活/今天.json       她今天过着什么日子（天气、心情、正在干嘛）
  [留] 生活/剧情.json       她正在演的大事（连续剧接着演）

用法
----
  python 重开.py            先看要清什么，然后清（每个文件先备份一份）
  python 重开.py --dry      只看，不动手
  python 重开.py --force    心跳还新也照清（不推荐 —— 机器人跑着时会被它盖回去）

想反悔
------
  每个被清的文件旁边都留了一份 xxx.bak-重开-<时间>，把名字改回去就行。
"""

from pathlib import Path
import datetime as dt
import io
import json
import os
import shutil
import sys
import time

from 角色 import ROOT, BASE, PROG, current   # 惰性 ROOT：指向"当前角色"的数据包
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 心跳：120 秒内有跳动就算「机器人还在跑」
ALIVE_SECONDS = 120


def _w(s) -> int:
    """字符串的显示宽度（汉字算 2 格）—— 中英混排时用它对齐才好看。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in str(s))


def _pad(s, n: int) -> str:
    return str(s) + " " * max(0, n - _w(s))


def heartbeat_age():
    """机器人上次心跳是几秒前。读不到心跳文件就返回 None。"""
    p = ROOT / "生活" / "在线.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return max(0.0, time.time() - float(d.get("ts") or 0))
    except Exception:
        return None


def _empty_journal() -> dict:
    """和 journal.Journal._load() 的初始结构保持一致。"""
    return {"core": "", "entries": [], "last_turn": 0, "last_time": "", "count": 0}


def _empty_memo() -> str:
    """和 journal.Journal._write_md() 的空版保持一致。"""
    # ★ 2026-10-05：标题跟着当前角色走（原来写死「达妮娅的备忘录」，
    #   于是西格莉卡的备忘录第一行也是「达妮娅的备忘录」）。
    try:
        _who = current()
    except Exception:
        _who = "她"
    return "\n".join([
        "# %s的备忘录" % _who, "",
        "（这是程序自动整理的。上面是「长期记忆」——她心里对他的总账；",
        "  下面是历次复盘。每次总结都会重写这一页。）", "",
        "## 长期记忆", "", "（还没攒出来）", "",
        "## 历次复盘", "", "（还没有）", "",
    ])


def _empty_relation() -> dict:
    """和 relation.Relation._load() 的初始结构保持一致。"""
    today = dt.date.today().isoformat()
    return {
        "first": today,
        "turns": 0,
        "first_msg": "",
        "intimacy": 0.0,
        "peak": 0.0,
        "stage": 1,
        "stage_since": today,
        "daily": {},
        "last": today,
    }


def plan_targets() -> list:
    """[(路径, 说明, 新内容)] —— 新内容是 bytes，None 表示不用重写。"""
    return [
        (ROOT / "memory.json", "最近 40 条对话（每轮原文回放）",
         json.dumps([], ensure_ascii=False, indent=2).encode("utf-8")),
        (ROOT / "记忆" / "总结.json", "她的复盘 + 长期记忆",
         json.dumps(_empty_journal(), ensure_ascii=False, indent=2).encode("utf-8")),
        (ROOT / "记忆" / "备忘录.md", "给人看的那一份复盘",
         _empty_memo().encode("utf-8")),
        (ROOT / "关系.json", "亲密度 / 轮数 / 档位（回到第 1 档）",
         json.dumps(_empty_relation(), ensure_ascii=False, indent=2).encode("utf-8")),
        (ROOT / "生活" / "约定.json", "她答应过他的事（叫他起床 / 待会再聊）",
         json.dumps({"seq": 0, "items": []}, ensure_ascii=False, indent=2).encode("utf-8")),
    ]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    dry = "--dry" in sys.argv
    force = "--force" in sys.argv

    print("=" * 62)
    try:
        _who = current()
    except Exception:
        _who = "她"
    print("  %s · 重开（把记忆清空，从第一天重新认识）" % _who)
    print("=" * 62)
    print()

    # ── ① 机器人还在跑吗 ──────────────────────────────────────────────
    age = heartbeat_age()
    if age is None:
        print("  [ ] 没有心跳文件，当成机器人没在跑。")
    elif age < ALIVE_SECONDS:
        print("  [!] 机器人好像还在跑（心跳 %d 秒前）。" % int(age))
        print("      现在清会被它下一次回话整个盖回来。")
        if not force:
            print()
            print("      先双击「停止」或者关掉它的黑窗口，再跑一次这个脚本。")
            print("      （真要硬清，加 --force。）")
            return 1
        print("      --force 给了，继续。")
    else:
        print("  [OK] 机器人没在跑（心跳 %d 分钟前），可以清。"
              % int(age // 60))
    print()

    # ── ② 列一遍要清的 ────────────────────────────────────────────────
    targets = plan_targets()
    todo = []
    print("── 要清的 ──────────────────────────────────────────────")
    for path, what, blob in targets:
        rel = path.relative_to(ROOT)
        if not path.exists():
            print("  [ ] %s%s" % (_pad(rel, 24), "（本来就没有，跳过）"))
            continue
        try:
            before = path.read_bytes()
        except Exception as e:
            print("  [X] %s读不出来：%s" % (_pad(rel, 24), e))
            return 1
        size = len(before)
        if before == blob:
            print("  [OK] %s%s —— 已经是空的了，不用动" % (_pad(rel, 24), what))
            continue
        todo.append((path, what, blob, size))
        print("  · %s%s（现在 %d 字节）" % (_pad(rel, 24), what, size))
    print()

    print("── 不动的 ──────────────────────────────────────────────")
    for name, why in [
        ("生活/用过的.json", "素材消耗账本 —— 她那边的事，跟你们聊过什么无关"),
        ("生活/今天.json", "她今天过着什么日子"),
        ("生活/剧情.json", "她正在演的大事（连续剧接着演）"),
    ]:
        print("  [留] %s%s" % (_pad(name, 24), why))
    print()

    if not todo:
        print("  [OK] 没有要清的东西 —— 她的记忆本来就是干净的。")
        return 0

    if dry:
        print("  --dry：只看不动。去掉 --dry 就会真清（清之前每个文件都会备份）。")
        return 0

    # ── ③ 备份 ────────────────────────────────────────────────────────
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path, _what, _blob, _size in todo:
        bak = "%s.bak-重开-%s" % (path, stamp)
        try:
            shutil.copy2(path, bak)
            print("  [OK] 备份：%s" % os.path.basename(bak))
        except Exception as e:
            print("  [X] 备份 %s 失败（%s），不清了 —— 别拿没备份的文件冒险。"
                  % (path.name, e))
            return 1
    print()

    # ── ④ 清 ──────────────────────────────────────────────────────────
    done = 0
    for path, what, blob, _size in todo:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with io.open(path, "wb") as f:
                f.write(blob)
            done += 1
            print("  [OK] 清空 %s" % path.relative_to(ROOT))
        except Exception as e:
            print("  [X] 写 %s 失败：%s" % (path.name, e))
            print("      用 *.bak-重开-%s 换回来。" % stamp)
            return 1

    print()
    print("  [OK] 清好了 %d 个文件。现在双击「一键启动.bat」，" % done)
    print("       她会当你们是刚认识 —— 第 1 档，亲密度 0。")
    print()
    print("  想反悔：把 *.bak-重开-%s 改名回原来的文件名就行。" % stamp)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
