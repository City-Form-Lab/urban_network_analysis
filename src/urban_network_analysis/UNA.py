import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

from .Topology import Topology
from .Settings import Settings, _json_dumps
from .Logger import Logger
from .Engines.Flow import Flow
from .Engines.AggregateFlow import AggregateFlow
from .Engines.AccessibilityWElevation import AccessibilityWElevation
from .Engines.Accessibility import Accessibility
from .Engines.AccessibilityWTurns import AccessibilityWTurns
from .Engines.Base import resolve_output_folder, write_gdf_outputs


import os
import json
import copy
import multiprocessing as mp

class UNA:

    topology: Topology
    settings: Settings
    accessibility = None
    flow = None
    composite_result = None  # populated by RunBatch when a row requests batch_composite_output

    has_centrality_results = False
    has_flow_results = False

    # Batch composite-output metric groupings (used by RunBatch's composite
    # feature — joining several rows' results into one file + sum column).
    # Extend these sets (and the Literal in Settings.batch_composite_result_column)
    # when adding new metrics.
    _COMPOSITE_PER_ORIGIN_METRICS = {"reach", "gravity_exponential",
                                      "gravity_logistic", "knn_access"}
    _COMPOSITE_PER_EDGE_METRICS   = {"edge_flow"}
    _COMPOSITE_PER_NODE_METRICS   = {"node_flow"}

    def __init__(self, verbosity: int = 1):
        self.topology = Topology()
        self.topology.logger.verbosity = verbosity
        self.accessibility = None
        self.flow = None
        self.composite_result = None
        self.settings = Settings()
        self.settings.Reset()
        self.projects = []

        self.has_flow_results = False
        self.has_centrality_results = False

        # I am using topology logger for logging in UNA, so that all logs are centralized in one place and can be easily accessed and exported if needed.
        self.topology.logger.log('UNA', "UNA instance created. Topology and Settings initialized", v=1)

        self.projects = []  # Initialize the project settings list

        
    def PrintSettings(self) -> None:
                
        print("\nCurrent Settings:")
        for key, value in self.settings.__dict__.items():
            print(f"{key}: {value} (type: {type(value).__name__})")

        print("\nTo modify settings, you can directly set attributes on the UNA instance's settings object. For example:")
        print("una.settings.data_folder = r'.\\Data'")

    def LoadSettings(self, file_path: str = None) -> None:
        # Placeholder for loading settings from a file or other source
        # For now, we will just return the current settings instance
        if file_path:
            self.settings = Settings()
            self.settings.Load(file_path)
    
        self.topology.logger.log('UNA', f"Settings loaded from {file_path}", v=1)

    def SaveSettings(self, file_path: str = None) -> None:
        # Placeholder for saving settings to a file or other destination
        self.settings.Save(file_path)

    def DataValidation(self) -> None:

        raise NotImplementedError("DataValidation() is not implemented yet. This function will check if the data and settings are valid before running calculations. It will check for common issues such as missing files, mismatched CRS, missing weight columns, etc.")

        # optoinal function to validate data before building topology and running calculations
        # checks if crs matching between network, origins and destinations
        # checks if weight columns exist in the data
        # checks if types so that network is linestrings, origins and destinations are points, etc.
        # checkc numeric values (e.g. if radius is positive, if weights are non-negative, etc.)
        
        self.topology.AddNetwork(self.settings)
        self.topology.AddOrigins(self.settings)
        self.topology.AddDestinations(self.settings)
        self.topology.BuildTopology()

        self.topology.Evaluate()

    def RunBatch(self, analysis: str, pairing_file: str = None) -> None:
        """Run an analysis (accessibility or flow) for every entry in self.projects.

        A "project" in UNA is a saved list of Settings snapshots (this method's
        self.projects list). An "analysis" is which engine family each row
        represents — "accessibility" or "flow" — so calling `RunBatch("flow")`
        runs every project row as a Flow analysis.  See DESIGN.md § Terminology.

        If pairing_file is provided, self.projects is replaced by loading that
        file first.  Accepts .csv, .tsv, or .json (array exported by
        ExportProjectAsJSON / ExportProjectAsCSV).  When no file is given,
        whatever is already in self.projects is used — build it with
        SaveSettingsToProject() first.

        Args:
            analysis:     "accessibility" or "flow"
            pairing_file: optional path to a .csv / .tsv / .json batch file
        """
        analysis = analysis.lower().strip()
        if analysis not in ('accessibility', 'flow'):
            raise ValueError(
                f"Unknown analysis '{analysis}'. Use 'accessibility' or 'flow'."
            )

        # Script-level fallback: an output_folder configured on the UNA
        # instance BEFORE calling RunBatch (e.g. the OUTPUT_FOLDER
        # constant in UNA_Batch.py) applies to every row that does not
        # set its own.  Precedence: row value > script value >
        # <data_folder>/Results.  Note data_folder is deliberately NOT
        # given a script-level override: pairing-CSV rows resolve their
        # relative paths from the CSV's own directory — the documented
        # convention — regardless of the instance's data_folder.
        script_output_folder = (self.settings.output_folder or '').strip() or None

        if pairing_file:
            self.projects = []
            file_dir = os.path.dirname(os.path.abspath(pairing_file))
            ext = os.path.splitext(pairing_file)[1].lower()

            if ext == '.json':
                with open(pairing_file, 'r', encoding='utf-8') as f:
                    raw_rows = json.load(f)
                if not isinstance(raw_rows, list):
                    raise ValueError("JSON project file must contain a top-level array of settings objects.")
            else:
                sep = '\t' if ext == '.tsv' else ','
                raw_rows = pd.read_csv(pairing_file, sep=sep, dtype=str).to_dict('records')

            for row in raw_rows:
                s = Settings()
                s.ApplyRow(row)
                if not (s.data_folder or '').strip():
                    s.data_folder = file_dir
                self.projects.append(s)

            self.topology.logger.log('RunBatch', f"Loaded {len(self.projects)} settings from {pairing_file}", v=1)

        if not self.projects:
            raise RuntimeError("No settings to run — provide a pairing_file or call SaveSettingsToProject() first to populate the batch list.")

        self.topology.logger.log('RunBatch', f"Running {analysis} — {len(self.projects)} settings", v=1)

        self._init_batch_compositor()

        for i, s in enumerate(self.projects):
            self.settings = s

            if not (self.settings.output_folder or '').strip():
                self.settings.output_folder = script_output_folder or \
                    os.path.join(self.settings.data_folder, "Results")

            missing = [f for f in ('network_file', 'origins_file', 'destinations_file')
                       if not (getattr(self.settings, f, None) or '').strip()]
            if missing:
                self.topology.logger.log('RunBatch', f"Row {i+1}/{len(self.projects)}: skipped — missing required fields: {missing}", v=1)
                continue

            if self.settings.output_file_name == "Results":
                self.settings.output_file_name = self.settings.name

            self.topology.logger.log('RunBatch', f"Row {i+1}/{len(self.projects)}: '{self.settings.name}'", v=1)

            if analysis == 'flow':
                self.RunFlow()
                engine = self.flow 
            else:
                self.RunAccessibility()
                engine = self.accessibility 

            self._capture_batch_row(s, engine)

        self._finalize_batch_composite()

    def RunAccessibility(self) -> None:

        # Build topology based on settings

        self.has_centrality_results = False

        self.settings.Validation()

        self.topology.AddNetwork(self.settings)
        self.topology.AddOrigins(self.settings)
        self.topology.AddDestinations(self.settings)

        # Obstacles add a cost penalty to traversing specific edges or nodes.
        # They affect routing in all accessibility engines — a blocked path or
        # construction site raises the effective travel cost through that edge.
        # Observers are NOT loaded here: they are passive flow counters that
        # only make sense when full paths are enumerated (RunFlow).
        if (self.settings.obstacle_points_file or '').strip():
            self.topology.AddObstacles(self.settings)

        has_model = False

        if self.settings.turns:
            if not self.settings.elevation:
                self.topology.logger.log(
                    "UNA Accessibility",
                    f"Using AccessibilityWTurns with turn penalties (threshold={self.settings.turn_threshold}, penalty={self.settings.turn_penalty})",
                    v=1,
                )
            else:
                self.topology.logger.log(
                    "UNA Accessibility",
                    f"Using AccessibilityWTurns with turn penalties and elevation (turn threshold={self.settings.turn_threshold}, turn penalty={self.settings.turn_penalty}, elevation penalty={self.settings.elevation_penalty})",
                    v=1,
                )

            self.settings.cluster_parallel = True

            n_origins = len(self.topology.origins.geometry)

            NUM_THREADS = max(1, mp.cpu_count() - 1)
            self.settings.cluster_workers = NUM_THREADS
            self.settings.clasters = max(1, min(NUM_THREADS, n_origins))

            self.accessibility = AccessibilityWTurns(self.topology, self.settings)
        else:
            # AccessibilityWElevation is used for all non-turn cases:
            #   elevation=True  → directional weights with elevation_penalty
            #   elevation=False → elevation_penalty=0, falls back to symmetric
            #                     weights, equivalent to the base Accessibility engine
            # This gives obstacle support (partial-edge corrections) in all cases.
            if self.settings.elevation:
                self.topology.logger.log(
                    "UNA Accessibility",
                    f"Using AccessibilityWElevation (elevation penalty={self.settings.elevation_penalty})",
                    v=1,
                )
            else:
                self.topology.logger.log(
                    "UNA Accessibility",
                    "Using AccessibilityWElevation (no elevation — symmetric weights)",
                    v=1,
                )
            self.accessibility = AccessibilityWElevation(self.topology, self.settings)

        self.accessibility.Centrality(self.settings)

        self.has_centrality_results = True

        if self.settings.output_folder is None:
            self.settings.output_folder =  os.path.join(self.settings.data_folder, "Results")

        if self.has_centrality_results:
            self.accessibility.ExportAccessibilityResults(self.settings, folder_prefix="accessibility_", file_name=self.settings.output_file_name)

        # should i clean up the topology and accessibility results from memory after export? maybe not, as user might want to run flow after centrality and it would be good to have the topology already built. I can always add a function to clear results from memory if needed.

    def RunODM(
        self,
        format: str = "Sqlite",
        speed: float = 5.0,
        file_name: str = None,
    ) -> None:
        """Compute and export an origin-destination distance/duration matrix.

        Completely independent — builds its own network topology and engine.
        Does not require RunAccessibility() to have been called first.

        Requires settings.origin_uid_column and settings.destination_id_column
        to be set so the output rows are labelled with real identifiers.

        Args:
            format:        Output format — "Sqlite", "feather", "csv", or "tsv".
            speed: Walking speed in km/h used to compute duration column.
            file_name:     Output file stem; defaults to settings.output_file_name.
        """
        orig_uid = (self.settings.origin_uid_column or '').strip()
        dest_id  = (self.settings.destination_id_column or '').strip()
        if not orig_uid:
            raise ValueError("RunODM requires settings.origin_uid_column to be set.")
        if not dest_id:
            raise ValueError("RunODM requires settings.destination_id_column to be set.")

        self.settings.Validation()

        self.topology.AddNetwork(self.settings)
        self.topology.AddOrigins(self.settings)
        self.topology.AddDestinations(self.settings)

        # Obstacles add a cost penalty to traversing specific edges or
        # nodes and therefore belong in an OD cost matrix just as they
        # do in accessibility and flow runs. Same conditional load as
        # RunAccessibility / RunFlow.
        if (self.settings.obstacle_points_file or '').strip():
            self.topology.AddObstacles(self.settings)

        # OD_Matrix() needs graph_engine on the main instance — force single-threaded
        self.settings.cluster_parallel = False

        if self.settings.turns:
            self.topology.logger.log("RunODM", f"Using AccessibilityWTurns (turn threshold={self.settings.turn_threshold}, penalty={self.settings.turn_penalty})", v=1)
            engine = AccessibilityWTurns(self.topology, self.settings)
        elif self.settings.elevation:
            self.topology.logger.log("RunODM", f"Using AccessibilityWElevation (elevation penalty={self.settings.elevation_penalty})", v=1)
            engine = AccessibilityWElevation(self.topology, self.settings)
        else:
            self.topology.logger.log("RunODM", "Using Accessibility engine", v=1)
            engine = AccessibilityWElevation(self.topology, self.settings)

        if self.settings.output_folder is None:
            self.settings.output_folder = os.path.join(self.settings.data_folder, "Results")

        engine.OD_Matrix(search_radius=self.settings.search_radius)
        engine.ExportODM(
            self.settings,
            folder_prefix="ODM_",
            file_name=file_name or self.settings.output_file_name,
            format=format,
            speed=speed,
        )

    def RunFlow(self) -> None:

        self.has_flow_results = False

        self.settings.Validation()

        # clasters

        self.topology.AddNetwork(self.settings)
        self.topology.AddOrigins(self.settings)
        self.topology.AddDestinations(self.settings)

        if (self.settings.observer_points_file or '').strip():
            self.topology.AddObservers(self.settings)

        if (self.settings.obstacle_points_file or '').strip():
            self.topology.AddObstacles(self.settings)

        # Choose most optimal model for calculation based on settings and data size

        n_origins =  len(self.topology.origins.geometry)

        NUM_THREADS     = max(1, mp.cpu_count() - 1)
        n_clusters      = max(1, min(NUM_THREADS, n_origins))
        self.topology.BuildClusters(n_clusters, self.settings.search_radius)

        self.topology.logger.log("UNA Flow", f"Running Flow with {n_clusters} clusters and {NUM_THREADS} threads.", v=1)

        # Store the engine on `self` so RunBatch (and any other post-run
        # inspection — e.g., _capture_batch_row's composite-output capture)
        # can find its result attributes (edge_flow, edge_flow_AB/BA, node_flow, …).
        # This mirrors what RunAccessibility does with self.accessibility.
        #
        # Engine dispatch (settings.flow_engine):
        #   "k_alternatives" (default) — path-enumerating Flow engine.
        #   "aggregate_flow"           — marginal-flow AggregateFlow;
        #                                 scales to state-wide analyses.
        # Both engines populate the same result attributes
        # (edge_flow / edge_flow_AB / edge_flow_BA / node_flow), so
        # every downstream consumer works unchanged.
        if self.settings.flow_engine == "aggregate_flow":
            self.flow = AggregateFlow(self.topology)
        else:
            self.flow = Flow(self.topology)


        self.flow.Centrality(self.settings)

        if self.settings.output_folder is None:
            self.settings.output_folder =  os.path.join(self.settings.data_folder, "Results")

        self.flow.ExportFlowResult(
            settings      = self.settings,
            folder_prefix = "flow_",
            file_name     = self.settings.output_file_name,
        )

    ## HELPER FUNCTIONS

    def ConvertProject_csv_to_json(
        self,
        csv_file: str,
        json_file: str = None,
        compact: bool = True,
    ) -> None:
        """Convert a project pairing CSV/TSV to a JSON array of settings objects.

        Each row becomes one element in the JSON array containing only the
        settings values that were present in that row.

        Args:
            csv_file:  Path to the pairing CSV or TSV file.
            json_file: Output path.  Defaults to the same path as csv_file
                       with a .json extension.
            compact:   If True (default), each object contains only fields
                       that differ from their default value.  If False, every
                       settings field is written out — useful for documentation.

        Returns:
            The path to the written JSON file.
        """
        sep = '\t' if csv_file.lower().endswith('.tsv') else ','
        rows = pd.read_csv(csv_file, sep=sep, dtype=str).to_dict('records')
        csv_dir = os.path.dirname(os.path.abspath(csv_file))

        project_list = []
        for row in rows:
            s = Settings()
            s.ApplyRow(row)
            if not (s.data_folder or '').strip():
                s.data_folder = csv_dir
            project_list.append(s.ToDict(compact=compact))

        if json_file is None:
            base = os.path.splitext(csv_file)[0]
            json_file = base + ("_compact" if compact else "_verbose") + ".json"

        os.makedirs(os.path.dirname(os.path.abspath(json_file)), exist_ok=True)
        with open(json_file, 'w', encoding='utf-8') as f:
            f.write(_json_dumps(project_list, indent=2))

        self.topology.logger.log('ConvertProject', f"Wrote {len(project_list)} rows → {json_file}", v=1)

    def SaveSettingsToProject(self) -> None:
        self.projects.append(copy.deepcopy(self.settings))

    def ClearProject(self) -> None:
        self.projects = []  



    def ExportProjectAsJSON(
        self,
        folder: str = None,
        file_name: str = "project",
        compact: bool = True,
    ) -> None:
        """Export the project settings list to a JSON file.

        Args:
            folder:    Output folder. Defaults to the current working directory.
            file_name: File name without extension.
            compact:   If True (default), only non-default fields are written.
        """
        if not self.projects:
            raise RuntimeError("No settings in project list — call SaveSettingsToProject() first.")

        out_folder = folder or os.getcwd()
        os.makedirs(out_folder, exist_ok=True)
        fname = file_name if os.path.splitext(file_name)[1] else file_name + ".json"
        path = os.path.join(out_folder, fname)
        data = [s.ToDict(compact=compact) for s in self.projects]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(_json_dumps(data, indent=2))
        self.topology.logger.log('ExportProject', f"Wrote {len(self.projects)} rows → {path}", v=1)

    def ExportProjectAsCSV(
        self,
        file_name: str = "project",
    ) -> None:
        """Export the project settings list to a CSV file.

        NOTE: The CSV is written to settings.data_folder so the project file
        stays alongside the data files it references. Set settings.data_folder
        before calling if you need a specific output location.

        Args:
            file_name: File name without extension.
        """
        if not self.projects:
            raise RuntimeError("No settings in project list — call SaveSettingsToProject() first.")

        out_folder = self.settings.data_folder or os.getcwd()
        os.makedirs(out_folder, exist_ok=True)
        fname = file_name if os.path.splitext(file_name)[1] else file_name + ".csv"
        path = os.path.join(out_folder, fname)
        # Collect the union of columns that are non-default in at least one row,
        # then export all rows using only those columns (filling defaults where needed).
        # Result: minimal consistent column set — no unused columns, no NaN gaps.
        compact_rows = [s.ToDict(compact=True)  for s in self.projects]
        verbose_rows = [s.ToDict(compact=False) for s in self.projects]
        used_keys = {k for row in compact_rows for k in row} - {'data_folder'}
        trimmed = [{k: row[k] for k in used_keys} for row in verbose_rows]
        pd.DataFrame(trimmed).to_csv(path, index=False)
        self.topology.logger.log('ExportProject', f"Wrote {len(self.projects)} rows → {path}", v=1)





    def ConvertGeoJson_to_Feather(self, geojson_file, feather_file):
        # Optional helper function to convert GeoJSON files to Feather format for faster loading in future runs
        raise NotImplementedError("ConvertGeoJson_to_Feather() is not implemented yet. This function will read a GeoJSON file, extract the relevant attributes and geometry, and save them in Feather format for faster loading in future runs.")

    # ──────────────────────────────────────────────────────────────────
    # RunBatch internals — composite-output feature (joining several
    # RunBatch rows' results into one file + sum column). Private,
    # single-caller helpers; kept separate from RunBatch for testability
    # but grouped at the bottom of the file since nothing outside
    # RunBatch calls them.
    # ──────────────────────────────────────────────────────────────────

    def _init_batch_compositor(self) -> None:
        """Reset composite-output state when at least one pairings row asks
        for it. Nothing else in the batch loop changes when the flag is off."""
        self._composite_active        = any(s.batch_composite_output for s in self.projects)
        self._composite_captured       = []    # list of (column_name, np.ndarray, metric, join_path)
        self.composite_result          = None  # first composite gdf; public, mirrors self.flow/self.accessibility
        self.composite_results         = []    # list of (group_name, gdf) — one per join target
        if self._composite_active:
            self.topology.logger.log(
                'RunBatch',
                "Batch composite output requested — a joint file will be "
                "written after all rows complete.",
                v=1,
            )

    def _capture_batch_row(self, row_settings: Settings, engine) -> None:
        """Copy the row's configured engine attribute into the composite accumulator."""
        if not (self._composite_active and row_settings.batch_composite_output):
            return

        attr = row_settings.batch_composite_result_column
        # NOTE: this getattr IS meaningful — the engine may not carry the
        # requested attribute (e.g. user picked 'edge_flow' but ran
        # accessibility). Engine-capability check, not a Settings fallback.
        arr = getattr(engine, attr, None)
        if arr is None:
            self.topology.logger.log(
                'RunBatch',
                f"Row '{row_settings.name}': engine has no attribute '{attr}'; "
                f"row skipped from composite.",
                v=1,
            )
            return

        arr = np.asarray(arr).flatten()
        prefix   = row_settings.batch_composite_column_prefix
        col_name = f"{prefix}{attr}_{row_settings.name}" if prefix else f"{attr}_{row_settings.name}"

        # Resolve this row's join target. Rows with different join targets
        # (e.g. different origins files) form separate composite groups —
        # one composite output is written per group.
        if attr in self._COMPOSITE_PER_ORIGIN_METRICS:
            join_path = os.path.normpath(os.path.join(row_settings.data_folder, row_settings.origins_file))
        elif attr in self._COMPOSITE_PER_EDGE_METRICS:
            join_path = os.path.normpath(os.path.join(row_settings.data_folder, row_settings.network_file))
        else:  # per-node metric — joined onto topology nodes
            join_path = None
        self._composite_captured.append((col_name, arr.copy(), attr, join_path))

        self.topology.logger.log(
            'RunBatch',
            f"Row '{row_settings.name}': captured '{col_name}' "
            f"({len(arr)} values, min={arr.min():.3f}, max={arr.max():.3f}).",
            v=2,
        )

    def _finalize_batch_composite(self) -> None:
        """After every row finishes, build and write the composite (if any row asked for one)."""
        if self._composite_active and self._composite_captured:
            self._compute_batch_composite()
            self._export_batch_composite()

    def _compute_batch_composite(self) -> None:
        """Group captured columns by join target; build one composite per group.

        Rows sharing the same origins file (per-origin metrics) or the
        same network file (per-edge metrics) join into one composite.
        A pairing table with several distinct origin layers therefore
        yields several composite outputs — one per origin layer — each
        with its own sum column.
        """
        if not self._composite_captured:
            raise ValueError(
                "No composite rows captured. Set settings.batch_composite_output "
                "= True on at least one pairings row before calling RunBatch."
            )

        # Group captured columns by join target, preserving capture order.
        groups: dict[str | None, list[tuple[str, np.ndarray]]] = {}
        for col, arr, metric, join_path in self._composite_captured:
            groups.setdefault(join_path, []).append((col, arr))

        self.composite_results = []
        sum_col = self.settings.batch_composite_sum_column_name

        for join_path, cols in groups.items():
            if join_path is None:   # per-node metrics — joined onto topology nodes
                gdf = self._build_composite_nodes_gdf()
                group_name = "nodes"
                src_desc = "topology.network node_points"
            else:
                gdf = gpd.read_file(join_path)
                group_name = os.path.splitext(os.path.basename(join_path))[0]
                src_desc = join_path

            for col, arr in cols:
                if len(arr) != len(gdf):
                    raise ValueError(
                        f"Composite length mismatch: column '{col}' has "
                        f"{len(arr)} values but the join layer '{src_desc}' has "
                        f"{len(gdf)} features."
                    )
                gdf[col] = arr

            gdf[sum_col] = gdf[[c for c, _ in cols]].sum(axis=1)
            self.composite_results.append((group_name, gdf))

            self.topology.logger.log(
                'RunBatch',
                f"Composite '{group_name}' built: {len(cols)} category "
                f"columns + '{sum_col}' ({len(gdf):,} features).",
                v=1,
            )

        # Public convenience attribute — the first (or only) composite.
        self.composite_result = self.composite_results[0][1]

    def _build_composite_nodes_gdf(self) -> gpd.GeoDataFrame:
        """Build a per-node GeoDataFrame from the live Topology object."""
        net   = self.topology.network
        n_net = net.node_points.shape[0]
        xy    = net.node_points[:n_net, :2]
        geom  = [Point(float(x), float(y)) for x, y in xy]
        return gpd.GeoDataFrame(
            {"node_id": np.arange(n_net, dtype=np.int64)},
            geometry=geom, crs=net.geometry.crs,
        )

    def _export_batch_composite(
        self, folder_prefix: str = "composite_", file_name: str = "composite",
    ) -> None:
        """Write the composite output honoring output_* flags (mirrors Engines/Base).

        Each of output_geojson / output_feather / output_csv controls one
        file. When ALL three are False (composite output was still
        requested — we wouldn't be here otherwise), a CSV is written as a
        sensible fallback so the user's intent is never silently dropped.
        """
        if not self.composite_results:
            raise RuntimeError("Call _compute_batch_composite() before _export_batch_composite().")

        settings = self.settings
        output_folder = resolve_output_folder(settings, folder_prefix)
        single = len(self.composite_results) == 1

        for group_name, gdf in self.composite_results:
            # One join target → plain "composite"; several → suffix each
            # file with its join layer's name.
            out_name = file_name if single else f"{file_name}_{group_name}"

            wrote_any = write_gdf_outputs(
                gdf, settings, output_folder, out_name,
                self.topology.logger, "RunBatch", desc="Composite", csv_drop_geometry=False,
            )

            if not wrote_any:
                path = os.path.join(output_folder, f"{out_name}.csv")
                csv_gdf = (gdf.drop(columns="geometry")
                           if "geometry" in gdf.columns else gdf)
                csv_gdf.to_csv(path, index=False, sep=settings.csv_delimiter)
                self.topology.logger.log(
                    'RunBatch',
                    f"Composite CSV → {path} (fallback: no output_* flag was set)",
                    v=1,
                )

        self.topology.logger.log(
            'RunBatch',
            f"All composite outputs ({len(self.composite_results)} "
            f"composite{'s' if not single else ''}) written to {output_folder}",
            v=1,
        )