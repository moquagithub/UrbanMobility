"""BicycleLane v1 demo.

Runs the two supply-only detectors (D1 missing links, D2 route continuity).

* If network access to OpenStreetMap is available, it extracts the cycle and
  road networks for a small place and runs both detectors on real data.
* If OSM is unreachable (offline / CI), it **degrades gracefully** and runs the
  same detectors on the small synthetic examples used by the test-suite, so the
  script always produces output.

Usage
-----
    python demo.py                      # default small place, falls back offline
    python demo.py "Roquevaire, France" # try another place
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from a source checkout without installing the package (src layout).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString

from bicyclelane.detectors.continuity import detect_continuity_gaps
from bicyclelane.detectors.missing_links import detect_missing_links

SYNTH_CRS = "EPSG:2154"


# --------------------------------------------------------------------------- #
# Synthetic fallback (identical shapes to the offline tests)
# --------------------------------------------------------------------------- #
def _synthetic_missing_links() -> gpd.GeoDataFrame:
    cycle = nx.Graph()
    cycle.add_node("a0", x=0, y=0)
    cycle.add_node("a1", x=100, y=0)
    cycle.add_edge("a0", "a1", length=100)
    cycle.add_node("b0", x=200, y=0)
    cycle.add_node("b1", x=300, y=0)
    cycle.add_edge("b0", "b1", length=100)

    road = nx.Graph()
    road.add_node("r0", x=100, y=0)
    road.add_node("r1", x=200, y=0)
    road.add_edge("r0", "r1", length=100)
    return detect_missing_links(cycle, road, eps=300, kappa=1.5, city="Synthetic", crs=SYNTH_CRS)


def _synthetic_continuity() -> gpd.GeoDataFrame:
    road = gpd.GeoDataFrame(
        {"name": ["Main St", "Main St"]},
        geometry=[LineString([(0, 0), (500, 0)]), LineString([(500, 0), (1000, 0)])],
        crs=SYNTH_CRS,
    )
    cycle = gpd.GeoDataFrame(
        geometry=[LineString([(0, 0), (300, 0)]), LineString([(600, 0), (1000, 0)])],
        crs=SYNTH_CRS,
    )
    return detect_continuity_gaps(cycle, road, d_min=30, d_max=500, city="Synthetic", crs=SYNTH_CRS)


def _run_synthetic() -> None:
    print("== Synthetic offline demo ==")
    d1 = _synthetic_missing_links()
    print(f"\nD1 missing links: {len(d1)} opportunity(ies)")
    if len(d1):
        print(d1[["id", "score", "rank", "gap_m", "road_len_m", "explanation"]].to_string(index=False))
    d2 = _synthetic_continuity()
    print(f"\nD2 continuity gaps: {len(d2)} opportunity(ies)")
    if len(d2):
        print(d2[["id", "score", "rank", "gap_len_m", "continuity_index", "explanation"]].to_string(index=False))


# --------------------------------------------------------------------------- #
# Live demo (best effort)
# --------------------------------------------------------------------------- #
def _run_live(place: str) -> bool:
    """Attempt a live run. Returns True on success, False if OSM is unreachable."""
    try:
        import osmnx as ox  # noqa: F401

        from bicyclelane.crs import project_gdf, project_graph
        from bicyclelane.osm import get_cycle_graph
        from bicyclelane.roads import get_road_graph
    except Exception as exc:  # pragma: no cover - import/setup issues
        print(f"[live] setup unavailable ({exc}); using synthetic fallback.")
        return False

    try:
        print(f"== Live demo for {place!r} ==")
        cycle_g = project_graph(get_cycle_graph(place))
        road_g = project_graph(get_road_graph(place))

        import osmnx as ox

        cycle_gdf = ox.graph_to_gdfs(cycle_g, nodes=False)
        road_gdf = ox.graph_to_gdfs(road_g, nodes=False)

        d1 = detect_missing_links(cycle_g, road_g, city=place)
        d2 = detect_continuity_gaps(cycle_gdf, road_gdf, city=place)
        print(f"D1 missing links : {len(d1)} opportunities")
        print(f"D2 continuity    : {len(d2)} opportunities")
        for tag, gdf in (("d1", d1), ("d2", d2)):
            if len(gdf):
                print(gdf.head(5).drop(columns="geometry").to_string(index=False))
        return True
    except Exception as exc:  # pragma: no cover - network / data issues
        print(f"[live] failed ({type(exc).__name__}: {exc}); using synthetic fallback.")
        return False


def main() -> None:
    place = sys.argv[1] if len(sys.argv) > 1 else "Roquevaire, France"
    if not _run_live(place):
        _run_synthetic()


if __name__ == "__main__":
    main()
