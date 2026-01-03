import adsk.core, adsk.fusion, traceback, os

# ------------------------------------------------------------
# Helper: detect design mode (parametric vs direct)
# ------------------------------------------------------------
def _is_parametric(design: adsk.fusion.Design) -> bool:
    """
    Robust check for history/timeline. If timeline exists, history is on.
    """
    try:
        return bool(design.timeline)
    except:
        return False


def _add_temp_body_to_component(design: adsk.fusion.Design,
                                comp: adsk.fusion.Component,
                                temp_body: adsk.fusion.BRepBody,
                                name: str,
                                logger=None) -> adsk.fusion.BRepBody:
    """
    Insert a transient/temporary BRepBody into a component, correctly handling:
      - Parametric (history ON): must add inside a BaseFeature edit
      - Direct (history OFF): BaseFeatures unsupported; add directly
    """
    if temp_body is None:
        raise RuntimeError("Temp body is None (boolean likely returned empty / wrong variable passed).")
    if hasattr(temp_body, "isValid") and not temp_body.isValid:
        raise RuntimeError("Temp body is invalid (boolean likely produced empty result).")

    if _is_parametric(design):
        # Parametric: add within BaseFeature edit
        base_feat = comp.features.baseFeatures.add()
        base_feat.startEdit()
        new_body = comp.bRepBodies.add(temp_body, base_feat)
        base_feat.finishEdit()
    else:
        # Direct modeling: BaseFeatures not supported
        new_body = comp.bRepBodies.add(temp_body)

    new_body.name = name
    new_body.isVisible = True
    return new_body


# ------------------------------------------------------------
# Utility: find a target occurrence by keyword (unchanged)
# ------------------------------------------------------------
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


def _get_bbox_extents_from_body(body: adsk.fusion.BRepBody):
    bb = body.boundingBox
    pmin, pmax = bb.minPoint, bb.maxPoint
    return pmin.x, pmax.x, pmin.y, pmax.y, pmin.z, pmax.z


def _v(x, y, z):
    return adsk.core.Vector3D.create(x, y, z)


def _dot(a: adsk.core.Vector3D, b: adsk.core.Vector3D) -> float:
    return a.x * b.x + a.y * b.y + a.z * b.z


def _scaled_point(p: adsk.core.Point3D, n: adsk.core.Vector3D, dist_cm: float) -> adsk.core.Point3D:
    # returns p + n * dist
    return adsk.core.Point3D.create(p.x + n.x * dist_cm, p.y + n.y * dist_cm, p.z + n.z * dist_cm)


def _get_outward_normal(body: adsk.fusion.BRepBody, face: adsk.fusion.BRepFace, 
                        bbox_center: adsk.core.Point3D, eps_cm: float) -> adsk.core.Vector3D:
    """
    Returns an *outward* unit normal using cheap heuristic first, then optional containment check.
    Phase C optimization: use dot(normal, normalize(facePoint - bboxCenter)) to decide flip;
    only call pointContainment() if ambiguous.
    """
    p = face.pointOnFace
    ok, n = face.evaluator.getNormalAtPoint(p)
    if not ok:
        return _v(0, 0, 1)

    n.normalize()

    # Cheap heuristic: dot product with vector from bbox center to face point
    # If negative, normal likely points inward
    try:
        to_face = adsk.core.Vector3D.create(
            p.x - bbox_center.x,
            p.y - bbox_center.y,
            p.z - bbox_center.z
        )
        to_face.normalize()
        heur_dot = _dot(n, to_face)
        
        # If very confident (heur_dot > 0.1), trust the heuristic
        if heur_dot > 0.1:
            # Normal likely points outward
            n.normalize()
            return n
        elif heur_dot < -0.1:
            # Normal likely points inward, flip
            n.scaleBy(-1.0)
            n.normalize()
            return n
        # else: ambiguous, use pointContainment
    except:
        pass

    # Ambiguous or heuristic failed: use pointContainment
    p_out = _scaled_point(p, n, eps_cm)
    try:
        rel = body.pointContainment(p_out)
        if rel == adsk.fusion.PointContainment.PointInsidePointContainment:
            n.scaleBy(-1.0)
    except:
        pass

    n.normalize()
    return n


def _classify_face_option_b(out_n: adsk.core.Vector3D,
                           thr: float = 0.65):
    """
    Option B axes:
      UP    = +Z
      REAR  = +X
      RIGHT = +Y
      LEFT  = -Y
    Returns one of: 'TOP','REAR','LEFT','RIGHT' or None (ignore/open).
    """
    UP = _v(0, 0, 1)
    REAR = _v(1, 0, 0)
    RIGHT = _v(0, 1, 0)
    LEFT = _v(0, -1, 0)
    FRONT = _v(-1, 0, 0)
    DOWN = _v(0, 0, -1)

    scores = {
        'TOP': _dot(out_n, UP),
        'REAR': _dot(out_n, REAR),
        'RIGHT': _dot(out_n, RIGHT),
        'LEFT': _dot(out_n, LEFT),
        'FRONT': _dot(out_n, FRONT),
        'BOTTOM': _dot(out_n, DOWN),
    }

    # Keep front and bottom open
    best = max(scores, key=lambda k: scores[k])
    best_val = scores[best]

    if best_val < thr:
        return None

    if best in ('FRONT', 'BOTTOM'):
        return None

    if best in ('TOP', 'REAR', 'LEFT', 'RIGHT'):
        return best

    return None


def _make_faces_collection(faces) -> adsk.core.ObjectCollection:
    oc = adsk.core.ObjectCollection.create()
    for f in faces:
        oc.add(f)
    return oc


def panelize_step_into_new_design(app,
                                 ui,
                                 step_path: str,
                                 capture_expr: str,
                                 panel_priority,
                                 keep_tools_visible: bool,
                                 source_camera,
                                 log_folder: str = None):
    """
    FACE-DRIVEN panel extraction (Option B axes):
      UP=+Z, REAR=+X, RIGHT=+Y, LEFT=-Y
    Produces solid panel bodies by thickening exterior face sets inward.
    Leaves FRONT (-X) and BOTTOM (-Z) open (ignored).
    
    Args:
        log_folder: Optional directory for debug log. If None, writes next to STEP file.
    """

    debug = []
    def d(msg):
        debug.append(msg)
    
    dbg_path = None
    if log_folder and os.path.isdir(log_folder):
        dbg_path = os.path.join(log_folder, "panelizer_face_debug.log")

    try:
        d("=== panelize_step_into_new_design (FACE-DRIVEN) START ===")
        d(f"step_path={step_path}")
        d(f"capture_expr={capture_expr}")
        d(f"panel_priority={panel_priority}")

        # Write initial header to log file immediately
        if dbg_path:
            try:
                with open(dbg_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(debug) + "\n")
                    f.flush()
            except Exception as write_err:
                dbg_path = None

        new_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        new_design = adsk.fusion.Design.cast(new_doc.products.itemByProductType('DesignProductType'))
        if not new_design:
            if ui:
                ui.messageBox("Failed to create new Fusion design.")
            return {'ok': False}
        
        # Detect mode (do not force - STEP import determines final mode)
        is_param = _is_parametric(new_design)
        d(f"Design mode after creation: {'parametric' if is_param else 'direct'}")

        # camera copy (best-effort)
        try:
            vp = app.activeViewport
            vp.camera = source_camera
            vp.update()
        except:
            pass

        units = new_design.unitsManager
        root = new_design.rootComponent

        if not os.path.isfile(step_path):
            if ui:
                ui.messageBox("STEP not found:\n" + step_path)
            return {'ok': False}

        import_mgr = app.importManager
        step_opts = import_mgr.createSTEPImportOptions(step_path)
        import_mgr.importToTarget(step_opts, root)
        
        # Detect actual mode after STEP import
        is_param = _is_parametric(new_design)
        d(f"Design mode after STEP import: {'parametric' if is_param else 'direct'}")

        # pick a component with solids
        target_comp = root
        solids = _collect_solids(root)
        if not solids and root.allOccurrences.count > 0:
            # common case: STEP imports as an occurrence
            target_comp = root.allOccurrences.item(0).component
            solids = _collect_solids(target_comp)

        if not solids:
            if ui:
                ui.messageBox("No solids after STEP import.")
            return {'ok': False}

        source_body = solids[0]
        d(f"source_body={source_body.name}")

        # Capture depth: we treat as *panel thickness* (inward thicken distance)
        capture_cm = units.evaluateExpression(capture_expr, 'cm')  # Thicken wants ValueInput; units internal are cm :contentReference[oaicite:2]{index=2}
        if capture_cm <= 0:
            if ui:
                ui.messageBox("capture_expr must evaluate > 0")
            return {'ok': False}

        # Epsilon for outward-normal test: small fraction of bbox diagonal
        min_x, max_x, min_y, max_y, min_z, max_z = _get_bbox_extents_from_body(source_body)
        dx, dy, dz = (max_x - min_x), (max_y - min_y), (max_z - min_z)
        diag = max(1e-6, (dx*dx + dy*dy + dz*dz) ** 0.5)
        eps_cm = max(0.01, diag * 0.001)  # ~0.1% of diag, min 0.01 cm
        bbox_center = adsk.core.Point3D.create((min_x + max_x) / 2, (min_y + max_y) / 2, (min_z + max_z) / 2)
        d(f"bbox dx,dy,dz(cm)=({dx:.3f},{dy:.3f},{dz:.3f}) eps_cm={eps_cm:.4f}")

        # 1) Classify faces
        buckets = { 'TOP': [], 'REAR': [], 'LEFT': [], 'RIGHT': [] }
        skipped = 0

        for face in source_body.faces:
            try:
                # Heuristic: skip tiny faces (optional) – keep it conservative
                # if face.area < 1e-6: continue

                out_n = _get_outward_normal(source_body, face, bbox_center, eps_cm)
                cls = _classify_face_option_b(out_n, thr=0.65)

                if cls in buckets:
                    buckets[cls].append(face)
                else:
                    skipped += 1
            except:
                skipped += 1

        d(f"classified faces: TOP={len(buckets['TOP'])} REAR={len(buckets['REAR'])} LEFT={len(buckets['LEFT'])} RIGHT={len(buckets['RIGHT'])} skipped={skipped}")

        # 2) SLAB/BOOLEAN approach: Directly intersect source with half-space planes
        # Instead of creating box bodies, we'll use boolean operations with implicit half-spaces
        # For now, simplify: Copy source 4 times and try union/difference operations
        
        # Phase D: defer compute during batch operations
        try:
            root.isComputeDeferred = True
        except:
            pass
        
        temp_mgr = adsk.fusion.TemporaryBRepManager.get()
        base_features = target_comp.features.baseFeatures
        
        created = {}
        created_count = 0
        
        # For each panel, we'll create slabs using primitive box creation in current design,
        # then copy to temp manager for boolean operations
        order = [p.upper() for p in (panel_priority or ['TOP','LEFT','RIGHT','REAR'])]
        
        # Pre-calculate slab geometry
        margin_cm = max(dx, dy, dz) * 1.5
        slab_defs = {
            'TOP': (
                adsk.core.Point3D.create(min_x - margin_cm, min_y - margin_cm, (min_z + max_z) / 2),
                (max_x + margin_cm) - (min_x - margin_cm),
                (max_y + margin_cm) - (min_y - margin_cm),
                (max_z + margin_cm) - ((min_z + max_z) / 2)
            ),
            'REAR': (
                adsk.core.Point3D.create((min_x + max_x) / 2, min_y - margin_cm, min_z - margin_cm),
                (max_x + margin_cm) - ((min_x + max_x) / 2),
                (max_y + margin_cm) - (min_y - margin_cm),
                (max_z + margin_cm) - (min_z - margin_cm)
            ),
            'LEFT': (
                adsk.core.Point3D.create(min_x - margin_cm, min_y - margin_cm, min_z - margin_cm),
                (max_x + margin_cm) - (min_x - margin_cm),
                ((min_y + max_y) / 2) - (min_y - margin_cm),
                (max_z + margin_cm) - (min_z - margin_cm)
            ),
            'RIGHT': (
                adsk.core.Point3D.create(min_x - margin_cm, (min_y + max_y) / 2, min_z - margin_cm),
                (max_x + margin_cm) - (min_x - margin_cm),
                (max_y + margin_cm) - ((min_y + max_y) / 2),
                (max_z + margin_cm) - (min_z - margin_cm)
            ),
        }

        for pname in order:
            if pname not in slab_defs:
                continue
            
            try:
                d(f"{pname}: creating slab and intersecting...")
                
                origin, length_cm, width_cm, height_cm = slab_defs[pname]
                
                # Create OrientedBoundingBox3D for the slab
                pmin = origin
                pmax = adsk.core.Point3D.create(
                    origin.x + length_cm,
                    origin.y + width_cm,
                    origin.z + height_cm
                )
                
                # Calculate center and FULL dimensions (not halves)
                center_x = (pmin.x + pmax.x) / 2.0
                center_y = (pmin.y + pmax.y) / 2.0
                center_z = (pmin.z + pmax.z) / 2.0
                center = adsk.core.Point3D.create(center_x, center_y, center_z)
                
                full_length = pmax.x - pmin.x
                full_width = pmax.y - pmin.y
                full_height = pmax.z - pmin.z
                
                # Create direction vectors (X = length, Y = width, Z computed by right-hand rule)
                length_dir = adsk.core.Vector3D.create(1, 0, 0)
                width_dir = adsk.core.Vector3D.create(0, 1, 0)
                
                # Create OrientedBoundingBox3D: center, lengthDir, widthDir, length, width, height
                obb = adsk.core.OrientedBoundingBox3D.create(center, length_dir, width_dir, full_length, full_width, full_height)
                
                # Create slab box from OBB
                slab_box = temp_mgr.createBox(obb)
                
                if not slab_box:
                    d(f"{pname}: FAILED: failed to create slab box")
                    continue
                
                # Make a temp copy of the source solid (this will be mutated by booleanOperation)
                panel_temp = temp_mgr.copy(source_body)
                if panel_temp is None or (hasattr(panel_temp, "isValid") and not panel_temp.isValid):
                    d(f"{pname}: FAILED: temp_mgr.copy(source_body) produced invalid temp body")
                    continue
                
                # Boolean intersection:
                # IMPORTANT: booleanOperation returns None and mutates panel_temp in-place.
                temp_mgr.booleanOperation(
                    panel_temp,
                    slab_box,
                    adsk.fusion.BooleanTypes.IntersectionBooleanType
                )
                
                # After boolean, panel_temp is the result (or may become invalid if empty).
                if hasattr(panel_temp, "isValid") and not panel_temp.isValid:
                    d(f"{pname}: no intersection result (panel_temp invalid after boolean)")
                    continue
                
                d(f"{pname}: intersection successful, adding body to component")
                
                # Use helper to insert (handles both parametric and direct modes)
                panel_body = _add_temp_body_to_component(
                    design=new_design,
                    comp=target_comp,
                    temp_body=panel_temp,
                    name=f"PANEL_{pname}",
                    logger=d
                )
                
                created_count += 1
                created[pname] = panel_body
                d(f"{pname}: inserted {panel_body.name}")
                
            except Exception as panel_err:
                d(f"{pname}: FAILED: {panel_err}")
                d(traceback.format_exc())
                continue
        
        # Phase D: Resume compute
        try:
            root.isComputeDeferred = False
        except:
            pass

        # Hide the original source solid (optional)
        try:
            source_body.isVisible = False
        except:
            pass

        d(f"final created_count={created_count}")
        d("=== panelize_step_into_new_design END ===")

        # Write final debug log (append to what was already written during processing)
        if dbg_path:
            try:
                with open(dbg_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(debug) + "\n")
                    f.flush()
            except Exception as write_err:
                pass

        return {'ok': True, 'doc_name': new_doc.name, 'panel_count': created_count}

    except:
        if ui:
            ui.messageBox('panelizer_core failed:\n' + traceback.format_exc())
        return {'ok': False}
