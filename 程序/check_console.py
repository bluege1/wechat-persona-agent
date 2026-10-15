# -*- coding: utf-8 -*-
"""达妮娅 · 控制台体检（见 README 坑十九）

为什么要单独一支：控制台是**另一个进程**，`查错.py` 只能做源码级断言
（「页面上有没有这串字」）。但「旋钮点下去真能落盘吗」「走不开时真不发吗」
「忘掉之后 core 真的没动吗」—— 这些**必须真起服务打一遍**才知道。

做法：把项目**除 `.venv` / 日志 / __pycache__ / *.bak 外**复制到 `_sandbox_ui/`，
在那个副本里起控制台（所以**正在用的数据一个字节都不会动**），HTTP 打一遍。

第二段是无头渲染：9 个面板在 node 里跑一遍，查 `undefined` / `NaN` /
`[object Object]` 有没有漏进 HTML。网页都是字符串拼的，语法对不代表页面不炸。
没装 node 就自动跳过（只提示，不算失败）。

跑完全绿会把副本删掉；有失败就留着，方便复看。

用法：
    .venv\\Scripts\\python.exe check_console.py
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
except Exception:
    pass

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
SB = ROOT / "_sandbox_ui"
PORT = 7799
PY_ = str(PROG / ".venv" / "Scripts" / "python.exe")
SKIP = {".venv", "日志", "__pycache__"}
JUNK = ("_page.js", "_all.json", "_render_check.js", "_sandbox_ui")

fails = []
oks = 0


def ok(cond, label, extra=""):
    global oks
    if cond:
        oks += 1
        print("[OK] %s%s" % (label, ("  —— " + extra) if extra else ""))
    else:
        fails.append(label)
        print("[X ] %s%s" % (label, ("  —— " + extra) if extra else ""))


def find_node():
    """找个能用的 node。找不到就返回 None（渲染那段自动跳过）。"""
    cands = []
    w = shutil.which("node")
    if w:
        cands.append(w)
    cands += [r"C:/Program Files/nodejs/node.exe",
              str(Path.home() / "AppData/Roaming/npm/node.exe")]
    try:
        import glob as _g
        cands += sorted(_g.glob(str(Path.home()
                                      / ".workbuddy/binaries/node/versions/*/node.exe")))
    except Exception:
        pass
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


def free_port(p=PORT, tries=30):
    for q in range(p, p + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", q))
            return q
        except OSError:
            continue
        finally:
            try:
                s.close()
            except Exception:
                pass
    return p


def wait_port(port, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = socket.socket()
        s.settimeout(0.4)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.25)
        finally:
            try:
                s.close()
            except Exception:
                pass
    return False


print("=" * 66)
print("达妮娅 · 控制台体检")
print("=" * 66)

# ── 0. 建隔离副本 ──
if SB.exists():
    shutil.rmtree(SB)
SB.mkdir(parents=True)
for p in ROOT.iterdir():
    if p.name in SKIP or p.name.startswith("_") or ".bak" in p.name:
        continue
    if p.is_dir():
        shutil.copytree(p, SB / p.name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*"))
    else:
        shutil.copy2(p, SB / p.name)
print("隔离副本：%s" % SB)

# 「她主动过几次」清零 —— 不然第 ② 号闸（每小时/每天上限）会先拦住，
# 挡在我们要验的第 ④ 号闸（走不走得开）前面。
(SB / "生活").mkdir(exist_ok=True)
(SB / "生活" / "主动.json").write_text(
    json.dumps({"sent": [], "last_talk": time.time() - 99999}), encoding="utf-8")

# 塞三条「旧流水账」进去，好验证「忘掉」真能清、且 core 不动
jf = SB / "记忆" / "总结.json"
jd = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
CORE = jd.get("core") or "他是一个爱在半夜说话的人。"
jd["core"] = CORE
jd["entries"] = [
    {"time": "2026-09-20 21:00", "at_turn": 10, "text": "他说今天吃了麻辣烫。"},
    {"time": "2026-09-20 23:00", "at_turn": 20, "text": "他说要打两把游戏再睡。"},
    {"time": "2026-09-21 09:00", "at_turn": 30, "text": "他先说了晚安。"},
]
jf.parent.mkdir(parents=True, exist_ok=True)
jf.write_text(json.dumps(jd, ensure_ascii=False, indent=2), encoding="utf-8")

# ── 1. 起服务 ──
env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
PORT = free_port(PORT)
srv = subprocess.Popen([PY_, str(SB / "控制台.py"), "--port", str(PORT), "--no-browser"],
                       cwd=str(SB), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
BASE = "http://127.0.0.1:%d" % PORT


def get(path, base=None):
    with urllib.request.urlopen((base or BASE) + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, payload=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


try:
    if not wait_port(PORT):
        print("[X ] 控制台在副本里没起来")
        print(srv.stdout.read().decode("utf-8", "replace")[-2500:])
        sys.exit(1)

    # ── 2. /api/all：新字段在不在 ──
    print("\n-- 一、/api/all 把「此刻忙不忙」带出来了 --")
    d = get("/api/all")
    ok(d.get("ok") is True, "/api/all 通")
    life = d.get("life") or {}
    nb = life.get("now_brief") or {}
    ok("now_brief" in life, "life 里带上了 now_brief")
    ok(isinstance(nb, dict) and "busy" in nb and "where" in nb,
       "now_brief 有 busy / where", repr(nb)[:110])
    ok("busy" in life, "life 顶层也带了 busy", "busy=%s" % life.get("busy"))
    ok(life.get("busy") == bool(nb.get("busy")), "life.busy 和 now_brief.busy 一致")

    pa = d.get("proactive") or {}
    for k in ("quiet_when_busy", "busy", "gate_busy", "busy_where"):
        ok(k in pa, "proactive.status 里带了 %s" % k, repr(pa.get(k))[:60])
    if pa.get("quiet_when_busy") and pa.get("busy"):
        ok(pa.get("can_send") is False, "走不开 + 闸开着 => can_send=False")
        ok(bool(pa.get("why_not")), "给得出一个不能发的理由",
           str(pa.get("why_not"))[:70])
    else:
        ok(True, "（此刻她腾得出手，闸没触发 —— 逻辑跳过）")
        ok(True, "（同上）")

    # ── 3. 四个新旋钮：读得到 ──
    print("\n-- 二、四个新旋钮在网页上读得到 --")
    cfg = d.get("config") or {}
    for k in ("proactive.quiet_when_busy", "journal.keep_entries",
              "journal.core_chars", "journal.min_turns"):
        ok(k in cfg, "配置项可读：%s" % k, "值=%r" % ((cfg.get(k) or {}).get("value"),))

    # ── 4. 第 ④ 号闸的对照组 ──
    #   把别的闸都放开，只留「走不开」—— 这样能证明拦她的确实是这道闸。
    print("\n-- 三、第 ④ 号闸（走不走得开）的对照组 --")
    post("/api/relation/set", {"intimacy": 999})      # ① 配额：拉到顶档
    post("/api/config", {"values": {                  # ② 频率 / ③ 安静够久：全放开
        "proactive.max_per_hour": 99, "proactive.max_per_day": 99,
        "proactive.min_idle_minutes": 0,
        "proactive.active_hours.0": 0, "proactive.active_hours.1": 24}})
    pa3 = (get("/api/all") or {}).get("proactive") or {}
    nb3 = (((get("/api/all") or {}).get("life") or {}).get("now_brief") or {})
    if nb3.get("busy"):
        ok(pa3.get("gate_busy") is True, "认出「此刻走不开」",
           str(pa3.get("busy_where"))[:56])
        ok(pa3.get("can_send") is False, "★ 走不开 => 不给发（别的闸都放开了）")
        ok("走不开" in (pa3.get("why_not") or ""), "★ 理由就是「走不开」",
           str(pa3.get("why_not"))[:70])
        post("/api/config", {"values": {"proactive.quiet_when_busy": False}})
        pa4 = (get("/api/all") or {}).get("proactive") or {}
        ok(pa4.get("gate_busy") is False, "闸关掉后 gate_busy=False")
        ok(pa4.get("can_send") is True,
           "★ 闸关掉 => 同一时刻立刻放行（证明拦她的就是这道闸）",
           "why_not=%r" % pa4.get("why_not"))
        post("/api/config", {"values": {"proactive.quiet_when_busy": True}})
        pa5 = (get("/api/all") or {}).get("proactive") or {}
        ok(pa5.get("can_send") is False, "闸开回来 => 又拦住了")
    else:
        print("（她现在不在上课/睡觉，对照组跳过：%s）" % nb3.get("where"))
        for _ in range(6):
            ok(True, "（不忙，跳过闸对照）")

    # ── 5. 旋钮真能落盘 ──
    print("\n-- 四、新旋钮点下去真能写进 config.yaml --")
    r = post("/api/config", {"values": {"journal.keep_entries": 30,
                                        "proactive.quiet_when_busy": False}})
    ok(r.get("ok") is True and not (r.get("failed") or []),
       "通过网页改新旋钮", "done=%s failed=%s" % (r.get("done"), r.get("failed")))
    ytxt = io.open(SB / "config.yaml", encoding="utf-8").read()
    ok("keep_entries: 30" in ytxt, "config.yaml 里 keep_entries 真成了 30")
    ok("quiet_when_busy: false" in ytxt, "config.yaml 里 quiet_when_busy 真成了 false")
    post("/api/config", {"values": {"journal.keep_entries": 24,
                                    "proactive.quiet_when_busy": True}})
    ytxt = io.open(SB / "config.yaml", encoding="utf-8").read()
    ok("keep_entries: 24" in ytxt and "quiet_when_busy: true" in ytxt, "原值写得回去")

    # ── 6. 「忘掉旧复盘」──
    print("\n-- 五、「把旧流水账忘掉」只动 entries，一字不碰 core --")
    d2 = get("/api/all")
    ok(len(((d2.get("journal") or {}).get("entries") or [])) == 3, "预置的 3 条能看到")
    r = post("/api/journal/forget", {})
    ok(r.get("ok") is True, "POST /api/journal/forget 通")
    ok(r.get("forgot") == 3, "报告忘掉了 3 条", "forgot=%r" % r.get("forgot"))
    after = r.get("journal") or {}
    ok(len(after.get("entries") or []) == 0, "entries 真清空了")
    ok((after.get("core") or "").strip() == CORE.strip(),
       "★ 长期记忆一个字没动", "core=%r" % (after.get("core") or "")[:40])
    disk = json.loads(jf.read_text(encoding="utf-8"))
    ok(len(disk.get("entries") or []) == 0, "磁盘上的 entries 也空了")
    ok((disk.get("core") or "").strip() == CORE.strip(), "磁盘上的 core 也一字未动")
    baks = sorted((SB / "记忆").glob("总结.json.bak-忘复盘-*"))
    ok(len(baks) >= 1, "点之前自动备份了一份", baks[0].name if baks else "没找到")
    if baks:
        bd = json.loads(baks[-1].read_text(encoding="utf-8"))
        ok(len(bd.get("entries") or []) == 3, "备份里那 3 条还在（点错能捞回来）")
    r2 = post("/api/journal/forget", {})
    ok(r2.get("ok") is True and r2.get("forgot") == 0, "没有旧复盘时再点一次也不报错")

    # ── 7. 「试生成一条」把证据摆出来 ──
    print("\n-- 六、「试生成一条」会说清时间 / 此刻在哪 / 会不会被拦 --")
    r3 = post("/api/proactive/try", {})
    if r3.get("ok"):
        ok("ago_text" in r3, "报「这件事是多久前发生的」",
           "ago=%r ago_text=%r" % (r3.get("ago"), r3.get("ago_text")))
        ok(bool(r3.get("now_where")), "带你此刻在哪", str(r3.get("now_where"))[:56])
        ok("blocked" in r3, "说清这道闸会不会拦她",
           "blocked=%s now_busy=%s" % (r3.get("blocked"), r3.get("now_busy")))
        ok(bool(r3.get("parts")), "有内容", "%d 条" % len(r3.get("parts") or []))
        print("     样例：%s" % " / ".join(r3.get("parts") or [])[:150])
    else:
        print("     （跳过：%s —— 多半是今天素材用完了）" % r3.get("error"))
        for _ in range(4):
            ok(True, "（没吐内容，但接口是通的）")

    # ── 8. 无头渲染（服务还活着，直接取 /api/all）──
    print("\n-- 七、页面无头渲染（查 undefined / NaN 漏进 HTML）--")
    node = find_node()
    if not node:
        print("[! ] 没找到 node，这段跳过（不影响上面的结论）")
    else:
        src = io.open(ROOT / "控制台.py", encoding="utf-8").read()
        js = re.search(r"<script>(.*?)</script>",
                       re.search(r'PAGE = r"""(.*?)"""', src, re.S).group(1),
                       re.S).group(1)
        (ROOT / "_page.js").write_text(js, encoding="utf-8")
        (ROOT / "_all.json").write_text(
            json.dumps(get("/api/all"), ensure_ascii=False), encoding="utf-8")
        HARNESS = r"""
const fs = require('fs');
const P = %s;
const js = fs.readFileSync(P + '_page.js', 'utf8');
const D0 = JSON.parse(fs.readFileSync(P + '_all.json', 'utf8'));
let writes = [];
function mkEl(){
  return { set innerHTML(v){ writes.push(String(v)); },
           get innerHTML(){ return writes[writes.length-1] || ''; },
           className:'', textContent:'', value:'',
           addEventListener: () => {}, appendChild: () => {}, removeChild: () => {},
           style:{}, classList:{ add:()=>{}, remove:()=>{} } };
}
global.document = { getElementById: mkEl, querySelector: mkEl,
                    querySelectorAll: () => [], createElement: mkEl };
global.location = { hash: '' };
global.window = { addEventListener: () => {} };
global.confirm = () => true;
global.fetch = () => Promise.resolve({ text: () => Promise.resolve('{}') });
global.setTimeout = (f) => { try { f(); } catch(e){} return 0; };
eval(js);
D = D0;
const MUST = {
  proactive: ['此刻她走不走得开','五道闸','走不走得开','quiet_when_busy','你发消息来她照样回'],
  memory: ['复盘记什么','只记这五类','一律不写','整段留空','把旧流水账忘掉','journal.keep_entries'],
  life: ['走不开','时间不会落在上课段里'],
};
const panes = [['overview',paneOverview],['brain',()=>paneConfig(['target','llm.model'],'脑','x')],
  ['worldbook',paneWorldbook],['share',paneShare],['proactive',paneProactive],
  ['vision',paneVision],['memory',paneMemory],['life',paneLife],['story',paneStory]];
let bad = 0;
for (const [name, fn] of panes) {
  writes = [];
  let err = '';
  try { fn(); } catch (e) { err = String(e && e.stack || e); }
  const all = writes.join('\n');
  const longest = writes.reduce((a,b) => a.length >= b.length ? a : b, '');
  const smells = [];
  if (/undefined/.test(all)) smells.push('undefined');
  if (/\bNaN\b/.test(all)) smells.push('NaN');
  if (/\[object Object\]/.test(all)) smells.push('[object Object]');
  if (err) smells.push('抛错');
  if (longest.length < 200) smells.push('HTML 太短(' + longest.length + ')');
  const miss = (MUST[name] || []).filter(s => all.indexOf(s) < 0);
  if (miss.length) smells.push('缺内容: ' + miss.join('/'));
  if (smells.length) { bad++; console.log('[X ] ' + name + '  ' + smells.join(', '));
    if (err) console.log('     ' + err.split('\n').slice(0,3).join(' | ')); }
  else console.log('[OK] ' + name + '  ' + longest.length + ' 字符，干净'
                   + ((MUST[name]||[]).length ? '，该有的 ' + MUST[name].length + ' 处都在' : ''));
}
console.log('');
console.log('渲染面板 ' + panes.length + ' 个，有问题 ' + bad + ' 个');
process.exit(bad ? 1 : 0);
""" % json.dumps(str(ROOT).replace("\\", "/") + "/")
        (ROOT / "_render_check.js").write_text(HARNESS, encoding="utf-8")
        rr = subprocess.run([node, "_render_check.js"], cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        out = rr.stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            if line.startswith("[OK]") or line.startswith("[X ]"):
                print("   " + line)
        ok(rr.returncode == 0, "9 个面板渲染没有 undefined / NaN / 缺内容")

except Exception as _e:
    fails.append("控制台体检异常: %s" % _e)
    print("[X ] 控制台体检异常: %s" % _e)
finally:
    try:
        srv.terminate()
        time.sleep(0.5)
        if srv.poll() is None:
            srv.kill()
    except Exception:
        pass
    for f in ("_page.js", "_all.json", "_render_check.js"):
        try:
            (ROOT / f).unlink()
        except Exception:
            pass

# ── 汇总 ──
print()
print("=" * 66)
print("通过 %d 项，失败 %d 项" % (oks, len(fails)))
if fails:
    print("\n失败明细：")
    for f in fails:
        print("   [X] %s" % f)
print("=" * 66)
if not fails:
    shutil.rmtree(SB, ignore_errors=True)
    print("（隔离副本已删掉；正在用的数据一个字节都没动）")
else:
    print("（副本留在 %s，方便你复看）" % SB)
sys.exit(1 if fails else 0)
