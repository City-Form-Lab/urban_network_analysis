"""
UNA_Batch.py — generic UNA batch driver.

Copy this file anywhere (next to your data is convenient), edit the
three values below, and run it. Works across accessibility (Reach,
Gravity, KNN) and flow (edge betweenness) analyses from the same
pairings CSV.

Paths inside the pairings CSV resolve relative to the CSV's own
directory, so the same CSV works unchanged on any machine; only
OUTPUT_FOLDER is typically machine-specific.

When any pairings row sets ``batch_composite_output = True``, UNA writes
one additional composite file per origin layer — one column per row's
chosen result attribute (``batch_composite_result_column``: reach,
gravity_exponential, gravity_logistic, knn_access, edge_flow,
node_flow) plus a row-wise sum column.
"""

import urban_network_analysis

# --------------------------------------------------------------------------
# EDIT THESE THREE VALUES
# --------------------------------------------------------------------------

PAIRINGS_FILE = r"/Users/andressevtsuk/City Form Lab Dropbox/Andres Sevtsuk/00_ClaudeWork/Maine_KNN/destinations_within_3680m_of_195camercounts/Maine_statewide_betweenness_pairings_3680m_nodeflow_rest.csv"
OUTPUT_FOLDER = r"/Users/andressevtsuk/City Form Lab Dropbox/Andres Sevtsuk/00_ClaudeWork/Maine_KNN/destinations_within_3680m_of_195camercounts/Results"

# Which analysis to run per row — must match the metric in your pairings CSV:
#   "accessibility"  — for reach / gravity_* / knn_access composites
#   "flow"           — for edge_flow or node_flow composites
ANALYSIS = "flow"

# --------------------------------------------------------------------------
# Run the batch (per-row files + optional composite file)
# --------------------------------------------------------------------------

una = urban_network_analysis.UNA()
una.settings.output_folder = OUTPUT_FOLDER

una.RunBatch(ANALYSIS, pairing_file=PAIRINGS_FILE)
