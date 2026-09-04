"""Shared output schema for all detectors.

Every detector emits a ranked collection of :class:`Opportunity` records. The
:func:`to_geodataframe` helper serialises a list of opportunities into a
GeoDataFrame with a stable column order, which is the common product consumed by
the mapping / reporting layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

# Canonical, ordered set of non-attribute columns emitted by every detector.
CORE_COLUMNS = ["id", "city", "detector", "score", "rank", "explanation"]


@dataclass
class Opportunity:
    """A single detected opportunity to build a new bicycle lane.

    Attributes
    ----------
    id:
        Stable identifier, unique within a detector run.
    city:
        Name of the city / place the opportunity belongs to.
    detector:
        Detector code that produced it (e.g. ``"D1"``, ``"D2"``).
    geometry:
        The opportunity geometry in a metric CRS (e.g. the proposed connector
        line for D1, or the uncovered gap segment for D2).
    score:
        Detector-specific priority score; higher means higher priority.
    rank:
        1-based rank within the detector run (1 = highest score).
    attributes:
        Free-form detector-specific fields (lengths, component sizes, ...).
    explanation:
        Human-readable justification, suitable for a report or tooltip.
    """

    id: str
    city: str
    detector: str
    geometry: BaseGeometry
    score: float
    rank: int = 0
    attributes: Mapping[str, Any] = field(default_factory=dict)
    explanation: str = ""

    def to_record(self) -> dict[str, Any]:
        """Flatten to a plain dict (attributes are spread into top-level keys)."""
        record: dict[str, Any] = {
            "id": self.id,
            "city": self.city,
            "detector": self.detector,
            "score": self.score,
            "rank": self.rank,
            "explanation": self.explanation,
        }
        # Attributes are spread out; core keys always win to avoid collisions.
        for key, value in self.attributes.items():
            if key not in record and key != "geometry":
                record[key] = value
        record["geometry"] = self.geometry
        return record


def to_geodataframe(
    opportunities: Iterable[Opportunity], *, crs: Any = None
) -> gpd.GeoDataFrame:
    """Serialise opportunities to a GeoDataFrame with a stable column order.

    Parameters
    ----------
    opportunities:
        Iterable of :class:`Opportunity`.
    crs:
        CRS to assign to the resulting GeoDataFrame (should be the metric CRS the
        geometries live in).

    Returns
    -------
    geopandas.GeoDataFrame
        One row per opportunity. Core columns come first (in ``CORE_COLUMNS``
        order), followed by any attribute columns, then ``geometry``.
    """
    records = [opp.to_record() for opp in opportunities]
    if not records:
        gdf = gpd.GeoDataFrame(
            {col: [] for col in CORE_COLUMNS}, geometry=[], crs=crs
        )
        return gdf

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=crs)

    # Stable column order: core columns, then extra attribute columns, geometry last.
    extra = [c for c in gdf.columns if c not in CORE_COLUMNS and c != "geometry"]
    ordered = [c for c in CORE_COLUMNS if c in gdf.columns] + extra + ["geometry"]
    return gdf[ordered]
