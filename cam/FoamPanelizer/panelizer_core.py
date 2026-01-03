import adsk.core, adsk.fusion, traceback, os

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

        # 2) Phase A-D: Batch surface creation, stitch, thicken
        # Phase D: defer compute during batch operations
        try:
            root.isComputeDeferred = True
        except:
            pass
        
        temp_mgr = adsk.fusion.TemporaryBRepManager.get()
        thickness_cm = float(capture_cm)
        stitch_feats = target_comp.features.stitchFeatures
        thicken_feats = target_comp.features.thickenFeatures
        base_features = target_comp.features.baseFeatures

        created = {}
        created_count = 0
        all_surface_bodies = []  # Track for bulk hide later

        # ensure order
        order = [p.upper() for p in (panel_priority or ['TOP','LEFT','RIGHT','REAR'])]

        for pname in order:
            if pname not in buckets:
                continue
            faces = buckets[pname]
            if not faces:
                d(f"{pname}: no faces found, skipping")
                continue

            d(f"{pname}: processing {len(faces)} faces (batch mode)...")
            
            # Phase A: Create ONE base feature for this entire panel
            base_feat = base_features.add()
            if not base_feat:
                d(f"{pname}: failed to create base feature")
                continue
            
            base_feat.startEdit()
            
            # Copy all face surfaces into this base feature
            panel_surfaces = []
            for idx, face in enumerate(faces):
                try:
                    face_brep = temp_mgr.copy(face)
                    if face_brep:
                        surf_body = target_comp.bRepBodies.add(face_brep, base_feat)
                        if surf_body:
                            surf_body.name = f"SURF_{pname}_{idx+1:03d}"
                            panel_surfaces.append(surf_body)
                        else:
                            continue
                    else:
                        continue
                except Exception as face_err:
                    pass
                
                # Progress log every 20 faces (less frequent to speed up)
                if (idx + 1) % 20 == 0:
                    d(f"{pname}: copied {idx+1}/{len(faces)} faces...")
            
            base_feat.finishEdit()
            
            if not panel_surfaces:
                d(f"{pname}: no surface bodies created")
                continue
            
            d(f"{pname}: created {len(panel_surfaces)} surface bodies, now stitching...")
            all_surface_bodies.extend(panel_surfaces)
            
            # Phase B: Stitch all surfaces into ONE quilt
            try:
                stitch_input = stitch_feats.createInput(
                    adsk.core.ObjectCollection.create()  # Empty for now
                )
                # Add all surface bodies to the stitch input
                for surf_body in panel_surfaces:
                    for surf_face in surf_body.faces:
                        stitch_input.faces.add(surf_face)
                
                stitch_feat = stitch_feats.add(stitch_input)
                
                if stitch_feat and stitch_feat.bodies and stitch_feat.bodies.count > 0:
                    stitched_body = stitch_feat.bodies.item(0)
                    d(f"{pname}: stitched into 1 body, now thickening...")
                    
                    # Phase B: Thicken the stitched surface ONCE
                    stitch_faces = adsk.core.ObjectCollection.create()
                    for sf in stitched_body.faces:
                        stitch_faces.add(sf)
                    
                    thickness_vi = adsk.core.ValueInput.createByReal(-thickness_cm)
                    t_in = thicken_feats.createInput(
                        stitch_faces,
                        thickness_vi,
                        False,
                        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                        True
                    )
                    
                    t_feat = thicken_feats.add(t_in)
                    
                    if t_feat and t_feat.bodies and t_feat.bodies.count > 0:
                        for i in range(t_feat.bodies.count):
                            b = t_feat.bodies.item(i)
                            if t_feat.bodies.count == 1:
                                b.name = f"PANEL_{pname}"
                            else:
                                b.name = f"PANEL_{pname}_{i+1:02d}"
                            b.isVisible = True
                            created_count += 1
                        created[pname] = t_feat
                        d(f"{pname}: created {t_feat.bodies.count} panel body(s) ✓")
                    else:
                        d(f"{pname}: thicken produced no bodies")
                else:
                    d(f"{pname}: stitch produced no bodies")
            except Exception as stitch_err:
                d(f"{pname}: stitch/thicken FAILED: {stitch_err}")
                d(traceback.format_exc())
        
        # Phase D: Resume compute and hide intermediate surfaces in bulk
        try:
            root.isComputeDeferred = False
        except:
            pass
        
        d("Hiding intermediate surface bodies...")
        for surf_body in all_surface_bodies:
            try:
                surf_body.isVisible = False
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
