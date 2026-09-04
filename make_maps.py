"""Run D1/D2 on a real city and export a Folium opportunity map.

Usage:
    python make_maps.py "Aix-en-Provence, France"
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import osmnx as ox

from bicyclelane.crs import project_graph
from bicyclelane.osm import get_cycle_graph
from bicyclelane.roads import get_road_graph
from bicyclelane.detectors.missing_links import detect_missing_links
from bicyclelane.detectors.continuity import detect_continuity_gaps
from bicyclelane.mapping import render_opportunities

OUT = Path(__file__).resolve().parent / "outputs"


def main() -> None:
    place = sys.argv[1] if len(sys.argv) > 1 else "Aix-en-Provence, France"
    slug = place.split(",")[0].strip().lower().replace(" ", "_")
    print(f"== {place} ==")

    t = time.time()
    cycle_wgs = get_cycle_graph(place)
    road_wgs = get_road_graph(place)
    cycle_g = project_graph(cycle_wgs)
    road_g = project_graph(road_wgs)
    cycle_gdf_proj = ox.graph_to_gdfs(cycle_g, nodes=False)
    road_gdf_proj = ox.graph_to_gdfs(road_g, nodes=False)
    cycle_edges_wgs = ox.graph_to_gdfs(cycle_wgs, nodes=False)
    print(f"networks ready in {time.time() - t:.1f}s")

    layers = {}
    TOP = 150  # cap displayed features (already rank-sorted) to keep the map light

    d1 = detect_missing_links(cycle_g, road_g, city=place)
    print(f"D1 missing links: {len(d1)}")
    if len(d1):
        cols = [c for c in ("id", "rank", "score", "score_nodes", "gap_m", "road_len_m") if c in d1]
        print(d1[cols].head(8).to_string(index=False))
    layers[f"D1 missing links (top {min(TOP, len(d1))} of {len(d1)})"] = d1.head(TOP)

    try:
        d2 = detect_continuity_gaps(cycle_gdf_proj, road_gdf_proj, city=place)
        print(f"D2 continuity gaps: {len(d2)}")
        layers[f"D2 continuity gaps (top {min(TOP, len(d2))} of {len(d2)})"] = d2.head(TOP)
    except Exception as exc:
        print(f"[D2] skipped ({type(exc).__name__}: {exc})")

    out = render_opportunities(
        cycle_edges_wgs, layers, OUT / f"{slug}_opportunities.html",
        title=f"BicycleLane — opportunities · {place.split(',')[0]}",
    )
    print(f"MAP -> {out}")


if __name__ == "__main__":
    main()
