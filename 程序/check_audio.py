"""
声卡自检

检查三件事：
  1. 虚拟声卡 CABLE Input / CABLE Output 在不在
  2. 系统默认录音设备是不是 CABLE Output（微信从这儿录音，必须对）
  3. 顺手提醒系统默认播放设备的情况

退出码：0 = 全部就绪，1 = 有问题
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio_device import (  # noqa: E402
    E_CAPTURE,
    E_RENDER,
    ROLE_NAMES,
    alive_endpoints,
    current_default,
    registry_endpoints,
)

OK = "[OK]"
BAD = "[X] "
WARN = "[!] "


def name_of(flow, dev_id):
    for d, n, _ in registry_endpoints(flow):
        if d.lower() == str(dev_id).lower():
            return n
    return ""


def main():
    good = True

    renders = alive_endpoints(E_RENDER)
    caps = alive_endpoints(E_CAPTURE)

    cable_in = [x for x in renders if "cable" in x[1].lower() and "input" in x[1].lower()]
    cable_out = [x for x in caps if "cable" in x[1].lower() and "output" in x[1].lower()]

    print("=" * 64)
    print("  达妮娅 · 声卡自检")
    print("=" * 64)
    print()
    print("【1】虚拟声卡是否存在")
    if cable_in:
        print(f"  {OK} 播放端  {cable_in[0][1]}")
    else:
        print(f"  {BAD} 找不到 CABLE Input —— 虚拟声卡的播放端缺失")
        good = False
    if cable_out:
        print(f"  {OK} 录音端  {cable_out[0][1]}")
    else:
        print(f"  {BAD} 找不到 CABLE Output —— 虚拟声卡的录音端缺失")
        good = False

    print()
    print("【2】系统默认录音设备（微信从这里录，必须是 CABLE Output）")
    for role, rn in enumerate(ROLE_NAMES):
        try:
            did = current_default(E_CAPTURE, role)
            nm = name_of(E_CAPTURE, did)
        except Exception as e:
            print(f"  {BAD} {rn:16s} 读取失败：{e}")
            good = False
            continue
        if "CABLE Output" in nm:
            print(f"  {OK} {rn:16s} -> {nm}")
        else:
            print(f"  {BAD} {rn:16s} -> {nm or did}")
            good = False
    if not good:
        print()
        print("     修：双击「1-声卡自检.bat」拉到最下面，有自动修复命令。")

    print()
    print("【3】系统默认播放设备（达妮娅发语音不依赖它，仅作参考）")
    for role, rn in enumerate(ROLE_NAMES[:2]):
        try:
            did = current_default(E_RENDER, role)
            nm = name_of(E_RENDER, did)
        except Exception as e:
            print(f"  {WARN}{rn:16s} 读取失败：{e}")
            continue
        if "CABLE" in nm.upper():
            print(f"  {WARN}{rn:16s} -> {nm}")
        else:
            print(f"  {OK} {rn:16s} -> {nm or did}")
    print("     （如果是 CABLE Input，说明你电脑本身的声音会被吞掉；")
    print("       达妮娅不受影响，因为代码已指定虚拟声卡。想还原电脑声音看说明。）")

    print()
    print("=" * 64)
    if good:
        print("  [结论] 声卡就绪，可以启动。")
    else:
        print("  [结论] 有问题，见上面标 [X] 的行。")
    print("=" * 64)
    return 0 if good else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[X] 自检本身出错了：{e}")
        sys.exit(2)
