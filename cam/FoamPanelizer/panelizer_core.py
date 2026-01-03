import adsk.core, adsk.fusion, traceback, os

def find_target_occurrence(root: adsk.fusion.Component, keywords):
    keys = [k.lower() for k in keywords]
    for occ in root.allOccurrences:
        occ_name = (occ.name or '').lower()
        comp_name = (occ.component.name or '').lower()
        if any(k in occ_name or k in comp_name for k in keys):
            return occ
    return None

def _collect_solids(comp: adsk.fusion.Component):
    return [b for b in comp.bRepBodies if b.isSolid and b.isValid]

def _get_bbox_extents(comp: adsk.fusion.Component):
    min_x=min_y=min_z=None
    max_x=max_y=max_z=None
    for b in comp.bRepBodies:
        if not b.isSolid or not b.isValid:
            continue
        bb=b.boundingBox
        pmin, pmax = bb.minPoint, bb.maxPoint
        min_x = pmin.x if min_x is None else min(min_x, pmin.x)
        min_y = pmin.y if min_y is None else min(min_y, pmin.y)
        min_z = pmin.z if min_z is None else min(min_z, pmin.z)
        max_x = pmax.x if max_x is None else max(max_x, pmax.x)
        max_y = pmax.y if max_y is None else max(max_y, pmax.y)
        max_z = pmax.z if max_z is None else max(max_z, pmax.z)
    if None in (min_x,min_y,min_z,max_x,max_y,max_z):
        return None
    return min_x, max_x, min_y, max_y, min_z, max_z

def _insert_temp_body(comp, temp_body, name, visible):
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    new_body = comp.bRepBodies.add(temp_body, bf)
    bf.finishEdit()
    new_body.name = name
    new_body.isVisible = visible
    return new_body

def _combine(comp, target_body, tool_body, op):
    combine = comp.features.combineFeatures
    tools = adsk.core.ObjectCollection.create()
    tools.add(tool_body)
    ci = combine.createInput(target_body, tools)
    ci.operation = op
    ci.isKeepToolBodies = True
    ci.isNewComponent = False
    combine.add(ci)

def panelize_step_into_new_design(app, ui, step_path, capture_expr, rear_side, panel_priority, keep_tools_visible, source_camera):
    try:
        new_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        new_design = adsk.fusion.Design.cast(new_doc.products.itemByProductType('DesignProductType'))
        if not new_design:
            return {'ok': False}

        try:
            vp = app.activeViewport
            vp.camera = source_camera
            vp.update()
        except:
            pass

        units = new_design.unitsManager
        root = new_design.rootComponent

        if not os.path.isfile(step_path):
            ui.messageBox('STEP not found:\n' + step_path)
            return {'ok': False}

        import_mgr = app.importManager
        step_opts = import_mgr.createSTEPImportOptions(step_path)
        import_mgr.importToTarget(step_opts, root)

        target_comp = root
        if not _collect_solids(root) and root.allOccurrences.count > 0:
            target_comp = root.allOccurrences.item(0).component

        solids = _collect_solids(target_comp)
        if not solids:
            ui.messageBox('No solids after import.')
            return {'ok': False}

        capture_cm = units.evaluateExpression(capture_expr, 'cm')
        ext = _get_bbox_extents(target_comp)
        if not ext:
            return {'ok': False}
        min_x, max_x, min_y, max_y, min_z, max_z = ext
        margin = capture_cm * 0.25

        temp_mgr = adsk.fusion.TemporaryBRepManager.get()

        def make_slab(tag, min_pt, max_pt):
            # Compute center + extents (Fusion internal length units are cm)
            cx = 0.5 * (min_pt.x + max_pt.x)
            cy = 0.5 * (min_pt.y + max_pt.y)
            cz = 0.5 * (min_pt.z + max_pt.z)

            length = abs(max_pt.x - min_pt.x)  # along X
            width  = abs(max_pt.y - min_pt.y)  # along Y
            height = abs(max_pt.z - min_pt.z)  # along Z

            center = adsk.core.Point3D.create(cx, cy, cz)

            # Define box orientation axes:
            # lengthDirection = +X, widthDirection = +Y => height is +Z via right-hand rule
            length_dir = adsk.core.Vector3D.create(1, 0, 0)
            width_dir  = adsk.core.Vector3D.create(0, 1, 0)

            obb = adsk.core.OrientedBoundingBox3D.create(center, length_dir, width_dir, length, width, height)
            temp_box = temp_mgr.createBox(obb)

            return _insert_temp_body(target_comp, temp_box, f'_TOOL_{tag}', keep_tools_visible)

        slabs = {
            'TOP': make_slab('TOP',
                adsk.core.Point3D.create(min_x - margin, max_y - capture_cm, min_z - margin),
                adsk.core.Point3D.create(max_x + margin, max_y + margin, max_z + margin)),
            'LEFT': make_slab('LEFT',
                adsk.core.Point3D.create(min_x - margin, min_y - margin, min_z - margin),
                adsk.core.Point3D.create(min_x + capture_cm, max_y + margin, max_z + margin)),
            'RIGHT': make_slab('RIGHT',
                adsk.core.Point3D.create(max_x - capture_cm, min_y - margin, min_z - margin),
                adsk.core.Point3D.create(max_x + margin, max_y + margin, max_z + margin)),
        }
        if rear_side.upper() == 'MAX_Z':
            slabs['REAR'] = make_slab('REAR',
                adsk.core.Point3D.create(min_x - margin, min_y - margin, max_z - capture_cm),
                adsk.core.Point3D.create(max_x + margin, max_y + margin, max_z + margin))
        else:
            slabs['REAR'] = make_slab('REAR',
                adsk.core.Point3D.create(min_x - margin, min_y - margin, min_z - margin),
                adsk.core.Point3D.create(max_x + margin, max_y + margin, min_z + capture_cm))

        extracted = 0
        # Simple: intersect each solid with slab into new bodies by duplicating each body as tool target.
        # For v1, we operate in-place per-body (may create multiple bodies).
        for pname in panel_priority:
            pname = pname.upper()
            slab = slabs.get(pname)
            if not slab:
                continue
            for b in list(_collect_solids(target_comp)):
                try:
                    _combine(target_comp, b, slab, adsk.fusion.FeatureOperations.IntersectFeatureOperation)
                except:
                    pass
            # Rename bodies near the slab side
            for b in _collect_solids(target_comp):
                bb = b.boundingBox
                cx = 0.5*(bb.minPoint.x+bb.maxPoint.x)
                cy = 0.5*(bb.minPoint.y+bb.maxPoint.y)
                cz = 0.5*(bb.minPoint.z+bb.maxPoint.z)
                ok=False
                if pname=='TOP' and cy >= (max_y - capture_cm*0.9): ok=True
                if pname=='LEFT' and cx <= (min_x + capture_cm*0.9): ok=True
                if pname=='RIGHT' and cx >= (max_x - capture_cm*0.9): ok=True
                if pname=='REAR':
                    if rear_side.upper()=='MAX_Z' and cz >= (max_z - capture_cm*0.9): ok=True
                    if rear_side.upper()!='MAX_Z' and cz <= (min_z + capture_cm*0.9): ok=True
                if ok and not b.name.startswith('PANEL_'):
                    extracted += 1
                    b.name = f'PANEL_{pname}_{extracted:02d}'
                    b.isVisible = True

        if not keep_tools_visible:
            for s in slabs.values():
                s.isVisible = False

        return {'ok': True, 'doc_name': new_doc.name, 'panel_count': extracted}
    except:
        ui.messageBox('panelizer_core failed:\n' + traceback.format_exc())
        return {'ok': False}
