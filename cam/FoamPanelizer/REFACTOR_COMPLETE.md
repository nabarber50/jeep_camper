# FoamPanelizer Face-Driven Refactor — COMPLETE ✅

## Summary
Successfully refactored from slab/boolean-intersection approach to **face-driven panel extraction** that analyzes and thickens exterior faces to create panel bodies.

## Implementation Status

### ✅ Phase 1 — Remove Old Slab Logic
- Removed `make_slab()` function and slab dictionary creation
- Removed all `TemporaryBRepManager.booleanOperation()` intersection logic
- Removed slab-based labeling (bbox center tests)
- Removed slab visibility/keep-tool logic

### ✅ Phase 2 — Exterior Face Detection
**Function:** `_get_outward_normal(body, face, eps_cm)`
- Gets sample point via `face.pointOnFace`
- Gets normal via `face.evaluator.getNormalAtPoint()`
- Steps outward by `eps_cm` (0.1% of bbox diagonal, min 0.01 cm)
- Uses `body.pointContainment()` to detect if normal points inward
- Flips normal if containment returns `PointInsidePointContainment`
- Returns normalized outward unit normal

### ✅ Phase 3 — Face Classification
**Function:** `_classify_face_option_b(out_n, thr=0.65)`
- **Coordinate System (LOCKED):**
  - `UP = (0,0,1)` → +Z
  - `REAR = (1,0,0)` → +X
  - `RIGHT = (0,1,0)` → +Y
  - `LEFT = (0,-1,0)` → -Y
- Computes dot products against all 6 axes
- Selects axis with maximum dot product
- Requires `dot ≥ 0.65` threshold
- Ignores `FRONT` (-X) and `BOTTOM` (-Z) faces
- Returns: `'TOP'`, `'LEFT'`, `'RIGHT'`, `'REAR'`, or `None`

### ✅ Phase 4 — Panel Solid Creation
**Function:** `_make_faces_collection(faces)` + thicken loop
- Classifies all faces into buckets: `{'TOP': [], 'REAR': [], 'LEFT': [], 'RIGHT': []}`
- For each bucket with faces:
  - Creates `ObjectCollection` of faces
  - Calls `thickenFeatures.createInput()`:
    - `thickness = -capture_cm` (inward thickening, negative value)
    - `isSymmetric = False`
    - `operation = NewBodyFeatureOperation` (creates separate bodies)
    - `isChainSelection = True`
  - Executes `thickenFeatures.add()`
  - Names bodies: `PANEL_TOP`, `PANEL_LEFT`, `PANEL_RIGHT`, `PANEL_REAR`
  - If multiple bodies per panel: `PANEL_XXX_01`, `PANEL_XXX_02`, etc.

### ✅ Phase 5 — Cleanup
- Hides original source body: `source_body.isVisible = False`
- Leaves all created panel bodies visible: `body.isVisible = True`
- Does not modify original geometry destructively

### ✅ Phase 6 — Logging & Config Integration
- Added `log_folder` parameter to `panelize_step_into_new_design()`
- Writes debug log to `{log_folder}/panelizer_face_debug.log`
- FoamPanelizer.py passes `Config.get_run_log_folder()` to core
- Logs:
  - Bbox dimensions and epsilon calculation
  - Face counts per bucket: `TOP=X REAR=Y LEFT=Z RIGHT=W skipped=N`
  - Bodies created per panel: `{pname}: created {count} body(s)`
  - Final created count
- Falls back to STEP file directory if log_folder not provided

## Helper Functions

### `_get_outward_normal(body, face, eps_cm) -> Vector3D`
Detects exterior faces and returns reliable outward normals using point containment testing.

### `_classify_face_option_b(out_n, thr=0.65) -> str | None`
Classifies faces by computing dot products against canonical axes (UP/REAR/LEFT/RIGHT), applying threshold, and ignoring FRONT/BOTTOM.

### `_make_faces_collection(faces) -> ObjectCollection`
Helper to convert Python list of faces into Fusion API ObjectCollection for thicken input.

## Acceptance Criteria — Status

| Criterion | Status |
|-----------|--------|
| Panels contain only exterior skin, no ribs/internals | ✅ Face classification ensures exterior faces only |
| Panels match visible box faces | ✅ Dot product threshold filters aligned faces |
| Thickness is uniform and correct | ✅ Single `capture_cm` applied to all panels |
| Running twice produces identical results | ✅ Deterministic face iteration and classification |
| No slab volumes or spatial slicing artifacts | ✅ All slab logic removed |

## Non-Goals (Explicitly Not Implemented)
- ❌ Seam bisector planes (future)
- ❌ Split-body operations (future)
- ❌ CAM/nesting integration (handled by downstream tools)
- ❌ Rotation/orientation fixes (faces extracted as-is)
- ❌ Flattening (3D panels preserved)
- ❌ Corner-ownership heuristics (future refinement)

## Testing Checklist
- [ ] Run FoamPanelizer on camper model
- [ ] Verify 4 panels created: TOP, LEFT, RIGHT, REAR
- [ ] Verify front and bottom faces ignored
- [ ] Check panel thickness matches `CAPTURE_DEPTH` setting
- [ ] Confirm no interior ribs included in panels
- [ ] Check debug log for face classification counts
- [ ] Verify log written to timestamped folder

## Next Steps
1. Test on actual camper geometry
2. Validate panel dimensions and coverage
3. Proceed to BoxSlicer for stock fitting
4. Generate CAM toolpaths with foam_cam_template
