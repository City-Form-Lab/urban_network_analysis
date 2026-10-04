"""
UNA_Workspace.py — single-analysis driver.

Copy this file anywhere, point it at your data, and run it. This
example runs one origin → one destination through both flow engines on
identical inputs; only ``flow_engine`` changes between the runs.

Outputs (no timestamp subfolder, so paths are predictable):
  <data_folder>/Results/flow_k_alternatives.geojson
  <data_folder>/Results/flow_aggregate.geojson
"""

import urban_network_analysis

una = urban_network_analysis.UNA()
una.settings.data_folder          = r"/Users/andressevtsuk/City Form Lab Dropbox/Andres Sevtsuk/00_ClaudeWork/una-pkg/docs/Boston"
una.settings.network_file         = "20260703_PercLenNetwork_InnerCore.geojson"
una.settings.origins_file         = "Cambridge_building_centroids_pop2020.geojson"
una.settings.origin_weight_column = "pop2020"
una.settings.destinations_file    = "/Users/andressevtsuk/City Form Lab Dropbox/Andres Sevtsuk/00_ClaudeWork/una-pkg/docs/Boston/Results/facility_allocation_2026-10-02_1037/Results_facilities_selected.geojson"
una.settings.fa_existing_facilities_column = "existing"
una.settings.flow_engine        = "aggregate_flow"
una.settings.flow_decay_method  = "gravity_cap"   # matches (h)
una.settings.flow_gravity_cap   = 1.0             # pinned, as in (h)
una.settings.destination_weight_column = "attract" # same attractiveness as (h)

una.settings.search_radius    = 800        # service cutoff (meters)
una.settings.flow_decay       = True
una.settings.flow_decay_curve = "exponential"
una.settings.gravity_beta     = 0.002
 
una.settings.fa_problem_type   = "max_patronage"
una.settings.fa_new_facilities = 3            # evaluation run, examine only existing
una.settings.flow_gravity_cap  = 1.0
una.settings.flow_decay        = True
# una.RunFacilityAllocation()

una.RunFlow()

