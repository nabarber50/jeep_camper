import os
import math
import traceback
import adsk.core
import adsk.fusion

from .logging import AppLogger
# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------
# Keywords to find your camper base occurrence in the current design
TARGET_NAME_KEYWORDS = ['CAMPER BASE', 'CAMBER BASE']

# Foam thickness for slicing (change as needed)
FOAM_THICKNESS_EXPR = '1.5 in'   # e.g. '1.5 in', '50 mm'

# Temp STEP export path (ensure folder exists)
STEP_EXPORT_PATH = r'C:\temp\camper_base_slice.step'

# Rotation applied in the NEW design AFTER slicing,
# to match your preferred orientation for viewing/CAM.
APPLY_ROTATION   = True        # set False if you don't want any rotation
ROTATION_AXIS    = 'X'         # 'X', 'Y', or 'Z'
ROTATION_DEGREES = 90.0        # angle in degrees; flip sign if orientation is flipped

# Enforce that the model's long axis ends up along +Y by applying a
# +90° rotation about Z only when the X extent is greater than the Y extent.
# Set this to False to disable the automatic long-axis rotation.
ENFORCE_LONG_AXIS_Y = True

# Try to honor the higher-level setup config if available (e.g. when running
# from the package `cam/setup/foamcam`). If the import fails we fall back.
try:
    from ..setup.foamcam.config import Config  # type: ignore
except Exception:
    Config = None

# ---- New goodies -----------------------------------------------------

# 1) Group bodies per slice in the browser (nice for selection in CAM)
ENABLE_SLICE_GROUPS = True

# 2 & 4) Alignment holes / datum features through all slices
ENABLE_ALIGNMENT_HOLES = True           # turn ON when you’re ready
HOLE_DIAM_EXPR        = '0.25 in'
HOLE_EDGE_OFFSET_EXPR = '4 in'

# 3) Auto-export one STL per slice
AUTO_EXPORT_STL   = False                # turn ON when you’re ready
STL_OUTPUT_FOLDER = r'C:\temp\foam_slices'

# 5) Simple “nesting” – lay slices out along +X with spacing
ENABLE_SLICE_NESTING = False             # OFF by default for now
SLICE_SPACING_EXPR   = '4 in'            # gap between slice footprints

# ----------------------------------------------------------------------


def rotate_component_bodies_90deg_z(target_comp, logger=None):
    """
    Rotate all solid bodies in target_comp +90° about Z
    so long axis becomes +Y (Maslow long axis).
    """

    bodies = adsk.core.ObjectCollection.create()
    for b in target_comp.bRepBodies:
        if b.isSolid:
            bodies.add(b)

    if bodies.count == 0:
        if logger:
            logger.log("Rotation skipped: no solid bodies found.")
        return

    # Config opt-in: require explicit True on both flags to rotate sheet bodies.
    try:
        if Config is None or getattr(Config, 'MASLOW_SWAP_XY_COMPENSATION', False) is not True or getattr(Config, 'MASLOW_ROTATE_SHEET_BODIES', False) is not True:
            if logger:
                logger.log(f"Slicer: rotation suppressed (MASLOW_SWAP_XY_COMPENSATION={getattr(Config,'MASLOW_SWAP_XY_COMPENSATION',None)} MASLOW_ROTATE_SHEET_BODIES={getattr(Config,'MASLOW_ROTATE_SHEET_BODIES',None)})")
            return
    except Exception:
        # On any failure checking config, be conservative and skip rotation
        if logger:
            logger.log("Slicer: rotation suppressed due to config check error")
        return

    # Compute a stable pivot (model center in XY)
    min_x = min(b.boundingBox.minPoint.x for b in bodies)
    max_x = max(b.boundingBox.maxPoint.x for b in bodies)
    min_y = min(b.boundingBox.minPoint.y for b in bodies)
    max_y = max(b.boundingBox.maxPoint.y for b in bodies)

    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5

    pivot = adsk.core.Point3D.create(cx, cy, 0)

    rot = adsk.core.Matrix3D.create()
    rot.setToRotation(
        math.radians(90.0),                    # ← key line
        adsk.core.Vector3D.create(0, 0, 1),     # Z axis
        pivot
    )

    move_feats = target_comp.features.moveFeatures
    move_input = move_feats.createInput(bodies, rot)

    # Dev-only fail-fast guard (disabled by default)
    try:
        if Config and getattr(Config, 'DEBUG_FAIL_ON_ROTATION', False):
            raise RuntimeError('DEBUG_FAIL_ON_ROTATION triggered in foam_slicer.rotate_component_bodies_90deg_z')
    except Exception:
        # If Config is not available or check fails, ignore and continue
        pass

    move_feats.add(move_input)

    if logger:
        logger.log("Slicer: rotated all bodies +90° about Z (Maslow Y-long enforced).")

def find_target_occurrence(root: adsk.fusion.Component) -> adsk.fusion.Occurrence:
    """
    Search all occurrences (including nested) for one whose name or component
    name contains any of TARGET_NAME_KEYWORDS (case-insensitive).
    """
    all_occs = root.allOccurrences
    keywords_lower = [k.lower() for k in TARGET_NAME_KEYWORDS]

    for occ in all_occs:
        occ_name = occ.name.lower()
        comp_name = occ.component.name.lower()
        for key in keywords_lower:
            if key in occ_name or key in comp_name:
                return occ
    return None


def slice_in_new_design(app: adsk.core.Application, ui: adsk.core.UserInterface,
                        step_path: str, foam_thickness_expr: str,
                        source_camera: adsk.core.Camera, logger: AppLogger):
    """
    Create a new Fusion design, import the STEP at step_path,
    slice into foam layers along Y, then optionally:
      - group per slice (if supported)
      - add alignment holes
      - rotate for viewing
      - nest slices along X
      - export one STL per body in each slice
    """
    # Create new Fusion design document
    new_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    new_design = adsk.fusion.Design.cast(
        new_doc.products.itemByProductType('DesignProductType')
    )
    if not new_design:
        ui.messageBox('Could not create or access new Fusion design.')
        return

    # Match the camera / orientation from the source design (view only)
    try:
        vp = app.activeViewport
        vp.camera = source_camera
        vp.update()
    except:
        pass

    units_mgr = new_design.unitsManager
    new_root = new_design.rootComponent

    # Import STEP into the new design
    import_mgr = app.importManager
    if not os.path.isfile(step_path):
        ui.messageBox(f'STEP file not found at:\n{step_path}')
        return

    step_opts = import_mgr.createSTEPImportOptions(step_path)
    import_mgr.importToTarget(step_opts, new_root)

    # Collect solid bodies after import
    solids = [b for b in new_root.bRepBodies if b.isSolid]

    # If none on root, try first occurrence (common for STEP imports)
    if not solids and new_root.allOccurrences.count > 0:
        occ = new_root.allOccurrences.item(0)
        solids = [b for b in occ.component.bRepBodies if b.isSolid]
        target_comp = occ.component
    else:
        target_comp = new_root

    if not solids:
        ui.messageBox('No solid bodies found after STEP import in new design.')
        return

    # Get foam thickness in cm
    try:
        layer_thickness_cm = units_mgr.evaluateExpression(foam_thickness_expr, 'cm')
    except:
        ui.messageBox(
            'Could not evaluate foam thickness expression in new design:\n'
            f'"{foam_thickness_expr}"'
        )
        return

    if layer_thickness_cm <= 0:
        ui.messageBox(
            'Foam thickness is non-positive in new design.\n'
            f'Current expression: "{foam_thickness_expr}"'
        )
        return

    # ------------------------------------------------------------------
    # Compute overall bounding box across ALL solids
    # ------------------------------------------------------------------
    min_x = min_y = min_z = None
    max_x = max_y = max_z = None

    for b in target_comp.bRepBodies:
        if not b.isSolid:
            continue
        bb = b.boundingBox
        bx_min, by_min, bz_min = bb.minPoint.x, bb.minPoint.y, bb.minPoint.z
        bx_max, by_max, bz_max = bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z

        min_x = bx_min if min_x is None or bx_min < min_x else min_x
        max_x = bx_max if max_x is None or bx_max > max_x else max_x
        min_y = by_min if min_y is None or by_min < min_y else min_y
        max_y = by_max if max_y is None or by_max > max_y else max_y
        min_z = bz_min if min_z is None or bz_min < min_z else min_z
        max_z = bz_max if max_z is None or bz_max > max_z else max_z

    if min_y is None or max_y is None:
        ui.messageBox('Failed to compute bounding box in new design.')
        return

    height = max_y - min_y
    width_x = (max_x - min_x) if (min_x is not None and max_x is not None) else 0

    if height <= 0:
        ui.messageBox('Imported geometry height is zero or invalid.')
        return

    num_layers = math.ceil(height / layer_thickness_cm)
    if num_layers < 1:
        ui.messageBox('Computed number of layers < 1 in new design. Check foam thickness.')
        return

    num_planes = max(0, num_layers - 1)
    if num_planes == 0:
        ui.messageBox('Body is thinner than one foam layer; nothing to slice.')
        return

    # ------------------------------------------------------------------
    # Create slicing planes (offset XZ plane along Y)
    # ------------------------------------------------------------------
    planes = target_comp.constructionPlanes
    xz_plane = target_comp.xZConstructionPlane
    created_planes = []

    for i in range(1, num_planes + 1):
        offset_y = min_y + i * layer_thickness_cm
        p_input = planes.createInput()
        dist_val = adsk.core.ValueInput.createByReal(offset_y)
        p_input.setByOffset(xz_plane, dist_val)
        plane = planes.add(p_input)
        plane.name = f'SlicePlane_{i:02d}'
        created_planes.append(plane)

    # ------------------------------------------------------------------
    # Split ALL solids with all planes (multi-body safe)
    # ------------------------------------------------------------------
    split_feats = target_comp.features.splitBodyFeatures

    for plane in created_planes:
        bodies_to_split = adsk.core.ObjectCollection.create()
        for b in target_comp.bRepBodies:
            if b.isSolid:
                bodies_to_split.add(b)

        if bodies_to_split.count == 0:
            continue

        s_input = split_feats.createInput(bodies_to_split, plane, True)
        split_feats.add(s_input)

    # ------------------------------------------------------------------
    # Sort resulting bodies by center Y and build slice map
    # ------------------------------------------------------------------
    final_solids = [b for b in target_comp.bRepBodies if b.isSolid]
    if not final_solids:
        ui.messageBox('No solid bodies found after splitting in new design.')
        return

    def center_y(b: adsk.fusion.BRepBody) -> float:
        bb2 = b.boundingBox
        return 0.5 * (bb2.minPoint.y + bb2.maxPoint.y)

    # Map: slice_index -> list of bodies
    slice_map = {}

    for b in final_solids:
        cy = center_y(b)
        approx_idx = int(round((cy - min_y) / layer_thickness_cm)) + 1
        if approx_idx < 1:
            approx_idx = 1
        if approx_idx > num_layers:
            approx_idx = num_layers
        slice_map.setdefault(approx_idx, []).append(b)

    # Sort bodies globally (for nice naming)
    final_solids.sort(key=center_y)

    # Rename bodies with slice info
    body_counter_per_slice = {}
    for b in final_solids:
        cy = center_y(b)
        idx = int(round((cy - min_y) / layer_thickness_cm)) + 1
        idx = max(1, min(num_layers, idx))
        n_for_slice = body_counter_per_slice.get(idx, 0) + 1
        body_counter_per_slice[idx] = n_for_slice
        b.name = f'Layer_{idx:02d}_part_{n_for_slice:02d}'

    # ------------------------------------------------------------------
    # Optional: groups per slice (only if groups API is available)
    # ------------------------------------------------------------------
    if ENABLE_SLICE_GROUPS and hasattr(target_comp, 'groups'):
        groups = target_comp.groups
        for idx in sorted(slice_map.keys()):
            bodies = slice_map[idx]
            if not bodies:
                continue
            coll = adsk.core.ObjectCollection.create()
            for b in bodies:
                coll.add(b)
            g_input = groups.createInput(f'Slice_{idx:02d}')
            g_input.entities = coll
            groups.add(g_input)
    elif ENABLE_SLICE_GROUPS:
        pass
        # ui.messageBox(
        #     'Slice grouping is enabled, but this design environment '
        #     'does not support Component.groups. Skipping groups.'
        # )

    # ------------------------------------------------------------------
    # Optional: alignment holes (datum features) through all slices
    # ------------------------------------------------------------------
    if ENABLE_ALIGNMENT_HOLES:
        try:
            hole_diam_cm = units_mgr.evaluateExpression(HOLE_DIAM_EXPR, 'cm')
            edge_off_cm = units_mgr.evaluateExpression(HOLE_EDGE_OFFSET_EXPR, 'cm')
        except:
            ui.messageBox('Failed to evaluate HOLE_* expressions; check script config.')
            hole_diam_cm = None

        if hole_diam_cm and hole_diam_cm > 0 and edge_off_cm is not None:
            sketches = target_comp.sketches
            sk = sketches.add(xz_plane)

            # Positions in X/Z, offset from min edges
            x1 = min_x + edge_off_cm
            z1 = min_z + edge_off_cm
            x2 = max_x - edge_off_cm
            z2 = min_z + edge_off_cm

            center1 = adsk.core.Point3D.create(x1, 0, z1)
            center2 = adsk.core.Point3D.create(x2, 0, z2)
            r = hole_diam_cm / 2.0

            sk.sketchCurves.sketchCircles.addByCenterRadius(center1, r)
            sk.sketchCurves.sketchCircles.addByCenterRadius(center2, r)

            profs = sk.profiles
            if profs.count > 0:
                prof = profs.item(0)
                extrudes = target_comp.features.extrudeFeatures
                ext_input = extrudes.createInput(
                    prof,
                    adsk.fusion.FeatureOperations.CutFeatureOperation
                )
                # Big symmetric extent – guaranteed to pass through all slices
                dist_val = adsk.core.ValueInput.createByReal(height * 4.0)
                ext_input.setSymmetricExtent(dist_val, True)
                # Let Fusion cut all intersecting solids
                extrudes.add(ext_input)

    # ------------------------------------------------------------------
    # Optional: rotate all layer bodies after slicing (for nicer orientation)
    # ------------------------------------------------------------------
    if APPLY_ROTATION:
        move_feats = target_comp.features.moveFeatures
        bodies_to_move = adsk.core.ObjectCollection.create()
        for b in target_comp.bRepBodies:
            if b.isSolid:
                bodies_to_move.add(b)

        if bodies_to_move.count > 0:
            angle_rad = math.radians(ROTATION_DEGREES)

            axis_name = ROTATION_AXIS.upper()
            if axis_name == 'X':
                axis_vec = adsk.core.Vector3D.create(1, 0, 0)
            elif axis_name == 'Y':
                axis_vec = adsk.core.Vector3D.create(0, 1, 0)
            else:
                axis_vec = adsk.core.Vector3D.create(0, 0, 1)

            origin = adsk.core.Point3D.create(0, 0, 0)
            rot_xform = adsk.core.Matrix3D.create()
            rot_xform.setToRotation(angle_rad, axis_vec, origin)

            mv_input = move_feats.createInput(bodies_to_move, rot_xform)
            move_feats.add(mv_input)

    # ------------------------------------------------------------------
    # Optional: simple nesting along X (each slice gets its own “bay”)
    # ------------------------------------------------------------------
    if ENABLE_SLICE_NESTING and width_x > 0:
        try:
            spacing_cm = units_mgr.evaluateExpression(SLICE_SPACING_EXPR, 'cm')
        except:
            spacing_cm = width_x  # fallback
        if spacing_cm <= 0:
            spacing_cm = width_x

        sorted_slices = sorted(slice_map.keys())

        for nest_idx, slice_idx in enumerate(sorted_slices):
            bodies = slice_map[slice_idx]
            if not bodies:
                continue

            offset_x = nest_idx * (width_x + spacing_cm)

            # Build a pure translation transform
            xform = adsk.core.Matrix3D.create()
            translation = adsk.core.Vector3D.create(offset_x, 0, 0)
            xform.translation = translation

            # Apply transform directly to each body
            for b in bodies:
                try:
                    b.transform(xform)
                except Exception as e:
                    ui.messageBox(
                        f'Failed to nest slice {slice_idx:02d} body "{b.name}":\n{e}'
                    )

    # ------------------------------------------------------------------
    # Optional: auto-export STL for each slice (one file per body)
    # ------------------------------------------------------------------
    if AUTO_EXPORT_STL:
        try:
            if not os.path.isdir(STL_OUTPUT_FOLDER):
                os.makedirs(STL_OUTPUT_FOLDER, exist_ok=True)
        except Exception as e:
            ui.messageBox(f'Could not create STL output folder:\n{STL_OUTPUT_FOLDER}\n\n{e}')
        else:
            exp_mgr = new_design.exportManager
            for idx in sorted(slice_map.keys()):
                bodies = slice_map[idx]
                if not bodies:
                    continue
                for part_idx, b in enumerate(bodies, start=1):
                    filename = os.path.join(
                        STL_OUTPUT_FOLDER,
                        f'slice_{idx:02d}_part_{part_idx:02d}.stl'
                    )
                    try:
                        stl_opts = exp_mgr.createSTLExportOptions(b, filename)
                        exp_mgr.execute(stl_opts)
                    except Exception as e:
                        ui.messageBox(
                            f'Failed to export STL for slice {idx:02d} part {part_idx:02d}:\n{e}'
                        )
    # Optionally enforce long-axis along +Y: rotate +90° about Z only when X is the long axis
    if ENFORCE_LONG_AXIS_Y:
        # Allow a package-level config (if present) to disable rotations
        config_allows = True
        if 'Config' in globals() and Config is not None:
            config_allows = getattr(Config, 'ALLOW_ROTATE_90', True)
            if not config_allows and logger:
                logger.log("Slicer: Config.ALLOW_ROTATE_90 is False; skipping long-axis rotation.")

        if config_allows:
            try:
                if logger:
                    logger.log(f"Slicer: computed extents X={width_x:.3f} cm, Y={height:.3f} cm")
                if width_x > height:
                    rotate_component_bodies_90deg_z(target_comp, logger)
                    if logger:
                        logger.log("Slicer: applied +90° Z rotation to align long axis to +Y.")
                else:
                    if logger:
                        logger.log("Slicer: long axis already along Y; no rotation applied.")
            except Exception as e:
                if logger:
                    logger.log(f"Slicer: failed to apply long-axis rotation: {e}")

    ui.messageBox(
        'Slicing in NEW design complete.\n\n'
        f'New document title: {new_doc.name}\n'
        f'Component "{target_comp.name}" contains {len(final_solids)} layer bodies.\n\n'
        f'Foam thickness: {foam_thickness_expr}\n'
        'Save this new document (e.g. "CAMPER_BASE_SLICES_v1") and use it for CNC/export.\n'
        'Your original camper document remains unchanged.'
    )

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        
        # Check design mode and warn if not in direct modeling
        doc = app.activeDocument
        if doc:
            try:
                design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
                if design:
                    is_direct = design.designType == adsk.fusion.DesignTypes.DirectModelingDesignType
                    if not is_direct:
                        ui.messageBox(
                            "⚠️  WARNING: This design is in Parametric mode (design history enabled).\n\n"
                            "For best compatibility with the slicing script, switch to Direct Modeling:\n\n"
                            "Option 1: File → Workspace → Modeling\n"
                            "Option 2: Design menu → Toggle Design History OFF\n\n"
                            "The script will attempt to continue, but you may encounter API limitations.\n"
                            "Switching to Direct Modeling is strongly recommended."
                        )
            except Exception:
                pass  # Design mode check failed, continue anyway
        app = adsk.core.Application.get()
        ui = app.userInterface
        logger = AppLogger(path=r"C:\temp\test.out", ui=ui, raise_on_fail=True)

        doc = app.activeDocument
        if not doc:
            ui.messageBox('No active document.')
            return

        # Get the Design product from the document, even if Manufacture is active
        design = adsk.fusion.Design.cast(
            doc.products.itemByProductType('DesignProductType')
        )
        if not design:
            ui.messageBox(
                'Active document is not a Fusion 3D design.\n\n'
                'Open your camper model (the 3D design, not a drawing or only-CAM file) '
                'and run this script again.'
            )
            return

        root = design.rootComponent
        units_mgr = design.unitsManager

        # Foam thickness in cm (in current design)
        try:
            layer_thickness_cm = units_mgr.evaluateExpression(FOAM_THICKNESS_EXPR, 'cm')
        except:
            ui.messageBox(
                'Could not evaluate FOAM_THICKNESS_EXPR in current design:\n'
                f'"{FOAM_THICKNESS_EXPR}"'
            )
            return

        if layer_thickness_cm <= 0:
            ui.messageBox(
                'Foam thickness is non-positive in current design.\n'
                f'Current expression: "{FOAM_THICKNESS_EXPR}"'
            )
            return

        # Find camper base occurrence
        target_occ = find_target_occurrence(root)
        if not target_occ:
            msg = 'Could not find an occurrence whose name or component name contains any of:\n'
            msg += '\n'.join(TARGET_NAME_KEYWORDS)
            msg += '\n\nCheck the browser and adjust TARGET_NAME_KEYWORDS if needed.'
            ui.messageBox(msg)
            return

        target_comp = target_occ.component

        if not os.path.isdir(os.path.dirname(STEP_EXPORT_PATH)):
            ui.messageBox(
                'STEP export folder does not exist:\n'
                f'{os.path.dirname(STEP_EXPORT_PATH)}\n\n'
                'Please create that folder or change STEP_EXPORT_PATH in the script.'
            )
            return

        # Export camper base as STEP
        export_mgr = design.exportManager
        step_opts = export_mgr.createSTEPExportOptions(STEP_EXPORT_PATH, target_comp)
        export_mgr.execute(step_opts)

        # ui.messageBox(
        #     'Export complete.\n\n'
        #     f'Exported component "{target_comp.name}" as STEP to:\n{STEP_EXPORT_PATH}\n\n'
        #     'Now creating a NEW design, importing that geometry, slicing, and post-processing there.'
        # )

        # Capture current camera so new design uses same view orientation
        source_camera = app.activeViewport.camera

        # Now create new design and slice there
        slice_in_new_design(app, ui, STEP_EXPORT_PATH, FOAM_THICKNESS_EXPR, source_camera, logger=logger)

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
