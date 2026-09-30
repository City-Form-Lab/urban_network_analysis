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
    for suffix in ("_facilities.geojson", "_facilities_selected.geojson",
                   "_demand.geojson", "_summary.json"):
        path = os.path.join(out, "fa_test" + suffix)
        assert os.path.isfile(path), f"missing {path}"
    sel = gpd.read_file(os.path.join(out, "fa_test_facilities_selected.geojson"))
    assert len(sel) == 2 and set(sel["selected"]) == {1}   # only A and B
    with open(os.path.join(out, "fa_test_summary.json")) as f:
        summary = json.load(f)
    assert summary["n_new"] == 2
    print("  outputs: PASS")


def test_exact_max_access(folder):
    """Exact MILP must reproduce the (here provably optimal) greedy picks."""
    for p, exp_sel, exp_obj in ((1, [1, 0, 0], 30.0), (2, [1, 1, 0], 33.0)):
        una = fresh_una(folder)
        una.settings.fa_new_facilities = p
        una.settings.fa_solver = "exact"
        una.RunFacilityAllocation()
        e = eng(una)
        assert e.fa_selected.tolist() == exp_sel, \
            f"p={p}: selected={e.fa_selected}"
        assert abs(e.summary["objective_total_access"] - exp_obj) < 1e-9
        assert e.summary["solver"] == "exact"
    # exact + decay: same optimum as greedy decay run
    una = fresh_una(folder)
    una.settings.fa_new_facilities = 1
    una.settings.fa_solver = "exact"
    una.settings.flow_decay = True
    una.settings.flow_decay_curve = "exponential"
    una.settings.gravity_beta = 0.004
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [1, 0, 0]
    expected_obj = 10 * (np.exp(-0.4) + np.exp(-0.2) + np.exp(-0.6))
    assert abs(e.summary["objective_total_access"] - expected_obj) < 1e-9
    print("  exact MILP max_access: PASS")


def test_min_facilities(folder):
    """Cutoff 300: A+B cover all six demand points; C is redundant.
    Expected: exactly {A, B} for both solvers, and B stays fixed when
    required."""
    for solver in ("greedy", "exact"):
        una = fresh_una(folder)
        una.settings.fa_problem_type = "min_facilities"
        una.settings.fa_solver = solver
        una.RunFacilityAllocation()
        e = eng(una)
        assert e.fa_selected.tolist() == [1, 1, 0], \
            f"{solver}: selected={e.fa_selected}"
        assert e.summary["pct_covered"] == 100.0
        assert e.summary["n_new"] == 2

    una = fresh_una(folder)
    una.settings.destinations_file = "candidates_Breq.geojson"
    una.settings.fa_required_column = "required"
    una.settings.fa_problem_type = "min_facilities"
    una.settings.fa_solver = "exact"
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [1, 1, 0]
    assert e.fa_required.tolist() == [0, 1, 0]
    assert e.summary["n_new"] == 1                    # only A added
    print("  min_facilities greedy+exact: PASS")


def test_turns(folder):
    """L-shaped network with the 90° corner strictly MID-PATH.

    Two engine conventions (inherited from the flow engines) shape the
    geometry: connector transitions never cost a turn (virtual nodes
    don't turn), and a destination may be approached from either end
    of its snap edge via its connectors. So the penalized corner must
    be between two real network arcs, neither of which is the
    candidate's snap edge.

    Edges: (0,0)-(100,0), (100,0)-(200,0), (200,0)-(200,100),
    (200,100)-(200,200). Demand at the west end, candidate snapped to
    the last edge. Route = 4 × 100 = 400 geometric; one 90° turn at
    (200,0) between real arcs → +35 (the E3→E4 continuation is
    straight and free).

      * turns=False, cutoff 410 → covered, distance 400
      * turns=True,  cutoff 410 → uncovered (435 > 410)
      * turns=True,  cutoff 500 → covered, distance 435 exactly
    """
    sub = os.path.join(folder, "turns")
    os.makedirs(sub, exist_ok=True)
    edges = [LineString([(0, 0), (100, 0)]),
             LineString([(100, 0), (200, 0)]),
             LineString([(200, 0), (200, 100)]),
             LineString([(200, 100), (200, 200)])]
    gpd.GeoDataFrame({"eid": [0, 1, 2, 3]}, geometry=edges, crs=CRS) \
       .to_file(os.path.join(sub, "net.geojson"), driver="GeoJSON")
    gpd.GeoDataFrame({"name": ["d0"], "weight": [10]},
                     geometry=[Point(0, 5)], crs=CRS) \
       .to_file(os.path.join(sub, "demand.geojson"), driver="GeoJSON")
    gpd.GeoDataFrame({"name": ["X"], "required": [0]},
                     geometry=[Point(195, 200)], crs=CRS) \
       .to_file(os.path.join(sub, "candidates.geojson"), driver="GeoJSON")

    def run(turns, cutoff):
        una = fresh_una(sub)
        una.settings.search_radius  = cutoff
        una.settings.fa_new_facilities = 1
        una.settings.turns          = turns
        una.settings.turn_threshold = 45
        una.settings.turn_penalty   = 35
        una.RunFacilityAllocation()
        return eng(una)

    e = run(False, 410)
    assert e.fa_covered.tolist() == [1] and abs(e.fa_distance[0] - 400.0) < 1e-6, \
        f"turns off: covered={e.fa_covered}, d={e.fa_distance}"

    e = run(True, 410)
    assert e.fa_covered.tolist() == [0], \
        f"turns on, cutoff 410: covered={e.fa_covered} (d={e.fa_distance})"

    e = run(True, 500)
    assert e.fa_covered.tolist() == [1] and abs(e.fa_distance[0] - 435.0) < 1e-6, \
        f"turns on, cutoff 500: covered={e.fa_covered}, d={e.fa_distance}"
    print("  turn-aware costs (L-network): PASS")


def test_max_patronage(folder):
    """Gravity-cap trip generation, decay off, unit attraction, cap=2:
    demand i generates w_i * min(1, n_open_reachable/2) trips.

    Reachability at cutoff 300: A reaches the west cluster (30 wt),
    B the east cluster (3 wt), C reaches x=250 (wt 10) and x=750 (wt 1).

      p=1: A → 30*0.5 = 15;  B → 1.5;  C → 5.5  ⇒ pick A (15 trips)
      p=2: after A —
           B adds 3*0.5 = 1.5
           C adds x=250: 10*(1.0-0.5)=5  +  x=750: 1*0.5=0.5  = 5.5
           ⇒ pick C (NOT B — the cap rewards doubling up on demand
             that A only half-saturates). Total = 20.5 trips.

    Allocation with {A, C} open: x=250 generates 10 trips split 5/5
    between A and C (equal g); C also gets 0.5 from x=750.
      A: demand_served 5+5+5=... → trips captured 5+5+5 = 15? No:
      x=0: 5 trips (A only), x=150: 5 trips (A only),
      x=250: 10 trips split 5/5 ⇒ A captures 15, C captures 5.5.
    """
    una = fresh_una(folder)
    una.settings.fa_problem_type = "max_patronage"
    una.settings.fa_new_facilities = 2
    una.settings.flow_gravity_cap = 2.0
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [1, 0, 1], f"selected={e.fa_selected}"
    assert e.fa_rank.tolist() == [1, -1, 2]
    assert abs(e.summary["total_trips"] - 20.5) < 1e-9, e.summary
    # trip-generation factors: west x=0/x=150 → 0.5; x=250 → 1.0;
    # x=750 → 0.5; east x=900/1000 → 0 (nothing open in reach)
    assert np.allclose(e.fa_access, [0.5, 0.5, 1.0, 0.5, 0.0, 0.0])
    # facility patronage (Huff split of trips)
    assert abs(e.fa_access_captured[0] - 15.0) < 1e-9   # A
    assert abs(e.fa_access_captured[2] - 5.5) < 1e-9    # C
    assert e.fa_covered.tolist() == [1, 1, 1, 1, 0, 0]
    # exported facilities layer carries the trips as a 'patronage' column
    fac = gpd.read_file(os.path.join(una.settings.output_folder,
                                     "fa_test_facilities.geojson"))
    assert "patronage" in fac.columns and "access_captured" not in fac.columns
    assert abs(fac["patronage"].sum() - 20.5) < 1e-9

    # exact solver not available for this mode → logged greedy fallback
    una = fresh_una(folder)
    una.settings.fa_problem_type = "max_patronage"
    una.settings.fa_new_facilities = 1
    una.settings.flow_gravity_cap = 2.0
    una.settings.fa_solver = "exact"
    una.RunFacilityAllocation()
    assert eng(una).fa_selected.tolist() == [1, 0, 0]

    # percentile-string cap must raise a clear error
    una = fresh_una(folder)
    una.settings.fa_problem_type = "max_patronage"
    una.settings.flow_gravity_cap = "p95"
    try:
        una.RunFacilityAllocation()
        raise AssertionError("string cap should raise for max_patronage")
    except ValueError:
        pass

    # weighted attractiveness via destination_weight_column:
    # attr A=1, B=1, C=2; cap=2, decay off, p=2.
    #   pick 1: A (west cluster, 30*0.5 = 15 trips)
    #   pick 2: C — x=250: G 1→3, factor 0.5→1 → +5;
    #               x=750: G 0→2, factor 0→1  → +1;  gain 6 (B: 1.5)
    #   total = 21. Huff with {A, C}: x=250 splits 1/3 A, 2/3 C
    #   → A captures 5+5+10/3 = 40/3, C captures 20/3+1 = 23/3.
    cand_x = [100, 900, 500]
    gpd.GeoDataFrame(
        {"name": ["A", "B", "C"], "required": [0, 0, 0], "attr": [1, 1, 2]},
        geometry=[Point(x, 5) for x in cand_x], crs=CRS,
    ).to_file(os.path.join(folder, "candidates_attr.geojson"), driver="GeoJSON")
    una = fresh_una(folder)
    una.settings.destinations_file          = "candidates_attr.geojson"
    una.settings.destination_weight_column  = "attr"
    una.settings.fa_problem_type = "max_patronage"
    una.settings.fa_new_facilities = 2
    una.settings.flow_gravity_cap = 2.0
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [1, 0, 1], f"selected={e.fa_selected}"
    assert abs(e.summary["total_trips"] - 21.0) < 1e-9, e.summary
    assert abs(e.fa_access_captured[0] - 40.0 / 3.0) < 1e-9
    assert abs(e.fa_access_captured[2] - 23.0 / 3.0) < 1e-9
    print("  max_patronage (cap=2, Huff split, dest weights): PASS")


def test_evaluation_run(folder):
    """fa_new_facilities = 0: no siting — evaluate the required
    configuration as it stands. B required, cap=2, decay off:
    east cluster (3 × weight 1) reaches only B → factor 0.5 each →
    total trips 1.5, all landing at B. West cluster uncovered."""
    una = fresh_una(folder)
    una.settings.destinations_file = "candidates_Breq.geojson"
    una.settings.fa_required_column = "required"
    una.settings.fa_problem_type = "max_patronage"
    una.settings.fa_new_facilities = 0
    una.settings.flow_gravity_cap = 2.0
    una.RunFacilityAllocation()
    e = eng(una)
    assert e.fa_selected.tolist() == [0, 1, 0]        # B only, nothing added
    assert e.fa_rank.tolist() == [-1, 0, -1]
    assert abs(e.summary["total_trips"] - 1.5) < 1e-9
    assert abs(e.fa_access_captured[1] - 1.5) < 1e-9  # all patronage at B
    assert e.fa_covered.tolist() == [0, 0, 0, 1, 1, 1]
    print("  evaluation run (p=0, required only): PASS")


def test_batch(folder):
    """Two-row pairing CSV run via RunBatch('facility_allocation') with
    a composite on fa_access joined onto the shared demand layer."""
    import pandas as pd
    rows = pd.DataFrame([
        dict(name="fa_p1", network_file="net.geojson",
             origins_file="demand.geojson",
             destinations_file="candidates.geojson",
             origin_weight_column="weight", search_radius=300,
             fa_problem_type="max_access", fa_new_facilities=1,
             flow_decay="FALSE",
             output_geojson="TRUE", output_feather="FALSE",
             output_csv="FALSE", output_wStamp="FALSE",
             batch_composite_output="TRUE",
             batch_composite_result_column="fa_access"),
        dict(name="fa_p2", network_file="net.geojson",
             origins_file="demand.geojson",
             destinations_file="candidates.geojson",
             origin_weight_column="weight", search_radius=300,
             fa_problem_type="max_access", fa_new_facilities=2,
             flow_decay="FALSE",
             output_geojson="TRUE", output_feather="FALSE",
             output_csv="FALSE", output_wStamp="FALSE",
             batch_composite_output="TRUE",
             batch_composite_result_column="fa_access"),
    ])
    csv_path = os.path.join(folder, "fa_pairings.csv")
    rows.to_csv(csv_path, index=False)

    una = UNA(verbosity=0)
    una.settings.output_folder = os.path.join(folder, "BatchResults")
    una.RunBatch("facility_allocation", pairing_file=csv_path)

    comp = una.composite_result
    assert comp is not None and len(comp) > 0
    gdf = list(comp.values())[0] if isinstance(comp, dict) else comp
    cols = list(gdf.columns)
    assert any("fa_access_fa_p1" in c for c in cols), cols
    assert any("fa_access_fa_p2" in c for c in cols), cols
    assert len(gdf) == 6                      # joined onto the 6 demand points
    # row 1 (p=1, coverage): 3 west points have access 1; row 2 adds east
    a1 = gdf[[c for c in cols if "fa_p1" in c][0]].values
    a2 = gdf[[c for c in cols if "fa_p2" in c][0]].values
    assert abs(a1.sum() - 3.0) < 1e-9 and abs(a2.sum() - 6.0) < 1e-9
    print("  RunBatch facility_allocation + composite: PASS")


def test_unservable(folder):
    """Cutoff 120: x=250 and x=750 reach no candidate — unservable.
    min_facilities must still cover the four coverable points with A+B
    and report the unservable weight (10 + 1 = 11) as uncovered."""
    for solver in ("greedy", "exact"):
        una = fresh_una(folder)
        una.settings.search_radius = 120
        una.settings.fa_problem_type = "min_facilities"
        una.settings.fa_solver = solver
        una.RunFacilityAllocation()
        e = eng(una)
        assert e.fa_selected.tolist() == [1, 1, 0], \
            f"{solver}: selected={e.fa_selected}"
        assert e.fa_covered.tolist() == [1, 1, 0, 0, 1, 1]
        assert abs(e.summary["demand_uncovered"] - 11.0) < 1e-9
    print("  unservable demand (cutoff 120): PASS")


def main():
    with tempfile.TemporaryDirectory() as folder:
        build_toy(folder)
        print("FacilityAllocation toy tests:")
        test_p1_coverage(folder)
        test_p2_coverage(folder)
        test_required(folder)
        test_decay(folder)
        test_outputs_and_errors(folder)
        test_exact_max_access(folder)
        test_min_facilities(folder)
        test_turns(folder)
        test_unservable(folder)
        test_max_patronage(folder)
        test_evaluation_run(folder)
        test_batch(folder)
        print("ALL PASS")


if __name__ == "__main__":
    main()
