from __future__ import annotations

import traceback
from pathlib import Path
from tkinter import messagebox

from taffi_mouse.paths import LOG_DIR, ensure_dirs


def main() -> None:
    ensure_dirs()
    try:
        from taffi_mouse.app3 import Taffi3App

        Taffi3App().run()
    except Exception as exc:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / "startup-error.log"
        log_file.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror("塔菲键鼠启动失败", f"{exc}\n\n错误日志已保存到:\n{log_file}")
        except Exception:
            print(f"塔菲键鼠启动失败: {exc}\n{log_file}")


if __name__ == "__main__":
    main()
