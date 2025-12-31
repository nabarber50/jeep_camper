# cam/setup/foamcam/nesting.py
import math
from collections import defaultdict
from typing import List, Tuple, Optional
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
        self.min_spacing = self.units.eval_mm(getattr(Config, 'MIN_PART_SPACING', Config.LAYOUT_GAP))
        self.spacing = max(self.gap, self.min_spacing)
        
        # Build packing groups lookup for fast group membership checks
        self.packing_groups = getattr(Config, 'PACKING_GROUPS', [])
        self.part_to_group = {}  # Map: part_name -> group_index
        for group_idx, group in enumerate(self.packing_groups):
            for part_name in group:
                self.part_to_group[part_name] = group_idx

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
    
    def _get_packing_group_members(self, part_name: str) -> list:
        """Return all parts in the same packing group as part_name, or empty list if not in a group."""
        if part_name not in self.part_to_group:
            return []
        group_idx = self.part_to_group[part_name]
        return list(self.packing_groups[group_idx])
    
    def _get_group_anchor(self, part_name: str):
        """Return the first member of the packing group (the 'anchor' or parent part)."""
        members = self._get_packing_group_members(part_name)
        return members[0] if members else None

    def _show_accounting_dialog(self, total_input, main_count, void_count, placed_count, missing_parts, sheet_count, all_sheets):
        """Display a summary dialog of the nesting accounting."""
        try:
            if not self.logger.ui:
                return  # UI not available
            
            # Build dialog message
            lines = [
                "=== NESTING SUMMARY ===",
                "",
                f"Total parts loaded: {total_input}",
                f"Partitioned: main={main_count}, void_candidates={void_count}",
                f"Successfully placed: {placed_count}",
            ]
            
            if missing_parts:
                lines.append(f"Missing/unplaced: {len(missing_parts)}")
            else:
                lines.append("✅ All parts accounted for")
            
            lines.extend([
                "",
                f"Sheets created: {sheet_count}",
            ])
            
            # Add sheet summary
            if all_sheets:
                lines.append("\nSheet breakdown:")
                for sheet in all_sheets:
                    try:
                        sheet_name = sheet.occ.component.name
                        part_count = len(sheet.part_names)
                        lines.append(f"  {sheet_name}: {part_count} parts")
                    except:
                        pass
            
            dialog_text = "\n".join(lines)
            self.logger.ui.messageBox(dialog_text, "Nesting Summary")
        except Exception as e:
            self.logger.log(f"Failed to show accounting dialog: {e}")

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

                # Dev-only fail-fast guard (disabled by default)
                try:
                    try:
                        from foamcam.config import Config
                    except Exception:
                        try:
                            from .config import Config
                        except Exception:
                            Config = None
                    if Config and getattr(Config, 'DEBUG_FAIL_ON_ROTATION', False):
                        raise RuntimeError('DEBUG_FAIL_ON_ROTATION triggered in nesting._copy_via_temp_cookie_cutter')
                except Exception:
                    pass

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

    def _find_best_position_smart(self, 
                                   w: float, h: float,
                                   usable_w: float, usable_h: float,
                                   occupied: List[Tuple[float, float, float, float]]) -> Optional[Tuple[float, float]]:
        """
        Smart positioning: find best location considering existing placements.
        Returns (x, y) position or None if no fit.
        Uses multi-criteria heuristic: prioritize dense, contiguous packing.
        """
        best_pos = None
        best_score = float('inf')
        
        # Generate candidate positions: corners of occupied rectangles + grid points
        test_positions = [(0.0, 0.0)]  # Always try origin first (dense packing)
        
        if occupied:
            # Collect all corner positions from occupied rectangles
            for ox, oy, ow, oh in occupied:
                # Right edge placements (adjacent horizontal)
                test_positions.append((ox + ow + self.spacing, oy))
                # Below placements (adjacent vertical) 
                test_positions.append((ox, oy + oh + self.spacing))
                # Below-right diagonal
                test_positions.append((ox + ow + self.spacing, oy + oh + self.spacing))
            
            # Also try filling along top/left edges of sheet for very dense packing
            test_positions.append((0.0, 0.0))  # Ensure origin is first priority
        
        for test_x, test_y in test_positions:
            # Check if fits within bounds
            if test_x + w > usable_w + 1e-3 or test_y + h > usable_h + 1e-3:
                continue
            
            # Check overlap with occupied rectangles (with spacing tolerance)
            overlaps = False
            for ox, oy, ow, oh in occupied:
                # Rectangles must be separated by at least spacing
                if not (test_x + w + self.spacing <= ox + 1e-3 or 
                        test_x >= ox + ow + self.spacing - 1e-3 or
                        test_y + h + self.spacing <= oy + 1e-3 or 
                        test_y >= oy + oh + self.spacing - 1e-3):
                    overlaps = True
                    break
            
            if overlaps:
                continue
            
            # Multi-criteria scoring: prefer positions that minimize wasted space
            # Factors (in order of priority):
            # 1. Minimize total perimeter distance from (0,0) - prefer bottom-left corner
            # 2. Minimize gap between this part and existing parts (contiguity)
            # 3. Prefer positions near existing parts (fill gaps) over empty areas
            
            distance_from_origin = test_y * 2000.0 + test_x  # Prefer lower-left
            
            # Find nearest occupied rectangle (if any)
            min_edge_distance = float('inf')
            if occupied:
                for ox, oy, ow, oh in occupied:
                    # Distance to nearest edge of this rectangle
                    if test_x + w + self.spacing <= ox:
                        edge_dist = ox - (test_x + w + self.spacing)  # Gap on left
                    elif test_x >= ox + ow + self.spacing:
                        edge_dist = test_x - (ox + ow + self.spacing)  # Gap on right
                    elif test_y + h + self.spacing <= oy:
                        edge_dist = oy - (test_y + h + self.spacing)  # Gap above
                    elif test_y >= oy + oh + self.spacing:
                        edge_dist = test_y - (oy + oh + self.spacing)  # Gap below
                    else:
                        edge_dist = 0  # This shouldn't happen (we checked overlap)
                    
                    min_edge_distance = min(min_edge_distance, edge_dist)
            
            # Prefer contiguous placement (small gaps) over isolated placement
            # If no occupied parts, prefer origin
            if occupied and min_edge_distance < float('inf'):
                contiguity_penalty = min_edge_distance * 1.0  # Prefer tight packing
            else:
                contiguity_penalty = 0 if not occupied else 10000.0  # Penalty for isolated placement
            
            # Final score: lower is better
            score = distance_from_origin + contiguity_penalty
            
            if score < best_score:
                best_score = score
                best_pos = (test_x, test_y)
        
        return best_pos

    def _calculate_packing_efficiency(self, 
                                       placed: List[Tuple[float, float]],
                                       usable_w: float, usable_h: float) -> float:
        """Calculate packing efficiency as percentage of usable area filled."""
        if not placed:
            return 0.0
        total_area = sum(w * h for w, h in placed)
        usable_area = usable_w * usable_h
        return (total_area / usable_area * 100.0) if usable_area > 0 else 0.0

    def layout(self, bodies) -> list[SheetLayout]:
        """
        Returns list of SheetLayout (occurrences) created by nesting.
        """
        items: list[PartCandidate] = []
        skipped: list[str] = []

        # First pass: collect footprint data and remember anchor sheet classes
        part_records = []  # temp storage before enforcing group sheet classes
        anchor_best_map = {}  # anchor_name -> (sheet_class, w, h, usable_w, usable_h)

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
            
            # Check for tiny parts (Task 6)
            is_tiny = False
            min_w = getattr(self.Config, 'MIN_PART_WIDTH_MM', 25.0)
            min_h = getattr(self.Config, 'MIN_PART_HEIGHT_MM', 25.0)
            warn_tiny = getattr(self.Config, 'WARN_TINY_PARTS', True)
            skip_tiny = getattr(self.Config, 'SKIP_TINY_PARTS', False)
            
            if fp0.w_mm < min_w or fp0.h_mm < min_h:
                is_tiny = True
                if warn_tiny:
                    self.logger.log(f"⚠️  TINY PART: {name} ({fp0.w_mm:.1f} x {fp0.h_mm:.1f} mm) is below minimum size threshold (W>{min_w}mm, H>{min_h}mm)")
                if skip_tiny:
                    skipped.append(f"{name} ({fp0.w_mm:.1f} x {fp0.h_mm:.1f} mm) below minimum size (W<{min_w}mm or H<{min_h}mm)")
                    continue

            best, best_rot = self._pick_best_sheet_and_rot(fp0, fp1)
            if not best:
                skipped.append(f"{name} ({fp0.w_mm:.1f} x {fp0.h_mm:.1f} mm) too large for all sheet classes")
                continue

            part_records.append((b, name, fp0, fp1, best, best_rot))

            # If this is an anchor, remember its chosen sheet class for members
            group_members = self._get_packing_group_members(name)
            if group_members and len(group_members) > 1 and group_members[0] == name:
                anchor_best_map[name] = best

        # Second pass: build PartCandidates, enforcing anchor sheet class for group members
        for b, name, fp0, fp1, best, best_rot in part_records:
            enforced_best = best
            group_members = self._get_packing_group_members(name)
            if group_members and len(group_members) > 1:
                anchor_name = group_members[0]
                if anchor_name == name:
                    # Ensure anchor best is recorded
                    anchor_best_map[name] = best
                else:
                    anchor_best = anchor_best_map.get(anchor_name)
                    if anchor_best:
                        # Respect optional size caps when forcing to anchor class
                        max_dim = getattr(self.Config, 'PACKING_GROUP_FORCE_MAX_DIM_MM', None)
                        max_area = getattr(self.Config, 'PACKING_GROUP_FORCE_MAX_AREA_MM2', None)
                        largest_dim = max(fp0.w_mm, fp0.h_mm)
                        largest_area = max(fp0.w_mm * fp0.h_mm,
                                           fp1.w_mm * fp1.h_mm if fp1 else 0)
                        skip_force = False
                        if max_dim and largest_dim > max_dim:
                            skip_force = True
                        if max_area and largest_area > max_area:
                            skip_force = True

                        if skip_force:
                            self.logger.log(
                                f"  Not forcing {name} to {anchor_best[0]} (exceeds group force limit); keeping {best[0]}"
                            )
                        else:
                            enforced_best = anchor_best
                            self.logger.log(f"  Forcing {name} to sheet class {anchor_best[0]} to match anchor {anchor_name}")
                    else:
                        self.logger.log(f"  Group member {name} could not find anchor {anchor_name}; using its own best class {best[0]}")

            fr = self._fill_ratio_xy(b)
            items.append(PartCandidate(
                body=b,
                name=name,
                fp0=fp0,
                fp1=fp1,
                sheet_class=enforced_best[0],
                sheet_w=enforced_best[1],
                sheet_h=enforced_best[2],
                usable_w=enforced_best[3],
                usable_h=enforced_best[4],
                prefer_rot=bool(best_rot),
                fill_ratio=fr,
            ))

        if not items:
            self.logger.log("Nesting: no items after footprint/size filtering.")
            return []

        # Sort ALL items by area first (largest first) - prioritize parts with potential voids
        items.sort(key=lambda it: max(it.fp0.w_mm * it.fp0.h_mm,
                                      it.fp1.w_mm * it.fp1.h_mm if it.fp1 else 0), reverse=True)

        # Mark void candidates for later processing but don't separate them
        # This allows small parts to fill gaps on the same sheet as large parts
        anchors = set(group[0] for group in self.packing_groups if group)
        max_dim_cut = getattr(self.Config, 'VOID_CANDIDATE_MAX_DIM_MM', 300.0)
        max_area_cut = getattr(self.Config, 'VOID_CANDIDATE_MAX_AREA_MM2', 250000.0)

        void_candidate_names = set()
        for it in items:
            max_dim = max(it.fp0.w_mm, it.fp0.h_mm, it.fp1.w_mm if it.fp1 else 0, it.fp1.h_mm if it.fp1 else 0)
            area0 = it.fp0.w_mm * it.fp0.h_mm
            area1 = it.fp1.w_mm * it.fp1.h_mm if it.fp1 else area0
            max_area = max(area0, area1)
            if it.name not in anchors and ((max_dim <= max_dim_cut) or (max_area <= max_area_cut)):
                void_candidate_names.add(it.name)

        self.logger.log(
            f"Nesting partition: anchors={len(anchors)} void_candidates={len(void_candidate_names)} total_items={len(items)}"
        )

        # Apply packing group constraints: use stable sort to place anchor parts before their group members
        # (while preserving area-based ordering within each group)
        if self.packing_groups:
            anchor_parts = set()
            for group in self.packing_groups:
                if group:  # group is a tuple of part names
                    anchor_parts.add(group[0])  # First part is the anchor (parent)
            
            def group_sort_key(it):
                is_anchor = it.name in anchor_parts
                return not is_anchor  # Anchors first (False < True)
            
            items.sort(key=group_sort_key)  # Python's sort is stable by default
            self.logger.log(f"Packing groups detected: {len(anchor_parts)} anchor parts will be placed first")

        # Group by class
        groups = defaultdict(list)
        for it in items:
            groups[it.sheet_class].append(it)

        class_order = {cname: i for i, (cname, _sw, _sh) in enumerate(self.Config.SHEET_CLASSES)}
        
        # Process classes in order of their largest part (not predefined class order)
        # This ensures parts with voids get placed first across all classes
        class_largest_area = {}
        for cn in groups.keys():
            max_area = max(max(it.fp0.w_mm * it.fp0.h_mm,
                              it.fp1.w_mm * it.fp1.h_mm if it.fp1 else 0) for it in groups[cn])
            class_largest_area[cn] = max_area
        
        class_names_sorted = sorted(groups.keys(), key=lambda k: class_largest_area.get(k, 0), reverse=True)

        self.logger.log(
            "Class order: " + ", ".join(f"{cn}({len(groups[cn])})" for cn in class_names_sorted)
        )

        # Keep items within each class sorted by area (already sorted from global sort above)
        all_sheets: list[SheetLayout] = []
        global_sheet_index = 1
        
        # Track available voids across ALL sheets for cross-sheet-class nesting
        available_voids = []  # List of {'body': body, 'voids': [...], 'offset': (x,y), 'sheet_name': str}
        
        # Track which sheet each part is placed on (for packing groups)
        part_to_sheet = {}  # Map of part_name -> sheet_name for group constraints

        for cn in class_names_sorted:
            sw = sh = None
            for cname, cw, ch in self.Config.SHEET_CLASSES:
                if cname == cn:
                    sw, sh = cw, ch
                    break
            if sw is None:
                continue

            usable_w, usable_h = self._usable_for_class(sw, sh)

            # Pre-sort remaining items for this class by area (largest first)
            # This ensures big parts get priority and smaller parts fill gaps
            remaining = sorted(groups[cn], key=lambda it: max(it.fp0.w_mm * it.fp0.h_mm,
                                                               it.fp1.w_mm * it.fp1.h_mm if it.fp1 else 0), reverse=True)
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

                # Choose packing strategy
                use_smart = getattr(self.Config, 'PACKING_STRATEGY', 'shelf') == 'smart'
                enable_void_nesting = getattr(self.Config, 'ENABLE_VOID_NESTING', False)
                allow_cross_void = getattr(self.Config, 'VOID_NESTING_ALLOW_CROSS_SHEETS', False)
                
                if use_smart:
                    # Smart packing with best-fit positioning
                    occupied = []  # List of (x, y, w, h) for placed items
                    placed_sizes = []  # List of (w, h) for efficiency calculation
                
                # NOTE: available_voids is now declared at the global scope (before class loop)
                # to persist across ALL sheets for cross-sheet-class nesting
                    
                placed_any = False
                placed_here_count = 0  # parts actually added to this sheet component
                next_remaining = []
                placed_part_names = []  # Track names of parts placed on this sheet
                
                # Traditional shelf packing state
                x = 0.0
                y = 0.0
                row_h = 0.0

                # First pass: try to place items in order
                for idx, it in enumerate(remaining):
                    if idx % 10 == 0:
                        try: adsk.doEvents()
                        except: pass
                    
                    # Check packing group constraints: if this part is in a group, 
                    # it can only be placed on the same sheet as its anchor (if anchor is already placed)
                    group_members = self._get_packing_group_members(it.name)
                    if group_members and len(group_members) > 1:
                        anchor_name = group_members[0]
                        if anchor_name != it.name:  # This is a group member, not the anchor
                            anchor_sheet = part_to_sheet.get(anchor_name)
                            allow_spill = bool(getattr(self.Config, 'PACKING_GROUP_ALLOW_SPILLOVER', False))
                            if anchor_sheet is None:
                                # Anchor not yet placed, defer this part for now
                                self.logger.log(f"  Deferring {it.name} (anchor {anchor_name} not yet placed)")
                                next_remaining.append(it)
                                continue
                            elif anchor_sheet != sheet_name:
                                if allow_spill:
                                    # Allow fallback placement on a later sheet of the same class
                                    self.logger.log(
                                        f"  Group spillover: allowing {it.name} onto {sheet_name} (anchor {anchor_name} on {anchor_sheet})"
                                    )
                                else:
                                    # Anchor is on a different sheet, can't place here, defer
                                    self.logger.log(f"  Deferring {it.name} (anchor {anchor_name} on {anchor_sheet}, current sheet is {sheet_name})")
                                    next_remaining.append(it)
                                    continue
                            else:
                                # Anchor is on this sheet, proceed with placement
                                self.logger.log(f"  {it.name} will attempt placement with anchor {anchor_name} on {sheet_name}")

                    # build orientation tries                    # Try void nesting first if enabled and voids are available
                    # ONLY nest within CURRENT sheet in first pass (cross-sheet nesting happens in second pass)
                    nested_in_void = False
                    if enable_void_nesting and available_voids:
                        from foamcam.geometry import can_fit_in_void
                        void_margin = getattr(self.Config, 'VOID_NESTING_MARGIN', 5.0)
                        
                        for rot, fp in [(False, it.fp0)] + ([(True, it.fp1)] if it.fp1 else []):
                            if nested_in_void:
                                break
                            w, h = fp.w_mm, fp.h_mm
                            
                            for void_entry in available_voids:
                                # FIRST PASS: allow same-sheet; optionally allow cross-sheet void usage
                                target_sheet_comp = sheet_comp
                                target_sheet_occ = sheet_occ
                                target_sheet_name = sheet_name
                                target_part_list = placed_part_names

                                if void_entry['sheet_comp'] != sheet_comp:
                                    if not allow_cross_void:
                                        continue
                                    # Use the void's owning sheet
                                    target_sheet_comp = void_entry['sheet_comp']
                                    target_sheet_occ = void_entry['sheet_occ']
                                    target_sheet_name = void_entry['sheet_name']
                                    target_part_list = void_entry['part_names_list']
                                
                                for void in void_entry['voids']:
                                    fits = can_fit_in_void(w, h, void, void_margin)
                                    if not fits:
                                        if target_sheet_comp != sheet_comp:
                                            self.logger.log(
                                                f"  Void skip: {self._clean_part_name(it.name)} {w:.1f}x{h:.1f} vs void {void['width_mm']:.1f}x{void['height_mm']:.1f} on {target_sheet_name} (margin={void_margin})"
                                            )
                                        continue
                                    try:
                                        nb = self._copy_via_temp_cookie_cutter(it.body, target_sheet_occ, rotate_90=rot, base_feat=None)
                                        if not nb:
                                            continue
                                        
                                        clean_name = self._clean_part_name(it.name)
                                        try:
                                            nb.name = clean_name
                                        except:
                                            pass
                                        
                                        # Position inside void
                                        void_bbox = void['bbox_mm']
                                        void_center_x = (void_bbox[0] + void_bbox[2]) * 0.5
                                        void_center_y = (void_bbox[1] + void_bbox[3]) * 0.5
                                        
                                        x0,y0,z0,x1,y1,z1 = bbox_mm(nb)
                                        part_center_x = (x0 + x1) * 0.5
                                        part_center_y = (y0 + y1) * 0.5
                                        
                                        offset_x, offset_y = void_entry['offset']
                                        tx = (offset_x + void_center_x) - part_center_x
                                        ty = (offset_y + void_center_y) - part_center_y
                                        tz = -max(z0, z1)
                                        
                                        move_translate_only(target_sheet_comp, nb, tx, ty, tz)
                                        
                                        target_part_list.append({
                                            'name': clean_name,
                                            'width': fp.w_mm,
                                            'height': fp.h_mm
                                        })
                                        
                                        # Track which sheet this part was placed on (for packing groups)
                                        part_to_sheet[it.name] = target_sheet_name
                                        if self._get_packing_group_members(it.name):
                                            self.logger.log(f"  Placed anchor part {it.name} on {target_sheet_name} (via void nesting)")
                                        
                                        self.logger.log(f"  Nested {clean_name} ({w:.1f}x{h:.1f}) inside {void_entry['body_name']} on {target_sheet_name}")
                                        placed_any = True
                                        if target_sheet_comp == sheet_comp:
                                            placed_here_count += 1
                                        nested_in_void = True
                                        break
                                    except:
                                        if nb:
                                            try: nb.deleteMe()
                                            except: pass
                                        continue
                                if nested_in_void:
                                    break
                    
                    if nested_in_void:
                        continue  # Skip normal placement, already nested
                    
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

                        # Determine placement position
                        if use_smart:
                            # Smart packing: find best position
                            pos = self._find_best_position_smart(w, h, usable_w, usable_h, occupied)
                            if pos is None:
                                continue
                            place_x, place_y = pos
                        else:
                            # Shelf packing: row-based placement
                            # new row
                            if x > 0.0 and (x + w) > usable_w:
                                x = 0.0
                                y += row_h + self.spacing
                                row_h = 0.0

                            # no vertical space
                            if (y + h) > usable_h:
                                continue
                            
                            place_x, place_y = x, y

                        # insert
                        nb = self._copy_via_temp_cookie_cutter(it.body, sheet_occ, rotate_90=rot, base_feat=sheet_base_feat)
                        if not nb:
                            continue

                        clean_name = self._clean_part_name(it.name)
                        try:
                            nb.name = clean_name
                        except:
                            pass

                        # place to margin + (place_x,place_y), drop to Z=0
                        x0,y0,z0,x1,y1,z1 = bbox_mm(nb)
                        tx = (self.margin + place_x) - min(x0, x1)
                        ty = (self.margin + place_y) - min(y0, y1)
                        tz = -max(z0, z1)
                        try:
                            move_translate_only(sheet_comp, nb, tx, ty, tz)
                        except:
                            try: nb.deleteMe()
                            except: pass
                            continue

                        # Track part info (name + dimensions from the selected footprint orientation)
                        # Use the footprint dimensions directly since they're already calculated
                        placed_part_names.append({
                            'name': clean_name,
                            'width': fp.w_mm,
                            'height': fp.h_mm
                        })
                        
                        # Track which sheet this part was placed on (for packing groups)
                        part_to_sheet[it.name] = sheet_name
                        if self._get_packing_group_members(it.name):
                            self.logger.log(f"  Placed anchor part {it.name} on {sheet_name}")

                        # Detect internal voids in this part for potential nesting
                        if enable_void_nesting:
                            try:
                                from foamcam.geometry import detect_internal_voids
                                voids = detect_internal_voids(nb)
                                if voids:
                                    # Store void info with body reference, placement offset, and sheet context
                                    available_voids.append({
                                        'body': nb,
                                        'body_name': clean_name,
                                        'voids': voids,
                                        'offset': (self.margin + place_x, self.margin + place_y),
                                        'sheet_name': sheet_name,
                                        'sheet_comp': sheet_comp,  # Store component reference for cross-sheet nesting
                                        'sheet_occ': sheet_occ,
                                        'part_names_list': placed_part_names
                                    })
                                    self.logger.log(f"  Found {len(voids)} void(s) in {clean_name} for potential nesting")
                            except Exception as e:
                                pass  # Silently continue if void detection fails
                        # Update state based on packing mode
                        if use_smart:
                            occupied.append((place_x, place_y, w, h))
                            placed_sizes.append((w, h))
                        else:
                            x += w + self.spacing
                            row_h = max(row_h, h)
                        placed_any = True
                        placed_here_count += 1
                        placed_this = True
                        break

                    if not placed_this:
                        # didn’t fit here -> defer to next sheet
                        next_remaining.append(it)
                
                # Second pass: try to nest remaining small parts inside detected voids
                if enable_void_nesting and available_voids and next_remaining:
                    self.logger.log(
                        f"Void pass on {sheet_name}: remaining={len(next_remaining)} available_voids={len(available_voids)}"
                    )
                    from foamcam.geometry import can_fit_in_void
                    void_margin = getattr(self.Config, 'VOID_NESTING_MARGIN', 5.0)
                    
                    still_remaining = []
                    for it in next_remaining:
                        nested = False
                        
                        # Try both orientations
                        for rot, fp in [(False, it.fp0)] + ([(True, it.fp1)] if it.fp1 else []):
                            if nested:
                                break
                            w, h = fp.w_mm, fp.h_mm
                            
                            for void_entry in available_voids:
                                target_sheet_comp = sheet_comp
                                target_sheet_occ = sheet_occ
                                target_sheet_name = sheet_name
                                target_part_list = placed_part_names

                                if void_entry['sheet_comp'] != sheet_comp:
                                    if not allow_cross_void:
                                        continue
                                    target_sheet_comp = void_entry['sheet_comp']
                                    target_sheet_occ = void_entry['sheet_occ']
                                    target_sheet_name = void_entry['sheet_name']
                                    target_part_list = void_entry['part_names_list']
                                
                                for void in void_entry['voids']:
                                    fits = can_fit_in_void(w, h, void, void_margin)
                                    if not fits:
                                        if target_sheet_comp != sheet_comp:
                                            self.logger.log(
                                                f"    Void skip: {self._clean_part_name(it.name)} {w:.1f}x{h:.1f} vs void {void['width_mm']:.1f}x{void['height_mm']:.1f} on {target_sheet_name} (margin={void_margin})"
                                            )
                                        continue
                                    try:
                                        nb = self._copy_via_temp_cookie_cutter(it.body, target_sheet_occ, rotate_90=rot, base_feat=None)
                                        if not nb:
                                            continue
                                        
                                        clean_name = self._clean_part_name(it.name)
                                        try:
                                            nb.name = clean_name
                                        except:
                                            pass
                                        
                                        # Position inside void (center of void bbox)
                                        void_bbox = void['bbox_mm']
                                        void_center_x = (void_bbox[0] + void_bbox[2]) * 0.5
                                        void_center_y = (void_bbox[1] + void_bbox[3]) * 0.5
                                        
                                        x0,y0,z0,x1,y1,z1 = bbox_mm(nb)
                                        part_center_x = (x0 + x1) * 0.5
                                        part_center_y = (y0 + y1) * 0.5
                                        
                                        # Translate to center of void + parent offset
                                        offset_x, offset_y = void_entry['offset']
                                        tx = (offset_x + void_center_x) - part_center_x
                                        ty = (offset_y + void_center_y) - part_center_y
                                        tz = -max(z0, z1)
                                        
                                        move_translate_only(target_sheet_comp, nb, tx, ty, tz)
                                        
                                        # Track nested part
                                        target_part_list.append({
                                            'name': clean_name,
                                            'width': fp.w_mm,
                                            'height': fp.h_mm
                                        })
                                        
                                        self.logger.log(f"    Nested {clean_name} ({w:.1f}x{h:.1f}) inside {void_entry['body_name']} on {target_sheet_name}")
                                        part_to_sheet[it.name] = target_sheet_name
                                        placed_any = True
                                        if target_sheet_comp == sheet_comp:
                                            placed_here_count += 1
                                        nested = True
                                        break
                                    except Exception as e:
                                        if nb:
                                            try: nb.deleteMe()
                                            except: pass
                                        continue
                                if nested:
                                    break
                        
                        if not nested:
                            still_remaining.append(it)
                    
                    # Update remaining list and log results
                    nested_count = len(next_remaining) - len(still_remaining)
                    next_remaining = still_remaining
                    if nested_count > 0:
                        self.logger.log(f"  Void nesting placed {nested_count} additional parts")
                
                # Finish base feature
                try:
                    if sheet_base_feat:
                        sheet_base_feat.finishEdit()
                except:
                    pass

                # Log packing efficiency
                if placed_any and getattr(self.Config, 'PACKING_LOG_EFFICIENCY', False):
                    if use_smart and placed_sizes:
                        efficiency = self._calculate_packing_efficiency(placed_sizes, usable_w, usable_h)
                        self.logger.log(f"  Packing efficiency: {efficiency:.1f}% ({len(placed_sizes)} parts)")
                    elif not use_smart:
                        # Estimate for shelf packing based on final cursor position
                        used_h = y + row_h
                        used_area_approx = usable_w * used_h if used_h > 0 else 0
                        total_area = usable_w * usable_h
                        efficiency = (used_area_approx / total_area * 100.0) if total_area > 0 else 0
                        parts_count = len(remaining) - len(next_remaining)
                        self.logger.log(f"  Packing efficiency: ~{efficiency:.1f}% ({parts_count} parts)")

                if not placed_any:
                    self.logger.log(f"Layout stopped for {cn}: nothing could be placed on {sheet_name}.")
                    break

                # Drop empty sheets (can happen if all parts were placed via cross-sheet voids)
                if placed_here_count == 0:
                    try:
                        sheet_occ.deleteMe()
                    except:
                        pass
                else:
                    all_sheets.append(SheetLayout(
                        index=global_sheet_index,
                        class_name=cn,
                        occ=sheet_occ,
                        usable_w=usable_w,
                        usable_h=usable_h,
                        part_names=placed_part_names
                    ))
                    global_sheet_index += 1
                remaining = next_remaining

            self.logger.log(f"Class complete: {cn} -> sheets={sum(1 for s in all_sheets if s.class_name == cn)} usable={usable_w:.1f}x{usable_h:.1f}")

        # After all classes processed, collect any remaining unplaced void candidates
        # These are parts that were marked as void_candidates but couldn't be placed
        placed_names = set(part_to_sheet.keys())
        void_candidates = [it for it in items if it.name in void_candidate_names and it.name not in placed_names]
        
        # Preserve void_candidate_names for accounting (these are ALL parts that were marked as candidates)
        all_void_candidate_items = [it for it in items if it.name in void_candidate_names]
        
        self.logger.log(f"After main packing: placed={len(placed_names)} unplaced_void_candidates={len(void_candidates)}")

        # Coverage check: every candidate should have been placed somewhere
        # After primary placement, optionally back-fill all known voids with deferred small parts
        enable_backfill = getattr(self.Config, 'ENABLE_BACKFILL_VOID_PASS', True)
        if enable_backfill and getattr(self.Config, 'ENABLE_VOID_NESTING', False) and available_voids and void_candidates:
            from foamcam.geometry import can_fit_in_void
            void_margin = getattr(self.Config, 'VOID_NESTING_MARGIN', 2.0)
            allow_cross_void = getattr(self.Config, 'VOID_NESTING_ALLOW_CROSS_SHEETS', False)

            self.logger.log(
                f"Backfill pass: void_candidates={len(void_candidates)} available_voids={len(available_voids)}"
            )

            # Quick pre-filter: drop candidates that cannot fit in ANY known void (even ignoring margin)
            max_void_w = max((v['width_mm'] for ve in available_voids for v in ve['voids']), default=0.0)
            max_void_h = max((v['height_mm'] for ve in available_voids for v in ve['voids']), default=0.0)

            def can_ever_fit(it):
                dims = [(it.fp0.w_mm, it.fp0.h_mm)]
                if self.Config.ALLOW_ROTATE_90 and it.fp1 is not None:
                    dims.append((it.fp1.w_mm, it.fp1.h_mm))
                for w, h in dims:
                    if (w + 2 * void_margin) <= max_void_w and (h + 2 * void_margin) <= max_void_h:
                        return True
                    if (h + 2 * void_margin) <= max_void_w and (w + 2 * void_margin) <= max_void_h:
                        return True
                return False

            prefilter_count = len(void_candidates)
            void_candidates = [it for it in void_candidates if can_ever_fit(it)]
            if len(void_candidates) != prefilter_count:
                self.logger.log(
                    f"Backfill prefilter dropped {prefilter_count - len(void_candidates)} that cannot fit max void {max_void_w:.1f}x{max_void_h:.1f} (margin={void_margin})"
                )

            if not void_candidates:
                self.logger.log("Backfill skipped: no candidates can fit any void")
                available_voids = []
                # fall through to coverage check
            else:
                # Build a lookup to update sheet part lists when we place into an existing sheet
                sheet_by_name = {sheet.occ.component.name: sheet for sheet in all_sheets}

                def orientations(it):
                    if self.Config.ALLOW_ROTATE_90 and it.fp1 is not None:
                        return [(False, it.fp0), (True, it.fp1)]
                    return [(False, it.fp0)]

                # Best-fit: pick the candidate/void pair with minimum slack
                placed_any = True
                iterations = 0
                # ⚠️  SAFETY: Strict iteration cap to prevent hang. If backfill can't place within
                # this limit, we skip it and use fallback. This is more stable than an infinite loop.
                max_iterations = min(50, len(void_candidates) + 10)  # Very tight cap

                while placed_any and void_candidates and available_voids:
                    placed_any = False
                    best = None  # (slack, it_index, void_entry, void, rot, fp)
                    iterations += 1
                    if iterations > max_iterations:
                        self.logger.log(
                            f"Backfill aborting after {iterations} iterations (candidates={len(void_candidates)} voids={sum(len(v['voids']) for v in available_voids)})"
                        )
                        break

                    for v_entry in available_voids:
                        v_sheet_comp = v_entry['sheet_comp']
                        v_sheet_name = v_entry['sheet_name']
                        for void in v_entry['voids']:
                            vw = void['width_mm']; vh = void['height_mm']
                            for idx, it in enumerate(void_candidates):
                                # Respect anchor grouping: only place members if their anchor is already placed
                                group_members = self._get_packing_group_members(it.name)
                                if group_members and len(group_members) > 1:
                                    anchor_name = group_members[0]
                                    anchor_sheet = part_to_sheet.get(anchor_name)
                                    if not anchor_sheet:
                                        continue  # wait for anchor
                                for rot, fp in orientations(it):
                                    w = fp.w_mm; h = fp.h_mm
                                    if not can_fit_in_void(w, h, void, void_margin):
                                        continue
                                    slack = (vw - w - 2*void_margin) * (vh - h - 2*void_margin)
                                    if slack < 0:
                                        continue
                                    if best is None or slack < best[0]:
                                        best = (slack, idx, v_entry, void, rot, fp)

                    if not best:
                        break

                    _slack, cand_idx, v_entry, void, rot, fp = best
                    it = void_candidates.pop(cand_idx)

                    target_sheet_comp = v_entry['sheet_comp']
                    target_sheet_occ = v_entry['sheet_occ']
                    target_sheet_name = v_entry['sheet_name']
                    target_part_list = v_entry['part_names_list']

                    # If cross-sheet is disabled and sheet differs, skip this placement attempt
                    if target_sheet_comp != v_entry['sheet_comp'] and not allow_cross_void:
                        continue

                    nb = None
                    try:
                        # ⚠️  Backfill placement: known to hang/crash on certain geometry configs
                        # Attempt cookie-cutter copy
                        try:
                            nb = self._copy_via_temp_cookie_cutter(it.body, target_sheet_occ, rotate_90=rot, base_feat=None)
                        except Exception as e:
                            self.logger.log(f"  Backfill copy failed for {it.name}: {e}")
                            continue
                        
                        if not nb:
                            continue

                        clean_name = self._clean_part_name(it.name)
                        try:
                            nb.name = clean_name
                        except:
                            pass

                        # Position inside void center
                        void_bbox = void['bbox_mm']
                        void_center_x = (void_bbox[0] + void_bbox[2]) * 0.5
                        void_center_y = (void_bbox[1] + void_bbox[3]) * 0.5

                        try:
                            x0,y0,z0,x1,y1,z1 = bbox_mm(nb)
                        except Exception as e:
                            self.logger.log(f"  Backfill bbox failed for {clean_name}: {e}")
                            continue

                        part_center_x = (x0 + x1) * 0.5
                        part_center_y = (y0 + y1) * 0.5

                        offset_x, offset_y = v_entry['offset']
                        tx = (offset_x + void_center_x) - part_center_x
                        ty = (offset_y + void_center_y) - part_center_y
                        tz = -max(z0, z1)

                        try:
                            move_translate_only(target_sheet_comp, nb, tx, ty, tz)
                        except Exception as e:
                            self.logger.log(f"  Backfill move failed for {clean_name}: {e}")
                            continue

                        target_part_list.append({
                            'name': clean_name,
                            'width': fp.w_mm,
                            'height': fp.h_mm
                        })

                        part_to_sheet[it.name] = target_sheet_name
                        # Update SheetLayout part list if we know the sheet
                        sheet_obj = sheet_by_name.get(target_sheet_name)
                        if sheet_obj:
                            sheet_obj.part_names.append({
                                'name': clean_name,
                                'width': fp.w_mm,
                                'height': fp.h_mm
                            })

                        self.logger.log(
                            f"  Void backfill: nested {clean_name} ({fp.w_mm:.1f}x{fp.h_mm:.1f}) into {v_entry['body_name']} on {target_sheet_name}"
                        )

                        # Remove the used void so we don't double place in the same pocket
                        try:
                            v_entry['voids'].remove(void)
                        except:
                            pass
                        if not v_entry['voids']:
                            try:
                                available_voids.remove(v_entry)
                            except:
                                pass

                        placed_any = True
                    except Exception as e:
                        self.logger.log(f"  Backfill placement exception: {e}")
                        try:
                            if nb:
                                nb.deleteMe()
                        except:
                            pass
                        continue
        elif not enable_backfill and void_candidates:
            # Skip backfill entirely; send void candidates to the safer fallback path
            self.logger.log(
                f"Backfill disabled via config; skipping backfill for {len(void_candidates)} candidates (using fallback sheets)"
            )

        # Fallback: if deferred void candidates remain, place them on fresh sheets
        # so we do not hard-fail the run. This preserves the primary strategy
        # (try voids first) but guarantees every part gets a home.
        if void_candidates:
            self.logger.log(
                f"⚠️  Void backfill incomplete: falling back to normal placement for {len(void_candidates)} parts"
            )
            fallback_groups = defaultdict(list)
            for it in void_candidates:
                fallback_groups[it.sheet_class].append(it)

            class_order = {cname: i for i, (cname, _sw, _sh) in enumerate(self.Config.SHEET_CLASSES)}
            leftover = []  # any parts still unplaced after fallback

            for cn in sorted(fallback_groups.keys(), key=lambda k: class_order.get(k, 999)):
                sw = sh = None
                for cname, cw, ch in self.Config.SHEET_CLASSES:
                    if cname == cn:
                        sw, sh = cw, ch
                        break
                if sw is None:
                    leftover.extend(fallback_groups[cn])
                    continue

                usable_w, usable_h = self._usable_for_class(sw, sh)
                remaining_fb = sorted(
                    fallback_groups[cn],
                    key=lambda it: max(it.fp0.w_mm * it.fp0.h_mm, it.fp1.w_mm * it.fp1.h_mm if it.fp1 else 0),
                    reverse=True,
                )

                while remaining_fb:
                    sheet_name = f"SHEET_{global_sheet_index:02d}_{cn}"
                    sheet_occ = self._ensure_occurrence(sheet_name)
                    sheet_comp = sheet_occ.component

                    sheet_base_feat = None
                    try:
                        sheet_base_feat = sheet_comp.features.baseFeatures.add()
                        sheet_base_feat.startEdit()
                    except:
                        sheet_base_feat = None

                    self.logger.log(
                        f"--- FALLBACK SHEET {sheet_name} (void candidates) remaining={len(remaining_fb)} usable={usable_w:.1f}x{usable_h:.1f} ---"
                    )

                    placed_here = False
                    placed_part_names = []
                    next_remaining_fb = []
                    x = y = row_h = 0.0

                    for it in remaining_fb:
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

                            # shelf-style placement
                            if x > 0.0 and (x + w) > usable_w:
                                x = 0.0
                                y += row_h + self.spacing
                                row_h = 0.0

                            if (y + h) > usable_h:
                                # Part doesn't fit vertically on this sheet; defer to next sheet
                                break

                            place_x, place_y = x, y

                            nb = self._copy_via_temp_cookie_cutter(it.body, sheet_occ, rotate_90=rot, base_feat=sheet_base_feat)
                            if not nb:
                                continue

                            clean_name = self._clean_part_name(it.name)
                            try:
                                nb.name = clean_name
                            except:
                                pass

                            x0,y0,z0,x1,y1,z1 = bbox_mm(nb)
                            tx = (self.margin + place_x) - min(x0, x1)
                            ty = (self.margin + place_y) - min(y0, y1)
                            tz = -max(z0, z1)
                            try:
                                move_translate_only(sheet_comp, nb, tx, ty, tz)
                            except:
                                try: nb.deleteMe()
                                except: pass
                                continue

                            placed_part_names.append({
                                'name': clean_name,
                                'width': fp.w_mm,
                                'height': fp.h_mm
                            })

                            part_to_sheet[it.name] = sheet_name
                            placed_here = True
                            placed_this = True

                            x += w + self.spacing
                            row_h = max(row_h, h)
                            break

                        if not placed_this:
                            next_remaining_fb.append(it)

                    try:
                        if sheet_base_feat:
                            sheet_base_feat.finishEdit()
                    except:
                        pass

                    if placed_here:
                        all_sheets.append(SheetLayout(
                            index=global_sheet_index,
                            class_name=cn,
                            occ=sheet_occ,
                            usable_w=usable_w,
                            usable_h=usable_h,
                            part_names=placed_part_names
                        ))
                        global_sheet_index += 1
                        # Continue with remaining parts; loop will create next sheet
                        remaining_fb = next_remaining_fb
                    else:
                        try:
                            sheet_occ.deleteMe()
                        except:
                            pass
                        # No progress made on this sheet; move all deferred to leftover and exit
                        leftover.extend(next_remaining_fb)
                        break

                    remaining_fb = next_remaining_fb

            # Preserve original void_candidates count before overwriting with leftover
            # This is needed for accurate post-validation accounting
            original_void_candidates = all_void_candidate_items  # Use the preserved list from main packing
            void_candidates = leftover

        # Coverage check: distinguish required main items vs optional void-candidates
        placed_names_final = set(part_to_sheet.keys())
        main_names = {it.name for it in items}
        # Use all_void_candidate_items for accounting (all parts marked as void candidates, not just unplaced)
        if 'original_void_candidates' in locals():
            void_names = {it.name for it in original_void_candidates}
        else:
            void_names = {it.name for it in all_void_candidate_items}
        
        # Track original input bodies for total accounting
        total_input_count = len(bodies)
        all_part_names = main_names | void_names

        missing_main = sorted(main_names - placed_names_final)
        missing_void = sorted(void_names - placed_names_final)
        missing_all = sorted(all_part_names - placed_names_final)

        # POST-VALIDATION: Total accounting report
        self.logger.log(
            f"=== POST-VALIDATION: PART ACCOUNTING ==="
        )
        self.logger.log(
            f"Total input bodies loaded: {total_input_count}"
        )
        self.logger.log(
            f"Partitioned: main={len(main_names)}, void_candidates={len(void_names)}, total={len(all_part_names)}"
        )
        self.logger.log(
            f"Successfully placed: {len(placed_names)}"
        )
        if missing_all:
            self.logger.log(
                f"❌ MISSING/UNPLACED: {len(missing_all)} parts NOT on any sheet"
            )
            for missing_name in missing_all[:20]:  # Show first 20
                part_type = "MAIN" if missing_name in main_names else "VOID_CAND"
                self.logger.log(
                    f"    - {missing_name} ({part_type})"
                )
            if len(missing_all) > 20:
                self.logger.log(f"    ... and {len(missing_all) - 20} more")
        else:
            self.logger.log(f"✅ ALL PARTS ACCOUNTED FOR")

        if missing_main:
            msg = f"Nesting incomplete: {len(missing_main)} required parts could not be placed"
            self.logger.log("❌ " + msg + ": " + ", ".join(missing_main))
            skipped.extend(f"{nm} (unplaced)" for nm in missing_main)
            if getattr(self.Config, 'FAIL_ON_UNPLACED_PARTS', True):
                raise RuntimeError(msg)

        if missing_void:
            # Void candidates are best-effort; warn but do not fail the run
            self.logger.log(
                f"⚠️  Void fill incomplete: {len(missing_void)} small parts could not be tucked into voids"
            )
            skipped.extend(f"{nm} (void candidate, unplaced)" for nm in missing_void)

        if not missing_main:
            total_considered = len(main_names) + len(void_names)
            placed_count = len(placed_names_final)
            self.logger.log(
                f"✅ Placement coverage: placed {placed_count}/{total_considered} parts across {len(all_sheets)} sheets"
            )
            try:
                for sheet in all_sheets:
                    self.logger.log(
                        f"    {sheet.occ.component.name}: {len(sheet.part_names)} parts"
                    )
            except Exception:
                pass

        # Build and show accounting dialog
        self._show_accounting_dialog(
            total_input_count,
            len(main_names),
            len(void_names),
            len(placed_names),
            missing_all,
            len(all_sheets),
            all_sheets
        )

        # visibility handling
        if self.Config.HIDE_ORIGINALS_AFTER_COPY and all_sheets:
            # Hide all original input bodies
            for b in bodies:
                try:
                    bb = b.nativeObject if hasattr(b, "nativeObject") and b.nativeObject else b
                    bb.isVisible = False
                except:
                    pass
            # Show all bodies on sheets
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
