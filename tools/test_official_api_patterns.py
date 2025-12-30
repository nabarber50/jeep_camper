"""
Test official API patterns from Autodesk sample code.
Tests both tool library access AND contour geometry selection.
"""

import adsk.core, adsk.fusion, adsk.cam
import traceback

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = app.activeProduct
        
        if not design or design.productType != 'DesignProductType':
            ui.messageBox("Please open a design with a component before running this test")
            return
        
        # Switch to CAM workspace
        camWS = ui.workspaces.itemById('CAMEnvironment')
        camWS.activate()
        
        # Get CAM product
        doc = app.activeDocument
        products = doc.products
        cam = products.itemByProductType("CAMProductType")
        
        if not cam:
            ui.messageBox("CAM product not available")
            return
        
        #################### TEST 1: Tool Library Access ####################
        ui.messageBox("TEST 1: Accessing tool libraries via CAMManager.get()")
        
        # Use the official pattern from sample code
        camManager = adsk.cam.CAMManager.get()
        
        if camManager is None:
            ui.messageBox("FAILED: CAMManager.get() returned None")
            return
        
        ui.messageBox(f"✓ CAMManager.get() SUCCESS\nType: {type(camManager)}")
        
        # Access library manager
        libraryManager = camManager.libraryManager
        
        if libraryManager is None:
            ui.messageBox("FAILED: libraryManager is None")
            return
            
        ui.messageBox(f"✓ libraryManager SUCCESS\nType: {type(libraryManager)}")
        
        # Access tool libraries
        toolLibraries = libraryManager.toolLibraries
        
        if toolLibraries is None:
            ui.messageBox("FAILED: toolLibraries is None")
            return
            
        ui.messageBox(f"✓ toolLibraries SUCCESS\nType: {type(toolLibraries)}\nCount: {toolLibraries.count}")
        
        # Try loading a sample library
        MILLING_TOOL_LIBRARY_URL = adsk.core.URL.create('systemlibraryroot://Samples/Milling Tools (Metric).json')
        millingToolLibrary = toolLibraries.toolLibraryAtURL(MILLING_TOOL_LIBRARY_URL)
        
        if millingToolLibrary is None:
            ui.messageBox("FAILED: Could not load milling tool library")
            return
            
        ui.messageBox(f"✓ Tool library loaded!\nName: {millingToolLibrary.name}\nURL: {millingToolLibrary.url}")
        
        # Test tool query
        query = millingToolLibrary.createQuery()
        query.criteria.add('tool_type', adsk.core.ValueInput.createByString('flat end mill'))
        query.criteria.add('tool_diameter.min', adsk.core.ValueInput.createByReal(0.3))
        query.criteria.add('tool_diameter.max', adsk.core.ValueInput.createByReal(0.6))
        
        results = query.execute()
        
        if results is None or len(results) == 0:
            ui.messageBox("Tool query returned no results (this might be expected)")
        else:
            tool_names = [result.tool.description for result in results[:3]]
            ui.messageBox(f"✓ Tool query SUCCESS\nFound {len(results)} tools\nFirst 3:\n" + "\n".join(tool_names))
        
        #################### TEST 2: Contour Selection Pattern ####################
        ui.messageBox("TEST 2: Testing contour geometry selection pattern")
        
        # Get first body from design
        rootComp = design.rootComponent
        if rootComp.bRepBodies.count == 0:
            ui.messageBox("No bodies in design. Create a simple body to test contour selection.")
            return
        
        body = rootComp.bRepBodies.item(0)
        
        # Create a simple setup
        setups = cam.setups
        setupInput = setups.createInput(adsk.cam.OperationTypes.MillingOperation)
        setupInput.models = [body]
        setupInput.name = "Test Setup"
        setup = setups.add(setupInput)
        
        # Create a 2D contour operation input
        opInput = setup.operations.createInput('contour2d')
        opInput.displayName = "Test Contour"
        
        # Get a tool (if we have one from query)
        if results and len(results) > 0:
            opInput.tool = results[0].tool
        
        # TEST THE OFFICIAL PATTERN: Access .value property!
        contoursParam = opInput.parameters.itemByName('contours')
        
        if contoursParam is None:
            ui.messageBox("FAILED: Could not find 'contours' parameter")
            return
        
        ui.messageBox(f"✓ Found contours parameter\nType: {type(contoursParam)}")
        
        # Access the VALUE property (this is what we were missing!)
        contoursParamValue = contoursParam.value
        
        if contoursParamValue is None:
            ui.messageBox("FAILED: contours parameter .value is None")
            return
            
        ui.messageBox(f"✓ Got parameter VALUE\nType: {type(contoursParamValue)}")
        
        # Get curve selections (the official pattern)
        chains = contoursParamValue.getCurveSelections()
        
        if chains is None:
            ui.messageBox("FAILED: getCurveSelections() returned None")
            return
            
        ui.messageBox(f"✓ Got CurveSelections object\nType: {type(chains)}")
        
        # Try to create a new chain selection
        chain = chains.createNewChainSelection()
        
        if chain is None:
            ui.messageBox("FAILED: Could not create new chain selection")
            return
            
        ui.messageBox(f"✓ Created ChainSelection\nType: {type(chain)}")
        
        # Try to set geometry (using an edge from the body)
        if body.edges.count > 0:
            edge = body.edges.item(0)
            chain.inputGeometry = [edge]
            
            # Apply the selections back (the final step!)
            contoursParamValue.applyCurveSelections(chains)
            
            ui.messageBox("✓ Successfully set geometry and applied selections!\nPattern works!")
            
            # Add the operation to verify it worked
            op = setup.operations.add(opInput)
            ui.messageBox(f"✓ Operation created successfully!\nName: {op.name}")
        else:
            ui.messageBox("No edges available to test geometry assignment")
        
        ui.messageBox("=== ALL TESTS PASSED ===\nBoth tool selection AND contour selection are working!")
        
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
