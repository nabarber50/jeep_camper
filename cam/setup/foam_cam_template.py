# cam/setup/foam_cam_template.py
# ============================================================
# Foam CAM Template – Multi-Sheet Nesting + CAM (Refactored)
# Architectural cleanup: package modules + dataclasses
# ============================================================

import os, sys
import traceback
import adsk.core, adsk.fusion, adsk.cam

# folder containing this entry script
_here = os.path.dirname(os.path.abspath(__file__))

# If your package lives next to this script as: <something>/foamcam/...
# then the parent of that folder must be on sys.path.
_repo_root = _here  # adjust if needed (see note below)

if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
    
try:
    from foamcam.config import Config
    from foamcam.logging import AppLogger
    from foamcam.units import Units
    from foamcam.collect import collect_layout_bodies
    from foamcam.nesting import SheetNester
    from foamcam.cam_ops import CamBuilder
    from foamcam.stock_wcs import StockWcsEnforcer
    from foamcam.helpers import get_cam_product

except Exception as e:
    with open(os.path.join(os.path.expanduser("~"), "fusion_foamcam_panic.log"), "a", encoding="utf-8") as f:
        f.write(str(e))

def run(context):
    try:    
        app = adsk.core.Application.get()
        ui  = app.userInterface
        doc = app.activeDocument
        _logger = AppLogger(path=Config.LOG_PATH, ui=ui, raise_on_fail=True)
        _logger.log("=== RUN START ===")
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

        # Enforcer: ensures stock dims + tries to enforce WCS params (best effort)
        enforcer = StockWcsEnforcer(design, units, _logger, Config)

        builder = CamBuilder(cam, design, units, _logger, Config, enforcer=enforcer)
        result = builder.create_for_sheets(sheets, ui)

        _logger.log("CAM creation complete.")
        ui.messageBox(
            "Done.\n\n"
            f"Sheets: {len(sheets)}\n"
            f"Setups created: {result.setups_created}\n"
            f"Orientation enforcement failures: {result.enforcement_failures}\n"
        )
        _logger.log("=== RUN SUCCESS ===")

    except Exception:
        tb = traceback.format_exc()
        _logger.log("EXCEPTION:\n" + tb)
        if ui:
            ui.messageBox("Failed (see Desktop log):\n\n" + tb)
    finally:
        _logger.log("=== RUN END ===")
