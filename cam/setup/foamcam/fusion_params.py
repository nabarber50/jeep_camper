# cam/setup/foamcam/fusion_params.py
def dump_setup_params(logger, setup, contains=("wcs", "origin", "box", "point", "stock")):
    try:
        params = setup.parameters
        logger.log("---- SETUP PARAM DUMP (filtered) ----")
        for i in range(params.count):
            p = params.item(i)
            try:
                name = p.name
            except:
                continue
            low = (name or "").lower()
            if any(k in low for k in contains):
                try:
                    val = ""
                    try:
                        val = str(p.expression)
                    except:
                        try:
                            val = str(p.value)
                        except:
                            val = "<?>"
                    logger.log(f"PARAM: {name} = {val}")
                except:
                    logger.log(f"PARAM: {name}")
        logger.log("---- END SETUP PARAM DUMP ----")
    except Exception as e:
        logger.log(f"dump_setup_params failed: {e}")


def set_param_expr_any(params, names, expr: str):
    for nm in names:
        try:
            p = params.itemByName(nm)
            if p:
                p.expression = expr
                return True, nm
        except:
            pass
    return False, None


def set_param_bool_any(params, names, value: bool):
    for nm in names:
        try:
            p = params.itemByName(nm)
            if p:
                try:
                    p.value = value
                except:
                    p.expression = 'true' if value else 'false'
                return True, nm
        except:
            pass
    return False, None


def get_param_expr_any(params, names):
    for nm in names:
        try:
            p = params.itemByName(nm)
            if p:
                return (p.expression or "").strip(), nm
        except:
            pass
    return None, None
