# Fusion 360 Design History & Direct Modeling - Python API Reference

## Overview
This document provides concrete code examples for detecting and controlling design history mode (parametric vs direct modeling) in Fusion 360 Python scripts.

---

## 1. DETECTING DESIGN MODE (Parametric vs Direct Modeling)

### Key Property: `Design.designType`

```python
import adsk.fusion

# Get the active design
app = adsk.core.Application.get()
doc = app.activeDocument
design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))

# Check design type
design_type = design.designType

# Compare against known types
is_parametric = design_type == adsk.fusion.DesignTypes.ParametricDesignType
is_direct_modeling = design_type == adsk.fusion.DesignTypes.DirectModelingDesignType
```

### Safe Detection Pattern (With Fallback)

```python
def detect_design_mode(design: adsk.fusion.Design) -> str:
    """
    Safely detect whether design is in parametric or direct modeling mode.
    Returns: 'parametric', 'direct_modeling', or 'unknown'
    """
    try:
        design_type = getattr(design, 'designType', None)
        
        if design_type is None:
            return 'unknown'
        
        # Check against known types
        if design_type == getattr(adsk.fusion.DesignTypes, 'ParametricDesignType', None):
            return 'parametric'
        elif design_type == getattr(adsk.fusion.DesignTypes, 'DirectModelingDesignType', None):
            return 'direct_modeling'
        else:
            return 'unknown'
    except Exception as e:
        print(f"Error detecting design mode: {e}")
        return 'unknown'
```

---

## 2. DESIGN MODE IMPACT ON API OPERATIONS

### BaseFeature Limitations

**Parametric Mode:**
- ✅ BaseFeatures ARE supported
- ✅ Can use `features.baseFeatures.add()` to create explicit base features
- ✅ Can pass base feature to `bRepBodies.add(body, baseFeature)`

**Direct Modeling Mode:**
- ❌ BaseFeatures are NOT supported
- ❌ Cannot create `baseFeatures` - operations fail with "not supported in direct modeling"
- ❌ `copyToComponent()` is preferred method for inserting bodies
- ✅ Can use direct `bRepBodies.add(body)` without base feature

### Code Example: Conditional Copy Strategy

```python
def copy_body_with_mode_awareness(
    source_body: adsk.fusion.BRepBody,
    target_component: adsk.fusion.Component,
    base_feature: adsk.fusion.BaseFeature = None
) -> adsk.fusion.BRepBody:
    """
    Copy a body using the appropriate method for the current design mode.
    
    Args:
        source_body: The body to copy
        target_component: The target component to insert into
        base_feature: Optional base feature (only used in parametric mode)
    
    Returns:
        The new body in the target component, or None on failure
    """
    
    # Get the parent design from the target component
    design = getattr(target_component, 'parentDesign', None)
    if not design:
        return None
    
    # Detect design type
    design_type = getattr(design, 'designType', None)
    is_parametric = design_type == getattr(adsk.fusion.DesignTypes, 'ParametricDesignType', None)
    
    # Strategy 1: Parametric mode - try base feature first
    if is_parametric and base_feature:
        try:
            return target_component.bRepBodies.add(source_body, base_feature)
        except Exception as e:
            print(f"BaseFeature add failed: {e}")
    
    # Strategy 2: Parametric mode - create implicit base feature
    if is_parametric:
        try:
            bf = target_component.features.baseFeatures.add()
            bf.startEdit()
            result = bf.bRepBodies.add(source_body)
            bf.finishEdit()
            return result
        except Exception as e:
            print(f"Implicit BaseFeature creation failed: {e}")
    
    # Strategy 3: Direct modeling mode (or fallback) - use copyToComponent
    try:
        return source_body.copyToComponent(target_component)
    except Exception as e:
        print(f"copyToComponent failed: {e}")
    
    # Strategy 4: Last resort - direct add without base feature
    try:
        return target_component.bRepBodies.add(source_body)
    except Exception as e:
        print(f"Direct add failed: {e}")
        return None
```

---

## 3. DETECTING HISTORY EDITING MODE

### Related Property: `Design.designHistoryEditingEnabled` (if available)

**Note:** The specific property name may vary by API version. Use safe attribute access:

```python
def is_history_editing_enabled(design: adsk.fusion.Design) -> bool:
    """
    Check if design history editing is enabled.
    This property may not be available in all Fusion 360 versions.
    """
    try:
        # Try direct property access
        return getattr(design, 'designHistoryEditingEnabled', False)
    except Exception:
        # Fallback: infer from designType
        design_type = getattr(design, 'designType', None)
        # Parametric = history enabled; Direct = history disabled
        return design_type == getattr(adsk.fusion.DesignTypes, 'ParametricDesignType', None)
```

---

## 4. WORKSPACE CONTEXT: YOUR IMPLEMENTATION

### Location: [foamcam/nesting.py](foamcam/nesting.py#L265-L275)

Your codebase already implements design mode detection:

```python
# From your nesting.py (lines 265-275)

# Get the parent design
design_obj = getattr(target_comp, 'parentDesign', None) or getattr(self, 'design', None)
design_type = getattr(design_obj, 'designType', None)

# Check if parametric
is_parametric = design_type == getattr(adsk.fusion.DesignTypes, 'ParametricDesignType', None)

# If caller provided a base feature (rare), try it first in parametric mode only
if base_feat and is_parametric:
    try:
        return target_comp.bRepBodies.add(tmp, base_feat)
    except Exception as e:
        # Handle error...
```

### Your Error Handling: [foamcam/nesting.py](foamcam/nesting.py#L338-L346)

Your code also gracefully handles direct modeling limitations:

```python
# From your nesting.py (lines 338-346)

# Detect when base features are blocked in direct modeling
if "not supported in direct modeling" in str(e2).lower():
    blocked_msgs.append("base feature rejected in direct modeling")

if blocked_msgs:
    self._copy_blocked_reason = (
        "Insertion failed: " + "; ".join(blocked_msgs) + ". "
        "Run from the desktop Modeling workspace (history off/direct) or enable base features in parametric mode, then rerun."
    )
    raise RuntimeError(self._copy_blocked_reason)
```

---

## 5. COMPLETE WRAPPER FUNCTION

### All-in-One Design Mode Management

```python
class DesignModeManager:
    """
    Manage design mode detection and mode-aware operations.
    """
    
    def __init__(self, design: adsk.fusion.Design, logger=None):
        self.design = design
        self.logger = logger
    
    @property
    def mode(self) -> str:
        """Return 'parametric', 'direct_modeling', or 'unknown'"""
        try:
            design_type = getattr(self.design, 'designType', None)
            
            if design_type == getattr(adsk.fusion.DesignTypes, 'ParametricDesignType', None):
                return 'parametric'
            elif design_type == getattr(adsk.fusion.DesignTypes, 'DirectModelingDesignType', None):
                return 'direct_modeling'
            else:
                return 'unknown'
        except Exception as e:
            if self.logger:
                self.logger.log(f"Error detecting mode: {e}")
            return 'unknown'
    
    def is_parametric(self) -> bool:
        """Check if design is in parametric mode"""
        return self.mode == 'parametric'
    
    def is_direct_modeling(self) -> bool:
        """Check if design is in direct modeling mode"""
        return self.mode == 'direct_modeling'
    
    def supports_base_features(self) -> bool:
        """Check if current mode supports base features"""
        return self.is_parametric()
    
    def copy_body_safe(
        self,
        source_body: adsk.fusion.BRepBody,
        target_component: adsk.fusion.Component,
        base_feature: adsk.fusion.BaseFeature = None
    ) -> adsk.fusion.BRepBody:
        """Copy body using appropriate method for current mode"""
        
        mode = self.mode
        
        if self.logger:
            self.logger.log(f"Copying body in {mode} mode")
        
        # Parametric: try base feature first
        if self.is_parametric() and base_feature:
            try:
                result = target_component.bRepBodies.add(source_body, base_feature)
                if self.logger:
                    self.logger.log("✓ Body copied with explicit base feature")
                return result
            except Exception as e:
                if self.logger:
                    self.logger.log(f"⚠ Explicit base feature failed: {e}")
        
        # Parametric: create implicit base feature
        if self.is_parametric():
            try:
                bf = target_component.features.baseFeatures.add()
                bf.startEdit()
                result = bf.bRepBodies.add(source_body)
                bf.finishEdit()
                if self.logger:
                    self.logger.log("✓ Body copied with implicit base feature")
                return result
            except Exception as e:
                if self.logger:
                    self.logger.log(f"⚠ Implicit base feature failed: {e}")
        
        # Direct modeling or fallback: use copyToComponent
        try:
            result = source_body.copyToComponent(target_component)
            if result:
                if self.logger:
                    self.logger.log("✓ Body copied via copyToComponent")
                return result
        except Exception as e:
            if self.logger:
                self.logger.log(f"⚠ copyToComponent failed: {e}")
        
        # Last resort: direct add
        try:
            result = target_component.bRepBodies.add(source_body)
            if self.logger:
                self.logger.log("✓ Body copied via direct add")
            return result
        except Exception as e:
            if self.logger:
                self.logger.log(f"✗ All copy methods failed: {e}")
            return None
```

---

## 6. KEY PROPERTY & METHOD REFERENCE

| Property/Method | Object | Returns | Notes |
|---|---|---|---|
| `design.designType` | `adsk.fusion.Design` | `DesignTypes` enum | **Key property** - main way to detect mode |
| `adsk.fusion.DesignTypes.ParametricDesignType` | Enum | Value | Compare against this value |
| `adsk.fusion.DesignTypes.DirectModelingDesignType` | Enum | Value | Compare against this value |
| `design.designHistoryEditingEnabled` | `adsk.fusion.Design` | `bool` | May not be available in all versions |
| `component.parentDesign` | `adsk.fusion.Component` | `adsk.fusion.Design` | Get parent design from component |
| `features.baseFeatures.add()` | Parametric only | `BaseFeature` | ❌ Fails in direct modeling |
| `body.copyToComponent()` | Both modes | `BRepBody` | ✅ Works in direct modeling |
| `bRepBodies.add(body, baseFeature)` | Parametric | `BRepBody` | Use explicit base feature if available |
| `bRepBodies.add(body)` | Both modes | `BRepBody` | Works without base feature (fallback) |

---

## 7. USAGE EXAMPLE IN YOUR CONTEXT

```python
# In your foam_cam_template.py or similar:

design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))

# Create manager
mode_manager = DesignModeManager(design, logger=_logger)

_logger.log(f"Design mode: {mode_manager.mode}")
_logger.log(f"Supports base features: {mode_manager.supports_base_features()}")

# Use safe copy method when inserting bodies
new_body = mode_manager.copy_body_safe(
    source_body=foam_body,
    target_component=sheet_component,
    base_feature=optional_base_feature
)
```

---

## 8. COMMON ERRORS & SOLUTIONS

### Error: "not supported in direct modeling"
- **Cause:** Attempting to use `baseFeatures` in direct modeling mode
- **Solution:** Check `is_direct_modeling()` before using base features; use `copyToComponent()` instead

### Error: "targetbasefeature is required"
- **Cause:** Some geometries require base feature in parametric mode
- **Solution:** Try creating implicit base feature with `features.baseFeatures.add()`

### Error: "not supported on server"
- **Cause:** Attempting `copyToComponent()` in server execution context
- **Solution:** Use direct `bRepBodies.add()` as fallback

### designType is None
- **Cause:** Design object is invalid or not fully loaded
- **Solution:** Verify design was cast correctly; check `adsk.fusion.Design.cast()` result

---

## 9. REFERENCES IN YOUR CODEBASE

- **nesting.py**: Design mode detection and conditional insertion ([lines 265-346](foamcam/nesting.py#L265-L346))
- **config.py**: Configuration for Maslow compensation and mode-related settings ([lines 141-150](foamcam/config.py#L141-L150))
- **API_PATTERNS.md**: Official pattern documentation in your workspace

---

## 10. NOTES & BEST PRACTICES

1. **Always use safe attribute access** with `getattr()` for backwards compatibility
2. **Use enums from `adsk.fusion.DesignTypes`** - don't hardcode string comparisons
3. **Try multiple fallback strategies** when inserting bodies (base feature → copyToComponent → direct add)
4. **Log the detected mode** at script startup for debugging
5. **Design history status cannot be changed programmatically** - it's set in the file/UI
6. Your implementation in `nesting.py` is already following best practices!
