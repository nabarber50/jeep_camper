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

def _collect_visible_solids(design: adsk.fusion.Design):
    root = design.rootComponent
    out = []

    # root bodies
    try:
        for b in root.bRepBodies:
            if not b or not b.isSolid:
                continue
            if USE_VISIBLE_BODIES_ONLY and (not b.isVisible):
                continue
            out.append(b)
    except:
        pass

    # occurrence bodies (may be proxies)
    try:
        for occ in root.allOccurrences:
            comp = occ.component
            if not comp:
                continue
            for b in comp.bRepBodies:
                if not b or not b.isSolid:
                    continue
                if USE_VISIBLE_BODIES_ONLY and (not b.isVisible):
                    continue
                try:
                    out.append(b.createForAssemblyContext(occ))
                except:
                    out.append(b)
    except:
        pass

    # dedupe by native
    dedup = []
    seen = set()
    for b in out:
        k = _stable_key(b)
        if k not in seen:
            seen.add(k)
            dedup.append(b)

    return dedup

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
    Packs VISIBLE (slice) solid bodies onto as many sheets as needed.

    Critical fix:
      - DO NOT eliminate as “probe failed” unless we actually attempted a probe.
      - If a body fits the sheet in general but doesn't fit remaining space on THIS sheet,
        defer it to the NEXT sheet (so multiple big slices become sheet_01, sheet_02, ...).

    Build-safe behaviors:
      - Footprints computed from TEMP flatten, sanity-checked vs native bbox; fallback to bbox if TEMP is implausibly small.
      - Probe which insert method works (per body+rotation), and FINAL uses that same method.
      - Rotation only in TEMP insert; MoveFeature is translation-only.
      - Oversize bodies are skipped (do not stop the run).
      - Uses an 'items' list so we never lose footprint data due to unstable keys.
    """

    root = design.rootComponent

    # --- evaluate sheet geometry in mm ---
    sheet_w = _eval_mm(design, sheet_w_expr)
    sheet_h = _eval_mm(design, sheet_h_expr)
    margin  = _eval_mm(design, margin_expr)
    gap     = _eval_mm(design, gap_expr)

    usable_w = sheet_w - 2.0 * margin
    usable_h = sheet_h - 2.0 * margin

    if usable_w <= 0 or usable_h <= 0:
        ui.messageBox(
            "Invalid sheet usable area.\n\n"
            f"Sheet: {sheet_w:.1f} x {sheet_h:.1f} mm\n"
            f"Margin: {margin:.1f} mm\n"
            "Reduce margin or increase sheet size."
        )
        return []

    # ----------------------------
    # Collect bodies
    # ----------------------------
    bodies = _collect_visible_solids(design)
    if not bodies:
        ui.messageBox("No visible solid bodies found to layout.\n\nShow the parts you want to cut, then rerun.")
        return []

    # ----------------------------
    # Footprint helpers
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
        """
        Prefer TEMP flatten footprint, but sanity-check against bbox.
        If TEMP is implausibly small, fall back to bbox.
        """
        name = getattr(src_body, "name", "(unnamed)")
        bb = _bbox_footprint_mm(src_body, rot_90)
        tmp = _tmp_flatten_and_measure_footprint_mm(src_body, rot_90)

        if not tmp:
            if bb:
                try: log(f"Footprint FALLBACK(bbox): {name} rot={rot_90} bbox={bb}")
                except: pass
            return bb

        if not bb:
            try: log(f"Footprint TEMP(no bbox): {name} rot={rot_90} tmp={tmp}")
            except: pass
            return tmp

        tmp_area = max(tmp[0], 0.001) * max(tmp[1], 0.001)
        bb_area  = max(bb[0], 0.001)  * max(bb[1], 0.001)
        ratio = tmp_area / bb_area

        tmp_max = max(tmp[0], tmp[1])
        bb_max  = max(bb[0], bb[1])

        if ratio < 0.01 or (tmp_max < 25.0 and bb_max > 100.0):
            try:
                log(f"Footprint SANITY->bbox: {name} rot={rot_90} tmp={tmp} bbox={bb} ratio={ratio:.6f}")
            except:
                pass
            return bb

        try: log(f"Footprint TEMP ok: {name} rot={rot_90} tmp={tmp} bbox={bb} ratio={ratio:.6f}")
        except: pass
        return tmp

    # ----------------------------
    # Build items list + oversize skip
    # ----------------------------
    skipped = []
    items = []

    for b in bodies:
        name = getattr(b, "name", "(unnamed)")
        fp0 = _sanitized_footprint_mm(b, False)
        fp1 = _sanitized_footprint_mm(b, True) if allow_rotate_90 else None

        if not fp0:
            skipped.append(f"{name} (missing footprint)")
            continue

        fits0 = (fp0[0] <= usable_w and fp0[1] <= usable_h)
        fits1 = False
        if fp1:
            fits1 = (fp1[0] <= usable_w and fp1[1] <= usable_h)

        if not (fits0 or fits1):
            skipped.append(f"{name} ({fp0[0]:.1f} x {fp0[1]:.1f} mm)")
            continue

        items.append({"body": b, "name": name, "fp0": fp0, "fp1": fp1})

    if not items:
        ui.messageBox(
            "Nothing can fit on the usable sheet area.\n\n"
            f"Usable area: {usable_w:.1f} x {usable_h:.1f} mm\n"
            f"Rotate 90°: {'ON' if allow_rotate_90 else 'OFF'}\n\n"
            "First few skipped:\n" + "\n".join(skipped[:15])
        )
        return []

    items.sort(key=lambda it: max(it["fp0"][0], it["fp0"][1]), reverse=True)
    originals_for_hiding = [it["body"] for it in items]

    try:
        log(f"Auto layout: items={len(items)} skipped={len(skipped)}")
    except:
        pass

    # ----------------------------
    # Probe cache: (native_id, rot) -> "copy" / "temp" / ""
    # ----------------------------
    probe_cache = {}

    def _probe_method(src_body, sheet_occ, rot_90: bool) -> str:
        ck = (id(_resolve_native(src_body)), bool(rot_90))
        if ck in probe_cache:
            return probe_cache[ck]

        name = getattr(src_body, "name", "(unnamed)")
        try: log(f"Copy start(PROBE): {name} rot={rot_90}")
        except: pass

        # rot=True must be TEMP
        if rot_90:
            nb = _copy_body_to_component_via_temp(design, src_body, sheet_occ, rotate_90=True)
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

        # rot=False: prefer copyToComponent
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

        # fallback temp
        nb = _copy_body_to_component_via_temp(design, src_body, sheet_occ, rotate_90=False)
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

    def _final_insert(src_body, sheet_occ, rot_90: bool, method: str):
        name = getattr(src_body, "name", "(unnamed)")
        try: log(f"Copy start(FINAL): {name} rot={rot_90} method={method}")
        except: pass

        if method == "copy":
            try:
                native = _resolve_native(src_body)
                return native.copyToComponent(sheet_occ)
            except:
                return None

        if method == "temp":
            return _copy_body_to_component_via_temp(design, src_body, sheet_occ, rotate_90=bool(rot_90))

        return None

    # ----------------------------
    # Multi-sheet shelf packing
    # ----------------------------
    sheets = []
    remaining = list(items)
    sheet_index = 1

    failures_probe  = []
    failures_insert = []
    failures_move   = []

    while remaining:
        sheet_name = f"{layout_base_name}_{sheet_index:02d}"
        sheet_occ = _ensure_component_occurrence(root, sheet_name)
        sheet_comp = sheet_occ.component

        try: log(f"--- SHEET START {sheet_name} remaining={len(remaining)} ---")
        except: pass

        x = 0.0
        y = 0.0
        row_h = 0.0

        placed_any = False
        next_remaining = []

        for it in remaining:
            b = it["body"]
            name = it["name"]
            fp0 = it["fp0"]
            fp1 = it["fp1"]

            tries = [(False, fp0[0], fp0[1])]
            if allow_rotate_90 and fp1:
                tries.append((True, fp1[0], fp1[1]))

            placed_this = False
            probed_any = False  # IMPORTANT: only set if we actually try a probe

            for rot, w, h in tries:
                # If the part can never fit, it should have been filtered already.
                if w > usable_w or h > usable_h:
                    continue

                # If it doesn't fit in current row, try a new row
                if x > 0.0 and (x + w) > usable_w:
                    x = 0.0
                    y += row_h + gap
                    row_h = 0.0

                # If it doesn't fit vertically on this sheet anymore, defer to next sheet
                if (y + h) > usable_h:
                    continue

                # Now it fits spatially -> NOW we attempt probe+final
                method = _probe_method(b, sheet_occ, rot)
                probed_any = True
                if not method:
                    continue

                nb = _final_insert(b, sheet_occ, rot, method)
                if not nb:
                    failures_insert.append(f"{name}: final insert failed (rot={rot}, method={method})")
                    continue

                # translate-only placement
                x0, y0, z0, x1, y1, z1 = _bbox_mm(nb)
                tx = (margin + x) - min(x0, x1)
                ty = (margin + y) - min(y0, y1)
                tz = -max(z0, z1)

                try:
                    _move_body_translate_only(sheet_comp, nb, tx, ty, tz)
                except Exception as e:
                    try: nb.deleteMe()
                    except: pass
                    failures_move.append(f"{name}: move failed ({str(e)})")
                    continue

                x += w + gap
                row_h = max(row_h, h)
                placed_any = True
                placed_this = True
                break

            if not placed_this:
                # If we never probed (because it simply didn't fit in remaining space),
                # defer it to the next sheet.
                if not probed_any:
                    next_remaining.append(it)
                else:
                    # We probed but couldn't get an insert method to work for any orientation
                    failures_probe.append(f"{name}: probe failed (all orientations)")
                    # eliminate so we don't deadlock
                    # (do not append to next_remaining)

        if placed_any:
            sheets.append(sheet_occ)
            remaining = next_remaining
            sheet_index += 1
            continue

        # Nothing placed on this sheet:
        if next_remaining and len(next_remaining) == len(remaining):
            # Everything deferred but none placed -> we can't make progress -> stop
            ui.messageBox(
                "Layout stopped: no bodies could be placed on this sheet.\n\n"
                "This typically means Fusion refused insert for remaining bodies.\n"
                "Check Desktop log for PROBE/FINAL failures."
            )
        else:
            ui.messageBox(
                "Layout stopped: no bodies could be placed on this sheet.\n\n"
                "All remaining bodies were eliminated as unplaceable in this Fusion build.\n\n"
                "Check Desktop log for PROBE/FINAL failures."
            )
        break

    # ----------------------------
    # Visibility handling
    # ----------------------------
    if hide_originals and sheets:
        for b in originals_for_hiding:
            try:
                bb = b.nativeObject if hasattr(b, "nativeObject") and b.nativeObject else b
                bb.isVisible = False
            except:
                pass

    for occ in sheets:
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
        f"Sheets created: {len(sheets)}",
        f"Usable per sheet: {usable_w:.1f} x {usable_h:.1f} mm (margin={margin:.1f}mm, gap={gap:.1f}mm)",
        f"Rotation: {'ENABLED' if allow_rotate_90 else 'DISABLED'}",
        f"Skipped: {len(skipped)}",
        f"Probe failures: {len(failures_probe)}",
        f"Insert failures: {len(failures_insert)}",
        f"Move failures: {len(failures_move)}",
        f"Unplaced remaining: {len(remaining)}",
    ]

    if skipped:
        msg.append("")
        msg.append("First skipped:")
        msg.extend(["  - " + s for s in skipped[:10]])

    if failures_probe or failures_insert or failures_move:
        msg.append("")
        msg.append("First failures:")
        for s in (failures_probe + failures_insert + failures_move)[:10]:
            msg.append("  - " + s)

    ui.messageBox("\n".join(msg))
    return sheets

# ============================================================
# CAM: STOCK/WCS + OPS
# ============================================================

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


def create_cam_for_sheets(cam, design, ui, sheet_occs):
    """
    One setup per sheet occurrence.
    Avoids hard assumptions about param names and strategy IDs.
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
            return
        except:
            pass

    for occ in sheet_occs:
        setup_in = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
        setup = cam.setups.add(setup_in)
        setup.name = f'CAM_{occ.component.name}'

        # models = all bodies in sheet component
        coll = adsk.core.ObjectCollection.create()
        try:
            for b in occ.component.bRepBodies:
                if b and b.isSolid:
                    coll.add(b)
        except:
            pass

        try:
            setup.models = coll
        except:
            pass

        try:
            setup.stockMode = adsk.cam.SetupStockModes.FixedBoxStock
        except:
            pass

        # set stock/WCS using your build’s params
        try:
            configure_stock_and_wcs_for_your_build(
                setup,
                sheet_w_expr=SHEET_W,
                sheet_h_expr=SHEET_H,
                sheet_thk_expr=SHEET_THK,
                side_off_expr='0 mm',
                top_off_expr='0 mm',
                bot_off_expr='0 mm'
            )
        except:
            pass

        ops = setup.operations

        # ---- 2D Contour (best effort) ----
        prof_in = create_2d_contour_input_best_effort(ops, ui, warn_state)
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

    ui.messageBox(
        "CAM creation complete.\n\n"
        "Notes:\n"
        "- If 2D Contour cannot be created by API in this build, add it manually once and save a Template.\n"
        "- If stock sizing didn’t apply automatically, confirm Setup → Stock is 2438.4 × 1219.2 × 38.1 mm and save a Template."
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
            sheets = auto_layout_visible_bodies_multi_sheet(
                design=design,
                ui=ui,
                layout_base_name=LAYOUT_BASE_NAME,
                sheet_w_expr=SHEET_W,
                sheet_h_expr=SHEET_H,
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
        create_cam_for_sheets(cam, design, ui, sheets)
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
