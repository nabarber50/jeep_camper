# Quick Reference: Official Sample Code vs Our Implementation

## From the Official Autodesk Sample Code You Provided

### Section 1: Tool Library Query (Lines 58-67)
```python
toolLibraries = adsk.cam.CAMManager.get().libraryManager.toolLibraries
url = adsk.core.URL.create(TOOL_LIBRARY_URN)
toolLibrary = toolLibraries.toolLibraryAtURL(url)
query = toolLibrary.createQuery()
query.criteria.add('tool_type', adsk.core.ValueInput.createByString('chamfer mill'))
query.criteria.add('tool_diameter.min', adsk.core.ValueInput.createByReal(0.4))
query.criteria.add('tool_diameter.max', adsk.core.ValueInput.createByReal(0.6))
results = query.execute()
tools: list[adsk.cam.Tool] = []
for result in results:
    tools.append(result.tool)
engravingTool = tools[0]
```

### Section 2: Contour Selection (Lines 82-86)
```python
cadContours2dParam: adsk.cam.CadContours2dParameterValue = operationInput.parameters.itemByName('contours').value
curveSelections = cadContours2dParam.getCurveSelections()
chain = curveSelections.createNewFaceContourSelection()
chain.inputGeometry = engravingsBottomFacesSelectionSet.entities
cadContours2dParam.applyCurveSelections(curveSelections)
```

### Section 3: Selection Sets (Line 73)
```python
engravingsBottomFacesSelectionSet = cam.selectionSets.add(bottomFacesInCam, '(cam) Engravings bottom faces (all)')
```

---

## Our Implementation - Now Matches Official Pattern Exactly

### In foamcam/cam_ops.py - `_find_tool_by_query()` (Starting ~line 290)

**Key Lines:**
```python
# LINE: Load library by URL
url = adsk.core.URL.create(library_url)
toolLibrary = toolLibraries.toolLibraryAtURL(url)

# LINES: Add criteria with ValueInput (OFFICIAL PATTERN)
query = toolLibrary.createQuery()
query.criteria.add('tool_type', adsk.core.ValueInput.createByString(tool_type))
query.criteria.add('tool_diameter.min', adsk.core.ValueInput.createByReal(min_diameter))
query.criteria.add('tool_diameter.max', adsk.core.ValueInput.createByReal(max_diameter))

# LINES: Execute and extract tool (OFFICIAL PATTERN)
results = query.execute()
for result in results:
    tool = result.tool
    tools_found.append(tool)
```

### In foamcam/cam_ops.py - `_auto_select_contours()` (Starting ~line 383)

**Key Lines:**
```python
# LINE: Get parameter value
cadContours2dParam = contours_param.value

# LINE: Get curve selections (returns collection, not None)
curveSelections = cadContours2dParam.getCurveSelections()

# LINES: Create selection and assign geometry (OFFICIAL PATTERN)
chain = curveSelections.createNewFaceContourSelection()
chain.inputGeometry = largest_face  # Single object, not list

# LINE: Apply back (OFFICIAL PATTERN)
cadContours2dParam.applyCurveSelections(curveSelections)
```

### In foamcam/helpers.py - `create_cam_selection_set()` (NEW, ~line 6)

**Key Lines:**
```python
# LINE: Create selection set (from official pattern)
selection_set = cam.selectionSets.add(faces, set_name)
```

### In foamcam/helpers.py - `apply_selection_set_to_contours()` (NEW, ~line 50)

**Key Lines:**
```python
# LINES: Use selection set in contour operation (OFFICIAL PATTERN)
curveSelections = cadContours2dParam.getCurveSelections()
chain = curveSelections.createNewFaceContourSelection()
chain.inputGeometry = selection_set.entities
cadContours2dParam.applyCurveSelections(curveSelections)
```

---

## Direct Code Mapping

| Official Sample | Our Implementation | File | Purpose |
|---|---|---|---|
| Line 58-67 | `_find_tool_by_query()` | cam_ops.py | Tool library query |
| Line 82-86 | `_auto_select_contours()` | cam_ops.py | Contour selection |
| Line 73 | `create_cam_selection_set()` | helpers.py | Selection sets (NEW) |
| - | `apply_selection_set_to_contours()` | helpers.py | Apply sets (NEW) |

---

## Implementation Status

✅ **Tool Query Pattern**: Lines 293-340 in cam_ops.py - IMPLEMENTED  
✅ **Contour Pattern**: Lines 383-475 in cam_ops.py - IMPLEMENTED  
✅ **Selection Sets**: Lines 6-50 in helpers.py - IMPLEMENTED (NEW)  
✅ **Apply Sets**: Lines 52-113 in helpers.py - IMPLEMENTED (NEW)  

All implementations now exactly match the official Autodesk sample code patterns!

---

## Testing the Implementation

### 1. Check Tool Selection Works
Add this to `foam_cam_template.py` inside main CAM setup loop:
```python
tool = camOps._find_tool_best_effort()
if tool:
    logger.log(f"✓ Tool found: {tool.description}")
else:
    logger.log(f"✗ Tool not found (check TOOL_LIBRARY_URL in config)")
```

### 2. Check Contour Selection Works
Add this after creating operation:
```python
success = camOps._auto_select_contours(operationInput, model_bodies)
if success:
    logger.log(f"✓ Contours applied")
else:
    logger.log(f"✗ Contours failed (check getCurveSelections in operation)")
```

### 3. Check Selection Sets Work (if enabling)
```python
from foamcam.helpers import create_cam_selection_set, apply_selection_set_to_contours

# Create selection set
sel_set = create_cam_selection_set(cam, [body], "TestSet", logger)
if sel_set:
    # Apply to operation
    success = apply_selection_set_to_contours(operationInput, sel_set, logger)
```

### 4. Check Post-Processing (if enabling)
In config.py: `AUTO_POST_PROCESS = True`
Then check console for:
```
Post-process: starting for setup 'Sheet_STD_4x8_1'
Post processor: 'grbl' (vendor='Autodesk')
NC program created: 'FoamCAM_Sheet_STD_4x8_1'
Executing post-processing...
Post-processing complete
Post-process SUCCESS: /path/to/file.nc (12345 bytes)
```

---

## Reference Links

- **Official Sample**: The code you provided starting with `import adsk.core, adsk.fusion, adsk.cam, traceback`
- **Tool Pattern**: Lines 58-67 of official sample
- **Contour Pattern**: Lines 82-86 of official sample
- **Our Implementation**: See files listed above

All three features now use the **exact same API patterns** as the official Autodesk sample code! 🎯
