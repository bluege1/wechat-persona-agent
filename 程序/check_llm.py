# -*- coding: utf-8 -*-
"""
达妮娅 · 大脑自检
==================
只测「大脑」这一环：config.yaml 里的 base_url / api_key / model 能不能正常聊天。
跟微信、声卡、OCR 全都没关系。

用法：
    python check_llm.py

排错时第一件事就跑这个——聊天出问题时，先确认到底是「大脑不通」还是「收发不通」。
"""

import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from 角色 import ROOT, BASE, PROG   # 惰性 ROOT：指向"当前角色"的数据包
try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8", errors="replace")
    sys.stderr.reconfigure(encoding=sys.stderr.encoding or "utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(ROOT))
from bridge import Brain  # noqa: E402


def mask(s: str) -> str:
    s = str(s or "")
    if not s:
        return "<空>"
    if len(s) <= 12:
        return s
    return s[:8] + "*" * (len(s) - 12) + s[-4:]


# ★ 自检不许动她的账本。
#   踩过的坑（2026-09-21）：这个脚本原来直接 Brain(cfg) + reply() 一句测试词，
#   结果那句测试词**被写进了 memory.json**——
#   她每轮都会把最近几条当「真的发生过的事」回放一遍，于是历史里多了一句
#   「在吗？用你自己的语气跟我打个招呼」，而且那句话里带「你自己」三个字，
#   正好命中了世界书里「你自己长什么样」那张卡的关键词 → 之后好几轮，
#   他随便说点什么，控制台都会翻出她的外貌卡。用户原话：「这张卡是瞎几把翻的？」
#   所以：自检把**所有会写的状态文件**都指到一个临时目录，跑完就删，
#   一个字节都不碰她的 memory.json / 关系.json / 分享.json / 今天.json。
SANDBOX_FILES = [
    ("memory", "file"),
    ("share", "state_file"),
    ("life", "file"),
    ("story", "file"),
    ("sticker", "state_file"),
]


def sandbox_cfg(cfg: dict, box: Path) -> dict:
    """把配置里所有「程序会写的文件」改成临时目录下的路径。"""
    import copy
    c = copy.deepcopy(cfg)
    box.mkdir(parents=True, exist_ok=True)

    def _redirect(d: dict, key: str, name: str) -> None:
        d[key] = str(box / name)

    for sect, key in SANDBOX_FILES:
        s = c.get(sect)
        if isinstance(s, dict) and s.get(key):
            _redirect(s, key, Path(str(s[key])).name)
    # 关系进度：两个键是同一件事（引擎读 state_file，老路径读 relation_state）
    c["relation_state"] = str(box / "关系.json")
    r = c.get("relation")
    if isinstance(r, dict):
        r["state_file"] = str(box / "关系.json")
    # 复盘：目录整个换掉（免得攒够轮数时把 总结.json / 备忘录.md 重写一遍）
    j = c.get("journal")
    if isinstance(j, dict):
        j["dir"] = str(box / "记忆")
        j["enabled"] = False          # 自检不写复盘，直接关掉
    return c


def main() -> int:
    cfg_file = ROOT / "config.yaml"
    print("=" * 62)
    print("达妮娅 · 大脑自检")
    print("=" * 62)
    print()

    if not cfg_file.exists():
        print("[X] 找不到 config.yaml")
        return 1

    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    llm = cfg.get("llm") or {}

    base = str(llm.get("base_url", "")).strip()
    model = str(llm.get("model", "")).strip()
    key = str(llm.get("api_key", "")).strip()

    print("当前配置：")
    print(f"  base_url : {base or '<空>'}")
    print(f"  model    : {model or '<空>'}")
    print(f"  api_key  : {mask(key)}")
    print()

    bad = False
    if not key or not key.startswith("sk-"):
        print("[X] api_key 没填，或者不是 sk- 开头。")
        print("    在「2-改配置.bat」里改，或直接编辑 config.yaml。")
        bad = True
    if not base:
        print("[X] base_url 是空的。")
        bad = True
    elif "cloud.siliconflow.cn" in base:
        print("[!] base_url 写成了 cloud.siliconflow.cn —— 那是官网控制台，返回的是网页。")
        print("    正确写法：https://api.siliconflow.cn/v1")
        print("    （程序会自动纠正并继续，但还是建议你自己改过来）")
    if not model:
        print("[X] model 是空的。")
        bad = True
    elif "/" not in model and "siliconflow" in base:
        print(f"[!] 硅基流动的模型名要带厂商前缀，DeepSeek 系列应该写成 deepseek-ai/{model}")
    if bad:
        print()
        return 1

    # ★ 自检不许动她的账本（原因见上面 SANDBOX_FILES 那段）。
    box = Path(tempfile.gettempdir()) / "达妮娅-测大脑"
    shutil.rmtree(box, ignore_errors=True)
    sbox = sandbox_cfg(cfg, box)
    print("-" * 62)
    print("实际发一句过去试试……")
    print("（用的是临时记忆 %s，跑完就删 ——" % box)
    print("  她的 memory.json / 关系.json / 分享.json 一个字节都不会动）")
    print("-" * 62)
    try:
        try:
            brain = Brain(sbox)
            # ★ 测试词本身也别带「你自己」这种泛用词 —— 万一哪天漏了沙箱，
            #   它留在历史里就会变成世界书的误命中（这坑真踩过）。
            answer = brain.reply("在吗？用你平时说话的样子跟我打个招呼，一句话就好。")
        except Exception as e:
            print()
            print("[X] 没通。原因：")
            for line in str(e).splitlines():
                print("    " + line)
            print()
            print("  对照检查：")
            print("    · base_url  必须是 https://api.siliconflow.cn/v1（api，不是 cloud）")
            print("    · model     必须带厂商前缀，如 deepseek-ai/DeepSeek-V4-Flash")
            print("    · api_key   去 https://cloud.siliconflow.cn 控制台「API 密钥」页重新复制")
            return 1

        print()
        print(f"  [达妮娅] {answer}")
        print()
        print("-" * 62)
        print("[OK] 大脑通了。可以放心去跑「一键启动.bat」。")
        print("-" * 62)
        return 0
    finally:
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(1)
