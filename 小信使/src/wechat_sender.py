"""
微信消息发送器核心模块
负责 UI 自动化、队列调度和各子模块的协调。
"""

import ctypes
import logging
import random
import threading
import time
from queue import Queue, Empty
from typing import Any, Dict, Optional

import pyperclip
import uiautomation as auto

from .clipboard_utils import copy_file_to_clipboard
from .mouse_wanderer import MouseWanderer
from .voice_sender import VoiceSender, AUDIO_AVAILABLE
from .network.ws_client import WSCommandReceiver
from .network.http_server import HTTPCommandReceiver

logger = logging.getLogger("WeChatSender")


class WeChatSender:
    """
    微信消息发送器
    组合鼠标漫游、WebSocket 指令接收、语音发送、剪贴板工具等模块
    通过内部队列统一调度文字、语音、表情包三种消息类型
    """

    def __init__(self, config: dict = None, debug_mode: bool = False):
        if config is None:
            config = {}

        # 线程安全控制
        self.ui_lock = threading.Lock()
        self.send_queue: Queue[Dict[str, Any]] = Queue()
        self._stop_event = threading.Event()

        self.last_target: Optional[str] = None

        # ── 子模块初始化 ──

        # 鼠标漫游
        wanderer_cfg = config.get("wanderer", {})
        self.wanderer = MouseWanderer(
            min_interval=wanderer_cfg.get("min_interval", 10.0),
            max_interval=wanderer_cfg.get("max_interval", 30.0),
            wander_times_range=(
                wanderer_cfg.get("times_min", 1),
                wanderer_cfg.get("times_max", 3),
            ),
        )
        # 【达妮娅懒人包改动】默认关闭。
        # 这是原项目的「反检测」模块：每隔十几秒把物理鼠标指针甩到屏幕随机位置。
        # 它会让你自己的鼠标不受控地乱跳，根本没法正常用电脑
        # （本次就是它造成的）。默认值由 True 改成 False，
        # 想恢复原行为要在 config.yaml 里显式写 wanderer.enabled: true。
        if not debug_mode and wanderer_cfg.get("enabled", False):
            self.wanderer.start()
            logger.warning(
                "[WeeMessenger - 警告] 鼠标漫游已启用 —— 你的鼠标指针会被定时挪动。"
                " 不想要就去 config.yaml 把 wanderer.enabled 改成 false。"
            )
        else:
            logger.info("[WeeMessenger - 提示] 鼠标漫游已禁用（不会动你的鼠标）")

        # 语音发送器（仅当音频可用）
        self.voice_sender: Optional[VoiceSender] = None
        if AUDIO_AVAILABLE:
            self.voice_sender = VoiceSender()

        # 发送工作线程
        self._worker_thread = threading.Thread(target=self._send_worker, daemon=True)
        self._worker_thread.start()

        # 网络指令接收
        if not debug_mode:
            # WebSocket
            ws_cfg = config.get("ws", {})
            if ws_cfg.get("enabled", True):
                ws_url = ws_cfg.get("url", "ws://127.0.0.1:9876")
                self.ws_receiver = WSCommandReceiver(ws_url, self.send_queue, self._stop_event)
                self.ws_receiver.start()
                logger.info(f"[WeeMessenger - 提示] WebSocket 已连接: {ws_url}")
            else:
                self.ws_receiver = None
                logger.info("[WeeMessenger - 提示] WebSocket 已禁用")

            # HTTP
            http_cfg = config.get("http", {})
            if http_cfg.get("enabled", True):
                http_host = http_cfg.get("host", "0.0.0.0")
                http_port = http_cfg.get("port", 9877)
                self.http_receiver = HTTPCommandReceiver(
                    http_host, http_port, self.send_queue, self._stop_event
                )
                self.http_receiver.start()
                logger.info(f"[WeeMessenger - 提示] HTTP 服务器已启动: http://{http_host}:{http_port}")
            else:
                self.http_receiver = None
                logger.info("[WeeMessenger - 提示] HTTP 服务器已禁用")

            # 两个模式都禁用时提醒
            if not self.ws_receiver and not self.http_receiver:
                logger.warning("╔════════════════════════════════════════════════════════════╗")
                logger.warning("║  [温馨提示] WebSocket 和 HTTP 均已禁用                     ║")
                logger.warning("║  小信使将无法接收任何外部消息！                            ║")
                logger.warning("║  如需使用，请编辑 config.yaml 启用至少一个模式后重启程序~  ║")
                logger.warning("╚════════════════════════════════════════════════════════════╝")
        else:
            self.ws_receiver = None
            self.http_receiver = None
            logger.info("[WeeMessenger - 提示] 微信发送器初始化完成！（调试模式，跳过网络模块）")

    # ==================== 对外接口 ====================

    def send_message(self, target: str, message: str):
        """程序内直接调用，将文字消息投入发送队列"""
        self.send_queue.put({
            "type": "send_message",
            "target": target,
            "message": message,
        })

    # ==================== 发送工作线程 ====================

    def _send_worker(self):
        """发送工作线程主循环，从队列取指令并分发到对应处理方法"""
        logger.info("[WeeMessenger - 提示] 发送工作线程启动！")
        with auto.UIAutomationInitializerInThread():
            while not self._stop_event.is_set():
                try:
                    cmd = self.send_queue.get(timeout=1)
                    cmd_type = cmd["type"]
                    target = cmd["target"]

                    if cmd_type == "send_message":
                        self._execute_send_message(target, cmd["message"],
                                                   refocus=bool(cmd.get("refocus")))

                    elif cmd_type == "send_voice":
                        message = cmd.get("message", "")
                        # 若前置有文字消息，先发送文字
                        if message:
                            self._execute_send_message(target, message)
                            time.sleep(random.uniform(1.0, 2.0))
                        # 发送语音（若音频不可用则降级为仅文字）
                        if self.voice_sender:
                            status = self.voice_sender.send(
                                target,
                                cmd["voice_data"],
                                cmd["voice_format"],
                                ensure_chat_fn=self._ensure_chat_window,
                                lock=self.ui_lock,
                            )
                            # 注意：status=ok 只表示"按键流程走完了"。
                            # 微信侧到底有没有产生语音条，要靠调用方回读聊天区确认。
                            if status == "ok":
                                logger.info(
                                    "[WeeMessenger - 提示] 语音按键流程完成。"
                                    "若微信里没出现语音条，请检查三件事："
                                    "① 微信→左下角≡→设置→快捷键→「发送语音消息」"
                                    "绑的键是不是 Alt（不是就用环境变量 "
                                    "DANIYA_VOICE_HOTKEY 按虚拟键码配上）；"
                                    "② 微信→设置→语音视频→麦克风是不是 CABLE Output；"
                                    "③ 微信版本要 4.1.9 以上才支持电脑端发语音。"
                                )
                            else:
                                logger.warning(
                                    f"[WeeMessenger - 警告] 语音没能发出，状态={status}"
                                    f"（no_audio=音频库不可用，busy=没抢到UI锁，"
                                    f"error=过程异常）"
                                )
                        else:
                            logger.warning("[WeeMessenger - 警告] 音频不可用，语音发送已跳过")

                    elif cmd_type == "send_sticker":
                        self._execute_send_sticker(target, cmd["sticker_data"])

                    else:
                        logger.error(f"[WeeMessenger - 错误] 未知指令类型: {cmd_type}")

                    # 指令间随机延迟
                    delay = random.randint(1, 3) + random.uniform(0.5, 1.5)
                    logger.info(f"[WeeMessenger - 提示] 指令完成，等待 {delay:.1f} 秒")
                    time.sleep(delay)

                except Empty:
                    continue
                except Exception as e:
                    logger.error(f"[WeeMessenger - 错误] 发送工作异常: {e}", exc_info=True)

    # ==================== UI 自动化（文字发送）====================

    # 微信进程里这些顶层窗口不是主界面（托盘图标、消息泵、输入法……）
    _AUX_CLASSES = (
        'Base_PowerMessageWindow',
        'Qt51514WxTrayIconMessageWindowClass',
        'DisplayICC_SystemMessageWindow',
        'Chrome_SystemMessageWindow',
        'SoPY_Status', 'SoPY_Hint', 'SoPY_UI',
        'MSCTFIME UI', 'Sogou_TSF_UI', 'IME', 'Default IME',
    )
    _MAIN_CLASSES = (
        'WeChatMainWndForPC', 'mmui::MainWindow', 'mmui::XView',
        'Qt51514QWindowIcon', 'Qt51514QWindowForeign', 'Qt5QWindowIcon',
    )

    @staticmethod
    def _enum_wechat_windows():
        """EnumWindows 枚举微信进程的全部顶层窗口 —— 包括隐藏的。

        ★ 2026-10-20 立。UIA 的 GetRootControl().GetChildren() 里
          【没有隐藏窗口】。微信被收进托盘（ShowWindow(SW_HIDE)）之后
          按进程名兜底也找不到它，整条发送链路只会报「未找到微信窗口」。
        """
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        sep = chr(92)
        rows = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _img(pid: int) -> str:
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                return ''
            try:
                buf = ctypes.create_unicode_buffer(1024)
                sz = ctypes.c_ulong(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
                    return buf.value.lower()
                return ''
            finally:
                k32.CloseHandle(h)

        def _cb(hwnd, _lp):
            try:
                pid = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if _img(int(pid.value)).rsplit(sep, 1)[-1] not in ('weixin.exe', 'wechat.exe'):
                    return True
                cls = ctypes.create_unicode_buffer(256)
                u32.GetClassNameW(hwnd, cls, 256)
                title = ctypes.create_unicode_buffer(256)
                u32.GetWindowTextW(hwnd, title, 256)
                rows.append((int(hwnd), cls.value, title.value,
                             bool(u32.IsWindowVisible(hwnd)),
                             bool(u32.IsIconic(hwnd))))
            except Exception:
                pass
            return True

        try:
            u32.EnumWindows(proto(_cb), 0)
        except Exception:
            pass
        return rows

    @classmethod
    def _recover_hidden(cls) -> Optional[auto.Control]:
        """把「收进托盘」的微信主窗口显示回来（默认不抢焦点）。

        只在 UIA 完全找不到窗口时调用。返回控件对象；救不回来返回 None。
        """
        rows = cls._enum_wechat_windows()
        best, score0 = 0, -1
        for hwnd, kls, title, _vis, _ico in rows:
            if not kls or kls in cls._AUX_CLASSES:
                continue
            if kls in cls._MAIN_CLASSES:
                score = 3
            elif title == '微信':
                score = 2
            elif kls.startswith('Qt') or kls.startswith('mmui'):
                score = 1
            else:
                continue
            if score > score0:
                best, score0 = hwnd, score
        if not best:
            return None

        u32 = ctypes.windll.user32
        if u32.IsWindowVisible(best):
            # 可见却不被 UIA 认出来：交给 UIA 用句柄再试一次
            try:
                return auto.ControlFromHandle(best)
            except Exception:
                return None

        try:
            logger.warning('[WeeMessenger - 警告] 微信主窗口被隐藏了（收进托盘）—— '
                           '正在把它显示回来（不抢焦点）')
            u32.ShowWindow(best, 4)          # SW_SHOWNOACTIVATE
            time.sleep(0.35)
            return auto.ControlFromHandle(best)
        except Exception as e:
            logger.error(f'[WeeMessenger - 错误] 恢复隐藏的微信窗口失败: {e}')
            return None

    @staticmethod
    def find_wechat_window() -> Optional[auto.WindowControl]:
        """
        查找微信主窗口。

        ⚠️ 不能写死类名：微信各版本主窗口的窗口类名完全不同——
             微信 3.x / 早期 4.x : WeChatMainWndForPC
             微信 4.x 正常        : mmui::MainWindow / mmui::XView
             微信 4.x 渲染回退    : Qt51514QWindowIcon
                                     （此时 UIA 读不到任何控件，但
                                       本项目的键盘/剪贴板发送仍可工作）
        所以按「候选类名 → 按进程名兜底 → Win32 捞隐藏窗口」三级查找。
        """
        for cls in ('WeChatMainWndForPC', 'mmui::MainWindow', 'mmui::XView',
                    'Qt51514QWindowIcon', 'Qt51514QWindowForeign', 'Qt5QWindowIcon'):
            try:
                w = auto.WindowControl(searchDepth=1, ClassName=cls)
                if w.Exists(0, 0):
                    return w
            except Exception:
                continue

        try:
            w = auto.WindowControl(searchDepth=1, Name='微信')
            if w.Exists(0, 0):
                return w
        except Exception:
            pass

        # 兜底：按进程名找微信的顶层窗口
        try:
            k32 = ctypes.windll.kernel32
            PROC_QUERY_LIMITED = 0x1000
            SKIP = ('SoPY_Status', 'Default IME', 'MSCTFIME UI')

            def _img(pid: int) -> str:
                h = k32.OpenProcess(PROC_QUERY_LIMITED, False, pid)
                if not h:
                    return ''
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    sz = ctypes.c_ulong(1024)
                    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
                        return buf.value.lower()
                    return ''
                finally:
                    k32.CloseHandle(h)

            cands = []
            for c in auto.GetRootControl().GetChildren():
                try:
                    exe = _img(c.ProcessId).rsplit('\\', 1)[-1]
                    if exe not in ('weixin.exe', 'wechat.exe'):
                        continue
                    cls = c.ClassName or ''
                    if not cls or cls in SKIP:
                        continue
                    cands.append(c)
                except Exception:
                    continue
            if cands:
                cands.sort(key=lambda c: 0 if (c.ClassName or '').startswith('mmui') else 1)
                logger.info(
                    f"[WeeMessenger - 提示] 按进程兜底找到微信窗口，"
                    f"类名={cands[0].ClassName!r}"
                )
                return cands[0]
        except Exception as e:
            logger.warning(f"[WeeMessenger - 警告] 按进程兜底找窗口失败: {e}")

        # ★ 2026-10-20：走到这儿说明 UIA 连窗口都看不见了 —— 多半是被收进托盘。
        return WeChatSender._recover_hidden()

    @staticmethod
    def _activate(window) -> bool:
        """
        把微信窗口恢复出来并置前。

        微信最小化时会被挪到 (-32000, -32000)，此时 SetActive() 的
        SetForegroundWindow 会失败，必须先 ShowWindow(SW_RESTORE)；
        被收进托盘时是 SW_HIDE，得用 SW_SHOW（SW_RESTORE 对隐藏窗口不生效）。

        返回 True = 【现在前台确实是微信】。上层拿它当发按键前的闸。
        """
        try:
            hwnd = int(window.NativeWindowHandle or 0)
        except Exception:
            hwnd = 0
        if not hwnd:
            try:
                window.SetActive()
            except Exception:
                pass
            return False

        u32 = ctypes.windll.user32
        try:
            if u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, 9)              # SW_RESTORE
            elif not u32.IsWindowVisible(hwnd):
                u32.ShowWindow(hwnd, 5)              # SW_SHOW
            u32.BringWindowToTop(hwnd)
            u32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
        except Exception:
            pass
        try:
            window.SetActive()
        except Exception:
            pass
        time.sleep(0.1)

        if u32.GetForegroundWindow() != hwnd:
            # 被系统前台锁拒绝：挂到前台线程上再试一次
            try:
                k32 = ctypes.windll.kernel32
                fg = u32.GetForegroundWindow()
                tid_self = k32.GetCurrentThreadId()
                tid_fg = u32.GetWindowThreadProcessId(fg, None)
                tid_wx = u32.GetWindowThreadProcessId(hwnd, None)
                if tid_fg:
                    u32.AttachThreadInput(tid_self, tid_fg, True)
                if tid_wx:
                    u32.AttachThreadInput(tid_self, tid_wx, True)
                u32.BringWindowToTop(hwnd)
                u32.SetForegroundWindow(hwnd)
                u32.SetFocus(hwnd)
                if tid_wx:
                    u32.AttachThreadInput(tid_self, tid_wx, False)
                if tid_fg:
                    u32.AttachThreadInput(tid_self, tid_fg, False)
                time.sleep(0.12)
            except Exception:
                pass

        return u32.GetForegroundWindow() == hwnd

    def _ensure_foreground(self, window) -> bool:
        """发按键之前必须确认微信在前台。

        ★ 2026-10-20 立。SendKeys 是【全局】键盘模拟 —— 前台只要不是微信，
          Ctrl+A / Delete / Ctrl+V / Enter 就会原样打进你正在用的窗口
          （在资源管理器里 Ctrl+A + Delete 是删文件）。
          所以宁可这一条不发，也不能盲按。
        """
        try:
            hwnd = int(window.NativeWindowHandle or 0)
        except Exception:
            hwnd = 0
        if hwnd and ctypes.windll.user32.GetForegroundWindow() == hwnd:
            return True
        if self._activate(window):
            return True
        logger.error('[WeeMessenger - 错误] 微信没能到前台，这一条不发按键'
                     '（硬发会把字打进你正在用的窗口）。'
                     '多半是窗口被收进托盘 / 被别的程序锁了焦点')
        return False

    def _close_stray_search(self):
        """清场：**不再按 Esc**，只把窗口从托盘里救回来。

        ★ 2026-10-12 立。当时的坑：
           Ctrl+F 之后没清搜索框、粘贴完只等 0.01 秒就回车，
           那一下回车落进了「搜一搜」——右侧弹出结果面板并一直挂着。
           面板一挂，聊天区被挤窄，机器人看错整个界面（自己的气泡判成对方的、
           回读读到搜索结果、前台通道又补发一遍 → 重复气泡 → 刷屏一小时）。
           当时的修法是「发送前按两下 Esc 清场」。

        ★ 2026-10-20 **这个修法本身就是新事故的根因，已废弃**：
           用户实测（10-20 08:54）：在「搜索 / 搜一搜」页面里按 Esc，
           微信**第一下就把主窗口关掉了**（ShowWindow(SW_HIDE)，
           IsWindowVisible=0，坐标还在原处 —— 不是最小化）。
           窗口一隐藏，UIA 再也枚举不到它，`find()` 返回 None，
           后面每条都「未找到微信窗口」，读消息也全瞎。
           实测复现：Ctrl+F → 输入词 → 回车进搜索页 → 按 Esc → visible 变 0。
           而**快速发送路径**恰好要求「先清场」，于是它专门在最需要清场的时候
           把微信关掉。

           现在改成：
             · 这里一下 Esc 都不按；
             · 真正卡在搜索页时，由 bridge 用鼠标点搜索框重新搜（它读得懂画面，
               确认「进没进会话」才继续），小信使不盲操作；
             · 这里只负责「窗口别是隐形的」—— 隐形了就显示回来。
        """
        try:
            window = self.find_wechat_window()
            if window is None:
                return
            try:
                hwnd = int(window.NativeWindowHandle or 0)
            except Exception:
                hwnd = 0
            if hwnd and not ctypes.windll.user32.IsWindowVisible(hwnd):
                self._activate(window)
        except Exception as e:
            logger.warning(f"[WeeMessenger - 警告] 发送前检查窗口失败: {e}")

    def _ensure_chat_window(self, target_name: str, force: bool = False):
        """
        确保当前微信窗口已打开与 target_name 的聊天。
        若目标与上次相同则复用窗口，否则通过 Ctrl+F 搜索进入。

        force=True 时【即使目标与上次相同也重新搜一遍】。
        为什么需要它（2026-10-04）：微信的输入框是会丢键盘焦点的 ——
        你在聊天区点一下消息，Qt 就把焦点移出输入框了，这时往它投按键
        （无论是后台的 WM_CHAR 还是前台的 Ctrl+V）都会落空，消息静默消失。
        能把它救回来的动作只有「重新走一遍 Ctrl+F 搜索 → 进会话」，
        那一下微信才会把焦点送回输入框 —— 所以后台没发成时由上层要求 force。
        """
        window = self.find_wechat_window()
        if not window:
            raise RuntimeError("[WeeMessenger - 错误] 未找到微信窗口")
        # ★ 2026-10-20：下面的 Ctrl+F / Ctrl+A / Ctrl+V / Enter 全是全局按键，
        #   前台不是微信就会打到别的窗口里去。先确认它真的在前台。
        if not self._ensure_foreground(window):
            raise RuntimeError("[WeeMessenger - 错误] 微信窗口没能到前台，放弃本次发送")
        time.sleep(random.uniform(0.1, 0.3))

        if self.last_target == target_name and not force:
            logger.info(f"[WeeMessenger - 提示 目标与上次相同 ({target_name})，复用当前聊天窗口")
            return

        if force and self.last_target == target_name:
            logger.info(f"[WeeMessenger - 提示] {target_name}：强制重搜一次"
                        f"（多半是输入框丢了焦点，重搜会把焦点找回来）")

        logger.info(f"[WeeMessenger] 搜索联系人 '{target_name}'")
        auto.SendKeys('{Ctrl}f')
        time.sleep(random.uniform(0.3, 0.5))
        # ★ 2026-10-12：搜索框里可能还留着上一回的关键词（微信不会自己清），
        #   直接 Ctrl+V 会拼成「上次的词+这次的名字」，一条都搜不到 → 回车落进搜一搜。
        #   先全选删掉，保证框里是干净的。
        auto.SendKeys('{Ctrl}a')
        time.sleep(0.08)
        auto.SendKeys('{Delete}')
        time.sleep(0.08)
        pyperclip.copy(target_name)
        auto.SendKeys('{Ctrl}v')
        # ★ 2026-10-12：原来是 0.01 秒 —— 微信的候选列表根本来不及渲染，
        #   回车就落进了「搜一搜」（右侧弹出结果面板，从此把聊天区挤歪）。
        time.sleep(0.45)
        auto.SendKeys('{Enter}')
        time.sleep(random.uniform(0.8, 1.0))
        self.last_target = target_name

    def _clear_input_box(self):
        """粘贴之前把输入框清空、光标顶到行尾（前台通道）。

        ★ 2026-10-05 加：以前这里是直接 Ctrl+V，**不清框**。
          上一条要是还留在框里（后台投字失败，或者回车没生效），
          Ctrl+V 就会插在它前面，微信里冒出一条缝合怪 ——
          实测：「吹得我头发乱乱的刚从天台下来 风好大」（用户截图实证）。
        """
        try:
            auto.SendKeys('{Ctrl}a')
            time.sleep(0.06)
            auto.SendKeys('{Delete}')
            time.sleep(0.06)
            auto.SendKeys('{End}')
            time.sleep(0.06)
        except Exception as e:
            logger.warning(f"[WeeMessenger - 警告] 清空输入框失败: {e}")

    def _execute_send_message(self, target: str, message: str, refocus: bool = False):
        """
        执行文字消息的物理模拟发送：
        1. 查找并激活微信窗口
        2. 若目标与上次相同则快速粘贴发送；否则通过 Ctrl+F 搜索后发送

        refocus=True 时跳过「快速发送」那条捷径，强制重搜一次会话 ——
        用于后台通道刚失败、输入框多半丢了焦点的情况（见 _ensure_chat_window）。
        """
        if not self.ui_lock.acquire(timeout=5.0):
            logger.error("[WeeMessenger - 错误] 获取UI锁超时，取消发送")
            return

        try:
            time.sleep(random.uniform(0.5, 2.0))
            window = self.find_wechat_window()
            if not window:
                logger.error("[WeeMessenger - 错误] 未找到微信窗口"
                             "（连托盘里的也捞过了），发送中止")
                return
            if not self._ensure_foreground(window):
                return
            time.sleep(random.uniform(0.1, 0.3))
            # ★ 2026-10-12：发送前先确认窗口在——上一轮要是把微信留在了
            #   搜索 / 搜一搜页面，这里会检查它有没有被藏起来。
            # ★ 2026-10-20：**不再用 Esc 清场**（在搜索页里 Esc 会关掉主窗口，
            #   实测把整条链路弄瞎过）。卡在搜索页由 bridge 点搜索框切回来。
            self._close_stray_search()
            # 确认窗口还在前台，不然就这一条不发，绝不盲按。
            if not self._ensure_foreground(window):
                return

            fast_sent = False
            if self.last_target == target and not refocus:
                logger.info(f"[WeeMessenger - 提示] 目标与上次相同 ({target})，尝试快速发送")
                try:
                    self._clear_input_box()
                    pyperclip.copy(message)
                    time.sleep(random.uniform(0.1, 0.3))
                    auto.SendKeys('{Ctrl}v')
                    sleep_time = min(len(message) * 0.02, 2.0) + random.uniform(0.5, 1.5)
                    time.sleep(sleep_time)
                    auto.SendKeys('{Enter}')
                    logger.info(f"[WeeMessenger - 提示] 快速发送完成: {target}")
                    fast_sent = True
                except Exception as e:
                    logger.warning(f"[WeeMessenger - 警告] 快速发送失败: {e}")

            if not fast_sent:
                self._ensure_chat_window(target, force=refocus)

                # 粘贴消息并发送
                self._clear_input_box()
                pyperclip.copy(message)
                time.sleep(random.uniform(0.3, 0.5))
                auto.SendKeys('{Ctrl}v')
                sleep_time = min(len(message) * 0.02, 2.0) + random.uniform(0.5, 1.5)
                time.sleep(sleep_time)
                auto.SendKeys('{Enter}')
                logger.info(f"[WeeMessenger - 提示] 完整发送完成: {target}")

            self.last_target = target
            time.sleep(random.uniform(0.5, 1.5))

        except Exception as e:
            logger.error(f"[WeeMessenger - 错误] 模拟发送文字异常: {e}", exc_info=True)
        finally:
            self.ui_lock.release()

    # ==================== 表情包发送 ====================

    def _execute_send_sticker(self, target: str, sticker_data_b64: str):
        """
        发送微信表情包（GIF）：
        1. 解码 base64 并保存为临时 .gif 文件
        2. 通过 CF_HDROP 将文件放入剪贴板
        3. 激活聊天窗口并粘贴发送
        """
        import base64
        import os
        import tempfile

        if not self.ui_lock.acquire(timeout=10.0):
            logger.error("[WeeMessenger - 错误] 获取UI锁超时，取消表情包发送")
            return

        tmp_path = None
        try:
            sticker_bytes = base64.b64decode(sticker_data_b64)
            with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(sticker_bytes)

            logger.info(f"[WeeMessenger] 表情包临时文件: {tmp_path}")

            self._ensure_chat_window(target)
            # ★ 2026-10-20：粘贴靠的是全局按键，先确认微信在前台
            window = self.find_wechat_window()
            if not window or not self._ensure_foreground(window):
                return
            copy_file_to_clipboard(tmp_path)
            time.sleep(random.uniform(0.1, 0.3))

            auto.SendKeys('{Ctrl}v')
            time.sleep(random.uniform(1.0, 2.0))  # 等待微信加载预览
            auto.SendKeys('{Enter}')
            time.sleep(random.uniform(0.5, 1.0))

            logger.info(f"[WeeMessenger - 提示] 表情包已发送给: {target}")

        except Exception as e:
            logger.error(f"[WeeMessenger - 错误] 发送表情包异常: {e}", exc_info=True)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.warning(f"[WeeMessenger - 警告] 清理临时表情包文件失败: {e}")
            self.ui_lock.release()

    # ==================== 生命周期 ====================

    def shutdown(self):
        """安全关闭所有子模块和线程"""
        logger.info("[WeeMessenger - 提示] 正在关闭微信发送器……")
        self._stop_event.set()
        if self.ws_receiver:
            self.ws_receiver.close()
            self.ws_receiver.join(timeout=3.0)
        if self.http_receiver:
            self.http_receiver.close()
            self.http_receiver.join(timeout=3.0)
        self.wanderer.stop()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        logger.info("[WeeMessenger - 提示] 微信发送器已关闭！")
