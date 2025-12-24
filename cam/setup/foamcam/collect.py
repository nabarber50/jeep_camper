# cam/setup/foamcam/collect.py
from foamcam.models import CollectorDiagnostics
from foamcam.geometry import resolve_native


def collect_layout_bodies(design, Config, logger):
    """
    Collect solids from root + all occurrences.
    If USE_VISIBLE_BODIES_ONLY=True, still includes bodies whose name contains 'Layer_'.
    Proxy-aware dedupe.
    """
    r = design.rootComponent
    diag = CollectorDiagnostics()
    included = []

    def record_excluded(b, reason: str, where: str):
        nm = "(unnamed)"
        try:
            nm = (getattr(b, "name", "") or nm)
        except:
            pass
        if len(diag.excluded_samples) < 60:
            diag.excluded_samples.append((nm, reason, where))

    def want_body(b, where: str) -> bool:
        diag.seen_total += 1
        if where == "root":
            diag.seen_root += 1
        else:
            diag.seen_occ += 1

        if not b:
            diag.non_brep_or_null += 1
            record_excluded(b, "null body", where)
            return False

        try:
            if not hasattr(b, "isSolid"):
                diag.non_brep_or_null += 1
                record_excluded(b, "not a BRepBody", where)
                return False
        except:
            diag.non_brep_or_null += 1
            record_excluded(b, "not a BRepBody", where)
            return False

        try:
            if not b.isSolid:
                diag.not_solid += 1
                record_excluded(b, "not solid (surface body)", where)
                return False
        except:
            diag.not_solid += 1
            record_excluded(b, "isSolid check failed", where)
            return False

        nm = (getattr(b, "name", "") or "")
        is_layer_named = ("layer_" in nm.lower())  # broadened (contains)

        if Config.USE_VISIBLE_BODIES_ONLY:
            try:
                if (not b.isVisible) and (not is_layer_named):
                    diag.filtered_visibility += 1
                    record_excluded(b, "hidden (and not Layer_*)", where)
                    return False
            except:
                pass

        return True

    # root bodies
    try:
        for b in r.bRepBodies:
            if want_body(b, "root"):
                included.append(b)
    except:
        pass

    # occurrence bodies (proxies)
    try:
        for occ in r.allOccurrences:
            comp = occ.component
            if not comp:
                continue
            for b in comp.bRepBodies:
                if not want_body(b, "occ"):
                    continue
                try:
                    included.append(b.createForAssemblyContext(occ))
                except:
                    included.append(b)
    except:
        pass

    # proxy-aware dedupe:
    dedup = []
    seen = set()
    for b in included:
        try:
            is_proxy = (hasattr(b, "assemblyContext") and b.assemblyContext is not None)
        except:
            is_proxy = False

        if is_proxy:
            k = ("proxy", id(b))
        else:
            try:
                k = ("native", id(resolve_native(b)))
            except:
                k = ("native", id(b))

        if k in seen:
            diag.deduped_out += 1
            continue
        seen.add(k)
        dedup.append(b)

    diag.included = len(dedup)
    return dedup, diag
