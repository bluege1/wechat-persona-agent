"""
声卡回环测试 —— 不依赖微信，单独验证虚拟声卡这条链通不通

原理：往 CABLE Input 播一段 440Hz 正弦波，同时从 CABLE Output 录音，
      再看录到的波形振幅。振幅正常 = 链路通；接近 0 = 链路断。

什么时候用它：
    微信发出来的语音条是空的 / 没声音，
    先用这个排除"声卡链路"这个可能性，再怀疑微信或小信使。

用法（必须用小信使的 python，因为它带 pygame）：
    D:\wechat-persona-agent\小信使\venv\\Scripts\\python.exe 声卡回环测试.py
"""
import array
import math
import os
import subprocess
import sys
import tempfile
import time
import wave

# ffmpeg 可能的位置
FFMPEG_CANDIDATES = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"D:\wechat-persona-agent\驱动\ffmpeg\ffmpeg.exe",
]
CABLE_IN_HINT = "CABLE Input"
CABLE_OUT_DSHOW = "CABLE Output (VB-Audio Virtual Cable)"


def find_ffmpeg():
    for p in FFMPEG_CANDIDATES:
        if os.path.exists(p):
            return p
    # 退回 PATH
    from shutil import which
    return which("ffmpeg")


def main():
    ff = find_ffmpeg()
    if not ff:
        print("[X] 找不到 ffmpeg，无法录音。")
        print("    可以临时把它放到 " + FFMPEG_CANDIDATES[1])
        return 2

    import pygame
    import pygame._sdl2.audio as sdl2_audio

    print("=" * 60)
    print("  声卡回环测试")
    print("=" * 60)

    print("\n[1] 启动 SDL 并枚举播放设备")
    pygame.mixer.init()
    names = list(sdl2_audio.get_audio_device_names(False))
    target = None
    for n in names:
        if CABLE_IN_HINT.lower() in n.lower():
            target = n
            break
    print("    设备列表:", names)
    if not target:
        print("[X] 列表里没有 CABLE Input —— 虚拟声卡没装好")
        return 1
    print("    选中   :", target)

    print("\n[2] 把 mixer 绑定到虚拟声卡")
    pygame.mixer.quit()
    pygame.mixer.init(devicename=target)
    print("    mixer 状态:", pygame.mixer.get_init())

    print("\n[3] 边播测试音边从 CABLE Output 录音（约 4 秒）")
    out = os.path.join(tempfile.gettempdir(), "_daniya_probe.wav")
    if os.path.exists(out):
        os.remove(out)
    rec = subprocess.Popen(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "dshow", "-i", f"audio={CABLE_OUT_DSHOW}",
         "-t", "4", "-ac", "1", "-ar", "16000", out],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(1.0)

    rate, _size, channels = pygame.mixer.get_init()
    seconds = 1.5
    buf = array.array("h")
    for i in range(int(rate * seconds)):
        v = int(12000 * math.sin(2 * math.pi * 440.0 * i / rate))
        for _ in range(channels):
            buf.append(v)
    pygame.mixer.Sound(buffer=buf.tobytes()).play()
    print("    已播放 1.5 秒 440Hz")
    time.sleep(3.2)
    try:
        rec.wait(timeout=15)
    except subprocess.TimeoutExpired:
        rec.kill()
    err = (rec.stderr.read() or b"").decode("utf-8", "replace").strip()
    if err:
        print("    ffmpeg:", err[:300])

    print("\n[4] 分析录音")
    if not os.path.exists(out):
        print("[X] 没录到文件，录音失败")
        return 1
    with wave.open(out, "rb") as w:
        data = w.readframes(w.getnframes())
    a = array.array("h")
    a.frombytes(data)
    if not a:
        print("[X] 录音是空的")
        return 1
    peak = max(abs(x) for x in a)
    rms = math.sqrt(sum(x * x for x in a) / len(a))
    print(f"    峰值 = {peak}    RMS = {rms:.1f}")

    print()
    print("=" * 60)
    if peak > 3000:
        print("  [OK] 声卡链路正常。语音条要是还是空的，")
        print("       问题就不在声卡，去看小信使窗口的日志。")
        rc = 0
    else:
        print("  [X] 声卡链路断了。检查：")
        print("       1. 默认录音设备是不是 CABLE Output")
        print("       2. 驱动 VB-Cable 里的驱动是否正常（设备管理器）")
        rc = 1
    print("=" * 60)
    return rc


if __name__ == "__main__":
    sys.exit(main())
