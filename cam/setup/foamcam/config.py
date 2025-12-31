# cam/setup/foamcam/config.py
import os
import re
import winreg
from pathlib import Path


class Config(object):
    @staticmethod
    def get_desktop_path(as_path_object: bool = False):
        """Return the Desktop path, respecting OneDrive redirection."""
        desktop = None

        # 1) Preferred: Windows 'User Shell Folders' registry value (OneDrive-aware)
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            ) as key:
                val, _typ = winreg.QueryValueEx(key, "Desktop")
                desktop = os.path.expandvars(val)
        except Exception:
            desktop = None

        # 2) Validate / normalize
        if desktop:
            desktop = os.path.normpath(desktop)
            if os.path.isdir(desktop):
                return Path(desktop) if as_path_object else desktop

        # 3) Fallbacks
        home = os.environ.get("USERPROFILE") or os.path.expanduser("~")

        od = os.environ.get("OneDrive")
        if od:
            od_desktop = os.path.normpath(os.path.join(od, "Desktop"))
            if os.path.isdir(od_desktop):
                return Path(od_desktop) if as_path_object else od_desktop

        fallback = os.path.normpath(os.path.join(home, "Desktop"))
        if os.path.isdir(fallback):
            return Path(fallback) if as_path_object else fallback

        return Path(home) if as_path_object else home

    # ----------------------------
    # CONFIG (ported from your script)
    # ----------------------------
    USE_VISIBLE_BODIES_ONLY = True

    # ---- Sheet / Nesting ----
    DO_AUTO_LAYOUT = True
    LAYOUT_BASE_NAME = 'SHEET_LAYOUT_4x8'

    SHEET_CLASSES = [
        ("STD_4x8", 1219.2, 2438.4),
        ("EXT_4x10", 1219.2, 3048.0),
        ("EXT_4x12", 1219.2, 3657.6),
        ("WIDE_6x10", 1828.8, 3048.0),
    ]
    
    # ---- Packing Optimization ----
    # Packing strategy: 'shelf' (fast, simple rows) or 'smart' (better density, slower)
    # 'shelf': Left-to-right rows, largest parts first (current default)
    # 'smart': Multi-pass with best-fit positioning and gap filling
    PACKING_STRATEGY = 'smart'  # 'shelf' or 'smart'
    
    # CRITICAL: Allow mixed packing of small + large parts on same sheets
    # When True: Small parts (void_candidates) are packed on the same sheets as large parts
    #            This dramatically reduces sheet count by filling gaps
    # When False: Small parts are relegated to separate "fallback" sheets (less efficient)
    # RECOMMENDED: Keep True for maximum material efficiency
    PACKING_MIXED_SMALL_LARGE = True  # Pack small parts alongside large parts on same sheets
    
    # When using 'smart' strategy, enable gap filling with smaller parts
    PACKING_SMART_GAP_FILL = True  # Try to fit smaller items into remaining spaces
    
    # Maximum packing passes to attempt per sheet (smart mode only)
    PACKING_MAX_PASSES = 2  # 1=single pass, 2+=multi-pass for better density
    
    # Log packing efficiency statistics
    PACKING_LOG_EFFICIENCY = True  # Report material utilization per sheet
    
    # ---- Void Nesting (Advanced) ----
    # Enable nesting smaller parts inside internal voids/holes of larger parts
    # Example: Small rectangles can nest inside the cutout of a large frame
    # Parts are now sorted by size globally to ensure large parts with voids are placed first
    ENABLE_VOID_NESTING = True  # Try nesting small parts in detected voids
    VOID_NESTING_MIN_SIZE = 30.0  # mm - minimum void size to consider for nesting
    VOID_NESTING_MARGIN = 2.0  # mm - clearance margin around nested parts (tighter to fit pockets)
    VOID_NESTING_ALLOW_CROSS_SHEETS = True  # Allow filling voids on already-created sheets from later parts/classes
    ENABLE_BACKFILL_VOID_PASS = False  # Second-pass void backfill (can be heavy); fallback sheets will be used instead
    # ⚠️  BACKFILL KNOWN ISSUE: Backfill void pass causes hang/crash during iteration.
    # Root cause: Likely infinite loop, Fusion API call exception, or memory issue in backfill loop.
    # Workaround: Disabled by default. Fallback placement (normal shelf packing on extra sheets) is stable.
    # Parts at or below these limits will be deferred to a dedicated void-fill phase before creating new sheets
    VOID_CANDIDATE_MAX_DIM_MM = 300.0
    VOID_CANDIDATE_MAX_AREA_MM2 = 250000.0
    
    # Packing groups: Force certain parts to be packed together on the same sheet
    # This enables void nesting between parts that have parent-child relationships
    # Format: list of tuples, each tuple contains part names that should stay together
    # Example: PACKING_GROUPS = [("Layer_15_part_01", "Layer_17_part_03")]
    # This forces L17_P03 to pack on same sheet as L15_P01 for void nesting optimization
    # To nest all small parts from layers 3-13 into the void of Layer_15_part_01:
    PACKING_GROUPS = [
        # Anchor part first, then only the big companions that must share its sheet
        # Keep small parts free to choose STD_4x8 where possible, reducing 6x10 usage
        ("Layer_15_part_01",   # Large anchor with void
         "Layer_17_part_03",   # 588×1325mm part (void carrier)
         "Layer_03_part_01",   # ~1002x1466
         "Layer_13_part_04"),  # ~976x1415
    ]

    # Hard-fail if any part cannot be placed; set to False to allow partial layouts
    FAIL_ON_UNPLACED_PARTS = True

    # When True, allow packing-group members to spill onto later sheets of the same class
    # after the anchor sheet has been processed. This prevents hard deadlocks when the
    # anchor sheet runs out of space but some grouped parts remain.
    PACKING_GROUP_ALLOW_SPILLOVER = True

    # Optional safety: do NOT force a group member to the anchor's sheet class if it exceeds
    # these size limits. This lets very large parts keep their own best-fitting class and
    # avoids inflating sheet count when anchors are on larger stock. Set to None to disable.
    PACKING_GROUP_FORCE_MAX_DIM_MM   = 1200.0  # skip forcing if either dimension exceeds this (None = disable cap)
    PACKING_GROUP_FORCE_MAX_AREA_MM2 = None    # or skip forcing if area exceeds this

    # ---- Axis mapping compensation (Maslow vs Fusion) ----
    # Symptom: Fusion setup/stock looks correct, but on the Maslow the job
    # runs 90° off (long moves run along the short physical axis).
    #
    # This is commonly caused by a sender/post/controller combination that
    # effectively swaps X/Y at runtime.
    #
    # When enabled, FoamCAM will:
    #   1) Rotate each generated SHEET_* model 90° about +Z once (persistent)
    #   2) Swap the stock box dimensions in the CAM setup (X=long, Y=short)
    # This makes the exported G-code cancel the downstream X/Y swap so the
    # cut runs the long direction along your physical long axis.
    MASLOW_SWAP_XY_COMPENSATION = False

    # When True, rotate the sheet component bodies +90° about Z in addition to
    # any WCS rotation. This used to be the hard-coded behavior; it can be
    # surprising, so default is False. Set to True to preserve the original
    # rotate-the-bodies compensation behavior.
    MASLOW_ROTATE_SHEET_BODIES = False

    # ---- Tool Library Configuration ----
    # Tool library URL for automatic tool selection (inch)
    # Using Fusion's built-in sample library by default
    TOOL_LIBRARY_URL = 'systemlibraryroot://Samples/Milling Tools (Inch).json'
    
    # Tool search criteria for auto-selection
    # If TOOL_NAME_SEARCH is set, search by name substring (case-insensitive)
    # Otherwise, search by type and diameter range
    # Note: Fusion internal units are ALWAYS cm, even for Inch libraries
    TOOL_NAME_SEARCH = 'flat'  # Search substring in tool description (try 'flat', '0.25', '1/4', etc.)
    TOOL_TYPE = 'flat end mill'  # Tool type when searching by criteria
    TOOL_DIAMETER_MIN = 0.5  # cm (for 1/4" = 0.635cm, this searches 0.5-0.7cm range)
    TOOL_DIAMETER_MAX = 0.7  # cm
    TOOL_MIN_FLUTE_LENGTH = None  # cm, or None for no minimum

    # ---- Post-Processing Configuration ----
    # Enable automatic post-processing after CAM setup creation
    AUTO_POST_PROCESS = True  # Set to True once tested and working
    
    # Testing mode: only generate NC for first sheet and 2D Profile operations
    # Set to False to generate NC files for all sheets and all operations
    GENERATE_NC_ONESHOT = True  # True = first sheet only (fast testing), False = all sheets (full production)
    
    # Post processor to use (description must match exactly)
    POST_PROCESSOR_NAME = 'Generic 2D'  # Available: 'Generic 2D', 'RS-274D', 'AutoCAD DXF', 'XYZ'
    POST_PROCESSOR_VENDOR = 'Autodesk'  # Filter by vendor
    
    # Output settings
    NC_OUTPUT_FOLDER = os.path.join(get_desktop_path(), "fusion_nc")  # NC files output folder
    NC_OPEN_IN_EDITOR = False  # Open generated NC files in editor
    NC_FILE_PREFIX = 'FoamCAM_'  # Prefix for generated NC filenames
    
    # Post parameters
    POST_TOLERANCE = 0.004  # Built-in tolerance for post processor
    POST_SHOW_SEQUENCE_NUMBERS = False  # Include N-numbers in output
    POST_PROGRAM_COMMENT = 'Generated by FoamCAM'  # Header comment
    
    # Part identification in NC files
    NC_ADD_PART_LABELS = True  # Add comments identifying each part being cut
    NC_LABEL_FORMAT = '; Part: {name} ({width:.1f}x{height:.1f}mm)'  # Comment format for part labels
    
    # Tiny parts detection and warnings
    WARN_TINY_PARTS = True  # Log warnings for parts below minimum size threshold
    MIN_PART_WIDTH_MM = 25.0  # Minimum width in mm (parts smaller may be difficult to cut)
    MIN_PART_HEIGHT_MM = 25.0  # Minimum height in mm (parts smaller may be difficult to cut)
    SKIP_TINY_PARTS = False  # If True, exclude tiny parts from layout entirely (default: warn only)

    # Developer-only guard: when True, raise a RuntimeError immediately before
    # applying any sheet-body rotation so a stack trace can be captured during
    # debugging. Default is False.
    DEBUG_FAIL_ON_ROTATION = False

    # Verbose diagnostic logging for geometry operations, footprint calculations, etc.
    # Set to False to suppress detailed transformation/monkeypatch logs.
    VERBOSE_GEOMETRY_LOGGING = False

    # ---- Optional concave/U-shape pairing (experimental) ----
    ENABLE_U_PAIRING = True
    U_FILL_RATIO_MAX = 0.70
    U_MIN_AREA_MM2   = 250000.0
    U_PAIR_STEP_MM   = 25.0
    U_PAIR_GRID_N    = 5
    U_PAIR_MIN_GAIN  = 0.10

    # ---- Naming ergonomics ----
    COMPACT_PART_NAMES = True
    LOG_NATIVE_BBOX_SIZES = False

    # Default sheet expressions (still used for CAM fallback)
    SHEET_W   = '2438.4 mm'
    SHEET_H   = '1219.2 mm'
    SHEET_THK = '38.1 mm'

    LAYOUT_MARGIN = '10 mm'
    LAYOUT_GAP    = '8 mm'
    MIN_PART_SPACING = '6 mm'  # minimum clearance between placed parts (bbox-to-bbox)
    ALLOW_ROTATE_90 = True
    HIDE_ORIGINALS_AFTER_COPY = True

    # ---- Tool preference ----
    # Tool selection searches all libraries for a tool whose description/name contains this substring (case-insensitive).
    # Common defaults: 'flat end mill' (Fusion default library), 'Ø1/4"', '1/4" flat', etc.
    PREFERRED_TOOL_NAME_CONTAINS = '1/4" flat end mill'
    PREFERRED_TOOL_DIAMETER_IN   = 0.25  # fallback diameter match if name search fails

    # ---- Maslow Z Safety ----
    MASLOW_RETRACT       = '1.5 mm'
    MASLOW_CLEARANCE     = '2.0 mm'
    MASLOW_FEED          = '1.0 mm'
    MASLOW_PLUNGE_FEED   = '300 mm/min'
    MASLOW_RETRACT_FEED  = '300 mm/min'

    # ---- Stepdowns ----
    PROFILE_STEPDOWN = '6 mm'
    ROUGH_STEPDOWN   = '6 mm'
    FINISH_STEPDOWN  = '2 mm'

    LOG_PATH = os.path.join(get_desktop_path(), "fusion_cam_log.txt")
    # LOG_PATH = "c:/Users/nabar/fusion_foamcam_panic.log"
    LAYER_NAME_RE = re.compile(r'^Layer_\d+_part_\d+$', re.IGNORECASE)
