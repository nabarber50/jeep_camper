
import adsk.core, adsk.fusion, adsk.cam, traceback

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        rootComp = design.rootComponent

        # Parameters
        jack_height = 1016.0  # mm
        jack_base_offset = 50.0  # mm from each long side
        jack_depth = 10.0
        jack_width = 15.0
        rod_radius = 5.0
        rod_length = 200.0

        # Get the bounding box of the body to position jacks
        camperBody = None
        for body in rootComp.bRepBodies:
            if body.isSolid:
                camperBody = body
                break
        if not camperBody:
            ui.messageBox('No solid body found in the root component.')
            return

        bbox = camperBody.boundingBox
        center_x = (bbox.minPoint.x + bbox.maxPoint.x) / 2
        y_min = bbox.minPoint.y
        y_max = bbox.maxPoint.y
        z_base = bbox.maxPoint.z  # Roof edge

        sketches = rootComp.sketches
        xyPlane = rootComp.xYConstructionPlane

        # Function to create a scissor jack on one side
        def createJack(sideY):
            sketch = sketches.add(xyPlane)
            lines = sketch.sketchCurves.sketchLines

            # Base point
            base_y = sideY
            base_x = center_x - 20
            top_x = center_x + 20
            base_z = z_base
            top_z = z_base + jack_height

            # X shape
            lines.addByTwoPoints(adsk.core.Point3D.create(base_x, base_y, base_z),
                                 adsk.core.Point3D.create(top_x, base_y, top_z))
            lines.addByTwoPoints(adsk.core.Point3D.create(top_x, base_y, base_z),
                                 adsk.core.Point3D.create(base_x, base_y, top_z))

            # Rod
            sketchCircles = sketch.sketchCurves.sketchCircles
            sketchCircles.addByCenterRadius(adsk.core.Point3D.create(center_x, base_y, z_base + jack_height / 2), rod_radius)

        # Create left and right jacks
        createJack(y_min + jack_base_offset)
        createJack(y_max - jack_base_offset)

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
