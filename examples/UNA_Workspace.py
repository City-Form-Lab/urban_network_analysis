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

# --- data -----------------------------------------------------------
una.settings.data_folder       = r"/path/to/Boston"
una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
una.settings.origins_file      = "MIT_SAP_flow_origin.geojson"
una.settings.destinations_file = "Harvard_HousingCtr.geojson"

# --- shared flow parameters -----------------------------------------
una.settings.search_radius            = 4000
una.settings.flow_detour_ratio        = 1.15
una.settings.flow_decay               = False    # no distance decay on trip generation
una.settings.flow_origin_weights      = True     # origin emits its weight (= 100 trips)
una.settings.origin_weight_column     = "weight"
una.settings.flow_destination_weights = False    # single destination — no Huff needed
una.settings.turns                    = False
una.settings.elevation                = False

una.settings.output_wStamp = False               # fixed output paths

# --- run 1: K-alternative paths -------------------------------------
una.settings.flow_engine      = "k_alternatives"
una.settings.output_file_name = "flow_k_alternatives"
una.RunFlow()

# --- run 2: aggregate flow (same inputs, different engine) ----------
una.settings.flow_engine      = "aggregate_flow"
una.settings.output_file_name = "flow_aggregate"
una.RunFlow()
