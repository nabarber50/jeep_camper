# cam/setup/foamcam/config.py
import os
import re

from .helpers import get_desktop_path

class Config(object):
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
    MASLOW_SWAP_XY_COMPENSATION = True

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

    LOG_PATH = os.path.join(get_desktop_path(), "fusion_cam_log.txt")
    # LOG_PATH = "c:/Users/nabar/fusion_foamcam_panic.log"
    LAYER_NAME_RE = re.compile(r'^Layer_\d+_part_\d+$', re.IGNORECASE)
