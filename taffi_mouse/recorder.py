from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse

from .utils import key_name, read_clipboard_text


class Recorder:
    def __init__(
        self,
        on_event: Callable[[Dict[str, Any]], None],
        should_ignore_point: Callable[[int, int], bool],
        move_interval: Callable[[], float],
    ) -> None:
        self.on_event = on_event
        self.should_ignore_point = should_ignore_point
        self.move_interval = move_interval
        self.mouse_listener: Optional[pynput_mouse.Listener] = None
        self.keyboard_listener: Optional[pynput_keyboard.Listener] = None
        self.start_time = 0.0
        self.last_move: Tuple[int, int, float] = (0, 0, 0.0)
        self.pressed = set()
        self.suppressed_releases = set()
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.start_time = time.time()
        self.last_move = (0, 0, 0.0)
        self.pressed.clear()
        self.suppressed_releases.clear()
        self.mouse_listener = pynput_mouse.Listener(
            on_click=self._on_click,
            on_move=self._on_move,
            on_scroll=self._on_scroll,
        )
        self.keyboard_listener = pynput_keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.mouse_listener.daemon = True
        self.keyboard_listener.daemon = True
        self.mouse_listener.start()
        self.keyboard_listener.start()
        logging.info("录制开始")

    def stop(self) -> None:
        self.running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None
        self.pressed.clear()
        self.suppressed_releases.clear()
        logging.info("录制停止")

    def offset(self) -> float:
        return round(max(0.0, time.time() - self.start_time), 3)

    def push(self, event: Dict[str, Any]) -> None:
        if not self.running:
            return
        event["time"] = self.offset()
        self.on_event(event)

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if not self.running or self.should_ignore_point(int(x), int(y)):
            return
        self.push(
            {
                "type": "mouse",
                "action": "down" if pressed else "up",
                "button": str(button).split(".")[-1],
                "x": int(x),
                "y": int(y),
            }
        )

    def _on_move(self, x: int, y: int) -> None:
        if not self.running or self.should_ignore_point(int(x), int(y)):
            return
        lx, ly, lt = self.last_move
        now = time.time()
        if abs(x - lx) > 14 or abs(y - ly) > 14:
            if now - lt >= self.move_interval():
                self.last_move = (int(x), int(y), now)
                self.push({"type": "move", "x": int(x), "y": int(y)})

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self.running or self.should_ignore_point(int(x), int(y)):
            return
        self.push({"type": "scroll", "x": int(x), "y": int(y), "dx": int(dx), "dy": int(dy)})

    def _on_press(self, key: Any) -> None:
        if not self.running:
            return
        name = key_name(key)
        if not name or name in self.pressed:
            return
        if name == "v" and "ctrl" in self.pressed:
            text = read_clipboard_text()
            if text:
                self.suppressed_releases.add(name)
                self.push({"type": "text", "text": text})
                return
        self.pressed.add(name)
        self.push({"type": "key", "action": "down", "key": name})

    def _on_release(self, key: Any) -> None:
        if not self.running:
            return
        name = key_name(key)
        if not name:
            return
        if name in self.suppressed_releases:
            self.suppressed_releases.discard(name)
            return
        self.pressed.discard(name)
        self.push({"type": "key", "action": "up", "key": name})
