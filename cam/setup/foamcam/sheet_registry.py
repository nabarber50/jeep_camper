# cam/setup/foamcam/sheet_registry.py
"""Global registry to track sheet layout and part names for NC labeling."""

# Global mapping: setup_name -> list of {name, width, height} dicts
_SHEET_PART_REGISTRY = {}

def register_sheet_parts(setup_name: str, part_info_list: list):
    """Register parts for a sheet setup.
    
    Args:
        setup_name: Name of the CAM setup
        part_info_list: List of dicts with {name, width, height} for each part
    """
    _SHEET_PART_REGISTRY[setup_name] = part_info_list

def get_sheet_parts(setup_name: str) -> list:
    """Get registered parts for a sheet setup.
    
    Returns:
        List of dicts with {name, width, height} for each part, or empty list if not found
    """
    return _SHEET_PART_REGISTRY.get(setup_name, [])

def clear_registry():
    """Clear the registry (call at start of each run)."""
    _SHEET_PART_REGISTRY.clear()

