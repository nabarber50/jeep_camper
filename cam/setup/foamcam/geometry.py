# cam/setup/foamcam/geometry.py
import math
import adsk.core, adsk.fusion

CM_TO_MM = 10.0
MM_TO_CM = 0.1

def resolve_native(src):
    try:
        if hasattr(src, "nativeObject") and src.nativeObject:
            return src.nativeObject
    except:
        pass
    return src

def bbox_mm(body: adsk.fusion.BRepBody):
    bb = body.boundingBox
    return (
        bb.minPoint.x * CM_TO_MM, bb.minPoint.y * CM_TO_MM, bb.minPoint.z * CM_TO_MM,
        bb.maxPoint.x * CM_TO_MM, bb.maxPoint.y * CM_TO_MM, bb.maxPoint.z * CM_TO_MM
    )

def union_bbox_mm(bodies):
    x0 = y0 = z0 =  1e99
    x1 = y1 = z1 = -1e99
    any_body = False
    for b in bodies or []:
        try:
            n = resolve_native(b)
            bx0, by0, bz0, bx1, by1, bz1 = bbox_mm(n)
            x0 = min(x0, bx0); y0 = min(y0, by0); z0 = min(z0, bz0)
            x1 = max(x1, bx1); y1 = max(y1, by1); z1 = max(z1, bz1)
            any_body = True
        except:
            pass
    if not any_body:
        return None
    return (x0, y0, z0, x1, y1, z1)

def model_xy_extents_mm(model_bodies):
    bb = union_bbox_mm(model_bodies)
    if not bb:
        return None
    x0,y0,_z0,x1,y1,_z1 = bb
    return (abs(x1-x0), abs(y1-y0))


def tmp_copy_rotate_flatten_measure_xy_mm(src_body, rot_90: bool):
    """
    Temp copy -> optional rotate 90° about Z -> translate so minZ==0 -> measure XY.
    Returns (w_mm, h_mm) or None.
    """
    src = resolve_native(src_body)
    try:
        temp_mgr = adsk.fusion.TemporaryBRepManager.get()
        tmp = temp_mgr.copy(src)
        if not tmp:
            return None

        if rot_90:
            bb = tmp.boundingBox  # cm
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
                    raise RuntimeError('DEBUG_FAIL_ON_ROTATION triggered in geometry.tmp_copy_rotate_flatten_measure_xy_mm')
            except Exception:
                pass

            if not temp_mgr.transform(tmp, R):
                return None

        bb = tmp.boundingBox
        minz = bb.minPoint.z
        Tz = adsk.core.Matrix3D.create()
        Tz.translation = adsk.core.Vector3D.create(0.0, 0.0, -minz)
        if not temp_mgr.transform(tmp, Tz):
            return None

        bb2 = tmp.boundingBox
        w_mm = abs(bb2.maxPoint.x - bb2.minPoint.x) * CM_TO_MM
        h_mm = abs(bb2.maxPoint.y - bb2.minPoint.y) * CM_TO_MM
        return (w_mm, h_mm)
    except:
        return None


def move_translate_only(comp: adsk.fusion.Component, body: adsk.fusion.BRepBody,
                        tx_mm: float, ty_mm: float, tz_mm: float):
    dx = tx_mm * MM_TO_CM
    dy = ty_mm * MM_TO_CM
    dz = tz_mm * MM_TO_CM

    mv_feats = comp.features.moveFeatures
    objs = adsk.core.ObjectCollection.create()
    objs.add(body)
    xform = adsk.core.Matrix3D.create()
    xform.translation = adsk.core.Vector3D.create(dx, dy, dz)
    inp = mv_feats.createInput(objs, xform)
    mv_feats.add(inp)


def detect_internal_voids(body: adsk.fusion.BRepBody):
    """Detect internal voids (holes/cutouts) in a body by analyzing face loops.
    
    Returns:
        List of void info dicts: [{'bbox_mm': (x0,y0,x1,y1), 'area_mm2': float}, ...]
    """
    voids = []
    try:
        # Look at top face (typically Z-max face for flat parts)
        top_face = None
        max_z = -1e99
        
        for face in body.faces:
            try:
                bbox = face.boundingBox
                face_z = (bbox.minPoint.z + bbox.maxPoint.z) * 0.5 * CM_TO_MM
                if face_z > max_z:
                    max_z = face_z
                    top_face = face
            except:
                continue
        
        if not top_face:
            return voids
        
        # Check for inner loops (holes/voids)
        for loop in top_face.loops:
            if not loop.isOuter:  # Inner loop = void
                try:
                    # Get bounding box of the void loop
                    min_x = min_y = 1e99
                    max_x = max_y = -1e99
                    
                    for edge in loop.edges:
                        edge_bbox = edge.boundingBox
                        min_x = min(min_x, edge_bbox.minPoint.x * CM_TO_MM)
                        min_y = min(min_y, edge_bbox.minPoint.y * CM_TO_MM)
                        max_x = max(max_x, edge_bbox.maxPoint.x * CM_TO_MM)
                        max_y = max(max_y, edge_bbox.maxPoint.y * CM_TO_MM)
                    
                    void_w = abs(max_x - min_x)
                    void_h = abs(max_y - min_y)
                    void_area = void_w * void_h
                    
                    # Only consider significant voids (larger than minimum part size)
                    if void_w > 30.0 and void_h > 30.0:  # mm
                        voids.append({
                            'bbox_mm': (min_x, min_y, max_x, max_y),
                            'width_mm': void_w,
                            'height_mm': void_h,
                            'area_mm2': void_area
                        })
                except:
                    continue
    except:
        pass
    
    return voids


def can_fit_in_void(part_w_mm: float, part_h_mm: float, void_info: dict, margin_mm: float = 5.0) -> bool:
    """Check if a part can fit inside a void with clearance margin."""
    void_w = void_info['width_mm']
    void_h = void_info['height_mm']
    
    # Try both orientations
    fits_normal = (part_w_mm + 2*margin_mm <= void_w) and (part_h_mm + 2*margin_mm <= void_h)
    fits_rotated = (part_h_mm + 2*margin_mm <= void_w) and (part_w_mm + 2*margin_mm <= void_h)
    
    return fits_normal or fits_rotated
