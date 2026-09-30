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
  <name>_facilities.geojson   candidates + selected/existing/rank/
                              demand_served/access_captured columns
  <name>_demand.geojson       demand + assigned_facility/distance/
                              access/covered columns
  <name>_summary.json         objective, solver used, coverage totals
"""

import urban_network_analysis

# --------------------------------------------------------------------------
# EDIT THESE VALUES
# --------------------------------------------------------------------------

DATA_FOLDER   = r"/Users/andressevtsuk/City Form Lab Dropbox/Andres Sevtsuk/_MIT_Fall2026/11.024:11.324/04_Assignments/Personal/Exercise2/una_Facility_Allocation"
NETWORK       = "downtown_centerline_network.geojson"
DEMAND        = "DT_building_centroids_800m_pop_jobs.geojson"    # origins = demand points
DEMAND_WEIGHT = "pop_est"                       # demand weight column
CANDIDATES    = "candadate_locations_999_2026.geojson"       # destinations = candidate facilities
EXISTING_COL  = "required"                      # truthy = already-open facility; None if none. Treats it as not required when it's empty, NaN, 0, 0.0, false, no, or none (case-insensitive) — anything else means required
OUTPUT_FOLDER = r"/Users/andressevtsuk/City Form Lab Dropbox/Andres Sevtsuk/_MIT_Fall2026/11.024:11.324/04_Assignments/Personal/Exercise2/una_Facility_Allocation/Results"

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
s.fa_existing_facilities_column   = EXISTING_COL

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
# output_wStamp defaults to True: each run writes into a new
# timestamped subfolder, never overwriting earlier results — the same
# convention as accessibility and flow runs.
s.output_geojson       = True
s.output_file_name     = "fa_run"

una.RunFacilityAllocation()

# --------------------------------------------------------------------------
# PART 2 — flow analysis on the chosen configuration (optional)
# --------------------------------------------------------------------------

if RUN_FLOW_ON_RESULT:
    # Only needed for the chaining step (Part 1 needs neither): the
    # chosen facilities are taken straight from the engine's in-memory
    # results — timestamp-proof, no reading output files back in.
    import os
    import geopandas as gpd

    e = una.facility_allocation
    dest = una.topology.destinations
    fac = gpd.GeoDataFrame(
        {"uid": list(dest.uid), "selected": e.fa_selected},
        geometry=gpd.GeoSeries(dest.geometry).reset_index(drop=True),
        crs=getattr(dest.geometry, "crs", None),
    )
    chosen = fac[fac["selected"] == 1]
    chosen_file = "chosen_facilities.geojson"
    chosen.to_file(os.path.join(DATA_FOLDER, chosen_file), driver="GeoJSON")
    print(f"{len(chosen)} facilities selected -> {chosen_file}")

    # All Part 1 parameters carry over on the same settings object
    # (search_radius, weights, gravity_beta, decay curve, elevation,
    # turns, ...). Only what must change for a flow run is set here —
    # including the trip-generation method that MATCHES the siting
    # objective, so Part 2 estimates flows under the same behavioral
    # model that chose the sites:
    #   max_access / min_facilities → "closest" (decay at the nearest
    #       facility — the access objective's model)
    #   max_patronage → "gravity_cap" (saturating participation, same
    #       flow_gravity_cap — the patronage objective's model)
    # flow_decay itself is inherited from Part 1 (False stays False for
    # pure-coverage runs).
    s.destinations_file  = chosen_file
    s.flow_engine        = "aggregate_flow"
    s.flow_decay_method  = "gravity_cap" if PROBLEM == "max_patronage" else "closest"
    s.output_file_name   = "fa_run_flow"

    una.RunFlow()
