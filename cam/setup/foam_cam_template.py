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
    Multi-sheet-class nesting:
      - Defines multiple sheet classes (STD_4x8, EXT_4x10, EXT_4x12, WIDE_6x10).
      - Each body is assigned to the smallest class that can fit (considering optional 90° rotation).
      - Runs the same stable packer per class, producing multiple sheets per class as needed.

    Preserves previously agreed stability rules:
      - Collector diagnostics + includes hidden bodies with 'Layer_' in name even if USE_VISIBLE_BODIES_ONLY=True
      - TEMP flatten footprint with sanity-check vs bbox; fallback to bbox if TEMP footprint implausible
      - Prefer TEMP insert for FINAL (rotation + flatten-to-Z0 cookie-cutter); copyToComponent fallback for rot=False
      - One BaseFeature per sheet for TEMP inserts
      - Translation-only moves
      - Skip oversize bodies (relative to all classes)
      - Do not mark probe failed unless probe was attempted (defer to next sheet if just no remaining space)
      - Rename inserted bodies to match source name
      - Periodic adsk.doEvents()
    """
    root = design.rootComponent

    # ----------------------------
    # Sheet classes (mm)
    # ----------------------------
    # name, width_mm, height_mm
    SHEET_CLASSES = [
        ("STD_4x8",   2438.4, 1219.2),
        ("EXT_4x10",  2438.4, 3048.0),
        ("EXT_4x12",  2438.4, 3657.6),
        ("WIDE_6x10", 1828.8, 3048.0),
    ]

    # ----------------------------
    # Safe translation-only move (self-contained)
    # ----------------------------
    def _move_translate_only(comp: adsk.fusion.Component,
                             body: adsk.fusion.BRepBody,
                             tx_mm: float, ty_mm: float, tz_mm: float):
        dx = tx_mm * 0.1  # mm -> cm
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
    # Evaluate parameters in mm (margin/gap are shared across classes)
    # ----------------------------
    # NOTE: sheet_w_expr/sheet_h_expr remain accepted but are not used for sizing now,
    # since we are using explicit sheet classes above. We still eval them to keep behavior
    # consistent if other parts of the script expect eval side effects/logs.
    _ = _eval_mm(design, sheet_w_expr)
    _ = _eval_mm(design, sheet_h_expr)
    margin = _eval_mm(design, margin_expr)
    gap = _eval_mm(design, gap_expr)

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
            "Check the log for 'Collector diagnostics' to see why bodies were excluded."
        )
        return []

    try:
        if diag_stats["included"] < 5:
            preview = "\n".join([f"- {nm} | {reason} | {where}" for (nm, reason, where) in diag_excluded[:10]])
            ui.messageBox(
                "Body collection diagnostics (included < 5):\n\n"
                f"seen_total={diag_stats['seen_total']} (root={diag_stats['seen_root']}, occ={diag_stats['seen_occ']})\n"
                f"included={diag_stats['included']}  deduped_out={diag_stats['deduped_out']}\n"
                f"excluded_not_solid={diag_stats['not_solid']}\n"
                f"excluded_hidden={diag_stats['filtered_visibility']}\n"
                f"excluded_non_brep_or_null={diag_stats['non_brep_or_null']}\n\n"
                "First excluded:\n" + (preview if preview else "(none)")
            )
    except:
        pass

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
    # Sheet-class chooser
    # ----------------------------
    def _usable_for_class(sw_mm: float, sh_mm: float):
        return (sw_mm - 2.0 * margin, sh_mm - 2.0 * margin)

    def _best_class_for_dims(w_mm: float, h_mm: float):
        # returns (class_name, sheet_w_mm, sheet_h_mm, usable_w, usable_h)
        for cname, sw, sh in SHEET_CLASSES:
            uw, uh = _usable_for_class(sw, sh)
            if w_mm <= uw and h_mm <= uh:
                return (cname, sw, sh, uw, uh)
        return None

    def _pick_best_sheet_and_rot(fp0, fp1):
        # Try 4x8 first
        for rot in ([False, True] if allow_rotate_90 else [False]):
            fp = fp1 if rot else fp0
            if not fp:
                continue
            ok = _best_class_for_dims(fp[0], fp[1])
            if ok and ok[0] == "STD_4x8":
                return ok, rot

        # Otherwise pick the smallest class that fits (considering both orientations)
        candidates = []
        for rot in ([False, True] if allow_rotate_90 else [False]):
            fp = fp1 if rot else fp0
            if not fp:
                continue
            ok = _best_class_for_dims(fp[0], fp[1])
            if ok:
                candidates.append((class_order.get(ok[0], 999), ok, rot))

        if not candidates:
            return None, False

        candidates.sort(key=lambda t: t[0])  # smallest class first
        _ord, ok, rot = candidates[0]
        return ok, rot

    # ----------------------------
    # Build items list, assign sheet class, skip true oversize
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

        try:
            # Real bbox of the native body (no temp flatten)
            n = _resolve_native(b)
            x0,y0,z0,x1,y1,z1 = _bbox_mm(n)
            bw = abs(x1-x0)
            bh = abs(y1-y0)
            bz = abs(z1-z0)
            log(f"SIZE CHECK native bbox: {name} -> W={bw:.1f}mm H={bh:.1f}mm Z={bz:.1f}mm")
        except:
            pass

        best, best_rot = _pick_best_sheet_and_rot(fp0, fp1)

        if not best:
            skipped.append(f"{name} ({fp0[0]:.1f} x {fp0[1]:.1f} mm) too large for all sheet classes")
            continue

        if not best:
            # too big for ALL classes
            skipped.append(f"{name} ({fp0[0]:.1f} x {fp0[1]:.1f} mm) too large for all sheet classes")
            continue

        items.append({
            "body": b,
            "name": name,
            "fp0": fp0,
            "fp1": fp1,
            "sheet_class": best[0],
            "sheet_w": best[1],
            "sheet_h": best[2],
            "usable_w": best[3],
            "usable_h": best[4],
            "prefer_rot": best_rot
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
    try:
        from collections import defaultdict
        groups = defaultdict(list)
        for it in items:
            groups[it["sheet_class"]].append(it)
    except:
        groups = {}
        for it in items:
            groups.setdefault(it["sheet_class"], []).append(it)

    # Sort groups by sheet class order (smallest first)
    class_order = {cname: i for i, (cname, _sw, _sh) in enumerate(SHEET_CLASSES)}
    class_names_sorted = sorted(groups.keys(), key=lambda k: class_order.get(k, 999))

    # Within each class, sort largest-first (stable shelf packing)
    for cn in class_names_sorted:
        groups[cn].sort(key=lambda it: max(it["fp0"][0], it["fp0"][1]), reverse=True)

    originals_for_hiding = [it["body"] for it in items]

    try:
        log(f"Auto layout: total items={len(items)} skipped={len(skipped)}")
        for cn in class_names_sorted:
            log(f"  class {cn}: {len(groups[cn])} items")
    except:
        pass

    # ----------------------------
    # Probe cache: (key, rot) -> "temp" / "copy" / ""
    # NOTE: because proxies can be unique, cache key uses:
    #   - proxies: id(body)
    #   - natives: id(native)
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
    # Packing routine for one class
    # ----------------------------
    def _pack_class(class_name: str, class_items: list, sheet_w_mm: float, sheet_h_mm: float):
        usable_w, usable_h = _usable_for_class(sheet_w_mm, sheet_h_mm)
        sheets_local = []

        remaining = list(class_items)
        sheet_index = 1

        failures_probe = []
        failures_insert = []
        failures_move = []

        while remaining:
            sheet_name = f"{layout_base_name}_{class_name}_{sheet_index:02d}"
            sheet_occ = _ensure_component_occurrence(root, sheet_name)
            sheet_comp = sheet_occ.component

            # One BaseFeature per sheet for TEMP inserts
            sheet_base_feat = None
            try:
                sheet_base_feat = sheet_comp.features.baseFeatures.add()
                sheet_base_feat.startEdit()
            except:
                sheet_base_feat = None

            try: log(f"--- SHEET START {sheet_name} remaining={len(remaining)} usable={usable_w:.1f}x{usable_h:.1f} ---")
            except: pass

            x = 0.0
            y = 0.0
            row_h = 0.0
            placed_any = False
            next_remaining = []

            for idx, it in enumerate(remaining):
                if idx % 10 == 0:
                    try: adsk.doEvents()
                    except: pass

                b = it["body"]
                name = it["name"]
                fp0 = it["fp0"]
                fp1 = it["fp1"]

                # Try preferred rotation first (helps route to smallest class)
                tries = []
                if it.get("prefer_rot", False) and allow_rotate_90 and fp1:
                    tries.append((True, fp1[0], fp1[1]))
                    tries.append((False, fp0[0], fp0[1]))
                else:
                    tries.append((False, fp0[0], fp0[1]))
                    if allow_rotate_90 and fp1:
                        tries.append((True, fp1[0], fp1[1]))

                placed_this = False
                probed_any = False

                for rot, w, h in tries:
                    if w > usable_w or h > usable_h:
                        continue

                    if x > 0.0 and (x + w) > usable_w:
                        x = 0.0
                        y += row_h + gap
                        row_h = 0.0

                    if (y + h) > usable_h:
                        continue

                    method = _probe_method(b, sheet_occ, rot, sheet_base_feat)
                    probed_any = True
                    if not method:
                        continue

                    nb = _final_insert(b, sheet_occ, rot, method, sheet_base_feat)
                    if not nb:
                        failures_insert.append(f"{name}: final insert failed (rot={rot}, method={method})")
                        continue

                    # Rename inserted body to match source
                    try:
                        nb.name = name
                    except:
                        pass

                    # Translate-only placement + drop to Z=0
                    x0, y0, z0, x1, y1, z1 = _bbox_mm(nb)
                    tx = (margin + x) - min(x0, x1)
                    ty = (margin + y) - min(y0, y1)
                    tz = -max(z0, z1)

                    try:
                        _move_translate_only(sheet_comp, nb, tx, ty, tz)
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
                    if not probed_any:
                        next_remaining.append(it)
                    else:
                        failures_probe.append(f"{name}: probe failed (all orientations)")

            # Close BaseFeature edit for this sheet
            try:
                if sheet_base_feat:
                    sheet_base_feat.finishEdit()
            except:
                pass

            if placed_any:
                sheets_local.append(sheet_occ)
                remaining = next_remaining
                sheet_index += 1
                continue

            # nothing placed -> stop this class
            if next_remaining and len(next_remaining) == len(remaining):
                ui.messageBox(
                    f"Layout stopped for {class_name}: no bodies could be placed on this sheet.\n\n"
                    "This typically means Fusion refused insert for remaining bodies.\n"
                    "Check Desktop log for PROBE/FINAL failures."
                )
            else:
                ui.messageBox(
                    f"Layout stopped for {class_name}: no bodies could be placed on this sheet.\n\n"
                    "All remaining bodies were eliminated as unplaceable in this Fusion build.\n\n"
                    "Check Desktop log for PROBE/FINAL failures."
                )
            break

        return sheets_local, failures_probe, failures_insert, failures_move, usable_w, usable_h

    # ----------------------------
    # Run per class
    # ----------------------------
    all_sheets = []
    all_fail_probe = []
    all_fail_insert = []
    all_fail_move = []

    for cn in class_names_sorted:
        # Get class dims
        sw = None
        sh = None
        for cname, cw, ch in SHEET_CLASSES:
            if cname == cn:
                sw, sh = cw, ch
                break
        if sw is None or sh is None:
            continue

        class_sheets, f_probe, f_ins, f_move, uw, uh = _pack_class(cn, groups[cn], sw, sh)
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
        for b in originals_for_hiding:
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
