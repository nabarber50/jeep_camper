# cam/setup/foamcam/log.py
import os
import datetime
import traceback


class AppLogger(object):
    def __init__(self, path: str, ui=None, raise_on_fail: bool = False):
        self.path = path
        self.ui = ui
        self.raise_on_fail = raise_on_fail

    def _ensure_dir(self):
        folder = os.path.dirname(self.path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)

    def log(self, msg: str, show_ui: bool = False):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"

        try:
            self._ensure_dir()
            with open(self.path, "a+", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except Exception as e:
            # Don't fail silently. Surface it (optionally) and/or raise.
            details = (
                f"Logger failed to write.\n\n"
                f"Path:\n{self.path}\n\n"
                f"{type(e).__name__}: {e}\n\n"
                f"{traceback.format_exc()}"
            )
            if show_ui and self.ui:
                try:
                    self.ui.messageBox(details, "Logger Error")
                except:
                    pass
            if self.raise_on_fail:
                raise
