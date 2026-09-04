"""BicycleLane v1 detectors.

Detectors implemented in this package:

* ``D1``  :func:`missing_links.detect_missing_links`     (supply only)
* ``D2``  :func:`continuity.detect_continuity_gaps`      (supply only)
* ``D4``  :func:`coverage.detect_coverage_coldspots`     (supply only)
* ``D5``  :func:`mismatch.detect_demand_supply_mismatch` (demand vs supply)
* ``D9``  :func:`intermodal.detect_intermodal_access`    (station access)
* ``D11`` :func:`generators.detect_generator_access`     (generator access)
"""

from __future__ import annotations

from .continuity import detect_continuity_gaps
from .coverage import detect_coverage_coldspots
from .generators import detect_generator_access
from .intermodal import detect_intermodal_access
from .mismatch import detect_demand_supply_mismatch, detect_mismatch_segments
from .missing_links import detect_missing_links

__all__ = [
    "detect_missing_links",
    "detect_continuity_gaps",
    "detect_coverage_coldspots",
    "detect_demand_supply_mismatch",
    "detect_mismatch_segments",
    "detect_intermodal_access",
    "detect_generator_access",
]
