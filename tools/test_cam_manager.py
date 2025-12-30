"""
Test CAMManager access via official SDK documentation approach.
Tests CAMManager.get() static method and libraryManager property.
"""

import adsk.core, adsk.fusion, adsk.cam
import traceback

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        
        # Test 1: Access CAMManager via documented static get() method
        ui.messageBox("Test 1: Accessing CAMManager.get()")
        cam_manager = adsk.cam.CAMManager.get()
        
        if cam_manager is None:
            ui.messageBox("CAMManager.get() returned None")
            return
        
        ui.messageBox(f"CAMManager.get() SUCCESS!\nType: {type(cam_manager)}\nisValid: {cam_manager.isValid}")
        
        # Test 2: Access libraryManager property
        ui.messageBox("Test 2: Accessing libraryManager property")
        lib_manager = cam_manager.libraryManager
        
        if lib_manager is None:
            ui.messageBox("libraryManager returned None")
            return
            
        ui.messageBox(f"libraryManager SUCCESS!\nType: {type(lib_manager)}\nisValid: {lib_manager.isValid}")
        
        # Test 3: Explore libraryManager attributes
        ui.messageBox("Test 3: Exploring libraryManager attributes")
        attrs = [attr for attr in dir(lib_manager) if not attr.startswith('_')]
        ui.messageBox(f"libraryManager attributes ({len(attrs)}):\n" + "\n".join(attrs[:20]))
        
        # Test 4: Look for tool library access
        ui.messageBox("Test 4: Looking for tool library properties")
        tool_attrs = [attr for attr in attrs if 'tool' in attr.lower() or 'library' in attr.lower()]
        ui.messageBox(f"Tool/Library related attributes:\n" + "\n".join(tool_attrs))
        
        # Test 5: Try accessing specific library properties
        results = []
        for attr in ['toolLibraries', 'toolLibrary', 'libraries', 'postLibrary', 'machineLibrary']:
            try:
                val = getattr(lib_manager, attr, "NOT_FOUND")
                if val != "NOT_FOUND":
                    results.append(f"{attr}: {type(val)} (isValid: {getattr(val, 'isValid', 'N/A')})")
                else:
                    results.append(f"{attr}: NOT_FOUND")
            except:
                results.append(f"{attr}: ERROR")
        
        ui.messageBox("Library property test results:\n" + "\n".join(results))
        
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
