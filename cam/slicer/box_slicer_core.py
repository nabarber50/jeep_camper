"""
BoxSlicer Core - Fit panels to standard stock sizes and generate rectangular boxes.

Strategy:
1. Load stock dimensions from Config.SHEET_CLASSES
2. Analyze panel dimensions (W x H x D)
3. Fit each panel to available stock, allowing lamination if needed
4. Generate rectangular box specifications for nesting
5. Minimize small parts by maximizing use of standard stock pieces
"""

from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional
import math


@dataclass
class Stock:
    """Represents a stock material size."""
    name: str
    width_mm: float
    height_mm: float
    
    @property
    def area(self) -> float:
        return self.width_mm * self.height_mm
    
    def fits(self, w: float, h: float) -> bool:
        """Check if rectangle (w x h) fits in this stock."""
        return (w <= self.width_mm and h <= self.height_mm) or \
               (w <= self.height_mm and h <= self.width_mm)
    
    def fits_no_rotate(self, w: float, h: float) -> bool:
        """Check if rectangle fits without rotation."""
        return w <= self.width_mm and h <= self.height_mm


@dataclass
class Panel:
    """Represents a panel to be sliced."""
    name: str
    width_mm: float
    height_mm: float
    depth_mm: float  # Material thickness (will determine lamination)


@dataclass
class Box:
    """Represents a rectangular box (final cutting piece)."""
    name: str
    width_mm: float
    height_mm: float
    depth_mm: float  # Total thickness (may be laminated)
    lamination_layers: int  # Number of foam pieces to stack
    source_panel: str  # Which panel this came from
    
    @property
    def area(self) -> float:
        return self.width_mm * self.height_mm


class BoxSlicer:
    """Fit panels to standard stock and generate boxes for CNC."""
    
    def __init__(self, stocks: List[Stock], foam_thickness_mm: float, 
                 allow_lamination: bool = True, max_layers: int = 3):
        """
        Args:
            stocks: List of Stock dimensions
            foam_thickness_mm: Single foam piece thickness
            allow_lamination: Allow stacking multiple foam pieces
            max_layers: Maximum lamination layers
        """
        self.stocks = sorted(stocks, key=lambda s: s.area)  # Sort by area (small to large)
        self.foam_thickness = foam_thickness_mm
        self.allow_lamination = allow_lamination
        self.max_layers = max_layers
        self.panels: List[Panel] = []
        self.boxes: List[Box] = []
    
    def add_panel(self, name: str, width_mm: float, height_mm: float, depth_mm: float) -> None:
        """Add a panel to be sliced."""
        self.panels.append(Panel(name, width_mm, height_mm, depth_mm))
    
    def slice_all(self) -> Dict:
        """Slice all panels and return results."""
        self.boxes = []
        results = {
            "panels": [],
            "boxes": [],
            "summary": {
                "total_panels": len(self.panels),
                "total_boxes": 0,
                "total_laminations": 0,
                "warnings": []
            }
        }
        
        for panel in self.panels:
            panel_result = self._slice_panel(panel)
            results["panels"].append(panel_result)
            self.boxes.extend(panel_result["boxes"])
            results["summary"]["total_laminations"] += sum(b.lamination_layers - 1 for b in panel_result["boxes"])
        
        results["summary"]["total_boxes"] = len(self.boxes)
        return results
    
    def _slice_panel(self, panel: Panel) -> Dict:
        """Slice a single panel and return box specifications."""
        result = {
            "panel_name": panel.name,
            "panel_dims": f"{panel.width_mm:.1f} × {panel.height_mm:.1f} × {panel.depth_mm:.1f} mm",
            "boxes": [],
            "strategy": None,
            "waste_pct": 0.0
        }
        
        # Calculate lamination layers needed
        layers_needed = math.ceil(panel.depth_mm / self.foam_thickness)
        
        if not self.allow_lamination and layers_needed > 1:
            result["warning"] = f"Panel depth {panel.depth_mm:.1f}mm exceeds foam thickness " \
                               f"{self.foam_thickness}mm and lamination disabled"
            return result
        
        if layers_needed > self.max_layers:
            result["warning"] = f"Panel depth {panel.depth_mm:.1f}mm requires {layers_needed} " \
                               f"layers (max {self.max_layers} allowed)"
            return result
        
        # Find best fitting stock
        best_stock = None
        best_waste = float('inf')
        
        for stock in self.stocks:
            if stock.fits(panel.width_mm, panel.height_mm):
                waste = (stock.area - (panel.width_mm * panel.height_mm)) / stock.area * 100
                if waste < best_waste:
                    best_waste = waste
                    best_stock = stock
        
        if not best_stock:
            result["error"] = f"No stock size can fit panel {panel.width_mm:.1f} × {panel.height_mm:.1f}mm"
            return result
        
        # Create box from this panel
        box = Box(
            name=f"{panel.name}_box_1",
            width_mm=panel.width_mm,
            height_mm=panel.height_mm,
            depth_mm=panel.depth_mm,
            lamination_layers=layers_needed,
            source_panel=panel.name
        )
        
        result["boxes"].append(box)
        result["strategy"] = f"Single box ({layers_needed} layers) on {best_stock.name}"
        result["waste_pct"] = best_waste
        result["stock_used"] = best_stock.name
        
        return result
    
    def get_box_summary(self) -> str:
        """Return human-readable summary of boxes."""
        lines = [
            "=== BOX SLICER SUMMARY ===\n",
            f"Total panels: {len(self.panels)}",
            f"Total boxes: {len(self.boxes)}",
            ""
        ]
        
        for box in self.boxes:
            lamination_str = f" (laminated ×{box.lamination_layers})" if box.lamination_layers > 1 else ""
            lines.append(f"  {box.name}: {box.width_mm:.1f} × {box.height_mm:.1f} × {box.depth_mm:.1f}mm{lamination_str}")
        
        return "\n".join(lines)
