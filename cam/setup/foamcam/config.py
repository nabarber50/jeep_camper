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

    # IMPORTANT AXIS CONVENTION (Maslow / most CNC):
    #   +X = left/right (typically the LONG axis on a 4x8)
    #   +Y = toward/away (typically the SHORT axis on a 4x8)
    # If you jog +X and the router moves right, and you jog +Y and it moves away,
    # that matches this convention.
    #
    # Therefore: sheet classes below are defined as (X_len_mm, Y_len_mm)
    # with the LONG dimension as X for 4x8 / 4x10 / 4x12.
    SHEET_CLASSES = [
        ("STD_4x8",   2438.4, 1219.2),
        ("EXT_4x10",  3048.0, 1219.2),
        ("EXT_4x12",  3657.6, 1219.2),
        ("WIDE_6x10", 3048.0, 1828.8),
    ]

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
