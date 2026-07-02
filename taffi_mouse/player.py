from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pyautogui

from .database import Database
from .input_driver import SafeInputDriver
from .models import Macro
from .utils import is_corner_failsafe, screen_size
from .vision import VisionEngine


class Player:
    def __init__(
        self,
        db: Database,
        status_callback: Callable[[str], None],
        progress_callback: Callable[[int, int], None],
        ref_path_lookup: Callable[[str], Optional[Path]],
        settings_provider: Callable[[], Dict[str, Any]],
    ) -> None:
        self.db = db
        self.status_callback = status_callback
        self.progress_callback = progress_callback
        self.ref_path_lookup = ref_path_lookup
        self.settings_provider = settings_provider
        self.driver = SafeInputDriver()
        self.vision = VisionEngine()
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.loop_count = 0
        self.running = False
        self.failure_message = ""

    def start(self, macro: Macro, events: List[Dict[str, Any]], settings: Optional[Dict[str, Any]] = None) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.pause_event.clear()
        self.loop_count = 0
        self.failure_message = ""
        self.running = True
        settings_snapshot = dict(settings if settings is not None else self.settings_provider())
        self.thread = threading.Thread(target=self._run, args=(macro, list(events), settings_snapshot), daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()
        self.driver.release_all()

    def toggle_pause(self) -> bool:
        if self.pause_event.is_set():
            self.pause_event.clear()
            return False
        self.pause_event.set()
        return True

    def _run(self, macro: Macro, events: List[Dict[str, Any]], settings: Dict[str, Any]) -> None:
        run_db: Optional[Database] = None
        run_id: Optional[int] = None
        status = "finished"
        message = ""
        try:
            try:
                run_db = Database(self.db.path)
                run_id = run_db.start_run(macro.id)
            except Exception as exc:
                logging.warning("运行记录创建失败: %s", exc)
            countdown = int(settings.get("countdown", 2))
            for remaining in range(countdown, 0, -1):
                if self.stop_event.is_set():
                    status = "stopped"
                    return
                self.status_callback(f"{remaining} 秒后开始，请切到目标窗口")
                time.sleep(1)
            playback_mode = str(settings.get("playback_mode", "single"))
            legacy_loop = bool(settings.get("loop", False))
            if playback_mode not in ("single", "count", "loop"):
                playback_mode = "loop" if legacy_loop else "single"
            max_runs = max(1, int(settings.get("playback_count", 1) or 1))
            if playback_mode == "single":
                max_runs = 1
            while not self.stop_event.is_set():
                self.loop_count += 1
                if playback_mode == "count":
                    self.status_callback(f"回放中，第 {self.loop_count} / {max_runs} 次")
                elif playback_mode == "loop":
                    self.status_callback(f"循环回放中，第 {self.loop_count} 次")
                else:
                    self.status_callback("回放中")
                self._play_once(macro, events, settings)
                if playback_mode == "count" and self.loop_count >= max_runs:
                    break
                if playback_mode == "single" or self.stop_event.is_set():
                    break
                if playback_mode not in ("count", "loop") and not legacy_loop:
                    break
                self._wait(float(settings.get("loop_interval", 0.4)))
            if self.stop_event.is_set():
                status = "stopped"
                message = self.failure_message
        except pyautogui.FailSafeException:
            status = "emergency"
            message = "触发屏幕角落急停"
            self.status_callback(message)
        except Exception as exc:
            status = "error"
            message = str(exc)
            logging.exception("回放异常")
            self.status_callback(f"回放异常: {exc}")
        finally:
            self.running = False
            self.driver.release_all()
            if run_db is not None and run_id is not None:
                try:
                    run_db.finish_run(run_id, status, self.loop_count, message)
                except Exception as exc:
                    logging.warning("运行记录保存失败: %s", exc)
                finally:
                    run_db.close()
            if status == "finished":
                self.status_callback("回放完成")

    def _wait(self, seconds: float) -> bool:
        end = time.time() + max(0, seconds)
        while time.time() < end:
            if self.stop_event.is_set() or is_corner_failsafe():
                self.stop_event.set()
                return False
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.05)
            time.sleep(0.02)
        return True

    def _wait_until(self, start: float, offset: float) -> bool:
        while True:
            if self.stop_event.is_set() or is_corner_failsafe():
                self.stop_event.set()
                return False
            if self.pause_event.is_set():
                paused_at = time.time()
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    time.sleep(0.05)
                start += time.time() - paused_at
            if time.time() - start >= offset:
                return True
            time.sleep(0.01)

    def _play_once(self, macro: Macro, events: List[Dict[str, Any]], settings: Dict[str, Any]) -> None:
        speed = max(0.1, float(settings.get("speed", 1.0)))
        cur_w, cur_h = screen_size()
        scale_x = cur_w / macro.screen_width if macro.screen_width else 1.0
        scale_y = cur_h / macro.screen_height if macro.screen_height else 1.0
        start = time.time()
        total = len(events)
        for index, event in enumerate(events):
            offset = float(event.get("time", 0)) / speed
            if not self._wait_until(start, offset):
                return
            self.progress_callback(index, total)
            self._execute(event, scale_x, scale_y, settings)

    def _execute(self, event: Dict[str, Any], scale_x: float, scale_y: float, settings: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "move":
            self.driver.move_to(int(float(event.get("x", 0)) * scale_x), int(float(event.get("y", 0)) * scale_y))
        elif kind == "mouse":
            x = int(float(event.get("x", 0)) * scale_x)
            y = int(float(event.get("y", 0)) * scale_y)
            if event.get("action") == "down" and settings.get("anchor_guard", True):
                x, y = self._adjust_by_refs(x, y, settings)
            if event.get("action") == "down":
                self.driver.mouse_down(x, y, str(event.get("button", "left")))
            else:
                self.driver.mouse_up(x, y, str(event.get("button", "left")))
        elif kind == "scroll":
            mul = int(settings.get("scroll_multiplier", 3))
            self.driver.scroll(int(event.get("dy", 0)) * mul)
        elif kind == "key":
            if event.get("action") == "down":
                self.driver.key_down(str(event.get("key", "")))
            else:
                self.driver.key_up(str(event.get("key", "")))
        elif kind == "text":
            self.driver.type_text(str(event.get("text", "")))
        elif kind == "image_click":
            self._image_click(event, settings)
        elif kind == "verify":
            self._verify(event, settings)
        elif kind == "wait":
            self._wait(float(event.get("duration", 0)))

    def _adjust_by_refs(self, x: int, y: int, settings: Dict[str, Any]) -> Tuple[int, int]:
        refs = settings.get("reference_files", [])
        if not refs:
            return x, y
        screen = self.vision.screenshot_cv()
        if screen is None:
            return x, y
        threshold = float(settings.get("threshold", 0.82))
        radius = int(settings.get("anchor_radius", 240))
        best: Optional[Tuple[int, int, float]] = None
        for item in refs:
            path = Path(item)
            tpl = self.vision.load_template(path)
            found = self.vision.find(screen, tpl, threshold) if tpl is not None else None
            if found and abs(found[0] - x) <= radius and abs(found[1] - y) <= radius:
                if best is None or found[2] > best[2]:
                    best = found
        if best:
            self.status_callback(f"坐标修正到 ({best[0]}, {best[1]}) {best[2]:.0%}")
            return best[0], best[1]
        return x, y

    def _image_click(self, event: Dict[str, Any], settings: Dict[str, Any]) -> None:
        image_name = str(event.get("image_name") or event.get("image") or "")
        ref_map = settings.get("reference_map", {})
        path = Path(ref_map.get(image_name, "")) if isinstance(ref_map, dict) and ref_map.get(image_name) else None
        if path is None:
            path = self.ref_path_lookup(image_name)
        if not path:
            self.fail(f"参考图不存在: {image_name}")
            return
        retries = int(event.get("retries", 5))
        threshold = float(settings.get("threshold", 0.82))
        for attempt in range(retries + 1):
            if self.stop_event.is_set():
                return
            found = self.vision.find_file(path, threshold)
            if found:
                self.driver.click(found[0], found[1])
                self.status_callback(f"识图点击: {image_name} {found[2]:.0%}")
                return
            if attempt < retries:
                self.status_callback(f"等待识图: {image_name} ({attempt + 1}/{retries})")
                self._wait(0.35)
        self.fail(f"识图未找到: {image_name}")

    def _verify(self, event: Dict[str, Any], settings: Dict[str, Any]) -> None:
        image_name = str(event.get("image_name") or event.get("image") or "")
        ref_map = settings.get("reference_map", {})
        path = Path(ref_map.get(image_name, "")) if isinstance(ref_map, dict) and ref_map.get(image_name) else None
        if path is None:
            path = self.ref_path_lookup(image_name)
        if not path:
            if event.get("on_fail", "stop") == "stop":
                self.fail(f"校验参考图不存在: {image_name}")
            return
        retries = int(event.get("retries", 3))
        threshold = float(settings.get("threshold", 0.82))
        for attempt in range(retries + 1):
            if self.stop_event.is_set():
                return
            found = self.vision.find_file(path, threshold)
            if found:
                self.status_callback(f"校验通过: {image_name} {found[2]:.0%}")
                return
            if attempt < retries:
                self._wait(0.35)
        self.status_callback(f"校验失败: {image_name}")
        if event.get("on_fail", "stop") == "stop":
            self.fail(f"校验失败: {image_name}")

    def fail(self, message: str) -> None:
        self.failure_message = message
        self.status_callback(message)
        self.stop_event.set()
