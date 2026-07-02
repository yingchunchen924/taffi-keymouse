from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pyautogui

try:
    import win32api
except Exception:
    win32api = None

try:
    import win32clipboard
    import win32con
except Exception:
    win32clipboard = None
    win32con = None


CONTROL_CHAR_KEYS = {chr(index): chr(ord("a") + index - 1) for index in range(1, 27)}
SHIFTED_CHAR_KEYS = {
    "~": "`",
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name).strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:90] or "未命名脚本"


def screen_size() -> Tuple[int, int]:
    if win32api:
        try:
            return int(win32api.GetSystemMetrics(0)), int(win32api.GetSystemMetrics(1))
        except Exception:
            pass
    size = pyautogui.size()
    return int(size.width), int(size.height)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def read_clipboard_text(max_chars: int = 20000) -> Optional[str]:
    if not win32clipboard or not win32con:
        return None
    for _attempt in range(3):
        try:
            win32clipboard.OpenClipboard(None)
            try:
                if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return None
                value = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return str(value)[:max_chars]
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.03)
    return None


def write_clipboard_text(text: str) -> bool:
    if not win32clipboard or not win32con:
        return False
    for _attempt in range(3):
        try:
            win32clipboard.OpenClipboard(None)
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(text))
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.03)
    return False


def key_name(key: Any) -> str:
    try:
        if hasattr(key, "char") and key.char:
            char = str(key.char)
            if char in CONTROL_CHAR_KEYS:
                return CONTROL_CHAR_KEYS[char]
            if len(char) == 1 and char.isalpha():
                return char.lower()
            return SHIFTED_CHAR_KEYS.get(char, char)
    except Exception:
        pass
    name = str(getattr(key, "name", str(key))).replace("Key.", "").lower()
    return {
        "ctrl_l": "ctrl",
        "ctrl_r": "ctrl",
        "shift_l": "shift",
        "shift_r": "shift",
        "alt_l": "alt",
        "alt_r": "alt",
        "cmd": "win",
        "cmd_l": "win",
        "cmd_r": "win",
        "esc": "esc",
        "return": "enter",
        "page_up": "pageup",
        "page_down": "pagedown",
        "caps_lock": "capslock",
    }.get(name, name)


def is_corner_failsafe(margin: int = 3) -> bool:
    try:
        x, y = pyautogui.position()
        w, h = screen_size()
        return (
            (x <= margin and y <= margin)
            or (x <= margin and y >= h - margin)
            or (x >= w - margin and y <= margin)
            or (x >= w - margin and y >= h - margin)
        )
    except Exception:
        return False


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    kind = event.get("type", "")
    if kind == "click":
        return {
            "type": "mouse",
            "action": "down" if event.get("pressed", True) else "up",
            "button": event.get("button", "left"),
            "x": int(event.get("x", 0)),
            "y": int(event.get("y", 0)),
            "time": float(event.get("time", 0)),
        }
    if kind == "keydown":
        return {"type": "key", "action": "down", "key": event.get("key", ""), "time": float(event.get("time", 0))}
    if kind == "keyup":
        return {"type": "key", "action": "up", "key": event.get("key", ""), "time": float(event.get("time", 0))}
    out = dict(event)
    out["time"] = float(out.get("time", 0))
    return out


def event_summary(index: int, event: Dict[str, Any]) -> str:
    kind = event.get("type", "?")
    t = f"@{float(event.get('time', 0)):.2f}s"
    if kind == "mouse":
        return f"{index + 1:04d} 鼠标{event.get('action', '')} {event.get('button', 'left')} ({event.get('x', 0)}, {event.get('y', 0)}) {t}"
    if kind == "move":
        return f"{index + 1:04d} 移动 ({event.get('x', 0)}, {event.get('y', 0)}) {t}"
    if kind == "scroll":
        return f"{index + 1:04d} 滚轮 dy={event.get('dy', 0)} {t}"
    if kind == "key":
        return f"{index + 1:04d} 按键{event.get('action', '')} {event.get('key', '')} {t}"
    if kind == "text":
        text = str(event.get("text", "")).replace("\n", "\\n")
        return f"{index + 1:04d} 输入文本 {text[:32]} {t}"
    if kind == "image_click":
        return f"{index + 1:04d} 识图点击 {event.get('image_name', event.get('image', ''))} 等待{event.get('retries', 5)}次 {t}"
    if kind == "verify":
        return f"{index + 1:04d} 识图校验 {event.get('image_name', event.get('image', ''))} 等待{event.get('retries', 3)}次 失败:{event.get('on_fail', 'stop')} {t}"
    if kind == "wait":
        return f"{index + 1:04d} 等待 {event.get('duration', 0)}s {t}"
    if kind == "note":
        return f"{index + 1:04d} 标记 {event.get('text', '')} {t}"
    return f"{index + 1:04d} {kind} {t}"
