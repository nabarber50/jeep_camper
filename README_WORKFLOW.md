# FoamCAM Workflow - Panel-First Manufacturing

## Overview
A three-step Fusion 360 workflow optimized for foam CNC cutting:
1. **FoamPanelizer** - Extract structural panels (TOP/LEFT/RIGHT/REAR)
2. **BoxSlicer** - Fit panels to standard stock and calculate lamination
3. **foam_cam_template** - Nest rectangular boxes for efficient cutting

This approach minimizes waste, reduces assembly complexity, and leverages CNC strengths.

---

## Step 1: FoamPanelizer
**Purpose:** Extract key structural panels from your design.

**Location:** `cam/FoamPanelizer/FoamPanelizer.py`

**Workflow:**
1. Open your camper design in Fusion 360
2. Run **FoamPanelizer** command
3. Creates a new document with extracted panels (TOP, LEFT, RIGHT, REAR)
4. Exports STEP for panelization analysis

**Output:**
- New Fusion design with panel components
- STEP file at `C:\temp\panelize_export.step`
- Log: `fusion_cam_logs/<timestamp>/fusion_cam_panelizer.txt`

**Config (in FoamPanelizer.py):**
```python
TARGET_KEYWORDS = ["CAMPER BASE", "CAMBER BASE"]  # Target component
CAPTURE_DEPTH = "80 mm"                           # Capture depth for slicing
REAR_SIDE = "MIN_Z"                               # Rear panel orientation
PANEL_PRIORITY = ["TOP", "LEFT", "RIGHT", "REAR"] # Panel extraction order
```

---

## Step 2: BoxSlicer
**Purpose:** Analyze panels and fit them to standard stock dimensions.

**Location:** `cam/slicer/box_slicer.py`

**Workflow:**
1. With the panelized design open in Fusion 360
2. Run **BoxSlicer** command
3. Analyzes each panel's bounding box
4. Fits panels to available stock sizes
5. Calculates lamination (stacking) if needed
6. Generates rectangular box specifications

**Output:**
- Box specifications with lamination info
- Summary of stock usage and waste %
- Log: `fusion_cam_logs/<timestamp>/fusion_cam_slicer.txt`
- Ready for nesting in foam_cam_template

**Config (in common/config.py):**
```python
SHEET_CLASSES = [
    ("STD_4x8", 1219.2, 2438.4),    # Standard 4×8 foot foam sheets
    ("EXT_4x10", 1219.2, 3048.0),   # Extended sizes available
    ("EXT_4x12", 1219.2, 3657.6),
    ("WIDE_6x10", 1828.8, 3048.0),
]

FOAM_THICKNESS_MM = 38.1            # Single foam piece thickness
ALLOW_LAMINATION = True             # Stack multiple pieces for deeper panels
LAMINATION_MAX_LAYERS = 3           # Maximum layers to stack
```

**Key Features:**
- **Best-fit algorithm** - Minimizes waste by selecting optimal stock
- **Automatic lamination** - Stacks foam when panel depth > thickness
- **Waste reporting** - Shows % waste for each panel's stock choice
- **Panel analysis** - Extracts bounds from 3D geometry

---

## Step 3: foam_cam_template
**Purpose:** Nest rectangular boxes on sheets for CNC cutting.

**Location:** `cam/setup/foam_cam_template.py`

**Workflow:**
1. Ensure the current design has your part bodies (from step 1-2 or your original design)
2. Run **foam_cam_template** command
3. Nests all parts/boxes on available stock sheets
4. Creates SHEET_1, SHEET_2, etc. components with positioned parts
5. Generates CAM setups for cutting (optional)

**Output:**
- New sheets with positioned parts (SHEET_1, SHEET_2, ...)
- Positioning data for toolpath generation
- Log: `fusion_cam_logs/<timestamp>/fusion_cam_nesting.txt`
- CAM operations (if enabled)

**Config (in common/config.py):**
```python
PACKING_STRATEGY = 'smart'          # 'shelf' or 'smart' packing
PACKING_MIXED_SMALL_LARGE = True    # Mix small parts with large on same sheet
PACKING_ALLOW_CROSS_CLASS = True    # Use cross-sheet stock classes
ENABLE_VOID_NESTING = True          # Nest smaller parts in voids (advanced)
```

---

## Logging

All logs are organized in a single directory:
```
C:\Users\<username>\Desktop\fusion_cam_logs\
  2026-01-03_14-30-45/
    fusion_cam_panelizer.txt
    fusion_cam_slicer.txt
    fusion_cam_nesting.txt
    fusion_cam_operations.txt
  2026-01-03_14-35-12/
    ...
```

Each run gets its own timestamped folder for easy tracking and debugging.

---

## Complete Workflow Example

```
1. Start with camper design in Fusion 360
   └─ Contains "CAMPER BASE" component with geometry

2. Run FoamPanelizer
   └─ Extracts TOP, LEFT, RIGHT, REAR panels
   └─ Creates new design: "Untitled"
   └─ Exports STEP: C:\temp\panelize_export.step

3. With panelized design active, run BoxSlicer
   └─ Analyzes each panel's dimensions
   └─ Fits to SHEET_CLASSES (STD_4x8, EXT_4x10, etc.)
   └─ Calculates lamination (e.g., 2× layers for deep panels)
   └─ Outputs: 6 boxes ready for nesting

4. Run foam_cam_template (with same design or re-import)
   └─ Nests 6 rectangular boxes on 2-3 sheets
   └─ Minimizes waste and gluing
   └─ Creates SHEET_1, SHEET_2, etc.
   └─ Ready for CNC toolpath generation

5. Generate CAM toolpaths in Fusion
   └─ Supports parallel processing (multiple sheets)
   └─ Ready for Maslow CNC or other controllers
```

---

## Manufacturing Benefits

| Aspect | Panel-First Approach |
|--------|----------------------|
| **Part Count** | Fewer, larger pieces → easier assembly |
| **Gluing** | Reduced panel seams → stronger structure |
| **Nesting** | Rectangular boxes → better utilization |
| **CNC Time** | Larger pieces → longer cuts, fewer tool changes |
| **Waste** | Optimized stock fit → less scrap |
| **Flexibility** | Lamination → variable thickness without custom stock |

---

## Troubleshooting

**FoamPanelizer fails:**
- Check `TARGET_KEYWORDS` match your component name
- Ensure export folder exists: `C:\temp\`
- Check log: `fusion_cam_slicer.txt`

**BoxSlicer finds no panels:**
- Ensure panel bodies have names containing: TOP, LEFT, RIGHT, REAR, FRONT, or BOTTOM
- Run FoamPanelizer first to extract panels
- Check log for body analysis details

**Nesting produces many sheets:**
- Reduce part sizes in BoxSlicer (change panel slicing strategy)
- Increase sheet sizes in `SHEET_CLASSES`
- Enable `PACKING_ALLOW_CROSS_CLASS` to use larger sheets

**Wrong dimensions:**
- Verify foam thickness in Config: `FOAM_THICKNESS_MM`
- Check lamination is enabled if panels are thick
- Review log for bounding box analysis

---

## Configuration Files

**Main Config:** `cam/common/config.py`
- Stock sizes, foam thickness, packing strategy
- Shared across all scripts

**Scripts:**
- `FoamPanelizer.py` - Panel extraction targets and export
- `box_slicer.py` - Panel analysis and fitting logic
- `foam_cam_template.py` - Nesting and CAM generation

**Logging:**
- Separate log files per process
- Timestamped folders for easy archival

---

## Future Enhancements

- [ ] Interactive panel selection GUI in FoamPanelizer
- [ ] Custom slicing patterns (grid, radial, structural zones)
- [ ] Assembly drawings and BOM generation
- [ ] Glue edge visualization
- [ ] Lamination bond line strength analysis
