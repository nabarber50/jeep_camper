import os, sys, traceback, importlib

# Ensure this script's directory is on sys.path (Fusion does NOT do this reliably)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Add setup directory to path so we can import common and foamcam modules
# FoamPanelizer is at cam/FoamPanelizer/, common is at cam/common/
CAM_DIR = os.path.dirname(SCRIPT_DIR)  # ../cam
SETUP_DIR = os.path.join(CAM_DIR, 'setup')
COMMON_DIR = os.path.join(CAM_DIR, 'common')
if SETUP_DIR not in sys.path:
    sys.path.insert(0, SETUP_DIR)
if COMMON_DIR not in sys.path:
    sys.path.insert(0, COMMON_DIR)

try:
    import adsk.core, adsk.fusion
except ImportError as e:
    print(f"FATAL: Could not import adsk modules: {e}")
    sys.exit(1)

# Import custom modules with error handling
AppLogger = None
Config = None

try:
    from common.logging import AppLogger
except ImportError as e:
    print(f"WARNING: Could not import AppLogger: {e}")
    # Create a fallback logger that writes to disk
    class MinimalLogger:
        def __init__(self, path=None, **kwargs):
            self.path = path
            self.msgs = []
        
        def log(self, msg):
            self.msgs.append(msg)
            print(msg)
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(msg + "\n")
                        f.flush()
                except Exception as ex:
                    print(f"Failed to write to {self.path}: {ex}")
    
    AppLogger = MinimalLogger

try:
    from common.config import Config
except ImportError as e:
    print(f"WARNING: Could not import Config: {e}")
    # Create minimal fallback config with log folder creation
    from datetime import datetime
    import os
    class MinimalConfig:
        @staticmethod
        def get_run_log_folder():
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            logs_root = os.path.join(desktop, "fusion_cam_logs")
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            run_folder = os.path.join(logs_root, timestamp)
            try:
                os.makedirs(run_folder, exist_ok=True)
                return run_folder
            except Exception:
                return logs_root
        
        _log_folder = get_run_log_folder.__func__()
        LOG_PATH_PANELIZER = os.path.join(_log_folder, "fusion_cam_panelizer.txt")
    Config = MinimalConfig

# ---- CONFIG (edit these) ----
TARGET_KEYWORDS = ["CAMPER BASE", "CAMBER BASE"]
STEP_PATH = r"C:\temp\panelize_export.step"
CAPTURE_DEPTH = "80 mm"
PANEL_PRIORITY = ["TOP", "LEFT", "RIGHT", "REAR"]  # Panels to extract (front and bottom are OPEN)
KEEP_TOOL_SLABS_VISIBLE = False
# Note: Coordinate system (from geometry analysis):
#   X = length (front-back, the long axis)
#   Y = width (left-right)
#   Z = height (up-down)
# Only 4 panels: TOP, LEFT, RIGHT, REAR (front and bottom open for access)
# -----------------------------

def run(context):
    ui = None
    logger = None
    log_path = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        
        # Initialize logger - use Config.LOG_PATH_PANELIZER if available
        try:
            log_path = Config.LOG_PATH_PANELIZER
        except Exception as e:
            log_path = os.path.join(os.path.expanduser("~"), "Desktop", "foampanelizer.log")
        
        # Ensure the directory exists
        log_dir = os.path.dirname(log_path)
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
            except Exception as e:
                # If that fails, use Desktop
                log_path = os.path.join(os.path.expanduser("~"), "Desktop", "foampanelizer.log")
                log_dir = os.path.dirname(log_path)
        
        # Try to write startup marker
        startup_marker = f"=== FOAMPANELIZER RUN START ===\nLog path: {log_path}\n"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(startup_marker)
                f.flush()
        except Exception as write_err:
            # If write fails, log the error to Desktop
            fallback = os.path.join(os.path.expanduser("~"), "Desktop", "foampanelizer_error.log")
            with open(fallback, "a", encoding="utf-8") as f:
                f.write(f"Failed to write to {log_path}: {write_err}\n")
        
        logger = AppLogger(path=log_path, ui=ui, raise_on_fail=False)
        
        logger.log("=== RUN START ===")
        logger.log(f"Log path: {log_path}")

        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            logger.log("No active Fusion design.")
            if ui:
                ui.messageBox(f"No active Fusion design.\n\nLog:\n{log_path}")
            return

        # Verify export folder exists
        export_dir = os.path.dirname(STEP_PATH)
        logger.log(f"STEP_PATH={STEP_PATH}")
        if export_dir and not os.path.isdir(export_dir):
            logger.log(f"Export dir missing: {export_dir}")
            if ui:
                ui.messageBox(f"Export folder does not exist:\n{export_dir}\n\nCreate it and re-run.\n\nLog:\n{log_path}")
            return

        # Import panelizer_core (robust) with forced reload to pick up changes
        logger.log("Importing panelizer_core...")
        try:
            import panelizer_core
            importlib.reload(panelizer_core)  # Force reload to pick up code changes
        except Exception as e:
            logger.log("FAILED import panelizer_core: " + repr(e))
            if ui:
                ui.messageBox(f"Failed to import panelizer_core.\nMake sure panelizer_core.py is in the same folder.\n\n{e}\n\nLog:\n{log_path}")
            return

        # Find target occurrence
        root = design.rootComponent
        logger.log(f"Searching target keywords: {TARGET_KEYWORDS}")
        occ = panelizer_core.find_target_occurrence(root, TARGET_KEYWORDS)
        if not occ:
            logger.log("Target occurrence NOT FOUND.")
            if ui:
                ui.messageBox(
                    "Could not find target component/occurrence.\n"
                    f"Keywords tried: {TARGET_KEYWORDS}\n\n"
                    f"Log:\n{log_path}"
                )
            return
        logger.log(f"Found target occurrence: {occ.name} / component={occ.component.name}")

        # Export STEP
        logger.log("Exporting STEP...")
        export_mgr = design.exportManager
        step_opts = export_mgr.createSTEPExportOptions(STEP_PATH, occ.component)
        export_mgr.execute(step_opts)
        logger.log("STEP export complete.")

        # Panelize in new design
        logger.log("Panelizing into new design...")
        result = panelizer_core.panelize_step_into_new_design(
            app=app,
            ui=ui,
            step_path=STEP_PATH,
            capture_expr=CAPTURE_DEPTH,
            panel_priority=PANEL_PRIORITY,
            keep_tools_visible=KEEP_TOOL_SLABS_VISIBLE,
            source_camera=app.activeViewport.camera
        )
        logger.log(f"panelize result: {result}")

        if result and result.get("ok"):
            if ui:
                ui.messageBox(
                    "FoamPanelizer complete ✅\n\n"
                    f"New document: {result.get('doc_name')}\n"
                    f"Extracted panels: {result.get('panel_count')}\n\n"
                    f"Log:\n{log_path}"
                )
        else:
            if ui:
                ui.messageBox(
                    "FoamPanelizer finished but did not report success.\n\n"
                    f"Log:\n{log_path}"
                )

        logger.log("=== RUN END ===")

    except Exception as e:
        err = traceback.format_exc()
        if logger:
            logger.log("EXCEPTION:\n" + err)
        else:
            print("EXCEPTION:\n" + err)
        if ui:
            ui.messageBox(
                "FoamPanelizer crashed:\n\n" 
                + err 
                + f"\n\nLog:\n{log_path if log_path else 'Could not determine log path'}"
            )

