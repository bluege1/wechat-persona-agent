# -*- coding: utf-8 -*-
"""
达妮娅 · 控制台（本地网页）
===========================

双击上一层的「7-控制台.bat」，浏览器会自己打开一个页面。
在这里你可以在网页里做这些事，不用再去翻文件：

  · 填 API      —— 地址、Key、模型、温度，改完直接生效
  · 改人设      —— persona.md 全文编辑
  · 改世界观    —— 世界观.md 全文编辑
  · 改「你和我」—— 关系备忘、分档位的态度
  · 看记忆      —— 她定期复盘攒下来的长期记忆，也可以手动让她现在就复盘一次
  · 看她的日子  —— 今天几点在干嘛、作息表、事件池、重摇今天
  · 调节奏      —— 语音、打字节奏、主动消息、关系进度快慢

技术上就是一个只监听 127.0.0.1 的小服务（Python 标准库，不用装任何东西）。
你的 API Key 只在你自己这台机器上传，不会跑到别的地方去。

命令行：
  python 控制台.py                 启动，默认 7788 端口
  python 控制台.py --port 8899     换端口
  python 控制台.py --no-browser    不自动开浏览器
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import datetime as dt
import json
import re
import socket
import sys
import threading
import webbrowser

import yaml

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
# 允许在网页里编辑的文件（白名单 —— 这是本地服务，但也没必要开个任意读写的口子）
EDITABLE = {
    "persona": {"file": "persona.md", "title": "人设", "hint": "她是谁、怎么说话"},
    "opening": {"file": "开场.md", "title": "开场白",
                "hint": "她加到你之后主动说的第一串话。按 ## 段分组，"
                        "每段挑一句按顺序发。改完记得删掉 生活\\开场.json 才会重发"},
    "core": {"file": "世界/核心.md", "title": "世界观·核心卡",
             "hint": "每一轮都带的那张，尽量短（省 token）"},
    "book": {"file": "世界/词条.md", "title": "世界观·词条库",
             "hint": "说到才展开的卡片。加料就往这儿加"},
    "world": {"file": "世界观.md", "title": "世界观（整篇版）",
              "hint": "只在 world.enabled=false 时才用，平时只是存档"},
    "relation": {"file": "你和我.md", "title": "你和我", "hint": "关系备忘、分档位的态度"},
}

# 允许在网页里改的配置项（点号路径 -> 显示名）。没列出来的不给改，避免手滑改坏。
EDITABLE_CONFIG = {
    "target": "会话名（微信里你的显示名）",
    "llm.base_url": "接口地址 base_url",
    "llm.api_key": "API Key",
    "llm.model": "模型名",
    "llm.temperature": "温度（越高越像人）",
    "llm.max_tokens": "单条最多多少 token",
    "voice.enabled": "语音总开关",
    "voice.mode": "什么时候发语音",
    "voice.engine": "音色引擎（edge / local）",
    "voice.edge_voice": "edge 音色",
    "voice.rate": "语速",
    "voice.pitch": "音调",
    "behavior.poll_interval": "多久扫一次新消息（秒）",
    "behavior.reply_delay_min": "回复前最短延迟（秒）",
    "behavior.reply_delay_max": "回复前最长延迟（秒）",
    "behavior.wechat_style": "微信口语化（逗号句号改断句）",
    "behavior.split_replies": "长回复拆成几条",
    "behavior.split_chars": "一条最多几个字（超了强行切）",
    "behavior.split_max_parts": "最多几条",
    "behavior.total_chars": "一次回复总字数上限（0=不限）",
    "share.enabled": "分享欲 · 联想（按关系档位控制她说多少）",
    "share.length_by_stage": "字数条数按档位走（关掉用上面那三个固定值）",
    "share.quota": "限制「一天最多提自己几次」",
    "share.query_lines": "联想时往前看几轮的话",
    "share.max_pick": "一次最多递给她几件事",
    "behavior.strip_actions": "剥掉动作描写",
    "behavior.max_per_hour": "每小时最多发几条",
    "behavior.catch_up": "开机补一句（程序没在跑时错过的消息）",
    "behavior.catch_up_max_minutes": "离开超过这么多分钟就不补（0=不限制）",
    "humanize.enabled": "拟人打字节奏",
    "humanize.typing_speed": "每个字大约几秒",
    "life.day_plan.enabled": "她的一天带偏移（起晚了/差点迟到/失眠）",
    "life.day_plan.source": "偏移从哪来（auto=API+本地兜底 / local=只本地）",
    "life.day_plan.horizon_hours": "细日程只给到从现在起几小时",
    "proactive.enabled": "她会主动找你",
    "proactive.min_idle_minutes": "安静多少分钟后才主动",
    "proactive.chance_per_tick": "每次轮询的触发概率",
    "proactive.active_hours.0": "主动消息：从几点",
    "proactive.active_hours.1": "主动消息：到几点",
    "proactive.max_per_day": "每天最多主动几次",
    "proactive.max_per_hour": "每小时最多主动几次",
    "proactive.quiet_when_busy": "★ 上课 / 睡着时不主动开口（他发消息来照样回）",
    "vision.enabled": "她能不能看懂你发的图片",
    "vision.model": "视觉模型（说话那个模型不会看图）",
    "vision.base_url": "读图接口地址（留空＝跟上面 llm 一样）",
    "vision.api_key": "读图 API Key（留空＝跟上面 llm 一样）",
    "vision.max_tokens": "图片描述最多多少 token",
    "vision.max_per_day": "一天最多读几张图（防手滑刷钱）",
    "vision.min_gap_seconds": "两次读图至少隔几秒",
    "vision.max_side": "送模型前把图缩到多小（省 token）",
    "sticker.enabled": "她会发表情包",
    "sticker.chance": "每回完一次话，跟一张的概率",
    "sticker.min_turns": "至少聊过多少轮才开始发",
    "sticker.min_gap_minutes": "两张之间至少隔几分钟",
    "sticker.max_per_day": "一天最多几张",
    "sticker.dir": "表情包放哪个目录",
    "life.enabled": "她有平行生活",
    "life.rare_chance": "摇到「少见的事」的概率",
    "life.reuse_after_days": "素材多久后解禁（0=永不复用）",
    "story.enabled": "会演大事件（连续剧）",
    "story.max_active": "同时最多几件大事在演",
    "story.gap_days.0": "两件大事之间：最少隔几天",
    "story.gap_days.1": "两件大事之间：最多隔几天",
    "journal.enabled": "定期总结记忆",
    "journal.every_turns": "攒多少轮复盘一次",
    "journal.every_hours": "或隔多少小时复盘",
    "journal.min_turns": "少于多少轮不记（免得把寒暄也记下来）",
    "journal.core_chars": "「长期记忆」字数上限（超了模型自己挑重点）",
    "journal.keep_entries": "历次复盘最多留几条（留多了白占 token）",
    "relation.daily_cap": "★ 每天最多涨多少亲密度（决定发展快慢）",
    "relation.idle_days": "几天不聊开始冷下来",
    # ── 好感度 · 表白 · 恋人（第 4 档满了之后那一整套，见 relation.py / 表白.py）──
    #   档位**区间**和**每档效果**不在这里 —— 它们是一张表，在「好感度」页里整表改。
    "confess.line": "★ 表白线：亲密度到多少她才开始暗示（默认 2000）",
    "confess.direct_chance": "★ 你直接表白的基础成功率（默认 0.25＝25%）",
    "confess.prep_scale": "用心分每 1 分加多少成功率（0.006 → 满分 +60%）",
    "confess.repeat_penalty": "随便表白一次扣多少成功率（默认 0.05）",
    "confess.hint_days": "暗示铺满要几天（默认 3：一天深一层，共四层）",
    "confess.her_chance": "★ 铺完之后，她哪天自己开口的概率（每天掷一次，默认 0.12）",
    "confess.give_up_after_days": "她等这么多天还没等到，就把心思收回去（默认 10）",
    "confess.give_up_chance": "到那天「收回去」的概率（默认 0.05）",
    "confess.refuse_drop_to": "★ 你拒绝她 → 亲密度掉回多少（默认 600＝重要的人）",
    "confess.despair_days": "★ 被拒之后自暴自弃几天（默认 7）",
    "confess.despair_gain_scale": "自暴自弃期间亲密度涨得慢多少（0.2＝只剩两成）",
    "confess.prep_weights.len": "用心分·① 话说得够长（默认 10）",
    "confess.prep_weights.name": "用心分·② 叫了她的名字（默认 10）",
    "confess.prep_weights.past": "用心分·③ 提到你们之间的事（默认 20）",
    "confess.prep_weights.why": "用心分·④ 说清为什么是她（默认 15）",
    "confess.prep_weights.night": "用心分·⑤ 深夜说的（默认 10）",
    "confess.prep_weights.build": "用心分·⑥ 这几天一直在铺垫（默认 15）",
    "confess.prep_weights.gift": "用心分·⑦ 带着东西（照片/语音/礼物，默认 20）",
}

_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════
#  配置读写（行级替换，保留注释和排版）
# ══════════════════════════════════════════════════════════════════════════

def _fmt_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{},&*?|<>=!%@`\"']", s) or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _split_comment(rest: str):
    """把 '  值   # 注释' 拆成 (值, 注释原文)。引号里的 # 不算。"""
    out, quote = [], None
    for i, ch in enumerate(rest):
        if quote:
            if ch == quote:
                quote = None
            out.append(ch)
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or rest[i - 1] in " \t"):
            return "".join(out), rest[i:]
        out.append(ch)
    return "".join(out), ""


def patch_yaml(text: str, dotted: str, value):
    """在 YAML 文本里按 'a.b.c' 找到那个 key，只换它的值，其它一个字不动。

    dotted 末尾是数字时按「列表下标」处理（例如 proactive.active_hours.0），
    支持 `actice_hours: [9, 23]` 这种行内写法。

    找不到就返回 None（调用方会放弃，绝不新建半截配置）。
    """
    parts = dotted.split(".")
    idx = None
    if parts[-1].isdigit() and len(parts) >= 2:
        idx = int(parts[-1])
        parts = parts[:-1]
    key = parts[-1]
    lines = text.split("\n")

    start, end, indent = 0, len(lines), 0
    for p in parts[:-1]:
        found = None
        for i in range(start, end):
            ln = lines[i]
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            m = re.match(r"^(\s*)([A-Za-z_][\w\-]*)\s*:", ln)
            if m and m.group(2) == p and len(m.group(1)) == indent:
                found = i
                break
        if found is None:
            return None
        indent = len(re.match(r"^(\s*)", lines[found]).group(1)) + 2
        nxt = len(lines)
        for j in range(found + 1, len(lines)):
            ln = lines[j]
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            m2 = re.match(r"^(\s*)\S", ln)
            if m2 and len(m2.group(1)) < indent:
                nxt = j
                break
        start, end = found + 1, nxt

    for i in range(start, end):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w\-]*)\s*:(.*)$", ln)
        if not m or m.group(2) != key or len(m.group(1)) != indent:
            continue

        if idx is None:
            _old, comment = _split_comment(m.group(3))
            cmt = ("  " + comment.strip()) if comment.strip() else ""
            lines[i] = "%s%s: %s%s" % (m.group(1), key, _fmt_value(value), cmt)
            return "\n".join(lines)

        # 列表元素：[9, 23]
        valpart, comment = _split_comment(m.group(3))
        valpart = valpart.strip()
        if not (valpart.startswith("[") and valpart.endswith("]")):
            return None
        items = [x.strip() for x in valpart[1:-1].split(",")]
        if idx >= len(items):
            return None
        items[idx] = _fmt_value(value).strip('"')
        cmt = ("  " + comment.strip()) if comment.strip() else ""
        lines[i] = "%s%s: [%s]%s" % (m.group(1), key, ", ".join(items), cmt)
        return "\n".join(lines)
    return None


def read_config() -> dict:
    p = ROOT / "config.yaml"
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def get_by_path(cfg: dict, dotted: str):
    cur = cfg
    for k in dotted.split("."):
        if isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(k)]
            except Exception:
                return None
        elif isinstance(cur, dict):
            if k not in cur:
                return None
            cur = cur[k]
        else:
            return None
    return cur


def write_config_values(pairs: dict):
    """pairs: {'llm.api_key': 'sk-xxx', ...}。全部成功才落盘。"""
    p = ROOT / "config.yaml"
    text = p.read_text(encoding="utf-8")
    new = text
    done, failed = [], []
    for dotted, value in pairs.items():
        if dotted not in EDITABLE_CONFIG:
            failed.append(dotted)
            continue
        out = patch_yaml(new, dotted, value)
        if out is None:
            failed.append(dotted)
        else:
            new = out
            done.append(dotted)
    if done:
        backup = p.with_suffix(".yaml.bak")
        try:
            backup.write_text(text, encoding="utf-8")
        except Exception:
            pass
        p.write_text(new, encoding="utf-8")
    return done, failed


# ══════════════════════════════════════════════════════════════════════════
#  LLM（控制台自己调，不依赖 bridge）
# ══════════════════════════════════════════════════════════════════════════

def llm_chat(messages, max_tokens: int = 900) -> str:
    import requests

    cfg = read_config()
    llm = cfg.get("llm", {}) or {}
    base = str(llm.get("base_url", "")).strip().rstrip("/")
    if "cloud.siliconflow.cn" in base:
        base = base.replace("cloud.siliconflow.cn", "api.siliconflow.cn")
    if not base.endswith("/v1"):
        base += "/v1"
    url = base + "/chat/completions"

    r = requests.post(
        url,
        headers={"Authorization": "Bearer %s" % llm.get("api_key", ""),
                 "Content-Type": "application/json"},
        json={
            "model": llm.get("model"),
            "messages": messages,
            "temperature": float(llm.get("temperature", 1.2)),
            "max_tokens": int(max_tokens),
        },
        timeout=120,
    )
    if "application/json" not in (r.headers.get("Content-Type") or ""):
        raise RuntimeError("接口返回的不是 JSON（HTTP %s）。检查 base_url 和 Key。"
                           % r.status_code)
    data = r.json()
    if r.status_code != 200 or "choices" not in data:
        raise RuntimeError("接口报错：%s" % (data.get("message") or data)[:200])
    return (data["choices"][0]["message"].get("content") or "").strip()


# ══════════════════════════════════════════════════════════════════════════
#  数据汇总
# ══════════════════════════════════════════════════════════════════════════

def _mod(name):
    """按需 import —— 缺文件时返回 None，不影响控制台启动。"""
    try:
        return __import__(name)
    except Exception:
        return None


def _char_info() -> dict:
    """「角色」页要的东西：现在是谁、都有谁、各自到哪一步了。

    ⚠ 这个函数**只读盘，不改任何东西**。真正的切人走 /api/char/switch。
      为什么分开：读和写混在一起，页面一刷新就可能把人切走，那就没人敢开这个页了。
    """
    r = _mod("角色")
    if not r:
        return {"error": "找不到 角色.py —— 角色那一层没装好"}
    try:
        names = r.chars()
        cur = r.current()
    except Exception as e:
        return {"error": "角色层读不出来：%s" % e}

    rel = _mod("relation")
    out = {"current": cur, "list": [],
           "roles_dir": str(getattr(r, "ROLES_DIR", ""))}

    for n in names:
        d = r.char_dir(n)
        it = {"name": n, "is_current": (n == cur),
              "stage": 1, "stage_count": 5, "stage_name": "初遇",
              "intimacy": 0.0, "turns": 0, "days": 1,
              "memory": 0, "journal": 0,
              "opening_sent": False, "his_name": "", "target": ""}

        cfg_n = {}
        try:
            cfg_n = yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8")) or {}
            it["target"] = str(cfg_n.get("target") or "")
        except Exception:
            pass

        try:
            rj = json.loads((d / "关系.json").read_text(encoding="utf-8")) or {}
            it["stage"] = max(1, int(rj.get("stage", 1) or 1))
            it["intimacy"] = round(float(rj.get("intimacy", 0) or 0), 1)
            it["turns"] = int(rj.get("turns", 0) or 0)
            it["his_name"] = str(rj.get("his_name") or "")
            try:
                first = dt.date.fromisoformat(str(rj.get("first") or ""))
                it["days"] = (dt.date.today() - first).days
            except Exception:
                pass
        except Exception:
            pass

        # 档位名：拿**她自己的** config 去算她的五档表（每人的档名可以不一样）
        if rel:
            try:
                tab = rel.stage_table(cfg_n)
                it["stage_count"] = len(tab)
                i = max(1, min(len(tab), it["stage"])) - 1
                it["stage_name"] = str(tab[i].get("name") or "")
            except Exception:
                pass

        try:
            it["memory"] = len(json.loads((d / "memory.json").read_text(encoding="utf-8")) or [])
        except Exception:
            pass

        try:
            jd = d / "记忆"
            it["journal"] = len(list(jd.glob("*.md"))) if jd.is_dir() else 0
        except Exception:
            pass

        try:
            it["opening_sent"] = bool(r.发过开场(n))
        except Exception:
            pass

        out["list"].append(it)
    return out


def collect() -> dict:
    cfg = read_config()
    data = {"ok": True, "config": {}, "editables": EDITABLE_CONFIG}
    data["char"] = _char_info()

    # ── 配置（只回可改的那些）──
    for dotted, label in EDITABLE_CONFIG.items():
        data["config"][dotted] = {"label": label, "value": get_by_path(cfg, dotted)}

    # ── 世界书（省 token 的那套：核心卡 + 说到才展开的词条）──
    wbmod = _mod("世界书")
    if wbmod:
        try:
            book = wbmod.Book(cfg)
            st = book.stats()
            st["enabled"] = bool((cfg.get("world") or {}).get("enabled", True))
            st["entries"] = [{k: v for k, v in e.items() if k != "body"}
                             for e in st["entries"]]
            issues, _ = book.audit()
            st["audit_issues"] = [w for w, _ in issues]
            try:
                st["old_chars"] = len((ROOT / "世界观.md").read_text(encoding="utf-8"))
            except Exception:
                st["old_chars"] = 0
            data["world"] = st
        except Exception as e:
            data["world"] = {"error": str(e)}
    else:
        data["world"] = {"error": "找不到 世界书.py"}

    # ── 关系进度 ──
    relation = _mod("relation")
    if relation:
        try:
            r = relation.Relation(cfg)
            rs = r.stats()
            # ★ 「好感度」那一页要用的东西，一次全带过去 ——
            #   五档表（区间 + 每档效果）、能不能改的字段名、成功率的曲线，
            #   还有 表白.py 里那几段「她该怎么说」的台词骨架。
            rs["stage_table"] = [dict(s) for s in r.stages]
            rs["default_stages"] = [dict(s) for s in relation.DEFAULT_STAGES]
            rs["num_fields"] = list(relation._NUM_FIELDS)
            rs["int_fields"] = ["say_per_day", "say_chars", "say_parts",
                                "say_total", "say_tokens", "active_per_day", "from", "to"]
            rs["chance_curve"] = [{"prep": p, "chance": r.confess_chance(p)}
                                  for p in (0, 20, 40, 60, 80, 100)]
            rs["prompt_seen"] = {
                "进度": r.prompt_block(),
                "表白/恋人": (r.lover_block() if r.is_lover()
                              else (r.despair_block() if r.despair_active()
                                    else r.crush_block())),
            }
            bmod_b = _mod("表白")
            if bmod_b:
                rs["prep_items"] = [{"key": k, "label": lb, "how": how,
                                     "weight": d}
                                    for k, lb, how, d in bmod_b.PREP_ITEMS]
                rs["hint_levels"] = [dict(x) for x in bmod_b.HINT_LEVELS]
                rs["lover_rules"] = list(bmod_b.LOVER_RULES)
                rs["lover_head"] = bmod_b.LOVER_HEAD
                rs["her_lines"] = bmod_b.her_confess_lines()
                rs["despair_say"] = list(bmod_b.DESPAIR_SAY)
                rs["prep_max"] = bmod_b.PREP_MAX
            data["relation"] = rs
        except Exception as e:
            data["relation"] = {"error": str(e)}
    else:
        data["relation"] = {"error": "找不到 relation.py"}

    # ── 记忆 ──
    journal = _mod("journal")
    if journal:
        try:
            j = journal.Journal(cfg)
            s = j.stats()
            s["entries"] = list(reversed(s.get("entries") or []))   # 新的在前
            data["journal"] = s
        except Exception as e:
            data["journal"] = {"error": str(e)}
    else:
        data["journal"] = {"error": "找不到 journal.py"}

    # ── 她的日子 ──
    daily = _mod("daily_life")
    life_events = []          # 给下面「分享欲」算联想用（当天发生过什么）
    if daily:
        try:
            life = daily.Life(cfg)
            st = life.roll()
            life.advance()
            st = life.state
            events = []
            for p in daily.PERIOD_ORDER:
                for it in st["slots"].get(p, []):
                    events.append({
                        "time": it.get("time", ""),
                        "min": it.get("min", 0),
                        "text": it["text"],
                        "talk": it.get("talk", 0),
                        "period": daily.period_name(p),
                    })
            events.sort(key=lambda x: x.get("min", 0))
            nudge = life.nudge()
            now_min = dt.datetime.now().hour * 60 + dt.datetime.now().minute
            rtxt, _ = daily.routine_now(now_min)
            # 此刻她在哪一段、走不走得开 —— 跟主动消息那道闸用的是同一个函数，
            # 所以控制台上从这里看到的「在上课」，和「这会儿不会主动开口」永远一致。
            try:
                nowb = life.now_brief(now_min) or {}
            except Exception:
                nowb = {}
            data["life"] = {
                "date": st.get("date"),
                "weather": st.get("weather"),
                "mood": st.get("mood"),
                "energy": st.get("energy"),
                "school_day": st.get("school_day"),
                "quirk": st.get("quirk"),
                "rare": st.get("rare"),
                "pending": st.get("pending") or [],
                "hunger": st.get("hunger"),
                "now_doing": rtxt,
                "now_brief": nowb,
                "busy": bool(nowb.get("busy")),
                "busy_where": nowb.get("where") or rtxt,
                "events": events,
                "nudge": (nudge or {}).get("text"),
                "plan": [
                    {"from": "%02d:%02d" % (a // 60 % 24, a % 60),
                     "to": "%02d:%02d" % (b // 60 % 24, b % 60),
                     "text": t}
                    for a, b, t in daily._routine_plan()
                ],
                # 素材池现在住在「当前角色」的 素材.py 里（daily_life 只转手），
                # 所以要从 daily.素材 上取 —— 直接写 daily.POOL 是取不到的。
                "pool": {k: len(v) for k, v in daily.素材.POOL.items()},
                "pool_total": sum(len(v) for v in daily.素材.POOL.values()),
                "quirks": len(daily.素材.QUIRKS),
                "rares": len(daily.素材.RARE),
                "stock": life.stock(),
                "day_plan": life.plan_line(),
                "day_kind": (life.plan or {}).get("kind"),
                "day_note": (life.plan or {}).get("note"),
                "day_edits": [
                    {"from": "%02d:%02d" % (e["a"] // 60 % 24, e["a"] % 60),
                     "to": "%02d:%02d" % (e["b"] // 60 % 24, e["b"] % 60),
                     "text": " / ".join(s[2] for s in e["segs"])}
                    for e in (life.plan or {}).get("edits", [])
                ],
                "appoints": [
                    {"from": "%02d:%02d" % (a // 60 % 24, a % 60),
                     "to": "%02d:%02d" % (b // 60 % 24, b % 60),
                     "text": t, "id": it.get("id"), "raw": it.get("raw") or ""}
                    for a, b, t, it in life.appointments_detail()
                ],
            }
            # 正在演的大事件
            try:
                data["story"] = life.story.stats()
            except Exception as e:
                data["story"] = {"error": str(e)}
            # 今天发生过什么 —— 给「分享欲」页算联想（她自己看不到这份清单）
            try:
                life_events = life.today_events()
            except Exception:
                life_events = []
        except Exception as e:
            data["life"] = {"error": str(e)}
    else:
        data["life"] = {"error": "找不到 daily_life.py"}
        data["story"] = {"error": "找不到 daily_life.py"}

    # ── 分享欲 · 联想（她这一档说多少、今天还剩几次提自己）──
    shmod = _mod("分享")
    if shmod:
        try:
            sh = shmod.Share(cfg)
            stage = 1
            rel = data.get("relation")
            if isinstance(rel, dict) and rel.get("stage"):
                stage = int(rel["stage"])
            sh.sync(stage)
            s = sh.stats()
            s["asked_rule"] = sh.level.get("asked_rule", "")
            s["share_rule"] = sh.level.get("share_rule", "")
            s["stages"] = [
                {"i": k, "name": v["name"], "per_day": v["per_day"],
                 "max_chars": v["max_chars"], "max_parts": v["max_parts"],
                 "total_chars": v["total_chars"],
                 "proactive": v["proactive_per_day"]}
                for k, v in sorted(shmod.STAGES.items())
            ]
            s["topics"] = sorted(shmod.TOPICS.keys())
            s["events"] = [
                {"text": e.get("text", ""), "time": e.get("time", ""),
                 "kind": e.get("kind", ""), "said": bool(e.get("said")),
                 "topics": [n for n, _ in shmod.topics_of(e.get("text") or "")]}
                for e in (life_events or [])
            ]
            data["share"] = s
        except Exception as e:
            data["share"] = {"error": str(e)}
    else:
        data["share"] = {"error": "找不到 分享.py"}

    # ── 主动消息（她会不会自己先开口、为什么没开口）──
    #
    #   这块最难查，因为「她没动静」的时候，看聊天记录永远是空的 ——
    #   只有 why_not() 说得出到底是哪一道闸卡住了。所以这页的重点是「理由」。
    #
    #   ⚠ Proactive 是 bridge.py 里的类，正常情况下活在跑机器人那个进程里。
    #     这个控制台是另一个进程，所以只读【状态文件】生活/主动.json
    #     （重启不清零，也是靠它）。readonly=True 保证控制台不写那个文件。
    bmod = _mod("bridge")
    if bmod:
        try:
            pa = bmod.Proactive(cfg, readonly=True)
            stage = 1
            rel = data.get("relation")
            if isinstance(rel, dict) and rel.get("stage"):
                stage = int(rel["stage"])
            # ★ 安静系数：优先用 relation 档位表里那一档自己的值（「好感度」页改过就生效），
            #   跟 bridge.Proactive.sync_stage 一个路子 —— 两边算出来的数必须一样。
            scale = None
            rmod = _mod("relation")
            if rmod:
                try:
                    scale = float(rmod.Relation(cfg).stage_effects(stage)["idle_scale"])
                except Exception:
                    scale = None
            pa.idle_scale = scale if (scale and scale > 0) \
                else bmod.IDLE_SCALE.get(stage, 1.0)

            quota = None
            if shmod:
                try:
                    q = shmod.Share(cfg)
                    q.sync(stage)
                    quota = q.proactive_quota()
                except Exception:
                    quota = None
            pa.quota = quota
            pa.stage = stage
            if quota is not None and quota > 0:
                pa.max_day = min(pa.max_day_cfg, quota)

            s = pa.status()
            s["stage"] = stage
            s["config_max_day"] = pa.max_day_cfg
            s["state_file"] = str(pa.path)
            # ── ⑤ 走不走得开（2026-09-22 加）──
            #   这道闸排在「随机」前面，是一票否决：她在上课 / 睡着就绝不主动开口，
            #   跟 bridge._maybe_proactive 里的判断一字不差（同一个 now_brief）。
            #   他发消息来的那一路不过这道闸 —— 那是「不装死」，两码事。
            life_d = data.get("life") or {}
            nb = life_d.get("now_brief") or {}
            s["busy"] = bool(nb.get("busy"))
            s["busy_text"] = (nb.get("text") or "").strip()
            s["busy_where"] = nb.get("where") or life_d.get("now_doing") or ""
            if s["quiet_when_busy"] and s["busy"]:
                s["gate_busy"] = True
                if s["can_send"]:
                    _w = s["busy_text"]
                    _w = "在睡觉" if "睡" in _w else (_w[:16] or "在忙")
                    s["can_send"] = False
                    s["why_not"] = "她这会儿走不开（%s）—— 上课 / 睡着时不主动开口" % _w
            else:
                s["gate_busy"] = False
            data["proactive"] = s
        except Exception as e:
            data["proactive"] = {"error": str(e)}
    else:
        data["proactive"] = {"error": "找不到 bridge.py"}

    # ── 读图（她能不能看懂你发的图片）──
    vmod = _mod("读图")
    if vmod:
        try:
            v = vmod.Vision(cfg, cfg.get("llm"))
            vs = v.stats()
            vs["prompt"] = vmod.PROMPT
            vs["cant_see"] = vmod.CANT_SEE
            data["vision"] = vs
        except Exception as e:
            data["vision"] = {"error": str(e)}
    else:
        data["vision"] = {"error": "找不到 读图.py"}

    # ── 表情包（她回完话追一张）──
    smod = _mod("表情包")
    if smod:
        try:
            tur = 0
            if isinstance(data.get("relation"), dict):
                tur = int(data["relation"].get("turns") or 0)
            data["sticker"] = smod.Sticker(cfg, readonly=True).stats(tur)
        except Exception as e:
            data["sticker"] = {"error": str(e)}
    else:
        data["sticker"] = {"error": "找不到 表情包.py"}

    # ── 文件概况 ──
    files = {}
    for key, meta in EDITABLE.items():
        p = ROOT / meta["file"]
        files[key] = {
            "title": meta["title"],
            "hint": meta["hint"],
            "file": meta["file"],
            "exists": p.exists(),
            "chars": len(p.read_text(encoding="utf-8")) if p.exists() else 0,
        }
    data["files"] = files
    data["memory_turns"] = len(read_json(ROOT / "memory.json") or [])

    # ── 每轮真正会发出去的字数（判断贵不贵用）──
    #   注意：词条库（book）虽然有一万七千字，但**不占每轮开销** ——
    #   只有被翻到的卡才发出去。所以这里绝不能把它的字数算进去。
    w = data.get("world") or {}
    w_on = bool(w.get("enabled")) and not w.get("error")
    stage = int((data.get("relation") or {}).get("stage") or 1)
    trim_on = bool((cfg.get("world") or {}).get("trim_stages", True))

    rel_chars = files.get("relation", {}).get("chars", 0)
    if trim_on:
        rmod = _mod("relation")
        if rmod is not None and hasattr(rmod, "trim_stages"):
            try:
                src = (ROOT / files["relation"]["file"]).read_text(encoding="utf-8")
                rel_chars = len(rmod.trim_stages(src, stage))
            except Exception:
                pass

    parts = [("人设", files.get("persona", {}).get("chars", 0))]
    if w_on:
        parts.append(("世界观·核心卡", w.get("core_chars", 0)))
        parts.append(("世界观·词条（翻到的才发）", 1000))
    else:
        parts.append(("世界观（整篇）", files.get("world", {}).get("chars", 0)))
    parts.append(("你和我" + ("（只带当前档）" if trim_on else ""), rel_chars))
    parts.append(("长期记忆", len((data["journal"] or {}).get("core") or "")))
    parts.append(("关系进度", 260))
    parts.append(("她的日子", 800))
    data["prompt_parts"] = [{"name": n, "chars": c} for n, c in parts]
    data["prompt_chars"] = sum(c for _, c in parts)
    data["prompt_old"] = (files.get("persona", {}).get("chars", 0)
                          + files.get("world", {}).get("chars", 0)
                          + files.get("relation", {}).get("chars", 0)
                          + len((data["journal"] or {}).get("core") or "")
                          + 260 + 800)

    return data


def pa_busy_flag(now_brief: dict, cfg: dict) -> bool:
    """她这会儿是不是「走不开，所以不会主动开口」。

    跟 bridge._maybe_proactive 里那 ⑤ 号闸是同一套条件：
    proactive.quiet_when_busy 开着，并且 now_brief 说她在上课 / 睡着了。
    """
    on = bool(((cfg or {}).get("proactive") or {}).get("quiet_when_busy", True))
    return bool(on and (now_brief or {}).get("busy"))


def read_json(p: Path):
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    server_version = "DeniaPanel/1.0"

    def log_message(self, fmt, *args):
        pass    # 别把控制台刷满

    # ── 工具 ──
    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8")) or {}
        except Exception:
            return {}

    # ── GET ──
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")

        if u.path == "/api/all":
            try:
                with _lock:
                    return self._send(200, collect())
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})

        if u.path == "/api/text":
            key = (q.get("name") or [""])[0]
            meta = EDITABLE.get(key)
            if not meta:
                return self._send(400, {"ok": False, "error": "不许读这个文件"})
            p = ROOT / meta["file"]
            text = p.read_text(encoding="utf-8") if p.exists() else ""
            return self._send(200, {"ok": True, "name": key, "file": meta["file"],
                                    "text": text, "chars": len(text)})

        if u.path == "/api/share/hit":
            # 试联想：把一句话丢进去，看会不会勾出她今天的那件事
            shmod = _mod("分享")
            daily = _mod("daily_life")
            if not shmod:
                return self._send(500, {"ok": False, "error": "找不到 分享.py"})
            text = (q.get("q") or [""])[0]
            cfg = read_config()
            try:
                sh = shmod.Share(cfg)
                st = 1
                rmod = _mod("relation")
                if rmod:
                    st = rmod.Relation(cfg).stage
                sh.sync(st)
                evs = daily.Life(cfg).today_events() if daily else []
                got = sh.recall([text], evs)
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {
                "ok": True, "q": text, "stage": sh.stage,
                "topics": [{"name": n, "score": s} for n, s in shmod.topics_of(text)],
                "hits": [{"topic": p["topic"], "text": p["event"]["text"],
                          "time": p["event"].get("time", "")} for p in got],
                "left": sh.left(),
                "block": sh.block(got) if got else "",
            })

        if u.path == "/api/world/hit":
            # 试匹配：把一句话丢进去，看这一轮会翻出哪几张卡
            wbmod = _mod("世界书")
            if not wbmod:
                return self._send(500, {"ok": False, "error": "找不到 世界书.py"})
            text = (q.get("q") or [""])[0]
            try:
                book = wbmod.Book(read_config())
                r = book.match([(text, 3.0)])
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "q": text, "hits": r["hits"],
                                    "chars": r["chars"],
                                    "skipped": r.get("skipped") or []})

        if u.path == "/api/pool":
            daily = _mod("daily_life")
            if not daily:
                return self._send(500, {"ok": False, "error": "找不到 daily_life.py"})
            period = (q.get("period") or ["morning"])[0]
            if period not in daily.素材.POOL:
                return self._send(400, {"ok": False,
                                        "error": "没有这个时段",
                                        "periods": daily.PERIOD_ORDER})
            try:
                used = daily.Life(read_config())._blocked()
            except Exception:
                used = set()
            all_items = daily.素材.POOL[period]
            items = [{"text": t, "talk": k} for t, k in all_items if t not in used]
            return self._send(200, {"ok": True, "period": period,
                                    "name": daily.period_name(period), "items": items,
                                    "used": len(all_items) - len(items),
                                    "total": len(all_items)})

        return self._send(404, {"ok": False, "error": "没有这个地址"})

    # ── POST ──
    def do_POST(self):
        u = urlparse(self.path)
        body = self._json_body()

        if u.path == "/api/config":
            pairs = body.get("values") or {}
            if not isinstance(pairs, dict) or not pairs:
                return self._send(400, {"ok": False, "error": "没有要改的东西"})
            try:
                with _lock:
                    done, failed = write_config_values(pairs)
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "done": done, "failed": failed})

        if u.path == "/api/text":
            key = body.get("name")
            meta = EDITABLE.get(key)
            if not meta:
                return self._send(400, {"ok": False, "error": "不许写这个文件"})
            text = body.get("text")
            if not isinstance(text, str):
                return self._send(400, {"ok": False, "error": "内容不对"})
            p = ROOT / meta["file"]
            try:
                with _lock:
                    if p.exists():
                        (ROOT / (meta["file"] + ".bak")).write_text(
                            p.read_text(encoding="utf-8"), encoding="utf-8")
                    p.write_text(text, encoding="utf-8")
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "chars": len(text),
                                    "backup": meta["file"] + ".bak"})

        if u.path == "/api/life/refresh":
            daily = _mod("daily_life")
            if not daily:
                return self._send(500, {"ok": False, "error": "找不到 daily_life.py"})
            try:
                with _lock:
                    daily.Life(read_config()).roll(force=True)
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True})

        if u.path == "/api/proactive/try":
            # 试生成一条主动消息。
            #   真的跑一次模型 —— 不真跑就看不出效果，那是假按钮。
            #   但一个字都不记：不动历史、不消耗素材、不推进关系（dry=True）。
            bmod = _mod("bridge")
            if not bmod:
                return self._send(500, {"ok": False, "error": "找不到 bridge.py"})
            cfg = read_config()
            try:
                with _lock:
                    brain = bmod.Brain(cfg)
                    st = 1
                    if brain.rel_engine is not None:
                        try:
                            st = int(brain.rel_engine.stage)
                        except Exception:
                            st = 1
                    if brain.share is not None:
                        brain.share.sync(st)
                    seed = brain.life.nudge() if brain.life is not None else None
                    if not seed:
                        return self._send(200, {
                            "ok": False, "error": "今天已经没什么能说的了"
                            "（素材都用过一遍了）"})
                    # 「这条是几分钟前发生的」—— 在跑模型之前先取走，
                    # 免得被下面这次生成 consume 掉。
                    seed_ago = seed.get("ago")
                    seed_time = seed.get("time") or ""
                    ans = brain.proactive(seed, dry=True)
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            if not ans:
                return self._send(200, {"ok": False, "error": "模型这次没吐出内容，再点一次"})
            parts = [p for p in ans.split("\n") if p.strip()]
            # 她此刻在哪、走不走得开（跟那道闸同一个 now_brief）。
            nb = {}
            try:
                nb = brain.life.now_brief() if brain.life is not None else {}
            except Exception:
                nb = {}
            if seed_ago is None:
                ago_text = "（这条不是「今天发生的事」，没有时间词）"
            elif seed_ago <= 3:
                ago_text = "刚刚"
            elif seed_ago <= 30:
                ago_text = "%d 分钟前" % seed_ago
            else:
                ago_text = "今天早些时候"
            return self._send(200, {
                "ok": True, "stage": st, "seed": seed,
                "text": ans, "parts": parts,
                "ago": seed_ago,
                "seed_time": seed_time,
                "ago_text": ago_text,
                "now_where": (nb or {}).get("where") or "",
                "now_busy": bool((nb or {}).get("busy")),
                "blocked": bool(pa_busy_flag(nb, cfg)),
                "chars": sum(len(re.sub(r"\s", "", p)) for p in parts),
                "note": "这只是试给你看的 —— 没有真的发出去，也没记进她的记忆。"})

        if u.path == "/api/vision/try":
            # 试读一张本地图片：填路径 → 看视觉模型读出什么。
            # 不用真去微信发图就能验证配置对不对。
            vmod = _mod("读图")
            if not vmod:
                return self._send(500, {"ok": False, "error": "找不到 读图.py"})
            p = str(body.get("path") or "").strip().strip('"')
            if not p:
                return self._send(400, {"ok": False, "error": "没填图片路径"})
            f = Path(p)
            if not f.is_absolute():
                f = ROOT / f
            if not f.exists():
                return self._send(400, {"ok": False, "error": "找不到这个文件：%s" % f})
            cfg = read_config()
            try:
                v = vmod.Vision(cfg, cfg.get("llm"))
                v.min_gap = 0                      # 手动试的时候就别卡频率了
                r = vmod.read_media(v, f.read_bytes())
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "file": str(f), "kind": r["kind"],
                                    "why": r["why"], "text": r["text"],
                                    "wrapped": vmod.wrap_for_chat(r["text"]) if r["text"] else ""})

        if u.path in ("/api/story/start", "/api/story/stop", "/api/story/reset"):
            daily = _mod("daily_life")
            if not daily:
                return self._send(500, {"ok": False, "error": "找不到 daily_life.py"})
            try:
                with _lock:
                    life = daily.Life(read_config())
                    s = life.story
                    act = u.path.rsplit("/", 1)[-1]
                    if act == "reset":
                        s.reset()
                    else:
                        aid = (body.get("id") or "").strip()
                        if not aid:
                            return self._send(400, {"ok": False, "error": "没给 id"})
                        ok = s.start(aid) if act == "start" else s.stop(aid)
                        if not ok:
                            return self._send(400, {
                                "ok": False,
                                "error": ("它已经在演了，或者 id 不对" if act == "start"
                                          else "它现在没在演")})
                    return self._send(200, {"ok": True, "story": s.stats()})
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})

        if u.path == "/api/relation/set":
            relation = _mod("relation")
            if not relation:
                return self._send(500, {"ok": False, "error": "找不到 relation.py"})
            try:
                v = float(body.get("intimacy"))
                with _lock:
                    r = relation.Relation(read_config())
                    r.data["intimacy"] = max(0.0, v)
                    r.data["peak"] = max(float(r.data.get("peak", 0.0)), r.data["intimacy"])
                    r._recalc_stage()
                    r.save()
                    out = r.stats()
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "relation": out})

        # ── 好感度 · 五档表（区间 + 每档效果）整表保存 / 恢复默认 ──
        if u.path in ("/api/relation/stages", "/api/relation/stages/reset"):
            relation = _mod("relation")
            if not relation:
                return self._send(500, {"ok": False, "error": "找不到 relation.py"})
            try:
                with _lock:
                    r = relation.Relation(read_config())
                    if u.path.endswith("/reset"):
                        r.reset_stages()
                        note = "已经回到默认那五档（覆盖删掉了）"
                    else:
                        ok, why = r.set_stages(body.get("stages"),
                                               write_cfg=bool(body.get("write_cfg", True)))
                        if not ok:
                            return self._send(400, {"ok": False, "error": why})
                        note = why
                    out = r.stats()
                    out["stage_table"] = [dict(s) for s in r.stages]
                    out["default_stages"] = [dict(s) for s in relation.DEFAULT_STAGES]
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "note": note, "relation": out})

        # ── 好感度 · 表白（调试台）：试表白 / 代她开口 / 答应 / 拒绝 / 重置 / 跳到某档 ──
        if u.path == "/api/relation/confess":
            relation = _mod("relation")
            if not relation:
                return self._send(500, {"ok": False, "error": "找不到 relation.py"})
            act = str(body.get("action") or "").strip()
            try:
                with _lock:
                    r = relation.Relation(read_config())
                    if act == "try":
                        res = r.try_confess(int(body.get("prep") or 0))
                    elif act == "accept":
                        res = r.accept_her()
                    elif act == "refuse":
                        res = r.refuse_her()
                    elif act == "her":
                        r.mark_her_asked()
                        r.mark_await()
                        res = {"ok": True, "awaiting": True}
                    elif act == "phase":
                        res = r.advance_confession()
                    elif act == "reset":
                        r.reset_confess()
                        res = {"ok": True}
                    elif act == "stage":
                        n = max(1, min(len(r.stages), int(body.get("stage") or 1)))
                        if n >= len(r.stages):
                            res = r.become_lover("控制台手动")
                        else:
                            # ★ 从恋人档往回跳，得先摘掉「在一起」那面旗 ——
                            #   档位是钉死的（gate），不摘旗 _recalc_stage 根本不往下走。
                            st = r._cst()
                            st["lover_since"] = None
                            st["how"] = None
                            st["await_answer"] = None
                            r.data["intimacy"] = max(
                                0.0, float(r.stages[n - 1].get("from") or 1))
                            r._recalc_stage()
                            r.save()
                            res = {"ok": True, "stage": r.stage}
                    else:
                        return self._send(400, {"ok": False,
                                                "error": "不认识的 action：%s" % act})
                    out = r.stats()
                    out["stage_table"] = [dict(s) for s in r.stages]
                    out["chance_curve"] = [{"prep": p, "chance": r.confess_chance(p)}
                                           for p in (0, 20, 40, 60, 80, 100)]
                    out["prompt_seen"] = {
                        "进度": r.prompt_block(),
                        "表白/恋人": (r.lover_block() if r.is_lover()
                                      else (r.despair_block() if r.despair_active()
                                            else r.crush_block())),
                    }
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "action": act,
                                    "result": res, "relation": out})

        # ── 好感度 · 用心分试算器：把一句话按七项拆开看能拿几分 ──
        if u.path == "/api/relation/prep":
            bm = _mod("表白")
            if not bm:
                return self._send(500, {"ok": False, "error": "找不到 表白.py"})
            cfg = read_config()
            try:
                w = None
                r = None
                relation = _mod("relation")
                if relation:
                    r = relation.Relation(cfg)
                    w = r.confess_cfg().get("prep_weights")
                res = bm.prep_score(str(body.get("text") or ""),
                                    weights=w,
                                    has_photo=bool(body.get("photo")),
                                    has_voice=bool(body.get("voice")),
                                    has_gift=bool(body.get("gift")),
                                    build_count=int(body.get("build") or 0),
                                    now=dt.datetime.now())
                res["tips"] = bm.prep_tips(res, limit=4)
                res["is_confess"] = bm.looks_like_confess(str(body.get("text") or ""))
                if r is not None:
                    res["chance"] = r.confess_chance(res["score"])
                    res["chance_base"] = r.confess_chance(0)
                    res["tried"] = int(r._cst().get("tried") or 0)
                    res["read"] = bm.read_answer(str(body.get("text") or ""))
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "prep": res})

        if u.path == "/api/journal/summarize":
            journal = _mod("journal")
            relation = _mod("relation")
            if not journal:
                return self._send(500, {"ok": False, "error": "找不到 journal.py"})
            try:
                cfg = read_config()
                j = journal.Journal(cfg)
                history = read_json(ROOT / (cfg.get("memory", {}) or {})
                                    .get("file", "memory.json")) or []
                turns = 0
                if relation:
                    turns = relation.Relation(cfg).turns()
                if not history:
                    return self._send(400, {"ok": False,
                                            "error": "memory.json 里还没有对话，没法复盘"})
                got = j.summarize(llm_chat, history, turns,
                                  note="（这是手动触发的一次复盘。）")
                if not got:
                    return self._send(500, {"ok": False, "error": "复盘的输出没解析出来"})
                s = j.stats()
                s["entries"] = list(reversed(s.get("entries") or []))
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "journal": s, "got": got})

        # ── 角色：切人 / 重新认识 ──────────────────────────────────
        #
        #  ⚠ 这里切的只是"**下次启动用谁**"。
        #    正在跑的那个 bridge 进程不会因为这儿改了指针就换人 ——
        #    它的大脑在启动时就把人设、世界书、记忆的路径存下来了。
        #    要**当场**换人，得在微信里发 /切换。
        #    页面上会把这句写清楚，免得你以为点了没反应。
        if u.path == "/api/char/switch":
            # ⚠ body 在 do_POST 开头已经读过了。http 的 rfile 只能读一次，
            #   再调 _json_body() 会去 socket 上等一批永远不会来的字节 —— 请求就那么挂住。
            name = str(body.get("name") or "").strip()
            r = _mod("角色")
            if not r:
                return self._send(500, {"ok": False, "error": "找不到 角色.py"})
            ok, msg = r.switch(name)
            return self._send(200, {
                "ok": bool(ok),
                "msg": ("指针已经指向「%s」—— 机器人那边这一两秒就会自己换过来"
                        "（它每轮都瞄一眼这个文件），不用重启" % msg) if ok else msg,
                "char": _char_info()})

        if u.path == "/api/char/reset":
            name = str(body.get("name") or "").strip()
            r = _mod("角色")
            if not r:
                return self._send(500, {"ok": False, "error": "找不到 角色.py"})
            if not r.exists(name):
                return self._send(200, {"ok": False, "msg": "没有这个角色：%s" % name})
            try:
                with _lock:
                    done = r.重新认识(name)
            except Exception as e:
                return self._send(500, {"ok": False, "error": "清的时候出错了：%s" % e})
            return self._send(200, {
                "ok": True,
                "msg": "「%s」已经退回初遇 —— 记忆清空、关系归零、开场白重置。"
                       "机器人那边这一两秒就会自己重读，不用重启。" % name,
                "done": done, "char": _char_info()})

        if u.path == "/api/journal/forget":
            # 「不重要的东西就忘掉」—— 只清「历次复盘」，长期记忆（core）一个字不动。
            # 改规矩之前攒下的那几篇是按老标准写的（一句一行，等于把对话抄了一遍），
            # 这个按钮就是让你说一句「这些忘了吧」，不用去开黑框敲 --forget。
            journal = _mod("journal")
            if not journal:
                return self._send(500, {"ok": False, "error": "找不到 journal.py"})
            try:
                with _lock:
                    j = journal.Journal(read_config())
                    n = len(j.data.get("entries") or [])
                    if n:
                        # 先留一份底再清 —— 万一手滑点错了还能捞回来
                        try:
                            import shutil
                            shutil.copyfile(
                                str(j.file),
                                str(j.file) + ".bak-忘复盘-%s"
                                % dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
                        except Exception:
                            pass
                        j.data["entries"] = []
                        j.save()
                    s = j.stats()
                    s["entries"] = list(reversed(s.get("entries") or []))
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "forgot": n, "journal": s,
                                    "note": "忘掉了 %d 篇旧复盘；长期记忆（他是什么人）没动。" % n})

        return self._send(404, {"ok": False, "error": "没有这个地址"})


# ══════════════════════════════════════════════════════════════════════════
#  页面
# ══════════════════════════════════════════════════════════════════════════

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>达妮娅 · 控制台</title>
<style>
:root{
  --bg:#f6f6f8; --panel:#ffffff; --line:#e4e4ea; --text:#1c1c22; --dim:#71717f;
  --accent:#c2559a; --accent2:#7b6cf6; --ok:#2f9e6e; --warn:#c98a1b; --bad:#d2504a;
  --code:#f2f2f6; --shadow:0 1px 3px rgba(0,0,0,.06),0 8px 24px rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#131318; --panel:#1b1b22; --line:#2c2c36; --text:#e9e9ef; --dim:#9a9aab;
    --accent:#e78ec4; --accent2:#9d90ff; --ok:#4cc38a; --warn:#e0b14e; --bad:#ec6f68;
    --code:#24242d; --shadow:0 1px 3px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.3);
  }
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{
  background:var(--bg); color:var(--text); font-size:14px; line-height:1.6;
  font-family:"Segoe UI","Microsoft YaHei",system-ui,-apple-system,sans-serif;
}
.wrap{display:flex;min-height:100vh}
aside{
  width:190px;flex:0 0 190px;background:var(--panel);border-right:1px solid var(--line);
  padding:18px 12px;position:sticky;top:0;height:100vh;overflow:auto;
}
.brand{font-weight:700;font-size:15px;letter-spacing:.5px;margin:2px 0 16px 8px}
.brand small{display:block;font-weight:400;color:var(--dim);font-size:11px;letter-spacing:0}
nav a{
  display:block;padding:8px 10px;border-radius:8px;color:var(--text);text-decoration:none;
  font-size:13px;margin-bottom:2px;cursor:pointer;
}
nav a:hover{background:var(--code)}
nav a.on{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;font-weight:600}
main{flex:1;padding:22px 26px 60px;max-width:1000px}
h2{font-size:17px;margin:0 0 4px}
.sub{color:var(--dim);font-size:12.5px;margin-bottom:18px}
.card{
  background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin-bottom:14px;box-shadow:var(--shadow);
}
.card h3{font-size:14px;margin:0 0 10px;display:flex;align-items:center;gap:8px}
.card h3 .tag{font-size:11px;font-weight:400;color:var(--dim)}
.row{display:flex;gap:12px;flex-wrap:wrap}
.col{flex:1;min-width:220px}
label{display:block;font-size:12px;color:var(--dim);margin:10px 0 4px}
input[type=text],input[type=number],input[type=password],select,textarea{
  width:100%;background:var(--code);border:1px solid var(--line);color:var(--text);
  border-radius:8px;padding:8px 10px;font-size:13px;font-family:inherit;outline:none;
}
input:focus,textarea:focus,select:focus{border-color:var(--accent)}
textarea{
  min-height:400px;font-family:Consolas,"Cascadia Mono",monospace;font-size:12.5px;
  line-height:1.75;resize:vertical;
}
button{
  background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;border:0;
  border-radius:8px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;
  font-family:inherit;margin-top:12px;
}
button.ghost{background:var(--code);color:var(--text);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:default}
.bar{height:9px;background:var(--code);border-radius:99px;overflow:hidden;margin:8px 0 4px}
.bar > i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:99px;transition:width .4s}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:13px}
.kv b{color:var(--dim);font-weight:400}
.pill{display:inline-block;font-size:11px;padding:2px 9px;border-radius:99px;background:var(--code);color:var(--dim);margin-right:6px}
.pill.hot{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff}
.tl{border-left:2px solid var(--line);margin-left:6px;padding-left:14px}
.tl li{list-style:none;position:relative;padding:4px 0;font-size:13px}
.tl li:before{content:"";position:absolute;left:-20px;top:12px;width:7px;height:7px;border-radius:99px;background:var(--line)}
.tl li.hot:before{background:var(--accent)}
.tl li i{color:var(--dim);font-style:normal;font-size:12px;margin-right:8px}
.tl{padding-left:14px;margin:0}
.hint{font-size:12px;color:var(--dim);margin-top:6px}
.msg{font-size:12.5px;margin-top:10px;min-height:18px}
.msg.ok{color:var(--ok)} .msg.bad{color:var(--bad)} .msg.warn{color:var(--warn)}
.stages{font-size:12.5px}
.stages div{display:flex;justify-content:space-between;padding:3px 0;color:var(--dim)}
.stages div.on{color:var(--text);font-weight:700}
.hidden{display:none}
.switch{display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12.5px;color:var(--dim)}
pre.core{
  white-space:pre-wrap;background:var(--code);border-radius:8px;padding:12px 14px;
  font-size:13px;line-height:1.8;margin:0;font-family:inherit;
}
.entry{border-left:2px solid var(--line);padding:2px 0 2px 12px;margin:10px 0}
.entry b{font-size:12px;color:var(--dim);font-weight:400}
.entry p{margin:4px 0 0}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:99px;animation:s .7s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes s{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="wrap">
<aside>
  <div class="brand"><span id="brand">她</span><small>控制台 · 本地</small></div>
  <nav id="nav"></nav>
</aside>
<main id="main"></main>
</div>
<script>
var D = null;                       // 最近一次 /api/all 的结果
var PANES = [
  ["chars","角色"],
  ["overview","概览"],["brain","大脑 / API"],["persona","人设"],["opening","开场白"],["worldbook","世界书"],
  ["share","分享欲"],["favor","好感度"],["relation","你和我"],["memory","记忆"],
  ["life","她的日子"],["story","大事"],["proactive","主动消息"],["vision","读图"],
  ["tune","语音与节奏"]
];
var cur = location.hash ? location.hash.slice(1) : "overview";

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }

async function api(path, opt){
  var r = await fetch(path, opt);
  var t = await r.text();
  try { return JSON.parse(t); } catch(e){ return {ok:false, error:t.slice(0,300)}; }
}

function nav(){
  document.getElementById("nav").innerHTML = PANES.map(function(p){
    return '<a class="'+(p[0]===cur?"on":"")+'" href="#'+p[0]+'" onclick="go(\''+p[0]+'\');return false">'+p[1]+'</a>';
  }).join("");
}
function go(k){ cur = k; location.hash = k; nav(); render(); }

function render(){
  if(!D) return;
  // 侧边栏顶上那块大字 = 现在是谁。不然开着三个角色的控制台，你分不出在看谁。
  try{
    if(D.char && D.char.current){
      document.getElementById("brand").textContent = D.char.current;
      document.title = D.char.current + " · 控制台";
    }
  }catch(e){}
  if(cur==="chars")    return paneChars();
  if(cur==="overview") return paneOverview();
  if(cur==="brain")    return paneConfig(["target","llm.base_url","llm.api_key","llm.model","llm.temperature","llm.max_tokens"],"大脑 / API","接到哪个模型上说话。改完点保存就生效，不用重启。");
  if(cur==="persona"||cur==="relation"||cur==="opening") return paneText(cur);
  if(cur==="core"||cur==="book"||cur==="world") return paneText(cur);
  if(cur==="worldbook") return paneWorldbook();
  if(cur==="share")    return paneShare();
  if(cur==="favor")    return paneFavor();
  if(cur==="proactive")return paneProactive();
  if(cur==="vision")   return paneVision();
  if(cur==="memory")   return paneMemory();
  if(cur==="life")     return paneLife();
  if(cur==="story")    return paneStory();
  if(cur==="tune")     return paneConfig(["voice.enabled","voice.mode","voice.engine","voice.edge_voice","voice.rate","voice.pitch","behavior.poll_interval","behavior.reply_delay_min","behavior.reply_delay_max","behavior.split_replies","behavior.split_chars","behavior.split_max_parts","behavior.strip_actions","behavior.max_per_hour","humanize.enabled","humanize.typing_speed","life.day_plan.enabled","life.day_plan.source","life.day_plan.horizon_hours","proactive.enabled","proactive.min_idle_minutes","proactive.chance_per_tick","proactive.active_hours.0","proactive.active_hours.1","proactive.max_per_day","sticker.enabled","sticker.chance","sticker.min_turns","sticker.min_gap_minutes","sticker.max_per_day","sticker.dir","life.enabled","life.rare_chance","life.reuse_after_days","story.enabled","story.max_active","story.gap_days.0","story.gap_days.1","journal.enabled","journal.every_turns","journal.every_hours","relation.daily_cap","relation.idle_days"],"语音与节奏","语音、打字节奏、主动消息、表情包、关系快慢、素材消耗 —— 这些决定她像不像真人。", paneStickerCard());
}

/* ── 角色（换人 / 重新认识）── */
function paneChars(){
  var c = D.char||{};
  var h = '';
  h += '<h2>角色</h2><div class="sub">一份代码，几个角色数据包。'
     + '同一时刻只有一个人在跟你说话 —— 换过去之后，她的人设、世界书、记忆、日程'
     + '全都是她自己的，刚走的那个人<b>一个字都不会留下</b>。</div>';

  if(c.error){ h += '<div class="msg bad">'+esc(c.error)+'</div>'; }

  // ★ 最容易误会的一点，写在最上面：
  h += '<div class="msg ok"><b>点「切到她」就是当场换人，不用重启。</b><br>'
     + '机器人每一轮都会瞄一眼 角色\\当前.json —— 这儿点完，它下一次循环就换过去了。<br>'
     + '在微信里发 <b>/切换 名字</b> 效果一样，而且那条指令<b>不进大模型、不进她的记忆</b>，'
     + '她不会知道自己被"输入过命令"。</div>';

  h += '<div class="row">';
  (c.list||[]).forEach(function(it){
    var on = !!it.is_current;
    h += '<div class="col"><div class="card"'+(on?' style="border-color:var(--accent)"':'')+'>';
    h += '<h3>'+esc(it.name)+(on?' <span class="tag">现在是她</span>':'')+'</h3>';
    h += '<div class="kv">';
    h += '<b>进度</b><span>第 '+it.stage+'/'+it.stage_count+' 档 「'+esc(it.stage_name)+'」</span>';
    h += '<b>亲密度</b><span>'+it.intimacy+'</span>';
    h += '<b>聊过</b><span>'+it.turns+' 轮 · 第 '+it.days+' 天</span>';
    h += '<b>短期记忆</b><span>'+it.memory+' 条对话</span>';
    h += '<b>长期记忆</b><span>'+it.journal+' 篇</span>';
    h += '<b>开场白</b><span>'+(it.opening_sent
        ? '已经说过了'
        : '<b style="color:var(--accent)">还没说 —— 下次启动她先开口</b>')+'</span>';
    h += '<b>他叫什么</b><span>'+(it.his_name?esc(it.his_name):'她还不知道（问出来了会自己记住）')+'</span>';
    h += '<b>微信会话</b><span>'+esc(it.target||'（config 里没填 target）')+'</span>';
    h += '</div>';
    h += '<div class="row" style="margin-top:10px">';
    if(!on){
      h += '<button class="ghost" onclick="charSwitch(\''+esc(it.name)+'\')">切到她</button>';
    }else{
      h += '<button class="ghost" onclick="charReset(\''+esc(it.name)+'\')">重新认识（清空进度）</button>';
    }
    h += '<button class="ghost" onclick="go(\'persona\')">看人设</button>';
    h += '</div>';
    h += '</div></div>';
  });
  h += '</div>';

  h += '<div class="card"><h3>微信里的两条指令</h3><div class="kv">';
  h += '<b>/人物</b><span>看看现在能联系到谁</span>';
  h += '<b>/切换 名字</b><span>当场换人（静默切换 —— 旧的不说再见，新的不打招呼，'
     + '她会自己开口说第一句）</span>';
  h += '</div><div class="hint">这两条只给系统看，<b>不进大模型、不进她的记忆</b>。'
     + '不然她会"记得"你对她说了一句莫名其妙的话。</div></div>';

  h += '<div class="card"><h3>重新认识 = 回到初遇</h3><div class="hint">'
     + '清掉的是<b>经历</b>：短期记忆、长期记忆、关系档位、今天的状态、开场白记号。<br>'
     + '<b>人设、世界书、素材、音色不动</b> —— 那些是她的底子，不是她的经历。<br>'
     + '清之前会自动在 角色\\&lt;名字&gt;\\_历史备份\\ 里留一份，反悔了能捞回来。</div></div>';
  // ★ 这里必须**自己**把内容塞进 #main —— 跟别的 pane 一样。
  //   写成 `return h;` 的话返回值没人接：点「角色」标签页只换高亮、
  //   右边内容纹丝不动，而且不报任何错。（2026-09-23 真踩过）
  document.getElementById("main").innerHTML = h;
}

async function charSwitch(name){
  if(!confirm("现在就换成 "+name+"？\n\n"
    + "正在跑着的机器人下一轮就会换过去（不用重启）。\n"
    + "旧的那位不会说再见，新的这位会自己开口说第一句。")) return;
  var r = await api("/api/char/switch", {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify({name:name})});
  alert(r.ok ? (r.msg||"好了") : ("没成："+(r.msg||r.error||"未知原因")));
  if(r.char) D.char = r.char;
  render();
}

async function charReset(name){
  if(!confirm("让「"+name+"」重新认识你？\n\n"
    + "会清掉：聊天记忆、长期记忆、关系档位（回到「初遇」）、今天的状态、开场白记号。\n"
    + "不会动：人设、世界书、素材、音色。\n\n"
    + "清之前会自动留一份备份。确定吗？")) return;
  var r = await api("/api/char/reset", {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify({name:name})});
  alert(r.ok ? (r.msg||"好了") : ("没成："+(r.error||r.msg||"未知原因")));
  if(r.char) D.char = r.char;
  render();
}

/* ── 概览 ── */
function paneOverview(){
  var r = D.relation||{}, l = D.life||{}, j = D.journal||{};
  var st = r.stages||[];
  var pct = r.progress||0;
  var h = '';
  h += '<h2>概览</h2><div class="sub">她那边的状态，一眼看完。</div>';

  h += '<div class="card"><h3>你们到哪一步了 <span class="tag">按亲密度，不按天数</span></h3>';
  if(r.error){ h += '<div class="msg bad">'+esc(r.error)+'</div>'; }
  else {
    h += '<div class="row"><div class="col">';
    h += '<div class="kv">';
    h += '<b>现在是</b><span>第 '+r.stage+'/'+r.stage_count+' 档 <b style="color:var(--accent);font-weight:700">「'+esc(r.stage_name)+'」</b></span>';
    h += '<b>认识</b><span>第 '+r.days+' 天</span>';
    h += '<b>聊过</b><span>'+r.turns+' 轮</span>';
    h += '<b>亲密度</b><span>'+r.intimacy+'（历史最高 '+r.peak+'）</span>';
    h += '<b>今日已涨</b><span>'+r.today_gain+' / '+r.daily_cap+'</span>';
    h += '</div>';
    h += '<div class="bar"><i style="width:'+pct+'%"></i></div>';
    h += '<div class="hint">'+(r.next_name? ('到下一档「'+esc(r.next_name)+'」还差 '+r.to_next+'（已走完 '+pct+'%）') : '到顶了')+'</div>';
    h += '</div><div class="col stages">';
    st.forEach(function(s){
      var tail = s.gate ? '一道门（只能表白进）'
                        : (s.from + ' - ' + (s.to==null?'∞':s.to));
      h += '<div class="'+(s.i===r.stage?"on":"")+'"><span>'+s.i+'. '+esc(s.name)+'</span><span>'+(s.i===r.stage?"← 现在":tail)+'</span></div>';
    });
    h += '</div></div>';
    h += '<div class="hint">前四档只按亲密度自动晋升；<b>第五档是一道门，不是台阶</b> —— '
      + '亲密度再高也推不开它，只有表白能进。每一档的区间和效果，都在 '
      + '<a href="#favor" onclick="go(\'favor\');return false" style="color:var(--accent)">「好感度」</a> 那一页改。</div>';
    if(r.is_lover){
      h += '<div class="msg good">你们已经在一起了（第 '+r.lover_days+' 天'
        + (r.lover_how?('，'+esc(r.lover_how)):'')+'）。恋人那一段跟前面四档不是一套规矩。</div>';
    } else if(r.phase && r.phase!=="none"){
      h += '<div class="msg '+(r.despair?"bad":(r.phase==="ready"?"warn":""))+'">'
        + '表白进度：<b>'+esc(r.phase_text||r.phase)+'</b>'
        + (r.awaiting? '　·　她已经开口了，正等你回。':'')
        + (r.despair? ('　·　她很难过，还有 '+r.despair_days_left+' 天。'):'')
        + '</div>';
    }
    h += '<label>手动改亲密度（调试用）</label><div class="row" style="align-items:flex-end">';
    h += '<div style="width:160px"><input type="number" id="relv" value="'+r.intimacy+'"></div>';
    h += '<button class="ghost" onclick="setRel()">设定</button></div>';
    h += '<div class="msg" id="relmsg"></div>';
  }
  h += '</div>';

  h += '<div class="card"><h3>她那边现在</h3>';
  if(l.error){ h += '<div class="msg bad">'+esc(l.error)+'</div>'; }
  else {
    h += '<div class="kv">';
    h += '<b>今天</b><span>'+esc(l.date)+'　'+(l.school_day?"有课":"没课")+'</span>';
    h += '<b>天气</b><span>'+esc(l.weather)+'</span>';
    h += '<b>状态</b><span>'+esc(l.mood)+'，精力 '+l.energy+'/100</span>';
    h += '<b>此刻在</b><span>'+esc(l.now_doing||"——")
    + (l.busy? '　<span class="pill hot">走不开</span>'
             : '　<span class="pill">腾得出手</span>')+'</span>';
    if(l.quirk) h += '<b>小插曲</b><span>'+esc(l.quirk)+'</span>';
    if(l.rare)  h += '<b>少见的事</b><span>'+esc(l.rare)+'</span>';
    h += '<b>惦记着</b><span>'+esc((l.pending||[]).join("；"))+'</span>';
    h += '<b>想吃</b><span>'+esc(l.hunger)+'</span>';
    var _sk = l.stock||{};
    if(_sk.left!=null) h += '<b>素材剩余</b><span>'+_sk.left+' / '+_sk.total+' 条（已用 '+(_sk.used||0)+'）</span>';
    h += '</div>';
    h += '<button class="ghost" onclick="refreshLife()">重摇今天</button>';
    h += '<span class="msg" id="lifemsg"></span>';
  }
  h += '</div>';

  var _s = D.story||{};
  h += '<div class="card"><h3>她正在演的大事 <span class="tag">连续剧</span></h3>';
  if(_s.error){ h += '<div class="msg bad">'+esc(_s.error)+'</div>'; }
  else if(!(_s.active||[]).length){ h += '<div class="hint">现在没在演 —— 到日子会自己开一条。</div>'; }
  else {
    (_s.active||[]).forEach(function(a){
      h += '<div class="entry" style="border-color:var(--accent)"><b>'+esc(a.name)+'　第 '+a.day+'/'+a.day_total+' 天</b><p>'+esc(a.current)+'</p></div>';
    });
  }
  h += '<button class="ghost" onclick="go(\'story\')">看全部 '+( _s.total||0 )+' 条剧本</button>';
  h += '</div>';

  h += '<div class="card"><h3>她的记忆 <span class="tag">定期复盘攒下来的</span></h3>';
  if(j.error){ h += '<div class="msg bad">'+esc(j.error)+'</div>'; }
  else {
    h += '<div class="kv"><b>复盘次数</b><span>'+j.count+' 次</span>';
    h += '<b>最近一次</b><span>'+esc(j.last_time||"还没")+'</span>';
    h += '<b>目前到</b><span>第 '+j.last_turn+' 轮</span></div>';
    h += '<div class="hint">长期记忆 '+(j.core?('已有 '+j.core.length+' 字'):'还没攒出来')+'　·　到「记忆」那一页看全文</div>';
  }
  h += '</div>';

  h += '<div class="card"><h3>Token 账 <span class="tag">每轮都发出去的部分</span></h3>';
  var pd = D.prompt_parts||[];
  var savePct = D.prompt_old ? Math.round((1 - D.prompt_chars/D.prompt_old)*100) : 0;
  h += '<div class="kv">';
  pd.forEach(function(x){ h += '<b>'+esc(x.name)+'</b><span>'+x.chars+' 字</span>'; });
  h += '</div>';
  h += '<div class="sub" style="margin:10px 0 0">合计 <b>'+D.prompt_chars+'</b> 字（约 '+Math.round(D.prompt_chars/1.6)+' token）。';
  if(D.prompt_old) h += '改造前是 '+D.prompt_old+' 字，省了约 <b style="color:var(--accent)">'+savePct+'%</b>。';
  h += '</div>';
  h += '<div class="hint">词条库那一万多字<b>不算在里面</b> —— 只有被翻到的卡才发。'
    + '人设 + 核心卡 + 你和我 是静态的，摆在提示最前面，还能吃到接口的前缀缓存。'
    + '另外还有最近 '+D.memory_turns+' 条短期对话（memory.turns）单独算。</div>';
  h += '<button class="ghost" onclick="go(\'worldbook\')">去世界书看看</button>';
  h += '</div>';

  document.getElementById("main").innerHTML = h;
}

/* ── 世界书（省 token 的那套） ── */
function paneWorldbook(){
  var w = D.world||{}, h = '';
  h += '<h2>世界书 <span class="tag">省 token 的那套</span></h2>';
  h += '<div class="sub">以前每一轮都把她那边一万多字的设定整篇发过去，聊什么都要带上全部。现在拆成两样：<b>核心卡</b>（每轮都带，很短）＋ <b>词条</b>（他说到才展开）。</div>';
  if(w.error){ document.getElementById("main").innerHTML = h+'<div class="card"><div class="msg bad">'+esc(w.error)+'</div></div>'; return; }

  var oldN = w.old_chars||0, core = w.core_chars||0, avg = 900;
  var pct = oldN ? Math.round((1-(core+avg)/oldN)*100) : 0;
  h += '<div class="card"><h3>省了多少 <span class="tag">只算世界观这一段</span></h3><div class="kv">';
  h += '<b>改造前</b><span>每轮固定 <b>'+oldN+'</b> 字（整篇都发）</span>';
  h += '<b>现在</b><span>核心卡 <b>'+core+'</b> 字 ＋ 命中的卡片（通常 1~3 张，约 '+avg+' 字）</span>';
  h += '<b>词条库</b><span>'+w.count+' 张 / '+w.chars+' 字 / '+w.keys+' 个关键词　—— <b>不占每轮开销</b>，翻到才拿</span>';
  h += '<b>约省</b><span style="color:var(--accent);font-weight:700">'+pct+'%</span></div>';
  h += '<div class="hint">核心卡是静态的，摆在提示最前面，还能吃到接口的前缀缓存 —— 实际比字数看上去更便宜。</div>';
  h += '</div>';

  h += '<div class="card"><h3>试匹配 <span class="tag">他说这句话，她会翻出哪些卡</span></h3>';
  h += '<div class="row" style="align-items:flex-end"><div style="flex:1"><label>随便打一句</label>';
  h += '<input id="wq" placeholder="比如：西西今天又在图书馆睡了"></div>';
  h += '<button onclick="tryHit()">翻一下</button></div>';
  h += '<div id="wqbox"><div class="hint">他话里出现某个关键词，那张卡才展开；没出现的就不占地方。</div></div>';
  h += '</div>';

  h += '<div class="card"><h3>卡片库 <span class="tag">'+w.count+' 张</span></h3>';
  h += '<div class="hint">卡片本身不发给模型 —— 只有被翻到的才发。想加料就编辑下面的「词条库」。</div>';
  var tags = w.tags||{}, ents = w.entries||[];
  Object.keys(tags).forEach(function(t){
    h += '<div style="margin:16px 0 6px"><b>'+esc(t)+'</b> <span class="tag">'+tags[t]+' 张</span></div>';
    ents.filter(function(e){ return e.tag===t; }).forEach(function(e){
      var keys = (e.keys||[]).map(esc).join('、');
      h += '<div class="row" style="align-items:flex-start;margin:5px 0;border-bottom:1px solid var(--line);padding-bottom:5px">';
      h += '<div style="width:170px"><b>'+esc(e.name)+'</b>'+(e.pin?' <span class="tag">常驻</span>':'')+'</div>';
      h += '<div style="flex:1;color:var(--dim);font-size:13px;line-height:1.6">'+keys+'</div>';
      h += '<span class="tag">'+e.chars+' 字</span></div>';
    });
  });
  var iss = w.audit_issues||[];
  if(iss.length){
    h += '<div class="msg warn" style="margin-top:12px">关键词自检有 '+iss.length+' 处要修：'+iss.map(esc).join('；')+'</div>';
  }
  h += '</div>';

  h += '<div class="card"><h3>改设定</h3>';
  h += '<div class="hint">她那边的东西全在这三份文件里，点开就能改，保存后下一轮生效。</div>';
  h += '<button onclick="go(\'core\')">核心卡（每轮都带）</button> ';
  h += '<button onclick="go(\'book\')">词条库（说到才展开）</button> ';
  h += '<button class="ghost" onclick="go(\'world\')">整篇版（存档）</button>';
  h += '</div>';

  document.getElementById("main").innerHTML = h;
  var q = document.getElementById("wq");
  if(q) q.addEventListener("keydown", function(e){ if(e.key==="Enter") tryHit(); });
}

async function tryHit(){
  var el = document.getElementById("wq"), box = document.getElementById("wqbox");
  var q = (el.value||"").trim();
  if(!q) return;
  box.innerHTML = '<div class="sub" style="margin:8px 0"><span class="spin"></span>翻卡片…</div>';
  var r = await api("/api/world/hit?q="+encodeURIComponent(q));
  if(!r.ok){ box.innerHTML = '<div class="msg bad">'+esc(r.error||"失败")+'</div>'; return; }
  if(!r.hits.length){
    box.innerHTML = '<div class="hint" style="margin-top:8px">一张都没翻到 —— 这轮只靠核心卡（基本常识）说话。'
      + '要是你觉得这儿本该翻出点什么，说明那张卡缺关键词，去词条库里补几个。</div>';
    return;
  }
  var h = '<div class="sub" style="margin:8px 0">翻出 '+r.hits.length+' 张，一共展开 '+r.chars+' 字：</div>';
  r.hits.forEach(function(x){
    h += '<div class="row" style="align-items:flex-start;margin:8px 0;border-bottom:1px solid var(--line);padding-bottom:6px">';
    h += '<div style="flex:1"><b>'+esc(x.name)+'</b> <span class="tag">'+esc(x.tag)+'</span>';
    h += '<div class="hint">命中：'+((x.why||[]).map(esc).join('、')||'常驻')+'　·　'+x.chars+' 字</div></div>';
    h += '<span class="tag">分 '+x.score+'</span></div>';
  });
  if((r.skipped||[]).length) h += '<div class="hint">装不下、这轮先不给的：'+r.skipped.map(esc).join('、')+'</div>';
  box.innerHTML = h;
}

/* ── 分享欲 · 联想（她刚认识你不会倒自己的日常） ── */
function paneShare(){
  var s = D.share||{}, h = '';
  h += '<h2>分享欲 <span class="tag">按关系档位控制她说多少</span></h2>';
  h += '<div class="sub">两个毛病一起治：<b>她太爱说自己</b>（才聊十几轮就把今天的日常倒一遍），'
    + '和<b>素材凭空蹦出来</b>（聊到一半突然说「我下午买了三本书」）。'
    + '现在：说多少跟着关系档位走，今天的事也要他聊到了才想起来。</div>';
  if(s.error){
    document.getElementById("main").innerHTML = h+'<div class="card"><div class="msg bad">'+esc(s.error)+'</div></div>';
    return;
  }

  h += '<div class="card"><h3>现在这一档 <span class="tag">第 '+s.stage+' 档 · '+esc(s.stage_name)+'</span></h3><div class="kv">';
  h += '<b>今天提自己</b><span>已用 '+s.used_today+' / '+s.per_day+' 次　—— 还剩 <b>'+s.left_today+'</b> 次</span>';
  h += '<b>主动找他</b><span>'+s.proactive_per_day+' 次/天'
    + (s.proactive_per_day?'':'　<b style="color:var(--accent)">这一档她不主动开口</b>')+'</span>';
  h += '<b>一次说多少</b><span>一条 ≤'+s.max_chars+' 字　·　最多 '+s.max_parts+' 条　·　一共 ≤'+s.total_chars+' 字</span>';
  h += '<b>生成时掐在</b><span>'+s.max_tokens+' token（光靠人设拦不住，两头都得掐）</span>';
  h += '<b>说超了怎么办</b><span>让她自己<b>重说一遍</b>（换个短的说法）；真得砍就砍<b>整条</b> —— 不砍半句、不补省略号</span>';
  h += '</div>';
  h += '<div class="hint" style="margin-top:10px">这一档她该怎么说话：<br>· '+esc(s.asked_rule||'')
    + '<br>· '+esc(s.share_rule||'')+'</div>';
  h += '</div>';

  h += '<div class="card"><h3>五个档 <span class="tag">「会说多少」是长出来的</span></h3>';
  h += '<div class="hint">这张表在 relation.py 的 DEFAULT_STAGES 里，是唯一的一份 —— 想让她更快健谈/更慢热，'
    + '去左边「好感度」页整表改（这里只是看）。第 5 档是「一道门」：只能表白进，分数推不动。</div>';
  (s.stages||[]).forEach(function(x){
    var on = x.i===s.stage;
    h += '<div class="row" style="margin:6px 0;padding:6px 8px;border-radius:6px;'
      + (on?'background:var(--code);':'') + '">';
    h += '<div style="width:36px"><b>'+(on?'▶ ':'')+x.i+'</b></div>';
    h += '<div style="width:120px"><b>'+esc(x.name)+'</b></div>';
    h += '<div style="flex:1;color:var(--dim);font-size:13px">一天提自己 '+x.per_day+' 次　·　'
      + x.max_chars+' 字 / '+x.max_parts+' 条 / 共 '+x.total_chars+' 字　·　主动 '+x.proactive+' 次/天</div>';
    h += '</div>';
  });
  h += '</div>';

  h += '<div class="card"><h3>试联想 <span class="tag">他说这句话，她会想起今天哪件事</span></h3>';
  h += '<div class="row" style="align-items:flex-end"><div style="flex:1"><label>随便打一句</label>';
  h += '<input type="text" id="sq" placeholder="比如：我最近在看一本书"></div>';
  h += '<button onclick="tryShare()">试试</button></div>';
  h += '<div id="sqbox"><div class="hint">他聊到哪一类话题，才会翻出相关的那件事；没聊到就不翻 —— '
    + '<b>不翻才是常态</b>。</div></div>';
  h += '</div>';

  var evs = s.events||[], noTag = 0;
  h += '<div class="card"><h3>她今天做过的事 <span class="tag">'+evs.length+' 件</span></h3>';
  h += '<div class="hint"><b>这份清单她看不见。</b>只有被话题勾到的那一件，才会作为「你想起来了」递到她眼前。</div>';
  evs.forEach(function(e){
    if(!(e.topics||[]).length) noTag++;
    h += '<div class="row" style="align-items:flex-start;margin:5px 0;border-bottom:1px solid var(--line);padding-bottom:5px">';
    h += '<div style="width:52px;color:var(--dim);font-size:13px">'+esc(e.time||'—')+'</div>';
    h += '<div style="flex:1;font-size:13px;line-height:1.6">'+esc(e.text)
      + (e.said?' <span class="tag">已说过</span>':'')+'</div>';
    h += '<div style="width:170px;text-align:right">'
      + ((e.topics||[]).map(function(t){return '<span class="tag">'+esc(t)+'</span>';}).join(' ')
         || '<span class="tag">挂不上话题</span>')+'</div>';
    h += '</div>';
  });
  if(noTag){
    h += '<div class="msg warn" style="margin-top:10px">有 '+noTag+' 件挂不上任何话题，'
      + '也就是说永远聊不出来。想让它能被聊出来，往 分享.py 的 TOPICS 里加个词。</div>';
  }
  h += '</div>';

  h += '<div class="card"><h3>话题表 <span class="tag">'+(s.topics||[]).length+' 个话题</span></h3>';
  h += '<div class="hint">'+(s.topics||[]).map(esc).join('　·　')+'</div>';
  h += '<div class="hint">他话里出现某个话题的词，话题才被激活。'
    + '想加话题（比如「音乐」「健身」）就改 分享.py 的 TOPICS。</div>';
  h += '</div>';

  document.getElementById("main").innerHTML = h;
  var q = document.getElementById("sq");
  if(q) q.addEventListener("keydown", function(e){ if(e.key==="Enter") tryShare(); });
}

async function tryShare(){
  var el = document.getElementById("sq"), box = document.getElementById("sqbox");
  var q = (el.value||"").trim();
  if(!q) return;
  box.innerHTML = '<div class="sub" style="margin:8px 0"><span class="spin"></span>联想中…</div>';
  var r = await api("/api/share/hit?q="+encodeURIComponent(q));
  if(!r.ok){ box.innerHTML = '<div class="msg bad">'+esc(r.error||"失败")+'</div>'; return; }
  var h = '<div class="sub" style="margin:8px 0">命中的话题：'
    + ((r.topics||[]).map(function(t){ return esc(t.name)+'('+t.score+')'; }).join('、') || '无') + '</div>';
  if(!(r.hits||[]).length){
    h += '<div class="hint">没翻出东西 —— '
      + (r.left<=0 ? '今天这种「说到自己」的机会已经用完了。' : '这句话跟今天的事撞不上，她会正常聊天。')
      + '</div>';
    box.innerHTML = h; return;
  }
  (r.hits||[]).forEach(function(x){
    h += '<div style="margin:8px 0;padding:8px;background:var(--code);border-radius:6px">'
      + '<b>'+esc(x.topic)+'</b> → '+esc((x.time?x.time+' ':'')+x.text)+'</div>';
  });
  h += '<div class="hint">递给她的原话长这样：</div>';
  h += '<div style="white-space:pre-wrap;font-size:12px;color:var(--dim);background:var(--code);'
    + 'padding:10px;border-radius:6px;line-height:1.7">'+esc(r.block||'')+'</div>';
  box.innerHTML = h;
}

/* ── 好感度 · 表白 · 恋人（谁都不许把这几块揉在一起） ──
   这一段刻意分成五块，因为它们讲的不是一件事：
     ① 现在在哪     —— 一眼看清
     ② 五档区间     —— 只按分数走的那四段（第 5 档没有区间）
     ③ 每档效果     —— 她这一档说多少、多久主动一次
     ④ 表白专区     —— 一道门：他先开口 / 她先开口 / 被拒
     ⑤ 恋人         —— 完全另一套，请看那一整块
     ⑥ 调试台       —— 试算用心分、直接跳到某档、看当前提示词
*/

// 表白参数放在 config.yaml 的 confess: 段里，这里列出来是给「表白参数」那张卡用的
var CF_KEYS = [
  "confess.line", "confess.direct_chance", "confess.prep_scale",
  "confess.repeat_penalty", "confess.hint_days", "confess.her_chance",
  "confess.give_up_after_days", "confess.give_up_chance",
  "confess.refuse_drop_to", "confess.despair_days", "confess.despair_gain_scale",
  "confess.prep_weights.len", "confess.prep_weights.name",
  "confess.prep_weights.past", "confess.prep_weights.why",
  "confess.prep_weights.night", "confess.prep_weights.build",
  "confess.prep_weights.gift"
];
function cfid(k){ return "cf" + k.replace(/\./g, "_"); }

function paneFavor(){
  var r = D.relation||{}, h = '';
  h += '<h2>好感度 <span class="tag">四级台阶 + 一道门</span></h2>';
  h += '<div class="sub">前四档<b>只按亲密度自动晋升</b>（聊得走心涨得快，每天有上限），你什么都不用做。'
    + '第五档「恋人」<b>不是台阶，是一道门</b> —— 亲密度涨到天上去也推不开它，'
    + '<b>只有表白才能进</b>。进门之后她整个人换一套规矩，跟前面四档不共用。</div>';
  if(r.error){
    document.getElementById("main").innerHTML =
      h + '<div class="card"><div class="msg bad">'+esc(r.error)+'</div></div>';
    return;
  }
  var t = r.stage_table||[];
  var isL = !!r.is_lover;

  /* ① 现在在哪 */
  h += '<div class="card"><h3>现在走到哪了 <span class="tag">第 '+r.stage+' / '+r.stage_count+' 档 · '+esc(r.stage_name)+'</span></h3>';
  h += '<div class="kv">';
  h += '<b>亲密度</b><span>'+r.intimacy+'（历史最高 '+r.peak+'）　今日已涨 '+r.today_gain+' / '+r.daily_cap+'</span>';
  h += '<b>表白线</b><span>到 <b>'+r.line+'</b> 她开始暗示'
    + (r.intimacy>=r.line? '　<span class="pill hot">早就过了</span>'
                          : '　还差 '+(r.line - r.intimacy))+'</span>';
  h += '<b>表白进度</b><span>'+esc(r.phase_text||r.phase)
    + (r.hint_level? '　·　暗示铺到第 '+r.hint_level+'/4 层「'+esc(r.hint_name)+'」':'') + '</span>';
  if(isL) h += '<b>在一起</b><span>第 '+r.lover_days+' 天（'+esc(r.lover_how||"")+'）</span>';
  if(r.despair) h += '<b>她的状态</b><span style="color:var(--bad);font-weight:700">很难过 —— 还有 '+r.despair_days_left+' 天</span>';
  if(r.awaiting) h += '<b>她的状态</b><span>已经开口了，正等你回</span>';
  h += '</div>';
  h += '<div class="bar"><i style="width:'+(r.progress||0)+'%"></i></div>';
  h += '<div class="hint">'+(r.next_gate
      ? '下一档是<b>一道门</b>：只能靠表白，分数推不动。'
      : (r.next_name? ('到下一档「'+esc(r.next_name)+'」还差 '+r.to_next+' 分（已走完 '+(r.progress||0)+'%）')
                    : '到顶了。'))+'</div>';
  h += '</div>';

  /* ② 五档区间（可改） */
  h += '<div class="card"><h3>五档区间 <span class="tag">随时可改</span></h3>';
  h += '<div class="hint">亲密度落进哪一段，她就在哪一档。<b>下一档的下限必须等于上一档的上限</b>，'
    + '否则保存会被拦下（列里会说清哪一档对不上）。第 5 档是门，没有区间 —— 留空就对了。</div>';
  h += '<div class="row" style="font-size:12px;color:var(--dim);padding-bottom:4px"><div style="width:34px">档</div>'
    + '<div style="width:150px">名字</div><div style="width:96px">下限</div><div style="width:96px">上限</div>'
    + '<div style="flex:1">说明</div></div>';
  t.forEach(function(s, i){
    var k = i+1, g = !!s.gate;
    h += '<div class="row" style="align-items:center;margin:4px 0'+(k===r.stage?';background:var(--code);border-radius:6px':'')+'">';
    h += '<div style="width:34px"><b>'+(k===r.stage?'▶ ':'')+k+'</b></div>';
    h += '<div style="width:150px"><input id="stg'+k+'name" value="'+esc(s.name)+'" style="width:100%"></div>';
    h += '<div style="width:96px"><input id="stg'+k+'from" value="'+esc(s.from==null?"":s.from)+'" style="width:100%"></div>';
    h += '<div style="width:96px"><input id="stg'+k+'to" value="'+esc(s.to==null?"":s.to)+'" style="width:100%"'
      + (g?' placeholder="不封顶"':'')+'></div>';
    h += '<div style="flex:1;font-size:12.5px;color:var(--dim)">'
      + (g? '<b style="color:var(--accent)">★ 一道门</b>：只能靠表白进，分数推不动'
          : (k===1? '刚认识，她还端着' : '亲密度到了就自动进')) + '</div>';
    h += '</div>';
  });
  h += '</div>';

  /* ③ 每档效果（可改） */
  h += '<div class="card"><h3>每一档的效果 <span class="tag">她说什么、说多少、多久主动一次</span></h3>';
  h += '<div class="hint">「允许说的话」用 、或 , 分隔（可选：名字 / 想你 / 撒娇 / 闹脾气）。'
    + '空着＝这一档还不许。第 5 档那行<b>故意全放开</b> —— 她不用再算着说了。</div>';
  h += '<div class="row" style="font-size:12px;color:var(--dim);padding-bottom:4px">'
    + '<div style="width:34px">档</div><div style="width:110px">一天说她几次</div>'
    + '<div style="width:100px">一条最多几字</div><div style="width:92px">一次最多几条</div>'
    + '<div style="width:104px">一次共几字</div><div style="width:92px">生成 token</div>'
    + '<div style="width:104px">主动次数/天</div><div style="width:92px">安静系数</div>'
    + '<div style="flex:1">允许说的话</div></div>';
  t.forEach(function(s, i){
    var k = i+1;
    h += '<div class="row" style="align-items:center;margin:4px 0'+(k===r.stage?';background:var(--code);border-radius:6px':'')+'">';
    h += '<div style="width:34px"><b>'+k+'</b></div>';
    h += '<div style="width:110px"><input id="stg'+k+'pd" value="'+esc(s.say_per_day)+'" style="width:90%"></div>';
    h += '<div style="width:100px"><input id="stg'+k+'sc" value="'+esc(s.say_chars)+'" style="width:90%"></div>';
    h += '<div style="width:92px"><input id="stg'+k+'sp" value="'+esc(s.say_parts)+'" style="width:90%"></div>';
    h += '<div style="width:104px"><input id="stg'+k+'st" value="'+esc(s.say_total)+'" style="width:90%"></div>';
    h += '<div style="width:92px"><input id="stg'+k+'tok" value="'+esc(s.say_tokens)+'" style="width:90%"></div>';
    h += '<div style="width:104px"><input id="stg'+k+'ap" value="'+esc(s.active_per_day)+'" style="width:90%"></div>';
    h += '<div style="width:92px"><input id="stg'+k+'is" value="'+esc(s.idle_scale)+'" style="width:90%"></div>';
    h += '<div style="flex:1"><input id="stg'+k+'al" value="'+esc((s.allow||[]).join('、'))+'" style="width:100%"></div>';
    h += '</div>';
  });
  h += '<div class="row" style="margin-top:12px"><button onclick="saveStages()">保存五档表</button>'
    + '<button class="ghost" onclick="resetStages()">恢复默认</button>'
    + '<span class="msg" id="stgmsg"></span></div>';
  h += '<div class="hint">保存会写进 关系.json（立刻生效，机器人不用重启），同时也在 config.yaml 的 '
    + '<b>relation.stages</b> 里留一份 —— 以后想手改 YAML 也有个底。</div>';
  h += '<div class="hint">安静系数＝「多久没说话她才想起你」的倍数：1.8＝得安静很久，0.3＝你半小时不吭声她就坐不住。</div>';
  h += '</div>';

  /* ④ 表白专区 */
  h += '<div class="card"><h3>表白 <span class="tag">第 4 档满了之后的两条路</span></h3>';
  h += '<div class="sub">两条路完全不同，别混：<b>他先开口</b>要算用心分；'
    + '<b>她先开口</b>是他点头或摇头。不管哪条，最后都要过同一道门。</div>';
  h += '<div class="kv">';
  h += '<b>① 他先开口</b><span>一句话直接说 —— 基础成功率只有 <b>'+Math.round((r.confess_cfg.direct_chance||0.25)*100)+'%</b>，'
    + '因为那样她会觉得敷衍。<b>但她不会拒绝你</b>，她会叫你回去好好想想该怎么表白。'
    + '说得越用心，成功率越高（满分能到 '+Math.round((r.chance_best||0)*100)+'%）。'
    + (r.tried? '　你已经随便试过 '+r.tried+' 次，每次扣 '
        +Math.round((r.confess_cfg.repeat_penalty||0.05)*100)+'%。' : '') + '</span>';
  h += '<b>② 她先开口</b><span>亲密度过了 '+r.line+' 之后，她开始<b>一层一层地暗示</b>，'
    + '绝不会直接说要表白；暗示铺满（'+r.confess_cfg.hint_days+' 天），她就会挑一天自己开口 —— '
    + '每天 '+Math.round((r.confess_cfg.her_chance||0)*100)+'% 的概率。'
    + '这时候你可以<b>答应</b>（进第 5 档），也可以<b>拒绝</b> —— 拒绝她会极伤心、自暴自弃，'
    + '亲密度直接掉回 <b>'+r.confess_cfg.refuse_drop_to+'</b>（重要的人）。</span>';
  h += '<b>③ 拖太久</b><span>她铺完暗示之后等 '+r.confess_cfg.give_up_after_days
    + ' 天还没等到，有 '+Math.round((r.confess_cfg.give_up_chance||0)*100)+'% 的概率'
    + '<b>把这份心思收回去</b>（轻症）。这时候你不提，她就当没这回事 —— 但你真开口，她还是会听。</span>';
  h += '</div>';

  /* 成功率曲线 */
  h += '<div style="margin-top:12px"><b>用心分 → 成功率</b></div>';
  h += '<div class="row" style="font-size:12.5px;color:var(--dim);margin-top:4px">';
  (r.chance_curve||[]).forEach(function(x){
    h += '<div style="flex:1;text-align:center;padding:6px;background:var(--code);border-radius:6px;margin:0 3px">'
      + '用心分 '+x.prep+'<br><b style="color:var(--accent);font-size:15px">'+Math.round(x.chance*100)+'%</b></div>';
  });
  h += '</div>';
  h += '<div class="hint">用心分 = 你说那句话里七样东西各值多少分，满分 '
    + (r.prep_max||100)+' 分。改下面「用心分怎么算」里的权重就能调。</div>';

  /* 四层暗示 */
  h += '<div style="margin-top:16px"><b>四层暗示</b> <span class="tag">一天深一层</span></div>';
  h += '<div class="hint">前面三层她<b>绝不承认</b> —— 你问就是「你想多了」。到第 4 层才是随时会开口。</div>';
  (r.hint_levels||[]).forEach(function(x, i){
    var on = r.hint_level === (i+1);
    h += '<div style="margin:5px 0;padding:7px 9px;border-radius:6px;'
      + (on? 'background:var(--code);border-left:3px solid var(--accent)' : 'background:var(--code)')+'">';
    h += '<b>第 '+x.n+' 层 · '+esc(x.name)+'</b>' + (on? ' <span class="tag">现在在这层</span>':'');
    h += '<div style="font-size:13px;line-height:1.65;margin-top:3px">'+esc(x.what)+'</div>';
    h += '<div class="hint">她这时候会这么说：'+(x.say||[]).map(function(s){return '「'+esc(s)+'」';}).join('　')+'</div>';
    h += '<div class="hint">'+esc(x.cant||'')+'</div></div>';
  });

  /* 她自己开口那段 */
  h += '<div style="margin-top:16px"><b>她真开口那天，大概是这个骨架</b></div>';
  h += '<div class="hint">不是台词，是骨架 —— 每次说出来的不一样，但形状是这几个动作。</div>';
  (r.her_lines||[]).forEach(function(x){
    h += '<div style="margin:4px 0;padding:5px 8px;background:var(--code);border-radius:6px;font-size:13px">· '+esc(x)+'</div>';
  });

  /* 表白参数 */
  h += paneConfessForm(r);

  h += '<div class="hint"><b>被拒之后</b>那几天，她会说的大概是这些：'
    + (r.despair_say||[]).map(function(s){return '「'+esc(s)+'」';}).join('　')
    + '　—— 这段每轮都会出现在她的提示词里，直到 '
    + (r.confess_cfg.despair_days||7)+' 天过去。</div>';
  h += '</div>';

  /* ⑤ 恋人（单独一大块） */
  h += '<div class="card" style="border-color:var(--accent)"><h3>第五档 · 恋人 <span class="tag">跟前面四档不是一回事</span></h3>';
  h += '<div class="sub">'+esc(r.lover_head||'')+'</div>';
  h += '<div class="hint">她谈恋爱之后是这样 —— 这 '+((r.lover_rules||[]).length)+' 条每轮都在她的提示词里，'
    + '完整写法在「你和我」的第 5 档（那一节在你没到恋人档之前，她是读不到的）。</div>';
  (r.lover_rules||[]).forEach(function(x, i){
    h += '<div style="margin:5px 0;padding:7px 9px;background:var(--code);border-radius:6px;font-size:13px;line-height:1.7">'
      + '<b>'+(i+1)+'.</b> '+esc(x)+'</div>';
  });
  h += '<div class="hint" style="margin-top:12px"><b>恋人档的数字</b>（一天说几条、一次几字、主动几次、粘不粘人）'
    + '就是上面「每一档的效果」那张表的<b>第 5 行</b> —— 那一行是独立的一套，别照前四档的规律去推它。</div>';
  h += '<div class="hint">在一起之后还会开几条<b>只有恋人能演的剧情线</b>（第一次真正的吵架 / 她复查结果不太好 / '
    + '你们的日子 / 那部手机不太对劲）—— 去「大事」那一页看。</div>';
  h += '<div class="hint">回到前面的档位去演？在下面调试台把档位切回去（或者点「重置表白状态」），'
    + '第 4 档那条「她心里那件事」也会跟着自动撤掉。</div>';
  h += '</div>';

  /* ⑥ 调试台 */
  h += '<div class="card"><h3>调试台 <span class="tag">不用真等，先看效果</span></h3>';
  h += '<div class="hint">这些按钮直接改 关系.json，立刻生效。<b>动的是真数据</b> —— 想留个底就先复制一份 关系.json。</div>';

  h += '<div style="margin-top:10px"><b>① 用心分试算</b></div>';
  h += '<div class="hint">把你想说的话打进去，看她会算你几分、成功率是多少。</div>';
  h += '<textarea id="fmtxt" rows="3" style="width:100%" placeholder="达妮娅，我想了很久…"></textarea>';
  h += '<div class="row" style="align-items:center;margin-top:6px">'
    + '<label style="width:auto"><input type="checkbox" id="fmphoto"> 带了照片</label>'
    + '<label style="width:auto"><input type="checkbox" id="fmvoice"> 发了语音</label>'
    + '<label style="width:auto"><input type="checkbox" id="fmgift"> 有礼物</label>'
    + '<label style="width:auto">最近聊到你们俩的轮数　<input id="fmbuild" value="0" style="width:52px"></label>'
    + '<button onclick="prepTry()">算一算</button></div>';
  h += '<div id="fmres"></div>';

  h += '<div style="margin-top:18px"><b>② 直接跳到某一档</b>（调试）</div>';
  h += '<div class="row" style="margin-top:6px">';
  for(var i2=1;i2<=5;i2++){
    h += '<button class="ghost" onclick="confessDo(\'stage\',null,'+(i2)+')">跳到第 '+i2+' 档</button> ';
  }
  h += '</div>';

  h += '<div style="margin-top:18px"><b>③ 把表白流程走一遍</b>（调试）</div>';
  h += '<div class="row" style="margin-top:6px">';
  h += '<button class="ghost" onclick="confessDo(\'phase\')">推进一天</button> ';
  h += '<button class="ghost" onclick="confessDo(\'her\')">让她开口（挂上「等你回」）</button> ';
  h += '<button class="ghost" onclick="confessDo(\'accept\')">替她答应</button> ';
  h += '<button class="ghost" onclick="confessDo(\'refuse\')">替她拒绝（会掉档！）</button>';
  h += '</div>';
  h += '<div class="row" style="margin-top:6px">'
    + '<button class="ghost" onclick="confessDo(\'reset\')">重置表白状态（恋人档也会一起退出）</button>'
    + '<span class="msg" id="cfmsg"></span></div>';

  h += '<div style="margin-top:18px"><b>④ 她这一轮实际读到的提示词</b></div>';
  h += '<div class="hint">你在别处改了参数，想知道她那边到底变成什么样 —— 看这里，不用去猜。</div>';
  var ps = r.prompt_seen||{};
  Object.keys(ps).forEach(function(k){
    if(!ps[k]) return;
    h += '<div style="margin-top:8px"><span class="tag">'+esc(k)+'</span></div>';
    h += '<div style="white-space:pre-wrap;font-size:12px;color:var(--dim);background:var(--code);'
      + 'padding:10px;border-radius:6px;line-height:1.7;max-height:340px;overflow:auto">'+esc(ps[k])+'</div>';
  });
  h += '</div>';

  document.getElementById("main").innerHTML = h;
}

/* 表白参数那张卡（读 config 的 confess: 段） */
function paneConfessForm(r){
  var g = D.config||{}, h = '';
  h += '<div style="margin-top:16px"><b>表白参数</b> <span class="tag">门有多高、她多沉不住气，都在这儿</span></div>';
  h += '<div class="hint">存在 config.yaml 的 <b>confess:</b> 段里。改完点保存，下一轮就生效（不用重启）。</div>';
  CF_KEYS.forEach(function(k){
    var c = g[k]||{};
    h += '<div class="row" style="align-items:center;margin:5px 0"><div style="flex:1;font-size:13px">'
      + esc(c.label||k)+'</div>'
      + '<div style="width:110px"><input id="'+cfid(k)+'" value="'
      + esc(c.value==null?"":c.value)+'" style="width:100%"></div></div>';
  });
  h += '<div class="row" style="margin-top:8px"><button onclick="saveConfessCfg()">保存表白参数</button>'
    + '<span class="msg" id="cfcfgmsg"></span></div>';
  return h;
}

async function saveConfessCfg(){
  var vals = {};
  CF_KEYS.forEach(function(k){
    var e = document.getElementById(cfid(k));
    if(e) vals[k] = e.value;
  });
  var el = document.getElementById("cfcfgmsg");
  el.className = "msg"; el.textContent = "保存中…";
  var r = await api("/api/config", {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify({values:vals})});
  if(!r.ok){ el.className = "msg bad"; el.textContent = r.error||"失败"; return; }
  el.className = "msg good"; el.textContent = "已保存（下一轮生效）";
  setTimeout(function(){ reload(); }, 600);
}

async function saveStages(){
  var t = (D.relation||{}).stage_table||[];
  var el = document.getElementById("stgmsg");
  function v(id){ var e = document.getElementById(id); return e? (e.value||"").trim() : ""; }
  var out = [];
  for(var i=0;i<t.length;i++){
    var k = i+1;
    out.push({
      key: t[i].key, gate: t[i].gate||"",
      name: v("stg"+k+"name"),
      from: v("stg"+k+"from"), to: v("stg"+k+"to"),
      say_per_day: v("stg"+k+"pd"), say_chars: v("stg"+k+"sc"),
      say_parts: v("stg"+k+"sp"), say_total: v("stg"+k+"st"),
      say_tokens: v("stg"+k+"tok"), active_per_day: v("stg"+k+"ap"),
      idle_scale: v("stg"+k+"is"), allow: v("stg"+k+"al")
    });
  }
  el.className = "msg"; el.textContent = "保存中…";
  var r = await api("/api/relation/stages", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({stages: out, write_cfg: true})});
  if(!r.ok){ el.className = "msg bad"; el.textContent = r.error||"失败"; return; }
  D.relation = r.relation;
  el.className = "msg good"; el.textContent = r.note||"已保存";
  setTimeout(function(){ reload(); }, 700);
}

async function resetStages(){
  var el = document.getElementById("stgmsg");
  el.className = "msg"; el.textContent = "恢复中…";
  var r = await api("/api/relation/stages/reset", {method:"POST",
    headers:{"Content-Type":"application/json"}, body:"{}"});
  if(!r.ok){ el.className = "msg bad"; el.textContent = r.error||"失败"; return; }
  D.relation = r.relation;
  el.className = "msg good"; el.textContent = r.note||"已恢复默认";
  setTimeout(function(){ reload(); }, 700);
}

async function confessDo(act, prep, stage){
  var el = document.getElementById("cfmsg");
  if(el){ el.className = "msg"; el.textContent = "…"; }
  var body = {action: act};
  if(prep != null)  body.prep = prep;
  if(stage != null) body.stage = stage;
  var r = await api("/api/relation/confess", {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  if(!r.ok){ if(el){ el.className="msg bad"; el.textContent = r.error||"失败"; } return; }
  D.relation = r.relation;
  var t = r.result||{}, msg = "";
  if(t && t.ok === false){
    if(el){ el.className = "msg bad"; el.textContent = t.error || "这一步没做成"; }
    setTimeout(function(){ reload(); }, 700);
    return;
  }
  if(act === "try"){
    msg = "成功率 "+Math.round((t.chance||0)*100)+"%　掷出 "+(t.roll!=null?t.roll:"—")
      + "　→ "+(t.ok? "成功，你们在一起了" : "没成：她没答应，也没拒绝，叫你回去好好想想");
  } else if(act === "phase"){
    msg = "推了一天。" + (t.started? " 她开始暗示了（第 1 层）。" : "")
      + (t.hint_level? (" 暗示到第 "+t.hint_level+" 层。") : "")
      + (t.gave_up? " 她把这份心思收回去了。" : "");
  } else if(act === "her"){
    msg = "她开口了，现在等你回 —— 你在聊天里说「好」就在一起，说「算了」她就掉档。";
  } else if(act === "accept"){
    msg = "答应了：现在第 "+t.stage+" 档 · 恋人。";
  } else if(act === "refuse"){
    msg = "拒绝了：亲密度掉回 "+t.intimacy+"（第 "+t.stage+" 档），自暴自弃到 "+t.despair_until+"。";
  } else if(act === "reset"){
    msg = "表白状态清空了（档位没动）。";
  } else if(act === "stage"){
    msg = "现在第 "+(t.stage||stage)+" 档。";
  }
  if(el){ el.className = "msg " + (act==="refuse"?"warn":"good"); el.textContent = msg; }
  setTimeout(function(){ reload(); }, 800);
}

async function prepTry(){
  var tx = document.getElementById("fmtxt");
  var q = (tx && tx.value || "").trim();
  var box = document.getElementById("fmres");
  if(!q){ if(box) box.innerHTML = '<div class="msg warn">先打一句要试的话。</div>'; return; }
  function ck(id){ var e = document.getElementById(id); return !!(e && e.checked); }
  var be = document.getElementById("fmbuild");
  var build = parseInt((be && be.value || "0"), 10); if(isNaN(build)) build = 0;
  box.innerHTML = '<div class="sub" style="margin-top:8px"><span class="spin"></span>算…</div>';
  var r = await api("/api/relation/prep", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({text:q, photo:ck("fmphoto"), voice:ck("fmvoice"),
                         gift:ck("fmgift"), build:build})});
  if(!r.ok){ box.innerHTML = '<div class="msg bad">'+esc(r.error||"失败")+'</div>'; return; }
  var p = r.prep, h = '';
  h += '<div class="kv" style="margin-top:10px">';
  h += '<b>用心分</b><span><b style="color:var(--accent);font-size:18px">'+p.score+'</b> / '+p.max
    + '　→ 成功率 <b>'+Math.round((p.chance||0)*100)+'%</b>'
    + '（裸表白是 '+Math.round((p.chance_base||0)*100)+'%）</span>';
  h += '<b>认得出是表白吗</b><span>'+(p.is_confess? '认得出来' : '不像表白 —— 她那边不会走表白流程')+'</span>';
  h += '<b>字数</b><span>'+p.chars
    + '　·　她现在是深夜模式吗：'+(p.night? '是（22:30–02:00）':'不是')+'</span>';
  if(p.tried) h += '<b>已随便试过</b><span>'+p.tried+' 次（每次扣成功率）</span>';
  h += '</div>';
  h += '<div style="margin-top:8px">';
  (p.items||[]).forEach(function(x){
    h += '<div class="row" style="align-items:flex-start;margin:4px 0">'
      + '<div style="width:24px;font-weight:700;color:'+(x.got? 'var(--accent)':'var(--dim)')+'">'+(x.got? '√':'-')+'</div>'
      + '<div style="flex:1"><b style="font-size:13px">'+esc(x.label)+'</b> '
      + '<span class="tag">'+x.weight+' 分</span>'
      + '<div class="hint">'+esc(x.how)+'</div></div></div>';
  });
  h += '</div>';
  if(p.tips && p.tips !== "挑不出毛病。"){
    h += '<div class="msg warn" style="margin-top:8px">还差：'+esc(p.tips)
      + '　—— 她说「你回去好好想想该怎么跟我说」，说的就是这几样。</div>';
  } else {
    h += '<div class="msg good" style="margin-top:8px">挑不出毛病 —— 这句话她听得出来是认真的。</div>';
  }
  box.innerHTML = h;
}

/* ── 主动消息（她会不会自己先开口） ── */
function paneProactive(){
  var p = D.proactive||{}, h = '';
  h += '<h2>主动消息 <span class="tag">她会不会自己先开口</span></h2>';
  h += '<div class="sub">没有这一块，她就永远是个「你问一句、她答一句」的应答机。'
    + '但「会主动」不是一开始就有的 —— 关系浅的时候她压根不开口（那是第 3 档才长出来的本事）。'
    + '她没动静的时候，聊天记录里永远是空的，只有这页说得出是哪道闸卡住了。</div>';
  if(p.error){
    document.getElementById("main").innerHTML = h+'<div class="card"><div class="msg bad">'+esc(p.error)+'</div></div>';
    return;
  }

  h += '<div class="card"><h3>现在能不能发 <span class="tag">第 '+p.stage+' 档</span></h3>';
  h += '<div class="kv">';
  if(p.can_send){
    h += '<b>结论</b><span><b style="color:var(--ok)">条件都满足了</b>'
      + '　—— 每次轮询有 '+Math.round((p.chance||0)*100)+'% 概率她就会开口，'
      + '所以「满足条件」不等于「马上就发」，这是故意的</span>';
  } else {
    h += '<b>结论</b><span><b style="color:var(--accent)">现在不会发</b>　—— '+esc(p.why_not)+'</span>';
  }
  h += '<b>安静了多久</b><span>'+p.idle_minutes+' 分钟　/　这一档要满 <b>'+p.wait_minutes+'</b> 分钟'
    + '（基础 '+p.base_minutes+' × 档位系数 '+p.idle_scale+'）</span>';
  h += '<b>今天已经主动</b><span>'+p.sent_day+' / '+(p.max_day===0?'0（这一档不主动）':p.max_day)+' 次'
    + (p.quota!=null? '　—— 档位给的配额是 '+p.quota+' 次，config 里写的是 '+p.config_max_day+' 次，取小的' : '')
    + '</span>';
  h += '<b>这一个小时</b><span>'+p.sent_hour+' / '+p.max_hour+' 次</span>';
  h += '<b>活跃时段</b><span>'+p.active_hours[0]+' 点到 '+p.active_hours[1]+' 点（这个区间之外她不会主动开口）</span>';
  h += '<b>此刻她在</b><span>'+esc(p.busy_where||"——")
    + (p.quiet_when_busy
        ? (p.busy? '　—— <b style="color:var(--accent)">走不开</b>，这会儿不主动开口'
                 : '　—— <b style="color:var(--ok)">腾得出手</b>')
        : '　—— 这道闸关着，走不走得开她都可能开口')+'</span>';
  h += '<b>素材从哪来</b><span>daily_life.nudge()：优先正在演的大事，再挑今天还没提过的事</span>';
  h += '<b>记录在</b><span>'+esc(p.state_file)+'　—— 重启不清零，靠的就是它</span>';
  h += '</div></div>';

  h += '<div class="card"><h3>此刻她走不走得开 <span class="tag">第 ⑤ 道闸看的就是它</span></h3>';
  h += '<div class="kv">';
  if(p.busy){
    h += '<b>结论</b><span><b style="color:var(--accent)">走不开</b>　—— '+esc(p.busy_where||"")+'</span>';
  } else {
    h += '<b>结论</b><span><b style="color:var(--ok)">腾得出手</b>　—— '+esc(p.busy_where||"（作息表接不上，就当她不忙）")+'</span>';
  }
  h += '<b>这道闸</b><span>'+(p.quiet_when_busy
      ? '<b style="color:var(--ok)">开着</b>（quiet_when_busy）'
      : '<b style="color:var(--accent)">关着</b>（quiet_when_busy）')+'</span>';
  h += '</div>';
  h += '<div class="hint">上课 / 睡着的时候她<b>不会自己先找你</b> —— 真人上课时不会先找人聊天。<br>'
    + '★ 这道闸<b>只管「她先开口」这一路</b>：你发消息来她照样回，不受影响（那是「不装死」，两码事）。<br>'
    + '判断用的是<b>分钟级作息表</b>，跟「她的日子」页、「她自检」里看到的完全一致 —— '
    + '控制台上她写着在上课，这里就一定不会开口，两边对得上。<b>改完不用重启</b>。</div>';
  h += '</div>';

  h += '<div class="card"><h3>五道闸 <span class="tag">按顺序过，过不了就不发</span></h3>';
  h += '<div class="hint">空闲时每轮扫一次，下面五条一条不过就跳过这一轮：<br>'
    + '① <b>分享欲配额</b>：第 1、2 档是 0 —— 刚认识她根本不会来找你<br>'
    + '② <b>频率</b>：活跃时段 + 每小时 '+p.max_hour+' 条 + 每天上限<br>'
    + '③ <b>安静够久</b>：至少「'+p.base_minutes+' 分钟 × 档位系数」，越熟这个系数越小（1.8 → 0.3）<br>'
    + '④ <b>走不走得开</b>：上课 / 睡着 → 直接跳过，这是硬性的，不看概率<br>'
    + '⑤ <b>随机</b>：满足条件也只按概率发，时机才不会像闹钟</div>';
  h += '</div>';

  h += '<div class="card"><h3>试生成一条 <span class="tag">不会真的发出去</span></h3>';
  h += '<div class="hint">真跑一次模型（不真跑就看不出效果），但<b>一个字都不记</b> —— '
    + '不动历史、不消耗素材、不推进关系。看完就扔。</div>';
  h += '<button onclick="tryProactive()">生成一条给我看看</button>';
  h += '<div id="pabox"></div>';
  h += '</div>';

  var times = p.sent_times||[];
  h += '<div class="card"><h3>今天她主动过的时间 <span class="tag">'+times.length+' 次</span></h3>';
  if(!times.length){
    h += '<div class="hint">今天一次都还没有。上面「结论」那一行会告诉你为什么。</div>';
  } else {
    h += '<div class="hint">'+times.map(function(t){ return esc(t); }).join('　·　')+'</div>';
  }
  h += '</div>';

  var pkeys = ["proactive.enabled","proactive.min_idle_minutes",
    "proactive.chance_per_tick","proactive.active_hours.0","proactive.active_hours.1",
    "proactive.max_per_hour","proactive.max_per_day","proactive.quiet_when_busy"];
  h += '<div class="card"><h3>调它 <span class="tag">改完重启生效</span></h3>';
  h += cfgFieldsHtml(pkeys);
  h += '<button onclick="saveCfg('+JSON.stringify(pkeys).replace(/"/g,"&quot;")+')">保存</button>';
  h += '<div class="msg" id="cfgmsg"></div>';
  h += '<div class="hint">「安静多少分钟」是<b>基础值</b>，实际还要乘关系档位系数（第 1 档 ×1.8 → 第 5 档恋人 ×0.3）—— '
    + '所以想让她早点来，改档位比改这里更管用。每天上限还会被分享欲的档位配额再压一次。<br>'
    + '<b>「上课 / 睡着时不主动开口」</b>＝ 上面那道第 ④ 号闸，关掉就恢复「什么时候都可能开口」。'
    + '它只管「她先开口」，你发消息来她照回。</div></div>';

  document.getElementById("main").innerHTML = h;
}

async function tryProactive(){
  var box = document.getElementById("pabox");
  box.innerHTML = '<div class="sub" style="margin:10px 0"><span class="spin"></span>让她想说点什么…（真跑一次模型，稍等）</div>';
  var r = await api("/api/proactive/try", {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
  if(!r.ok){ box.innerHTML = '<div class="msg bad">'+esc(r.error||"失败")+'</div>'; return; }
  var h = '';
  if(r.blocked){
    h += '<div class="msg warn">★ 注意：她<b>此刻走不开</b>（'+esc(r.now_where||"")+'）—— '
      + '真跑起来这一条<b>不会发出去</b>（第 ④ 号闸会拦下它）。'
      + '这里只是想让你看看她上课时会怎么措辞。想把这道闸关掉：'
      + '把下面「上课 / 睡着时不主动开口」改成「关」。</div>';
  } else if(r.now_where){
    h += '<div class="msg ok">她此刻：'+esc(r.now_where)+'　—— 腾得出手，这一条真跑起来是会发的。</div>';
  }
  h += '<div class="hint" style="margin-top:10px">她今天想说的事：<b>'
    + esc((r.seed&&r.seed.time?r.seed.time+' ':'')+(r.seed&&r.seed.text||''))+'</b>'
    + (r.seed&&r.seed.kind? '（'+esc(r.seed.kind)+'）':'') + '</div>';
  h += '<div class="hint">这件事是 <b>'+esc(r.ago_text||"")+'</b> 发生的'
    + (r.seed_time? '（日程上写在 '+esc(r.seed_time)+'）':'')
    + ' —— 所以提示词里要她把时间词带上，别读成「她此刻还站在那儿」。<br>'
    + '★ 这就是上次那个矛盾（10:28 自检说她在上课、消息却说「电梯里碰到一群新生」）修掉的地方。</div>';
  h += '<div class="msg ok">会发成这样（'+r.parts.length+' 条，共 '+r.chars+' 字）：</div>';
  (r.parts||[]).forEach(function(t){
    h += '<div style="margin:6px 0;padding:8px 10px;background:var(--code);border-radius:6px;'
      + 'display:inline-block;max-width:80%">'+esc(t)+'</div><br>';
  });
  h += '<div class="hint">'+esc(r.note||'')+'</div>';
  box.innerHTML = h;
}

/* ── 读图（她能不能看懂你发的图片） ── */
function paneVision(){
  var v = D.vision||{}, h = '';
  h += '<h2>读图 <span class="tag">她能不能看懂你发的图片</span></h2>';
  h += '<div class="sub">收消息走的是「后台截图 + OCR」，OCR <b>只会认字</b> —— '
    + '你发一张图片过去，聊天区里就是一块缩略图，一个字符都读不到，她既不吭声也不知道你发了东西。'
    + '这页配的是一条补路：把那块截图裁下来交给<b>视觉模型</b>读成人话，再当成「你看到的就是这些」递给她。</div>';
  if(v.error){
    document.getElementById("main").innerHTML = h+'<div class="card"><div class="msg bad">'+esc(v.error)+'</div></div>';
    return;
  }

  h += '<div class="card"><h3>现在能不能读</h3><div class="kv">';
  if(v.ready){
    h += '<b>结论</b><span><b style="color:var(--ok)">能读</b></span>';
  } else {
    h += '<b>结论</b><span><b style="color:var(--accent)">读不了</b>　—— '+esc(v.why_not)+'</span>';
  }
  h += '<b>视觉模型</b><span>'+esc(v.model||'（没填）')+'</span>';
  h += '<b>今天已读</b><span>'+v.used_today+' / '+(v.max_per_day||'不限')+' 张</span>';
  h += '<b>最近读到</b><span>'+(v.last_text? esc(v.last_text.slice(0,80)) : '还没读过')
    + (v.last_error? '　<b style="color:var(--bad)">上次出错：'+esc(v.last_error.slice(0,80))+'</b>' : '')+'</span>';
  h += '</div></div>';

  h += '<div class="card"><h3>读不到的时候她会说什么</h3>';
  h += '<div class="hint">读不出来（没配模型 / 调用失败 / 额度用完）时<b>绝不猜</b> —— 她会老实承认看不见：</div>';
  h += '<div style="white-space:pre-wrap;font-size:12px;color:var(--dim);background:var(--code);'
    + 'padding:10px;border-radius:6px;line-height:1.7">'+esc(v.cant_see||'')+'</div>';
  h += '</div>';

  h += '<div class="card"><h3>试读一张图 <span class="tag">不用去微信发图就能验证</span></h3>';
  h += '<div class="hint">填本机图片的完整路径（png / jpg 都行），用你现在这套配置真读一次。</div>';
  h += '<div class="row" style="align-items:flex-end"><div style="flex:1"><label>图片路径</label>';
  h += '<input type="text" id="vpath" placeholder="比如 D:/照片/test.png"></div>';
  h += '<button onclick="tryVision()">读一下</button></div>';
  h += '<div id="vbox"></div>';
  h += '</div>';

  h += '<div class="card"><h3>它给模型的原话 <span class="tag">提示词</span></h3>';
  h += '<div style="white-space:pre-wrap;font-size:12px;color:var(--dim);background:var(--code);'
    + 'padding:10px;border-radius:6px;line-height:1.7">'+esc(v.prompt||'')+'</div>';
  h += '<div class="hint">提示词写得死板是故意的：模型的输出会被当成聊天内容喂给达妮娅，'
    + '它要是分点、写作文、加评价，她那句话也就跟着变 AI 味了。</div>';
  h += '</div>';

  h += '<div class="card"><h3>调它 <span class="tag">改完重启生效</span></h3>';
  h += cfgFieldsHtml(["vision.enabled","vision.model","vision.base_url","vision.api_key",
    "vision.max_tokens","vision.max_per_day","vision.min_gap_seconds","vision.max_side"]);
  h += '<button onclick="saveCfg('+JSON.stringify(["vision.enabled","vision.model","vision.base_url","vision.api_key","vision.max_tokens","vision.max_per_day","vision.min_gap_seconds","vision.max_side"]).replace(/"/g,"&quot;")+')">保存</button>';
  h += '<div class="msg" id="cfgmsg"></div>';
  h += '<div class="hint"><b>说话那个模型（DeepSeek 系列）不会看图</b>，这里必须单独填一个视觉模型。'
    + '地址和 Key 留空就跟上面 llm 用同一套。实测好用的是 Qwen/Qwen3-VL-32B-Instruct（硅基流动）。</div></div>';

  document.getElementById("main").innerHTML = h;
}

async function tryVision(){
  var box = document.getElementById("vbox");
  var p = (document.getElementById("vpath").value||"").trim();
  if(!p){ box.innerHTML = '<div class="msg bad">先填个图片路径</div>'; return; }
  box.innerHTML = '<div class="sub" style="margin:10px 0"><span class="spin"></span>让模型看图…</div>';
  var r = await api("/api/vision/try", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:p})});
  if(!r.ok){ box.innerHTML = '<div class="msg bad">'+esc(r.error||"失败")+'</div>'; return; }
  var h = '';
  if(r.kind==="text"){
    h += '<div class="msg ok">读出来了：</div>';
    h += '<div style="margin:6px 0;padding:10px;background:var(--code);border-radius:6px;'
      + 'white-space:pre-wrap;line-height:1.7">'+esc(r.text)+'</div>';
    h += '<div class="hint">递给她的原话长这样（这段会进这一轮的提示词）：</div>';
    h += '<div style="white-space:pre-wrap;font-size:12px;color:var(--dim);background:var(--code);'
      + 'padding:10px;border-radius:6px;line-height:1.7">'+esc(r.wrapped)+'</div>';
  } else if(r.kind==="notimage"){
    h += '<div class="msg warn">模型说这画面里只有文字，没有图片 —— 这种情况不会用它，'
      + '交给 OCR 就够了。</div>';
  } else {
    h += '<div class="msg bad">读不了：'+esc(r.why)+'</div>';
  }
  box.innerHTML = h;
}

/* ── 配置类面板 ── */
function cfgFieldsHtml(keys){
  var h = '';
  keys.forEach(function(k){
    var c = D.config[k]; if(!c) return;
    var v = c.value;
    var type = "text";
    if(typeof v === "boolean") type = "bool";
    else if(typeof v === "number") type = "number";
    if(k.indexOf("api_key")>=0) type = "password";
    h += '<label>'+esc(c.label)+' <span style="opacity:.6">('+esc(k)+')</span></label>';
    if(type==="bool"){
      h += '<select id="c_'+k+'"><option value="true"'+(v?" selected":"")+'>开</option><option value="false"'+(!v?" selected":"")+'>关</option></select>';
    } else {
      h += '<input type="'+type+'" id="c_'+k+'" value="'+esc(v==null?"":v)+'">';
    }
  });
  return h;
}

function paneConfig(keys, title, sub, extra){
  var h = '<h2>'+esc(title)+'</h2><div class="sub">'+esc(sub)+'</div><div class="card">';
  h += cfgFieldsHtml(keys);
  h += '<button onclick="saveCfg('+JSON.stringify(keys).replace(/"/g,"&quot;")+')">保存</button>';
  h += '<div class="msg" id="cfgmsg"></div>';
  h += '<div class="hint">保存时原文件会备份成 config.yaml.bak。改完重启「一键启动.bat」生效（关系进度和记忆下次运行就会用新值）。</div>';
  h += '</div>';
  h += (extra||'');
  document.getElementById("main").innerHTML = h;
}

/* 表情包的实况卡（挂在「语音与节奏」页下面） */
function paneStickerCard(){
  var s = D.sticker||{};
  var h = '<div class="card"><h3>表情包 <span class="tag">她回完话会追一张</span></h3>';
  if(s.error){ return h+'<div class="msg bad">'+esc(s.error)+'</div></div>'; }
  h += '<div class="kv">';
  if(!s.enabled){
    h += '<b>现在</b><span><b style="color:var(--accent)">关着</b>　—— '
      + '打开上面那条「她会发表情包」</span>';
  } else if(!s.dir_exists || !s.count){
    h += '<b>现在</b><span><b style="color:var(--accent)">还发不了</b>　—— '+esc(s.why_not)+'</span>';
  } else if(s.can_send){
    h += '<b>现在</b><span><b style="color:var(--ok)">条件都满足</b>　—— '
      + '每次回完话有 '+Math.round((s.chance||0)*100)+'% 概率跟一张</span>';
  } else {
    h += '<b>现在</b><span><b style="color:var(--accent)">这会儿不发</b>　—— '+esc(s.why_not)+'</span>';
  }
  h += '<b>目录</b><span>'+esc(s.dir)+'　—— 认到 <b>'+s.count+'</b> 张（只认 '+esc((s.exts||[]).join('/'))+'）</span>';
  h += '<b>今天已发</b><span>'+s.sent_today+' / '+s.max_per_day+' 张'
    + ((s.sent_times||[]).length? '　（'+s.sent_times.map(function(t){return esc(t);}).join('、')+'）' : '')+'</span>';
  h += '<b>聊过</b><span>'+s.turns+' 轮　·　到 '+s.min_turns+' 轮才开始发</span>';
  h += '</div>';

  var fs = s.files||[];
  if(fs.length){
    h += '<div class="hint" style="margin-top:10px">目录里的图（<b>最近发过的会排到后面</b>，免得连着出现同一张）：</div>';
    h += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">';
    fs.forEach(function(f){
      h += '<span class="pill'+(f.recent?'':' hot')+'" title="'+esc(f.kb+' KB')+'">'
        + esc(f.name) + (f.recent? ' （发过）':'') + '</span>';
    });
    h += '</div>';
  } else {
    h += '<div class="hint" style="margin-top:10px">这个目录还是空的 —— '
      + '丢几个 <b>.gif</b> 进去就自动生效，不用改配置、也不用重启。'
      + '目录里那份 <b>说明.txt</b> 写了挑图建议。</div>';
  }
  h += '</div>';
  return h;
}

async function saveCfg(keys){
  var vals = {};
  keys.forEach(function(k){
    var el = document.getElementById("c_"+k); if(!el) return;
    var c = D.config[k]; var o = c ? c.value : "";
    var raw = el.value;
    if(typeof o === "boolean") vals[k] = (raw==="true");
    else if(typeof o === "number") vals[k] = Number(raw);
    else vals[k] = raw;
  });
  msg("cfgmsg","warn","保存中…",true);
  var r = await api("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:vals})});
  if(r.ok && (!r.failed || !r.failed.length)){ msg("cfgmsg","ok","已保存（备份 config.yaml.bak）"); await reload(); }
  else if(r.ok){ msg("cfgmsg","warn","部分保存成功，这几项没找到："+ (r.failed||[]).join("、")); await reload(); }
  else msg("cfgmsg","bad","失败："+(r.error||""));
}

/* ── 文本类面板 ── */
async function paneText(key){
  var f = D.files[key];
  document.getElementById("main").innerHTML =
    '<h2>'+esc(f.title)+'</h2><div class="sub">'+esc(f.hint)+'　·　'+esc(f.file)+'　·　'+f.chars+' 字</div>'+
    '<div class="card"><div class="sub" id="tload" style="margin:0"><span class="spin"></span>正在读 '+esc(f.file)+'…</div>'+
    '<textarea id="ta" class="hidden"></textarea>'+
    '<button onclick="saveText(\''+key+'\')">保存</button>'+
    '<button class="ghost" onclick="paneText(\''+key+'\')">重新载入</button>'+
    '<div class="msg" id="txtmsg"></div>'+
    '<div class="hint">保存前会自动备份成 '+esc(f.file)+'.bak。改动下一轮对话就生效（人设/世界观/你和我都是每轮现读的）。</div>'+
    '</div>';
  var r = await api("/api/text?name="+key);
  if(r.ok){
    var ta = document.getElementById("ta");
    ta.value = r.text;
    ta.classList.remove("hidden");
    document.getElementById("tload").classList.add("hidden");
  } else {
    document.getElementById("tload").innerHTML = '<span style="color:var(--bad)">读不到：'+esc(r.error)+'</span>';
  }
}
async function saveText(key){
  msg("txtmsg","warn","保存中…",true);
  var r = await api("/api/text",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:key,text:document.getElementById("ta").value})});
  if(r.ok){ msg("txtmsg","ok","已保存（"+r.chars+" 字，备份 "+r.backup+"）"); await reload(true); }
  else msg("txtmsg","bad","失败："+(r.error||""));
}

/* ── 记忆 ── */
function paneMemory(){
  var j = D.journal||{};
  var h = '<h2>记忆 <span class="tag">她会记住什么、忘掉什么</span></h2>';
  h += '<div class="sub">她会定期把你们的事复盘一遍，攒成长期记忆 —— 这样聊到第 300 轮，她还记得第 20 轮。'
    + '但复盘是<b>记忆</b>，不是<b>日记</b>：只留值得留的，别的就让它过去。</div>';
  if(j.error){ document.getElementById("main").innerHTML = h+'<div class="card"><div class="msg bad">'+esc(j.error)+'</div></div>'; return; }

  var keep = ((D.config["journal.keep_entries"]||{}).value);
  h += '<div class="card"><h3>复盘记什么 <span class="tag">只记这五类</span></h3>';
  h += '<div class="kv">';
  h += '<b>只记</b><span>① 关系的进展　② 关于他本人的信息　③ 你们之间的约定　④ 他的性子　⑤ 她还没弄明白的</span>';
  h += '<b>一律不写</b><span>吃了什么、他打了多久游戏、谁先说了晚安……（流水账）</span>';
  h += '<b>硬上限</b><span>最多 5 条 / 120 字（程序硬截：去重、剥掉符号、超了在句读处断，模型想多写也写不进来）</span>';
  h += '<b>没什么可记</b><span>整段留空 —— <b>不落这条复盘</b>，也绝不拿「长期记忆」顶上去凑数</span>';
  h += '<b>历次复盘</b><span>最多留 '+ (keep==null?24:keep) +' 条，老的那些交回长期记忆吸收掉</span>';
  h += '</div>';
  h += '<div class="hint">为什么要这样收：不收的话「聊个几百轮 token 直接爆炸」。'
    + '模拟人的记忆 —— 关系、承诺、他是什么人留下；琐事忘掉。<br>'
    + '黑框里也能看：<code>python journal.py --show</code>；想忘掉旧账：<code>python journal.py --forget</code>（＝下面那个按钮）。</div>';
  h += '</div>';

  h += '<div class="card"><h3>长期记忆 <span class="tag">她心里对他的总账</span></h3>';
  h += '<pre class="core">'+esc(j.core||"（还没攒出来。聊够 "+j.every_turns+" 轮，或者点下面的按钮，让她现在就记一次。）")+'</pre>';
  h += '<button onclick="sumNow()">现在复盘一次</button>';
  h += '<span class="msg" id="jmsg"></span>';
  h += '<div class="hint">每 '+j.every_turns+' 轮、或隔 '+j.every_hours+' 小时自动复盘一次（<b>复盘频率</b>在「语音与节奏」页改）。'
    + '手动复盘要调一次模型，等十几秒。</div>';
  h += '</div>';

  var es = j.entries||[];
  h += '<div class="card"><h3>历次复盘 <span class="tag">攒过 '+ (j.count||0) +' 次 · 现在留着 '+es.length+' 条</span></h3>';
  if(!es.length){
    h += '<div class="hint">还没有。一段里没有值得记的五类事，就不会落这条 —— 这是对的，不是坏了。</div>';
  }
  es.slice(0,40).forEach(function(e){
    var n = plain(e.text).length;
    var over = n>120;
    h += '<div class="entry"><b>'+esc(e.time)+' · 第 '+esc(e.at_turn)+' 轮 · '
      + '<span style="color:'+(over?'var(--bad)':'var(--dim)')+'">'+n+' 字'+(over?'（超标，程序会截断）':'')+'</span></b>'
      + '<p>'+esc(e.text).replace(/\n/g,"<br>")+'</p></div>';
  });
  if(es.length>40) h += '<div class="hint">（只显示最近 40 条，全部内容看 记忆\\备忘录.md）</div>';
  h += '<div class="row" style="align-items:flex-start;margin-top:14px">';
  h += '<button class="ghost" onclick="forgetNow()">把旧流水账忘掉</button>';
  h += '<span class="msg" id="fmsg"></span></div>';
  h += '<div class="hint">「忘掉」只清<b>历次复盘</b>这一摞 —— <b>长期记忆（他是什么人）一个字不动</b>。'
    + '改规矩之前攒下的那几篇是按老标准写的（一句一行，等于把对话抄了一遍），用这个按钮一句忘掉，不用去开黑框。'
    + '点之前会自动备份一份 <b>总结.json.bak-忘复盘-…</b>。</div>';
  h += '</div>';

  var jkeys = ["journal.enabled","journal.every_turns","journal.every_hours",
               "journal.min_turns","journal.core_chars","journal.keep_entries"];
  h += '<div class="card"><h3>调它 <span class="tag">改完重启生效</span></h3>';
  h += cfgFieldsHtml(jkeys);
  h += '<button onclick="saveCfg('+JSON.stringify(jkeys).replace(/"/g,"&quot;")+')">保存</button>';
  h += '<div class="msg" id="cfgmsg"></div>';
  h += '<div class="hint"><b>历次复盘最多留几条</b>：留多了白占 token（最近几条会进每轮提示）。'
    + '<b>长期记忆字数上限</b>：超了模型自己挑重点。<b>少于多少轮不记</b>：免得把寒暄也记下来。</div>';
  h += '</div>';

  document.getElementById("main").innerHTML = h;
}
function plain(s){ return String(s==null?"":s).replace(/\s/g,""); }
async function forgetNow(){
  var es = (D.journal&&D.journal.entries)||[];
  if(!es.length){ msg("fmsg","warn","本来就没有历次复盘，不用忘。"); return; }
  if(!confirm("确定要忘掉这 "+es.length+" 条历次复盘吗？\n\n· 长期记忆（他是什么人）一个字都不会动\n· 点之前会自动备份一份 JSON，点错了还能捞回来")) return;
  msg("fmsg","warn","正在忘…",true);
  var r = await api("/api/journal/forget",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
  if(r.ok){ D.journal = r.journal; paneMemory(); msg("fmsg","ok", r.note||("忘掉了 "+(r.forgot||0)+" 条。")); }
  else msg("fmsg","bad","失败："+(r.error||""));
}
async function sumNow(){
  msg("jmsg","warn","她在复盘，调一次模型，可能要十几秒…",true);
  var r = await api("/api/journal/summarize",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
  if(r.ok){ D.journal = r.journal; paneMemory(); msg("jmsg","ok","记下来了。"); }
  else msg("jmsg","bad","失败："+(r.error||""));
}

/* ── 她的日子 ── */
function paneLife(){
  var l = D.life||{};
  var h = '<h2>她的日子</h2><div class="sub">平行生活是怎么摇出来的：每天生成一份日程，跟着真实时间往前走。她的作息是照虚质科学部课表来的。</div>';
  if(l.error){ document.getElementById("main").innerHTML = h+'<div class="card"><div class="msg bad">'+esc(l.error)+'</div></div>'; return; }

  h += '<div class="card"><h3>今天 <span class="tag">'+esc(l.date)+'　'+(l.school_day?"有课":"没课")+'</span></h3>';
  h += '<div class="kv"><b>天气</b><span>'+esc(l.weather)+'</span><b>状态</b><span>'+esc(l.mood)+'，精力 '+l.energy+'/100</span>';
  h += '<b>此刻在</b><span>'+esc(l.now_doing||"——")
    + (l.busy? '　<span class="pill hot">走不开</span>'
             : '　<span class="pill">腾得出手</span>')+'</span>';
  if(l.quirk) h += '<b>小插曲</b><span>'+esc(l.quirk)+'</span>';
  if(l.rare) h += '<b>少见的事</b><span>'+esc(l.rare)+'</span>';
  h += '</div>';
  h += '<div class="hint">「走不开」＝ 在上课 / 睡着了。这时候她<b>不会主动来找你</b>'
    + '（就是「主动消息」页那道第 ④ 号闸）；但你发消息来她照回。<br>'
    + '另外，今天那些随机小事的<b>时间不会落在上课段里</b> —— 免得出现'
    + '「自检说她在上课、消息却说刚在电梯碰到人」那种打架。</div>';
  h += '<button onclick="refreshLife()">重摇今天</button><span class="msg" id="lifemsg"></span>';
  h += '</div>';

  h += '<div class="card"><h3>今天这一天 <span class="tag">和固定课表不一样的地方</span></h3>';
  h += '<div class="kv"><b>偏移</b><span>'+esc(l.day_plan||"（照基准作息，今天没有偏移）")+'</span></div>';
  if((l.day_edits||[]).length){
    h += '<ul class="tl">';
    l.day_edits.forEach(function(e){
      h += '<li><i>'+esc(e.from)+'–'+esc(e.to)+'</i>'+esc(e.text)+'</li>';
    });
    h += '</ul>';
  }
  h += '<div class="hint">每天排一份（起晚了 / 差点迟到 / 失眠 / 起得早）。'+
       'config.yaml 里 <code>life.day_plan.source</code> 选 auto 就是「一天开始问一次 API，失败本地现算」；'+
       '选 local 就永远不请求。</div>';
  if((l.appoints||[]).length){
    h += '<h3 style="margin-top:12px">他定的约 <span class="tag">每天重排日程也删不掉</span></h3>';
    h += '<ul class="tl">';
    l.appoints.forEach(function(e){
      h += '<li class="hot"><i>'+esc(e.from)+'–'+esc(e.to)+'</i>'+esc(e.text)+
           '<span class="hint">　起因：'+esc(e.raw)+'　（取消：约定.py --drop '+esc(String(e.id))+'）</span></li>';
    });
    h += '</ul>';
    h += '<div class="hint">他说好的事存在 生活/约定.json 里，跟她的今天.json 不是一个文件 —— '+
         '所以「重摇今天」、每天那份新日程都动不了它。只有你自己能取消。</div>';
  }
  h += '</div>';

  h += '<div class="card"><h3>今天到现在发生过的事</h3>';
  if(!(l.events||[]).length) h += '<div class="hint">（今天还没摇出什么来）</div>';
  else {
    h += '<ul class="tl">';
    l.events.forEach(function(e){
      h += '<li class="'+(e.talk>=2?"hot":"")+'"><i>'+esc(e.time)+' '+esc(e.period)+'</i>'+esc(e.text)+'</li>';
    });
    h += '</ul>';
    h += '<div class="hint">粉色点 = 她比较想跟你聊的（主动找你时会优先挑这些）。</div>';
  }
  h += '</div>';

  h += '<div class="card"><h3>她的作息</h3><ul class="tl">';
  (l.plan||[]).forEach(function(p){
    h += '<li><i>'+esc(p.from)+'–'+esc(p.to)+'</i>'+esc(p.text)+'</li>';
  });
  h += '</ul><div class="hint">作息跟着课表走：周一到周六上课，周日睡到中午。所以她凌晨三点不会说「等下要去吃早饭」。</div></div>';

  h += '<div class="card"><h3>素材库 <span class="tag">随机事件发生器用的就是这些</span></h3>';
  var sk = l.stock||{};
  var left = (sk.left==null? l.pool_total : sk.left), total = sk.total||l.pool_total;
  if(sk.left!=null && sk.left<=0){
    h += '<div class="msg bad">素材全部用完了 —— 她只剩作息和大事可以聊。往 素材.py 里加料吧。</div>';
  } else if(sk.left!=null && sk.left<100){
    h += '<div class="msg warn">素材快见底了（剩 '+sk.left+' 条），建议往 素材.py 里加料。</div>';
  }
  h += '<div class="kv"><b>剩余</b><span><b>'+left+'</b> / '+total+' 条（已用 '+(sk.used||0)+'）</span>';
  h += '<b>小插曲</b><span>剩 '+(sk.quirks==null?l.quirks:sk.quirks)+' / '+l.quirks+' 条</span>';
  h += '<b>少见的事</b><span>剩 '+(sk.rares==null?l.rares:sk.rares)+' / '+l.rares+' 条</span>';
  h += '<b>摇到少见事的概率</b><span>每天 '+Math.round((D.config["life.rare_chance"]||{}).value*100)+'%</span></div>';
  h += '<div class="hint" style="margin-top:0">★ 素材是消耗品：用掉一条就永久划掉，不会再出现第二次。'+
       '所以直接看下面的剩余数，别看总数。</div>';
  h += '<div class="hint">按每天大约 7 条算，还剩的料够 ';
  h += (left>0 ? Math.round(left/7) : 0);
  h += ' 天左右。</div>';
  h += '<div class="row" style="align-items:flex-end"><div style="width:220px"><label>看某个时段还剩哪些料</label>';
  h += '<select id="poolsel" onchange="loadPool()">';
  [["dawn","清晨"],["morning","上午"],["noon","中午"],["afternoon","下午"],["dusk","傍晚"],["evening","晚上"],["night","深夜"]].forEach(function(p){
    var n = (sk.pool||{})[p[0]];
    if(n==null) n = (l.pool||{})[p[0]]||0;
    h += '<option value="'+p[0]+'">'+p[1]+'（剩 '+n+'）</option>';
  });
  h += '</select></div></div><div id="poolbox"></div>';
  h += '<div class="hint">想加自己的料：打开 <b>素材.py</b>，往对应列表里塞 (\"文本\", 话题度) 就行，格式一模一样。</div>';
  h += '</div>';

  if(l.nudge) h += '<div class="card"><h3>她这会儿可能想跟你说的</h3><p style="margin:0">'+esc(l.nudge)+'</p></div>';

  document.getElementById("main").innerHTML = h;
  loadPool();
}
async function loadPool(){
  var sel = document.getElementById("poolsel"); if(!sel) return;
  var box = document.getElementById("poolbox");
  box.innerHTML = '<div class="msg warn"><span class="spin"></span>读事件池…</div>';
  var r = await api("/api/pool?period="+sel.value);
  if(!r.ok){ box.innerHTML = '<div class="msg bad">'+esc(r.error)+'</div>'; return; }
  box.innerHTML = '<ul class="tl">'+ r.items.map(function(it){
    return '<li class="'+(it.talk>=2?"hot":"")+'"><i>'+it.talk+'</i>'+esc(it.text)+'</li>';
  }).join("") + '</ul><div class="hint">'+r.name+'：还剩 '+r.items.length+' 条，已经用掉 '+(r.used||0)+' 条（用掉的不会再出现）。前面的数字是话题度：2 = 她挺想说的。</div>';
}
async function refreshLife(){
  msg("lifemsg","warn","重摇中…",true);
  var r = await api("/api/life/refresh",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
  if(r.ok){ await reload(true); } else msg("lifemsg","bad","失败："+(r.error||""));
}

/* ── 大事 ── */
function paneStory(){
  var s = D.story||{};
  var h = '<h2>大事</h2><div class="sub">日常素材是「今天一件小事」，说完就过去了。大事是连着演好几天的事 —— 比如她说这周要考试：今天提考试，明天复习，后天考，大后天出成绩。她会连着几天惦记同一件事。</div>';
  if(s.error){ document.getElementById("main").innerHTML = h+'<div class="card"><div class="msg bad">'+esc(s.error)+'</div></div>'; return; }
  var act = s.active||[];
  h += '<div class="card"><h3>正在演 <span class="tag">'+act.length+' / '+(s.max_active||2)+' 条</span></h3>';
  if(!act.length) h += '<div class="hint">现在没在演 —— 到日子会自己开一条。</div>';
  act.forEach(function(a){
    h += '<div style="border-left:2px solid var(--accent);padding-left:12px;margin:14px 0">';
    h += '<div class="row"><div><b>'+esc(a.name)+'</b> <span class="tag">第 '+a.day+' / '+a.day_total+' 天</span> <span class="tag">阶段 '+a.stage+'/'+a.stage_total+'</span></div>';
    h += '<button class="ghost" onclick="storyDo(\'stop\',\''+esc(a.id)+'\')">停掉</button></div>';
    h += '<div class="hint">'+esc(a.hint)+'</div>';
    (a.history||[]).forEach(function(x){
      h += '<div class="entry"><b>'+esc(x.date)+'</b><p>'+esc(x.text)+'</p></div>';
    });
    h += '<div class="entry" style="border-color:var(--accent)"><b>现在</b><p>'+esc(a.current)+'</p></div>';
    h += '</div>';
  });
  h += '</div>';
  h += '<div class="card"><h3>全部剧本 <span class="tag">共 '+(s.total||0)+' 条</span></h3>';
  h += '<div class="hint">点「开演」可以立刻让它上场（预览用）。演完的不会重演。</div>';
  (s.catalog||[]).forEach(function(c){
    var badge = c.state==="active"
      ? '<span class="tag" style="border-color:var(--accent);color:var(--accent)">正在演</span>'
      : (c.state==="done" ? '<span class="tag">已演完</span>' : '');
    h += '<div class="row" style="align-items:flex-start;margin:10px 0;border-bottom:1px solid var(--line);padding-bottom:8px">';
    h += '<div style="flex:1"><b>'+esc(c.name)+'</b> '+badge;
    h += '<div class="hint" style="margin-top:2px">'+esc(c.hint)+'</div>';
    h += '<div class="hint">'+c.days+' 天 / '+c.stages+' 个阶段'+(c.branches?('，'+c.branches+' 处有多种走向'):'')+'</div></div>';
    if(c.state!=="active") h += '<button class="ghost" onclick="storyDo(\'start\',\''+esc(c.id)+'\')">开演</button>';
    h += '</div>';
  });
  h += '<div class="row" style="margin-top:14px"><button class="ghost" onclick="storyDo(\'reset\')">全部重来（清空进度）</button>';
  h += '<span class="msg" id="storymsg"></span></div>';
  h += '</div>';
  document.getElementById("main").innerHTML = h;
}
async function storyDo(act, id){
  var body = id ? JSON.stringify({id:id}) : "{}";
  var r = await api("/api/story/"+act,{method:"POST",headers:{"Content-Type":"application/json"},body:body});
  if(r.ok){ D.story = r.story; paneStory(); }
  else { var el=document.getElementById("storymsg"); if(el){ el.className="msg bad"; el.textContent=(r.error||"失败"); } }
}
async function setRel(){
  var v = Number(document.getElementById("relv").value);
  var r = await api("/api/relation/set",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({intimacy:v})});
  if(r.ok){ D.relation = r.relation; paneOverview(); msg("relmsg","ok","改好了。"); }
  else msg("relmsg","bad","失败："+(r.error||""));
}

function msg(id, cls, text, spin){
  var el = document.getElementById(id); if(!el) return;
  el.className = "msg "+cls;
  el.innerHTML = (spin?'<span class="spin"></span>':'') + esc(text);
}

async function reload(keepPane){
  var r = await api("/api/all");
  if(!r.ok){ document.getElementById("main").innerHTML='<div class="card"><div class="msg bad">读数据失败：'+esc(r.error)+'</div></div>'; return; }
  D = r;
  if(!keepPane) render(); else render();
}

nav(); reload();
window.addEventListener("hashchange",function(){ cur=location.hash.slice(1)||"overview"; nav(); render(); });
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════════════════════

def _pick_port(want: int) -> int:
    for p in range(want, want + 20):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            continue
        finally:
            try:
                s.close()
            except Exception:
                pass
    return want


def main():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]
    want = 7788
    if "--port" in args:
        try:
            want = int(args[args.index("--port") + 1])
        except Exception:
            pass
    open_browser = "--no-browser" not in args

    port = _pick_port(want)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d" % port

    print("=" * 58)
    print("达妮娅 · 控制台")
    print("=" * 58)
    print("  地址 : %s" % url)
    print("  目录 : %s" % ROOT)
    print()
    print("  只在你自己这台机器上跑（只监听 127.0.0.1），")
    print("  API Key 不会跑到别的地方去。")
    print()
    print("  关掉这个黑窗口 = 关掉控制台。")
    print("-" * 58)

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[OK] 已停止。")
    finally:
        try:
            srv.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
