"""Folium rendering of detected opportunities over the existing cycle network.

This is the visual export layer shared by the detectors: given the existing
cycle network and one or more opportunity layers (the GeoDataFrames returned by
``detect_*``), it produces a standalone, self-contained HTML map with a toggle
per layer and a popup per opportunity.

Everything is reprojected to EPSG:4326 for display; detectors work in a metric
CRS, so the input layers may be projected -- they are converted here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import geopandas as gpd

# Colour palette for opportunity layers (matches the report's accent colours).
_PALETTE = ["#c2410c", "#0f766e", "#7c3aed", "#b91c1c", "#0369a1"]


def _to_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf
    epsg = gdf.crs.to_epsg()
    return gdf if epsg == 4326 else gdf.to_crs(4326)


def _latlon(line):
    """Shapely LineString (lon/lat) -> list of (lat, lon) for Folium."""
    return [(y, x) for x, y in line.coords]


def _popup_html(row) -> str:
    bits = []
    for key in ("id", "detector", "rank", "score", "score_nodes",
                "gap_m", "road_len_m", "gap_len_m", "continuity_index"):
        if key in row and row[key] == row[key]:  # skip NaN
            val = row[key]
            bits.append(f"<b>{key}</b>: {round(val, 4) if isinstance(val, float) else val}")
    expl = row.get("explanation") if hasattr(row, "get") else None
    html = "<br>".join(bits)
    if expl:
        html += f"<hr style='margin:4px 0'>{expl}"
    return f"<div style='font-size:12px;max-width:260px'>{html}</div>"


def render_opportunities(
    cycle_network_wgs84: gpd.GeoDataFrame,
    layers: Mapping[str, gpd.GeoDataFrame],
    out_path: str | Path,
    *,
    zoom_start: int = 13,
    title: str | None = None,
) -> Path:
    """Render the cycle network + opportunity layers to a standalone HTML map.

    Parameters
    ----------
    cycle_network_wgs84:
        The existing cycle network (lines). Drawn as a light-blue base layer.
    layers:
        Mapping ``label -> GeoDataFrame`` of opportunities. LineStrings are drawn
        as thick coloured lines, Polygons as translucent filled zones.
    out_path:
        Destination ``.html`` file.
    """
    import folium

    net = _to_wgs84(cycle_network_wgs84)
    minx, miny, maxx, maxy = net.total_bounds
    center = [(miny + maxy) / 2, (minx + maxx) / 2]

    m = folium.Map(location=center, zoom_start=zoom_start,
                   tiles="cartodbpositron", control_scale=True)

    net_fg = folium.FeatureGroup(name=f"Existing cycle network ({len(net)})", show=True)
    folium.GeoJson(
        net.__geo_interface__,
        style_function=lambda _f: {"color": "#3186cc", "weight": 2, "opacity": 0.55},
    ).add_to(net_fg)
    net_fg.add_to(m)

    for i, (label, gdf) in enumerate(layers.items()):
        color = _PALETTE[i % len(_PALETTE)]
        # If the label already carries its own count (e.g. "top 150 of 354"),
        # use it verbatim; otherwise append the feature count.
        name = label if "(" in label else f"{label} ({len(gdf)})"
        fg = folium.FeatureGroup(name=name, show=True)
        if len(gdf):
            g = _to_wgs84(gdf)
            for _, row in g.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                popup = folium.Popup(_popup_html(row), max_width=300)
                if geom.geom_type == "LineString":
                    folium.PolyLine(_latlon(geom), color=color, weight=5,
                                    opacity=0.9, popup=popup).add_to(fg)
                    # endpoint markers make short connectors visible when zoomed out
                    for lat, lon in (_latlon(geom)[0], _latlon(geom)[-1]):
                        folium.CircleMarker([lat, lon], radius=3, color=color,
                                            fill=True, fill_opacity=0.9).add_to(fg)
                elif geom.geom_type in ("Polygon", "MultiPolygon"):
                    folium.GeoJson(
                        geom.__geo_interface__,
                        style_function=lambda _f, c=color: {
                            "color": c, "weight": 1, "fillColor": c, "fillOpacity": 0.4},
                        popup=popup,
                    ).add_to(fg)
        fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    if title:
        m.get_root().html.add_child(folium.Element(
            f"<div style='position:fixed;top:10px;left:50px;z-index:9999;"
            f"background:white;padding:6px 12px;border-radius:6px;"
            f"font:600 14px sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)'>{title}</div>"
        ))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    return out_path
