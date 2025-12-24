# cam/setup/foamcam/stock_wcs.py
import math
from foamcam.geometry import model_xy_extents_mm
from foamcam.fusion_params import (
    dump_setup_params, set_param_expr_any, set_param_bool_any
)

class StockWcsEnforcer:
    def __init__(self, design, units, logger, Config):
        self.design = design
        self.units = units
        self.logger = logger
        self.Config = Config

    def _pick_smallest_sheet_class_for_model(self, model_w_mm, model_h_mm, margin_mm):
        req_w = model_w_mm + 2.0 * margin_mm
        req_h = model_h_mm + 2.0 * margin_mm

        for cname, sw, sh in self.Config.SHEET_CLASSES:
            stockY = max(sw, sh)  # long axis
            stockX = min(sw, sh)

            if (req_w <= stockX and req_h <= stockY) or (req_h <= stockX and req_w <= stockY):
                return (cname, stockX, stockY)
        return None

    def _set_fixed_stock_box_mm(self, setup, sx, sy, sz) -> bool:
        params = getattr(setup, "parameters", None)
        if not params:
            self.logger.log("No setup.parameters; cannot set stock.")
            return False

        okx, xnm = set_param_expr_any(params, ["job_stockFixedX", "job_stockFixedBoxWidth", "job_stockFixedBoxX"], f"{float(sx)} mm")
        oky, ynm = set_param_expr_any(params, ["job_stockFixedY", "job_stockFixedBoxDepth", "job_stockFixedBoxY"], f"{float(sy)} mm")
        okz, znm = set_param_expr_any(params, ["job_stockFixedZ", "job_stockFixedBoxHeight", "job_stockFixedBoxZ"], f"{float(sz)} mm")

        self.logger.log(f"Stock set attempt: X={sx}({xnm}) ok={okx}, Y={sy}({ynm}) ok={oky}, Z={sz}({znm}) ok={okz}")
        return bool(okx and oky and okz)

    def _set_wcs_top_center_stock_point(self, setup) -> bool:
        params = getattr(setup, "parameters", None)
        if not params:
            return False

        # origin mode
        set_param_expr_any(params, ["wcs_origin_mode"], "stockPoint")
        # box point token (many builds want quoted)
        ok, _ = set_param_expr_any(params, ["wcs_origin_boxPoint"], "'top center'")
        if not ok:
            ok, _ = set_param_expr_any(params, ["wcs_origin_boxPoint"], "top center")

        # force stock point (best effort)
        set_param_expr_any(params, ["wcs_stock_point"], "true")
        set_param_expr_any(params, ["wcs_model_point"], "false")

        if ok:
            self.logger.log("WCS origin set: stockPoint / top center (stock point forced).")
        return bool(ok)

    def _try_set_wcs_rotation_90(self, setup, rotate_90: bool) -> bool:
        """
        Many Fusion builds don't honor setup.workCoordinateSystemOrientation.rotationAngle.
        So we try known param names that show up in dumps across builds.
        """
        if not rotate_90:
            return True

        params = getattr(setup, "parameters", None)
        if not params:
            return False

        # 1) angle-style parameters (if present)
        # Some builds store degrees, some radians; we try both tokens.
        candidates = [
            ("wcs_rotationAngle", "90 deg"),
            ("wcs_rotationAngle", "90"),
            ("wcs_rotation", "90 deg"),
            ("wcs_rotation", "90"),
            ("job_wcsRotation", "90 deg"),
            ("job_wcsRotation", "90"),
        ]
        for nm, expr in candidates:
            ok, used = set_param_expr_any(params, [nm], expr)
            if ok:
                self.logger.log(f"WCS rotation set via param {used} = {expr}")
                return True

        # 2) axis-swap style parameters (rare but exists)
        # If there are explicit axis selection params, try swapping.
        # NOTE: these names are speculative; dump will tell us what's real on your build.
        axis_swap_attempts = [
            ("wcs_axisX", "y"),
            ("wcs_axisY", "x"),
            ("wcs_xAxis", "y"),
            ("wcs_yAxis", "x"),
        ]
        swapped_any = False
        for nm, expr in axis_swap_attempts:
            ok, used = set_param_expr_any(params, [nm], expr)
            swapped_any = swapped_any or ok
            if ok:
                self.logger.log(f"WCS axis param set {used}={expr}")
        return swapped_any

    def enforce(self, setup, model_bodies) -> dict:
        """
        Core rule:
          - Maslow +Y moves away from you
          - Fusion +Y MUST be the long sheet direction
          - If model long axis is X, rotate WCS 90° so toolpaths align
        Also ensures stock >= model and only then sets origin top-center.
        """
        ex = model_xy_extents_mm(model_bodies)
        if not ex:
            raise RuntimeError("Could not compute model extents (no bodies?).")
        model_x, model_y = ex
        model_long_is_x = (model_x >= model_y)

        margin_mm = self.units.eval_mm(self.Config.LAYOUT_MARGIN)
        stock_thk_mm = self.units.eval_mm(self.Config.SHEET_THK)

        pick = self._pick_smallest_sheet_class_for_model(model_x, model_y, margin_mm)
        if not pick:
            raise RuntimeError(f"No sheet fits model {model_x:.1f}x{model_y:.1f} mm with margin {margin_mm:.1f} mm")

        cname, stockX, stockY = pick

        rotate_wcs_90 = bool(model_long_is_x)

        ok_stock = self._set_fixed_stock_box_mm(setup, stockX, stockY, stock_thk_mm)
        if not ok_stock:
            dump_setup_params(self.logger, setup)
            raise RuntimeError("Failed to set fixed stock box dimensions (no matching stock params found).")

        # verify fit before setting origin
        fit_x = (model_y if rotate_wcs_90 else model_x) + 2.0 * margin_mm
        fit_y = (model_x if rotate_wcs_90 else model_y) + 2.0 * margin_mm
        if fit_x > stockX + 1e-6 or fit_y > stockY + 1e-6:
            raise RuntimeError(f"Stock too small after orientation. Need {fit_x:.1f}x{fit_y:.1f}, have {stockX:.1f}x{stockY:.1f}")

        # WCS rotation (param based best effort)
        rot_ok = self._try_set_wcs_rotation_90(setup, rotate_wcs_90)
        if rotate_wcs_90:
            self.logger.log(f"WCS rotation requested (90°): success={rot_ok}")

        # origin
        origin_ok = self._set_wcs_top_center_stock_point(setup)
        if not origin_ok:
            self.logger.log("WCS origin not set; dumping params for diagnosis.")
            dump_setup_params(self.logger, setup)

        # HARD LOCK stock behaviors that sometimes override fixed box
        try:
            params = setup.parameters
            set_param_expr_any(params, ['job_stockFixedBoxPosition','stockFixedBoxPosition','job_stockPosition','stockPosition'], 'center')
            set_param_bool_any(params, ['job_stockGroundToModel','stockGroundToModel','job_groundStockAtModelOrigin'], False)
            self.logger.log("Stock lock applied: fixed box preserved.")
        except:
            self.logger.log("WARNING: failed to hard-lock stock; Fusion may resize it.")

        self.logger.log(
            f"Setup orientation complete: sheetClass={cname} stockX={stockX:.1f} stockY={stockY:.1f} "
            f"modelX={model_x:.1f} modelY={model_y:.1f} rotateWCS90={rotate_wcs_90} rotParamOK={rot_ok}"
        )

        return {"sheetClass": cname, "rotateWCS90": rotate_wcs_90, "stockX": stockX, "stockY": stockY, "rotParamOK": rot_ok}
