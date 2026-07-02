from __future__ import annotations

import logging
import time
from typing import Optional, Set

import pydirectinput
import pyautogui

from .utils import read_clipboard_text, write_clipboard_text

pyautogui.FAILSAFE = True
pydirectinput.FAILSAFE = True
pyautogui.PAUSE = 0.015
pydirectinput.PAUSE = 0.015


KEY_MAP = {
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
}

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


class SafeInputDriver:
    def __init__(self) -> None:
        self.pressed_keys: Set[str] = set()
        self.mouse_buttons: Set[str] = set()

    def map_key(self, key: str) -> Optional[str]:
        raw = str(key)
        if raw in CONTROL_CHAR_KEYS:
            raw = CONTROL_CHAR_KEYS[raw]
        if len(raw) == 1 and raw.isalpha():
            raw = raw.lower()
        raw = SHIFTED_CHAR_KEYS.get(raw, raw)
        mapped = KEY_MAP.get(raw.lower(), raw.lower())
        if mapped not in pydirectinput.KEYBOARD_MAPPING:
            logging.warning("跳过不支持的按键: %r", key)
            return None
        return mapped

    def release_all(self) -> None:
        for key in list(self.pressed_keys):
            try:
                pydirectinput.keyUp(self.map_key(key))
            except Exception as exc:
                logging.debug("释放按键失败: %s %s", key, exc)
        self.pressed_keys.clear()
        for button in list(self.mouse_buttons):
            try:
                pydirectinput.mouseUp(button=button)
            except Exception as exc:
                logging.debug("释放鼠标失败: %s %s", button, exc)
        self.mouse_buttons.clear()

    def move_to(self, x: int, y: int) -> None:
        pydirectinput.moveTo(int(x), int(y), duration=0.01)

    def mouse_down(self, x: int, y: int, button: str = "left") -> None:
        self.move_to(x, y)
        pydirectinput.mouseDown(button=button)
        self.mouse_buttons.add(button)

    def mouse_up(self, x: int, y: int, button: str = "left") -> None:
        self.move_to(x, y)
        pydirectinput.mouseUp(button=button)
        self.mouse_buttons.discard(button)

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.move_to(x, y)
        pydirectinput.click(x=int(x), y=int(y), button=button)

    def scroll(self, dy: int) -> None:
        pydirectinput.scroll(int(dy))

    def type_text(self, text: str) -> None:
        if not text:
            return
        old_text = read_clipboard_text()
        self.release_all()
        if write_clipboard_text(text):
            pydirectinput.keyDown("ctrl")
            pydirectinput.press("v")
            pydirectinput.keyUp("ctrl")
            time.sleep(0.08)
            if old_text is not None:
                write_clipboard_text(old_text)
            return
        pyautogui.write(str(text), interval=0.005)

    def key_down(self, key: str) -> None:
        mapped = self.map_key(key)
        if mapped:
            pydirectinput.keyDown(mapped)
            self.pressed_keys.add(key)

    def key_up(self, key: str) -> None:
        mapped = self.map_key(key)
        if mapped:
            pydirectinput.keyUp(mapped)
            self.pressed_keys.discard(key)
