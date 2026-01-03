# cam/setup/foam_cam_template.py
# ============================================================
# Foam CAM Template – Multi-Sheet Nesting + CAM (Refactored)
# Architectural cleanup: package modules + dataclasses
# ============================================================

import sys
import os
import traceback
import importlib
import adsk.core, adsk.fusion, adsk.cam

# folder containing this entry script
_here = os.path.dirname(os.path.abspath(__file__))

# Add both the setup directory (for foamcam package) and parent cam directory (for common)
_repo_root = _here  # setup/ folder
_cam_root = os.path.dirname(_here)  # cam/ folder

if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
if _cam_root not in sys.path:
    sys.path.insert(1, _cam_root)

# Force reload of foamcam modules to pick up code changes (bypass __pycache__)
_foamcam_modules = [m for m in list(sys.modules.keys()) if m.startswith('foamcam')]
for mod_name in _foamcam_modules:
    try:
        del sys.modules[mod_name]
    except:
        pass
    
try:
    from common.config import Config
    from common.logging import AppLogger
    from foamcam.units import Units
    from foamcam.collect import collect_layout_bodies
    from foamcam.nesting import SheetNester
    from foamcam.cam_ops import CamBuilder
    from foamcam.stock_wcs import StockWcsEnforcer
    from foamcam.helpers import get_cam_product, post_process_setup
    from foamcam.sheet_registry import register_sheet_parts, clear_registry

except Exception as e:
    import traceback
    with open(os.path.join(os.path.expanduser("~"), "fusion_foamcam_panic.log"), "a", encoding="utf-8") as f:
        f.write("IMPORT ERROR:\n")
        f.write(traceback.format_exc())
        f.write("\n\n")
    raise  # Re-raise to stop execution

def run(context):
    """Main entrypoint for Foam CAM template."""
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface
        doc = app.activeDocument
        _logger = AppLogger(path=Config.LOG_PATH_NESTING, ui=ui, raise_on_fail=True)
        _logger.log("=== RUN START ===")
        # Quick diagnostic to confirm which config flags are active at runtime
        _logger.log(
            "Startup: "
            f"MASLOW_SWAP_XY_COMPENSATION={getattr(Config,'MASLOW_SWAP_XY_COMPENSATION',None)} "
            f"MASLOW_ROTATE_SHEET_BODIES={getattr(Config,'MASLOW_ROTATE_SHEET_BODIES',None)} "
            f"ALLOW_ROTATE_90={getattr(Config,'ALLOW_ROTATE_90',None)}"
        )
        
        # Check design mode and warn if not in direct modeling
        try:
            design = adsk.fusion.Design.cast(app.activeProduct)

            if not design:
                ui.messageBox('No active design', 'No Design')
                return

            # Check the current modeling mode
            if design.designType == adsk.fusion.DesignTypes.DirectDesignType:
                ui.messageBox('Design is already in Direct Modeling mode.')
            else:
                # Attempt to change to Direct Modeling mode
                # NOTE: This will trigger a UI prompt for the user to confirm history deletion
                design.designType = adsk.fusion.DesignTypes.DirectDesignType
                ui.messageBox('Design has been switched to Direct Modeling mode (user confirmation required in UI).')
        except:
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
            
        # Clear sheet registry at start of each run
        clear_registry()
        
    except Exception as e:
        with open(os.path.join(os.path.expanduser("~"), "fusion_foamcam_panic.log"), "a", encoding="utf-8") as f:
            # f.write("sys.path[0]=%s\n" % sys.path[0])
            f.write(str(e))
            return

    try:
        if not doc:
            ui.messageBox("No active document.")
            _logger.log("No active document -> abort.")
            return
        _logger.log(f"Active doc: {doc.name if doc else 'None'}", show_ui=True)

        design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
        if not design:
            ui.messageBox("Active document is not a Fusion Design (.f3d).")
            _logger.log("Not a Fusion Design -> abort.")
            return
        _logger.log(f"Design loaded: {bool(design)}")

        units = Units(design, _logger)

        # 1) Collect
        bodies, diag = collect_layout_bodies(design, Config, _logger)
        _logger.log(diag.to_log_string())

        if not bodies:
            ui.messageBox("No eligible solid bodies found to layout.\nCheck log for collector diagnostics.")
            _logger.log("No bodies -> abort.")
            return

        # 2) Nest
        sheets = []
        if Config.DO_AUTO_LAYOUT:
            _logger.log("Starting auto layout...")
            nester = SheetNester(design, units, _logger, Config)
            sheets = nester.layout(bodies)
            _logger.log(f"Auto layout complete. Sheets: {len(sheets)}")
            
            # Register sheet parts for NC labeling
            for sheet in sheets:
                setup_name = f"CAM_SHEET_{sheet.index:02d}_{sheet.class_name}"
                register_sheet_parts(setup_name, sheet.part_names)
        else:
            _logger.log("DO_AUTO_LAYOUT=False; skipping layout.")

        if not sheets:
            ui.messageBox(
                "No sheet layouts were created.\n\n"
                "If you expected sheets:\n"
                "- Make sure bodies are Solid\n"
                "- If visibility filtering is on, make sure bodies are Visible\n"
                "- Re-run\n\n"
                "Stopping before CAM creation."
            )
            _logger.log("No sheets created -> stop before CAM creation.")
            return

        # 3) CAM
        _logger.log("Acquiring CAM product...")
        cam = get_cam_product(app, ui, doc)
        if not cam:
            ui.messageBox("CAM product not available... etc")
            return

        _logger.log(f"CAM loaded: {bool(cam)}")
        if not cam:
            ui.messageBox(
                "No CAM product found.\n\n"
                "Fix:\n"
                "1) Switch to Manufacture workspace once\n"
                "2) Wait for it to load\n"
                "3) Re-run the script"
            )
            _logger.log("No CAM product -> abort.")
            return

        _logger.log("Creating CAM setups/ops for sheets...")

        # Create progress dialog for CAM creation
        progressDialog = ui.createProgressDialog()
        progressDialog.isCancelButtonShown = False
        progressDialog.show('Creating CAM setups...', '%p%', 0, 100)
        adsk.doEvents()

        # Enforcer: ensures stock dims + tries to enforce WCS params (best effort)
        enforcer = StockWcsEnforcer(design, units, _logger, Config)

        builder = CamBuilder(cam, design, units, _logger, Config, enforcer=enforcer)

        # Update progress to 50% at start
        progressDialog.progressValue = 50
        adsk.doEvents()

        result = builder.create_for_sheets(sheets, ui)

        # Update progress to 90% after CAM creation
        progressDialog.progressValue = 90
        adsk.doEvents()

        _logger.log("CAM creation complete.")
        
        # Post-processing (if enabled)
        nc_files = []
        if getattr(Config, 'AUTO_POST_PROCESS', False):
            _logger.log("Auto post-processing enabled, generating NC files...")
            _logger.log(f"  Total setups in CAM: {cam.setups.count}")
            
            # Update progress for post-processing phase
            progressDialog.progressValue = 95
            progressDialog.show('Generating NC files...', '%p%', 0, 100)
            adsk.doEvents()
            
            total_setups = sum(1 for setup in cam.setups if setup.name.startswith('CAM_SHEET_'))
            completed_setups = 0
            
            for setup in cam.setups:
                try:
                    _logger.log(f"  Checking setup: '{setup.name}'")
                    # Post-process all setups (they start with CAM_SHEET_ from layout)
                    if setup.name.startswith('CAM_SHEET_'):
                        _logger.log(f"    Post-processing setup: {setup.name}")
                        nc_path = post_process_setup(cam, setup, Config, _logger)
                        if nc_path:
                            nc_files.append(nc_path)
                            _logger.log(f"    Generated: {nc_path}")
                        else:
                            _logger.log(f"    Failed to generate NC file for {setup.name}")
                        
                        # Update progress bar for post-processing
                        completed_setups += 1
                        if total_setups > 0:
                            progress = 95 + int(5 * completed_setups / total_setups)
                            progressDialog.progressValue = progress
                            adsk.doEvents()
                    else:
                        _logger.log(f"    Skipping (name doesn't start with CAM_SHEET_)")
                except Exception as e:
                    _logger.log(f"  Post-process error for {setup.name}: {e}")
            
            # Set progress to 100% when done
            progressDialog.progressValue = 100
            adsk.doEvents()
            progressDialog.hide()
            
            if nc_files:
                _logger.log(f"Post-processing complete: {len(nc_files)} NC files generated")
            else:
                _logger.log("Post-processing: no NC files generated")
        else:
            _logger.log("AUTO_POST_PROCESS disabled, skipping NC generation")
            progressDialog.progressValue = 100
            adsk.doEvents()
            progressDialog.hide()
        
        # Build completion message
        completion_msg = (
            "Done.\n\n"
            f"Sheets: {len(sheets)}\n"
            f"Setups created: {result.setups_created}\n"
            f"Orientation enforcement failures: {result.enforcement_failures}\n"
        )
        
        if nc_files:
            completion_msg += f"\nNC files generated: {len(nc_files)}\n"
            completion_msg += "Output folder: " + str(getattr(Config, 'NC_OUTPUT_FOLDER', None) or Config.get_desktop_path())
        
        ui.messageBox(completion_msg)
        _logger.log("=== RUN SUCCESS ===")

    except Exception:
        tb = traceback.format_exc()
        _logger.log("EXCEPTION:\n" + tb)
        if ui:
            ui.messageBox("Failed (see Desktop log):\n\n" + tb)
    finally:
        _logger.log("=== RUN END ===")
