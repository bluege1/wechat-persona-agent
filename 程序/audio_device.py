"""
Windows 默认音频设备 查询 / 切换
纯 comtypes 实现，不需要装任何第三方工具（nircmd / AudioDeviceCmdlets 都不需要）。

用法:
    python audio_device.py list                       列出所有播放/录音设备
    python audio_device.py get                        打印当前默认设备
    python audio_device.py set-play "CABLE Input"     把默认播放设为 CABLE Input
    python audio_device.py set-rec  "CABLE Output"    把默认录音设为 CABLE Output

原理:
    - 设备清单从注册表 HKLM\\...\\MMDevices\\Audio\\{Render|Capture} 读
    - 默认设备通过未公开的 COM 接口 IPolicyConfig::SetDefaultEndpoint 设置
    - 三个 role（Console / Multimedia / Communications）会一起设置
"""

import sys
import winreg

import comtypes
from comtypes import GUID, COMMETHOD, HRESULT, IUnknown
from ctypes import POINTER, c_void_p, c_uint32, c_wchar_p, c_int, c_ulong

# ---------------------------------------------------------------- COM 接口定义

class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate",
                  (["in"], POINTER(GUID), "iid"),
                  (["in"], c_ulong, "dwClsCtx"),
                  (["in"], c_void_p, "pActivationParams"),
                  (["out"], POINTER(c_void_p), "ppInterface")),
        COMMETHOD([], HRESULT, "OpenPropertyStore",
                  (["in"], c_ulong, "stgmAccess"),
                  (["out"], POINTER(c_void_p), "ppProperties")),
        COMMETHOD([], HRESULT, "GetId",
                  (["out"], POINTER(c_wchar_p), "ppstrId")),
        COMMETHOD([], HRESULT, "GetState",
                  (["out"], POINTER(c_ulong), "pdwState")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], c_uint32, "dataFlow"),
                  (["in"], c_ulong, "dwStateMask"),
                  (["out"], POINTER(c_void_p), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], c_uint32, "dataFlow"),
                  (["in"], c_uint32, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        COMMETHOD([], HRESULT, "GetDevice",
                  (["in"], c_wchar_p, "pwstrId"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
        COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback",
                  (["in"], c_void_p, "pClient")),
        COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback",
                  (["in"], c_void_p, "pClient")),
    ]


class IPolicyConfig(IUnknown):
    """未公开接口，Windows Vista 至今可用，用来设置默认端点。"""
    _iid_ = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetMixFormat",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["out"], POINTER(c_void_p), "ppFormat")),
        COMMETHOD([], HRESULT, "GetDeviceFormat",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_int, "bDefault"),
                  (["out"], POINTER(c_void_p), "ppFormat")),
        COMMETHOD([], HRESULT, "ResetDeviceFormat",
                  (["in"], c_wchar_p, "pszDeviceName")),
        COMMETHOD([], HRESULT, "SetDeviceFormat",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_void_p, "pEndpointFormat"),
                  (["in"], c_void_p, "pMixFormat")),
        COMMETHOD([], HRESULT, "GetProcessingPeriod",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_int, "bDefault"),
                  (["out"], POINTER(c_void_p), "pmftDefaultPeriod"),
                  (["out"], POINTER(c_void_p), "pmftMinimumPeriod")),
        COMMETHOD([], HRESULT, "SetProcessingPeriod",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_void_p, "pmftPeriod")),
        COMMETHOD([], HRESULT, "GetShareMode",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["out"], POINTER(c_void_p), "pMode")),
        COMMETHOD([], HRESULT, "SetShareMode",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_void_p, "pMode")),
        COMMETHOD([], HRESULT, "GetPropertyValue",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_void_p, "key"),
                  (["out"], POINTER(c_void_p), "pv")),
        COMMETHOD([], HRESULT, "SetPropertyValue",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_void_p, "key"),
                  (["in"], c_void_p, "pv")),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_uint32, "role")),
        COMMETHOD([], HRESULT, "SetEndpointVisibility",
                  (["in"], c_wchar_p, "pszDeviceName"),
                  (["in"], c_int, "bVisible")),
    ]


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
CLSID_PolicyConfigClient = GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")

E_RENDER, E_CAPTURE = 0, 1
ROLE_NAMES = ["Console", "Multimedia", "Communications"]

# ------------------------------------------------------------- 注册表读取设备

MMDEV_BASE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
PKEY_FRIENDLY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
PKEY_DESC = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"

PREFIX = {E_RENDER: "{0.0.0.00000000}.", E_CAPTURE: "{0.0.1.00000000}."}


def registry_endpoints(flow):
    """返回 [(完整设备ID, 显示名, 描述名), ...]，只含当前接上的设备。"""
    kind = "Render" if flow == E_RENDER else "Capture"
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{MMDEV_BASE}\\{kind}")
    except OSError:
        return out
    i = 0
    while True:
        try:
            guid_key = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        name = desc = ""
        try:
            with winreg.OpenKey(root, guid_key + r"\Properties") as pk:
                try:
                    name = winreg.QueryValueEx(pk, PKEY_FRIENDLY)[0]
                except OSError:
                    pass
                try:
                    desc = winreg.QueryValueEx(pk, PKEY_DESC)[0]
                except OSError:
                    pass
        except OSError:
            pass
        out.append((PREFIX[flow] + guid_key, name, desc))
    winreg.CloseKey(root)
    return out


# ------------------------------------------------------------------ COM 帮助

def _enumerator():
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        # 线程模型已被 comtypes 导入时设好，忽略即可
        pass
    return comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator, interface=IMMDeviceEnumerator,
        clsctx=comtypes.CLSCTX_ALL,
    )


def alive_endpoints(flow):
    """用 MMDeviceEnumerator 校验 DeviceID 是否真实存在（同时验证 ID 拼接格式）。"""
    en = _enumerator()
    good = []
    for dev_id, name, desc in registry_endpoints(flow):
        try:
            dev = en.GetDevice(dev_id)
        except Exception:
            continue
        real = None
        try:
            real = dev.GetId()
        except Exception:
            pass
        if real:
            good.append((real, name, desc))
    return good


def current_default(flow, role):
    en = _enumerator()
    dev = en.GetDefaultAudioEndpoint(flow, role)
    return dev.GetId()


def set_default(dev_id, roles=(0, 1, 2)):
    pc = comtypes.CoCreateInstance(
        CLSID_PolicyConfigClient, interface=IPolicyConfig,
        clsctx=comtypes.CLSCTX_ALL,
    )
    for r in roles:
        pc.SetDefaultEndpoint(dev_id, r)


# --------------------------------------------------------------------- 命令

def _match(items, keyword):
    kw = keyword.lower().replace(" ", "")
    # 支持直接按设备 ID 片段匹配，例如 {d4a2d7b8
    if kw.startswith("{") or "0.0." in kw:
        by_id = [x for x in items if kw in x[0].lower()]
        if by_id:
            return by_id[0]
    exact = [x for x in items if x[1].lower().replace(" ", "") == kw]
    if exact:
        return exact[0]
    sub = [x for x in items if kw in x[1].lower().replace(" ", "")]
    if sub:
        sub.sort(key=lambda x: len(x[1]))
        return sub[0]
    return None


def cmd_list():
    for flow, label in ((E_RENDER, "播放设备 (Render)"), (E_CAPTURE, "录音设备 (Capture)")):
        print(f"\n=== {label} ===")
        items = alive_endpoints(flow)
        if not items:
            print("  （读不到任何设备）")
        for dev_id, name, desc in items:
            print(f"  {name:24s} | {desc:28s} | {dev_id}")
    print()


def cmd_get():
    for flow, label in ((E_RENDER, "播放"), (E_CAPTURE, "录音")):
        print(f"\n=== 当前默认{label}设备 ===")
        for role, rname in enumerate(ROLE_NAMES):
            try:
                did = current_default(flow, role)
            except Exception as e:
                print(f"  {rname:16s}: 读取失败 {e}")
                continue
            name = ""
            for d, n, _ in registry_endpoints(flow):
                if d.lower() == did.lower():
                    name = n
                    break
            print(f"  {rname:16s}: {name or '?'}   {did}")
    print()


def _do_set(flow, keyword):
    items = alive_endpoints(flow)
    hit = _match(items, keyword)
    if not hit:
        print(f"[X] 找不到名称包含「{keyword}」的设备。可用设备：")
        for _, n, _ in items:
            print("    - " + n)
        return 1
    dev_id, name, _ = hit
    print(f"[*] 目标设备: {name}  ({dev_id})")
    try:
        set_default(dev_id)
    except Exception as e:
        print(f"[X] 设置失败: {e}")
        return 1
    for role in range(3):
        try:
            now = current_default(flow, role)
        except Exception:
            now = "?"
        flag = "OK " if str(now).lower() == dev_id.lower() else "?? "
        print(f"  {flag}{ROLE_NAMES[role]:16s} -> {now}")
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0].lower()
    if cmd == "list":
        cmd_list()
        return 0
    if cmd == "get":
        cmd_get()
        return 0
    if cmd in ("set-play", "set-rec"):
        if len(args) < 2:
            print("缺少设备名参数")
            return 2
        flow = E_RENDER if cmd == "set-play" else E_CAPTURE
        return _do_set(flow, args[1])
    print(f"未知命令: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
