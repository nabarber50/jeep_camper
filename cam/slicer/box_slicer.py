"""
BoxSlicer - Fusion 360 Script
Fit panels to standard stock sizes and generate rectangular boxes for optimal CNC manufacturing.

Workflow:
1. Load panelized design (created by FoamPanelizer)
2. Analyze panel dimensions (WIDTH × HEIGHT × DEPTH)
3. Fit each panel to available stock sizes from Config
4. Calculate lamination (stacking) if panel exceeds foam thickness
5. Generate rectangular boxes ready for nesting
6. Output summary and ready for foam_cam_template
"""

import os, sys, traceback, importlib

# Ensure this script's directory is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Add parent directories to path for common imports
CAM_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # ../../cam
COMMON_DIR = os.path.join(CAM_DIR, 'common')
SLICER_DIR = SCRIPT_DIR
if COMMON_DIR not in sys.path:
    sys.path.insert(0, COMMON_DIR)
if SLICER_DIR not in sys.path:
    sys.path.insert(0, SLICER_DIR)

try:
    import adsk.core, adsk.fusion
except ImportError as e:
    print(f"FATAL: Could not import adsk modules: {e}")
    sys.exit(1)

# Import custom modules
AppLogger = None
Config = None

try:
    from common.logging import AppLogger
except ImportError as e:
    class MinimalLogger:
        def __init__(self, path=None, **kwargs):
            self.path = path
        def log(self, msg):
            print(msg)
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(msg + "\n")
                        f.flush()
                except:
                    pass
    AppLogger = MinimalLogger

try:
    from common.config import Config
except ImportError as e:
    from datetime import datetime
    class MinimalConfig:
        @staticmethod
        def get_run_log_folder():
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            logs_root = os.path.join(desktop, "fusion_cam_logs")
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            run_folder = os.path.join(logs_root, timestamp)
            try:
                os.makedirs(run_folder, exist_ok=True)
            except:
                pass
            return run_folder
        _log_folder = get_run_log_folder.__func__()
        LOG_PATH_SLICER = os.path.join(_log_folder, "fusion_cam_slicer.txt")
        SHEET_CLASSES = [
            ("STD_4x8", 1219.2, 2438.4),
            ("EXT_4x10", 1219.2, 3048.0),
            ("EXT_4x12", 1219.2, 3657.6),
            ("WIDE_6x10", 1828.8, 3048.0),
        ]
        FOAM_THICKNESS_MM = 38.1
        ALLOW_LAMINATION = True
        LAMINATION_MAX_LAYERS = 3
    Config = MinimalConfig

# ---- CONFIG ----
TARGET_KEYWORDS = ["SHEET"]  # Look for sheet/panel bodies
PANEL_NAMES = ["TOP", "LEFT", "RIGHT", "REAR", "FRONT", "BOTTOM"]  # Panel keywords
# ----------------


def run(context):
    ui = None
    logger = None
    log_path = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        
        # Initialize logger
        try:
            log_path = Config.LOG_PATH_SLICER
        except:
            log_path = os.path.join(os.path.expanduser("~"), "Desktop", "box_slicer.log")
        
        log_dir = os.path.dirname(log_path)
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
            except:
                pass
        
        logger = AppLogger(path=log_path, ui=ui, raise_on_fail=False)
        
        logger.log("=== BOX SLICER RUN START ===")
        logger.log(f"Log path: {log_path}")
        
        # Get active design
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            logger.log("ERROR: No active Fusion design")
            if ui:
                ui.messageBox(f"No active design.\n\nLog: {log_path}")
            return
        
        # Import box_slicer_core
        logger.log("Importing box_slicer_core...")
        try:
            import box_slicer_core as bsc
            importlib.reload(bsc)  # Force reload to pick up code changes
        except Exception as e:
            logger.log(f"ERROR: Could not import box_slicer_core: {e}")
            if ui:
                ui.messageBox(f"Failed to import box_slicer_core.\n\n{e}\n\nLog: {log_path}")
            return
        
        # Build stock list from Config
        stocks = []
        try:
            for sheet_class in Config.SHEET_CLASSES:
                name, width, height = sheet_class
                stocks.append(bsc.Stock(name, width, height))
                logger.log(f"Stock: {name} ({width:.1f} × {height:.1f}mm)")
        except Exception as e:
            logger.log(f"ERROR loading stock classes: {e}")
            if ui:
                ui.messageBox(f"Failed to load stock dimensions.\n\n{e}\n\nLog: {log_path}")
            return
        
        # Initialize slicer
        foam_thick = Config.FOAM_THICKNESS_MM if hasattr(Config, 'FOAM_THICKNESS_MM') else 38.1
        allow_lam = Config.ALLOW_LAMINATION if hasattr(Config, 'ALLOW_LAMINATION') else True
        max_lam = Config.LAMINATION_MAX_LAYERS if hasattr(Config, 'LAMINATION_MAX_LAYERS') else 3
        
        slicer = bsc.BoxSlicer(stocks, foam_thick, allow_lam, max_lam)
        logger.log(f"Foam thickness: {foam_thick}mm, Lamination: {allow_lam}, Max layers: {max_lam}")
        
        # Analyze panel bodies
        logger.log("Analyzing panel bodies...")
        root = design.rootComponent
        panel_count = 0
        
        for body in root.bRepBodies:
            body_name = body.name
            logger.log(f"  Analyzing body: {body_name}")
            
            # Check if this is a panel
            is_panel = any(keyword.upper() in body_name.upper() for keyword in PANEL_NAMES)
            if not is_panel:
                logger.log(f"    Skipping (not a panel)")
                continue
            
            # Get bounding box
            try:
                bbox = body.boundingBox
                width = bbox.maxPoint.x - bbox.minPoint.x
                height = bbox.maxPoint.y - bbox.minPoint.y
                depth = bbox.maxPoint.z - bbox.minPoint.z
                
                logger.log(f"    Dims: {width:.1f} × {height:.1f} × {depth:.1f}mm")
                
                # Add to slicer
                slicer.add_panel(body_name, width, height, depth)
                panel_count += 1
            except Exception as e:
                logger.log(f"    ERROR getting bounds: {e}")
        
        logger.log(f"Found {panel_count} panels to slice")
        
        if panel_count == 0:
            logger.log("WARNING: No panels found")
            if ui:
                ui.messageBox(f"No panel bodies found.\nMake sure to run FoamPanelizer first.\n\nLog: {log_path}")
            return
        
        # Slice all panels
        logger.log("Slicing panels...")
        result = slicer.slice_all()
        
        # Log results
        for panel_result in result["panels"]:
            logger.log(f"\nPanel: {panel_result['panel_name']}")
            logger.log(f"  Dims: {panel_result['panel_dims']}")
            if panel_result.get("strategy"):
                logger.log(f"  Strategy: {panel_result['strategy']}")
                logger.log(f"  Waste: {panel_result['waste_pct']:.1f}%")
            if panel_result.get("error"):
                logger.log(f"  ERROR: {panel_result['error']}")
            if panel_result.get("warning"):
                logger.log(f"  WARNING: {panel_result['warning']}")
        
        # Summary
        summary = result["summary"]
        logger.log(f"\n=== SUMMARY ===")
        logger.log(f"Total panels: {summary['total_panels']}")
        logger.log(f"Total boxes: {summary['total_boxes']}")
        logger.log(f"Total laminations: {summary['total_laminations']}")
        
        if summary.get("warnings"):
            logger.log(f"Warnings: {len(summary['warnings'])}")
            for w in summary["warnings"]:
                logger.log(f"  - {w}")
        
        logger.log(slicer.get_box_summary())
        
        # Show results to user
        summary_msg = (
            f"BoxSlicer Complete ✅\n\n"
            f"Panels found: {summary['total_panels']}\n"
            f"Boxes generated: {summary['total_boxes']}\n"
            f"Lamination passes: {summary['total_laminations']}\n\n"
            f"Ready for foam_cam_template nesting.\n\n"
            f"Log: {log_path}"
        )
        if ui:
            ui.messageBox(summary_msg)
        
        logger.log("=== RUN END ===")
        
    except Exception as e:
        err = traceback.format_exc()
        if logger:
            logger.log(f"EXCEPTION:\n{err}")
        else:
            print(f"EXCEPTION:\n{err}")
        if ui:
            ui.messageBox(f"BoxSlicer crashed:\n\n{err}\n\nLog: {log_path if log_path else 'unknown'}")


def stop(context):
    pass
