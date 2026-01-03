# Quick Reference: Design History & Direct Modeling Detection

## TL;DR - Copy & Paste

```python
# 1. Detect design mode
design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
is_parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
is_direct = design.designType == adsk.fusion.DesignTypes.DirectModelingDesignType

# 2. Safe detection with fallback
def check_mode(design):
    dt = getattr(design, 'designType', None)
    if dt == getattr(adsk.fusion.DesignTypes, 'ParametricDesignType', None):
        return 'parametric'
    elif dt == getattr(adsk.fusion.DesignTypes, 'DirectModelingDesignType', None):
        return 'direct_modeling'
    return 'unknown'

# 3. Copy body with mode awareness
def copy_body_safe(source_body, target_comp):
    is_param = target_comp.parentDesign.designType == adsk.fusion.DesignTypes.ParametricDesignType
    
    if is_param:
        try:
            bf = target_comp.features.baseFeatures.add()
            bf.startEdit()
            result = bf.bRepBodies.add(source_body)
            bf.finishEdit()
            return result
        except:
            pass
    
    try:
        return source_body.copyToComponent(target_comp)  # Works in both modes
    except:
        return target_comp.bRepBodies.add(source_body)   # Fallback
```

## Key Properties

| What | Code | Result |
|---|---|---|
| Check if parametric | `design.designType == DesignTypes.ParametricDesignType` | `bool` |
| Check if direct | `design.designType == DesignTypes.DirectModelingDesignType` | `bool` |
| Get from component | `target_comp.parentDesign` | `Design` object |

## What Works Where

| Operation | Parametric | Direct Modeling |
|---|:---:|:---:|
| `baseFeatures.add()` | ✅ | ❌ |
| `bRepBodies.add(body, baseFeature)` | ✅ | ❌ |
| `body.copyToComponent(comp)` | ✅ | ✅ |
| `bRepBodies.add(body)` | ✅ | ✅ |

## Error Patterns

| Error | Cause | Fix |
|---|---|---|
| "not supported in direct modeling" | Using baseFeatures | Use `copyToComponent()` |
| "targetbasefeature is required" | Geometry needs base feature | Create with `baseFeatures.add()` |
| designType is None | Design not loaded | Verify `Design.cast()` succeeded |

## Your Implementation

Your `foamcam/nesting.py` ([lines 265-275](foamcam/nesting.py#L265-L275)) already does this correctly:

```python
design_type = getattr(design_obj, 'designType', None)
is_parametric = design_type == getattr(adsk.fusion.DesignTypes, 'ParametricDesignType', None)

if is_parametric:
    # Use baseFeatures
else:
    # Use copyToComponent()
```

Perfect pattern! ✓
