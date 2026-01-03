import adsk.core, adsk.fusion, traceback, os, sys

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

def panelize_step_into_new_design(app, ui, step_path, capture_expr, panel_priority, keep_tools_visible, source_camera):
    """
    Slice a camper into panel components.
    
    Coordinate system:
      X = width (left-right, min_x is LEFT, max_x is RIGHT)
      Y = length (front-back, min_y is FRONT, max_y is REAR)
      Z = height (up-down, max_z is TOP, min_z is BOTTOM)
    
    Panels created (front and bottom are OPEN):
      TOP   - high Z values (roof)
      REAR  - high Y values (back wall)
      LEFT  - low X values (left side)
      RIGHT - high X values (right side)
    """
    debug_log = []
    debug_log.append("=== panelize_step_into_new_design START ===")
    debug_log.append(f"  panel_priority: {panel_priority}")
    
    def write_debug():
        try:
            # Simple approach: use the directory that FoamPanelizer.py already created
            # Just get it from os.environ or reconstruct it
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            logs_root = os.path.join(desktop, "fusion_cam_logs")
            
            # Find the most recent timestamped folder
            if os.path.isdir(logs_root):
                folders = [os.path.join(logs_root, d) for d in os.listdir(logs_root) 
                          if os.path.isdir(os.path.join(logs_root, d))]
                if folders:
                    latest_folder = max(folders, key=os.path.getmtime)
                    debug_file = os.path.join(latest_folder, "panelizer_debug.log")
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write("\n".join(debug_log))
                        f.flush()
                    return
        except Exception as e:
            pass
        
        # Fallback to Desktop
        try:
            fallback = os.path.join(os.path.expanduser("~"), "Desktop", "panelizer_debug.log")
            with open(fallback, "w", encoding="utf-8") as f:
                f.write("\n".join(debug_log))
                f.flush()
        except:
            pass
    
    # Write immediate start marker
    write_debug()
    
    try:
        new_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        new_design = adsk.fusion.Design.cast(new_doc.products.itemByProductType('DesignProductType'))
        if not new_design:
            debug_log.append("ERROR: Failed to create new design")
            write_debug()
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
            debug_log.append(f"ERROR: STEP not found: {step_path}")
            write_debug()
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
            debug_log.append("ERROR: No solids after import")
            write_debug()
            ui.messageBox('No solids after import.')
            return {'ok': False}

        capture_cm = units.evaluateExpression(capture_expr, 'cm')
        ext = _get_bbox_extents(target_comp)
        if not ext:
            debug_log.append("ERROR: Could not get bbox extents")
            write_debug()
            return {'ok': False}
        min_x, max_x, min_y, max_y, min_z, max_z = ext
        
        # Debug: print bbox dimensions to understand orientation
        # ACTUAL ORIENTATION (from log analysis):
        # X = 243.58 cm (LONGEST - this is front-back, the long axis)
        # Y = 63.50 cm (SHORTEST - this is left-right, the width)
        # Z = 146.68 cm (MIDDLE - this is up-down, the height)
        # So real coordinates: X=length, Y=width, Z=height (rotated 90° from expected)
        width_x = max_x - min_x
        length_y = max_y - min_y
        height_z = max_z - min_z
        debug_log.append(f"BBox dimensions (cm):")
        debug_log.append(f"  X (actual length/long-axis): {width_x:.2f} cm")
        debug_log.append(f"  Y (actual width/left-right):  {length_y:.2f} cm")
        debug_log.append(f"  Z (actual height/up-down):    {height_z:.2f} cm")
        debug_log.append(f"  Z (height): {height_z:.2f} cm  [{min_z:.2f} to {max_z:.2f}]")
        debug_log.append(f"Expected: Y should be longest (front-back), Z should be tallest (up-down)")
        
        margin = capture_cm * 0.25

        temp_mgr = adsk.fusion.TemporaryBRepManager.get()

        def make_slab(tag, min_pt, max_pt):
            cx = 0.5 * (min_pt.x + max_pt.x)
            cy = 0.5 * (min_pt.y + max_pt.y)
            cz = 0.5 * (min_pt.z + max_pt.z)
            length = abs(max_pt.x - min_pt.x)
            width  = abs(max_pt.y - min_pt.y)
            height = abs(max_pt.z - min_pt.z)
            center = adsk.core.Point3D.create(cx, cy, cz)
            length_dir = adsk.core.Vector3D.create(1, 0, 0)
            width_dir  = adsk.core.Vector3D.create(0, 1, 0)
            obb = adsk.core.OrientedBoundingBox3D.create(center, length_dir, width_dir, length, width, height)
            temp_box = temp_mgr.createBox(obb)
            return _insert_temp_body(target_comp, temp_box, f'_TOOL_{tag}', keep_tools_visible)

        # Create slabs for each panel
        # ACTUAL ORIENTATION: X=length (front-back), Y=width (left-right), Z=height (up-down)
        # TOP   - high Z values (roof)
        # REAR  - high X values (back, far end of long axis)
        # LEFT  - low Y values (left side)
        # RIGHT - high Y values (right side)
        slabs = {
            'TOP': make_slab('TOP',
                adsk.core.Point3D.create(min_x - margin, min_y - margin, max_z - capture_cm),
                adsk.core.Point3D.create(max_x + margin, max_y + margin, max_z + margin)),
            'REAR': make_slab('REAR',
                adsk.core.Point3D.create(max_x - capture_cm, min_y - margin, min_z - margin),
                adsk.core.Point3D.create(max_x + margin, max_y + margin, max_z + margin)),
            'LEFT': make_slab('LEFT',
                adsk.core.Point3D.create(min_x - margin, min_y - margin, min_z - margin),
                adsk.core.Point3D.create(max_x + margin, min_y + capture_cm, max_z + margin)),
            'RIGHT': make_slab('RIGHT',
                adsk.core.Point3D.create(min_x - margin, max_y - capture_cm, min_z - margin),
                adsk.core.Point3D.create(max_x + margin, max_y + margin, max_z + margin)),
        }

        extracted = 0
        panel_results = {}
        
        debug_log.append(f"Creating panels in order: {panel_priority}")
        debug_log.append(f"Original solids count: {len(solids)}")
        
        # Get the first solid as our source geometry
        if not solids:
            ui.messageBox('No solids to panelize')
            return {'ok': False}
        
        source_solid = solids[0]
        debug_log.append(f"Source solid: {source_solid.name}")
        
        # For each panel, COPY the source solid and intersect the copy with the slab
        temp_mgr = adsk.fusion.TemporaryBRepManager.get()
        
        for pname in panel_priority:
            pname = pname.upper()
            slab = slabs.get(pname)
            if not slab:
                debug_log.append(f"  Skipping {pname} - not in slabs dict")
                continue
            
            debug_log.append(f"  Creating {pname} panel...")
            try:
                # Copy the source solid using TemporaryBRepManager
                source_brep = temp_mgr.copy(source_solid)
                
                # Perform boolean intersection at BRep level
                slab_brep = temp_mgr.copy(slab)
                temp_mgr.booleanOperation(source_brep, slab_brep, adsk.fusion.BooleanTypes.IntersectionBooleanType)
                
                # Add the intersected result as new body
                bf = target_comp.features.baseFeatures.add()
                bf.startEdit()
                result_body = target_comp.bRepBodies.add(source_brep, bf)
                bf.finishEdit()
                result_body.name = f'PANEL_{pname}'
                result_body.isVisible = True
                
                extracted += 1
                panel_results[pname] = result_body
                debug_log.append(f"    ✓ Created PANEL_{pname}")
            except Exception as e:
                debug_log.append(f"    ✗ Failed to create {pname} panel: {e}")
                debug_log.append(traceback.format_exc())
        
        # Hide the original source solid
        source_solid.isVisible = False
        debug_log.append(f"Hidden original solid: {source_solid.name}")
        
        # Hide tool slabs if requested
        if not keep_tools_visible:
            for s in slabs.values():
                s.isVisible = False
        
        # Hide non-panel solids
        for b in _collect_solids(target_comp):
            if not b.name.startswith('PANEL_'):
                b.isVisible = False

        debug_log.append(f"Final panel count: {extracted}")
        debug_log.append("=== panelize_step_into_new_design END ===")
        write_debug()

        return {'ok': True, 'doc_name': new_doc.name, 'panel_count': extracted}
    except Exception as exc:
        debug_log.append(f"=== panelize_step_into_new_design EXCEPTION ===")
        debug_log.append(f"{exc}")
        debug_log.append(traceback.format_exc())
        write_debug()
        ui.messageBox('panelizer_core failed:\n' + traceback.format_exc())
        return {'ok': False}
