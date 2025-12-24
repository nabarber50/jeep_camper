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

        This has proven to be highly build-dependent, so we:
          1) Try a small set of known parameter names.
          2) Fall back to a heuristic scan of *all* setup parameters that look like WCS rotation.
          3) As a last resort, try the API property if it exists on this build.

        Returns True if we believe we set a 90° rotation when requested.
        """
        params = getattr(setup, "parameters", None)
        if not params:
            return False

        # When NOT rotating, we still try to explicitly clear rotation where possible.
        target_exprs = ["90 deg", "90"] if rotate_90 else ["0 deg", "0"]

        # 0) Try API property if present (some builds support it, many ignore it)
        try:
            wcs = getattr(setup, "workCoordinateSystemOrientation", None)
            if wcs is not None:
                try:
                    # Some builds expect radians.
                    wcs.rotationAngle = math.radians(90.0 if rotate_90 else 0.0)
                except:
                    pass
        except:
            pass

        # 1) angle-style parameters (known names across some builds)
        known_names = [
            "wcs_rotationAngle",
            "wcs_rotation",
            "job_wcsRotation",
            "job_wcsRotationAngle",
            # Seen in some manufacturing workspaces / posts
            "job_wcsRotationZ",
            "wcs_rotationZ",
            "wcs_rotZ",
            "job_setupWcsRotation",
            "setup_wcsRotation",
            "setup_wcsRotationAngle",
        ]
        for nm in known_names:
            for expr in target_exprs:
                ok, used = set_param_expr_any(params, [nm], expr)
                if ok:
                    self.logger.log(f"WCS rotation set via param {used} = {expr}")
                    return True

        # 2) heuristic scan: any param that looks like a WCS rotation angle
        #    (e.g. contains both 'wcs' and ('rot' or 'angle')).
        heuristic_hits = []
        try:
            for p in params:
                try:
                    nm = getattr(p, "name", "")
                    lnm = (nm or "").lower()
                    if "wcs" not in lnm:
                        continue
                    if ("rot" not in lnm) and ("angle" not in lnm):
                        continue
                    heuristic_hits.append(nm)
                except:
                    continue
        except:
            heuristic_hits = []

        for hit in heuristic_hits:
            for expr in target_exprs:
                ok, used = set_param_expr_any(params, [hit], expr)
                if ok:
                    self.logger.log(f"WCS rotation set via heuristic param {used} = {expr}")
                    return True

        # 3) axis-swap / XY-swap style parameters (rare)
        swap_true  = ["true", "1"]
        swap_false = ["false", "0"]
        swap_exprs = swap_true if rotate_90 else swap_false
        swap_names = []
        try:
            for p in params:
                nm = getattr(p, "name", "")
                lnm = (nm or "").lower()
                if "wcs" not in lnm:
                    continue
                if ("swap" in lnm and ("xy" in lnm or "x" in lnm and "y" in lnm)) or ("flip" in lnm and ("xy" in lnm or "x" in lnm and "y" in lnm)):
                    swap_names.append(nm)
        except:
            swap_names = []

        for nm in swap_names:
            for expr in swap_exprs:
                ok, used = set_param_expr_any(params, [nm], expr)
                if ok:
                    self.logger.log(f"WCS XY swap set via param {used} = {expr}")
                    return True

        return False

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

        # Decide whether to rotate the CAM WCS 90° about Z.
        #
        # We *prefer* the "automatic" rule (rotate when the model's long axis is X),
        # but in practice Fusion/Post/Maslow combinations sometimes export XY swapped
        # even when the sheet looks correct inside Fusion. When that happens, the most
        # reliable fix is to force a 90° WCS rotation (equivalent to the old "rotate the
        # model" workaround).
        force_rot = bool(getattr(self.Config, "MASLOW_FORCE_WCS_ROTATE_90", False))
        rotate_wcs_90 = True if force_rot else bool(model_long_is_x)
        if force_rot:
            self.logger.log("Maslow: forcing WCS rotation 90° (Config.MASLOW_FORCE_WCS_ROTATE_90=True)")

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
