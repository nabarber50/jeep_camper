# CAM Improvements - Based on Official Autodesk Sample Code

## Summary
Refactored tool selection, contour selection, and post-processing to use the official Autodesk SDK patterns from the provided sample code. All implementations now match the working example patterns.

## Changes Made

### 1. Tool Selection (`cam_ops.py` - `_find_tool_by_query`)
**Official Pattern Used:** From Autodesk sample code (lines 58-67)

**Key Changes:**
- Using `query.criteria.add()` with `ValueInput` objects (not direct attribute assignment)
- `query.criteria.add('tool_type', adsk.core.ValueInput.createByString(tool_type))`
- `query.criteria.add('tool_diameter.min', adsk.core.ValueInput.createByReal(min_diameter))`
- Executing query with `results = query.execute()`
- Extracting tool with `tool = result.tool` from each result in results list

**Improvements:**
- Comprehensive error checking at each step (CAMManager load, library load, criteria setup, query execution)
- Detailed logging showing which step failed if tool selection doesn't work
- Better handling of tool.description attribute errors
- Added traceback on exception for debugging

### 2. Contour Selection (`cam_ops.py` - `_auto_select_contours`)
**Official Pattern Used:** From Autodesk sample code (lines 82-86)

**Key Changes:**
- `curveSelections = cadContours2dParam.getCurveSelections()` returns collection, not None
- Creates new selections: `chain = curveSelections.createNewFaceContourSelection()`
- Sets geometry directly: `chain.inputGeometry = largest_face` (not as a list)
- Applies back: `cadContours2dParam.applyCurveSelections(curveSelections)`

**Improvements:**
- Uses correct API pattern - getCurveSelections() returns empty collection to add to
- Assigns geometry as single objects (not lists)
- Tries face contours first (preferred for 2D profiling), falls back to edge chains
- Detailed logging at each step for debugging

### 3. Selection Sets Support (`helpers.py` - New Functions)
**Official Pattern Used:** From Autodesk sample code (line 73)

**New Helper Functions:**

#### `create_cam_selection_set(cam, bodies, set_name, logger)`
- Creates reusable geometry selections from model body faces
- Usage: `selection_set = create_cam_selection_set(cam, [body], 'MyGeometry', logger)`
- Returns SelectionSet object with pre-selected geometry

#### `apply_selection_set_to_contours(operation_input, selection_set, logger)`
- Applies pre-created selection set to operation contours
- More reliable than manual geometry assignment
- Uses the official pattern from sample code
- Returns True/False for success/failure

**Benefits:**
- Selection sets are reusable across multiple operations
- More reliable geometry assignment than direct geometry references
- Enables per-operation tool mapping (Task 7)
- Matches official Autodesk implementation patterns

### 4. Post-Processing Improvements (`helpers.py` - `post_process_setup`)

**Improvements:**
- Verify setup has operations before attempting post-processing
- Detailed logging showing post-processor name, vendor, file extension
- Check PostProcessOptions.create() result before using
- Verify NC output file was actually created (file existence check)
- Log file size when successful
- Added traceback on exception for debugging
- Better error messages at each phase

## Testing Recommendations

1. **Tool Selection**: Set `TOOL_LIBRARY_URL` and `TOOL_NAME_SEARCH` in config.py, check console for:
   - "Tool query: Loaded library..." 
   - "Tool query: Criteria: type=..." 
   - "Tool query: Success: Found..." or specific error

2. **Contour Selection**: Review console for:
   - "Face contour: added from body..." (success)
   - "getCurveSelections() failed..." (if error)
   - "Contour selection: applied geometry successfully"

3. **Post-Processing**: Enable `AUTO_POST_PROCESS=True` in config.py, check for:
   - "Post-process: starting for setup..."
   - "Post processor: '...'"
   - "NC program created:..."
   - "Post-processing complete"
   - "Post-process SUCCESS: ..." with file size

## Known Limitations

1. **Contour Selection**: `getCurveSelections()` may still be context-dependent - if it fails, manual CAM UI selection is required
2. **Tool Selection**: Library access depends on Fusion 360 runtime - test with actual FusionCAM operations
3. **Post-Processing**: NC file generation depends on post-processor availability and format

## Files Modified

- `cam/setup/foamcam/cam_ops.py`:
  - `_find_tool_by_query()` - Complete rewrite with official pattern
  - `_auto_select_contours()` - Complete rewrite with official pattern

- `cam/setup/foamcam/helpers.py`:
  - `create_cam_selection_set()` - NEW
  - `apply_selection_set_to_contours()` - NEW  
  - `post_process_setup()` - Enhanced error handling and logging

## Next Steps

1. Run script with test Fusion 360 document
2. Review console output for any remaining errors
3. If specific API calls still fail, check:
   - Fusion 360 CAM workspace is active
   - Operations have proper geometry selections
   - Post-processor library is available
4. Can now proceed with Tasks 2, 5, 6, 7 (density, labeling, tiny parts, per-op tools)
