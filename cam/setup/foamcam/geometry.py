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
