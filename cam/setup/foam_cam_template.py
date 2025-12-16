# ============================================================
# Foam CAM Template – Multi-Sheet Nesting + CAM (Clean, Stable)
# ============================================================

import os
import re
import math
import datetime
import traceback
import adsk.core, adsk.fusion, adsk.cam


# ----------------------------
# CONFIG
# ----------------------------

USE_VISIBLE_BODIES_ONLY = True

# ---- Sheet / Nesting ----
DO_AUTO_LAYOUT = True
LAYOUT_BASE_NAME = 'SHEET_LAYOUT_4x8'

SHEET_CLASSES = [
    ("STD_4x8",   1219.2, 2438.4),
    ("EXT_4x10",  1219.2, 3048.0),
    ("EXT_4x12",  1219.2, 3657.6),
    ("WIDE_6x10", 1828.8, 3048.0),
]

# ---- Optional concave/U-shape pairing (experimental) ----
ENABLE_U_PAIRING = True       # set True to try interlocking U-shaped parts
U_FILL_RATIO_MAX = 0.70        # <= this considered "concave-ish" (lower => more concave)
U_MIN_AREA_MM2   = 250000.0    # ignore tiny parts (mm^2); reduces runtime
U_PAIR_STEP_MM   = 25.0        # grid step for dx/dy when searching pair offsets
U_PAIR_GRID_N    = 5           # odd number; 5 => offsets [-2..+2] steps
U_PAIR_MIN_GAIN  = 0.10        # require >=10% bbox area improvement to accept pair

# ---- Naming ergonomics ----
COMPACT_PART_NAMES = True      # Layer_15_part_01 -> L15_P01
LOG_NATIVE_BBOX_SIZES = False  # log native bbox sizes (can be noisy)


SHEET_W   = '2438.4 mm'     # 8 ft (X)
SHEET_H   = '1219.2 mm'     # 4 ft (Y)
SHEET_THK = '38.1 mm'       # foam thickness

LAYOUT_MARGIN = '10 mm'
LAYOUT_GAP    = '8 mm'
ALLOW_ROTATE_90 = True
HIDE_ORIGINALS_AFTER_COPY = True

# ---- Tool preference ----
PREFERRED_TOOL_NAME_CONTAINS = 'Ø1/4"'   # name substring
PREFERRED_TOOL_DIAMETER_IN   = 0.25

# ---- Maslow Z Safety ----
MASLOW_RETRACT       = '1.5 mm'
MASLOW_CLEARANCE     = '2.0 mm'
MASLOW_FEED          = '1.0 mm'
MASLOW_PLUNGE_FEED   = '300 mm/min'
MASLOW_RETRACT_FEED  = '300 mm/min'

# ---- Stepdowns ----
PROFILE_STEPDOWN = '6 mm'
ROUGH_STEPDOWN   = '6 mm'
FINISH_STEPDOWN  = '2 mm'

LOG_PATH = os.path.join(os.path.expanduser("~"), "Desktop", "foam_cam_template_log.txt")
LAYER_NAME_RE = re.compile(r'^Layer_\d+_part_\d+$', re.IGNORECASE)


# ============================================================
# LOGGING + UNITS
# ============================================================

_EVAL_MM_FACTOR = None  # auto-detected at runtime

def log(msg: str):
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except:
        pass

def _dump_setup_params(setup, contains=("wcs", "origin", "box", "point")):
    try:
        params = setup.parameters
        log("---- SETUP PARAM DUMP (filtered) ----")
        for i in range(params.count):
            p = params.item(i)
            name = ""
            try:
                name = p.name
            except:
                continue
            low = name.lower()
            if any(k in low for k in contains):
                try:
                    val = ""
                    try:
                        val = str(p.expression)
                    except:
                        try: val = str(p.value)
                        except: val = "<?>"
                    log(f"PARAM: {name} = {val}")
                except:
                    log(f"PARAM: {name}")
        log("---- END SETUP PARAM DUMP ----")
    except Exception as e:
        log(f"_dump_setup_params failed: {e}")

def _eval_mm(design: adsk.fusion.Design, expr: str) -> float:
    """
    Evaluate expression to mm float.
    Some Fusion builds return 'mm' values with cm magnitude (probe ~= 0.1).
    We detect once and scale by 10 when needed.
    """
    global _EVAL_MM_FACTOR
    um = design.unitsManager

    if _EVAL_MM_FACTOR is None:
        try:
            probe = float(um.evaluateExpression('1 mm', 'mm'))
        except:
            probe = 1.0

        if 0.09 <= probe <= 0.11:
            _EVAL_MM_FACTOR = 10.0
        else:
            _EVAL_MM_FACTOR = 1.0

        try:
            log(f"_eval_mm calibration: probe={probe} -> factor={_EVAL_MM_FACTOR}")
        except:
            pass

    val = float(um.evaluateExpression(expr, 'mm'))
    return val * _EVAL_MM_FACTOR

def _union_bbox_mm(bodies):
    """Union bbox of bodies, returns (x0,y0,z0,x1,y1,z1) in mm."""
    x0 = y0 = z0 =  1e99
    x1 = y1 = z1 = -1e99
    any_body = False
    for b in bodies or []:
        try:
            n = _resolve_native(b)
            bx0, by0, bz0, bx1, by1, bz1 = _bbox_mm(n)
            x0 = min(x0, bx0); y0 = min(y0, by0); z0 = min(z0, bz0)
            x1 = max(x1, bx1); y1 = max(y1, by1); z1 = max(z1, bz1)
            any_body = True
        except:
            pass
    if not any_body:
        return None
    return (x0, y0, z0, x1, y1, z1)

def _model_xy_extents_mm(model_bodies):
    bb = _union_bbox_mm(model_bodies)
    if not bb:
        return None
    x0,y0,_z0,x1,y1,_z1 = bb
    return (abs(x1-x0), abs(y1-y0))

def _pick_smallest_sheet_class_for_model(model_w_mm, model_h_mm, margin_mm):
    """
    Returns (className, stockX_mm, stockY_mm) where stockY is the LONG axis (Maslow +Y away).
    Uses SHEET_CLASSES (w,h) in mm.
    """
    req_w = model_w_mm + 2.0 * margin_mm
    req_h = model_h_mm + 2.0 * margin_mm

    best = None
    for cname, sw, sh in SHEET_CLASSES:
        # enforce: Fusion +Y is the LONG physical axis
        stockY = max(sw, sh)
        stockX = min(sw, sh)

        # model must fit within stock X/Y (no rotation here; rotation decision is WCS-level)
        if req_w <= stockX and req_h <= stockY:
            best = (cname, stockX, stockY)
            break
        if req_h <= stockX and req_w <= stockY:
            # would fit if we rotate WCS 90°
            best = (cname, stockX, stockY)
            break

    return best

def _set_setup_fixed_box_stock_mm(design, setup, stock_x_mm, stock_y_mm, stock_z_mm):
    """
    Sets setup to fixed box stock with given mm dims.
    Tries common parameter names across Fusion builds.
    """
    def _set_param_mm(name_candidates, value_mm):
        for nm in name_candidates:
            try:
                p = setup.parameters.itemByName(nm)
                if p:
                    try:
                        p.expression = f"{value_mm} mm"
                        return True
                    except:
                        try:
                            p.value = float(value_mm)
                            return True
                        except:
                            pass
            except:
                pass
        return False

    def _set_param_raw(name_candidates, raw_expr):
        for nm in name_candidates:
            try:
                p = setup.parameters.itemByName(nm)
                if p:
                    try:
                        p.expression = str(raw_expr)
                        return True
                    except:
                        try:
                            p.value = raw_expr
                            return True
                        except:
                            pass
            except:
                pass
        return False


    # Stock mode (best-effort)
    try:
        setup.stockMode = adsk.cam.SetupStockModes.FixedBoxStock
    except:
        pass

    ok_mode = _set_param_raw(["job_stockMode", "stockMode"], "fixedBox")

    okx = _set_param_mm(
        ["job_stockFixedBoxWidth","stockFixedBoxWidth","job_stockWidth","stockWidth",
        "job_stockFixedX","stockFixedX"],
        stock_x_mm
    )

    oky = _set_param_mm(
        ["job_stockFixedBoxLength","stockFixedBoxLength",
        "job_stockFixedBoxDepth","stockFixedBoxDepth",   # add these
        "job_stockLength","stockLength",
        "job_stockFixedY","stockFixedY"],
        stock_y_mm
    )

    okz = _set_param_mm(
        ["job_stockFixedBoxHeight","stockFixedBoxHeight",
        "job_stockThickness","stockThickness",
        "job_stockFixedZ","stockFixedZ"],
        stock_z_mm
    )
    # Optional: force center modes if your build supports them
    try:
        for nm in ["job_stockFixedXMode", "job_stockFixedYMode", "job_stockFixedZMode"]:
            p = setup.parameters.itemByName(nm)
            if p:
                try: p.value = "center"
                except: p.expression = "center"
    except:
        pass

    try:
        log(f"Stock set (mm): X={stock_x_mm:.1f} Y={stock_y_mm:.1f} Z={stock_z_mm:.1f} ok=({okx},{oky},{okz})")
    except:
        pass


def _set_wcs_top_center_after_verified(setup):
    """
    Your Fusion build uses:
      - wcs_origin_mode = 'stockPoint'
      - wcs_origin_boxPoint = 'top center'
    And we force stock-point selection to avoid Fusion switching back to model origin.
    """
    try:
        params = setup.parameters

        # 1) Make sure we're using stock point origin mode
        try:
            p = params.itemByName("wcs_origin_mode")
            if p:
                p.expression = "stockPoint"
        except:
            pass

        # 2) Set origin to TOP CENTER (exact token for your build)
        try:
            p = params.itemByName("wcs_origin_boxPoint")
            if p:
                p.expression = "'top center'"  # quotes matter in many builds
        except:
            try:
                log("WCS origin: could not set wcs_origin_boxPoint.")
            except:
                pass
            return False

        # 3) ✅ THIS IS THE SNIPPET YOU ASKED ABOUT (put it RIGHT HERE)
        # Prefer stock point, not model point
        for nm, expr in [("wcs_stock_point", "true"), ("wcs_model_point", "false")]:
            try:
                p = params.itemByName(nm)
                if p:
                    p.expression = expr
            except:
                pass

        try:
            log("WCS origin set: stockPoint / top center (stock point forced).")
        except:
            pass
        return True

    except Exception as e:
        try:
            log(f"WCS origin: exception setting top center: {e}")
        except:
            pass
        return False

def _ensure_setup_orientation_stock_wcs(design, ui, setup, model_bodies):
    """
    Core rule:
      - Maslow +Y moves away from you
      - Therefore Fusion +Y MUST be the long sheet direction
      - If model long axis is X, rotate WCS 90° (swap X/Y axes) so toolpaths align to Maslow
    Also ensures stock >= model and only then sets origin top-center.
    """
    # 1) detect model extents
    ex = _model_xy_extents_mm(model_bodies)
    if not ex:
        raise RuntimeError("Could not compute model extents (no bodies?).")
    model_x, model_y = ex
    model_long_is_x = (model_x >= model_y)

    margin_mm = _eval_mm(design, LAYOUT_MARGIN)
    stock_thk_mm = _eval_mm(design, SHEET_THK)

    # 2) pick a sheet class that will fit (and enforce long axis to +Y)
    pick = _pick_smallest_sheet_class_for_model(model_x, model_y, margin_mm)
    if not pick:
        raise RuntimeError(f"No SHEET_CLASSES size fits model {model_x:.1f}x{model_y:.1f} mm with margin {margin_mm:.1f} mm")

    cname, stockX, stockY = pick

    # 3) decide whether we need to rotate WCS 90°
    # If model is longer in X than Y, we rotate WCS so model-long maps to +Y.
    rotate_wcs_90 = model_long_is_x

    # 4) Set stock dims. If we rotate WCS, we DO NOT rotate the physical stock;
    # we keep stock long axis in +Y (stockY is long), but we rotate WCS axes so toolpaths match Maslow.
    _set_setup_fixed_box_stock_mm(design, setup, stockX, stockY, stock_thk_mm)

    # 5) Apply WCS orientation swap (best-effort). Your build may already do this elsewhere;
    # this block is safe: it only tries if the API surface exists.
    try:
        if rotate_wcs_90:
            # Many builds allow setting orientation axes explicitly.
            # We try to swap X and Y by rotating the WCS about +Z.
            o = setup.workCoordinateSystemOrientation
            o.flipX = False
            o.flipY = False
            try:
                o.rotationAngle = math.radians(90.0)
            except:
                # if rotationAngle not supported, leave it; stock still correct, but WCS may still be off
                pass
            try:
                log("WCS: requested +90° rotation so model-long maps to +Y (Maslow away).")
            except:
                pass
        else:
            try:
                log("WCS: no rotation needed (model-long already aligns with +Y).")
            except:
                pass
    except:
        try:
            log("WCS rotation: API not available on this Fusion build; stock will still be correct.")
        except:
            pass

    # 6) Verify stock >= model (in the intended mapping) before setting origin
    # If rotate_wcs_90, model_x maps to Y and model_y maps to X
    fit_x = (model_y if rotate_wcs_90 else model_x) + 2.0 * margin_mm
    fit_y = (model_x if rotate_wcs_90 else model_y) + 2.0 * margin_mm
    if fit_x > stockX + 1e-6 or fit_y > stockY + 1e-6:
        raise RuntimeError(
            f"Stock too small after orientation. Need {fit_x:.1f}x{fit_y:.1f} mm, have {stockX:.1f}x{stockY:.1f} mm"
        )

    # 7) Now set origin top-center (only after verification)
    ok = _set_wcs_top_center_after_verified(setup)
    if not ok:
        log("WCS origin still not set; see parameter dump above.")
        _dump_setup_params(setup)

    try:
        log(f"Setup orientation complete: sheetClass={cname} stockX={stockX:.1f} stockY={stockY:.1f} "
            f"modelX={model_x:.1f} modelY={model_y:.1f} rotateWCS90={rotate_wcs_90}")
    except:
        pass

    # --- HARD LOCK: prevent Fusion from re-sizing stock to model ---
    try:
        params = setup.parameters

        # Force model position to Center AFTER stock dims are set
        for nm in [
            'job_stockFixedBoxPosition',
            'stockFixedBoxPosition',
            'job_stockPosition',
            'stockPosition'
        ]:
            try:
                p = params.itemByName(nm)
                if p:
                    p.expression = 'center'
            except:
                pass

        # Explicitly disable "Ground stock at model origin"
        for nm in [
            'job_stockGroundToModel',
            'stockGroundToModel',
            'job_groundStockAtModelOrigin'
        ]:
            try:
                p = params.itemByName(nm)
                if p:
                    try:
                        p.value = False
                    except:
                        p.expression = 'false'
            except:
                pass

        log("Stock lock applied: fixed box preserved.")
    except:
        log("WARNING: failed to hard-lock stock; Fusion may resize it.")


    return {"sheetClass": cname, "rotateWCS90": rotate_wcs_90, "stockX": stockX, "stockY": stockY}


# ============================================================
# GEOMETRY HELPERS
# ============================================================

def _bbox_mm(body: adsk.fusion.BRepBody):
    # Fusion internal length is cm -> convert to mm via *10
    bb = body.boundingBox
    return (
        bb.minPoint.x * 10.0, bb.minPoint.y * 10.0, bb.minPoint.z * 10.0,
        bb.maxPoint.x * 10.0, bb.maxPoint.y * 10.0, bb.maxPoint.z * 10.0
    )

def _resolve_native(src):
    try:
        if hasattr(src, "nativeObject") and src.nativeObject:
            return src.nativeObject
    except:
        pass
    return src

def _stable_key(src):
    try:
        return id(_resolve_native(src))
    except:
        return id(src)

def _orient_sheet_exprs_long_y(design: adsk.fusion.Design, w_expr: str, h_expr: str):
    """
    Returns (w_expr2, h_expr2) such that evaluated H (Y) is the long side.
    We keep it expression-based so CAM params get the same orientation.
    """
    try:
        w_mm = _eval_mm(design, w_expr)
        h_mm = _eval_mm(design, h_expr)
        if w_mm > h_mm:
            try:
                log(f"Swapping sheet exprs for +Y long: W={w_mm:.1f} H={h_mm:.1f}")
            except:
                pass
            return (h_expr, w_expr)
    except Exception as e:
        try:
            log(f"Sheet orient helper failed: {e}")
        except:
            pass
    return (w_expr, h_expr)

def _copy_body_to_component_via_temp(design: adsk.fusion.Design,
                                     body: adsk.fusion.BRepBody,
                                     target_occ: adsk.fusion.Occurrence,
                                     rotate_90: bool,
                                     base_feat: adsk.fusion.BaseFeature = None) -> adsk.fusion.BRepBody:
    """
    TEMP insert (stable):
      - TemporaryBRepManager.copy()
      - optional rotate in TEMP
      - flatten to Z=0 in TEMP (cookie-cutter)
      - insert via bRepBodies.add(tmp, base_feat) when provided
      - retry ladder to reduce hard failures

    Returns new body in target_occ.component or None.
    """
    try:
        target_comp = target_occ.component
        src = _resolve_native(body)

        temp_mgr = adsk.fusion.TemporaryBRepManager.get()
        tmp = temp_mgr.copy(src)
        if not tmp:
            return None

        # Optional rotate (TEMP)
        if rotate_90:
            bb = tmp.boundingBox  # cm
            pivot = adsk.core.Point3D.create(bb.minPoint.x, bb.minPoint.y, 0.0)
            R = adsk.core.Matrix3D.create()
            R.setToRotation(math.radians(90.0), adsk.core.Vector3D.create(0, 0, 1), pivot)
            if not temp_mgr.transform(tmp, R):
                return None

        # Flatten to Z=0 in TEMP (cookie-cutter)
        bb2 = tmp.boundingBox  # cm
        minz = bb2.minPoint.z
        if abs(minz) > 1e-9:
            Tz = adsk.core.Matrix3D.create()
            Tz.translation = adsk.core.Vector3D.create(0.0, 0.0, -minz)
            if not temp_mgr.transform(tmp, Tz):
                return None

        # --- Insert retry ladder ---
        # A) if caller provided a baseFeature (recommended)
        if base_feat:
            try:
                nb = target_comp.bRepBodies.add(tmp, base_feat)
                return nb
            except:
                pass

        # B) try creating a baseFeature (single insert) if none was provided
        try:
            bf = target_comp.features.baseFeatures.add()
            bf.startEdit()
            nb = target_comp.bRepBodies.add(tmp, bf)
            bf.finishEdit()
            return nb
        except:
            try:
                # Ensure edit closes if it got opened
                if 'bf' in locals() and bf:
                    bf.finishEdit()
            except:
                pass

        # C) last attempt: some builds support add(tmp) without baseFeature
        try:
            nb = target_comp.bRepBodies.add(tmp)
            return nb
        except:
            pass

        return None
    except:
        return None

# ============================================================
# COMPONENT / INSERT / MOVE
# ============================================================

def _ensure_component_occurrence(root: adsk.fusion.Component, comp_name: str) -> adsk.fusion.Occurrence:
    try:
        for occ in root.occurrences:
            if occ.component and occ.component.name == comp_name:
                return occ
    except:
        pass

    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = comp_name
    return occ


def _tmp_flatten_and_measure_footprint_mm(src_body, rot_90: bool):
    """
    Temp copy -> optional rotate 90° about Z -> translate so minZ==0 -> measure XY.
    Returns (w_mm, h_mm) or None.
    """
    src = _resolve_native(src_body)
    try:
        temp_mgr = adsk.fusion.TemporaryBRepManager.get()
        tmp = temp_mgr.copy(src)
        if not tmp:
            return None

        if rot_90:
            bb = tmp.boundingBox  # cm
            pivot = adsk.core.Point3D.create(bb.minPoint.x, bb.minPoint.y, 0.0)
            R = adsk.core.Matrix3D.create()
            R.setToRotation(math.radians(90.0), adsk.core.Vector3D.create(0, 0, 1), pivot)
            if not temp_mgr.transform(tmp, R):
                return None

        # flatten to Z=0
        bb = tmp.boundingBox
        minz = bb.minPoint.z
        Tz = adsk.core.Matrix3D.create()
        Tz.translation = adsk.core.Vector3D.create(0.0, 0.0, -minz)
        if not temp_mgr.transform(tmp, Tz):
            return None

        bb2 = tmp.boundingBox
        w_mm = abs(bb2.maxPoint.x - bb2.minPoint.x) * 10.0
        h_mm = abs(bb2.maxPoint.y - bb2.minPoint.y) * 10.0
        return (w_mm, h_mm)
    except:
        return None

def auto_layout_visible_bodies_multi_sheet(design: adsk.fusion.Design,
                                          ui: adsk.core.UserInterface,
                                          layout_base_name: str,
                                          sheet_w_expr: str,
                                          sheet_h_expr: str,
                                          margin_expr: str,
                                          gap_expr: str,
                                          allow_rotate_90: bool,
                                          hide_originals: bool):
    """
    Multi-sheet-class nesting with stable Fusion insert/move semantics.

    Uses global SHEET_CLASSES (CONFIG section). Order matters: earlier classes are preferred.
    Adds:
      - Collector diagnostics (and includes hidden 'Layer_' bodies even if USE_VISIBLE_BODIES_ONLY=True)
      - Proxy-aware dedupe (occurrence proxies treated as unique)
      - Sanitized footprints (TEMP flatten footprint sanity-checked vs bbox)
      - Prefer TEMP insert for FINAL; copyToComponent only as last-resort for rot=False
      - One BaseFeature per sheet for TEMP inserts
      - Translation-only moves + drop to Z=0 (cookie-cutter)
      - Defer to next sheet if it simply doesn't fit remaining space on current sheet
      - Optional U-pairing prepass (ENABLE_U_PAIRING) to interlock concave-ish parts
      - Cleaner names: sheets named SHEET_##_<CLASS>, inserted bodies named from source (optionally compacted)
    """
    root = design.rootComponent

    # ----------------------------
    # Local knobs (fallbacks if not defined in CONFIG)
    # ----------------------------
    enable_u_pairing = globals().get("ENABLE_U_PAIRING", False)
    u_fill_ratio_max = float(globals().get("U_FILL_RATIO_MAX", 0.70))
    u_min_area_mm2   = float(globals().get("U_MIN_AREA_MM2", 250000.0))
    u_pair_step_mm   = float(globals().get("U_PAIR_STEP_MM", 25.0))
    u_pair_grid_n    = int(globals().get("U_PAIR_GRID_N", 5))
    u_pair_min_gain  = float(globals().get("U_PAIR_MIN_GAIN", 0.10))
    compact_part_names = bool(globals().get("COMPACT_PART_NAMES", True))

    # ----------------------------
    # Safe translation-only move (self-contained)
    # ----------------------------
    def _move_translate_only(comp: adsk.fusion.Component,
                             body: adsk.fusion.BRepBody,
                             tx_mm: float, ty_mm: float, tz_mm: float):
        # Fusion internal distance is cm; mm -> cm = *0.1
        dx = tx_mm * 0.1
        dy = ty_mm * 0.1
        dz = tz_mm * 0.1
        mv_feats = comp.features.moveFeatures
        objs = adsk.core.ObjectCollection.create()
        objs.add(body)
        xform = adsk.core.Matrix3D.create()
        xform.translation = adsk.core.Vector3D.create(dx, dy, dz)
        inp = mv_feats.createInput(objs, xform)
        mv_feats.add(inp)

    # ----------------------------
    # Margin/gap in mm (sheet sizes come from SHEET_CLASSES)
    # ----------------------------
    try:
        _ = _eval_mm(design, sheet_w_expr)
        _ = _eval_mm(design, sheet_h_expr)
    except:
        pass
    margin = _eval_mm(design, margin_expr)
    gap = _eval_mm(design, gap_expr)

    # ----------------------------------------------------------------
    # ENSURE SHEET LONG DIMENSION IS +Y
    # If the sheet width (X) is greater than the sheet height (Y),
    # rotate the sheet definition so +Y holds the long direction.
    # ----------------------------------------------------------------
    try:
        # Evaluate provided sheet expressions
        sheet_w_mm = _eval_mm(design, sheet_w_expr)
        sheet_h_mm = _eval_mm(design, sheet_h_expr)

        # If sheet_w is the greater dimension, we want +Y long,
        # so swap usable definitions
        if sheet_w_mm > sheet_h_mm:
            log(f"Swapping sheet dims for orientation: original W={sheet_w_mm:.1f} H={sheet_h_mm:.1f}")
            # swap expressions so Y becomes long direction
            tmp_expr = sheet_w_expr
            sheet_w_expr = sheet_h_expr
            sheet_h_expr = tmp_expr

            # Reevaluate to get new mm values
            usable_swap_w = sheet_w_mm
            usable_swap_h = sheet_h_mm

            # recompute usable area with swapped dimensions
            sheet_w_mm = usable_swap_h
            sheet_h_mm = usable_swap_w
            usable_w = sheet_w_mm - 2.0 * margin
            usable_h = sheet_h_mm - 2.0 * margin
            log(f" After swap: sheet W={sheet_w_mm:.1f} H={sheet_h_mm:.1f}, usable={usable_w:.1f}x{usable_h:.1f}")

    except Exception as e:
        try:
             log(f"Orientation fix skipped: {str(e)}")
        except: pass


    # ----------------------------
    # Collector with diagnostics
    # ----------------------------
    def _collect_slice_solids_with_diagnostics(design_: adsk.fusion.Design):
        r = design_.rootComponent
        included = []

        stats = {
            "seen_total": 0,
            "seen_root": 0,
            "seen_occ": 0,
            "non_brep_or_null": 0,
            "not_solid": 0,
            "filtered_visibility": 0,
            "included": 0,
            "deduped_out": 0,
        }
        excluded_samples = []  # (name, reason, where)

        def _record_excluded(b, reason: str, where: str):
            nm = "(unnamed)"
            try:
                nm = (getattr(b, "name", "") or nm)
            except:
                pass
            if len(excluded_samples) < 60:
                excluded_samples.append((nm, reason, where))

        def _want_body(b, where: str):
            stats["seen_total"] += 1
            if where == "root":
                stats["seen_root"] += 1
            else:
                stats["seen_occ"] += 1

            if not b:
                stats["non_brep_or_null"] += 1
                _record_excluded(b, "null body", where)
                return False

            try:
                if not hasattr(b, "isSolid"):
                    stats["non_brep_or_null"] += 1
                    _record_excluded(b, "not a BRepBody", where)
                    return False
            except:
                stats["non_brep_or_null"] += 1
                _record_excluded(b, "not a BRepBody", where)
                return False

            try:
                if not b.isSolid:
                    stats["not_solid"] += 1
                    _record_excluded(b, "not solid (surface body)", where)
                    return False
            except:
                stats["not_solid"] += 1
                _record_excluded(b, "isSolid check failed", where)
                return False

            nm = (getattr(b, "name", "") or "")
            is_layer_named = ("layer_" in nm.lower())

            if USE_VISIBLE_BODIES_ONLY:
                try:
                    if (not b.isVisible) and (not is_layer_named):
                        stats["filtered_visibility"] += 1
                        _record_excluded(b, "hidden (and not Layer_*)", where)
                        return False
                except:
                    pass

            return True

        # root bodies
        try:
            for b in r.bRepBodies:
                if _want_body(b, "root"):
                    included.append(b)
        except:
            pass

        # occurrence bodies (proxies)
        try:
            for occ in r.allOccurrences:
                comp = occ.component
                if not comp:
                    continue
                for b in comp.bRepBodies:
                    if not _want_body(b, "occ"):
                        continue
                    try:
                        included.append(b.createForAssemblyContext(occ))
                    except:
                        included.append(b)
        except:
            pass

        # proxy-aware dedupe:
        # - keep proxies unique (key by proxy id)
        # - dedupe natives by native id
        dedup = []
        seen = set()
        for b in included:
            try:
                is_proxy = (hasattr(b, "assemblyContext") and b.assemblyContext is not None)
            except:
                is_proxy = False

            if is_proxy:
                k = ("proxy", id(b))
            else:
                try:
                    k = ("native", id(_resolve_native(b)))
                except:
                    k = ("native", id(b))

            if k in seen:
                stats["deduped_out"] += 1
                continue
            seen.add(k)
            dedup.append(b)

        stats["included"] = len(dedup)
        return dedup, stats, excluded_samples

    bodies, diag_stats, diag_excluded = _collect_slice_solids_with_diagnostics(design)

    try:
        log("Collector diagnostics:")
        log(f"  seen_total={diag_stats['seen_total']} (root={diag_stats['seen_root']}, occ={diag_stats['seen_occ']})")
        log(f"  included={diag_stats['included']} deduped_out={diag_stats['deduped_out']}")
        log(f"  excluded_not_solid={diag_stats['not_solid']}")
        log(f"  excluded_hidden={diag_stats['filtered_visibility']}")
        log(f"  excluded_non_brep_or_null={diag_stats['non_brep_or_null']}")
        if diag_excluded:
            log("  First excluded samples (name | reason | where):")
            for nm, reason, where in diag_excluded[:20]:
                log(f"    - {nm} | {reason} | {where}")
    except:
        pass

    if not bodies:
        ui.messageBox(
            "No eligible BRep solid bodies found to layout.\n\n"
            "Check Desktop log for 'Collector diagnostics'."
        )
        return []

    # ----------------------------
    # Footprint helpers (sanitized footprint)
    # ----------------------------
    def _bbox_footprint_mm(src_body, rot_90: bool):
        try:
            n = _resolve_native(src_body)
            x0, y0, _z0, x1, y1, _z1 = _bbox_mm(n)
            w = abs(x1 - x0)
            h = abs(y1 - y0)
            return (h, w) if rot_90 else (w, h)
        except:
            return None

    def _sanitized_footprint_mm(src_body, rot_90: bool):
        name = getattr(src_body, "name", "(unnamed)")
        bb = _bbox_footprint_mm(src_body, rot_90)
        tmp = _tmp_flatten_and_measure_footprint_mm(src_body, rot_90)

        if not tmp:
            if bb:
                try: log(f"Footprint FALLBACK(bbox): {name} rot={rot_90} bbox={bb}")
                except: pass
                return bb
            return None

        if not bb:
            try: log(f"Footprint TEMP(no bbox): {name} rot={rot_90} tmp={tmp}")
            except: pass
            return tmp

        tmp_area = max(tmp[0], 0.001) * max(tmp[1], 0.001)
        bb_area = max(bb[0], 0.001) * max(bb[1], 0.001)
        ratio = tmp_area / bb_area
        tmp_max = max(tmp[0], tmp[1])
        bb_max = max(bb[0], bb[1])

        if ratio < 0.01 or (tmp_max < 25.0 and bb_max > 100.0):
            try: log(f"Footprint SANITY->bbox: {name} rot={rot_90} tmp={tmp} bbox={bb} ratio={ratio:.6f}")
            except: pass
            return bb

        try: log(f"Footprint TEMP ok: {name} rot={rot_90} tmp={tmp} bbox={bb} ratio={ratio:.6f}")
        except: pass
        return tmp

    # ----------------------------
    # Sheet-class chooser (uses global SHEET_CLASSES)
    # ----------------------------
    def _usable_for_class(sw_mm: float, sh_mm: float):
        return (sw_mm - 2.0 * margin, sh_mm - 2.0 * margin)

    def _best_class_for_dims(w_mm: float, h_mm: float):
        for cname, sw, sh in SHEET_CLASSES:
            uw, uh = _usable_for_class(sw, sh)
            if w_mm <= uw and h_mm <= uh:
                return (cname, sw, sh, uw, uh)
        return None

    # Prefer 4x8 if it fits (either orientation), otherwise smallest class that fits.
    def _pick_best_sheet_and_rot(fp0, fp1):
        local_order = {cname: i for i, (cname, _sw, _sh) in enumerate(SHEET_CLASSES)}

        # hard preference: STD_4x8 if it fits in any allowed orientation
        if allow_rotate_90:
            rots = [False, True]
        else:
            rots = [False]

        for rot in rots:
            fp = fp1 if rot else fp0
            if not fp:
                continue
            ok = _best_class_for_dims(fp[0], fp[1])
            if ok and ok[0] == "STD_4x8":
                return ok, rot

        # otherwise choose smallest class that fits
        candidates = []
        for rot in rots:
            fp = fp1 if rot else fp0
            if not fp:
                continue
            ok = _best_class_for_dims(fp[0], fp[1])
            if ok:
                candidates.append((local_order.get(ok[0], 999), ok, rot))

        if not candidates:
            return None, False

        candidates.sort(key=lambda t: t[0])
        _ord, ok, rot = candidates[0]
        return ok, rot

    # ----------------------------
    # U-ish detection (volume vs bbox volume proxy)
    # ----------------------------
    def _fill_ratio_xy(src_body) -> float:
        try:
            n = _resolve_native(src_body)
            x0,y0,z0,x1,y1,z1 = _bbox_mm(n)
            bw = max(abs(x1-x0), 1e-6)
            bh = max(abs(y1-y0), 1e-6)
            bz = max(abs(z1-z0), 1e-6)

            props = n.physicalProperties
            vol_cm3 = props.volume  # cm^3
            vol_mm3 = vol_cm3 * 1000.0

            bbox_mm3 = bw * bh * bz
            r = vol_mm3 / bbox_mm3
            if r < 0: r = 0.0
            if r > 1.5: r = 1.5
            return r
        except:
            return 1.0

    def _no_interference(design_: adsk.fusion.Design, a: adsk.fusion.BRepBody, b: adsk.fusion.BRepBody) -> bool:
        try:
            oc = adsk.core.ObjectCollection.create()
            oc.add(a); oc.add(b)
            res = design_.analyzeInterference(oc)
            return (res.count == 0)
        except:
            return False

    def _rot180_insert_temp(design_, body_, target_occ_, base_feat_):
        """TEMP copy + rotate 180° about Z + flatten to Z=0; insert into target occ."""
        try:
            target_comp = target_occ_.component
            src = _resolve_native(body_)
            temp_mgr = adsk.fusion.TemporaryBRepManager.get()
            tmp = temp_mgr.copy(src)
            if not tmp:
                return None

            # pivot about bbox min corner (cm)
            bb = tmp.boundingBox
            pivot = adsk.core.Point3D.create(bb.minPoint.x, bb.minPoint.y, 0.0)

            R = adsk.core.Matrix3D.create()
            R.setToRotation(math.radians(180.0), adsk.core.Vector3D.create(0,0,1), pivot)
            if not temp_mgr.transform(tmp, R):
                return None

            # flatten to Z=0
            bb2 = tmp.boundingBox
            minz = bb2.minPoint.z
            if abs(minz) > 1e-9:
                Tz = adsk.core.Matrix3D.create()
                Tz.translation = adsk.core.Vector3D.create(0.0, 0.0, -minz)
                if not temp_mgr.transform(tmp, Tz):
                    return None

            # insert
            if base_feat_:
                try:
                    return target_comp.bRepBodies.add(tmp, base_feat_)
                except:
                    pass

            try:
                bf = target_comp.features.baseFeatures.add()
                bf.startEdit()
                nb = target_comp.bRepBodies.add(tmp, bf)
                bf.finishEdit()
                return nb
            except:
                try:
                    if 'bf' in locals() and bf:
                        bf.finishEdit()
                except:
                    pass
            try:
                return target_comp.bRepBodies.add(tmp)
            except:
                return None
        except:
            return None

    def _clean_part_name(nm: str) -> str:
        if not compact_part_names:
            return nm
        try:
            s = nm.replace("Layer_", "L").replace("layer_", "L")
            s = s.replace("_part_", "_P").replace("_Part_", "_P").replace("_PART_", "_P")
            return s
        except:
            return nm

    # ----------------------------
    # Build items (compute footprints, choose sheet class, gather fill ratio)
    # ----------------------------
    skipped = []
    items = []

    for b in bodies:
        name = getattr(b, "name", "(unnamed)")
        fp0 = _sanitized_footprint_mm(b, False)
        fp1 = _sanitized_footprint_mm(b, True) if allow_rotate_90 else None

        # optional size sanity log (if enabled in CONFIG)
        if globals().get("LOG_NATIVE_BBOX_SIZES", False):
            try:
                n = _resolve_native(b)
                x0,y0,z0,x1,y1,z1 = _bbox_mm(n)
                log(f"SIZE CHECK native bbox: {name} -> W={abs(x1-x0):.1f}mm H={abs(y1-y0):.1f}mm Z={abs(z1-z0):.1f}mm")
            except:
                pass

        if not fp0:
            skipped.append(f"{name} (missing footprint)")
            continue

        best, best_rot = _pick_best_sheet_and_rot(fp0, fp1)
        if not best:
            skipped.append(f"{name} ({fp0[0]:.1f} x {fp0[1]:.1f} mm) too large for all sheet classes")
            continue

        fr = _fill_ratio_xy(b)
        items.append({
            "is_pair": False,
            "body": b,
            "name": name,
            "fp0": fp0,
            "fp1": fp1,
            "sheet_class": best[0],
            "sheet_w": best[1],
            "sheet_h": best[2],
            "usable_w": best[3],
            "usable_h": best[4],
            "prefer_rot": bool(best_rot),
            "fill_ratio": fr,
        })

    if not items:
        ui.messageBox(
            "Nothing eligible to layout.\n\n"
            "All bodies were skipped (oversize/missing footprint)."
        )
        return []

    # ----------------------------
    # Group by sheet class
    # ----------------------------
    from collections import defaultdict
    groups = defaultdict(list)
    for it in items:
        groups[it["sheet_class"]].append(it)

    class_order = {cname: i for i, (cname, _sw, _sh) in enumerate(SHEET_CLASSES)}
    class_names_sorted = sorted(groups.keys(), key=lambda k: class_order.get(k, 999))

    # ----------------------------
    # Probe cache (proxy-aware)
    # ----------------------------
    probe_cache = {}

    def _probe_key_for_body(src_body):
        try:
            is_proxy = (hasattr(src_body, "assemblyContext") and src_body.assemblyContext is not None)
        except:
            is_proxy = False
        if is_proxy:
            return ("proxy", id(src_body))
        try:
            return ("native", id(_resolve_native(src_body)))
        except:
            return ("native", id(src_body))

    def _probe_method(src_body, sheet_occ, rot_90: bool, base_feat: adsk.fusion.BaseFeature):
        ck = (_probe_key_for_body(src_body), bool(rot_90))
        if ck in probe_cache:
            return probe_cache[ck]

        name = getattr(src_body, "name", "(unnamed)")
        try: log(f"Copy start(PROBE): {name} rot={rot_90}")
        except: pass

        # rot=True must be TEMP
        if rot_90:
            nb = _copy_body_to_component_via_temp(design, src_body, sheet_occ, rotate_90=True, base_feat=base_feat)
            if nb:
                try: nb.deleteMe()
                except: pass
                probe_cache[ck] = "temp"
                try: log(f"Probe OK(temp): {name} rot={rot_90}")
                except: pass
                return "temp"

            probe_cache[ck] = ""
            try: log(f"Probe FAIL(all): {name} rot={rot_90}")
            except: pass
            return ""

        # rot=False: prefer TEMP first, fallback copyToComponent
        nb = _copy_body_to_component_via_temp(design, src_body, sheet_occ, rotate_90=False, base_feat=base_feat)
        if nb:
            try: nb.deleteMe()
            except: pass
            probe_cache[ck] = "temp"
            try: log(f"Probe OK(temp): {name} rot={rot_90}")
            except: pass
            return "temp"

        try:
            native = _resolve_native(src_body)
            nb2 = native.copyToComponent(sheet_occ)
            if nb2:
                try: nb2.deleteMe()
                except: pass
                probe_cache[ck] = "copy"
                try: log(f"Probe OK(copy): {name} rot={rot_90}")
                except: pass
                return "copy"
        except:
            pass

        probe_cache[ck] = ""
        try: log(f"Probe FAIL(all): {name} rot={rot_90}")
        except: pass
        return ""

    def _final_insert(src_body, sheet_occ, rot_90: bool, method: str, base_feat: adsk.fusion.BaseFeature):
        name = getattr(src_body, "name", "(unnamed)")
        try: log(f"Copy start(FINAL): {name} rot={rot_90} method={method}")
        except: pass

        if method == "temp":
            return _copy_body_to_component_via_temp(design, src_body, sheet_occ,
                                                    rotate_90=bool(rot_90), base_feat=base_feat)
        if method == "copy":
            try:
                native = _resolve_native(src_body)
                return native.copyToComponent(sheet_occ)
            except:
                return None
        return None

    # ----------------------------
    # U-pairing prepass (per class)
    # ----------------------------
    def _u_pair_group(class_name: str, class_items: list):
        if not enable_u_pairing:
            return class_items

        # Only consider larger, more concave-ish items
        candidates = []
        for it in class_items:
            w, h = it["fp0"]
            area = w * h
            if area < u_min_area_mm2:
                continue
            if it.get("fill_ratio", 1.0) > u_fill_ratio_max:
                continue
            candidates.append(it)

        if len(candidates) < 2:
            return class_items

        # Temp occurrence for collision testing
        tmp_occ = _ensure_component_occurrence(root, "_PAIR_TMP")
        tmp_comp = tmp_occ.component
        tmp_bf = None
        try:
            tmp_bf = tmp_comp.features.baseFeatures.add()
            tmp_bf.startEdit()
        except:
            tmp_bf = None

        try:
            used = set()
            new_items = []
            remaining = list(class_items)

            # Greedy pairing: for each A in descending size, find best B
            remaining_sorted = sorted(remaining, key=lambda it: (it["fp0"][0]*it["fp0"][1]), reverse=True)

            for a in remaining_sorted:
                if id(a) in used:
                    continue
                if a not in candidates:
                    continue

                aw, ah = a["fp0"]
                best = None  # (gain, b_item, dx, dy, union_w, union_h)

                # precompute: sum area
                a_area = aw * ah

                for b in remaining_sorted:
                    if b is a:
                        continue
                    if id(b) in used:
                        continue
                    if b not in candidates:
                        continue

                    bw, bh = b["fp0"]
                    base_sum_area = a_area + (bw * bh)

                    # Insert temp A (normal) and temp B (180°)
                    ta = _copy_body_to_component_via_temp(design, a["body"], tmp_occ, rotate_90=False, base_feat=tmp_bf)
                    tb = _rot180_insert_temp(design, b["body"], tmp_occ, tmp_bf)
                    if not ta or not tb:
                        try:
                            if ta: ta.deleteMe()
                            if tb: tb.deleteMe()
                        except:
                            pass
                        continue

                    # Normalize both to origin and Z=0
                    ax0,ay0,az0,ax1,ay1,az1 = _bbox_mm(ta)
                    try:
                        _move_translate_only(tmp_comp, ta, -min(ax0,ax1), -min(ay0,ay1), -max(az0,az1))
                    except:
                        pass

                    bx0,by0,bz0,bx1,by1,bz1 = _bbox_mm(tb)
                    try:
                        _move_translate_only(tmp_comp, tb, -min(bx0,bx1), -min(by0,by1), -max(bz0,bz1))
                    except:
                        pass

                    half = max(1, int(u_pair_grid_n // 2))
                    step = float(u_pair_step_mm)

                    for gx in range(-half, half+1):
                        for gy in range(-half, half+1):
                            dx = gx * step
                            dy = gy * step

                            # Reset tb to origin then move by dx/dy
                            bx0,by0,bz0,bx1,by1,bz1 = _bbox_mm(tb)
                            try:
                                _move_translate_only(tmp_comp, tb, -min(bx0,bx1), -min(by0,by1), 0.0)
                                _move_translate_only(tmp_comp, tb, dx, dy, 0.0)
                            except:
                                continue

                            if not _no_interference(design, ta, tb):
                                continue

                            ax0,ay0,az0,ax1,ay1,az1 = _bbox_mm(ta)
                            bx0,by0,bz0,bx1,by1,bz1 = _bbox_mm(tb)
                            ux0 = min(ax0, ax1, bx0, bx1)
                            uy0 = min(ay0, ay1, by0, by1)
                            ux1 = max(ax0, ax1, bx0, bx1)
                            uy1 = max(ay0, ay1, by0, by1)
                            uw = ux1 - ux0
                            uh = uy1 - uy0
                            union_area = uw * uh

                            gain = 1.0 - (union_area / max(base_sum_area, 1e-6))
                            if gain <= 0:
                                continue

                            if (best is None) or (gain > best[0]):
                                best = (gain, b, dx, dy, uw, uh)

                    # cleanup temps
                    try:
                        ta.deleteMe()
                        tb.deleteMe()
                    except:
                        pass

                if best and best[0] >= u_pair_min_gain:
                    gain, b, dx, dy, uw, uh = best
                    used.add(id(a))
                    used.add(id(b))

                    pair_name = f"PAIR_{a['name']}__{b['name']}"
                    try:
                        log(f"Paired {a['name']} + {b['name']} gain={gain:.2%} union={uw:.1f}x{uh:.1f}mm class={class_name}")
                    except:
                        pass

                    new_items.append({
                        "is_pair": True,
                        "name": pair_name,
                        "sheet_class": class_name,
                        "fp0": (uw, uh),
                        "fp1": None,
                        "prefer_rot": False,
                        "children": [
                            (a, False, False, 0.0, 0.0),
                            (b, False, True,  dx,  dy),
                        ]
                    })

            # Add any unpaired originals
            for it in remaining:
                if id(it) in used:
                    continue
                new_items.append(it)

            return new_items
        finally:
            try:
                if tmp_bf:
                    tmp_bf.finishEdit()
            except:
                pass

    # Apply U-pairing and sort within each class
    for cn in class_names_sorted:
        groups[cn] = _u_pair_group(cn, groups[cn])
        groups[cn].sort(key=lambda it: max(it["fp0"][0], it["fp0"][1]), reverse=True)

    # ----------------------------
    # Packing routine for one class (shelf pack; stable)
    # ----------------------------
    def _pack_class(class_name: str, class_items: list, sheet_w_mm: float, sheet_h_mm: float, global_sheet_start_index: int):
        usable_w, usable_h = _usable_for_class(sheet_w_mm, sheet_h_mm)
        sheets_local = []
        failures_probe = []
        failures_insert = []
        failures_move = []

        remaining = list(class_items)
        sheet_index = 1
        global_sheet_index = global_sheet_start_index

        while remaining:
            # Cleaner, predictable sheet naming
            sheet_name = f"SHEET_{global_sheet_index:02d}_{class_name}"
            sheet_occ = _ensure_component_occurrence(root, sheet_name)
            sheet_comp = sheet_occ.component

            # One BaseFeature per sheet for TEMP inserts
            sheet_base_feat = None
            try:
                sheet_base_feat = sheet_comp.features.baseFeatures.add()
                sheet_base_feat.startEdit()
            except:
                sheet_base_feat = None

            try:
                log(f"--- SHEET START {sheet_name} remaining={len(remaining)} usable={usable_w:.1f}x{usable_h:.1f} ---")
            except:
                pass

            x = 0.0
            y = 0.0
            row_h = 0.0
            placed_any = False
            next_remaining = []

            for idx, it in enumerate(remaining):
                if idx % 10 == 0:
                    try: adsk.doEvents()
                    except: pass

                w0, h0 = it["fp0"]
                fp1 = it.get("fp1", None)

                # Determine orientation tries
                if it.get("prefer_rot", False) and allow_rotate_90 and fp1:
                    tries = [(True, fp1[0], fp1[1]), (False, w0, h0)]
                else:
                    tries = [(False, w0, h0)]
                    if allow_rotate_90 and fp1:
                        tries.append((True, fp1[0], fp1[1]))

                placed_this = False
                probed_any = False

                for rot, w, h in tries:
                    if w > usable_w or h > usable_h:
                        continue

                    # new row if needed
                    if x > 0.0 and (x + w) > usable_w:
                        x = 0.0
                        y += row_h + gap
                        row_h = 0.0

                    # no vertical space left on this sheet
                    if (y + h) > usable_h:
                        continue

                    # If this is a composite "pair", place both children
                    if it.get("is_pair", False):
                        inserted = []
                        ok_pair = True

                        # Insert children in sheet component
                        for child_it, child_rot90, child_rot180, cdx, cdy in it["children"]:
                            src_body = child_it["body"]
                            if child_rot180:
                                nb = _rot180_insert_temp(design, src_body, sheet_occ, sheet_base_feat)
                            else:
                                nb = _copy_body_to_component_via_temp(design, src_body, sheet_occ,
                                                                      rotate_90=bool(child_rot90), base_feat=sheet_base_feat)
                            if not nb:
                                ok_pair = False
                                break

                            try:
                                nb.name = _clean_part_name(child_it["name"])
                            except:
                                pass

                            inserted.append((nb, cdx, cdy))

                        if not ok_pair:
                            try:
                                for nb, _, _ in inserted:
                                    nb.deleteMe()
                            except:
                                pass
                            failures_insert.append(f"{it['name']}: pair insert failed")
                            probed_any = True  # we did attempt insertion
                            continue

                        # Anchor placement using first child bbox
                        nb0, _, _ = inserted[0]
                        x0,y0,z0,x1,y1,z1 = _bbox_mm(nb0)
                        anchor_tx = (margin + x) - min(x0, x1)
                        anchor_ty = (margin + y) - min(y0, y1)
                        anchor_tz = -max(z0, z1)

                        try:
                            for nb, cdx, cdy in inserted:
                                _move_translate_only(sheet_comp, nb, anchor_tx + cdx, anchor_ty + cdy, anchor_tz)
                        except Exception as e:
                            try:
                                for nb, _, _ in inserted:
                                    nb.deleteMe()
                            except:
                                pass
                            failures_move.append(f"{it['name']}: pair move failed ({str(e)})")
                            probed_any = True
                            continue

                        # Success
                        x += w + gap
                        row_h = max(row_h, h)
                        placed_any = True
                        placed_this = True
                        break

                    # Otherwise: single body placement with probe + final insert
                    src_body = it["body"]
                    method = _probe_method(src_body, sheet_occ, rot, sheet_base_feat)
                    probed_any = True
                    if not method:
                        continue

                    nb = _final_insert(src_body, sheet_occ, rot, method, sheet_base_feat)
                    if not nb:
                        failures_insert.append(f"{it['name']}: final insert failed (rot={rot}, method={method})")
                        continue

                    # Rename inserted body (traceability)
                    try:
                        nb.name = _clean_part_name(it["name"])
                    except:
                        pass

                    # Translate-only placement to margin+(x,y), drop to Z=0
                    x0, y0, z0, x1, y1, z1 = _bbox_mm(nb)
                    tx = (margin + x) - min(x0, x1)
                    ty = (margin + y) - min(y0, y1)
                    tz = -max(z0, z1)

                    try:
                        _move_translate_only(sheet_comp, nb, tx, ty, tz)
                    except Exception as e:
                        try: nb.deleteMe()
                        except: pass
                        failures_move.append(f"{it['name']}: move failed ({str(e)})")
                        continue

                    x += w + gap
                    row_h = max(row_h, h)
                    placed_any = True
                    placed_this = True
                    break

                if not placed_this:
                    # If we never probed/inserted because it simply didn't fit remaining space, defer to next sheet
                    if not probed_any:
                        next_remaining.append(it)
                    else:
                        failures_probe.append(f"{it['name']}: probe/insert failed (all orientations)")

            # Finish BaseFeature edit
            try:
                if sheet_base_feat:
                    sheet_base_feat.finishEdit()
            except:
                pass

            if placed_any:
                sheets_local.append(sheet_occ)
                remaining = next_remaining
                sheet_index += 1
                global_sheet_index += 1
                continue

            # nothing placed -> stop
            ui.messageBox(
                f"Layout stopped for {class_name}: no bodies could be placed on this sheet.\n\n"
                "Check Desktop log for failures."
            )
            break

        return sheets_local, failures_probe, failures_insert, failures_move, usable_w, usable_h, global_sheet_index

    # ----------------------------
    # Run per class (in preferred order), keep a global sheet counter
    # ----------------------------
    all_sheets = []
    all_fail_probe = []
    all_fail_insert = []
    all_fail_move = []

    global_sheet_index = 1

    for cn in class_names_sorted:
        # Find dims
        sw = sh = None
        for cname, cw, ch in SHEET_CLASSES:
            if cname == cn:
                sw, sh = cw, ch
                break
        if sw is None or sh is None:
            continue

        class_sheets, f_probe, f_ins, f_move, uw, uh, global_sheet_index = _pack_class(
            cn, groups[cn], sw, sh, global_sheet_index
        )
        all_sheets.extend(class_sheets)
        all_fail_probe.extend([f"[{cn}] {s}" for s in f_probe])
        all_fail_insert.extend([f"[{cn}] {s}" for s in f_ins])
        all_fail_move.extend([f"[{cn}] {s}" for s in f_move])

        try:
            log(f"Class complete: {cn} -> sheets={len(class_sheets)} usable={uw:.1f}x{uh:.1f}")
        except:
            pass

    # ----------------------------
    # Visibility handling
    # ----------------------------
    if hide_originals and all_sheets:
        for it in items:
            b = it.get("body", None)
            if not b:
                continue
            try:
                bb = b.nativeObject if hasattr(b, "nativeObject") and b.nativeObject else b
                bb.isVisible = False
            except:
                pass

        for occ in all_sheets:
            try:
                for b in occ.component.bRepBodies:
                    if b and b.isSolid:
                        b.isVisible = True
            except:
                pass

    # ----------------------------
    # Summary
    # ----------------------------
    msg = [
        "Layout complete.",
        f"Sheets created: {len(all_sheets)}",
        f"Margin/gap: {margin:.1f}mm / {gap:.1f}mm",
        f"Rotation: {'ENABLED' if allow_rotate_90 else 'DISABLED'}",
        f"Skipped: {len(skipped)}",
        f"Probe failures: {len(all_fail_probe)}",
        f"Insert failures: {len(all_fail_insert)}",
        f"Move failures: {len(all_fail_move)}",
        "Sheet classes used: " + (", ".join(class_names_sorted) if class_names_sorted else "(none)"),
    ]

    if skipped:
        msg.append("")
        msg.append("First skipped:")
        msg.extend([" - " + s for s in skipped[:10]])

    if all_fail_probe or all_fail_insert or all_fail_move:
        msg.append("")
        msg.append("First failures:")
        for s in (all_fail_probe + all_fail_insert + all_fail_move)[:10]:
            msg.append(" - " + s)

    ui.messageBox("\n".join(msg))
    try:
        log(f"Auto layout complete. Sheets: {len(all_sheets)}")
    except:
        pass
    return all_sheets

def configure_stock_and_wcs_for_your_build(setup: adsk.cam.Setup,
                                          sheet_w_expr: str,
                                          sheet_h_expr: str,
                                          sheet_thk_expr: str,
                                          side_off_expr: str,
                                          top_off_expr: str,
                                          bot_off_expr: str):
    """
    Your build uses job_* params (from your dump).
    Best-effort set; missing params won’t crash.
    """
    params: adsk.cam.CAMParameters = setup.parameters

    def _set_expr(name: str, expr: str):
        p = params.itemByName(name)
        if not p:
            return False
        try:
            p.expression = expr
            return True
        except:
            return False

    def _try_enum(name: str, candidates):
        p = params.itemByName(name)
        if not p:
            return False
        for c in candidates:
            try:
                p.value = c
                return True
            except:
                continue
        return False

    _try_enum('job_stockOffsetMode', ['simple', 'advanced'])
    _try_enum('job_stockMode', ['fixedbox', 'fixedBox', 'fixed', 'default'])

    _set_expr('job_stockFixedX', sheet_w_expr)
    _set_expr('job_stockFixedY', sheet_h_expr)
    _set_expr('job_stockFixedZ', sheet_thk_expr)

    _try_enum('job_stockFixedXMode', ['center', 'model'])
    _try_enum('job_stockFixedYMode', ['center', 'model'])
    _try_enum('job_stockFixedZMode', ['center', 'model'])

    _set_expr('job_stockOffsetSides', side_off_expr)
    _set_expr('job_stockOffsetTop', top_off_expr)
    _set_expr('job_stockOffsetBottom', bot_off_expr)

    _try_enum('wcs_origin_mode', ['stockPoint', 'modelOrigin'])
    _try_enum('wcs_origin_boxPoint', ['top center', 'top'])


def apply_maslow_z(op: adsk.cam.Operation):
    p = op.parameters

    def _set_expr(name, expr):
        try:
            pp = p.itemByName(name)
            if pp:
                pp.expression = expr
        except:
            pass

    _set_expr('retractHeight_offset',   MASLOW_RETRACT)
    _set_expr('clearanceHeight_offset', MASLOW_CLEARANCE)
    _set_expr('feedHeight_offset',      MASLOW_FEED)

    _set_expr('plungeFeedrate',   MASLOW_PLUNGE_FEED)
    _set_expr('retractFeedrate',  MASLOW_RETRACT_FEED)

    # often used in your dumps
    _set_expr('tool_feedPlunge',  MASLOW_PLUNGE_FEED)
    _set_expr('tool_feedRetract', MASLOW_RETRACT_FEED)

    # try disabling rapid retract where available
    try:
        ar = p.itemByName('allowRapidRetract')
        if ar:
            try:
                ar.value = False
            except:
                ar.expression = 'false'
    except:
        pass


def find_tool(cam: adsk.cam.CAM):
    """
    Best-effort tool lookup across builds. Returns Tool or None.
    If your build hides tool libraries, return None and user picks tool manually.
    """
    name_key = (PREFERRED_TOOL_NAME_CONTAINS or '').lower()

    def _scan_tools(tools):
        try:
            for i in range(tools.count):
                t = tools.item(i)
                try:
                    if name_key and name_key in (t.name or '').lower():
                        return t
                except:
                    pass
        except:
            pass
        return None

    # try app.camManager.libraryManager.toolLibraries
    try:
        app = adsk.core.Application.get()
        cam_mgr = getattr(app, "camManager", None)
        if cam_mgr:
            lm = getattr(cam_mgr, "libraryManager", None)
            if lm:
                libs = getattr(lm, "toolLibraries", None)
                if libs:
                    for li in range(libs.count):
                        lib = libs.item(li)
                        tools = getattr(lib, "tools", None)
                        if tools:
                            t = _scan_tools(tools)
                            if t:
                                return t
    except:
        pass

    return None


def create_2d_contour_input_best_effort(ops: adsk.cam.Operations,
                                       ui: adsk.core.UserInterface,
                                       warn_once_state: dict):
    """
    Try multiple strategy IDs. Warn only once if none work.
    """
    candidates = [
        '2dContour','2DContour','contour2d','contour2D','Contour2D','2d-contour','2d_contour','2dContourOp',
        '2dProfile','2DProfile','profile2d','profile2D','2d-profile','2d_profile',
        'mill2dContour','Milling2DContour','trace',
    ]

    last_err = None
    for s in candidates:
        try:
            return ops.createInput(s)
        except Exception as e:
            last_err = e

    if not warn_once_state.get("warned", False):
        warn_once_state["warned"] = True
        ui.messageBox(
            "This Fusion build does not expose a 2D Contour strategy ID via API.\n\n"
            "Workaround:\n"
            "- Script will create Adaptive + Scallop.\n"
            "- Add 2D Contour manually once, then save as a Template.\n\n"
            f"Last error: {last_err}"
        )
    return None


def get_cam_product(app: adsk.core.Application,
                    ui: adsk.core.UserInterface,
                    doc: adsk.core.Document) -> adsk.cam.CAM:
    # Try direct
    try:
        cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
        if cam:
            return cam
    except:
        pass

    # Activate Manufacture
    try:
        cam_ws = ui.workspaces.itemById('CAMEnvironment')
        if cam_ws:
            cam_ws.activate()
    except:
        pass

    # Try again
    try:
        cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
        if cam:
            return cam
    except:
        pass

    # last fallback
    try:
        cam = adsk.cam.CAM.cast(app.activeProduct)
        if cam:
            return cam
    except:
        pass

    return None


def create_cam_for_sheets(cam, design, ui, sheets, enforce_orientation_cb=None):
    """
    One setup per sheet occurrence.
    Avoids hard assumptions about param names and strategy IDs.

    Updated:
      - derives occurrences from `sheets` (no `sheet_occs` dependency)
      - calls enforce_orientation_cb(setup, model_bodies_for_setup) right after setup.models assignment
      - falls back to configure_stock_and_wcs_for_your_build() if enforcement is missing/fails
    """
    warn_state = {"warned": False}

    # tool is optional
    tool = None
    try:
        tool = find_tool(cam)
        if tool:
            log(f"Tool auto-picked: {tool.name}")
        else:
            log("Tool auto-pick unavailable; ops will be created without tool selection.")
    except:
        tool = None

    def _set_expr(params, name, expr):
        try:
            p = params.itemByName(name)
            if p:
                p.expression = expr
                return True
        except:
            pass
        return False

    def _set_bool(params, name, val: bool):
        try:
            p = params.itemByName(name)
            if p:
                try:
                    p.value = val
                except:
                    p.expression = 'true' if val else 'false'
                return True
        except:
            pass
        return False

    def _try_assign_tool(op):
        if not tool:
            return
        try:
            op.tool = tool
        except:
            pass

    def _coerce_occ_list_from_sheets(sheets_list):
        """
        Accepts a variety of sheet item shapes:
          - Occurrence directly
          - dict with keys: 'occ', 'occurrence', 'sheet_occ', 'sheetOccurrence'
          - object with attribute .occ or .occurrence
        Returns list[adsk.fusion.Occurrence]
        """
        occs = []
        for s in (sheets_list or []):
            occ = None
            try:
                # direct occurrence
                if hasattr(s, "component") and hasattr(s, "transform"):
                    occ = s
                # dict forms
                elif isinstance(s, dict):
                    occ = s.get("occ") or s.get("occurrence") or s.get("sheet_occ") or s.get("sheetOccurrence")
                else:
                    # attribute forms
                    if hasattr(s, "occ"):
                        occ = getattr(s, "occ")
                    elif hasattr(s, "occurrence"):
                        occ = getattr(s, "occurrence")
            except:
                occ = None

            if occ:
                occs.append(occ)

        return occs

    occ_list = _coerce_occ_list_from_sheets(sheets)
    if not occ_list:
        # Nothing to do
        ui.messageBox("No sheet occurrences found in `sheets`. CAM not created.")
        log("create_cam_for_sheets: no occurrences derived from sheets -> abort.")
        return

    setups_created = 0
    enforcement_failures = 0

    for occ in occ_list:
        # Create setup
        setup_in = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
        setup = cam.setups.add(setup_in)
        try:
            setup.name = f'CAM_{occ.component.name}'
        except:
            setup.name = 'CAM_Sheet'

        # Models = all solid bodies in sheet component
        coll = adsk.core.ObjectCollection.create()
        model_bodies_for_this_setup = []
        try:
            for b in occ.component.bRepBodies:
                if b and b.isSolid:
                    coll.add(b)
                    model_bodies_for_this_setup.append(b)
        except:
            pass

        # Assign models
        try:
            setup.models = coll
        except:
            pass

        # Ensure stock mode is Fixed Box (best effort)
        try:
            setup.stockMode = adsk.cam.SetupStockModes.FixedBoxStock
        except:
            pass

        # # --- NEW: enforce Maslow/Fusion orientation + stock>=model + origin-after-verify ---
        # enforced_ok = False
        # if enforce_orientation_cb:
        #     try:
        #         enforced_ok = bool(enforce_orientation_cb(setup, model_bodies_for_this_setup))
        #     except:
        #         enforced_ok = False

        # if not enforced_ok:
        #     enforcement_failures += 1
        #     # Fallback: your existing build-specific param setter
        #     try:

        #         sheet_w_expr_oriented, sheet_h_expr_oriented = _orient_sheet_exprs_long_y(design, SHEET_W, SHEET_H)
        #         configure_stock_and_wcs_for_your_build(
        #             setup,
        #             sheet_w_expr=sheet_w_expr_oriented,
        #             sheet_h_expr=sheet_h_expr_oriented,
        #             sheet_thk_expr=SHEET_THK,
        #             side_off_expr='0 mm',
        #             top_off_expr='0 mm',
        #             bot_off_expr='0 mm'
        #         )
        #         log(f"{setup.name}: used fallback configure_stock_and_wcs_for_your_build()")
        #     except:
        #         log(f"{setup.name}: WARNING stock/WCS fallback also failed.")
        # else:
        #     log(f"{setup.name}: orientation enforcement applied.")

        ops = setup.operations

        # ---- 2D Contour (best effort) ----
        prof_in = None
        try:
            prof_in = create_2d_contour_input_best_effort(ops, ui, warn_state)
        except:
            prof_in = None

        if prof_in:
            prof_in.displayName = 'Foam Cutout 2D (Profile)'
            prof = ops.add(prof_in)
            _try_assign_tool(prof)

            _set_bool(prof.parameters, 'doRoughingPasses', True)
            _set_bool(prof.parameters, 'doMultipleDepths', True)
            _set_expr(prof.parameters, 'maximumStepdown', PROFILE_STEPDOWN)

            apply_maslow_z(prof)

        # ---- 3D Adaptive ----
        try:
            rough_in = ops.createInput('adaptive')
            rough_in.displayName = 'Foam Rough 3D (Adaptive)'
            rough = ops.add(rough_in)
            _try_assign_tool(rough)

            _set_expr(rough.parameters, 'maximumStepdown', ROUGH_STEPDOWN)
            apply_maslow_z(rough)
        except Exception as e:
            ui.messageBox(f"Failed creating Adaptive op in {setup.name}:\n{e}")

        # ---- 3D Scallop ----
        try:
            fin_in = ops.createInput('scallop')
            fin_in.displayName = 'Foam Finish 3D (Scallop)'
            fin = ops.add(fin_in)
            _try_assign_tool(fin)

            _set_expr(fin.parameters, 'finishingStepdown', FINISH_STEPDOWN)
            _set_expr(fin.parameters, 'maximumStepdown', FINISH_STEPDOWN)

            apply_maslow_z(fin)
        except Exception as e:
            ui.messageBox(f"Failed creating Scallop op in {setup.name}:\n{e}")

        setups_created += 1
        try:
            adsk.doEvents()
        except:
            pass

    ui.messageBox(
        "CAM creation complete.\n\n"
        f"Setups created: {setups_created}\n"
        f"Orientation enforcement failures (used fallback): {enforcement_failures}\n\n"
        "Notes:\n"
        "- If 2D Contour cannot be created by API in this build, add it manually once and save a Template.\n"
        "- If stock sizing didn’t apply automatically, confirm Setup → Stock is correct and save a Template.\n"
        "- If Maslow is still 90° off, your Fusion build may not support WCS rotation via API; we’ll target the exact WCS params next."
    )

# ============================================================
# RUN
# ============================================================

def run(context):
    ui = None
    log("=== RUN START ===")
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface
        doc = app.activeDocument

        log(f"Active doc: {doc.name if doc else 'None'}")
        if not doc:
            ui.messageBox("No active document.")
            log("No active document -> abort.")
            return

        design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
        log(f"Design loaded: {bool(design)}")
        if not design:
            ui.messageBox("Active document is not a Fusion Design (.f3d).")
            log("Not a Fusion Design -> abort.")
            return

        # 1) layout
        sheets = []
        if DO_AUTO_LAYOUT:
            log("Starting auto layout...")

            # Force Maslow/Fusion convention: +Y is long sheet direction (layout stage)
            sheet_w_expr_oriented, sheet_h_expr_oriented = _orient_sheet_exprs_long_y(design, SHEET_W, SHEET_H)

            sheets = auto_layout_visible_bodies_multi_sheet(
                design=design,
                ui=ui,
                layout_base_name=LAYOUT_BASE_NAME,
                sheet_w_expr=sheet_w_expr_oriented,
                sheet_h_expr=sheet_h_expr_oriented,
                margin_expr=LAYOUT_MARGIN,
                gap_expr=LAYOUT_GAP,
                allow_rotate_90=ALLOW_ROTATE_90,
                hide_originals=HIDE_ORIGINALS_AFTER_COPY
            )
            log(f"Auto layout complete. Sheets: {len(sheets)}")
        else:
            log("DO_AUTO_LAYOUT=False; skipping layout.")

        if not sheets:
            ui.messageBox(
                "No sheet layouts were created.\n\n"
                "If you expected sheets:\n"
                "- Make sure the bodies you want to nest are Visible\n"
                "- Ensure they are Solid bodies\n"
                "- Re-run\n\n"
                "Stopping before CAM creation."
            )
            log("No sheets created -> stop before CAM creation.")
            return

        # 2) CAM product
        log("Acquiring CAM product...")
        cam = get_cam_product(app, ui, doc)
        log(f"CAM loaded: {bool(cam)}")
        if not cam:
            ui.messageBox(
                "No CAM product found for this document.\n\n"
                "Fix:\n"
                "1) Switch to Manufacture workspace manually once\n"
                "2) Wait for it to load\n"
                "3) Re-run the script"
            )
            log("No CAM product -> abort.")
            return

        # 3) CAM per sheet
        log("Creating CAM setups/ops for sheets...")

        # --- NEW: enforce stock + WCS orientation per setup ---
        # This must be applied INSIDE create_cam_for_sheets when each setup is created.
        def _enforce_setup_orientation(setup, model_bodies_for_setup):
            try:
                _ensure_setup_orientation_stock_wcs(design, ui, setup, model_bodies_for_setup)
                return True
            except Exception:
                tb = traceback.format_exc()
                log("Orientation enforcement failed:\n" + tb)
                return False

        create_cam_for_sheets(
            cam, design, ui, sheets,
            enforce_orientation_cb=_enforce_setup_orientation
        )
        
        log("CAM creation complete.")

        ui.messageBox(f"Done.\n\nSheets: {len(sheets)}\nCAM Setups created: {len(sheets)}")
        log("=== RUN SUCCESS ===")

    except Exception:
        tb = traceback.format_exc()
        log("EXCEPTION:\n" + tb)
        if ui:
            ui.messageBox("Failed (see Desktop log: foam_cam_template_log.txt):\n\n" + tb)
    finally:
        log("=== RUN END ===")

