# cam/setup/foamcam/nesting.py
import math
from collections import defaultdict
import adsk.core, adsk.fusion

from foamcam.models import Footprint, PartCandidate, SheetLayout
from foamcam.geometry import (
    bbox_mm, resolve_native, tmp_copy_rotate_flatten_measure_xy_mm, move_translate_only
)


class SheetNester:
    def __init__(self, design, units, logger, Config):
        self.design = design
        self.units = units
        self.logger = logger
        self.Config = Config
        self.root = design.rootComponent

        self.margin = self.units.eval_mm(Config.LAYOUT_MARGIN)
        self.gap = self.units.eval_mm(Config.LAYOUT_GAP)

    def _ensure_occurrence(self, comp_name: str) -> adsk.fusion.Occurrence:
        try:
            for occ in self.root.occurrences:
                if occ.component and occ.component.name == comp_name:
                    return occ
        except:
            pass
        occ = self.root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        occ.component.name = comp_name
        return occ

    def _clean_part_name(self, nm: str) -> str:
        if not bool(self.Config.COMPACT_PART_NAMES):
            return nm
        try:
            s = nm.replace("Layer_", "L").replace("layer_", "L")
            s = s.replace("_part_", "_P").replace("_Part_", "_P").replace("_PART_", "_P")
            return s
        except:
            return nm

    def _usable_for_class(self, sw_mm: float, sh_mm: float):
        return (sw_mm - 2.0 * self.margin, sh_mm - 2.0 * self.margin)

    def _best_class_for_dims(self, w_mm: float, h_mm: float):
        for cname, sw, sh in self.Config.SHEET_CLASSES:
            uw, uh = self._usable_for_class(sw, sh)
            if w_mm <= uw and h_mm <= uh:
                return (cname, sw, sh, uw, uh)
        return None

    def _bbox_footprint_mm(self, src_body, rot_90: bool):
        try:
            n = resolve_native(src_body)
            x0, y0, _z0, x1, y1, _z1 = bbox_mm(n)
            w = abs(x1 - x0)
            h = abs(y1 - y0)
            return (h, w) if rot_90 else (w, h)
        except:
            return None

    def _sanitized_footprint(self, src_body, rot_90: bool) -> Footprint | None:
        name = getattr(src_body, "name", "(unnamed)")
        bb = self._bbox_footprint_mm(src_body, rot_90)
        tmp = tmp_copy_rotate_flatten_measure_xy_mm(src_body, rot_90)

        if not tmp:
            if bb:
                self.logger.log(f"Footprint FALLBACK(bbox): {name} rot={rot_90} bbox={bb}")
                return Footprint(bb[0], bb[1])
            return None

        if not bb:
            self.logger.log(f"Footprint TEMP(no bbox): {name} rot={rot_90} tmp={tmp}")
            return Footprint(tmp[0], tmp[1])

        tmp_area = max(tmp[0], 0.001) * max(tmp[1], 0.001)
        bb_area  = max(bb[0], 0.001) * max(bb[1], 0.001)
        ratio = tmp_area / bb_area
        tmp_max = max(tmp[0], tmp[1])
        bb_max  = max(bb[0], bb[1])

        if ratio < 0.01 or (tmp_max < 25.0 and bb_max > 100.0):
            self.logger.log(f"Footprint SANITY->bbox: {name} rot={rot_90} tmp={tmp} bbox={bb} ratio={ratio:.6f}")
            return Footprint(bb[0], bb[1])

        self.logger.log(f"Footprint TEMP ok: {name} rot={rot_90} tmp={tmp} bbox={bb} ratio={ratio:.6f}")
        return Footprint(tmp[0], tmp[1])

    def _pick_best_sheet_and_rot(self, fp0: Footprint, fp1: Footprint | None):
        allow_rot = bool(self.Config.ALLOW_ROTATE_90)

        rots = [False, True] if (allow_rot and fp1 is not None) else [False]

        # Hard preference: STD_4x8 if it fits
        for rot in rots:
            fp = fp1 if rot else fp0
            ok = self._best_class_for_dims(fp.w_mm, fp.h_mm)
            if ok and ok[0] == "STD_4x8":
                return ok, rot

        # Otherwise smallest class order
        local_order = {cname: i for i, (cname, _sw, _sh) in enumerate(self.Config.SHEET_CLASSES)}
        candidates = []
        for rot in rots:
            fp = fp1 if rot else fp0
            ok = self._best_class_for_dims(fp.w_mm, fp.h_mm)
            if ok:
                candidates.append((local_order.get(ok[0], 999), ok, rot))

        if not candidates:
            return None, False

        candidates.sort(key=lambda t: t[0])
        _, ok, rot = candidates[0]
        return ok, rot

    def _fill_ratio_xy(self, src_body) -> float:
        # volume / bbox volume proxy
        try:
            n = resolve_native(src_body)
            x0,y0,z0,x1,y1,z1 = bbox_mm(n)
            bw = max(abs(x1-x0), 1e-6)
            bh = max(abs(y1-y0), 1e-6)
            bz = max(abs(z1-z0), 1e-6)

            props = n.physicalProperties
            vol_cm3 = props.volume
            vol_mm3 = vol_cm3 * 1000.0

            bbox_mm3 = bw * bh * bz
            r = vol_mm3 / bbox_mm3
            if r < 0: r = 0.0
            if r > 1.5: r = 1.5
            return r
        except:
            return 1.0

    def _copy_via_temp_cookie_cutter(self, body, target_occ, rotate_90: bool, base_feat=None):
        """
        TEMP insert (stable):
          - copy temp
          - optional rotate 90° about Z
          - flatten minZ to 0 in TEMP
          - insert into base feature (preferred) or fallback
        """
        try:
            target_comp = target_occ.component
            src = resolve_native(body)

            temp_mgr = adsk.fusion.TemporaryBRepManager.get()
            tmp = temp_mgr.copy(src)
            if not tmp:
                return None

            if rotate_90:
                bb = tmp.boundingBox
                pivot = adsk.core.Point3D.create(bb.minPoint.x, bb.minPoint.y, 0.0)
                R = adsk.core.Matrix3D.create()
                R.setToRotation(math.radians(90.0), adsk.core.Vector3D.create(0, 0, 1), pivot)
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

            # Insert ladder
            if base_feat:
                try:
                    return target_comp.bRepBodies.add(tmp, base_feat)
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

    def layout(self, bodies) -> list[SheetLayout]:
        """
        Returns list of SheetLayout (occurrences) created by nesting.
        """
        items: list[PartCandidate] = []
        skipped: list[str] = []

        for b in bodies:
            name = getattr(b, "name", "(unnamed)")

            fp0 = self._sanitized_footprint(b, False)
            fp1 = self._sanitized_footprint(b, True) if self.Config.ALLOW_ROTATE_90 else None

            if self.Config.LOG_NATIVE_BBOX_SIZES:
                try:
                    n = resolve_native(b)
                    x0,y0,z0,x1,y1,z1 = bbox_mm(n)
                    self.logger.log(
                        f"SIZE CHECK native bbox: {name} -> "
                        f"W={abs(x1-x0):.1f}mm H={abs(y1-y0):.1f}mm Z={abs(z1-z0):.1f}mm"
                    )
                except:
                    pass

            if not fp0:
                skipped.append(f"{name} (missing footprint)")
                continue

            best, best_rot = self._pick_best_sheet_and_rot(fp0, fp1)
            if not best:
                skipped.append(f"{name} ({fp0.w_mm:.1f} x {fp0.h_mm:.1f} mm) too large for all sheet classes")
                continue

            fr = self._fill_ratio_xy(b)
            items.append(PartCandidate(
                body=b,
                name=name,
                fp0=fp0,
                fp1=fp1,
                sheet_class=best[0],
                sheet_w=best[1],
                sheet_h=best[2],
                usable_w=best[3],
                usable_h=best[4],
                prefer_rot=bool(best_rot),
                fill_ratio=fr,
            ))

        if not items:
            self.logger.log("Nesting: no items after footprint/size filtering.")
            return []

        # Group by class
        groups = defaultdict(list)
        for it in items:
            groups[it.sheet_class].append(it)

        class_order = {cname: i for i, (cname, _sw, _sh) in enumerate(self.Config.SHEET_CLASSES)}
        class_names_sorted = sorted(groups.keys(), key=lambda k: class_order.get(k, 999))

        # Sort within each class (largest first)
        for cn in class_names_sorted:
            groups[cn].sort(key=lambda it: max(it.fp0.w_mm, it.fp0.h_mm), reverse=True)

        all_sheets: list[SheetLayout] = []
        global_sheet_index = 1

        for cn in class_names_sorted:
            sw = sh = None
            for cname, cw, ch in self.Config.SHEET_CLASSES:
                if cname == cn:
                    sw, sh = cw, ch
                    break
            if sw is None:
                continue

            usable_w, usable_h = self._usable_for_class(sw, sh)

            remaining = list(groups[cn])
            while remaining:
                sheet_name = f"SHEET_{global_sheet_index:02d}_{cn}"
                sheet_occ = self._ensure_occurrence(sheet_name)
                sheet_comp = sheet_occ.component

                # One BaseFeature per sheet for TEMP inserts
                sheet_base_feat = None
                try:
                    sheet_base_feat = sheet_comp.features.baseFeatures.add()
                    sheet_base_feat.startEdit()
                except:
                    sheet_base_feat = None

                self.logger.log(f"--- SHEET START {sheet_name} remaining={len(remaining)} usable={usable_w:.1f}x{usable_h:.1f} ---")

                x = 0.0
                y = 0.0
                row_h = 0.0
                placed_any = False
                next_remaining = []

                for idx, it in enumerate(remaining):
                    if idx % 10 == 0:
                        try: adsk.doEvents()
                        except: pass

                    # build orientation tries
                    tries = []
                    if it.prefer_rot and self.Config.ALLOW_ROTATE_90 and it.fp1 is not None:
                        tries = [(True, it.fp1), (False, it.fp0)]
                    else:
                        tries = [(False, it.fp0)]
                        if self.Config.ALLOW_ROTATE_90 and it.fp1 is not None:
                            tries.append((True, it.fp1))

                    placed_this = False
                    for rot, fp in tries:
                        w = fp.w_mm
                        h = fp.h_mm
                        if w > usable_w or h > usable_h:
                            continue

                        # new row
                        if x > 0.0 and (x + w) > usable_w:
                            x = 0.0
                            y += row_h + self.gap
                            row_h = 0.0

                        # no vertical space
                        if (y + h) > usable_h:
                            continue

                        # insert
                        nb = self._copy_via_temp_cookie_cutter(it.body, sheet_occ, rotate_90=rot, base_feat=sheet_base_feat)
                        if not nb:
                            continue

                        try:
                            nb.name = self._clean_part_name(it.name)
                        except:
                            pass

                        # place to margin + (x,y), drop to Z=0
                        x0,y0,z0,x1,y1,z1 = bbox_mm(nb)
                        tx = (self.margin + x) - min(x0, x1)
                        ty = (self.margin + y) - min(y0, y1)
                        tz = -max(z0, z1)
                        try:
                            move_translate_only(sheet_comp, nb, tx, ty, tz)
                        except:
                            try: nb.deleteMe()
                            except: pass
                            continue

                        x += w + self.gap
                        row_h = max(row_h, h)
                        placed_any = True
                        placed_this = True
                        break

                    if not placed_this:
                        # didn’t fit here -> defer to next sheet
                        next_remaining.append(it)

                # Finish base feature
                try:
                    if sheet_base_feat:
                        sheet_base_feat.finishEdit()
                except:
                    pass

                if not placed_any:
                    self.logger.log(f"Layout stopped for {cn}: nothing could be placed on {sheet_name}.")
                    break

                all_sheets.append(SheetLayout(
                    index=global_sheet_index,
                    class_name=cn,
                    occ=sheet_occ,
                    usable_w=usable_w,
                    usable_h=usable_h
                ))
                global_sheet_index += 1
                remaining = next_remaining

            self.logger.log(f"Class complete: {cn} -> sheets={sum(1 for s in all_sheets if s.class_name == cn)} usable={usable_w:.1f}x{usable_h:.1f}")

        # visibility handling
        if self.Config.HIDE_ORIGINALS_AFTER_COPY and all_sheets:
            for it in items:
                try:
                    bb = it.body.nativeObject if hasattr(it.body, "nativeObject") and it.body.nativeObject else it.body
                    bb.isVisible = False
                except:
                    pass
            for sheet in all_sheets:
                try:
                    for b in sheet.occ.component.bRepBodies:
                        if b and b.isSolid:
                            b.isVisible = True
                except:
                    pass

        # summary to log
        self.logger.log(f"Layout complete. Sheets created: {len(all_sheets)} Skipped: {len(skipped)}")
        if skipped:
            self.logger.log("First skipped:\n" + "\n".join(" - " + s for s in skipped[:20]))

        return all_sheets
