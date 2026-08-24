

from dataclasses import dataclass, asdict, field
import numpy as np
import json
import re
import math
from pathlib import Path
import os
from typing import Literal


_NUMERIC_ARRAY_RE = re.compile(
    r'\[\s*(-?[\d.]+(?:[eE][+-]?\d+)?(?:\s*,\s*-?[\d.]+(?:[eE][+-]?\d+)?)*)\s*\]'
)

def _json_dumps(data, indent: int = 2) -> str:
    """json.dumps with indent, but numeric arrays stay on a single line."""
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    def _inline(m: re.Match) -> str:
        nums = re.split(r'\s*,\s*', m.group(1).strip())
        return '[' + ', '.join(nums) + ']'
    return _NUMERIC_ARRAY_RE.sub(_inline, text)


@dataclass
class Settings:

    _version: str = "1.0" # settings version, for future compatibility checks when loading old settings files. Update this if you make any changes to the settings structure or default values.

    name: str = "Default"  # if paring files are converted to settings then there is a flow_name. we can use it to name the settings file. if not then we can use this default name.


    data_folder: str = ""  # Define the folder where your input data files are located


    network_file: str = "Network.feather"  # Define the filename for the network data
    origins_file: str = "origins.feather"  # Define the filename for the origin points data
    destinations_file: str = "destinations.feather"  # Define the filename for the destination points data

    network_weight_column: str = "Geometric"  # Define the column name in the network data that contains the cost/distance values for routing
    network_weight_default: float = 1.0
    network_precision: int = 3 # Define the number of decimal places to round network costs to, for more efficient graph processing (optional)
    network_saved_nodes: bool = False # Define if the node information from the original network file should be preserved in the internal graph representation (optional, default=false). If false, only the node IDs and their connectivity are preserved, which can save memory and speed up processing for large networks. Node id is saved as _node_start_id and _node_end_id in the output files.
    network_load_nodes: bool = True # if True and curve has _node_start_id and _node_end_id is present then we will try to load nodes

    ##——— CHOOSE WHICH ACCESSIBILITY INDICES TO COMPUTE———

    calculate_reach:               bool = True
    calculate_exponential_gravity: bool = True
    calculate_logistic_gravity:    bool = True
    calculate_knn_access:          bool = True

    ##——— ACCESSIBILITY ANALYSIS SETTINGS———

    origin_weight_column: str = "Count"
    destination_weight_column: str = "Count"

    origin_uid_column: str = None        # Column in origin data to use as a unique identifier for each origin point; used for labeling in flow tracking output. Optional.

    # Per-origin destination assignment — when both columns are set, each origin
    # is routed ONLY to destinations whose destination_id_column value matches
    # the origin's origin_destination_id_column value (e.g. route each school
    # only to bus stops in the same district).  Leave both empty for the default
    # behaviour: every origin routes to all reachable destinations.
    origin_destination_id_column: str = ""   # Column in origin data holding the destination-group ID for per-origin routing assignment.
    destination_id_column: str = ""          # Column in destination data holding the group ID matched against origin_destination_id_column; also used as the destination UID in output files.

    default_cost: float = 1.0

    use_nearest_destination: bool = False  # Define if only a single nearest destination should be used for each origin, default=false
    search_radius: int = 1500

    clasters: int = 1 # Define the number of clusters to use for parallel processing. If 1, no clustering is applied.
    cluster_parallel: bool = False  # If True, process clusters in parallel during centrality
    cluster_workers: int = 1       # Maximum cluster workers when parallel cluster processing is enabled

    gravity_beta: float = 0.001 # Distance decay rate (exponential decay's β; logistic auto-derived k = ln(99) / midpoint)
    gravity_logistic_midpoint: int = 500
    gravity_plateau: int = 0 # Optionally define a "flat" plateau network radius around each origin point, inside which no distance decay is applied.
    gravity_decay_constant: float = math.log(99)  # standard logistic convention: ln(99)/midpoint puts the 1%/99% endpoints at d=±midpoint


    knn_weights: tuple = (1.0, 1.0, 0.5) # Used by ACCESSIBILITY engines (Reach/Gravity/KNN).  In v2.4.3 the flow elastic feature no longer uses this.
    knn_decay: Literal["none", "exponential", "logistic"] = "logistic"

    ##——— ADDITIONAL OPTIONAL IMPEDANCE FACTORS———

    elevation: bool = False  # Define if moving up in elevation should add additional incremental distance penalties
    elevation_penalty: int = 4 # Define the numeric magnitude of the elevation penalty: how many distance units are added per each vertical unit of elevation gain.

    turns: bool = False # Define if turns along the route should add additional incremental distance penalties
    turn_threshold: int = 45  # Define an angle (in degrees) that constitutes a turn, measured at a node where two or more network edges meet, e.g. 45.
    turn_penalty: int = 32 # Define the numeric magnitude of the turn penalty: how many distance units are added per each turn

    ##——— OUTPUT OPTIONS———

    output_folder: str = None # Define an output folder, default is the same as your input Data folder
    output_wStamp: bool = True # if true then we another folder with timestamp (YYYY-MM-DD_HHMM)

    output_copy_source_data: bool = False # Define if the original input data should be copied to the output folder along with the results, default is false

    # Define the format in which the outputs should be saved
    output_geojson: bool = True
    output_feather: bool = True
    output_csv: bool = False
    output_shp: bool = False
    csv_delimiter: str = ","

    result_prefix: str = ""  # prefix for accessibility result columns, e.g. "accessibility_"

    #output_folder_prefix: str = "Prefix_" # It is used in centrality export
    output_file_name: str = "Results"   # It is used in centrality export


    ##——— FLOW SETTINGS———

    # Flow engine selection (v2.5.2):
    #   "k_alternatives" — path-enumerating penalty-method engine (Flow).
    #                      Full path-level fidelity; cost grows with K and
    #                      network size.  Default.
    #   "aggregate_flow" — marginal-flow engine (AggregateFlow).  One
    #                      forward DAG sweep per OD pair over the reachable
    #                      subgraph; scales to state-wide analyses where
    #                      k_alternatives becomes infeasible.
    flow_engine: Literal["k_alternatives", "aggregate_flow"] = "aggregate_flow"

    flow_detour_ratio: float = 1.05           # Maximum path length as a ratio of the shortest path (1.05 = 5% detour allowed)
    flow_detour_buffer: float = 100.0           # Maximum extra distance allowed for alternative paths (in same units as network distances, e.g. meters)
    flow_detour_mode: Literal["ratio", "buffer", "min"] = "ratio"

    # =========================================================
    # v2.4.3: DECAY MODEL FOR TRIP GENERATION
    # =========================================================
    # The decay model controls how each origin's effective trip-generation
    # weight responds to its accessibility to the destination set.  Two
    # methods are available, distinguished by `flow_decay_method`:
    #
    #   "closest"   — factor = decay_curve(distance to nearest destination)
    #                 No additional user input required beyond the curve
    #                 parameters (beta, plateau, midpoint).  Trip generation
    #                 is fully determined by the distance to your single
    #                 nearest reachable destination.  Adding destinations
    #                 closer than the current closest INCREASES the factor;
    #                 adding farther destinations leaves it unchanged.
    #                 Monotonic in adding any destination.
    #
    #   "gravity_cap" — factor = min(1, gravity / flow_gravity_cap)
    #                 Where `gravity` is the Huff numerator, i.e. the sum of
    #                 destination weights × distance decay within
    #                 search_radius (or, if flow_destination_weights
    #                 = False, just the count of destinations).  Captures
    #                 destination density weighted by access cost: a far-away
    #                 destination contributes less to the cap than a near
    #                 one of the same weight.  Adding ANY reachable
    #                 destination increases the factor (until the cap
    #                 saturates).  Requires the user to set
    #                 flow_gravity_cap > 0.  When flow_decay
    #                 = False, `gravity` collapses to raw reach (no decay).
    #
    # When `flow_decay = False`, every origin gets factor = 1.0 and
    # the decay_method choice is ignored.
    #
    # The decay CURVE shape (exponential vs logistic) is set separately by
    # `flow_decay_curve` and is consumed by the "closest" factor
    # calculation AND by the destination-choice Huff math (where it appears
    # inside gravity[d] = weight × decay).
    flow_decay: bool = True                   # Apply distance decay to trip generation
    flow_decay_curve: Literal["exponential", "logistic"] = "exponential"
    flow_decay_method: Literal["closest", "gravity_cap"] = "closest"
    flow_gravity_cap: float = 1.0               # Required if decay_method = "gravity_cap"; cap value in (destination-weight × decay) units (or count units if d_weights = False)

    flow_path_detour_penalty: Literal["equal", "exponential", "logistic"] = "logistic"

    # Decouple path-penalty decay from OD-level gravity decay.
    flow_route_enumeration_beta: float = 0.0
    flow_route_enumeration_logistic_midpoint: float = 150.0

    flow_origin_weights: bool = True          # Scale contributions by origin weight
    flow_destination_weights: bool = True     # Use destination weights in gravity-based trip probability (Huff model)

    flow_return_directional: bool = False # If True, compute directional flow (A->B may differ from B->A). If False, contributions are averaged across both directions for an undirected result.

    # =========================================================
    # OBSERVER POINTS (v2.4.4) — passive flow counters.
    # Used in flow analysis ONLY.  Each observer point snaps to
    # an edge (default) or a node; after a run, you get per-point
    # flow_AB / flow_BA / flow_total counters in an extra output file.
    # Observer points do NOT have weights and do NOT influence routing.
    # =========================================================
    observer_points_file: str | None         = None
    observer_points_uid_column: str | None   = None
    observer_points_snap_to: Literal["edge", "node"] = "edge"

    # =========================================================
    # OBSTACLE POINTS (v2.4.4) — discrete penalty additions.
    # Each obstacle point adds a penalty (in network-weight units) to
    # the cost of traversing its host edge or node.  Used by BOTH
    # flow and accessibility engines.  Stacks additively with
    # elevation, turns, and any custom edge cost column.
    # =========================================================
    obstacle_points_file: str | None             = None
    obstacle_points_uid_column: str | None       = None
    obstacle_points_penalty_column: str          = "penalty"
    # Direction column — per-obstacle values 'both' | 'AB' | 'BA'.
    # When None or the column is missing, every obstacle defaults to
    # 'both' (penalty applied to both arc directions of the host edge).
    obstacle_points_direction_column: str | None = None
    obstacle_points_snap_to: Literal["edge", "node"] = "edge"
    # When True, the flow engine writes an extra output file
    # with per-obstacle path-hit counters (AB / BA / total).
    flow_track_obstacle_points_usage: bool = False

    # K-nearest destinations to actually route to (per origin).
    # 0 = no user cap (engine still enforces an internal ceiling of 4000
    # for memory safety).  When the cap triggers, the engine sorts
    # reachable destinations by shortest network distance and keeps the
    # first k — composable with use_nearest_destination (which is k=1).
    flow_k_nearest_destinations: int = 0

    # Deprecated alias for `flow_k_nearest_destinations`.  Will
    # be removed in a future release.  If both are set, the new name
    # wins and a warning is printed.
    flow_max_destinations_per_origin: int = 0

    # =========================================================
    # PENALTY-METHOD K-ALTERNATIVE PATHS (sole enumeration mode).
    # =========================================================
    flow_n_alternatives: int = 30
    flow_alternative_penalty_factor: float = 1.01

    # =========================================================
    # ROUTE-ALTERNATIVES OUTPUT (v2.5.2) — for route-choice studies.
    # =========================================================
    # flow_route_id_column — name of a column present in BOTH the origin
    #   and destination files (text or numeric).  Each origin is routed
    #   only to the destination(s) sharing its value, and the value is
    #   written to every exported route so externally observed routes
    #   (GPS traces, surveys) can be matched to their generated
    #   alternatives.  Internally this drives the same pairing machinery
    #   as origin_destination_id_column / destination_id_column — setting
    #   those two directly (to different columns per file) also works.
    # flow_output_routes — when True, the k_alternatives engine exports
    #   the complete generated routes (one row per alternative, full
    #   geometry) alongside the flow results, as <file_name>_routes.*.
    #   Columns: route_id, origin_uid, dest_uid, alt_rank, route_cost,
    #   n_edges, edge_ids, geometry.
    flow_route_id_column: str | None = None
    flow_output_routes: bool = False

    # Per-path diagnostic print.  When True, each emitted alternative path
    # prints a one-liner to stdout with destination index, total cost, arc count,
    # and (when turns are enabled) number of turns.  Useful for small tests
    # (single OD, tight detour_ratio).  Leave False in production runs.
    flow_debug_print_paths: bool = False

    flow_track_origins_per_destination: bool = False
    flow_compute_node_flow: bool = False

    # NOTE (v2.4.3): the previous KNN-based elastic feature
    # (flow_elastic_origin_weight, flow_elastic_min_factor,
    # flow_elastic_low_percentile) has been REMOVED.  Trip-generation
    # elasticity is now governed by the flow_decay_method setting above
    # ("closest" or "gravity_cap").  Both methods are monotonic — adding any
    # reachable destination never decreases an origin's effective trip-generation
    # weight, fixing the non-monotonicity that affected the gravity-decay
    # implementation in earlier versions.

    ##——— BATCH COMPOSITE OUTPUT (Tool: BatchCompositor) ———
    # When RunBatch runs several rows against a shared origins layer, these
    # settings ask UNA to also assemble a single joint output file with one
    # column per row's engine result plus a row-wise sum column. See
    # source/Tools/BatchCompositor.py.  All fields use the `batch_composite_`
    # prefix per DESIGN.md's "one prefix per subsystem" rule.
    batch_composite_output: bool = False                 # master switch — off = current per-row behavior only
    batch_composite_result_column: Literal[
        "reach", "gravity_exponential", "gravity_logistic",
        "knn_access", "edge_flow", "node_flow"
    ] = "knn_access"                                     # which engine attribute to include per row
    batch_composite_column_prefix: str = ""              # prepended to each per-row column name; blank uses row's `name`
    batch_composite_sum_column_name: str = "composite_sum"  # column name for the row-wise sum

    ##——— OTHER SETTINGS———

    progressbar: bool = True # Define if a progress bar should be shown during calculations (for long-running processes)
    logger_verbosity: int = 1 # Define the verbosity level for logging (0 = errors only, 1 = summary, 2 = detailed)



    def __post_init__(self):
        if not isinstance(self.knn_weights, np.ndarray):
            try:
                self.knn_weights = np.array(self.knn_weights)
            except Exception as e:
                print(f"Warning: Could not convert knn_weights to np.ndarray: {e}")

    def ApplyRow(self, row: dict) -> None:
        """Reset to defaults then apply one row of a pairing CSV/TSV.

        Column names must match Settings field names exactly; unknown columns
        are silently ignored (e.g. flow_name, destination_name).
        Empty / NaN cells are skipped so the Settings default is kept.
        """
        import dataclasses as _dc

        self.Reset()

        valid_fields = {f.name: f for f in _dc.fields(self)}
        for key, raw in row.items():
            if key not in valid_fields:
                continue
            if raw is None or str(raw).strip() in ('', 'nan', 'NaN', 'NaT'):
                continue

            field   = valid_fields[key]
            default = field.default  # _dc.MISSING if factory-based — rare in Settings

            try:
                if key == 'knn_weights' or isinstance(default, (tuple, np.ndarray)):
                    # Accept both JSON list style "[1, 1, 0.5]" and Python
                    # tuple style "(1, 1, 0.5)" (the format Settings
                    # defaults print in).
                    s = str(raw).strip()
                    if s.startswith('(') and s.endswith(')'):
                        s = '[' + s[1:-1] + ']'
                    value = np.array(json.loads(s))
                elif default is not _dc.MISSING and isinstance(default, bool):
                    s = str(raw).strip().lower()
                    value = s in ('true', 't', '1', 'yes', 'y')
                elif default is not _dc.MISSING and isinstance(default, int):
                    value = int(float(str(raw)))
                elif default is not _dc.MISSING and isinstance(default, float):
                    value = float(str(raw))
                else:
                    value = str(raw).strip() or None
                setattr(self, key, value)
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                print(f"Warning: pairing-row value {raw!r} for '{key}' could not be "
                      f"parsed ({e}); keeping the default {default!r}.")

    def Load(self, file_path: str):
        valid_fields = set(self.__dataclass_fields__.keys())
        input_path = Path(file_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Settings file not found: {input_path}")
        with open(input_path, 'r', encoding='utf-8') as f:
            loaded_dict = json.load(f)
        for key, value in loaded_dict.items():
            if key not in valid_fields:
                print(f"Warning: '{key}' from file is not a recognized Settings attribute (skipping)")
                continue
            if key == 'knn_weights' and isinstance(value, str):
                try:
                    vals = [float(x.strip()) for x in value.split(',')]
                    value = np.array(vals)
                except (ValueError, AttributeError) as e:
                    print(f"Warning: Could not parse knn_weights string '{value}': {e}")
                    continue
            elif key == 'knn_weights':
                if not isinstance(value, np.ndarray):
                    try:
                        value = np.array(value)
                    except Exception as e:
                        print(f"Warning: Could not convert knn_weights to np.array: {e}")
            setattr(self, key, value)
        self.Validation()
        print(f"Settings loaded from: {input_path}")



    def Save(self, file_path: str):
        settings_dict = self.ToDict(compact=False)
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(_json_dumps(settings_dict, indent=2))
        print(f"Settings exported to: {output_path}")

    def Validation(self):
        # Check for unexpected attributes (typos or accidental additions).
        # v2.4.3: three legacy KNN-elastic fields were removed
        # (betweenness_elastic_origin_weight, betweenness_elastic_min_factor,
        # betweenness_elastic_low_percentile).  If a script or CSV still sets
        # these, this check raises a helpful pointer to the new
        # decay_method / gravity_cap replacements.
        expected_fields = set(self.__dataclass_fields__.keys())
        actual_attrs = set(vars(self).keys())
        unexpected = actual_attrs - expected_fields

        if unexpected:
            _legacy_elastic = {'flow_elastic_origin_weight',
                               'flow_elastic_min_factor',
                               'flow_elastic_low_percentile'}
            legacy_seen = unexpected & _legacy_elastic
            hint = ""
            if legacy_seen:
                hint = (
                    "\n  NOTE: the legacy KNN-elastic settings "
                    f"{sorted(legacy_seen)} were removed in v2.4.3.  Replace with "
                    f"flow_decay_method = \"closest\" (default, no extra "
                    f"input) or \"gravity_cap\" (set flow_gravity_cap > 0)."
                )
            raise ValueError(
                f"Unexpected attributes found (possible typos): {sorted(unexpected)}{hint}"
            )

        # Ensure knn_weights is always a 1-D np.ndarray.
        if not isinstance(self.knn_weights, np.ndarray):
            try:
                self.knn_weights = np.array(self.knn_weights)
            except Exception as e:
                print(f"Warning: Could not convert knn_weights to np.ndarray: {e}")
        if isinstance(self.knn_weights, np.ndarray) and self.knn_weights.ndim == 0:
            print(
                f"Warning: knn_weights was set to a scalar ({float(self.knn_weights)!r}); "
                f"promoting to a 1-element array.  Tip: a single-element tuple in "
                f"Python is written `(1.0,)` — note the trailing comma.  `(1.0)` is "
                f"just a float in parentheses."
            )
            self.knn_weights = self.knn_weights.reshape(1)

        errors = []

        # Literal / enum field checks — derived directly from type annotations.
        import typing
        for field_name, hint in typing.get_type_hints(Settings).items():
            if typing.get_origin(hint) is typing.Literal:
                allowed = typing.get_args(hint)
                val = getattr(self, field_name, None)
                if val not in allowed:
                    errors.append(
                        f"{field_name} must be one of {allowed}, got {val!r}"
                    )

        # Numeric range checks for the v4 k-alternatives engine.
        if self.flow_n_alternatives < 1:
            errors.append(
                f"flow_n_alternatives must be >= 1, got {self.flow_n_alternatives}"
            )
        if self.flow_alternative_penalty_factor < 1.0:
            errors.append(
                f"flow_alternative_penalty_factor must be >= 1.0, got "
                f"{self.flow_alternative_penalty_factor}"
            )

        # v2.4.3: flow_gravity_cap is REQUIRED (>0) when
        # flow_decay = True AND flow_decay_method = "gravity_cap".
        if self.flow_decay and self.flow_decay_method == 'gravity_cap':
            cap = float(self.flow_gravity_cap)
            if cap <= 0.0:
                errors.append(
                    f"flow_gravity_cap must be > 0 when "
                    f"flow_decay_method='gravity_cap'; got {cap}.  Set it to the "
                    f"gravity value (sum of destination_weight × distance_decay within "
                    f"search_radius, or destination count if "
                    f"flow_destination_weights=False) at which an origin's trip "
                    f"generation should saturate at 100%."
                )

        if self.search_radius <= 0:
            errors.append("search_radius must be > 0")

        ord_col = (self.origin_destination_id_column or '').strip()
        dst_col = (self.destination_id_column or '').strip()
        if ord_col and not dst_col:
            errors.append(
                "origin_destination_id_column is set but destination_id_column is empty — "
                "per-origin routing requires destination_id_column to be set too."
            )

        engine = str(self.flow_engine).strip().lower()
        if engine not in ('k_alternatives', 'aggregate_flow'):
            errors.append(
                f"flow_engine must be 'k_alternatives' or 'aggregate_flow', got {engine!r}"
            )

        # Route-alternatives output (v2.5.2).
        route_col = (self.flow_route_id_column or '').strip()
        if route_col:
            ord_col = (self.origin_destination_id_column or '').strip()
            dst_col = (self.destination_id_column or '').strip()
            if (ord_col and ord_col != route_col) or (dst_col and dst_col != route_col):
                errors.append(
                    f"flow_route_id_column={route_col!r} conflicts with "
                    f"origin_destination_id_column={ord_col!r} / "
                    f"destination_id_column={dst_col!r}.  Set either the "
                    f"route id column OR the two assigned-routing columns, "
                    f"not both."
                )
            else:
                # The route id column drives the existing assigned-routing
                # machinery: same column name in both files.
                self.origin_destination_id_column = route_col
                self.destination_id_column        = route_col
        # NOTE: flow_output_routes with flow_engine='aggregate_flow' is not
        # an error — AggregateFlow.Centrality logs a warning and ignores it
        # (the aggregate engine does not enumerate individual routes).

        ratio = float(self.flow_detour_ratio)
        if ratio < 1.0:
            errors.append(f"flow_detour_ratio must be >= 1.0, got {ratio}")
        buf = float(self.flow_detour_buffer)
        if buf < 0.0:
            errors.append(f"flow_detour_buffer must be >= 0, got {buf}")

        if (self.flow_decay
                and self.flow_decay_method == 'closest'
                and self.flow_decay_curve == 'logistic'):
            midpoint = float(self.gravity_logistic_midpoint)
            if midpoint <= 0.0:
                errors.append(
                    "flow_decay_curve='logistic' (with flow_decay_method='closest') "
                    "requires gravity_logistic_midpoint > 0 (distance at which the "
                    f"factor = 0.5); got {midpoint!r}."
                )

        k_new = int(self.flow_k_nearest_destinations)
        k_old = int(self.flow_max_destinations_per_origin)
        if k_new < 0 or k_old < 0:
            errors.append(
                f"flow_k_nearest_destinations / flow_max_destinations_per_origin "
                f"must be >= 0, got new={k_new}, old={k_old}"
            )

        if errors:
            raise ValueError('Settings validation failed:\n' + '\n'.join(errors))

        if self.output_folder is None:
            self.output_folder =  os.path.join(self.data_folder, "Results")



    def Reset(self):
        default = Settings()
        self.__dict__.clear()
        self.__dict__.update(default.__dict__)

    def ToDict(self, compact: bool = True) -> dict:
        """Serialise settings to a plain dict suitable for JSON export.

        Args:
            compact: If True (default), only fields that differ from their
                     default value are included.  If False, all fields are
                     included — useful for documentation / self-describing files.
        """
        import dataclasses as _dc
        _defaults = Settings()
        result = {}
        for f in _dc.fields(self):
            val = getattr(self, f.name)
            default = getattr(_defaults, f.name)
            if compact:
                if isinstance(val, np.ndarray):
                    if np.array_equal(val, default):
                        continue
                else:
                    if val == default:
                        continue
            # JSON-safe conversion
            if f.name == 'knn_weights':
                try:
                    vals = list(val) if val is not None else []
                    result[f.name] = [float(x) for x in vals]
                except (TypeError, ValueError):
                    result[f.name] = [1.0]
            elif isinstance(val, np.ndarray):
                result[f.name] = val.tolist()
            elif isinstance(val, (np.integer, np.floating)):
                result[f.name] = val.item()
            else:
                result[f.name] = val
        return result
