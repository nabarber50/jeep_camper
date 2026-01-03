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


def _get_outward_normal(body: adsk.fusion.BRepBody, face: adsk.fusion.BRepFace, eps_cm: float) -> adsk.core.Vector3D:
    """
    Returns an *outward* unit normal for an exterior face by sampling a point and
    checking point containment of a small step along the normal.
    """
    p = face.pointOnFace
    ok, n = face.evaluator.getNormalAtPoint(p)
    if not ok:
        # fallback: arbitrary up
        return _v(0, 0, 1)

    n.normalize()

    # If stepping along n goes INSIDE the body, the normal is inward -> flip.
    p_out = _scaled_point(p, n, eps_cm)

    try:
        rel = body.pointContainment(p_out)  # returns PointContainment enum :contentReference[oaicite:1]{index=1}
        # Inside means we stepped inward, so flip to get outward.
        if rel == adsk.fusion.PointContainment.PointInsidePointContainment:
            n.scaleBy(-1.0)
    except:
        # If containment fails, keep the sampled normal
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
        d(f"bbox dx,dy,dz(cm)=({dx:.3f},{dy:.3f},{dz:.3f}) eps_cm={eps_cm:.4f}")

        # 1) Classify faces
        buckets = { 'TOP': [], 'REAR': [], 'LEFT': [], 'RIGHT': [] }
        skipped = 0

        for face in source_body.faces:
            try:
                # Heuristic: skip tiny faces (optional) – keep it conservative
                # if face.area < 1e-6: continue

                out_n = _get_outward_normal(source_body, face, eps_cm)
                cls = _classify_face_option_b(out_n, thr=0.65)

                if cls in buckets:
                    buckets[cls].append(face)
                else:
                    skipped += 1
            except:
                skipped += 1

        d(f"classified faces: TOP={len(buckets['TOP'])} REAR={len(buckets['REAR'])} LEFT={len(buckets['LEFT'])} RIGHT={len(buckets['RIGHT'])} skipped={skipped}")

        # 2) Extract faces as surface bodies, then thicken to create solid panels
        # Fusion API limitation: thickenFeatures cannot work on faces from solid bodies
        # Solution: Copy each face as BRep surface, add as base feature, then thicken
        # Note: Skip stitching (too slow with hundreds of faces), create multiple bodies per panel
        
        temp_mgr = adsk.fusion.TemporaryBRepManager.get()
        thickness_cm = float(capture_cm)
        thicken_feats = target_comp.features.thickenFeatures
        base_features = target_comp.features.baseFeatures

        created = {}
        created_count = 0

        # ensure order
        order = [p.upper() for p in (panel_priority or ['TOP','LEFT','RIGHT','REAR'])]

        for pname in order:
            if pname not in buckets:
                continue
            faces = buckets[pname]
            if not faces:
                d(f"{pname}: no faces found, skipping")
                continue

            d(f"{pname}: processing {len(faces)} faces (creating individual surface bodies)...")
            
            panel_bodies = []
            batch_size = 10  # Process 10 faces per batch for progress updates
            
            # Process faces in batches to avoid hanging on huge unions
            # Create individual surface bodies and thicken each
            for idx, face in enumerate(faces):
                try:
                    # Copy face as BRep surface
                    face_brep = temp_mgr.copy(face)
                    if not face_brep:
                        continue
                    
                    # Add as base feature body
                    base_feat = base_features.add()
                    if not base_feat:
                        continue
                    
                    base_feat.startEdit()
                    surf_body = target_comp.bRepBodies.add(face_brep, base_feat)
                    if not surf_body:
                        base_feat.finishEdit()
                        continue
                    
                    surf_body.name = f"SURF_{pname}_{idx+1:03d}"
                    base_feat.finishEdit()
                    
                    # Thicken this surface
                    surf_faces_col = adsk.core.ObjectCollection.create()
                    for sf in surf_body.faces:
                        surf_faces_col.add(sf)
                    
                    thickness_vi = adsk.core.ValueInput.createByReal(-thickness_cm)
                    t_in = thicken_feats.createInput(
                        surf_faces_col,
                        thickness_vi,
                        False,
                        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                        True
                    )
                    
                    t_feat = thicken_feats.add(t_in)
                    
                    if t_feat and t_feat.bodies and t_feat.bodies.count > 0:
                        for i in range(t_feat.bodies.count):
                            b = t_feat.bodies.item(i)
                            b.name = f"PANEL_{pname}_{idx+1:03d}"
                            b.isVisible = True
                            panel_bodies.append(b)
                            created_count += 1
                        
                        # Hide intermediate surface
                        try:
                            surf_body.isVisible = False
                        except:
                            pass
                    
                    # Progress logging every batch_size faces
                    if (idx + 1) % batch_size == 0:
                        msg = f"{pname}: processed {idx+1}/{len(faces)} faces..."
                        d(msg)
                        # Also try to write progress to file immediately for debugging
                        if dbg_path:
                            try:
                                with open(dbg_path, "a", encoding="utf-8") as f:
                                    f.write(msg + "\n")
                                    f.flush()
                            except:
                                pass
                        
                except Exception as face_err:
                    # Skip problematic faces, log error
                    d(f"{pname} face {idx+1}: {str(face_err)[:80]}")
            
            if panel_bodies:
                created[pname] = panel_bodies
                d(f"{pname}: created {len(panel_bodies)} panel body(s) from {len(faces)} faces")
            else:
                d(f"{pname}: no bodies created")

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
