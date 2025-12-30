# API Pattern Comparison - Official vs Previous Implementation

## 1. TOOL LIBRARY QUERY

### Official Pattern (From Autodesk Sample Code)
```python
# Load tool library
toolLibraries = adsk.cam.CAMManager.get().libraryManager.toolLibraries
url = adsk.core.URL.create(TOOL_LIBRARY_URN)
toolLibrary = toolLibraries.toolLibraryAtURL(url)

# Create query
query = toolLibrary.createQuery()
query.criteria.add('tool_type', adsk.core.ValueInput.createByString('chamfer mill'))
query.criteria.add('tool_diameter.min', adsk.core.ValueInput.createByReal(0.4))
query.criteria.add('tool_diameter.max', adsk.core.ValueInput.createByReal(0.6))

# Execute and extract tools
results = query.execute()
tools: list[adsk.cam.Tool] = []
for result in results:
    tools.append(result.tool)
engravingTool = tools[0]
```

### Previous Implementation (WRONG)
```python
# ❌ Trying to set criteria as attributes (doesn't work)
query.criteria.tool_type = tool_type
query.criteria.diameter_min = min_diameter
# This pattern doesn't match the API
```

### Current Implementation (FIXED)
```python
# ✓ Using official pattern with ValueInput
query = toolLibrary.createQuery()
query.criteria.add('tool_type', adsk.core.ValueInput.createByString(tool_type))
query.criteria.add('tool_diameter.min', adsk.core.ValueInput.createByReal(min_diameter))
query.criteria.add('tool_diameter.max', adsk.core.ValueInput.createByReal(max_diameter))

results = query.execute()
tools_found = []
for result in results:
    tool = result.tool  # ✓ Extract tool from result
    tools_found.append(tool)
```

---

## 2. CONTOUR SELECTION

### Official Pattern (From Autodesk Sample Code)
```python
# Get the parameter value
cadContours2dParam: adsk.cam.CadContours2dParameterValue = operationInput.parameters.itemByName('contours').value

# Get curve selections (empty collection)
curveSelections = cadContours2dParam.getCurveSelections()

# Create new selection and assign geometry
chain = curveSelections.createNewFaceContourSelection()
chain.inputGeometry = engravingsBottomFacesSelectionSet.entities

# Apply back to parameter
cadContours2dParam.applyCurveSelections(curveSelections)
```

### Previous Implementation (WRONG)
```python
# ❌ Trying to assign list to inputGeometry
chains = contours_value.getCurveSelections()
if not chains:
    # Incorrectly assuming None means failure
    return False

chain = chains.createNewFaceContourSelection()
chain.inputGeometry = [faces[0]]  # ❌ Assigning list instead of object
```

### Current Implementation (FIXED)
```python
# ✓ getCurveSelections() returns empty collection (not None)
curveSelections = cadContours2dParam.getCurveSelections()

# ✓ Create new selection
chain = curveSelections.createNewFaceContourSelection()

# ✓ Assign single geometry object (not list)
chain.inputGeometry = largest_face  # Not [largest_face]

# ✓ Apply back
cadContours2dParam.applyCurveSelections(curveSelections)
```

---

## 3. SELECTION SETS (NEW - From Official Pattern)

### Official Pattern (From Autodesk Sample Code)
```python
# Create selection set in CAM space
engravingsBottomFacesSelectionSet = cam.selectionSets.add(
    bottomFacesInCam, 
    '(cam) Engravings bottom faces (all)'
)

# Use selection set directly in operation
chain.inputGeometry = engravingsBottomFacesSelectionSet.entities
```

### New Implementation (ADDED)
```python
# ✓ Create reusable selection set from geometry
selection_set = create_cam_selection_set(cam, [body], 'GeometrySet', logger)

# ✓ Use selection set in contour operation
apply_selection_set_to_contours(operation_input, selection_set, logger)

# Implementation internally:
chain.inputGeometry = selection_set.entities
```

---

## 4. POST-PROCESSING

### Official Pattern (Implied in Framework)
```python
# Create post configuration from library
postQuery = postLibrary.createQuery(adsk.cam.LibraryLocations.Fusion360LibraryLocation)
postQuery.vendor = vendor
postConfigs = postQuery.execute()

# Create NC program
ncInput = cam.ncPrograms.createInput()
newProgram = cam.ncPrograms.add(ncInput)
newProgram.postConfiguration = post_config

# Post process
post_options = adsk.cam.NCProgramPostProcessOptions.create()
newProgram.postProcess(post_options)

# Verify result
if os.path.exists(output_path):
    # Success
```

### Previous Implementation
```python
# Basic implementation without verification
post_options = adsk.cam.NCProgramPostProcessOptions.create()
nc_program.postProcess(post_options)
# No verification that file was created
```

### Current Implementation (IMPROVED)
```python
# ✓ Full verification at each step
post_options = adsk.cam.NCProgramPostProcessOptions.create()
if not post_options:
    # Handle case where creation fails
    post_options = adsk.cam.NCProgramPostProcessOptions()

logger.log(f"Executing post-processing...")
nc_program.postProcess(post_options)

# ✓ Verify file was actually created
if os.path.exists(output_path):
    file_size = os.path.getsize(output_path)
    logger.log(f"Post-process SUCCESS: {output_path} ({file_size} bytes)")
else:
    logger.log(f"Post-process: file was not created")
```

---

## Key Takeaways

### Tool Selection
- **MUST** use `query.criteria.add()` with `ValueInput` objects
- **MUST** extract tool with `result.tool` from results
- Cannot set criteria as direct attributes

### Contour Selection
- `getCurveSelections()` returns collection (not None)
- Assign single objects to `inputGeometry` (not lists)
- Always call `applyCurveSelections()` at the end

### Selection Sets
- Create once with `cam.selectionSets.add()`
- Reference multiple times with `selection_set.entities`
- More reliable than passing raw geometry

### Post-Processing
- Always verify NC file exists after post-processing
- Check both creation and file existence
- Log all phases for debugging
