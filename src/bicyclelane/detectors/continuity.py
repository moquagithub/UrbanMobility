"""D2 - Route continuity detector.

Goal
----
Along a named street corridor that *partly* has cycle provision, find the
uncovered gaps that break continuity -- the missing middle segments a cyclist
would have to ride without a lane. Filling such a gap connects existing
provision on both sides, so gaps flanked by long covered runs are high value.

Algorithm (BicycleLane panorama, detector D2)
--------------------------------------------
1. Group road edges into **corridors** by street name. Each corridor's
   centreline is obtained by merging its road edges into a continuous line of
   length ``l_c``.
2. **Linear-reference** the cycle coverage onto the corridor centreline: every
   cycle geometry lying within a small buffer of the centreline is projected to
   a covered interval ``[s0, s1]`` in linear distance along the centreline. The
   covered intervals are merged.
3. The complement over ``[0, l_c]`` gives the uncovered gaps. Keep gaps ``I``
   with ``d_min <= |I| <= d_max``.
4. Score each kept gap with

       S2 = (L_before + L_after) / |I|

   where ``L_before`` / ``L_after`` are the lengths of the covered runs
   immediately preceding / following the gap. A short gap between two long
   covered runs scores highest.
5. The corridor's **continuity index** is ``kappa_c = covered / l_c`` in
   ``[0, 1]``.

All geometry is assumed to be in a **metric CRS** (lengths in metres).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterator

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, substring

from ..schema import Opportunity, to_geodataframe


def _iter_linestrings(geom: BaseGeometry | None) -> Iterator[LineString]:
    """Yield the LineString components of an arbitrary geometry."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, MultiLineString):
        for part in geom.geoms:
            if not part.is_empty:
                yield part
    elif hasattr(geom, "geoms"):  # GeometryCollection
        for part in geom.geoms:
            yield from _iter_linestrings(part)


def _coerce_name(value: Any) -> str | None:
    """Coerce a possibly list-valued OSM ``name`` tag into a single string."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if v), None)
        if value is None:
            return None
    text = str(value).strip()
    return text or None


def _snap_and_merge(
    lines: list[LineString], snap_tol_m: float
) -> Iterator[LineString]:
    """Merge a group of LineStrings into contiguous runs, tolerating tiny gaps.

    Edge endpoints that fall within ``snap_tol_m`` of one another are clustered
    and collapsed onto a shared representative coordinate *before* running
    :func:`shapely.ops.linemerge`. This stitches runs whose endpoints do not
    coincide exactly (rounding / intersection micro-gaps) while leaving genuinely
    disjoint pieces -- endpoints farther apart than the tolerance -- unmerged, so
    a street split by e.g. a river still yields one run per piece.

    Only the two *endpoints* of each edge are snapped (that is where
    :func:`linemerge` stitches); interior vertices are left untouched.
    """
    if len(lines) == 1:
        yield from _iter_linestrings(lines[0])
        return
    if snap_tol_m <= 0:
        yield from _iter_linestrings(linemerge(lines))
        return

    # Endpoint list: indices 2*k and 2*k+1 are the start / end of ``lines[k]``.
    endpoints: list[tuple[float, float]] = []
    for ls in lines:
        endpoints.append(tuple(ls.coords[0]))
        endpoints.append(tuple(ls.coords[-1]))

    # Union-find clustering of endpoints within ``snap_tol_m``.
    n = len(endpoints)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    tol2 = snap_tol_m * snap_tol_m
    for i in range(n):
        xi, yi = endpoints[i]
        for j in range(i + 1, n):
            xj, yj = endpoints[j]
            dx, dy = xi - xj, yi - yj
            if dx * dx + dy * dy <= tol2:
                union(i, j)

    # Representative coordinate per cluster (centroid of its members).
    members: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for idx, pt in enumerate(endpoints):
        members[find(idx)].append(pt)
    rep: dict[int, tuple[float, float]] = {
        root: (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )
        for root, pts in members.items()
    }

    # Rebuild each edge with its endpoints snapped to their cluster reps.
    snapped: list[LineString] = []
    for k, ls in enumerate(lines):
        coords = list(ls.coords)
        coords[0] = rep[find(2 * k)]
        coords[-1] = rep[find(2 * k + 1)]
        rebuilt = LineString(coords)
        if rebuilt.length > 0:
            snapped.append(rebuilt)

    if not snapped:
        return
    merged = linemerge(snapped) if len(snapped) > 1 else snapped[0]
    yield from _iter_linestrings(merged)


def corridors_from_roads(
    road_gdf: gpd.GeoDataFrame,
    *,
    name_col: str = "name",
    snap_tol_m: float = 2.0,
) -> list[tuple[str, LineString]]:
    """Group road edges by street name into corridor centrelines.

    Edges sharing a name are merged (:func:`shapely.ops.linemerge`). Before
    merging, edge endpoints lying within ``snap_tol_m`` metres of each other are
    snapped together, so same-name runs that are geometrically continuous but
    whose endpoints do not share exact coordinates (rounding / intersection
    micro-gaps) still collapse into a single centreline. A corridor that is
    genuinely in disjoint pieces (gap larger than the tolerance) still yields one
    centreline per contiguous run, each treated independently downstream.
    Unnamed edges are skipped.

    Parameters
    ----------
    snap_tol_m:
        Endpoint snapping tolerance in metres. Kept deliberately small so only
        rounding / intersection gaps are stitched -- never distinct parallel
        streets. ``<= 0`` disables snapping (plain :func:`linemerge`).
    """
    corridors: list[tuple[str, LineString]] = []
    if name_col not in road_gdf.columns:
        return corridors

    names = road_gdf[name_col].map(_coerce_name)
    for name, group in road_gdf.groupby(names):
        if name is None:
            continue
        lines = [
            ls
            for geom in group.geometry
            for ls in _iter_linestrings(geom)
        ]
        if not lines:
            continue
        for centreline in _snap_and_merge(lines, snap_tol_m):
            if centreline.length > 0:
                corridors.append((str(name), centreline))
    return corridors


def _coverage_intervals(
    centreline: LineString, cycle_gdf: gpd.GeoDataFrame, buffer_m: float
) -> list[tuple[float, float]]:
    """Project cycle coverage onto ``centreline`` and return merged covered
    intervals ``[(lo, hi), ...]`` in linear distance (metres)."""
    buffer = centreline.buffer(buffer_m)
    raw: list[tuple[float, float]] = []

    # Restrict to cycle geometries that actually touch the corridor buffer.
    sindex = cycle_gdf.sindex
    cand_pos = list(sindex.intersection(buffer.bounds))
    candidates = cycle_gdf.iloc[cand_pos] if cand_pos else cycle_gdf.iloc[[]]

    for geom in candidates.geometry:
        if geom is None or geom.is_empty:
            continue
        clipped = geom.intersection(buffer)
        for part in _iter_linestrings(clipped):
            d0 = centreline.project(Point(part.coords[0]))
            d1 = centreline.project(Point(part.coords[-1]))
            lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
            if hi - lo > 0:
                raw.append((lo, hi))

    if not raw:
        return []

    # Merge overlapping / touching intervals.
    raw.sort()
    merged: list[tuple[float, float]] = [raw[0]]
    for lo, hi in raw[1:]:
        last_lo, last_hi = merged[-1]
        if lo <= last_hi:
            merged[-1] = (last_lo, max(last_hi, hi))
        else:
            merged.append((lo, hi))
    return merged


def analyze_corridor(
    centreline: LineString,
    cycle_gdf: gpd.GeoDataFrame,
    *,
    buffer_m: float = 15.0,
) -> dict[str, Any]:
    """Decompose a corridor into covered runs and uncovered gaps.

    Returns a dict with ``corridor_len`` (l_c), ``covered_len``,
    ``continuity_index`` (kappa_c) and ``gaps`` -- a list of dicts, one per
    *uncovered* gap (all of them, unfiltered), each with ``lo``, ``hi``,
    ``length``, ``before`` and ``after``.

    By construction ``covered_len + sum(gap.length) == corridor_len`` (up to
    floating-point error), which is the mass-balance invariant.
    """
    l_c = centreline.length
    covered = _coverage_intervals(centreline, cycle_gdf, buffer_m)
    covered_len = sum(hi - lo for lo, hi in covered)

    # Build the alternating covered/uncovered partition of [0, l_c].
    gaps: list[dict[str, Any]] = []
    cursor = 0.0
    for idx, (lo, hi) in enumerate(covered):
        if lo - cursor > 1e-9:
            before = covered[idx - 1][1] - covered[idx - 1][0] if idx > 0 else 0.0
            after = hi - lo
            gaps.append(
                {"lo": cursor, "hi": lo, "length": lo - cursor,
                 "before": before, "after": after}
            )
        cursor = hi
    if l_c - cursor > 1e-9:
        before = covered[-1][1] - covered[-1][0] if covered else 0.0
        gaps.append(
            {"lo": cursor, "hi": l_c, "length": l_c - cursor,
             "before": before, "after": 0.0}
        )

    return {
        "corridor_len": l_c,
        "covered_len": covered_len,
        "continuity_index": (covered_len / l_c) if l_c > 0 else 0.0,
        "gaps": gaps,
    }


def detect_continuity_gaps(
    cycle_gdf: gpd.GeoDataFrame,
    road_gdf: gpd.GeoDataFrame,
    *,
    d_min: float = 30.0,
    d_max: float = 500.0,
    min_flank_m: float = 20.0,
    name_col: str = "name",
    buffer_m: float = 15.0,
    snap_tol_m: float = 2.0,
    city: str = "",
    crs: Any = None,
) -> gpd.GeoDataFrame:
    """Detect route-continuity gap opportunities (detector D2).

    Parameters
    ----------
    cycle_gdf:
        Cycle-lane geometries (LineStrings) in a **metric CRS**.
    road_gdf:
        Road-edge geometries in the same metric CRS, carrying a street-name
        column (``name_col``) used to group edges into corridors.
    d_min, d_max:
        Keep only uncovered gaps whose length ``|I|`` lies in ``[d_min, d_max]``.
    min_flank_m:
        Keep only **interior** gaps flanked by at least this much cycle provision
        on *both* sides. This is the key filter that turns D2 from "every
        unequipped street" into "genuine holes in otherwise-equipped corridors":
        corridors with no coverage are skipped, and leading/trailing uncovered
        ends (one flank = 0) are dropped.
    name_col:
        Column of ``road_gdf`` holding the street name.
    buffer_m:
        Half-width (metres) of the corridor buffer used to decide which cycle
        geometry counts as covering the corridor.
    snap_tol_m:
        Endpoint snapping tolerance (metres) used when merging same-name road
        edges into corridor centrelines. Small by design so only rounding /
        intersection micro-gaps are stitched, never distinct parallel streets.
    city:
        City / place name recorded on each opportunity.
    crs:
        Output CRS. Defaults to ``road_gdf.crs``.

    Returns
    -------
    geopandas.GeoDataFrame
        Ranked gap opportunities (highest ``score`` first). Geometry is the
        substring of the corridor centreline spanning the gap; attributes carry
        the corridor name/length, covered length, continuity index kappa_c, gap
        length, and the flanking covered-run lengths.
    """
    if crs is None:
        crs = road_gdf.crs

    # Align CRS of the cycle layer to the road layer.
    if cycle_gdf.crs is not None and road_gdf.crs is not None and cycle_gdf.crs != road_gdf.crs:
        cycle_gdf = cycle_gdf.to_crs(road_gdf.crs)

    opportunities: list[Opportunity] = []
    seq = 0
    for name, centreline in corridors_from_roads(
        road_gdf, name_col=name_col, snap_tol_m=snap_tol_m
    ):
        report = analyze_corridor(centreline, cycle_gdf, buffer_m=buffer_m)
        # Skip corridors with no cycle provision at all: an unequipped street is
        # not a *continuity* gap (that is D5's demand/supply territory).
        if report["covered_len"] <= 0.0:
            continue
        kappa_c = report["continuity_index"]
        l_c = report["corridor_len"]

        for gap in report["gaps"]:
            length = gap["length"]
            if length < d_min or length > d_max:
                continue
            l_before, l_after = gap["before"], gap["after"]
            # Interior gaps only: flanked by real provision on both sides.
            if l_before < min_flank_m or l_after < min_flank_m:
                continue
            score = (l_before + l_after) / length if length > 0 else 0.0
            gap_geom = substring(centreline, gap["lo"], gap["hi"])

            opportunities.append(
                Opportunity(
                    id=f"D2-{seq:04d}",
                    city=city,
                    detector="D2",
                    geometry=gap_geom,
                    score=float(score),
                    attributes={
                        "corridor": name,
                        "corridor_len_m": round(l_c, 3),
                        "covered_len_m": round(report["covered_len"], 3),
                        "continuity_index": round(kappa_c, 4),
                        "gap_len_m": round(length, 3),
                        "len_before_m": round(l_before, 3),
                        "len_after_m": round(l_after, 3),
                        "start_m": round(gap["lo"], 3),
                        "end_m": round(gap["hi"], 3),
                    },
                    explanation=(
                        f"Continuity gap on '{name}': {length:.0f} m uncovered "
                        f"between covered runs of {l_before:.0f} m and {l_after:.0f} m "
                        f"(corridor continuity kappa_c={kappa_c:.2f}). S2={score:.3f}."
                    ),
                )
            )
            seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank

    return to_geodataframe(opportunities, crs=crs)
