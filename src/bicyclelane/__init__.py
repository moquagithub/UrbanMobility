"""BicycleLane: detection of opportunities to build new bicycle lanes in a city.

This package provides the shared foundation (OSM extraction, CRS handling,
gridding, opportunity schema) and the v1 detectors. Two "supply-only"
detectors are implemented here:

* ``D1`` missing links   -> :func:`bicyclelane.detectors.missing_links.detect_missing_links`
* ``D2`` route continuity -> :func:`bicyclelane.detectors.continuity.detect_continuity_gaps`

All length/distance computations are performed in a metric CRS (local UTM,
or an explicit planar CRS such as EPSG:2154 for synthetic tests).
"""

from __future__ import annotations

__version__ = "0.1.0"

from .schema import Opportunity

__all__ = ["Opportunity", "__version__"]
