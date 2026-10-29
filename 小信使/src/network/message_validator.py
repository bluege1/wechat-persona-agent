"""
共享消息校验模块
从原始 JSON 数据中提取、校验并标准化为 send_queue 所需的命令字典。
WebSocket 和 HTTP 接收器共用此函数。
"""

from typing import Any, Dict, Optional, Tuple


def validate_and_normalize_message(data: Any) -> Tuple[Optional[Dict], Optional[str]]:
    """
    校验并标准化上游消息。

    Returns:
        (normalized_dict, None) — 校验通过，dict 可直接投入 send_queue
        (None, error_message)    — 校验失败，error_message 为中文错误描述
    """
    if not isinstance(data, dict):
        return None, "消息必须是 JSON 对象"

    msg_type = data.get("type")
    target = data.get("target")

    if not target:
        return None, "缺少 target 字段"

    if msg_type == "send_message":
        content = data.get("message")
        if not content:
            return None, "send_message 缺少 message 字段"
        out = {"type": "send_message", "target": target, "message": content}
        # refocus：让发送线程跳过「目标相同就直接粘贴」的捷径，强制重搜一次会话。
        # 用于上层后台通道刚失败、微信输入框丢了键盘焦点的情况
        # （2026-10-04；详见 wechat_sender._ensure_chat_window 的说明）。
        # ⚠ 一定要在这里显式带上 —— 这函数是白名单式的，
        #   原样 return 会把这个字段悄悄吃掉。
        if data.get("refocus"):
            out["refocus"] = True
        return out, None

    elif msg_type == "send_voice":
        voice_data_b64 = data.get("voice_data")
        if not voice_data_b64:
            content = data.get("message")
            if content:
                out = {
                    "type": "send_message",
                    "target": target,
                    "message": content,
                }
                if data.get("refocus"):
                    out["refocus"] = True
                return out, None
            return None, "send_voice 缺少 voice_data 字段"

        voice_format = data.get("voice_format") or data.get("format") or "mp3"
        return {
            "type": "send_voice",
            "target": target,
            "message": data.get("message", ""),
            "voice_data": voice_data_b64,
            "voice_format": voice_format,
        }, None

    elif msg_type == "send_sticker":
        sticker_data_b64 = data.get("sticker_data")
        if not sticker_data_b64:
            return None, "send_sticker 缺少 sticker_data 字段"
        return {
            "type": "send_sticker",
            "target": target,
            "sticker_data": sticker_data_b64,
        }, None

    return None, f"未知指令类型: {msg_type}"
