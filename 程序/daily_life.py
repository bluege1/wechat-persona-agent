# -*- coding: utf-8 -*-
"""
平行生活引擎 —— 让达妮娅有「她那边」的一天。
============================================

问题：原来的她只会回应你。你一停手，她就静止了 —— 这正是「纯 AI 味」的来源。
她会秒回，但她没有日子可过。

做法：
  1. 每天随机给她生成一份日程：天气、心情、精力、上午/下午/晚上各几件事、
     一件小插曲、今天惦记的东西、偶尔一件很少见的事。随真实时间自动补齐（advance）。
  2. 她的**作息**是跟着课表走的（周一到周六上课，周日睡到中午）——
     所以凌晨三点她不会说「等下要去吃早饭」。
  3. 她还在**演一部连续剧**：由 大事.py 提供的大事件（考试周、比赛、复查……），
     按真实日期推进，一天一天地演。所以她会连着几天惦记同一件事。
  4. 每次对话把「她那边现在几点、天气、正在干什么、今天已经发生了什么、
     这几天在闹什么大事」塞进系统提示 —— 她自然就有话可说。
  5. 给「主动发消息」提供话题种子（nudge）：优先挑正在演的大事，
     其次是今天还没跟你提过、又有点说头的事，而不是干巴巴的「在吗」。

⚠ 素材是消耗品
  用掉一条就永久划掉，以后再也不会出现第二次。账本在 生活/用过的.json。
  池子见 素材.py（556 条），剧情见 大事.py（18 条）。
  剩多少用 `python daily_life.py --stock` 看。空了就是空了 —— 少一件事，不会回收。

文件：
  生活/今天.json     当天状态（自动生成、自动更新，不用手改）
  生活/用过的.json   消耗账本（永久，别手删，删了素材就会重复）
  生活/剧情.json     正在演的大事件（由 大事.py 管）
  生活/日计划.json   今天这一天的偏移（起晚了 / 差点迟到 / 失眠…，见 日计划.py）
  生活/约定.json     他定的约（「晚上六点去和西西吃饭」），见 约定.py

命令行：
  python daily_life.py                    看看她今天过着什么日子
  python daily_life.py --stock            看素材还剩多少（最重要的一条）
  python daily_life.py --refresh          重摇今天（会把今天用掉的素材退回池子）
  python daily_life.py --nudge 5          抽 5 个「她想跟你说的事」看看样本
  python daily_life.py --pool morning     看「上午」这个时段还剩哪些料
  python daily_life.py --plan             看她的作息表（带「现在」和临时改动）
  python daily_life.py --self             她自己捋一遍：现在在哪、接下来两小时干嘛
  python daily_life.py --day              看她今天这一天有什么「偏移」
  python daily_life.py --divert 12:00-14:00 陪他打游戏
                                          手动替她改一段计划
  python daily_life.py --undo             撤掉今天所有临时改的计划
"""

from pathlib import Path
import datetime as dt
import json
import random
import sys

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 素材库与剧情线在角色包里（角色\<当前>\素材.py 等），**按当前角色加载**。
# 这里拿到的是惰性代理：`素材.POOL` 每次都去问「现在是谁」——
# 所以换角色之后自动指向新角色那三个文件，不会演出上一个人的剧本。
from 角色 import 大事, 素材, 日计划 as dayplan, 作息   # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
#  时段
# ══════════════════════════════════════════════════════════════════════════

PERIODS = [
    ("dawn",      5,  7,  "清晨"),
    ("morning",   7,  11, "上午"),
    ("noon",      11, 14, "中午"),
    ("afternoon", 14, 17, "下午"),
    ("dusk",      17, 19, "傍晚"),
    ("evening",   19, 23, "晚上"),
    ("night",     23, 29, "深夜"),     # 跨 0 点，29 = 次日 5 点
]


def period_of(hour: int) -> str:
    h = hour if hour >= 5 else hour + 24
    for key, a, b, _ in PERIODS:
        if a <= h < b:
            return key
    return "night"


def period_name(key: str) -> str:
    for k, _, _, name in PERIODS:
        if k == key:
            return name
    return "现在"


PERIOD_ORDER = [p[0] for p in PERIODS]


# ══════════════════════════════════════════════════════════════════════════
#  她的作息 —— 照着虚质科学部的课表来的
#
#  课表（周一到周六上课，周日休）：
#      08:00-09:40  1-2 节      10:00-11:40  3-4 节
#      11:40-14:00  午休        14:00-15:40  5-6 节
#      16:00-17:40  7-8 节      17:40-19:00  晚休
#      19:00-20:40  9-10 节     21:00-21:45  11 节
#
#  ★ 2026-09-22 重写，从「一段二十到一百分钟的粗块」改成**分到分钟**。
#
#  为什么改：08:24 的提示里原本写着
#       已经过完  07:30 刚醒，洗脸，跟床讨价还价；07:50 赶去教学区
#     她张嘴就是「我这边刚起 困死了」—— 可她 08:00 就该在教室了。
#     粗块有两个病：
#       ① 一段里糊了三个动作（起床 + 洗脸 + 讨价还价），没法回答
#          「你现在在干嘛」这种精确问题；
#       ② 段里的字会被当成**现在**：「07:30 刚醒」离她只有 54 分钟，
#          而「刚醒」这个词本身自带「就是刚才」的味道，模型就照着念了。
#
#  现在每一段都短（起床 2 分钟、刷牙 8 分钟、换衣服 7 分钟……），
#  并且配上它自己的起止时间。她读到的就是「07:32-07:40 在刷牙」
#  这种**能直接当台词说出口**的事实 —— 07:35 你发「早」，
#  她顺口就能回「早 我在刷牙」。
#
#  格式：(适用日, 起, 止, 文本, 话题度, 里程碑)
#      适用日  "wd"=周一到周六 / "sun"=周日
#      talk   0=不用提  1=可以聊  2=她挺想说
#      key    True = 这一天里的关键节点（起床 / 出门 / 到教室 / 下课 /
#             吃饭 / 洗澡 / 睡）。提示里「今天到这会儿你已经过完了」
#             和「再往后」只挑这些说 —— 不然一分钟一段会把
#             「我到底几点起的床」淹在几十行里。
# ══════════════════════════════════════════════════════════════════════════

# ── 周一到周六（有课）──
# ★ 作息表已经搬到角色包里：角色\<当前>\作息.py
#   —— 每个人过的一天不一样，所以它是**角色数据**，不是代码。
#   这里读的是 作息.ROUTINE，换角色自动换表。


# 睡觉那几段（写成一个集合，好认）
# SLEEPY 也搬去 作息.py 了（睡觉那几段因角色而异）


# ══════════════════════════════════════════════════════════════════════════
#  「她此刻腾不出手」——主动消息别跟她的日子打架（2026-09-22）
# ══════════════════════════════════════════════════════════════════════════
#
#  用户实测的那一次：
#      10:28  自检：她现在正在：在上课（3-4 节）：老师在台上念 PPT
#      10:28  她主动发来：「电梯里碰到一群新生／都盯着我看 我只好戴上帽子」
#     —— 两句话在同一个屏幕上打架。
#
#  查了今天.json：那条素材的时间戳是 **10:26**，正好落在 10:00-10:40
#  的课里。所以根因不是模型，是**素材的时间戳是随机撒的**：
#  _push() 在「上午」这一段的可用范围里随便挑一分钟，压根不看
#  她那个点在不在上课。撒进课里，她说什么都圆不回来。
#
#  两条修法：
#    ① 排素材时间时把**上课**那几段挖掉（free_ranges）——
#       素材只往课间 / 午休 / 晚休 / 睡前那些「手是自由的」时候排；
#    ② 她此刻要是在上课，就**不主动开口**（proactive.quiet_when_busy）——
#       真人上课时不会先找人聊天。回消息那一路不受影响。
#
#  ⚠ 只挖「上课」，**不挖睡觉**。深夜/凌晨那几条素材
#    （「半夜把今天的照片整理了一下」）本来就发生在她躺着的时候，
#    一起挖掉的话 night/dawn 两个池子直接作废。
# CLASS_MARKS 也搬去 作息.py 了（不是每个角色都有课）


def is_class_text(text: str) -> bool:
    """这一段她是不是在上课。（上课时手在桌子底下，但没法跟人好好聊。）"""
    t = (text or "").strip()
    return any(m in t for m in 作息.CLASS_MARKS)


def is_busy_text(text: str) -> bool:
    """此刻她是不是腾不出手 —— 上课 或者 睡着。主动消息靠它决定开不开口。"""
    t = (text or "").strip()
    return is_class_text(t) or (t in 作息.SLEEPY)


def free_ranges(lo: int, hi: int) -> list:
    """[lo, hi] 里「她的手是自由的」那几段。返回闭区间 [(起, 止), ...]。

    分钟数，只把**上课**那几段挖掉（睡觉段故意留着，见上面那段注释）。
    """
    busy = sorted((a, b) for a, b, t, _k, _m in _routine_today()
                  if is_class_text(t))
    out, cur = [], lo
    for a, b in busy:
        if b <= lo or a > hi:
            continue
        if a > cur:
            out.append((cur, min(a - 1, hi)))
        cur = max(cur, b)
        if cur > hi:
            break
    if cur <= hi:
        out.append((cur, hi))
    return [(a, b) for a, b in out if b >= a]


# ══════════════════════════════════════════════════════════════════════════
#  今天的偏移（见 日计划.py）
#
#  固定课表 ROUTINE 是底子；每天的「起晚了 / 差点迟到 / 失眠」是偏移。
#  偏移由 日计划.py 生成（本地现算 / 每天开始那次 API），这里是**运行时的一份**。
#
#  ★ 模块级缓存，默认**为空** —— 空的就等于「照基准作息」。
#    这是故意的：`查错.py` 里那些「8:24 = 早课」「7:35 = 刷牙」的断言
#    走的都是不带偏移的基准作息，必须可复现、不受当天摇到什么影响。
#    只有 `Life`（真在跑的那条路）才会把今天那份压上来。
# ══════════════════════════════════════════════════════════════════════════

_ACTIVE_PLAN = {}


def set_active_plan(plan):
    """把今天这份偏移压到模块级的作息上（传 {} / None = 退回基准作息）。"""
    global _ACTIVE_PLAN
    _ACTIVE_PLAN = dict(plan) if plan else {}


def active_plan() -> dict:
    return dict(_ACTIVE_PLAN)


def _is_school_day(weekday: int) -> bool:
    """weekday: 0=周一 … 6=周日。学院周一到周六都有课。"""
    return weekday != 6


# ══════════════════════════════════════════════════════════════════════════
#  「今天是哪种日子」—— 一个口径（2026-10-04 收口）
# ══════════════════════════════════════════════════════════════════════════
#
#  以前 routine_now 和 _base_routine_today 各算一遍
#  `dt.datetime.now().weekday()`。两处看着一样，但只要有一处被自检钉住，
#  另一处还在按真实日期走，两边就会对不上 —— **周日跑自检必然踩到**：
#
#      查错.py 第 30 组为了「上课段一定存在」，把作息钉成周一到周六那一份；
#      于是素材按 wd 表排在 13:27（那儿是「起来洗把脸」＝手是自由的），
#      断言却拿 routine_now 去查 13:27 —— 它按真实日期走 sun 表，
#      那儿是「去教学区自习室写作业」＝上课 → **假红**。
#
#  2026-10-04（周日）实测：西格莉卡红一条
#      「素材又撒进课里了：[('13:27', '晒了会儿赫利俄斯的光…', '去教学区自习室写作业')]」
#  达妮娅 / 爱弥斯 / 守岸人没红，只是她们的 sun 表 13:27 那段
#  不在 CLASS_MARKS 里 —— 同一个毛病，撞上了才看得见。
#
#  修法：口径收成一个函数。自检改这一个开关，不再各改各的。
_DAY_KIND = None        # None = 跟真实日期走；"wd" / "sun" = 被钉住


def day_kind() -> str:
    """今天是哪种日子：`"wd"`（周一到周六有课）/ `"sun"`（周日没课）。"""
    if _DAY_KIND in ("wd", "sun"):
        return _DAY_KIND
    return "wd" if _is_school_day(dt.datetime.now().weekday()) else "sun"


def set_day_kind(kind):
    """把「今天是哪种日子」钉住（自检用）。传 None 恢复跟真实日期走。"""
    global _DAY_KIND
    _DAY_KIND = kind if kind in ("wd", "sun") else None


def routine_now(now_min: int):
    """按作息表算「她现在多半在干嘛」。返回 (文本, talk) 或 (None, 0)。

    凌晨（0~5 点）直接返回「睡着」。
    """
    today = day_kind()
    m = now_min if now_min >= 5 * 60 else now_min + 24 * 60
    best = None
    for who, a, b, text, talk, _key in 作息.ROUTINE:
        if who != today:
            continue
        if a <= m < b:
            best = (text, talk)
    return best if best else (None, 0)


def _base_routine_today() -> list:
    """**不带偏移**的基准作息（周几决定 wd / sun）。

    给 日计划.py 当「底子」用 —— 生成偏移时要知道原本长什么样。
    """
    today = day_kind()
    return [(a, b, text, talk, key)
            for who, a, b, text, talk, key in 作息.ROUTINE if who == today]


def _routine_today() -> list:
    """今天真正生效的作息（基准 + 今天的偏移）。"""
    return dayplan.apply(_base_routine_today(), _ACTIVE_PLAN)


def wake_min_today() -> int:
    """今天她几点起的床 —— 作息表里上午第一段「不是睡觉」的开始时刻。

    07:30（周一到周六）/ 08:40（周日）。用来算「她已经醒着多久了」，
    那条数字专门治「8:24 还说刚起」。
    """
    for a, b, text, _talk, _key in _routine_today():
        if text not in 作息.SLEEPY and b <= 12 * 60:
            return a
    return 7 * 60 + 30


def awake_minutes(now_min: int) -> int:
    """她今天醒着多少分钟了。算不出（凌晨半夜）返回 -1。

    凌晨那段没法算 —— 她 23:40 睡下、07:30 才起，
    01:00 的时候「醒着 17 小时」是句真话但没用，不如不报。
    """
    if now_min < 5 * 60 or now_min >= 24 * 60:
        return -1
    w = wake_min_today()
    if now_min < w:
        return -1
    return now_min - w


def routine_plan() -> list:
    """今天会发生的作息段（去掉纯睡觉的），给 context() 用。"""
    return [(a, b, t) for a, b, t, _talk, _key in _routine_today()
            if t not in 作息.SLEEPY]


def routine_keys(now_min: int, diverts=None) -> list:
    """只挑「里程碑」段 —— 起床 / 出门 / 到教室 / 吃饭 / 洗澡 / 睡。

    返回 [(起, 文本, 状态)]，状态 done / now / next，按时间排。

    提示里「你已经过完了」和「再往后」只报这些，一分钟一段的小事
    不往里塞 —— 不然「我几点起的床」会被淹掉，
    而那正是最要紧的一条（8:24 还说「刚起」就是它没送到）。
    """
    m = _day_min(now_min)
    segs = _routine_today()
    if diverts:
        segs = _apply_diverts(segs, diverts)
    out = []
    for a, b, text, _talk, key in segs:
        if not key:
            continue
        state = "done" if b <= m else ("now" if a <= m < b else "next")
        out.append((a, text, state))
    out.sort(key=lambda x: x[0])
    return out


# 兼容旧名字（老代码 / 老断言可能还叫它）
_routine_plan = routine_plan


# ══════════════════════════════════════════════════════════════════════════
#  临时改计划 —— 他的提议把她的日程改掉了
#
#  「她十二点本来要去吃饭，可他叫她去玩游戏，或者他提了个更有意思的计划」——
#  这种时候她的日程**真的会变**，不能嘴上答应了、日程里还在吃饭。
#
#  做法：把改动存成一段「覆盖」，压在原作息上面（见 _apply_diverts）。
#  改动的来源写在 state["diverts"] 里，谁都能读、能查。
# ══════════════════════════════════════════════════════════════════════════

def _apply_diverts(segs: list, diverts: list) -> list:
    """把「临时改的计划」压到原作息上。

    segs    : [(起, 止, 文本, talk, key)] —— 原本的作息（已分到分钟）
    diverts : [(起, 止, 文本)]           —— 改掉的那几段
    返回新的 [(起, 止, 文本, talk, key)]。

    被压住的原段其实是**被切掉**的：左半截（到改动开始为止）和右半截
    （改动结束之后）都留着，中间那段换成新的。所以「午休 11:40-14:00」
    被他 12:00 叫走两小时，会变成 11:40-12:00 午休 + 12:00-14:00 陪你 +
    14:00-15:40 上课 —— 读起来是一条完整的一天。

    ★ 改动段自己算一个里程碑（key=True）：他约她做的事，她当然记得住，
      而且「今天到这会儿」那行要能把它列出来。
    """
    out = list(segs)
    for da, db, dtext in diverts:
        if db <= da:
            continue
        new = []
        for a, b, t, talk, key in out:
            if b <= da or a >= db:          # 跟这段改动完全不搭界
                new.append((a, b, t, talk, key))
                continue
            if a < da:                      # 左边留半截
                new.append((a, da, t, talk, key))
            if b > db:                      # 右边留半截
                new.append((db, b, t, talk, key))
        # 改出来的这一段她当然更想提，talk 给满，并且当里程碑
        new.append((da, db, dtext, 2, True))
        out = new
    out.sort(key=lambda x: x[0])
    return out


def _hhmm(m: int) -> str:
    """分钟数 → HH:MM（跨 0 点的按 24h 折回来）。"""
    return "%02d:%02d" % (m // 60 % 24, m % 60)


def _day_min(now_min: int) -> int:
    """把「此刻」摆到一条不断开的轴上 —— 凌晨 0~5 点算「昨天那一天的延续」。

    ★ 2026-10-05 加。为什么非要有它：
      她 23:40 睡下、凌晨 00:01 收到消息。这时 now_min=1，
      而昨晚那几段里程碑的钟点是 19:40 / 23:40（= 1180 / 1420 分钟）。
      「现在 - 那段」= 1 - 1420 = 负数 → 各处一律 `max(0, …)` 兜成 0，
      于是控制台和提示词里都写着「19:40 在宿舍写日记（0 分钟前）」——
      **「0 分钟前」就是「刚刚」**。她照着这几行回话，就把昨晚的事说成正在发生
      （实测：半夜 00:01，她说「刚从天台下来 风好大…正准备去食堂拿点吃的」）。
      折算到 24 点之后接着数（00:01 → 1441），23:40 就是「21 分钟前」，对得上。
    """
    return now_min if now_min >= 5 * 60 else now_min + 24 * 60


def _daynight_txt(hour: int) -> str:
    """把当前钟点翻成一句「天色人话」，配时钟同步指标用。"""
    if 5 <= hour < 8:
        return "现在是清晨（天刚亮）。"
    if 8 <= hour < 11:
        return "现在是上午。"
    if 11 <= hour < 13:
        return "现在是中午。"
    if 13 <= hour < 17:
        return "现在是下午。"
    if 17 <= hour < 19:
        return "现在是傍晚（天在黑）。"
    if 19 <= hour < 23:
        return "现在是夜里（天黑了）。"
    return "现在是深夜（凌晨，外面天黑着，多数人睡了）。"


def _now_min() -> int:
    """现在几点几分（从 0 点算起的分钟数）。"""
    now = dt.datetime.now()
    return now.hour * 60 + now.minute


def routine_timeline(now_min: int, diverts=None) -> list:
    """今天的时间轴：每段带状态 —— 已经过完了 / 正在进行 / 接下来。

    返回 [(起, 止, 文本, "done"|"now"|"next")]。

    diverts 是「临时改的计划」[(起, 止, 文本)]，会被压在原作息上
    （见 _apply_diverts）。不传就按原作息算。

    ★ 这是「活人感」的命门。以前只给她「此刻多半在：早课」一句话，
      模型很容易把它读成**「我要去上早课」**——于是 8:39 说出「我刚爬下床」，
      接着又说「我上课要补觉」，时间线整个穿帮。
      现在把三个时态一起摆出来：几点起的床（已经发生）、现在正在哪一段（进行中）、
      后面还有什么（还没发生）。**有"已经过去"的东西，她才知道自己在几点。**

    ★ 2026-09-22 起作息表分到了分钟，所以这里的每一段都短
      （「07:32-07:40 刷牙」），而不再是「07:30-07:50 刚醒，洗脸，跟床讨价还价」。
      段越短，她越不可能把「过去的那一段」说成现在。
    """
    m = _day_min(now_min)
    segs = _routine_today()
    if diverts:
        segs = _apply_diverts(segs, diverts)
    out = []
    for a, b, text, _talk, _key in segs:
        # ★ 睡觉段**也留在轴里** —— 凌晨他发消息时，"你现在在睡"正是最关键的
        #   那条状态（被消息叫醒也要回，见 persona「他半夜发消息」）。
        state = "done" if b <= m else ("now" if a <= m < b else "next")
        out.append((a, b, text, state))
    out.sort(key=lambda x: x[0])
    return out


# 兜底（几乎用不到）
FALLBACK_NUDGE = [
    "刚才看到一个东西，想到你了",
]


# ══════════════════════════════════════════════════════════════════════════
#  引擎
# ══════════════════════════════════════════════════════════════════════════

class Life:
    """达妮娅的「她那边」。—— 一天一份，随真实时间自动推进。"""

    def __init__(self, cfg: dict = None):
        cfg = cfg or {}
        life_cfg = cfg.get("life", {}) if isinstance(cfg, dict) else {}
        self.enabled = bool(life_cfg.get("enabled", True))
        state_file = life_cfg.get("file", "生活/今天.json")
        self.state_path = Path(state_file)
        if not self.state_path.is_absolute():
            self.state_path = ROOT / self.state_path
        self.dir = self.state_path.parent
        self.used_path = self.dir / "用过的.json"       # 消耗账本（永久）
        self.legacy_path = self.dir / "最近.json"        # 旧的 21 天窗口，只读迁移
        # 他定的约就存在这儿（默认跟 今天.json 同目录 = 生活/约定.json）。
        # 从 config 能改指 —— `查错.py` 搭沙箱时靠它隔离，不碰正在用的那份。
        _ap = life_cfg.get("appoint_file", "")
        self.appoint_path = Path(_ap) if _ap else (self.dir / "约定.json")
        if not self.appoint_path.is_absolute():
            self.appoint_path = ROOT / self.appoint_path
        self.recap_max = int(life_cfg.get("recap_max", 6))
        self.rare_chance = float(life_cfg.get("rare_chance", 0.18))
        # 0 = 用过的素材永不复用（默认）；填天数则那么久之后可以重新用
        self.reuse_after_days = int(life_cfg.get("reuse_after_days", 0))

        # ── 今天的偏移（起晚了 / 差点迟到 / 失眠…）见 日计划.py ──
        #   enabled 默认 False：不开就是「照基准作息」，一行都不变
        #   （`查错.py` 那些按基准作息的断言、控制台只读页都靠这个默认）。
        #   真跑的时候在 config.yaml 里打开。
        dp_cfg = life_cfg.get("day_plan") or {}
        self.plan_enabled = bool(dp_cfg.get("enabled", False))
        self.plan_source = str(dp_cfg.get("source", "auto")).lower()   # auto|api|local
        # 「两小时原则」：一天排一份，但**喂给模型的只有接下来这么多小时**
        self.plan_horizon_hours = float(dp_cfg.get("horizon_hours", 2))
        self.plan = {}

        self._used_cache = None
        self.story = 大事.Story(cfg, base=ROOT)
        self.state = self._load()
        self.refresh_plan()

    # ── 今天的偏移 ────────────────────────────────────────────────────
    def refresh_plan(self) -> dict:
        """把今天那份偏移拿出来 —— **只读**：不写盘、不发请求。

        有落盘的那份就用它；没有就本地现算一份放内存里（免费、确定性）。
        """
        if not self.plan_enabled:
            set_active_plan({})
            self.plan = {}
            return self.plan
        iso = dt.date.today().isoformat()
        got = dayplan.load(self.dir, iso)
        if got is None:
            got = dayplan.local_plan(iso, dt.date.today().weekday())
        self.plan = got
        set_active_plan(got)
        return got

    def ensure_day_plan(self, chat=None, force: bool = False) -> dict:
        """一天开始时调一次：没有今天那份就生成（能请求就请求），落盘。

        ★ 整个程序里**只有这里会写 `生活/日计划.json`、只有这里会发那次请求**。
          `--self` / 控制台那些只读路径走的都是 `refresh_plan()`，
          永远不会请求、也不会动她的生活目录。
        """
        if not self.plan_enabled:
            return {}
        iso = dt.date.today().isoformat()
        if not force and dayplan.load(self.dir, iso) is not None:
            return self.refresh_plan()
        use_api = chat if (chat is not None and self.plan_source in ("auto", "api")) else None
        plan = dayplan.generate(iso, dt.date.today().weekday(),
                                base_segs=_base_routine_today(), chat=use_api,
                                dir_path=self.dir, force=True)
        self.plan = plan
        set_active_plan(plan)
        return plan

    def plan_line(self) -> str:
        """给自检 / 控制台看的一行。"""
        return dayplan.describe(self.plan) if self.plan else ""

    # ── 消耗账本 ─────────────────────────────────────────────────────
    def _ledger(self) -> dict:
        """{素材文本: 用掉的那天}。永久有效。"""
        if self._used_cache is None:
            led = {}
            if self.legacy_path.exists():          # 旧的按天窗口，并进来保持连续
                try:
                    for day, texts in json.loads(
                            self.legacy_path.read_text(encoding="utf-8")).items():
                        for t in (texts or []):
                            led.setdefault(t, day)
                except Exception:
                    pass
            if self.used_path.exists():
                try:
                    d = json.loads(self.used_path.read_text(encoding="utf-8"))
                    raw = d.get("used") if isinstance(d, dict) else d
                    if isinstance(raw, dict):
                        led.update(raw)
                    elif isinstance(raw, list):     # 早期版本：只有列表，没有日期
                        for t in raw:
                            led.setdefault(t, dt.date.today().isoformat())
                except Exception:
                    pass
            self._used_cache = led
        return self._used_cache

    def _blocked(self) -> set:
        """现在还不许再用的素材。

        默认（reuse_after_days = 0）就是**全部**用过的 —— 永不复用。
        如果你哪天把 life.reuse_after_days 改成比如 120，
        那 120 天前用过的素材就重新回到池子里（默认不需要这么做）。
        """
        led = self._ledger()
        if self.reuse_after_days <= 0:
            return set(led)
        cut = (dt.date.today() - dt.timedelta(days=self.reuse_after_days)).isoformat()
        return set(t for t, d in led.items() if d > cut)

    def _flush_used(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            led = self._ledger()
            self.used_path.write_text(
                json.dumps({"count": len(led), "used": led},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception:
            pass

    def _consume(self, texts):
        """把素材标记为已用 —— 以后永远不会再出现。"""
        texts = [t for t in (texts or []) if t]
        if not texts:
            return
        led = self._ledger()
        today = dt.date.today().isoformat()
        for t in texts:
            led.setdefault(t, today)
        self._flush_used()

    def _release(self, texts):
        """把丢弃的素材退回池子（「重摇今天」时用，免得白白烧掉素材）。"""
        texts = [t for t in (texts or []) if t]
        if not texts:
            return
        led = self._ledger()
        for t in texts:
            led.pop(t, None)
        self._flush_used()

    # ── 当日状态存取 ─────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ── 摇一天 ────────────────────────────────────────────────────────
    def roll(self, force: bool = False):
        """没有今天的状态、或者 force，就重新生成一天的骨架。"""
        today = dt.date.today().isoformat()
        # 跨天了就把今天那份偏移重新取一遍（缓存里没有就本地现算）
        if self.plan_enabled and (self.plan or {}).get("date") != today:
            self.refresh_plan()
        if not force and self.state.get("date") == today and self.state.get("slots"):
            self.advance()
            return self.state

        salt = int(self.state.get("salt", 0)) + (1 if force else 0)
        rng = random.Random("%s|%s" % (today, salt))

        # 重摇：今天已经摇出来但不要了的那几条，退回池子，别浪费
        if force and self.state.get("slots"):
            give_back = []
            for _p in PERIOD_ORDER:
                for it in (self.state.get("slots") or {}).get(_p, []):
                    give_back.append(it.get("text"))
            give_back.append(self.state.get("quirk"))
            give_back.append(self.state.get("rare"))
            self._release(give_back)

        used = self._blocked()
        picked = []

        def pick(pool, n=1, consume=True):
            """挑 n 条还没用过的。consumed 的立刻记账，不会再用。"""
            cand = [x for x in pool if (x[0] if isinstance(x, tuple) else x) not in used]
            rng.shuffle(cand)
            out = cand[:n]
            if consume:
                for x in out:
                    t = x[0] if isinstance(x, tuple) else x
                    used.add(t)
                    picked.append(t)
            return out

        # 前一天的「惦记着的事」有一半概率留到今天（人是有连续性的）
        carried = []
        if not force and self.state.get("date"):
            for p in (self.state.get("pending") or [])[:1]:
                if rng.random() < 0.5:
                    carried.append(p)

        # 天气/状态/想吃的不进账本（环境可以重复），但避开最近几天的
        ambient = set(self.state.get("recent_ambient") or [])
        w = pick([(x, 0) for x in 素材.WEATHERS], consume=False)
        m = pick([(x, 0) for x in 素材.MOODS], consume=False)
        h = pick([(x, 0) for x in 素材.HUNGERS], consume=False)
        pd = pick([(x, 0) for x in 素材.PENDING], n=2, consume=False)

        state = {
            "date": today,
            "salt": salt,
            "weather": (w[0][0] if w else rng.choice(素材.WEATHERS)),
            "mood": (m[0][0] if m else rng.choice(素材.MOODS)),
            "energy": rng.randint(35, 92),
            "hunger": (h[0][0] if h else rng.choice(素材.HUNGERS)),
            "pending": (carried + [x[0] for x in pd])[:2],
            "quirk": "",
            "quirk_talk": 0,
            "quirk_shared": False,
            "rare": "",
            "rare_talk": 0,
            "rare_shared": False,
            "school_day": _is_school_day(dt.date.today().weekday()),
            "slots": {p[0]: [] for p in PERIODS},
            "shared": [],
            "diverts": [],                     # 今天临时改掉的计划（见 divert）
            "born": dt.datetime.now().isoformat(timespec="seconds"),
            "recent_ambient": [],
        }
        state["recent_ambient"] = [state["weather"], state["mood"], state["hunger"]]

        q = pick(素材.QUIRKS)
        if q:
            state["quirk"] = q[0][0]
            state["quirk_talk"] = q[0][1]

        # 偶尔来一件很少见的事
        if rng.random() < self.rare_chance:
            r = pick(素材.RARE)
            if r:
                state["rare"] = r[0][0]
                state["rare_talk"] = r[0][1]

        self._consume(picked)

        self.state = state
        self.advance()
        return self.state

    def advance(self):
        """把「到现在为止」该发生的补上。可以反复调用。

        时间线是单调递增的：每一件都排在上一件之后，所以「今天到现在」
        读起来是一条正常的时间线，不会出现 11:20 排在 13:52 后面。
        """
        st = self.state
        if not st:
            return st
        now = dt.datetime.now()
        now_min = now.hour * 60 + now.minute
        early = now.hour < 5                 # 凌晨：她基本在睡，只保证「深夜」有一件
        cur = "night" if early else period_of(now.hour)

        rng = random.Random("%s|%s" % (st.get("date"), st.get("salt", 0)))

        used = self._blocked()
        for p in PERIOD_ORDER:
            for it in st["slots"].get(p, []):
                used.add(it["text"])

        new_texts = []
        cur_idx = PERIOD_ORDER.index(cur)

        for idx, p in enumerate(PERIOD_ORDER):
            if idx > cur_idx:
                break
            slots = st["slots"].setdefault(p, [])
            is_current = (idx == cur_idx)

            if slots:
                # 已经是「当前时段」且距上一件超过 45 分钟 → 一半概率再发生一件，
                # 这样她的一天是活的，不会一整天就那几件事。
                if is_current and not early:
                    last = _last_min(st)
                    if last is not None and now_min - last > 45 and rng.random() < 0.5:
                        self._push(rng, st, p, slots, used, new_texts, now_min)
                continue

            if early:
                if p == "night":
                    self._push(rng, st, p, slots, used, new_texts, now_min)
                continue

            for _ in range(1 if is_current else rng.randint(1, 3)):
                self._push(rng, st, p, slots, used, new_texts, now_min)

        if new_texts:
            self._consume(new_texts)
        self._save()
        return st

    @staticmethod
    def _push(rng, st, p, slots, used, new_texts, now_min):
        """往某个时段里放一件事，时间一定排在已有事件之后。

        素材用完了就什么都不放 —— 这个时段少一件，也不回收旧料。
        """
        lo, hi = _bounds(p, now_min)
        floor = lo
        prev = _last_min(st)
        if prev is not None:
            floor = max(floor, prev + 5)
        if floor > hi:
            return
        # ★ 别把素材撒进上课那几段（2026-09-22，见 _CLASS_MARKS 上面的注释）：
        #   撒进课里就会出「自检说她在上课、她同时说自己在电梯里」。
        #   课间 / 午休 / 晚休 / 睡前都算自由时段，够撒。
        cand = free_ranges(floor, hi)
        if not cand:
            return
        _ra, _rb = rng.choice(cand)
        t = rng.randint(_ra, _rb)

        pool = [x for x in 素材.POOL.get(p, []) if x[0] not in used]
        if not pool:
            return                       # 这个时段的料用光了
        text, talk = rng.choice(pool)
        used.add(text)
        new_texts.append(text)
        slots.append({
            "text": text,
            "talk": talk,
            "min": t,
            "time": "%02d:%02d" % (t // 60 % 24, t % 60),
        })

    # ── 临时改计划 ────────────────────────────────────────────────────
    def diverts_today(self) -> list:
        """今天生效的「改过的计划」—— 返回 [(起, 止, 文本)]。

        两块合起来：
          ① 他临时改的（`state["diverts"]`，见 divert()）
          ② **他定的约**（`约定.json` 里今天到点的 appoint）——
             「上午九点说晚上六点去和西西吃饭」记的就是这个。

        ★ 顺序要紧：他定的约排在后头。`_apply_diverts` 是**顺着压**的，
          压在后头的赢 —— 所以每天那次 API 重排的日程、以及任何临时改动，
          都盖不掉他亲口定的约。

        ★ 他定的约存在 `约定.json` 里（**不是** state），所以「重摇今天」
          `--refresh`、每天那份新日程，全都不影响它。要删只有他自己开口
          （`约定.py --drop <id>`）。
        """
        today_iso = dt.date.today().isoformat()
        st = self.state or {}
        out = []
        if st.get("date") == today_iso:
            for d in (st.get("diverts") or []):
                try:
                    a = int(d.get("a"))
                    b = int(d.get("b"))
                    t = d.get("text") or ""
                except Exception:
                    continue
                if t and b > a:
                    out.append((a, b, t))
            out.sort(key=lambda x: x[0])
        out.extend(self.appointments_today())
        return out

    def appointments_today(self) -> list:
        """他定的、落在今天的约 —— 返回 [(起, 止, 文本)]。"""
        try:
            import 约定 as promise
        except Exception:
            return []
        try:
            got = promise.appointments_on(dt.date.today(), self.appoint_path)
        except Exception:
            return []
        return [(a, b, t) for a, b, t, _it in got]

    def appointments_detail(self) -> list:
        """他定的约，带 id 和起因（控制台 / 日志用）。"""
        try:
            import 约定 as promise
            return promise.appointments_on(dt.date.today(), self.appoint_path)
        except Exception:
            return []

    def diverts_detail(self) -> list:
        """今天改过的计划，带上「原本是什么、为什么改、什么时候改的」。"""
        st = self.state or {}
        out = list(st.get("diverts") or []) if \
            st.get("date") == dt.date.today().isoformat() else []
        for a, b, t, it in self.appointments_detail():
            out.append({
                "a": a, "b": b, "text": t,
                "why": "他定的（说好的事）",
                "was": self._base_text_at(a),
                "at": it.get("made", ""),
                "prot": True,
                "id": it.get("id"),
            })
        return out

    def divert(self, start_min: int, end_min: int, text: str,
               why: str = "") -> dict:
        """把她接下来那段日程改掉，返回这次改动的记录（用来打日志）。

        时间按「今天 0 点起的分钟数」算。返回的 dict 里带 `was`——
        原本那个点她该干嘛，这样控制台能打出「本来在上课 → 改成陪你」。
        """
        self.roll()
        st = self.state
        if not st:
            return {}
        start_min, end_min = int(start_min), int(end_min)
        if end_min <= start_min:
            return {}
        rec = {
            "a": start_min,
            "b": end_min,
            "text": str(text)[:40],
            "why": str(why or "")[:60],
            "was": self._base_text_at(start_min),
            "at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        ds = st.setdefault("diverts", [])
        # ★ 同一段、同一句话重复压（她连着两轮说「要去上课了」）——
        #   换掉旧的，别把一天 8 个格子浪费在同一件事上（2026-09-23）。
        ds = [d for d in ds
              if not (int(d.get("a", -1)) == start_min
                      and int(d.get("b", -1)) == end_min
                      and (d.get("text") or "") == rec["text"])]
        ds.append(rec)
        st["diverts"] = ds[-8:]              # 一天最多留 8 条，够了
        self._save()
        return rec

    def block_end_at(self, m: int) -> int:
        """m 这个点落在作息表的哪一段里 → 返回那一段的**结束**分钟数。

        接不上就返回 m 自己。给一致性自检用（见 settle_divert）：
        19:08 她说「我刚眯了会儿」，可作息表上 19:00-19:40 写着「在上课」——
        那一段整个都跟她说的对不上，替换段要一直盖到 19:40 才算抹干净。
        """
        mm = int(m) if int(m) >= 5 * 60 else int(m) + 24 * 60
        for a, b, _text, _talk, _key in _routine_today():
            if a <= mm < b:
                return int(b)
        return int(m)

    def settle_divert(self, text: str, now_min: int = None,
                      next_text: str = "", why: str = "",
                      next_until: int = None) -> dict:
        """她亲口说的话把那一段计划**提前结束了** —— 左半截留着，右半截抹掉。

        返回 {"trimmed": [...], "dropped": [...], "added": {...}|None}。

        ★ 2026-09-23 加，配合 约定.py 的一致性自检。治的是这一串：
            18:21 她说「我眯一会儿」→ 压进她今天的表：18:45-19:30「去眯一会儿」
            19:08 她自己先说了「我刚眯了会儿 现在还有点晕」
            19:21 又说「要去上课了」
          不处理的话，19:08 之后她的表上还写着「去眯一会儿」，
          而 19:00-19:40 原本是「在上课」—— 两句话都跟她自己说的对不上。

        分三种：
          · 整段都在过去（b <= now）    → **不动**。那是真发生过的，改了才是篡改。
          · 整段都还在将来（a >= now）  → **删掉**。这件事已经不做数了
            （她说她已经洗完澡了，22:00 那条「去洗澡」就该抹掉）。
          · 跨着此刻（a < now < b）     → 裁成 [a, now) 留档。

        给了 `next_text` 就再补一段 [now, 结尾) 写她**现在**在做的事，结尾取：
          · 原来那段的终点；
          · `next_until`（bridge 传的是「作息表上这一段到几点」）——
            两者取大的那个。不然 19:08 裁完，19:08-19:40 又落回「在上课」，
            等于凭空多出一节课。
        """
        self.roll()
        st = self.state
        if not st:
            return {"trimmed": [], "dropped": [], "added": None}
        now_min = _now_min() if now_min is None else int(now_min)
        ds = st.get("diverts") or []
        trimmed, dropped = [], []
        keep = []
        for d in ds:
            try:
                a, b = int(d.get("a")), int(d.get("b"))
            except Exception:
                keep.append(d)
                continue
            if text and (d.get("text") or "") != text:
                keep.append(d)
                continue
            if b <= now_min:                     # 全在过去 —— 别动
                keep.append(d)
            elif a >= now_min:                   # 全在将来 —— 作废
                dropped.append(d)
            else:                                # 跨着此刻 —— 裁短
                trimmed.append(dict(d))
                d = dict(d)
                d["b"] = now_min
                d["why2"] = "被她自己说的话截住"
                keep.append(d)
        # 先把裁短/删掉的结果落下去，**再**补「她现在在干嘛」那一段 ——
        # 反过来的话，`divert()` 刚 append 进去的那条会被这个赋值抹掉。
        st["diverts"] = keep[-8:]
        self._save()
        added = None
        if next_text:
            ends = [int(t.get("b")) for t in trimmed]
            if next_until:
                ends.append(int(next_until))
            end = max(ends) if ends else int(next_until or 0)
            # 计划整段都在过去了（她说「已经下课了」）也要盖住表上那一段
            if not ends and next_until:
                end = int(next_until)
            if end - now_min >= 3:
                added = self.divert(now_min, end, next_text,
                                    why=why or "她自己说的")
        return {"trimmed": trimmed, "dropped": dropped, "added": added}

    def _base_text_at(self, m: int) -> str:
        """按**原**作息算，m 这个点她本来在干嘛。接不上就返回空串。"""
        mm = m if m >= 5 * 60 else m + 24 * 60
        for a, b, text, _talk, _key in _routine_today():
            if a <= mm < b:
                return text
        return ""

    def clear_diverts(self) -> int:
        """把今天的临时改动全撤了（回到原作息）。返回撤了几条。"""
        st = self.state or {}
        n = len(st.get("diverts") or [])
        st["diverts"] = []
        self._save()
        return n

    # ── 时间轴 / 计划 / 自检 ──────────────────────────────────────────
    def timeline(self, now_min: int = None) -> list:
        """今天的时间轴（**带**临时改动）。"""
        self.roll()
        self.advance()
        if now_min is None:
            now_min = _now_min()
        return routine_timeline(now_min, self.diverts_today())

    def plan_ahead(self, hours: float = 2, now_min: int = None,
                   keep_sleep: bool = False) -> list:
        """从现在起 hours 小时内的安排 —— 她的「原计划」。

        返回 [(起, 止, 文本, 状态)]，状态是 now / next。

        段本身**不裁**：10:00-11:40 的课就是 10:00-11:40。
        因为这是「她的安排」，不是「接下来这么多字的时间切片」——
        裁成 10:03-12:03 反而看不出她在上什么课。

        ★ keep_sleep（2026-10-05 加）：睡觉那几段默认被滤掉（白天的「原计划」
          不该出现「在睡」）。可**凌晨**正相反 —— 她本来就在睡，
          滤掉之后就只剩一句「没别的安排，就这么晃着」，读起来像她这会儿闲着，
          她张口就是「正准备去食堂拿点吃的」。凌晨看安排时把睡眠段留下。
        """
        if now_min is None:
            now_min = _now_min()
        limit = _day_min(now_min) + int(round(hours * 60))
        out = []
        for a, b, text, state in self.timeline(now_min):
            if b <= _day_min(now_min) or a >= limit:
                continue
            if text in 作息.SLEEPY and not keep_sleep:
                continue
            out.append((a, b, text, state))
        return out

    def now_doing(self, now_min: int = None):
        """她现在正在做的那一段。返回 (起, 止, 文本) 或 None。"""
        if now_min is None:
            now_min = _now_min()
        for a, b, text, state in self.timeline(now_min):
            if state == "now":
                return (a, b, text)
        return None

    def now_brief(self, now_min: int = None) -> dict:
        """此刻那一段 + 「她腾不腾得出手」—— 给主动消息判断用（2026-09-22）。

        返回 {} 表示作息表接不上（那就当她不忙，别拦着她说话）。

            text   此刻那一段的原文（「在上课（3-4 节）：老师在台上念 PPT」）
            start / end  HH:MM
            busy   上课 / 睡着 → True
            where  「在上课（3-4 节）：老师在台上念 PPT（10:00-10:40）」
        """
        self.roll()
        self.advance()
        if now_min is None:
            now_min = _now_min()
        seg = self.now_doing(now_min)
        if not seg:
            return {}
        a, b, t = seg
        return {
            "text": t,
            "start": _hhmm(a),
            "end": _hhmm(b),
            "busy": is_busy_text(t),
            "where": "%s（%s-%s）" % (t, _hhmm(a), _hhmm(b)),
        }

    def self_check(self, now_min: int = None) -> str:
        """她自己捋一遍「我现在在哪、接下来干嘛」—— 打到黑框控制台给人看。

        ⚠ 这段**只是给人看的**，不进提示（进提示的那份是 context()）。
          它的用处是：你不用猜她这会儿该在干嘛，控制台上一眼就能看出
          「她心里是有数的」。她**不一定会主动跟他说** ——
          但他问到，她就得照这条答。
        """
        self.roll()
        self.advance()
        if now_min is None:
            now_min = _now_min()
        st = self.state or {}
        tl = self.timeline(now_min)
        ds = self.diverts_today()

        lines = ["-" * 58]
        lines.append("  她自检  %s   天气 %s"
                     % (_hhmm(now_min), st.get("weather", "")))
        if self.plan:
            lines.append("  今天偏移 %s" % self.plan_line())
        lines.append("-" * 58)

        # ★ 「多少分钟前」必须按一条不断开的轴算（见 _day_min）：
        #   凌晨的 now_min 比昨晚的钟点小，直接相减是负数、被兜成 0，
        #   读起来就成了「刚刚」。这里先把两样都算出来，下面共用。
        _dm = _day_min(now_min)
        donek = [(a, t) for a, t, s in routine_keys(now_min, ds) if s == "done"]

        nows = [(a, b, t) for a, b, t, s in tl if s == "now"]
        if nows:
            a, b, t = nows[0]
            if t in 作息.SLEEPY:
                lines.append("  此刻   她现在在睡觉（%s-%s）" % (_hhmm(a), _hhmm(b)))
                # ★ 她是几点睡下的、这条消息响起时已经睡了多久 ——
                #   凌晨他来消息时，这两样决定她「迷糊到什么程度」。
                _slept = [x[0] for x in donek if x[0] <= _dm - 5]
                if _slept:
                    lines.append("  ★ 她是 %s 睡下的 —— 这条消息响起时她才睡了 %d 分钟"
                                 % (_hhmm(_slept[-1]), _dm - _slept[-1]))
            else:
                lines.append("  此刻   她现在正在：%s（%s-%s，这段做了 %d 分钟）"
                             % (t, _hhmm(a), _hhmm(b), max(0, now_min - a)))
        else:
            lines.append("  此刻   （作息表接不上，按上一段算）")

        # ★ 「醒着多久」单独一行 —— 2026-09-22 加。
        #   08:24 那次她就是答了一句「我这边刚起 困死了」，可她 07:30 就起了、
        #   08:00 就在教室。这一行是给**看的人**先看见的：
        #   控制台上都能一眼看出「这个点她早该离床了」。
        _aw = awake_minutes(now_min)
        if _aw > 10 and not (nows and nows[0][2] in 作息.SLEEPY):
            lines.append("  ★ 她 %s 就醒了 —— 到这会儿醒着 %d 分钟，早不在床上了"
                         % (_hhmm(wake_min_today()), _aw))

        en = int(st.get("energy", 60))
        if en >= 75:
            feel = "精神很好"
        elif en >= 55:
            feel = "正常"
        elif en >= 40:
            feel = "有点累"
        elif en >= 25:
            feel = "很累，只想趴着"
        else:
            feel = "几乎撑不住"
        lines.append("  状态   精力 %d/100（%s）｜心情：%s"
                     % (en, feel, st.get("mood", "")))

        # ★ 她这会儿在睡就**别把睡眠段滤掉** —— 否则凌晨这一行只剩
        #   「没别的安排，就这么晃着」，读起来像她大半夜闲着没事干
        #   （实测 00:01：她照着这句说「正准备去食堂拿点吃的」）。
        _asleep = bool(nows and nows[0][2] in 作息.SLEEPY)
        ahead = self.plan_ahead(2, now_min, keep_sleep=_asleep)
        nxt = [x for x in ahead if x[3] == "next"]
        if nxt:
            lines.append("  后面两小时（原计划）：")
            for a, b, t, _s in nxt[:6]:
                lines.append("        %s-%s  %s" % (_hhmm(a), _hhmm(b), t))
        elif _asleep:
            lines.append("  后面两小时（原计划）：她本来就在睡 —— 回完这条接着睡")
        else:
            lines.append("  后面两小时（原计划）：没别的安排，就这么晃着")

        # 已经过完的只报**里程碑**（起床 / 出门 / 到教室 / 吃饭 / 洗澡），
        # 并且带上「多少分钟前」—— 这两样加起来，她就不会把
        # 一小时前的「刷牙」当成现在的事说。
        if donek:
            lines.append("  已经过完  " + "；".join(
                "%s %s（%d 分钟前）" % (_hhmm(a), t, max(0, _dm - a))
                for a, t in donek[-4:]))

        if ds:
            lines.append("  ★ 今天的计划已经被改过 %d 处：" % len(ds))
            for a, b, t in ds:
                lines.append("        %s-%s  %s" % (_hhmm(a), _hhmm(b), t))
        lines.append("  （她心里有数 —— 他问就照实答，不问就不主动报。）")
        return "\n".join(lines)

    # ── 对外：给模型看 ────────────────────────────────────────────────
    def context(self, max_items: int = None) -> str:
        """拼出「你那边（此刻）」这一段。

        ⚠ 这里**刻意不列「今天到现在发生了什么」**。

        以前是列的，结果就是：她刚跟人聊十几轮，就把今天买书、试衣服、
        吃甜点全倒出来 —— 一眼假。真人不是这么聊天的：刚认识的人
        只会敷衍「没干嘛」，是自己今天的事得**被话题勾出来**才想得起来。

        所以现在这一段只给「此刻」：几点、什么天气、现在在干嘛、作息，
        外加她心里惦记的大事（连续剧要连着演，必须每轮带着）。
        具体的「今天做过什么」交给 分享.py 按话题联想 —— 说到才翻。

        max_items 保留只是为了兼容旧调用，已经不用了。
        """
        if not self.enabled:
            return ""
        self.roll()
        self.advance()
        st = self.state

        now = dt.datetime.now()
        week = "一二三四五六日"[now.weekday()]
        now_min = now.hour * 60 + now.minute
        # ★ 不断开的那条轴（见 _day_min）：凌晨算「昨天的延续」，
        #   否则「已经过完 …（N 分钟前）」在 0~5 点全变成 0 分钟前 = 刚刚。
        _dm = _day_min(now_min)

        lines = ["# 你那边（这是你的处境，不是你该主动聊的话题）"]
        lines.append("- 你那边现在是 %d月%d日 周%s %02d:%02d，天气：%s"
                     % (now.month, now.day, week, now.hour, now.minute, st.get("weather", "")))

        # ══ ★ 时钟同步指标（2026-09-27）══════════════════════════════
        #   实测穿帮两次：
        #     ① 本地 02:39，她说「我这边十点半，你那儿几点了」；
        #     ② 加了规则之后还硬顶：「我这边真十点 天幕写着呢 /
        #        你那边的钟跟我这边不一样嘛」——
        #   原因是她的世界设定里有**天幕/人造太阳**（达妮娅、爱弥斯都有），
        #   模型拿「我这边的天是亮的」去反推时间，跟提示里的凌晨打架，
        #   于是它选择相信自己的天空，编出一个时间。
        #   所以这条必须**把天色也一起钉在同一个时刻上**，
        #   并给「对不上」指定唯一解释：以系统给的数字为准。
        lines.append(
            "- ★ 你的钟和他那边的钟**是同一个**，没有例外：他那边 %02d:%02d，"
            "你那边就是 %02d:%02d，同一天、同一分钟，没有时差，也不用换算。\n"
            "  · 别问「你那儿几点了」—— 上面写着就是答案；他问你时间，直接报这个数。\n"
            "  · 别把时间说成别的（「我这边才十点 / 你那边的钟跟我这边不一样」"
            "这两种话都不许说）。\n"
            "  · 你那边能看出时间的一切 —— 钟、天幕、人造太阳、海面的光度 —— "
            "都跟这个时刻对齐。**你和他不可能一个白天一个黑夜。**\n"
            "  · 万一对不上（你觉得天亮了，可上面写的是凌晨）—— 以这个数字为准，"
            "那是你看错了，不是「两边时间不一样」。\n"
            "  · 此刻天色：%s"
            % (now.hour, now.minute, now.hour, now.minute,
               _daynight_txt(now.hour)))

        # 状态：光给数字她不会演 —— 翻成人话
        en = int(st.get("energy", 60))
        if en >= 75:
            feel = "今天精神出奇地好，话会比平时多"
        elif en >= 55:
            feel = "正常状态，不困也不亢奋"
        elif en >= 40:
            feel = "有点累，反应会慢半拍"
        elif en >= 25:
            feel = "很累，只想趴着，多说两句都嫌费劲"
        else:
            feel = "几乎撑不住，只想躺下闭眼"
        lines.append("- 你今天的心情：%s" % st.get("mood", ""))
        lines.append("- 你现在的精力：%d/100 —— %s" % (en, feel))

        # 今天的「小状况」：起晚了 / 差点迟到 / 失眠。只告诉她事实，
        # 不催她讲 —— 免得又变成报备。
        if self.plan and self.plan.get("note"):
            lines.append("- 你今天的小状况：%s"
                         "（你自己心里有数就行，他问到再顺口提，别主动倒）"
                         % self.plan["note"])

        # ══ ★ 今天的时间轴：已经过完了什么 / 现在正在哪一段 / 接下来还有什么 ══
        #
        # 这一段是整份提示里最要紧的「活人感」来源，别删、别省。
        # 以前只给一句「此刻多半在：早课」，模型会把它读成「我要去上早课」——
        # 于是 8:39 说出「我刚爬下床」，接着又说「我上课要补觉」，时间线全穿帮。
        # 有了「已经过完」的那半截，她才知道自己现在到底站在一天的哪个位置。
        tl = self.timeline(now_min)
        ds = self.diverts_today()
        keys = routine_keys(now_min, ds)
        if tl:
            nows = [(x[0], x[1], x[2]) for x in tl if x[3] == "now"]
            # ⚠ 别在这里用裸名 `b` 当推导变量 —— 下面 `na, nb, nt = nows[0]` 会给
            #   本函数里的 `b` 赋值，推导式里的 `b` 就变成「还没赋值的自由变量」，
            #   跑起来直接 NameError（2026-09-21 踩过）。所以这里叫 na/nb/nt。
            # ★ 「两小时原则」：细的只给到从现在起 plan_horizon_hours 小时
            #   （config.yaml 的 life.day_plan.horizon_hours，默认 2）。
            #   再往后的用下面「今天再往后」那行报里程碑就够了 ——
            #   一分钟一段地铺一整天，反而把要紧的几句淹掉。
            _hz = now_min + int(round(self.plan_horizon_hours * 60))
            nxt = [(x[0], x[1], x[2]) for x in tl
                   if x[3] == "next" and x[2] not in 作息.SLEEPY and x[0] < _hz]
            if nows:
                na, nb, nt = nows[0]
                if nt in 作息.SLEEPY:
                    _sl = [a for a, t, s in keys if s == "done" and a <= _dm - 5]
                    lines.append(
                        "- ★ 你现在**在睡觉**，是他一条消息把你叫醒的"
                        "（见 persona「他半夜发消息」——照样要回，"
                        "但可以像个刚醒的人）"
                        + ("；你是 %s 睡下的，这条响起时你才睡了 %d 分钟 ——"
                           "这是今天最后一件已经过完的事，不是「刚从天台下来」。"
                           % (_hhmm(_sl[-1]), _dm - _sl[-1]) if _sl else ""))
                else:
                    lines.append("- ★ 你现在**正在**这一段里（%s-%s）：%s"
                                 " —— 这一小段你已经做了 %d 分钟"
                                 % (_hhmm(na), _hhmm(nb), nt, max(0, now_min - na)))

            # ★★「醒着多久」—— 2026-09-22 加。这一行是「8:24 还说刚起」的正面解药：
            #   把「07:30 起的」和「已经醒着 54 分钟」明明白白摆在她眼前。
            #   模型不傻，它只是不知道 —— 给了它这个数，它自己就不会说错。
            _aw = awake_minutes(now_min)
            if _aw > 10 and not (nows and nows[0][2] in 作息.SLEEPY):
                lines.append(
                    "- ★ 你 %s 就醒了，到这会儿**已经醒着 %d 分钟** —— "
                    "「刚起」「刚醒」「还在床上」「刚爬下床」这种话，"
                    "这个点说出来就是错的，你早不在床上了。"
                    % (_hhmm(wake_min_today()), _aw))

            donek = [(a, t) for a, t, s in keys if s == "done"]
            if donek:
                lines.append("- 今天到这会儿，你已经过完了：%s"
                             % "；".join("%s %s（%d 分钟前）"
                                        % (_hhmm(a), t, max(0, _dm - a))
                                        for a, t in donek[-4:]))
            # ★ 细的「接下来」—— 作息表分到分钟之后，这几行就是
            #   「她接下来这几十分钟干嘛」的准确答案。07:35 他发「早」，
            #   这里给的正好是「07:32-07:40 刷牙、洗脸」，她顺嘴就能说。
            if nxt:
                lines.append("- 你接下来这几段：%s"
                             % "；".join("%s-%s %s" % (_hhmm(a), _hhmm(b), t)
                                        for a, b, t in nxt[:6]))
            # ★ 粗的「今天剩什么」—— 只报里程碑。他问「你今天还忙吗」
            #   「晚上有空吗」，答案在这行里，不在上面那几行十几分钟的小事里。
            aheadk = [(a, t) for a, t, s in keys if s == "next"][:5]
            if aheadk:
                lines.append("- 今天再往后：%s"
                             % "；".join("%s %s" % (_hhmm(a), t) for a, t in aheadk))
            # ★ 「表上的字」和「嘴里的字」不是一回事（2026-09-28 实录）——
            #   上面这些钟点（12:00 / 19:00-19:40）是拿来给她自己对表的，
            #   她照着念就出事：主动发出去的那条是
            #   「00 39那会儿睡前把闹钟往后调了十分钟」（用户：「什么鬼」）。
            #   真人开口不念表，这里明说一句，省得她逐字转述。
            lines.append("- （上面这些钟点是给你自己对表用的 —— 说出口要翻成人话："
                         "「九点四十」「那会儿」「早上」，别念「19:40」这种数字。）")
            if ds:
                _ap = {(a, b, t) for a, b, t in self.appointments_today()}
                lines.append("- ★ 今天的计划已经被改过了：%s"
                             % "；".join("%s-%s %s%s"
                                         % (_hhmm(a), _hhmm(b), t,
                                            "（**他亲口跟你定好的**，别忘了）"
                                            if (a, b, t) in _ap else "")
                                         for a, b, t in ds))
            lines.append("- ★ 回他之前先看这几行，只说自己这个点该说的话：")
            lines.append("  · 正在做的事用「在……」说 —— 上面「你现在正在」那一段就是答案"
                         "（「我在上早课」「我在刷牙」），不是「我要去上课」，"
                         "更不是「刚起床」；")
            lines.append("  · 「已过完」的是**过去**（提可以，别说成现在）；"
                         "「接下来」的是**还没发生**的（说「待会」「等会儿」）。")
            lines.append("  · **他要是问你此刻在干嘛 / 忙不忙 / 有没有空，"
                         "就照上面几行如实说**（「在上课」「在图书馆」「刚下课」），"
                         "短可以，但不能是「没干嘛」「没什么」这种空话，也不许岔开。")
            lines.append("  · 但他只是随口打个招呼（「早」「在吗」「干嘛呢」），"
                         "**你手上这件小事可以顺嘴带一句** —— 「早 我在刷牙」"
                         "「刚下课 在往食堂走」。这叫顺口，不是报备。")
            lines.append("  · 报备是指把自己一整天排着队倒出来"
                         "（我七点半起的、八点上课、刚下课那个），不用。"
                         "他没问，你就不交代日程 —— 你的日子是你自己过的。")

        # 这几天在演的大事（连续剧）—— 这个必须每轮都带，不然会断
        story_lines = self.story.lines()
        if story_lines:
            lines.append("")
            lines.extend(story_lines)

        if st.get("pending"):
            lines.append("- 你心里惦记着：%s" % "；".join(st["pending"]))
        if st.get("hunger"):
            lines.append("- 你现在想吃：%s" % st["hunger"])

        lines.append("")
        lines.append("（你今天确实过了一天，做过一些事 —— 但**不用主动跟他讲**。")
        lines.append("  他要是正好聊到相关的，系统会把那件事递到你眼前，那时候再说。")
        lines.append("  在那之前，别自己编今天干了什么，也别报流水账。）")
        return "\n".join(lines)

    # ── 给联想引擎用（不给模型看） ──────────────────────────────────────
    def today_events(self) -> list:
        """今天发生过的事情清单 —— 给 分享.py 联想用。

        这就是原来直接列进提示词的那份东西。现在它只在
        「他说的话撞上了某个话题」时才被翻出来一条，见 分享.py。
        """
        if not self.enabled:
            return []
        self.roll()
        self.advance()
        st = self.state
        shared = set(st.get("shared", []))

        out = []

        # 今天各时段发生过的事（只给已经到点的，不剧透还没发生的）
        for p in PERIOD_ORDER:
            for it in st["slots"].get(p, []):
                t = it.get("text")
                if not t:
                    continue
                out.append({
                    "text": t,
                    "time": it.get("time", ""),
                    "kind": "event",
                    "min": it.get("min", _to_min(it.get("time", ""))),
                    "said": t in shared,
                })

        # 今天那件小插曲 / 少见的事
        if st.get("quirk"):
            out.append({"text": st["quirk"], "time": "", "kind": "quirk",
                        "min": 0, "said": bool(st.get("quirk_shared"))
                        or st["quirk"] in shared})
        if st.get("rare"):
            out.append({"text": st["rare"], "time": "", "kind": "rare",
                        "min": 0, "said": bool(st.get("rare_shared"))
                        or st["rare"] in shared})

        out.sort(key=lambda x: x.get("min", 0))
        return out

    def nudge(self):
        """挑一件「她现在想跟你说」的事。返回 dict 或 None。"""
        if not self.enabled:
            return None
        self.roll()
        self.advance()
        st = self.state
        shared = set(st.get("shared", []))

        rng = random.Random()
        now_min = _now_min()

        # 1) 正在演的大事优先 —— 她心里装着这件事，自然会提
        sp = [x for x in self.story.pending() if x.get("talk", 0) >= 2]
        if sp and rng.random() < 0.6:
            it = rng.choice(sp)
            return {
                "text": it["text"],
                "kind": "story",
                "arc": it.get("arc", ""),
                "time": it.get("when", ""),
                # 大事没有精确时间戳 → ago = None（引子会含糊成「今天早些时候」）
                "ago": None,
                "mood": st.get("mood", ""),
                "weather": st.get("weather", ""),
                "now": "%02d:%02d" % (now_min // 60, now_min % 60),
                "now_text": (self.now_doing(now_min) or (0, 0, ""))[2],
            }

        # 2) 今天发生过、还没提过的事
        cand = []
        if st.get("rare") and not st.get("rare_shared") and st.get("rare_talk", 0) >= 2:
            cand.append({"text": st["rare"], "talk": 2, "kind": "rare"})
        for p in PERIOD_ORDER:
            for it in st["slots"].get(p, []):
                if it["text"] in shared:
                    continue
                if it.get("talk", 0) >= 2:
                    cand.append(it)
        if st.get("quirk") and not st.get("quirk_shared") and st.get("quirk_talk", 0) >= 2:
            cand.append({"text": st["quirk"], "talk": 2, "kind": "quirk"})

        if not cand:
            # 退一步：任何今天还没说过的
            for p in PERIOD_ORDER:
                for it in st["slots"].get(p, []):
                    if it["text"] not in shared:
                        cand.append(it)
        if not cand:
            return None

        # 越靠后（越近发生）越可能被提起
        it = rng.choice(cand[-4:]) if rng.random() < 0.7 else rng.choice(cand)
        is_quirk = it.get("kind") == "quirk" or it["text"] == st.get("quirk")

        # ★ 「这件事是多久以前发生的」（2026-09-22 加）——
        #   10:26 的素材被当成「现在」说出来，就是这么来的：
        #   引子只说「你今天做了这件事」，她读成「正在做」。
        #   这里把「几分钟前」算出来交给 bridge，让它硬性要求带上时间词。
        #   quirk / rare / story 没有时间戳 → None（引子会含糊成「今天早些时候」）。
        _m = it.get("min")
        ago = None
        if isinstance(_m, int) and _m >= 0:
            ago = max(0, now_min - _m)
        return {
            "text": it["text"],
            "kind": it.get("kind") or ("quirk" if is_quirk else "event"),
            "time": it.get("time", ""),
            "ago": ago,
            "mood": st.get("mood", ""),
            "weather": st.get("weather", ""),
            "now": "%02d:%02d" % (now_min // 60, now_min % 60),
            "now_text": (self.now_doing(now_min) or (0, 0, ""))[2],
        }

    def mark_shared(self, text: str):
        """记下「这件事已经跟她提过了」，避免下次再说一遍。"""
        st = self.state
        if not st or not text:
            return
        if st.get("quirk") == text:
            st["quirk_shared"] = True
        if st.get("rare") == text:
            st["rare_shared"] = True
        for p in PERIOD_ORDER:
            for it in st["slots"].get(p, []):
                if it["text"] == text:
                    it["talk"] = 0
        sh = st.setdefault("shared", [])
        if text not in sh:
            sh.append(text)
        self._save()
        # 大事也记一笔（同一天不再用它开头）
        try:
            self.story.mark_shared(text)
        except Exception:
            pass

    # ── 库存 ─────────────────────────────────────────────────────────
    def stock(self) -> dict:
        """还剩多少素材。耗尽了就只能靠大事和作息撑着了。"""
        used = self._blocked()
        per = {k: sum(1 for x in v if x[0] not in used) for k, v in 素材.POOL.items()}
        q = sum(1 for x in 素材.QUIRKS if x[0] not in used)
        r = sum(1 for x in 素材.RARE if x[0] not in used)
        pool_left = sum(per.values())
        return {
            "pool": per,
            "pool_left": pool_left,
            "pool_total": sum(len(v) for v in 素材.POOL.values()),
            "pool_used": sum(len(v) for v in 素材.POOL.values()) - pool_left,
            "quirks": q, "quirks_total": len(素材.QUIRKS),
            "rares": r, "rares_total": len(素材.RARE),
            "left": pool_left + q + r,
            "total": sum(len(v) for v in 素材.POOL.values()) + len(素材.QUIRKS) + len(素材.RARE),
            "used": len(self._ledger()),
            "reuse_after_days": self.reuse_after_days,
        }

    def stats(self):
        st = self.roll()
        return {
            "date": st.get("date"),
            "weather": st.get("weather"),
            "mood": st.get("mood"),
            "energy": st.get("energy"),
            "school_day": st.get("school_day"),
            "events": sum(len(v) for v in st.get("slots", {}).values()),
        }


# ══════════════════════════════════════════════════════════════════════════
#  小工具
# ══════════════════════════════════════════════════════════════════════════

def _bounds(key: str, now_min: int):
    """某个时段的可用时间范围（分钟）。深夜跨 0 点，用 23:00~29:00 表示。"""
    if key == "night" and now_min < 23 * 60:
        return 0, now_min                      # 凌晨：只到「现在」
    for k, a, b, _ in PERIODS:
        if k == key:
            return a * 60, min(b * 60 - 1, now_min)
    return 0, now_min


def _to_min(t: str) -> int:
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def _load_cfg() -> dict:
    """读 config.yaml（读不到就当空配置）。命令行里想看真实设置时用。"""
    try:
        import yaml
        p = ROOT / "config.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _last_min(st: dict):
    best = None
    for p in PERIOD_ORDER:
        for it in st["slots"].get(p, []):
            v = it.get("min")
            if v is None:
                v = _to_min(it.get("time", ""))
            if v is not None and v >= 0:
                best = v if best is None else max(best, v)
    return best


# ══════════════════════════════════════════════════════════════════════════
#  命令行：方便你自己看一眼她今天在干嘛
# ══════════════════════════════════════════════════════════════════════════

def _cli():
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]

    if "--stock" in args:
        s = Life({}).stock()
        print("=" * 62)
        print("素材库存（用掉就永久没了）")
        print("=" * 62)
        for p in PERIOD_ORDER:
            left = s["pool"].get(p, 0)
            tot = len(素材.POOL.get(p, []))
            bar = "#" * int(20 * left / max(1, tot))
            print("  %-6s %4d / %4d  %s" % (period_name(p), left, tot, bar))
        print("  " + "-" * 46)
        print("  %-6s %4d / %4d" % ("小插曲", s["quirks"], s["quirks_total"]))
        print("  %-6s %4d / %4d" % ("少见事", s["rares"], s["rares_total"]))
        print()
        print("  剩余合计：%d / %d（已用 %d）" % (s["left"], s["total"], s["used"]))
        if s["reuse_after_days"] > 0:
            print("  （life.reuse_after_days=%d：%d 天前用过的会回到池子）"
                  % (s["reuse_after_days"], s["reuse_after_days"]))
        if s["left"] == 0:
            print("  [X] 素材全用完了！往 素材.py 里继续加料吧。")
        elif s["left"] < 100:
            print("  [!] 快见底了，建议往 素材.py 里加料（格式：(\"文本\", 话题度)）。")
        else:
            est = s["left"] / 7.0
            print("  按每天大约消耗 7 条算，还够 %.0f 天左右。" % est)
        return

    if "--pool" in args:
        key = "morning"
        try:
            key = args[args.index("--pool") + 1]
        except Exception:
            pass
        if key not in 素材.POOL:
            print("[X] 没有这个时段。可选：" + "、".join(PERIOD_ORDER))
            return
        used = Life({})._blocked()
        print("=" * 62)
        print("事件池 · %s（%s）—— 只列还没用过的" % (period_name(key), key))
        print("=" * 62)
        n = 0
        for text, talk in 素材.POOL[key]:
            if text in used:
                continue
            n += 1
            print("  [%d] %s" % (talk, text))
        print()
        print("  剩 %d 条（这个池子用过的 %d 条已经不再出现）" % (n, len(素材.POOL[key]) - n))
        return

    if "--self" in args:
        # 她自己捋一遍现在在哪、接下来干嘛（一键启动时那个黑框里也打这个）
        print(Life(_load_cfg()).self_check())
        return

    if "--day" in args:
        # 看她今天这一天有什么「偏移」（起晚了 / 差点迟到 / 失眠…）
        life = Life(_load_cfg())
        print("=" * 62)
        print("她今天这一天（偏移）")
        print("=" * 62)
        if not life.plan_enabled:
            print("  （没开 —— config.yaml 里 life.day_plan.enabled 还是 false）")
        print("  " + (life.plan_line() or "（照基准作息，今天没有偏移）"))
        for e in (life.plan or {}).get("edits", []):
            print("  · %s-%s 这一段换掉：" % (_hhmm(e["a"]), _hhmm(e["b"])))
            for a, b, t, _talk, key in e["segs"]:
                print("      %s-%s %s%s" % (_hhmm(a), _hhmm(b), t, "  ★" if key else ""))
        ap = life.appointments_detail()
        if ap:
            print("")
            print("  他定的约（每天重排日程也删不掉，只有他能取消）：")
            for a, b, t, it in ap:
                print("      %s-%s %s（起因：%s）"
                      % (_hhmm(a), _hhmm(b), t, it.get("raw") or "他说的"))
        print("")
        print("  提前看往后几天摇到什么：python 日计划.py --local 7")
        return

    if "--undo" in args:
        n = Life({}).clear_diverts()
        print("[OK] 撤掉了 %d 处临时改的计划（回到原作息）。" % n)
        return

    if "--divert" in args:
        # 手动替她改一段计划：python daily_life.py --divert 12:00-14:00 陪他打游戏
        try:
            i = args.index("--divert")
            span = args[i + 1]
            text = args[i + 2] if len(args) > i + 2 else ""
            a_s, b_s = span.split("-")
            a = int(a_s.split(":")[0]) * 60 + int(a_s.split(":")[1])
            b = int(b_s.split(":")[0]) * 60 + int(b_s.split(":")[1])
        except Exception:
            print("[X] 用法：python daily_life.py --divert 12:00-14:00 陪他打游戏")
            return
        rec = Life({}).divert(a, b, text, why="手动改的")
        if not rec:
            print("[X] 没改成（时间不对？）")
            return
        print("[OK] 改好了：%s-%s  %s（本来：%s）"
              % (_hhmm(rec["a"]), _hhmm(rec["b"]), rec["text"], rec["was"] or "没有安排"))
        return

    if "--plan" in args:
        print("=" * 62)
        print("她的作息（今天%s）" % ("有课" if _is_school_day(dt.date.today().weekday()) else "没课"))
        print("=" * 62)
        life = Life({})
        ds = life.diverts_today()
        for a, b, text, state in life.timeline(_now_min()):
            if text in ("睡着", "睡死过去"):
                continue
            mark = {"done": "  ", "now": "->", "next": "  "}[state]
            print(" %s%02d:%02d-%02d:%02d  %s"
                  % (mark, a // 60 % 24, a % 60, b // 60 % 24, b % 60, text))
        if ds:
            print()
            print("  ★ 今天被改过的计划：")
            for a, b, t in ds:
                print("     %02d:%02d-%02d:%02d  %s"
                      % (a // 60 % 24, a % 60, b // 60 % 24, b % 60, t))
        print()
        s = Life({}).stock()
        print("  素材剩余：%d / %d" % (s["left"], s["total"]))
        print("  剧情线：%d 条（在演 %d 条）"
              % (len(大事.ARCS), len(大事.Story({}).stats()["active"])))
        return

    if "--refresh" in args:
        life = Life({})
        life.roll(force=True)
        print("[OK] 已重摇今天（今天用掉的素材已退回池子）。")
        return

    if "--nudge" in args:
        n = 5
        try:
            n = int(args[args.index("--nudge") + 1])
        except Exception:
            pass
        life = Life({})
        print("抽 %d 个「她想跟你说的事」：\n" % n)
        for i in range(n):
            got = life.nudge()
            if not got:
                print("  （今天的事都说完了）")
                break
            print("  [%s] %s  %s" % (got["kind"], got.get("time", ""), got["text"]))
        print()
        print("（这个只是抽样看看；真实运行时她不会重复说同一件。）")
        return

    life = Life({})
    print("=" * 62)
    print("达妮娅 · 今天")
    print("=" * 62)
    print()
    print(life.context())
    print()
    print("=" * 62)
    print("她可能主动找你聊的：")
    got = life.nudge()
    print("   ", got["text"] if got else "（今天没什么想说的）")


if __name__ == "__main__":
    _cli()
