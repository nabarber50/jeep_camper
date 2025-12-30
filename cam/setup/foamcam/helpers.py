import adsk.core
import adsk.cam
import os


def create_cam_selection_set(cam, bodies, set_name, logger):
    """Create a CAM selection set from model bodies' top faces.
    
    Selection sets can be used directly in contour selections for more reliable
    geometry assignment (per official Autodesk sample code).
    
    Args:
        cam: CAM product
        bodies: List of model bodies
        set_name: Name for the selection set
        logger: Logger instance
    
    Returns:
        SelectionSet object or None if failed
    """
    try:
        if not bodies or len(bodies) == 0:
            logger.log(f"Selection set: no bodies provided for '{set_name}'")
            return None
        
        # Collect all top faces from bodies
        faces = []
        for body in bodies:
            try:
                for face in body.faces:
                    try:
                        # Prefer larger faces (likely to be top surface)
                        if face.area > 0:
                            faces.append(face)
                    except:
                        pass
            except:
                pass
        
        if not faces:
            logger.log(f"Selection set: no faces found in bodies for '{set_name}'")
            return None
        
        # Create selection set
        try:
            selection_set = cam.selectionSets.add(faces, set_name)
            logger.log(f"Selection set: created '{set_name}' with {len(faces)} faces")
            return selection_set
        except Exception as e:
            logger.log(f"Selection set: failed to create '{set_name}': {e}")
            return None
        
    except Exception as e:
        logger.log(f"Selection set: exception - {e}")
        return None


def apply_selection_set_to_contours(operation_input, selection_set, logger):
    """Apply a CAM selection set to an operation's contour geometry.
    
    Uses the official Autodesk pattern from sample code:
    Get empty curveSelections -> create new face contour -> set inputGeometry from set -> apply
    
    Args:
        operation_input: Operation input object
        selection_set: SelectionSet object created by create_cam_selection_set()
        logger: Logger instance
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if not operation_input or not selection_set:
            logger.log("Contour set: operation_input or selection_set is None")
            return False
        
        # Get contours parameter
        params = operation_input.parameters
        contours_param = params.itemByName('contours')
        
        if not contours_param:
            logger.log("Contour set: 'contours' parameter not found")
            return False
        
        cadContours2dParam = contours_param.value
        if not cadContours2dParam:
            logger.log("Contour set: parameter value is None")
            return False
        
        # Get curve selections (empty collection)
        try:
            curveSelections = cadContours2dParam.getCurveSelections()
        except Exception as e:
            logger.log(f"Contour set: getCurveSelections() failed: {e}")
            return False
        
        if not curveSelections:
            logger.log("Contour set: getCurveSelections() returned None")
            return False
        
        # Create new face contour selection and assign geometry from selection set
        try:
            chain = curveSelections.createNewFaceContourSelection()
            if not chain:
                logger.log("Contour set: createNewFaceContourSelection() returned None")
                return False
            
            # Assign the selection set entities to the chain
            chain.inputGeometry = selection_set.entities
            
            # Apply back to parameter
            cadContours2dParam.applyCurveSelections(curveSelections)
            
            logger.log(f"Contour set: applied {selection_set.entities.count} entities to contours")
            return True
            
        except Exception as e:
            logger.log(f"Contour set: failed to assign geometry: {e}")
            return False
        
    except Exception as e:
        logger.log(f"Contour set: exception - {e}")
        return False


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


def get_post_configuration(post_name: str, vendor: str, operation_type, logger):
    """Find and import post processor configuration.
    
    Args:
        post_name: Description of post processor (e.g., 'grbl', 'RS-274D')
        vendor: Vendor filter (e.g., 'Autodesk')
        operation_type: OperationTypes enum (MillingOperation, TurningOperation, etc.)
        logger: Logger instance
    
    Returns:
        Tuple of (postConfig, ncExtension) or (None, None) if not found
    """
    try:
        camManager = adsk.cam.CAMManager.get()
        if not camManager:
            logger.log("Post config: CAMManager.get() returned None")
            return None, None
        
        libraryManager = camManager.libraryManager
        if not libraryManager:
            logger.log("Post config: libraryManager is None")
            return None, None
        
        postLibrary = libraryManager.postLibrary
        if not postLibrary:
            logger.log("Post config: postLibrary is None")
            return None, None
        
        # Create query for post processors
        postQuery = postLibrary.createQuery(adsk.cam.LibraryLocations.Fusion360LibraryLocation)
        if vendor:
            postQuery.vendor = vendor
        
        # Set capability based on operation type
        if operation_type == adsk.cam.OperationTypes.MillingOperation:
            postQuery.capability = adsk.cam.PostCapabilities.Milling
        elif operation_type == adsk.cam.OperationTypes.TurningOperation:
            postQuery.capability = adsk.cam.PostCapabilities.Turning
        elif operation_type == adsk.cam.OperationTypes.JetOperation:
            postQuery.capability = adsk.cam.PostCapabilities.Jet
        
        postConfigs = postQuery.execute()
        logger.log(f"Post config: found {len(postConfigs)} post processors")
        
        # Log all available post processors for debugging
        if len(postConfigs) > 0:
            logger.log(f"  Available post processors:")
            for i, config in enumerate(postConfigs):
                logger.log(f"    [{i+1}] '{config.description}' (vendor: {config.vendor})")
        
        # Find the requested post processor
        for config in postConfigs:
            if config.description.lower() == post_name.lower():
                logger.log(f"Post config: found '{config.description}'")
                
                # Import to user library
                url = adsk.core.URL.create("user://")
                import_name = f"FoamCAM_{config.description}.cps"
                try:
                    importedURL = postLibrary.importPostConfiguration(config, url, import_name)
                    postConfig = postLibrary.postConfigurationAtURL(importedURL)
                    logger.log(f"Post config: imported as '{import_name}'")
                    return postConfig, config.extension
                except Exception as e:
                    logger.log(f"Post config: import failed - {e}")
                    # Try to use existing imported config
                    try:
                        existing_url = adsk.core.URL.create(f"user://{import_name}")
                        postConfig = postLibrary.postConfigurationAtURL(existing_url)
                        if postConfig:
                            logger.log(f"Post config: using existing '{import_name}'")
                            return postConfig, config.extension
                    except:
                        pass
        
        logger.log(f"Post config: '{post_name}' not found")
        return None, None
        
    except Exception as e:
        logger.log(f"Post config: exception - {e}")
        return None, None


def create_nc_program(cam, setup, post_config, nc_extension, config, logger):
    """Create and configure NC program for a setup.
    
    Args:
        cam: CAM product
        setup: Setup to post-process
        post_config: Post processor configuration
        nc_extension: File extension (e.g., '.nc', '.gcode')
        config: Config object with post-processing settings
        logger: Logger instance
    
    Returns:
        NCProgram instance or None if failed
    """
    try:
        # Create NC program input
        ncInput = cam.ncPrograms.createInput()
        
        # Set output folder
        output_folder = getattr(config, 'NC_OUTPUT_FOLDER', None)
        if not output_folder:
            # Use Desktop as default
            output_folder = config.get_desktop_path()
        output_folder = str(output_folder).replace('\\', '/')
        
        # Configure NC program parameters
        program_name = getattr(config, 'NC_FILE_PREFIX', 'FoamCAM_') + setup.name
        ncInput.displayName = program_name
        
        # Set filename
        filename_param = ncInput.parameters.itemByName('nc_program_filename')
        if filename_param:
            filename_param.value.value = program_name
        
        # Set comment
        comment = getattr(config, 'POST_PROGRAM_COMMENT', 'Generated by FoamCAM')
        comment_param = ncInput.parameters.itemByName('nc_program_comment')
        if comment_param:
            comment_param.value.value = comment
        
        # Set open in editor
        open_in_editor = getattr(config, 'NC_OPEN_IN_EDITOR', False)
        editor_param = ncInput.parameters.itemByName('nc_program_openInEditor')
        if editor_param:
            editor_param.value.value = open_in_editor
        
        # Set output folder
        folder_param = ncInput.parameters.itemByName('nc_program_output_folder')
        if folder_param:
            folder_param.value.value = output_folder
        
        # Assign operations (just this setup)
        ncInput.operations = [setup]
        
        # Create the NC program
        newProgram = cam.ncPrograms.add(ncInput)
        
        # Assign post configuration
        newProgram.postConfiguration = post_config
        
        # Configure post parameters
        postParams = newProgram.postParameters
        
        # Tolerance
        tolerance = getattr(config, 'POST_TOLERANCE', 0.004)
        tolerance_param = postParams.itemByName('builtin_tolerance')
        if tolerance_param:
            tolerance_param.value.value = tolerance
        
        # Sequence numbers
        show_seq = getattr(config, 'POST_SHOW_SEQUENCE_NUMBERS', False)
        seq_param = postParams.itemByName('showSequenceNumbers')
        if seq_param:
            seq_param.value.value = str(show_seq).lower()
        
        # Update parameters
        newProgram.updatePostParameters(postParams)
        
        logger.log(f"NC program created: {program_name}")
        return newProgram
        
    except Exception as e:
        logger.log(f"NC program creation failed: {e}")
        return None


def post_process_setup(cam, setup, config, logger):
    """Post-process a single setup to generate NC file.
    
    Args:
        cam: CAM product
        setup: Setup to post-process
        config: Config object
        logger: Logger instance
    
    Returns:
        Path to generated NC file or None if failed
    """
    try:
        # Verify setup has operations
        if not setup.operations or setup.operations.count == 0:
            logger.log(f"Post-process: setup '{setup.name}' has no operations")
            return None
        
        logger.log(f"Post-process: starting for setup '{setup.name}' ({setup.operations.count} operations)")
        
        # Optional testing mode: only process first sheet
        if Config.GENERATE_NC_ONESHOT and not setup.name.endswith('_01_STD_4x8'):
            logger.log(f"  Skipping (GENERATE_NC_ONESHOT=True, testing first sheet only)")
            return None
        
        # Generate toolpaths for all operations in this setup
        logger.log(f"  Generating toolpaths...")
        try:
            operations_to_generate = adsk.core.ObjectCollection.create()
            for op in setup.operations:
                # Optional testing mode: only generate 2D Profile operations
                if Config.GENERATE_NC_ONESHOT:
                    op_name = op.name if hasattr(op, 'name') else str(op)
                    if 'Profile' in op_name or '2D' in op_name:
                        operations_to_generate.add(op)
                        logger.log(f"    Including operation: {op_name} (testing mode)")
                    else:
                        logger.log(f"    Skipping operation: {op_name} (testing mode)")
                else:
                    operations_to_generate.add(op)
            
            if operations_to_generate.count > 0:
                gtf = cam.generateToolpath(operations_to_generate)
                
                # Wait for generation to complete
                while not gtf.isGenerationCompleted:
                    adsk.doEvents()
                
                logger.log(f"  Toolpaths generated ({operations_to_generate.count} operations)")
            else:
                logger.log(f"  No operations to generate")
        except Exception as gen_error:
            logger.log(f"  Toolpath generation warning: {gen_error}")
            # Continue anyway - some operations may have generated
        
        # Get post processor configuration
        post_name = getattr(config, 'POST_PROCESSOR_NAME', 'grbl')
        vendor = getattr(config, 'POST_PROCESSOR_VENDOR', 'Autodesk')
        
        logger.log(f"  Post processor: '{post_name}' (vendor='{vendor}')")
        
        post_config, nc_extension = get_post_configuration(
            post_name, vendor, setup.operationType, logger
        )
        
        if not post_config:
            logger.log(f"Post-process: could not load post processor '{post_name}'")
            return None
        
        logger.log(f"  Extension: '{nc_extension}'")
        
        # Create NC program
        nc_program = create_nc_program(cam, setup, post_config, nc_extension, config, logger)
        if not nc_program:
            logger.log(f"Post-process: failed to create NC program")
            return None
        
        logger.log(f"  NC program created for setup: {setup.name}")
        
        # Post process to generate file
        try:
            post_options = adsk.cam.NCProgramPostProcessOptions.create()
            if not post_options:
                logger.log(f"  Warning: PostProcessOptions.create() returned None")
                post_options = adsk.cam.NCProgramPostProcessOptions()
            
            logger.log(f"  Executing post-processing...")
            nc_program.postProcess(post_options)
            logger.log(f"  Post-processing complete")
        except Exception as e:
            logger.log(f"  Post-processing failed: {e}")
            return None
        
        # Build output path
        output_folder = getattr(config, 'NC_OUTPUT_FOLDER', None)
        if not output_folder:
            output_folder = config.get_desktop_path()
        
        output_folder = str(output_folder).replace('\\', '/')
        program_name = getattr(config, 'NC_FILE_PREFIX', 'FoamCAM_') + setup.name
        output_path = os.path.join(output_folder, program_name + nc_extension)
        
        # Verify file was created
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            logger.log(f"Post-process SUCCESS: {output_path} ({file_size} bytes)")
            return output_path
        else:
            logger.log(f"Post-process: file was not created at {output_path}")
            return None
        
    except Exception as e:
        logger.log(f"Post-process exception: {e}")
        import traceback
        logger.log(f"  Traceback: {traceback.format_exc()}")
        return None
