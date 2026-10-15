# -*- coding: utf-8 -*-
"""
微信窗口定位（版本无关）
========================

背景——踩过的坑：
  微信 PC 端不同版本，主窗口的「窗口类名」完全不同：

    微信 3.x / 早期 4.x   WeChatMainWndForPC     ← 老的自动化工具都用这个
    微信 4.x 正常情况      mmui::MainWindow
                          mmui::XView
    微信 4.x 异常情况      Qt51514QWindowIcon     ← 整个 UI 被当成一块画布自绘
                          Qt51514QWindowForeign     UIA 树里什么都没有

  所以只认一个类名一定会挂。本模块按「候选类名 → 按进程兜底」的顺序找，
  并且能判断出「拿到了窗口但读不到控件」这种最坑的情况，给出明确处置建议。

被 bridge.py 和自检脚本共用。
"""

import ctypes
import time

import uiautomation as auto

# 已知的微信主窗口类名，按「优先度」排序
CLASS_CANDIDATES = (
    "WeChatMainWndForPC",     # 3.x / 早期 4.x
    "mmui::MainWindow",       # 4.x 正常
    "mmui::XView",            # 4.x 正常（新版）
    "Qt51514QWindowIcon",     # 4.x 渲染回退（能定位窗口，但读不到控件）
    "Qt51514QWindowForeign",
    "Qt5QWindowIcon",
)

# 进程名（微信 4.x 叫 Weixin.exe，3.x 叫 WeChat.exe）
PROC_NAMES = ("weixin.exe", "wechat.exe")

_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32

SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def pid_image_name(pid: int) -> str:
    """拿进程的 exe 全路径（不依赖 psutil）。"""
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(h)


def is_wechat_pid(pid: int) -> bool:
    path = pid_image_name(pid).lower().replace("/", "\\")
    return bool(path) and path.rsplit("\\", 1)[-1] in PROC_NAMES


def _desktop_windows():
    """桌面上的所有顶层窗口（uiautomation 控件对象）。"""
    try:
        return auto.GetRootControl().GetChildren()
    except Exception:
        return []


def find(timeout: float = 0.0, interval: float = 1.0):
    """
    找微信主窗口，返回 uiautomation 的 WindowControl；找不到返回 None。

    timeout>0 时会重试，直到超时。
    """
    deadline = time.time() + timeout
    while True:
        # 1) 先按已知类名直接找（最快）
        for cls in CLASS_CANDIDATES:
            try:
                w = auto.WindowControl(searchDepth=1, ClassName=cls)
                if w.Exists(0, 0):
                    return w
            except Exception:
                pass

        # 2) 再按标题找（旧版标题就是「微信」）
        try:
            w = auto.WindowControl(searchDepth=1, Name="微信")
            if w.Exists(0, 0):
                return w
        except Exception:
            pass

        # 3) 兜底：按进程名筛顶层窗口，优先 mmui/WeChatMainWnd，其次任何窗口
        cands = []
        for w in _desktop_windows():
            try:
                pid = w.ProcessId
                if not is_wechat_pid(pid):
                    continue
                cls = w.ClassName or ""
                nm = w.Name or ""
                # 过滤掉输入法候选框之类的杂窗口
                if cls in ("SoPY_Status", "Default IME", "MSCTFIME UI"):
                    continue
                if not cls:
                    continue
                cands.append((cls, nm, w))
            except Exception:
                continue
        if cands:
            cands.sort(key=lambda c: (0 if c[0].startswith("mmui") else
                                      1 if c[0] == "WeChatMainWndForPC" else 2))
            return cands[0][2]

        if time.time() >= deadline:
            return None
        time.sleep(interval)


def activate(win) -> bool:
    """把微信窗口恢复并置前（微信最小化时会躲到 -32000 坐标去）。"""
    try:
        hwnd = win.NativeWindowHandle
    except Exception:
        hwnd = 0
    if hwnd:
        try:
            _u32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
            _u32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            time.sleep(0.2)
        except Exception:
            pass
    try:
        win.SetActive()
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# 控件树探测
# ══════════════════════════════════════════════════════════════

def count_tree(ctrl, max_depth: int = 8, max_nodes: int = 400):
    """返回 (节点总数, [(ControlType, Name, ClassName), ...] 有文字的节点)。"""
    n = 0
    texts = []

    def walk(c, d):
        nonlocal n
        if d > max_depth or n > max_nodes:
            return
        try:
            kids = c.GetChildren()
        except Exception:
            return
        for k in kids:
            n += 1
            if n > max_nodes:
                return
            try:
                nm = (k.Name or "").strip()
                if nm:
                    texts.append((k.ControlTypeName, nm, k.ClassName or ""))
            except Exception:
                pass
            walk(k, d + 1)

    walk(ctrl, 1)
    return n, texts


def is_canvas_window(win, node_threshold: int = 8) -> bool:
    """
    判断是不是「拿到窗口但读不到控件」的画布情况。
    典型：只有 1~2 个节点，且类名是 Qt 开头。
    """
    try:
        cls = win.ClassName or ""
    except Exception:
        cls = ""
    n, _ = count_tree(win, max_depth=5, max_nodes=60)
    if n <= node_threshold and (cls.startswith("Qt") or not cls):
        return True
    return False


def diagnose(win) -> dict:
    """给窗口做一次完整体检，返回结构化结论。"""
    try:
        cls = win.ClassName or ""
        nm = win.Name or ""
    except Exception:
        cls, nm = "", ""
    nodes, texts = count_tree(win)
    return {
        "class": cls,
        "title": nm,
        "nodes": nodes,
        "texts": texts,
        "canvas": is_canvas_window(win),
    }
