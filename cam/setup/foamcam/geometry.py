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
                    from common.config import Config
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


def detect_internal_voids(body: adsk.fusion.BRepBody, logger=None):
    """Detect internal voids (holes/cutouts) in a body by analyzing face loops.
    
    Args:
        body: The BRepBody to analyze
        logger: Optional logger instance for diagnostics
    
    Returns:
        List of void info dicts: [{'bbox_mm': (x0,y0,x1,y1), 'area_mm2': float}, ...]
    """
    voids = []
    try:
        body_name = body.name if hasattr(body, 'name') else 'unknown'

        # Identify candidate faces: top (max Z) and bottom (min Z)
        top_face = None
        bottom_face = None
        max_z = -1e99
        min_z = 1e99
        top_area = 0.0
        bottom_area = 0.0
        face_count = 0

        for face in body.faces:
            face_count += 1
            try:
                bbox = face.boundingBox
                face_z = (bbox.minPoint.z + bbox.maxPoint.z) * 0.5 * CM_TO_MM
                face_area = getattr(face, 'area', None)

                if face_z > max_z:
                    max_z = face_z
                    top_face = face
                    top_area = face_area if face_area is not None else 0.0
                if face_z < min_z:
                    min_z = face_z
                    bottom_face = face
                    bottom_area = face_area if face_area is not None else 0.0
            except:
                continue

        if not top_face:
            if logger:
                logger.log(f"  ⚠️  {body_name}: No top face found (faces={face_count})")
            return voids

        if logger:
            outer_loops = sum(1 for loop in top_face.loops if loop.isOuter)
            inner_loops = sum(1 for loop in top_face.loops if not loop.isOuter)
            logger.log(
                f"  🔍 {body_name}: Using top face z={max_z:.1f}mm area={top_area:.1f} (outer_loops={outer_loops}, inner_loops={inner_loops})"
            )
            if bottom_face:
                bottom_outer = sum(1 for loop in bottom_face.loops if loop.isOuter)
                bottom_inner = sum(1 for loop in bottom_face.loops if not loop.isOuter)
                logger.log(
                    f"     Bottom face z={min_z:.1f}mm area={bottom_area:.1f} (outer_loops={bottom_outer}, inner_loops={bottom_inner})"
                )

        loop_idx = 0
        for loop in top_face.loops:
            loop_idx += 1
            if not loop.isOuter:  # Inner loop = void
                try:
                    min_x = min_y = 1e99
                    max_x = max_y = -1e99

                    edge_count = 0
                    vertex_count = 0
                    curve_sample_count = 0
                    edge_types = []

                    for edge in loop.edges:
                        try:
                            edge_count += 1
                            edge_type = edge.geometry.curveType if hasattr(edge.geometry, 'curveType') else 'unknown'
                            edge_types.append(edge_type)

                            v1 = edge.startVertex
                            if v1 and v1.geometry:
                                vertex_count += 1
                                x = v1.geometry.x * CM_TO_MM
                                y = v1.geometry.y * CM_TO_MM
                                min_x = min(min_x, x)
                                max_x = max(max_x, x)
                                min_y = min(min_y, y)
                                max_y = max(max_y, y)

                            v2 = edge.endVertex
                            if v2 and v2.geometry:
                                vertex_count += 1
                                x = v2.geometry.x * CM_TO_MM
                                y = v2.geometry.y * CM_TO_MM
                                min_x = min(min_x, x)
                                max_x = max(max_x, x)
                                min_y = min(min_y, y)
                                max_y = max(max_y, y)

                            if hasattr(edge.geometry, 'evaluator'):
                                try:
                                    evaluator = edge.geometry.evaluator
                                    success, start_param, end_param = evaluator.getParameterExtents()
                                    if success:
                                        for i in range(1, 20):  # 19 intermediate samples
                                            t = start_param + (end_param - start_param) * (i / 20.0)
                                            success, point = evaluator.getPointAtParameter(t)
                                            if success and point:
                                                curve_sample_count += 1
                                                x = point.x * CM_TO_MM
                                                y = point.y * CM_TO_MM
                                                min_x = min(min_x, x)
                                                max_x = max(max_x, x)
                                                min_y = min(min_y, y)
                                                max_y = max(max_y, y)
                                except:
                                    pass
                        except:
                            continue

                    void_w_raw = abs(max_x - min_x)
                    void_h_raw = abs(max_y - min_y)

                    if logger:
                        logger.log(f"  🔍 VOID DETECTION: loop={loop_idx} edges={edge_count} vertices={vertex_count} curve_samples={curve_sample_count}")
                        logger.log(f"     Edge types: {', '.join(set(str(t) for t in edge_types))}")
                        logger.log(f"     Raw bbox: x=[{min_x:.1f}, {max_x:.1f}] y=[{min_y:.1f}, {max_y:.1f}]")
                        logger.log(f"     Raw dimensions: {void_w_raw:.1f} × {void_h_raw:.1f} mm")

                    from common.config import Config
                    buffer = getattr(Config, 'VOID_MEASUREMENT_BUFFER', 100.0)
                    void_w = void_w_raw + buffer
                    void_h = void_h_raw + buffer
                    void_area = void_w * void_h

                    if logger:
                        logger.log(f"     Buffer={buffer:.1f}mm → Final: {void_w:.1f} × {void_h:.1f} mm (area={void_area:.0f}mm²)")

                    if void_w_raw > 30.0 and void_h_raw > 30.0:
                        voids.append({
                            'bbox_mm': (min_x, min_y, max_x, max_y),
                            'width_mm': void_w,
                            'height_mm': void_h,
                            'area_mm2': void_area,
                            'raw_width_mm': void_w_raw,
                            'raw_height_mm': void_h_raw,
                            'edge_count': edge_count,
                            'vertex_count': vertex_count,
                            'curve_sample_count': curve_sample_count
                        })
                        if logger:
                            logger.log(f"     ✅ Void accepted (raw > 30mm threshold)")
                    else:
                        if logger:
                            logger.log(f"     ❌ Void rejected (raw {void_w_raw:.1f}×{void_h_raw:.1f} below 30mm threshold)")
                except Exception as e:
                    if logger:
                        logger.log(f"  ⚠️  Void detection error: {e}")
                    continue
    except:
        pass
    
    return voids


def can_fit_in_void(part_w_mm: float, part_h_mm: float, void_info: dict, margin_mm: float = 5.0) -> bool:
    """Check if a part can fit inside a void with clearance margin.
    
    For foam nesting: Parts can be slightly oversized if within tolerance (~3mm).
    Margin interpretation: 
      - Positive: require clearance (part + 2*margin <= void)
      - Negative: allow overage (part <= void + 2*abs(margin))
    
    Note: Only checks the given orientation. Rotation should be handled by the caller.
    """
    void_w = void_info.get('width_mm')
    void_h = void_info.get('height_mm')
    
    # Safety check: if void_w/void_h are None, default to buffered dimensions if available
    if void_w is None:
        void_w = void_info.get('w_mm', 0)
    if void_h is None:
        void_h = void_info.get('h_mm', 0)
    
    if margin_mm >= 0:
        # Standard: require clearance margin
        fits = (part_w_mm + 2*margin_mm <= void_w) and (part_h_mm + 2*margin_mm <= void_h)
    else:
        # Negative margin: allow parts up to abs(margin) larger than void
        tolerance = abs(margin_mm)
        fits = (part_w_mm <= void_w + 2*tolerance) and (part_h_mm <= void_h + 2*tolerance)
    
    return fits
