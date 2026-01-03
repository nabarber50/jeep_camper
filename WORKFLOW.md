# FoamPanelizer CAM Workflow

The code implements a multi-stage workflow for manufacturing a camper from foam panels using CNC machining:

## Overall Purpose
Extract individual panels (top, sides, rear) from a 3D camper model in Fusion 360, then generate CNC toolpaths to cut those panels from foam blocks.

## Key Components

### 1. **FoamPanelizer** (cam/FoamPanelizer/)
- **Purpose**: Slice a 3D camper body into discrete flat panels for manufacturing
- **Process**: 
  - Takes the source camper solid body
  - Creates large slab volumes at each face location (TOP, LEFT, RIGHT, REAR)
  - Uses boolean intersection to extract only the geometry that overlaps each slab
  - Creates 4 separate body components representing panels
- **Coordinate System**: X = length (front-back), Y = width (left-right), Z = height
- **Output**: 4 panel bodies in new Fusion design (front and bottom intentionally left open)

### 2. **BoxSlicer** (cam/BoxSlicer/)
- **Purpose**: Fit each extracted panel into a rectangular foam stock block
- **Process**:
  - Analyzes each panel's bounding box dimensions
  - Calculates how many foam layers needed (for thick sections)
  - Determines optimal stock size to minimize waste
- **Output**: Panel dimension metadata for CAM operations

### 3. **foam_cam_template** (cam/setup/foam_cam_template.py)
- **Purpose**: Generate CNC toolpaths to machine the panels from foam blocks
- **Process** (foamcam module):
  - Sets up manufacturing workspace (WCS) and stock geometry
  - Creates roughing and finishing toolpaths (3D adaptive, horizontal, parallel)
  - Handles tool selection, feeds/speeds, nesting operations
  - Generates G-code (.nc files) for CNC machine
- **Output**: Manufacturing-ready toolpaths and G-code

### 4. **config.py** (cam/common/config.py)
- **Purpose**: Shared configuration and logging infrastructure
- **Key Functions**:
  - `get_desktop_path()`: Finds user's Desktop for log folders
  - `get_run_log_folder()`: Creates timestamped folders like `Desktop/fusion_cam_logs/20260103_143022/`
  - Defines log file paths used across all scripts
- **Used By**: All CAM scripts for consistent logging

## Workflow Sequence

```
3D Camper Model 
    ↓
[FoamPanelizer] → Extract 4 panel bodies
    ↓
[BoxSlicer] → Analyze panel dimensions, calculate stock
    ↓
[foam_cam_template] → Generate CNC toolpaths
    ↓
G-code → CNC machine cuts foam panels
```

## Current State
The panelizer successfully creates 4 panels using BRep boolean operations, but visual validation is needed to confirm the extracted geometry matches the expected panel layout.
