"""Hand-verifiable regression test for the FacilityAllocation engine.

Toy world (all geometry on a straight 1 km street, EPSG:3857 meters):

    network   nodes every 100 m from x=0 to x=1000 (10 edges)
    demand    x =   0, 150, 250   weight 10 each   (west cluster)
              x = 750, 900, 1000  weight  1 each   (east cluster)
    candidates A @ x=100, B @ x=900, C @ x=500     (required column)
    cutoff    300

Expected (coverage mode, flow_decay=False):
    * C reaches nobody (nearest demand is 250 away? no — |500-250|=250 ≤ 300,
      |500-750|=250 ≤ 300 — C DOES reach x=250 and x=750, total weight 11).
    * A covers the west cluster  (dists 100, 50, 150)  → weight 30.
    * B covers the east cluster  (dists 150, 0*, 100)  → weight  3.
      (*x=900 sits exactly at B)
    * p=1, nothing required      → pick A, objective 30.
    * p=2, nothing required      → A then B (B adds 3, C adds only 1:
      x=250 and x=750 are already covered by A/B? with A open, C's
      marginal = x=750 (1) + nothing west → after A the best 2nd pick is
      B with 3). Objective 33, coverage 100%.
    * B required, p=1            → baseline 3, greedy adds A (rank: B=0, A=1).

Decay mode (exponential, beta=0.004): access of demand at x=150 assigned
to A equals exp(-0.004 * 50).

Run:  python tests/test_facility_allocation.py
"""

import json
import os
import sys
import tempfile

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from urban_network_analysis import UNA  # noqa: E402

CRS = "EPSG:3857"


def build_toy(folder: str) -> None:
    edges = [LineString([(x, 0), (x + 100, 0)]) for x in range(0, 1000, 100)]
    gpd.GeoDataFrame({"eid": range(len(edges))}, geometry=edges, crs=CRS) \
       .to_file(os.path.join(folder, "net.geojson"), driver="GeoJSON")

    dem_x = [0, 150, 250, 750, 900, 1000]
    dem_w = [10, 10, 10, 1, 1, 1]
    gpd.GeoDataFrame(
        {"name": [f"d{x}" for x in dem_x], "weight": dem_w},
        geometry=[Point(x, 5) for x in dem_x], crs=CRS,
    ).to_file(os.path.join(folder, "demand.geojson"), driver="GeoJSON")

    cand_x = [100, 900, 500]
    gpd.GeoDataFrame(
        {"name": ["A", "B", "C"], "required": [0, 0, 0]},
        geometry=[Point(x, 5) for x in cand_x], crs=CRS,
    ).to_file(os.path.join(folder, "candidates.geojson"), driver="GeoJSON")

    gpd.GeoDataFrame(
        {"name": ["A", "B", "C"], "required": [0, 1, 0]},
        geometry=[Point(x, 5) for x in cand_x], crs=CRS,
    ).to_file(os.path.join(folder, "candidates_Breq.geojson"), driver="GeoJSON")


def fresh_una(folder: str) -> UNA:
    una = UNA(verbosity=0)
    s = una.settings
    s.data_folder          = folder
    s.network_file         = "net.geojson"
    s.origins_file         = "demand.geojson"
    s.destinations_file    = "candidates.geojson"
    s.origin_weight_column = "weight"
    s.search_radius        = 300
    s.flow_decay           = False
    s.output_folder        = os.path.join(folder, "Results")
    s.output_wStamp        = False
    s.output_feather       = False
    s.output_csv           = False
    s.output_geojson       = True
    s.output_file_name     = "fa_test"
    return una


def eng(una):
    return una.facility_allocation


def test_p1_coverage(folder):
    una = fresh_una(folder)
    una.settings.fa_new_facilities = 1
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [1, 0, 0], f"selected={e.fa_selected}"   # A only
    assert e.fa_rank.tolist() == [1, -1, -1]
    assert abs(e.summary["objective_total_access"] - 30.0) < 1e-9
    assert abs(e.summary["demand_covered"] - 30.0) < 1e-9
    # distances of the west cluster to A (x=100): 100, 50, 150
    d = e.fa_distance
    assert abs(d[0] - 100.0) < 1e-6 and abs(d[1] - 50.0) < 1e-6 \
        and abs(d[2] - 150.0) < 1e-6, f"dist={d}"
    assert e.fa_covered.tolist() == [1, 1, 1, 0, 0, 0]
    print("  p=1 coverage: PASS")


def test_p2_coverage(folder):
    una = fresh_una(folder)
    una.settings.fa_new_facilities = 2
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [1, 1, 0]           # A and B
    assert e.fa_rank.tolist() == [1, 2, -1]
    assert abs(e.summary["objective_total_access"] - 33.0) < 1e-9
    assert e.summary["pct_covered"] == 100.0
    assert e.fa_assigned_uid[4] is not None              # x=900 → B
    assert e.fa_distance[4] < 1e-6                       # sits exactly at B
    # facility loads: A serves 30, B serves 3
    assert abs(e.fa_demand_served[0] - 30.0) < 1e-9
    assert abs(e.fa_demand_served[1] - 3.0) < 1e-9
    print("  p=2 coverage: PASS")


def test_required(folder):
    una = fresh_una(folder)
    una.settings.destinations_file = "candidates_Breq.geojson"
    una.settings.fa_required_column = "required"
    una.settings.fa_new_facilities = 1
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_required.tolist() == [0, 1, 0]
    assert e.fa_selected.tolist() == [1, 1, 0]           # B kept, A added
    assert e.fa_rank.tolist() == [1, 0, -1]              # required rank 0
    assert abs(e.summary["objective_total_access"] - 33.0) < 1e-9
    print("  required-facility: PASS")


def test_decay(folder):
    una = fresh_una(folder)
    una.settings.fa_new_facilities = 1
    una.settings.flow_decay = True
    una.settings.flow_decay_curve = "exponential"
    una.settings.gravity_beta = 0.004
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [1, 0, 0]           # still A
    # demand at x=150 (index 1): dist 50 → access exp(-0.2)
    assert abs(e.fa_access[1] - np.exp(-0.004 * 50)) < 1e-9
    expected_obj = 10 * (np.exp(-0.4) + np.exp(-0.2) + np.exp(-0.6))
    assert abs(e.summary["objective_total_access"] - expected_obj) < 1e-9
    print("  exponential decay: PASS")


def test_outputs_and_errors(folder):
    una = fresh_una(folder)
    una.settings.fa_new_facilities = 2
    una.RunFacilityAllocation()
    out = una.settings.output_folder
    for suffix in ("_facilities.geojson", "_demand.geojson", "_summary.json"):
        path = os.path.join(out, "fa_test" + suffix)
        assert os.path.isfile(path), f"missing {path}"
    with open(os.path.join(out, "fa_test_summary.json")) as f:
        summary = json.load(f)
    assert summary["n_new"] == 2

    una = fresh_una(folder)
    una.settings.turns = True
    try:
        una.RunFacilityAllocation()
        raise AssertionError("turns=True should raise")
    except ValueError:
        pass

    una = fresh_una(folder)
    una.settings.fa_problem_type = "min_facilities"
    try:
        una.RunFacilityAllocation()
        raise AssertionError("min_facilities should raise (Phase 2)")
    except NotImplementedError:
        pass
    print("  outputs + guard errors: PASS")


def main():
    with tempfile.TemporaryDirectory() as folder:
        build_toy(folder)
        print("FacilityAllocation toy tests:")
        test_p1_coverage(folder)
        test_p2_coverage(folder)
        test_required(folder)
        test_decay(folder)
        test_outputs_and_errors(folder)
        print("ALL PASS")


if __name__ == "__main__":
    main()
