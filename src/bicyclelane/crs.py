"""CRS helpers.

All geometric measurements in BicycleLane (lengths, euclidean gaps, buffers,
linear referencing) must be performed in a *metric* projected CRS so that
distances are expressed in metres. This module centralises the reprojection
logic for both GeoDataFrames and OSMnx graphs.

The default strategy is to reproject to the local UTM zone estimated from the
data's centroid (via :func:`osmnx.projection.project_gdf` /
:func:`osmnx.projection.project_graph` when available), which keeps distortion
low for a city-sized extent. For fully synthetic / offline work a fixed planar
CRS (e.g. EPSG:2154, RGF93 / Lambert-93 for metropolitan France) can be passed
explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import geopandas as gpd

if TYPE_CHECKING:  # pragma: no cover - typing only
    import networkx as nx

# WGS84 geographic CRS used by raw OSM data.
WGS84 = "EPSG:4326"
# A convenient metric fallback for metropolitan France (RGF93 / Lambert-93).
LAMBERT93 = "EPSG:2154"


def estimate_utm_crs(gdf: gpd.GeoDataFrame) -> str:
    """Return the EPSG code (as ``"EPSG:xxxxx"``) of the local UTM zone.

    Uses GeoPandas' built-in estimator, which picks the UTM zone containing the
    centroid of the data's total bounds.
    """
    crs = gdf.estimate_utm_crs()
    return crs.to_string() if crs is not None else LAMBERT93


def project_gdf(
    gdf: gpd.GeoDataFrame, to_crs: Optional[str] = None
) -> gpd.GeoDataFrame:
    """Reproject a GeoDataFrame to a metric CRS.

    Parameters
    ----------
    gdf:
        Input GeoDataFrame (any CRS; if it has none, WGS84 is assumed).
    to_crs:
        Target CRS. If ``None``, the local UTM zone is estimated from the data.

    Returns
    -------
    geopandas.GeoDataFrame
        A copy reprojected to the metric CRS.
    """
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84, allow_override=True)
    if to_crs is None:
        to_crs = estimate_utm_crs(gdf)
    return gdf.to_crs(to_crs)


def project_graph(graph: "nx.Graph", to_crs: Optional[str] = None) -> "nx.Graph":
    """Reproject an OSMnx graph to a metric CRS.

    Thin wrapper around :func:`osmnx.projection.project_graph`. Imported lazily
    so that the rest of the package (and the offline test-suite) does not depend
    on OSMnx being importable.
    """
    import osmnx as ox

    return ox.projection.project_graph(graph, to_crs=to_crs)


def is_metric(gdf: gpd.GeoDataFrame) -> bool:
    """Return ``True`` if the GeoDataFrame is in a projected (metric) CRS."""
    return gdf.crs is not None and gdf.crs.is_projected
