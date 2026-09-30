"""
UNA_FacilityAllocation.py — facility-siting driver.

Copy this file anywhere, point it at your data, and run it. The
workflow has two parts:

  PART 1  chooses where to open new facilities among candidate sites
          (RunFacilityAllocation) — demand comes from the origins
          layer, candidate sites from the destinations layer, and
          already-existing facilities are marked by a truthy value in
          a column on the candidates layer.

  PART 2  (optional) feeds the chosen configuration straight back
          into a flow analysis: the selected facilities become the
          destinations of a RunFlow() call, estimating the
          street-level pedestrian volumes the chosen sites would
          generate — siting and footfall from one impedance model.

Outputs of PART 1 (in OUTPUT_FOLDER):
  <name>_facilities.geojson   candidates + selected/required/rank/
                              demand_served/access_captured columns
  <name>_demand.geojson       demand + assigned_facility/distance/
                              access/covered columns
  <name>_summary.json         objective, solver used, coverage totals
"""

import urban_network_analysis

# --------------------------------------------------------------------------
# EDIT THESE VALUES
# --------------------------------------------------------------------------

DATA_FOLDER   = r"/path/to/your/data"
NETWORK       = "network.geojson"
DEMAND        = "building_centroids.geojson"    # origins = demand points
DEMAND_WEIGHT = "pop2020"                       # demand weight column
CANDIDATES    = "candidate_sites.geojson"       # destinations = candidate facilities
REQUIRED_COL  = "existing"                      # truthy = already-open facility; None if none
OUTPUT_FOLDER = r"/path/to/your/data/Results"

CUTOFF         = 800            # service cutoff (network units)

# Problem type — three options:
#   "max_access"     maximize demand-weighted access to the nearest open
#                    facility with NEW_FACILITIES new sites
#   "min_facilities" fewest facilities covering all coverable demand
#                    (NEW_FACILITIES ignored — the count is the output)
#   "max_patronage"  maximize total trips generated (gravity-cap model,
#                    Huff-split patronage). Extra requirements below:
#                    a NUMERIC flow_gravity_cap, and optionally
#                    destination_weight_column on the candidates layer
#                    as facility attractiveness (sizes).
PROBLEM        = "max_access"
NEW_FACILITIES = 2              # ignored by min_facilities
SOLVER         = "greedy"       # or "exact" (MILP; falls back to greedy if
                                # oversized; max_patronage always greedy)

RUN_FLOW_ON_RESULT = False      # PART 2 on/off

# --------------------------------------------------------------------------
# PART 1 — choose the facilities
# --------------------------------------------------------------------------

una = urban_network_analysis.UNA()
s = una.settings

s.data_folder          = DATA_FOLDER
s.network_file         = NETWORK
s.origins_file         = DEMAND
s.origin_weight_column = DEMAND_WEIGHT
s.destinations_file    = CANDIDATES
s.fa_required_column   = REQUIRED_COL

s.search_radius        = CUTOFF
s.fa_problem_type      = PROBLEM
s.fa_new_facilities    = NEW_FACILITIES
s.fa_solver            = SOLVER

# Access decay — mirrors the flow engines' "closest" trip-generation
# convention. Set flow_decay = False for pure coverage objectives.
s.flow_decay           = True
s.flow_decay_curve     = "exponential"
s.gravity_beta         = 0.002

# max_patronage only — uncomment and set:
# s.flow_gravity_cap          = 3.5     # NUMERIC saturation cap (derive from
#                                       # an accessibility/flow run, e.g. p95)
# s.destination_weight_column = "size"  # facility attractiveness column on
#                                       # the candidates layer (optional;
#                                       # unit values when unset)

# Impedance — everything the other engines support applies here too.
s.elevation            = False   # True with a 3D network
s.turns                = False   # True for turn-aware costs

s.output_folder        = OUTPUT_FOLDER
s.output_wStamp        = False
s.output_geojson       = True
s.output_file_name     = "fa_run"

una.RunFacilityAllocation()

# --------------------------------------------------------------------------
# PART 2 — flow analysis on the chosen configuration (optional)
# --------------------------------------------------------------------------

if RUN_FLOW_ON_RESULT:
    # These are only needed for the chaining step: geopandas reads the
    # facilities output back in to filter the selected sites, os joins
    # the file paths. Part 1 needs neither.
    import os
    import geopandas as gpd

    fac = gpd.read_file(os.path.join(OUTPUT_FOLDER, "fa_run_facilities.geojson"))
    chosen = fac[fac["selected"] == 1]
    chosen_file = "chosen_facilities.geojson"
    chosen.to_file(os.path.join(DATA_FOLDER, chosen_file), driver="GeoJSON")
    print(f"{len(chosen)} facilities selected -> {chosen_file}")

    s.destinations_file  = chosen_file
    s.flow_engine        = "aggregate_flow"
    s.flow_decay         = True
    s.flow_decay_method  = "closest"
    s.output_file_name   = "fa_run_flow"

    una.RunFlow()
