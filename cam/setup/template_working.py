import adsk.core, adsk.fusion, adsk.cam, traceback

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
USE_VISIBLE_BODIES_ONLY = True

STOCK_SIDE_OFFSET = '10 mm'
STOCK_TOP_OFFSET = '0 mm'
STOCK_BOTTOM_OFFSET = '0 mm'

# ----------------------------------------------------------------------
# MASLOW Z-SAFE LINKING / HEIGHTS
# ----------------------------------------------------------------------
MASLOW_RETRACT_HEIGHT = '1.5 mm'
MASLOW_CLEARANCE_HEIGHT = '2.0 mm'
MASLOW_FEED_HEIGHT = '1.0 mm'          # if available
MASLOW_PLUNGE_FEED = '300 mm/min'
MASLOW_RETRACT_FEED = '300 mm/min'
MASLOW_ENABLE_STAY_DOWN = True
MASLOW_ENABLE_RAMPING = True


def collect_model_bodies(design: adsk.fusion.Design,
                         use_visible_only: bool) -> list:
    models = []
    root = design.rootComponent

    # Root bodies
    for b in root.bRepBodies:
        if not b.isSolid:
            continue
        if use_visible_only and not b.isVisible:
            continue
        models.append(b)

    # Occurrence bodies
    for occ in root.allOccurrences:
        comp = occ.component
        for b in comp.bRepBodies:
            if not b.isSolid:
                continue
            if use_visible_only and not b.isVisible:
                continue
            try:
                proxy = b.createForAssemblyContext(occ)
                models.append(proxy)
            except:
                models.append(b)

    return models


# ----------------------------------------------------------------------
# CAM PARAM HELPERS
# ----------------------------------------------------------------------
def setup_2d_profile_params(op: adsk.cam.Operation):
    params: adsk.cam.CAMParameters = op.parameters

    p = params.itemByName('tolerance')
    if p:
        p.expression = '0.05 mm'

    # Multiple depths with smaller passes
    p = params.itemByName('multipleDepths')
    if p:
        p.value = True

    for name in ['maximumStepdown', 'stepdown']:
        p = params.itemByName(name)
        if p:
            p.expression = '4 mm'
            break

    # No stock to leave
    for n in ['stockToLeave', 'radialStockToLeave', 'radialStockToLeaveValue']:
        p = params.itemByName(n)
        if p:
            p.expression = '0 mm'
    for n in ['axialStockToLeave', 'axialStockToLeaveValue']:
        p = params.itemByName(n)
        if p:
            p.expression = '0 mm'

    # No tabs by default
    for n in ['tabs', 'useTabs']:
        p = params.itemByName(n)
        if p:
            p.value = False

    # ✅ Maslow Z-safe linking/heights
    apply_maslow_z_safety(op)

def dump_height_params(op: adsk.cam.Operation, ui: adsk.core.UserInterface, title='Height params'):
    params: adsk.cam.CAMParameters = op.parameters
    lines = [f'{title}: {op.name}', '']
    try:
        for i in range(params.count):
            p = params.item(i)
            n = (p.name or '').lower()
            if ('height' in n) or ('clear' in n) or ('retract' in n) or ('feed' in n) or ('link' in n):
                # Try to show expression if possible
                expr = ''
                try:
                    expr = p.expression
                except:
                    try:
                        expr = str(p.value)
                    except:
                        expr = '(unreadable)'
                lines.append(f'{p.name} = {expr}')
    except Exception as e:
        lines.append(f'Failed dumping params: {e}')
    ui.messageBox('\n'.join(lines))


def _set_param_expr(params: adsk.cam.CAMParameters, names, expr: str) -> bool:
    """Try setting expression on first matching param name."""
    for n in names:
        p = params.itemByName(n)
        if p:
            try:
                p.expression = expr
                return True
            except:
                pass
    return False


def _set_param_value(params: adsk.cam.CAMParameters, names, value) -> bool:
    """Try setting .value on first matching param name."""
    for n in names:
        p = params.itemByName(n)
        if p:
            try:
                p.value = value
                return True
            except:
                pass
    return False

def apply_maslow_z_safety(op: adsk.cam.Operation):
    """
    Reduce Z-axis motion & shock loads:
    - lower retract/clearance + ensure feedHeight <= retractHeight
    - reduce linking/connection clearance plane height (often hidden source of big Z lifts)
    - enable stay-down / keep-tool-down where possible
    - enable ramping where possible
    - cap plunge/retract feeds where possible
    """
    params: adsk.cam.CAMParameters = op.parameters

    # -----------------------------
    # Primary Heights / Linking
    # -----------------------------
    _set_param_expr(params, ['retractHeight', 'retract_height', 'retractHeightOffset'], MASLOW_RETRACT_HEIGHT)
    _set_param_expr(params, ['clearanceHeight', 'clearance_height', 'clearanceHeightOffset'], MASLOW_CLEARANCE_HEIGHT)
    _set_param_expr(params, ['feedHeight', 'feed_height', 'feedHeightOffset'], MASLOW_FEED_HEIGHT)

    _set_param_value(params, ['clearanceHeightFromRetract', 'useRetractForClearance', 'clearanceFromRetract'], True)

    # Your build uses *_mode + *_offset
    p = params.itemByName('retractHeight_mode')
    if p:
        try: p.value = 'from stock top'
        except: pass

    p = params.itemByName('clearanceHeight_mode')
    if p:
        try: p.value = 'from retract height'
        except: pass

    clearance_offset_expr = f'({MASLOW_CLEARANCE_HEIGHT}) - ({MASLOW_RETRACT_HEIGHT})'
    _set_param_expr(params, ['retractHeight_offset'], MASLOW_RETRACT_HEIGHT)
    _set_param_expr(params, ['clearanceHeight_offset'], clearance_offset_expr)

    _set_param_value(params, ['retractHeight_absolute'], True)
    _set_param_value(params, ['clearanceHeight_absolute'], True)

    # Feed Height must be <= retract height
    feed_offset_expr = f'({MASLOW_RETRACT_HEIGHT}) - 0.5 mm'  # e.g. 1.0mm when retract=1.5mm

    p = params.itemByName('feedHeight_mode')
    if p:
        try:
            # Your dump shows 'from top' is used; don't fight it—just set offset/value safely.
            # If you *prefer* stock top, change to 'from stock top' but only if your build accepts it.
            # p.value = 'from stock top'
            pass
        except:
            pass

    _set_param_expr(params, ['feedHeight_offset', 'feedHeightOffset', 'feedHeight'], feed_offset_expr)
    _set_param_value(params, ['feedHeight_absolute'], True)

    # -----------------------------
    # Stay down / keep tool down
    # -----------------------------
    if MASLOW_ENABLE_STAY_DOWN:
        _set_param_value(params, ['stayDown', 'useStayDown', 'keepToolDown', 'useKeepToolDown'], True)
        _set_param_value(params, ['liftBetweenCuts', 'liftBetweenPasses', 'liftToClearance'], False)

        # Some ops expose an explicit liftHeight
        _set_param_expr(params, ['liftHeight', 'lift_height'], '0 mm')

        # Some ops have a connections retraction type; try to avoid "full" if possible.
        # (Not all strategies accept the same enum strings; failures are harmless.)
        p = params.itemByName('connections_retraction_type')
        if p:
            for cand in ['minimum', 'short', 'none', 'stayDown', 'full']:
                try:
                    p.value = cand
                    if cand != 'full':
                        break
                except:
                    continue
    # -----------------------------
    # Ramping
    # -----------------------------
    if MASLOW_ENABLE_RAMPING:
        _set_param_value(params, ['useRamping', 'ramping', 'enableRamping'], True)
        _set_param_value(params, ['allowPlunging', 'usePlunge', 'plungeOnly'], False)

        # Some ops have a ramp clearance height (separate from main clearance)
        _set_param_expr(params, ['rampClearanceHeight', 'ramp_clearanceHeight'], MASLOW_CLEARANCE_HEIGHT)

    # -----------------------------
    # Z feeds (your build uses tool_feedPlunge/tool_feedRetract)
    # -----------------------------
    _set_param_expr(params,
                    ['plungeFeedrate', 'plungeFeed', 'plunge_feedrate', 'tool_feedPlunge'],
                    MASLOW_PLUNGE_FEED)
    _set_param_expr(params,
                    ['retractFeedrate', 'retractFeed', 'retract_feedrate', 'tool_feedRetract'],
                    MASLOW_RETRACT_FEED)

    # -----------------------------
    # Connection / linking clearance area (YOUR BUILD needs *_value / mode tweaks)
    # -----------------------------
    connection_clearance_expr = MASLOW_CLEARANCE_HEIGHT  # e.g. '2.0 mm'

    # Clearance AREA HEIGHT (plane)
    # Set mode (best effort), then set both offset and value.
    p = params.itemByName('connectionMoveClearanceAreaHeight_mode')
    if p:
        # Try modes that commonly mean "use the value directly"
        for cand in ['absolute', 'distance', 'value', 'from stock top']:
            try:
                p.value = cand
                break
            except:
                continue

    _set_param_expr(params, ['connectionMoveClearanceAreaHeight_offset'], connection_clearance_expr)
    _set_param_expr(params, ['connectionMoveClearanceAreaHeight_value'], connection_clearance_expr)
    _set_param_value(params, ['connectionMoveClearanceAreaHeight_absolute'], True)

    # Clearance AREA SPHERE RADIUS
    # Force radius mode so _value becomes the radius (see your _direct expression).
    p = params.itemByName('connectionMoveClearanceAreaSphereRadius_mode')
    if p:
        try:
            p.value = 'radius'
        except:
            pass

    _set_param_expr(params, ['connectionMoveClearanceAreaSphereRadius_offset'], connection_clearance_expr)
    _set_param_expr(params, ['connectionMoveClearanceAreaSphereRadius_value'], connection_clearance_expr)
    _set_param_value(params, ['connectionMoveClearanceAreaSphereRadius_absolute'], True)

def setup_3d_rough_params(op: adsk.cam.Operation):
    params: adsk.cam.CAMParameters = op.parameters

    p = params.itemByName('tolerance')
    if p:
        p.expression = '0.05 mm'

    p = params.itemByName('maximumStepdown')
    if p:
        p.expression = '6 mm'

    for name in ['adaptive_optimalLoad', 'optimalLoad']:
        p = params.itemByName(name)
        if p:
            p.expression = '3 mm'
            break

    for n in ['stockToLeaveRadial', 'radialStockToLeave', 'radialStockToLeaveValue']:
        p = params.itemByName(n)
        if p:
            p.expression = '1 mm'
            break

    for n in ['stockToLeaveAxial', 'axialStockToLeave', 'axialStockToLeaveValue']:
        p = params.itemByName(n)
        if p:
            p.expression = '1 mm'
            break

    # ✅ Maslow Z-safe linking/heights
    apply_maslow_z_safety(op)

def setup_3d_finish_params(op: adsk.cam.Operation):
    params: adsk.cam.CAMParameters = op.parameters

    p = params.itemByName('tolerance')
    if p:
        p.expression = '0.05 mm'

    p = params.itemByName('stepover')
    if p:
        p.expression = '0.5 mm'

    # ✅ Maslow Z-safe linking/heights
    apply_maslow_z_safety(op)

def configure_stock_and_wcs(setup: adsk.cam.Setup):
    """
    Configure stock as a 4×8 ft sheet, centered on the model,
    with WCS origin at stock center-top.
    """
    params: adsk.cam.CAMParameters = setup.parameters

    # Use fixed box stock (explicit 4x8 sheet)
    setup.stockMode = adsk.cam.SetupStockModes.FixedBoxStock

    # Width (X) = 96 in (2438.4 mm)  -> long direction, left-right on machine
    p = params.itemByName('stockWidth')
    if p:
        p.expression = '2438.4 mm'

    # Depth/Height (Y) = 48 in (1219.2 mm) -> front-back on machine
    p = params.itemByName('stockHeight')
    if p:
        p.expression = '1219.2 mm'

    # Thickness (Z). Use ~40 mm to comfortably cover 1.5" foam
    p = params.itemByName('stockThickness')
    if p:
        p.expression = '40 mm'

    # Center the model in the stock
    p = params.itemByName('stockOffsetX')
    if p:
        p.expression = '0 mm'
    p = params.itemByName('stockOffsetY')
    if p:
        p.expression = '0 mm'
    p = params.itemByName('stockOffsetZ')
    if p:
        p.expression = '0 mm'

    # WCS origin: center of stock top (works with G54 at foam center-top)
    try:
        box_param = params.itemByName('wcs_origin_boxPoint')
        if box_param:
            # "top 5" = top-face, center box point in Fusion's internal naming
            val = box_param.value
            val.value = 'top 5'
    except:
        pass
    # If Fusion adds a direct origin mode parameter in your build,
    # we can also try to force "Stock box point" here, but it's safe
    # to leave orientation at its default and only move the box point.


def create_2d_contour_input(ops: adsk.cam.Operations,
                            ui: adsk.core.UserInterface) -> adsk.cam.OperationInput:
    """
    Different Fusion builds use different internal IDs for 2D Contour.
    Try a few common ones and return the first that works.
    """
    candidates = [
        '2dContour',
        '2DContour',
        'contour2d',
        '2d-contour',
        'contour',  # generic fallback; may map to 2D contour
    ]

    last_err = None
    for strat in candidates:
        try:
            return ops.createInput(strat)
        except Exception as e:
            last_err = e
            continue

    ui.messageBox(
        'Could not create a 2D Contour operation automatically.\n'
        f'Fusion reported: {last_err}\n\n'
        'You can still create a manual 2D Contour (Foam cutout) in the UI,\n'
        'and then let the script handle the 3D rough/finish.'
    )
    return None


# ----------------------------------------------------------------------
# MAIN CREATOR
# ----------------------------------------------------------------------
def create_foam_setup_and_ops(cam: adsk.cam.CAM,
                              design: adsk.fusion.Design,
                              ui: adsk.core.UserInterface):
    setups: adsk.cam.Setups = cam.setups
    models = collect_model_bodies(design, USE_VISIBLE_BODIES_ONLY)

    if not models:
        ui.messageBox(
            'No solid bodies found in the design.\n\n'
            'Make sure your sliced foam design is open and that at least one '
            'Layer_* body is visible, then re-run.'
        )
        return

    model_coll = adsk.core.ObjectCollection.create()
    for m in models:
        model_coll.add(m)

    setup_input: adsk.cam.SetupInput = setups.createInput(
        adsk.cam.OperationTypes.MillingOperation
    )
    setup: adsk.cam.Setup = setups.add(setup_input)
    setup.name = 'Foam_Slice_Setup_3D_AUTO'

    try:
        setup.models = model_coll
    except Exception as e:
        ui.messageBox(
            f'Failed to assign models to setup:\n{e}\n'
            'Make sure at least one Layer_* body is visible.'
        )
        return

    # Configure 4x8 stock and center-top WCS
    configure_stock_and_wcs(setup)
    ops = setup.operations

    # ---------------- 1) 2D CONTOUR (PROFILE CUTOUT) -------------------
    profile_op = None
    profile_input = create_2d_contour_input(ops, ui)
    if profile_input:
        profile_input.displayName = 'Foam Cutout 2D (Profile)'
        profile_op = ops.add(profile_input)
        setup_2d_profile_params(profile_op)
        dump_height_params(profile_op, ui, 'AFTER applying Maslow Z safety')

    # ---------------- 2) 3D ROUGH – ADAPTIVE ---------------------------
    rough_input: adsk.cam.OperationInput = ops.createInput('adaptive')
    rough_input.displayName = 'Foam Rough 3D (Adaptive)'
    rough_op = ops.add(rough_input)
    setup_3d_rough_params(rough_op)
    dump_height_params(rough_op, ui, 'AFTER applying Maslow Z safety')

    # ---------------- 3) 3D FINISH – SCALLOP ---------------------------
    finish_input: adsk.cam.OperationInput = ops.createInput('scallop')
    finish_input.displayName = 'Foam Finish 3D (Scallop)'
    finish_op = ops.add(finish_input)
    setup_3d_finish_params(finish_op)
    dump_height_params(finish_op, ui, 'AFTER applying Maslow Z safety')

    msg = [
        'Auto CAM setup created:',
        f'  Setup: {setup.name}',
        '  Operations (in order):'
    ]
    if profile_op:
        msg.append('    1) Foam Cutout 2D (Profile)')
        msg.append('    2) Foam Rough 3D (Adaptive)')
        msg.append('    3) Foam Finish 3D (Scallop)')
    else:
        msg.append('    1) Foam Rough 3D (Adaptive)')
        msg.append('    2) Foam Finish 3D (Scallop)')
        msg.append('')
        msg.append('NOTE: 2D Contour could not be created automatically;')
        msg.append('      create a manual 2D Contour cutout if needed.')

    msg.append('')
    msg.append('Next steps per slice:')
    msg.append('  1) Edit each operation and pick your 6.35mm (1/4") foam tool.')
    if profile_op:
        msg.append('  2) For "Foam Cutout 2D (Profile)", on the Geometry tab,')
        msg.append('     select the outer perimeter edge loop as the contour.')
        msg.append('  3) On the 3D ops, add a Machining Boundary if you want')
        msg.append('     to keep the middle from being re-machined.')
        msg.append('  4) Generate toolpaths and simulate.')
        msg.append('  5) When happy, right-click the setup and choose')
        msg.append('     "Create Template" to reuse for other slices.')
    else:
        msg.append('  2) (Optional) Create a manual 2D Contour cutout first,')
        msg.append('     then let the 3D ops clean up the shape.')

    ui.messageBox('\n'.join(msg))


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        doc = app.activeDocument
        if not doc:
            ui.messageBox('No active document.')
            return

        design = adsk.fusion.Design.cast(
            doc.products.itemByProductType('DesignProductType')
        )
        if not design:
            ui.messageBox('Active document is not a Fusion design.')
            return

        # Make sure Manufacture workspace is active
        cam_ws = ui.workspaces.itemById('CAMEnvironment')
        if cam_ws:
            cam_ws.activate()

        products: adsk.core.Products = doc.products
        cam = adsk.cam.CAM.cast(products.itemByProductType('CAMProductType'))
        if not cam:
            ui.messageBox(
                'No CAM product found.\n\n'
                'Open the Manufacture workspace once for this design, then re-run.'
            )
            return

        create_foam_setup_and_ops(cam, design, ui)

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
