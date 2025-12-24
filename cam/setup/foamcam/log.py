# cam/setup/foamcam/log.py
import datetime

class Logger:
    def __init__(self, path: str):
        self.path = path

    def log(self, msg: str):
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except:
            pass
