import adsk.core, adsk.fusion, adsk.cam, traceback, math, os

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------
# Keywords to find your camper base occurrence in the current design
TARGET_NAME_KEYWORDS = ['CAMPER BASE', 'CAMBER BASE']

# Foam thickness for slicing (change as needed)
FOAM_THICKNESS_EXPR = '2 in'   # e.g. '1.5 in', '50 mm'

# Temp STEP export path (ensure folder exists)
STEP_EXPORT_PATH = r'C:\temp\camper_base_slice.step'

# Optional rotation to apply in the NEW design AFTER slicing,
# just to match your preferred view/orientation.
APPLY_ROTATION   = True        # set False if you don't want any rotation
ROTATION_AXIS    = 'X'         # 'X', 'Y', or 'Z'
ROTATION_DEGREES = 90.0       # angle in degrees; flip sign if orientation is flipped

# ----------------------------------------------------------------------


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
                        source_camera: adsk.core.Camera):
    """
    Create a new Fusion design, import the STEP at step_path,
    slice into foam layers along Y, THEN rotate the result if requested.
    This version does NOT try to combine bodies (avoids Combine failures).
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
    # Compute overall bounding box in Y across ALL solids (no combine)
    # ------------------------------------------------------------------
    min_y = None
    max_y = None

    for b in target_comp.bRepBodies:
        if not b.isSolid:
            continue
        bb = b.boundingBox
        by_min = bb.minPoint.y
        by_max = bb.maxPoint.y
        if min_y is None or by_min < min_y:
            min_y = by_min
        if max_y is None or by_max > max_y:
            max_y = by_max

    if min_y is None or max_y is None:
        ui.messageBox('Failed to compute bounding box in new design.')
        return

    height = max_y - min_y
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
    # Sort resulting bodies by center Y and rename as layers
    # ------------------------------------------------------------------
    final_solids = [b for b in target_comp.bRepBodies if b.isSolid]
    if not final_solids:
        ui.messageBox('No solid bodies found after splitting in new design.')
        return

    def center_y(b: adsk.fusion.BRepBody) -> float:
        bb2 = b.boundingBox
        return 0.5 * (bb2.minPoint.y + bb2.maxPoint.y)

    final_solids.sort(key=center_y)

    for idx, b in enumerate(final_solids, start=1):
        b.name = f'Layer_{idx:02d}'

    # ------------------------------------------------------------------
    # OPTIONAL: Rotate all layer bodies after slicing (for nicer orientation)
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
            transform = adsk.core.Matrix3D.create()
            transform.setToRotation(angle_rad, axis_vec, origin)

            mv_input = move_feats.createInput(bodies_to_move, transform)
            move_feats.add(mv_input)

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

        product = app.activeProduct
        design = adsk.fusion.Design.cast(product)
        if not design:
            ui.messageBox('No active Fusion design.')
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

        ui.messageBox(
            'Export complete.\n\n'
            f'Exported component "{target_comp.name}" as STEP to:\n{STEP_EXPORT_PATH}\n\n'
            'Now creating a NEW design, importing that geometry, slicing, and rotating there.'
        )

        # Capture current camera so new design uses same view orientation
        source_camera = app.activeViewport.camera

        # Now create new design and slice there
        slice_in_new_design(app, ui, STEP_EXPORT_PATH, FOAM_THICKNESS_EXPR, source_camera)

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
