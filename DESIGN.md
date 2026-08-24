# UNA — Design & Usage

Urban Network Analysis Python package for computing accessibility metrics and estimating pedestrian and bicycle flows over street networks.

---

## Scope discipline — a tight, contained core

The `source/` package is deliberately kept small and single-purpose. It owns:

- Network topology loading and CSR construction
- Impedance modeling (elevation, turns, obstacles)
- Dijkstra-based accessibility and flow engines
- Result export (feather / geojson / csv)
- Batch execution across a list of `Settings` snapshots

Anything else — visualization, notebooks, workshop tutorials, custom analysis scripts, plugin integrations, downstream data pipelines — lives **outside** the package as user scripts (e.g. `UNA_Workspace.py`, `UNA_Batch.py`) or as separate repositories. The rule of thumb: if a new capability doesn't strictly need to touch the CSR or the Settings dataclass, it belongs outside `source/`. This keeps the package easy to review, easy to package for distribution, and easy for external contributors to build tools on top of.

---

## Terminology

UNA uses three terms with distinct, non-overlapping meanings. Getting these right avoids most of the confusion new contributors run into when reading the codebase.

| Term | What it is | Where it lives |
|------|------------|----------------|
| **Engine** | A class that runs Dijkstra (or a path-enumeration variant) and produces a per-edge / per-node / per-origin scalar array. Selected by `RunAccessibility()` / `RunFlow()` based on Settings flags. | `source/Engines/` — `Accessibility`, `AccessibilityWElevation`, `AccessibilityWTurns`, `Flow`, `AggregateFlow`. Shared abstract exporter in `Engines/Base.py`. |
| **Analysis** | The top-level category flag on `RunBatch(analysis=…)` — currently `"accessibility"` or `"flow"`. It tells RunBatch which family of Run* method (and therefore which Engine) to dispatch to for each row. | Parameter of `UNA.RunBatch`. Extended to new values as new top-level Run* methods are added. |
| **Project** | A saved list of `Settings` snapshots — i.e. "a batch of runs I want to execute sequentially, expressed as data." Materialised on disk as `project.json` or `pairings.csv`. It is data, not code. | `una.projects` (Python list of Settings instances). Persisted via `SaveSettingsToProject`, `ExportProjectAsJSON`, `ExportProjectAsCSV`, `ConvertProject_csv_to_json`. |

**The three are orthogonal.** A **project** is a batch of any **analysis** (`accessibility`, `flow`, …), and each row of a project ultimately triggers one **engine**. The word "project" is reserved exclusively for the saved-Settings-list meaning above.

---

## Architecture

```
UNA                        ← public API, owns Settings and Topology
  ├── Settings             ← single dataclass, all parameters live here
  ├── Topology             ← network + origin/destination data, spatial indices
  └── Engines/             ← Dijkstra-based per-edge / per-origin scalar producers
        ├── Base           ← abstract base, all export methods
        ├── Accessibility
        ├── AccessibilityWElevation
        ├── AccessibilityWTurns
        ├── Flow           ← K-alternatives penalty-method engine
        └── AggregateFlow  ← marginal-flow engine for state-wide scale
```

`UNA` itself also carries the batch machinery — `RunBatch(analysis, pairing_file=…)` iterates a list of Settings snapshots, dispatches to `RunAccessibility()` or `RunFlow()` per row, and — when any row sets `batch_composite_output=True` — assembles a joint composite output file after all rows complete. This lives directly on the `UNA` class (no separate Tools subsystem).

**Data flow for a single run:**
User interacts with `UNA_Workspace.py`, where all settings can be assigned.

1. In `UNA_Workspace.py`, user sets parameters as `una.settings.parametername`.
2. `RunAccessibility()` or `RunFlow()` calls `topology.AddNetwork/Origins/Destinations(settings)`.
3. UNA selects the appropriate engine based on settings flags.
4. Engine runs Dijkstra / path enumeration, stores results as numpy arrays on itself.
5. Engine calls `Base.Export*()` to write results to disk.

Topology is rebuilt at every run — it is not cached between calls. Engines do not mutate topology.

---

## Engines — the only computational contract

All computation in UNA happens through Engines. An Engine is a Dijkstra-based network traverser whose output is a per-edge, per-node, or per-origin numeric array stored as an instance attribute. Engines share a strict interface:

- Constructor takes `topology` only; Settings flow in through `Centrality(settings)`.
- Implement `Centrality(settings)`; store results as instance arrays.
- Call one of `Base`'s export methods for file I/O — engines never open files directly.
- No mutation of the Topology object.
- All configurable parameters read from the shared `Settings` dataclass; no ad-hoc constructor args or module-level state.

New engines follow the same rules: one Settings prefix per subsystem (`flow_*`, `accessibility_*`, etc.), all validation in `Settings.Validation()`, all exports through `Base`.

If a new capability doesn't fit this contract (e.g. it needs to produce joined multi-column tables, PDF reports, interactive HTML), the correct home for it is a **user-side script** that composes UNA's public API — not a new subsystem inside `source/`. Anything the package must load, save, or route across engines should be added to `Settings` and `Base`; everything else lives outside the package.

---

## Settings

All configuration lives in a single `@dataclass Settings` in `source/Settings.py`. There are no hidden settings elsewhere.

```python
una.settings.search_radius = 1500
una.settings.elevation     = True
una.settings.output_geojson = False
```

Key field groups:

| Group | Fields |
|-------|--------|
| **Input** | `data_folder`, `network_file`, `origins_file`, `destinations_file`, `network_weight_column` |
| **Accessibility** | `calculate_reach`, `calculate_exponential_gravity`, `calculate_logistic_gravity`, `calculate_knn_access`, `search_radius`, `gravity_beta`, `knn_weights` |
| **Impedance** | `elevation`, `elevation_penalty`, `turns`, `turn_threshold`, `turn_penalty` |
| **Flow** | `flow_engine`, `flow_detour_ratio`, `flow_detour_mode`, `flow_decay`, `flow_decay_method`, `flow_path_detour_penalty`, `flow_origin_weights`, `flow_destination_weights`, `flow_return_directional` |
| **Batch composite** | `batch_composite_output`, `batch_composite_result_column`, `batch_composite_column_prefix`, `batch_composite_sum_column_name` |
| **Observer / Obstacle** | `observer_points_file`, `obstacle_points_file`, `obstacle_points_penalty_column` |
| **Output** | `output_folder`, `output_geojson`, `output_feather`, `output_csv`, `output_file_name`, `result_prefix` |

`settings.Validation()` is called automatically before each run, which checks enum-like fields.

`settings.Reset()` restores all fields to defaults.

**Design principle — trust Settings.** Every parameter the engines and batch code read is declared in `Settings.py` with a default; `Validation()` runs before every engine call. Consumer code MUST NOT sprinkle `getattr(settings, "foo", default)`, `settings.foo or "default"`, or `if not settings.foo:` guards for fields that exist in Settings. If a field is missing, the fix belongs in `Settings.py`, not in the consumer. This keeps engines and the batch layer lean.

**Serialization**

```python
una.SaveSettings("settings.json")      # writes all fields
una.LoadSettings("settings.json")      # replaces current settings

settings.ToDict(compact=True)          # only non-default fields
settings.ToDict(compact=False)         # all fields (for documentation / CSV export)
```

---

## Engine Selection

`RunAccessibility()` picks the engine based on settings flags:

```
turns=True                  → AccessibilityWTurns    (handles both turns and elevation)
elevation=True, turns=False → AccessibilityWElevation
otherwise                   → Accessibility          (base Dijkstra)
```

`RunFlow()` picks between two flow engines based on `settings.flow_engine`:

```
flow_engine="k_alternatives" (default) → Flow           (path-enumerating K-alternatives)
flow_engine="aggregate_flow"           → AggregateFlow  (marginal-flow, scales to state-wide)
```

Both flow engines populate the same result attributes (`edge_flow`, `edge_flow_AB`, `edge_flow_BA`, `node_flow`), so batch composite output and export methods work with either engine unchanged.

Engines inherit from `Base` and must implement `Centrality(settings)`. They store results as instance arrays (`self.reach`, `self.edge_flow`, etc.) and call the inherited export methods.

---

## Base Class Role

`Base` (`source/Engines/Base.py`) provides all export logic. Engines do not implement their own file I/O.

| Method | Used by |
|--------|---------|
| `ExportAccessibilityResults(settings, folder_prefix, file_name)` | Accessibility engines |
| `ExportFlowResult(settings, folder_prefix, file_name)` | Flow, AggregateFlow |
| `ExportODM(settings, folder_prefix, file_name, format, speed)` | Any engine via RunODM |
| `_write_observer_points()` | Flow (called internally by ExportFlowResult) |
| `_write_obstacle_points_usage()` | Flow |
| `_write_network_nodes_flow()` | Flow, AggregateFlow |
| `_write_destinations_used_origins()` | Flow |

Base also exposes shared write plumbing (`resolve_output_folder`, `write_gdf_outputs`) that `UNA.RunBatch` uses when composing the batch composite output — this is the only place outside `Engines/` that touches file I/O, and it does so through Base's helpers rather than duplicating logic.

Export format is controlled by `settings.output_feather / output_geojson / output_csv`.

---

## Basic Usage

```python
from source import UNA

una = UNA()

# Configure
una.settings.data_folder        = r".\Data"
una.settings.network_file       = "network.geojson"
una.settings.origins_file       = "origins.feather"
una.settings.destinations_file  = "destinations.feather"
una.settings.search_radius      = 1500
una.settings.output_file_name   = "run_1500m"

# Run
una.RunAccessibility()
una.RunFlow()

# OD matrix (independent, no prior RunAccessibility needed)
una.settings.origin_uid_column     = "id"
una.settings.destination_id_column = "id"
una.RunODM(format="Sqlite", speed=5.0)
```

---

## Batch Workflow

`RunBatch(analysis, pairing_file=…)` executes many Settings snapshots in one call. Snapshots come from either a CSV/TSV/JSON pairings file or from an in-memory list built with `SaveSettingsToProject`.

```python
# From a pairings file — one row per run
una.RunBatch("flow", pairing_file="Boston_Flow_Pairings.csv")

# Or build in code, then run
una.settings.search_radius = 1500
una.settings.name = "run_1500m"
una.SaveSettingsToProject()

una.settings.search_radius = 3000
una.settings.name = "run_3000m"
una.SaveSettingsToProject()

una.RunBatch("accessibility")   # runs whatever is in una.projects
```

**Batch composite output.** When any pairings row sets `batch_composite_output=True`, `UNA.RunBatch` captures each row's chosen engine result attribute (`reach`, `gravity_exponential`, `gravity_logistic`, `knn_access`, `edge_flow`, or `node_flow`; configurable via `batch_composite_result_column`), auto-selects the join target based on the metric (per-origin metrics join to `origins_file`; `edge_flow` joins to `network_file`; `node_flow` joins to network nodes built from the live Topology), and writes one composite file per enabled `output_*` flag with a row-wise sum column (default name `composite_sum`, renamable via `batch_composite_sum_column_name`). This is exposed to users through the thin `UNA_Batch.py` wrapper script.

**Project serialization** — snapshots are frozen with `deepcopy` at save time, so changing settings after saving does not affect earlier entries.

```python
una.ExportProjectAsJSON(folder=r".\Settings", file_name="project.json")
una.ExportProjectAsCSV()          # written to settings.data_folder
```

When reading a CSV back, `data_folder` is automatically set from the CSV file's directory — it is never written into the CSV export for this reason.

**`ConvertProject_csv_to_json`** converts an older-style pairing CSV to the JSON project format:

```python
una.ConvertProject_csv_to_json("pairings.csv", "project.json", compact=True)
```

---

## Impedance Model Summary

All impedance factors stack additively on edge weights:

```
effective_cost = geometric_distance
              + elevation_gain × elevation_penalty   (if elevation=True)
              + turn_count × turn_penalty            (if turns=True)
              + obstacle_penalty                     (if obstacle points present)
```

Elevation and obstacle penalties are precomputed into the CSR adjacency arrays before Dijkstra runs. Turn penalties require a line-graph transformation (each directed edge becomes a node; turning cost is added at the arc connecting two edges).

---

## Output Files

All results land in `settings.output_folder`. When `output_wStamp=True` (default), a timestamped subfolder is created per run.

| Engine | Files written |
|--------|--------------|
| Accessibility | `{name}.feather`, `.geojson`, `.csv` (one file with all metrics) |
| Flow / AggregateFlow | `{name}.feather/.geojson` (edge flow), `*_nodes.*`, `*_observers.*`, `*_obstacles.*`, `*_destinations_used_origins.*` |
| ODM | `ODM_{name}.db` (SQLite), `.feather`, `.csv`, or `.tsv` |
| Batch composite | `composite_{name}.geojson / .feather / .csv` (one per enabled output flag; CSV is written by default if the batch requested composite output but no other output flag is enabled) |

---

## Extending the package

Before adding a new subsystem, ask: **does it need to run inside `source/`, or can it be a user-side script that calls UNA's public API?** Prefer the second answer.

- **New Engine.** Only if it produces a new kind of Dijkstra-based per-entity scalar not covered by existing engines. Follow the Base contract; declare all params in Settings; register the dispatch in `RunAccessibility()` or `RunFlow()`.
- **New impedance mode.** Add fields to Settings, precompute into CSR weights inside Topology or the engine's setup, done.
- **New batch behavior.** Add the fields to Settings, extend `RunBatch` on the UNA class — no new module.
- **Anything else** (report generation, notebook workflows, plugin front-ends, interactive dashboards, tutorial-specific data prep): keep it outside `source/`, in the user's own repo or a companion script. The package stays lean; the extension stays flexible.
