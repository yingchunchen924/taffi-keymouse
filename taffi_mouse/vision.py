from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import pyautogui
from PIL import Image


class VisionEngine:
    def screenshot_cv(self) -> Optional[np.ndarray]:
        try:
            img = pyautogui.screenshot()
            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        except Exception as exc:
            logging.warning("截屏失败: %s", exc)
            return None

    def load_template(self, path: Path) -> Optional[np.ndarray]:
        try:
            img = Image.open(path).convert("RGB")
            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        except Exception as exc:
            logging.warning("读取参考图失败: %s %s", path, exc)
            return None

    def find(
        self,
        screen: np.ndarray,
        template: np.ndarray,
        threshold: float,
    ) -> Optional[Tuple[int, int, float]]:
        if screen is None or template is None:
            return None
        sh, sw = screen.shape[:2]
        th, tw = template.shape[:2]
        if th < 8 or tw < 8 or th > sh or tw > sw:
            return None
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val >= threshold:
            return max_loc[0] + tw // 2, max_loc[1] + th // 2, float(max_val)
        return None

    def find_file(self, image_path: Path, threshold: float) -> Optional[Tuple[int, int, float]]:
        screen = self.screenshot_cv()
        template = self.load_template(image_path)
        if screen is None or template is None:
            return None
        return self.find(screen, template, threshold)
