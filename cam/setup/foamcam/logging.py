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

                # Dev instrumentation: detect unexpected rotation log messages
                try:
                    if "Applied MASLOW_SWAP_XY_COMPENSATION" in msg:
                        f.write("[ROTATION_DETECT] Detected rotation message; dumping Python stack:\n")
                        import inspect
                        stack = inspect.stack()
                        # Skip the logger frame itself and report the first 20 frames
                        for fr in stack[1:21]:
                            fn = fr.filename
                            ln = fr.lineno
                            nm = fr.function
                            f.write(f"  File \"{fn}\", line {ln}, in {nm}\n")
                        f.write("[ROTATION_DETECT] End stack dump.\n")
                        f.flush()

                        # If configured, fail-fast so user sees traceback in console
                        try:
                            from foamcam.config import Config
                        except Exception:
                            try:
                                from .config import Config
                            except Exception:
                                Config = None
                        try:
                            if Config and getattr(Config, 'DEBUG_FAIL_ON_ROTATION', False):
                                raise RuntimeError('DEBUG_FAIL_ON_ROTATION: rotation log detected in logger')
                        except Exception:
                            # If we raise here it will bubble out; let that happen intentionally
                            raise
                except Exception:
                    # Avoid letting logger instrumentation crash silently; re-raise if it was intentional fail-fast
                    raise
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
