# cam/setup/foamcam/units.py
import adsk.fusion

class Units:
    """
    Handles expression evaluation to mm, including the 'probe=0.1 -> factor=10' quirk.
    """
    def __init__(self, design: adsk.fusion.Design, logger):
        self.design = design
        self.logger = logger
        self._factor = None

    def eval_mm(self, expr: str) -> float:
        um = self.design.unitsManager
        if self._factor is None:
            try:
                probe = float(um.evaluateExpression('1 mm', 'mm'))
            except:
                probe = 1.0
            self._factor = 10.0 if (0.09 <= probe <= 0.11) else 1.0
            self.logger.log(f"_eval_mm calibration: probe={probe} -> factor={self._factor}")

        val = float(um.evaluateExpression(expr, 'mm'))
        return val * self._factor
