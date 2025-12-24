# cam/setup/foamcam/config.py
import os
import re

class Config:
    # ----------------------------
    # CONFIG (ported from your script)
    # ----------------------------
    USE_VISIBLE_BODIES_ONLY = True

    # ---- Sheet / Nesting ----
    DO_AUTO_LAYOUT = True
    LAYOUT_BASE_NAME = 'SHEET_LAYOUT_4x8'

    SHEET_CLASSES = [
        ("STD_4x8",   1219.2, 2438.4),
        ("EXT_4x10",  1219.2, 3048.0),
        ("EXT_4x12",  1219.2, 3657.6),
        ("WIDE_6x10", 1828.8, 3048.0),
    ]

    MASLOW_FORCE_WCS_ROTATE_90 = True

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
    ALLOW_ROTATE_90 = True
    HIDE_ORIGINALS_AFTER_COPY = True

    # ---- Tool preference ----
    PREFERRED_TOOL_NAME_CONTAINS = 'Ø1/4"'
    PREFERRED_TOOL_DIAMETER_IN   = 0.25

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

    LOG_PATH = os.path.join(os.path.expanduser("~"), "Desktop", "foam_cam_template_log.txt")
    LAYER_NAME_RE = re.compile(r'^Layer_\d+_part_\d+$', re.IGNORECASE)
