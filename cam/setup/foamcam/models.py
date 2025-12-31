# cam/setup/foamcam/models.py
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any


@dataclass(frozen=True)
class Footprint:
    w_mm: float
    h_mm: float

    @property
    def area(self) -> float:
        return max(self.w_mm, 0.0) * max(self.h_mm, 0.0)


@dataclass
class CollectorDiagnostics:
    seen_total: int = 0
    seen_root: int = 0
    seen_occ: int = 0
    non_brep_or_null: int = 0
    not_solid: int = 0
    filtered_visibility: int = 0
    included: int = 0
    deduped_out: int = 0
    excluded_samples: List[Tuple[str, str, str]] = field(default_factory=list)

    def to_log_string(self) -> str:
        lines = [
            "Collector diagnostics:",
            f"  seen_total={self.seen_total} (root={self.seen_root}, occ={self.seen_occ})",
            f"  included={self.included} deduped_out={self.deduped_out}",
            f"  excluded_not_solid={self.not_solid}",
            f"  excluded_hidden={self.filtered_visibility}",
            f"  excluded_non_brep_or_null={self.non_brep_or_null}",
        ]
        if self.excluded_samples:
            lines.append("  First excluded samples (name | reason | where):")
            for nm, reason, where in self.excluded_samples[:20]:
                lines.append(f"    - {nm} | {reason} | {where}")
        return "\n".join(lines)


@dataclass
class PartCandidate:
    body: Any  # adsk.fusion.BRepBody (native or proxy)
    name: str
    fp0: Footprint
    fp1: Optional[Footprint]
    sheet_class: str
    sheet_w: float
    sheet_h: float
    usable_w: float
    usable_h: float
    prefer_rot: bool
    fill_ratio: float


@dataclass
class PlacedBody:
    name: str
    body: Any  # inserted body in target component
    tx_mm: float
    ty_mm: float
    tz_mm: float


@dataclass
class SheetLayout:
    index: int
    class_name: str
    occ: Any  # adsk.fusion.Occurrence
    usable_w: float
    usable_h: float
    part_names: List[str] = field(default_factory=list)  # Names of parts placed on this sheet


@dataclass
class CamBuildResult:
    setups_created: int = 0
    enforcement_failures: int = 0
