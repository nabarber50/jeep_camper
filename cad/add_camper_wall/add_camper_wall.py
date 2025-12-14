import adsk.core, adsk.fusion, traceback

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface
        design = app.activeProduct
        rootComp = design.rootComponent

        camper_wall_height = 1016  # mm (40 inches)
        roofBody = None

        # Get the roof body from the root level
        bodies = rootComp.bRepBodies
        if bodies.count == 0:
            ui.messageBox('❌ No bodies found in root. Run the roof script first.')
            return

        roofBody = bodies.item(0)

        # === STEP 1: Find bottom face of roof body to trace edge ===
        bottomFace = None
        minZ = float('inf')
        for face in roofBody.faces:
            faceZ = face.boundingBox.minPoint.z
            if faceZ < minZ:
                minZ = faceZ
                bottomFace = face

        if not bottomFace:
            ui.messageBox('❌ Could not find bottom face.')
            return

        # === STEP 2: Sketch base perimeter of roof on bottom face ===
        baseSketch = rootComp.sketches.add(bottomFace)
        baseSketch.name = 'Camper Base Perimeter'
        for edge in bottomFace.edges:
            baseSketch.project(edge)

        if baseSketch.profiles.count == 0:
            ui.messageBox('❌ No closed base profile found.')
            return

        baseProfile = baseSketch.profiles.item(0)

        # === STEP 3: Create offset plane at camper wall height ===
        topPlaneInput = rootComp.constructionPlanes.createInput()
        topPlaneInput.setByOffset(rootComp.xYConstructionPlane, adsk.core.ValueInput.createByReal(camper_wall_height))
        topPlane = rootComp.constructionPlanes.add(topPlaneInput)

        # === STEP 4: Create top sketch by copying projected base perimeter ===
        topSketch = rootComp.sketches.add(topPlane)
        topSketch.name = 'Camper Wall Top Perimeter'

        for curve in baseSketch.sketchCurves:
            if curve.geometry:
                geom = curve.geometry
                topSketch.sketchCurves.sketchLines.addByTwoPoints(
                    adsk.core.Point3D.create(geom.startPoint.x, geom.startPoint.y, 0),
                    adsk.core.Point3D.create(geom.endPoint.x, geom.endPoint.y, 0)
                )

        if topSketch.profiles.count == 0:
            ui.messageBox('❌ No profile formed at top sketch.')
            return

        # === STEP 5: Loft camper wall ===
        loftFeats = rootComp.features.loftFeatures
        loftInput = loftFeats.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        loftInput.loftSections.add(baseProfile)
        loftInput.loftSections.add(topSketch.profiles.item(0))
        loftInput.isSolid = True

        camperWallBody = loftFeats.add(loftInput).bodies.item(0)
        camperWallBody.name = 'Camper Pop-Up Wall'

        ui.messageBox('✅ Camper wall created using traced perimeter!')

    except Exception as e:
        if ui:
            ui.messageBox('❌ Script failed:\n{}'.format(traceback.format_exc()))
