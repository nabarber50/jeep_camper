import adsk.cam


def get_cam_product(app, ui, doc):
    # Ensure Manufacture (CAM) workspace is active so the CAM product exists.
    try:
        ws = ui.workspaces.itemById('CAMEnvironment')
        if ws and ui.activeWorkspace and ui.activeWorkspace.id != 'CAMEnvironment':
            ws.activate()
            adsk.doEvents()
    except:
        # If workspace activation fails, we still try to get the CAM product below.
        pass

    # Try activeProduct first (often works once Manufacture is active)
    cam = adsk.cam.CAM.cast(app.activeProduct)
    if cam:
        return cam

    # Fallback: try from document products
    try:
        cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
        if cam:
            return cam
    except:
        pass

    return None
