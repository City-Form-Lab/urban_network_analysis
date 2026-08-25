Project workflow — RunBatch()
=============================

A **project** is a list of ``Settings`` snapshots — each snapshot a
frozen configuration for one analysis. The project workflow lets you
save many configurations to a single file, then execute all of them
sequentially with one call to ``una.RunBatch()``. This is the tool of
choice when you need to run the same analysis with different parameters
(multiple radii, multiple destination categories, multiple scenarios)
without editing ``UNA_Workspace.py`` by hand.

.. contents:: On this page
   :local:
   :depth: 1


When to use the project workflow
--------------------------------

Reach for ``RunBatch()`` when:

- You want to run the same analysis on **many destination categories**
  — schools, jobs, transit, retail, healthcare, parks — with different
  search radii, weights, or decay parameters per category.
- You want to run **scenario comparisons** — same OD layers, different
  turn/elevation settings; or same settings, different network files
  representing before/after conditions.
- You want your analysis to be **reproducible** — a project file is
  a self-contained, reviewable, version-controllable record of every
  parameter used.

For a single one-off analysis, ``RunAccessibility()`` /
``RunFlow()`` / ``RunODM()`` directly are simpler.


The Project object
------------------

The UNA instance carries a ``projects`` list — initially empty. You
populate it with ``SaveSettingsToProject()``, which appends a deep copy
of the current ``una.settings`` to the list. Because it's a deep copy,
subsequent edits to ``una.settings`` do not affect the saved entries.

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   # Build a project in code
   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"

   una.settings.search_radius = 500
   una.settings.name          = "bus_500m"
   una.SaveSettingsToProject()

   una.settings.search_radius = 1500
   una.settings.name          = "bus_1500m"
   una.SaveSettingsToProject()

   una.settings.destinations_file = "schools_cambridge.geojson"
   una.settings.search_radius     = 800
   una.settings.name              = "schools_800m"
   una.SaveSettingsToProject()

The ``una.settings.name`` field is the human-readable label for each
snapshot. It becomes the ``output_file_name`` when the row runs, so
outputs are self-labeled.

You can inspect the project list at any time — it's just a list of
``Settings`` objects.


Exporting a project to file
---------------------------

Two formats are supported:

.. code-block:: python

   # JSON — human-readable, git-friendly
   una.ExportProjectAsJSON(folder=r"./Settings", file_name="boston_walkscore.json")

   # CSV — spreadsheet-editable
   una.ExportProjectAsCSV(file_name="boston_walkscore.csv")

The **compact** mode of both writers omits fields that match their
defaults, keeping the file to only the fields you actually changed.
This is the recommended default — you get a diff-friendly artifact
listing exactly what varies per row.

.. code-block:: python

   una.ExportProjectAsJSON(folder=r"./Settings", file_name="boston.json",
                           compact=True)     # default: only non-default fields
   una.ExportProjectAsJSON(folder=r"./Settings", file_name="boston_full.json",
                           compact=False)    # every field written out

.. note::

   ``ExportProjectAsCSV()`` writes to ``settings.data_folder`` so the
   project file sits next to the data files it references. Set
   ``data_folder`` before calling if you need a specific output
   location. The CSV export also drops the ``data_folder`` column
   itself, because on load it's recovered from the CSV file's
   directory.


Running a project
-----------------

Two modes: run whatever's already in ``self.projects``, or load a file
and run it in one call.

.. code-block:: python

   # Mode 1: run whatever was populated in code
   una.RunBatch("accessibility")

   # Mode 2: load from file, then run
   una.RunBatch("accessibility", pairing_file=r"./Settings/boston.json")
   una.RunBatch("flow",         pairing_file=r"Boston/Boston_Flow_pairings.csv")

The first argument tells UNA which run method to invoke per row —
``"accessibility"`` calls ``RunAccessibility()``, ``"flow"`` calls
``RunFlow()``. Rows are executed sequentially. Each row's output lands
under its own timestamped subfolder inside
``<data_folder>/Results/``, labeled with the row's ``name``.

When ``pairing_file`` is provided, ``self.projects`` is replaced by the
loaded entries — the file is authoritative.


Loading a CSV/TSV/JSON project
------------------------------

Column names in a CSV must match ``Settings`` field names exactly.
Unknown columns are silently ignored, so extra bookkeeping columns
(``flow_name``, ``notes``, ``owner``) can live alongside real settings
without breaking the loader. Empty cells and ``nan`` values are treated
as "use the default" for that field.

A minimal three-column pairing CSV:

.. code-block:: text

   name,destinations_file,search_radius
   bus_500m,MA_bus_stops.geojson,500
   schools_800m,schools_cambridge.geojson,800
   metro_1500m,MA_metro_stations.geojson,1500

When loaded via ``pairing_file="pairings.csv"``, UNA:

1. Creates a fresh ``Settings`` object per row.
2. Calls ``settings.ApplyRow(row)`` to apply each column onto its
   matching field.
3. Fills ``data_folder`` from the CSV file's directory if not present.
4. Appends the configured Settings object to ``self.projects``.
5. When you then call ``RunBatch("accessibility")``, iterates the
   list and dispatches to the appropriate run method.

.. important::

   **Row file paths resolve relative to the pairing CSV's own
   directory.** When a row leaves ``data_folder`` blank (the usual
   case), UNA sets it to the folder containing the CSV — so
   ``network_file``, ``origins_file``, and ``destinations_file`` are
   found relative to the CSV's location, including relative paths like
   ``../network/streets.geojson``. This makes a pairing table
   portable: keep the CSV in a fixed position relative to the data and
   the same file runs unchanged on any machine, regardless of where
   the drive or sync folder is mounted. A row can opt out by filling
   its own ``data_folder`` column or by using absolute file paths.

   The driver script does **not** override this: setting
   ``data_folder`` on ``una.settings`` before ``RunBatch`` has no
   effect on rows. The only script-level path RunBatch honors is
   ``output_folder``, used as a fallback for rows whose
   ``output_folder`` is blank — that is the per-machine knob for
   directing results.

The **JSON** format is functionally identical — a top-level JSON array
of settings objects. Use it when you want git-friendly diffs or
programmatic generation from another tool.


Converting old CSVs to JSON
---------------------------

If you have a CSV project and want to convert it to JSON without
running:

.. code-block:: python

   una.ConvertProject_csv_to_json(
       csv_file="pairings.csv",
       json_file="pairings.json",
       compact=True,        # only fields that differ from defaults
   )


Composite output — merging rows into joint files
------------------------------------------------

Beyond per-row outputs, ``RunBatch()`` can merge the results of many
rows into **composite** files with a sum column — the mechanism behind
WalkScore-style indices. Any row with
:py:data:`settings_reference:batch_composite_output` = TRUE has its
result column (chosen by
:py:data:`settings_reference:batch_composite_result_column`) captured
as the batch runs; after the last row, UNA joins the captured columns
onto their layer and writes the result to a
``composite_<timestamp>`` folder under ``Results/``.

**Join targets.** Per-origin metrics (``reach``, ``gravity_*``,
``knn_access``) join onto the row's *origins* layer — one value per
origin point. ``edge_flow`` joins onto the *network* layer;
``node_flow`` onto network nodes.

**Grouping by join layer.** Rows are grouped by their join layer, and
one composite file is written per group. A WalkScore-style project
where every row shares the same origins file yields a single
``composite`` file: one column per row (named
``<metric>_<row name>``) plus a final
:py:data:`settings_reference:batch_composite_sum_column_name` column
(default ``composite_sum``) summing them. A project whose rows use
*several different* origin layers — e.g. a town-wide pairing table
with "Homes to …", "Jobs to …", and "Schools to …" rows — yields one
composite per origin layer, each named after it:
``composite_home_state_portland``, ``composite_jobs_state_portland``,
and so on. Each group gets its own sum column over just its own rows.

**Output formats.** The composite honors the same ``output_geojson`` /
``output_feather`` / ``output_csv`` flags as engine exports. If all
three are FALSE on the final row, a CSV is written as a fallback so a
requested composite is never silently dropped.

The composited results also stay available in memory after the batch:
``una.composite_results`` is a list of ``(group_name, GeoDataFrame)``
pairs, and ``una.composite_result`` is the first (or only) composite —
convenient for immediate inspection or plotting.


A complete example
------------------

**In code:**

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.turns             = True
   una.settings.elevation         = True

   for radius, dest_file, dest_col, name in [
       ( 500, "MA_bus_stops.geojson",           "weekly_departures", "bus"),
       ( 800, "schools_cambridge.geojson",            "W",              "schools"),
       (1500, "MA_metro_stations.geojson",        "weekly_departures", "metro"),
       (1500, "Cambridge_transit_stations.geojson", "weekly_departures", "transit"),
   ]:
       una.settings.destinations_file        = dest_file
       una.settings.destination_weight_column = dest_col
       una.settings.search_radius             = radius
       una.settings.name                      = name
       una.SaveSettingsToProject()

   # Save + run
   una.ExportProjectAsJSON(folder=r"./Settings", file_name="boston_walkscore.json")
   una.RunBatch("accessibility")

Four accessibility flows run in sequence, each producing its own
timestamped output. When you're done you can commit
``boston_walkscore.json`` to git as a self-describing record of the
analysis.

**From a file, later:**

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()
   una.RunBatch("accessibility", pairing_file=r"./Settings/boston_walkscore.json")

Reproducing the four-flow batch from the JSON.


Cleaning up
-----------

If you want to reset the in-memory project list without exiting Python:

.. code-block:: python

   una.ClearProject()

``self.projects`` is emptied. The next
``SaveSettingsToProject()`` call starts a fresh list.


Behavior notes
--------------

**Settings leak.** ``RunBatch()`` sets ``self.settings = row_snapshot``
for each row as it executes. After the batch, ``self.settings`` holds
the *last* row's values, not what it held before ``RunBatch()`` was
called. If you interactively call another method afterward, be aware
that the settings you'll be running with are the last row's, not your
originals.

**Missing required fields.** Rows whose ``network_file``, ``origins_file``,
or ``destinations_file`` are blank are logged as "skipped" and the
batch continues with the next row. This is useful when you're
prototyping a wide CSV where not every row has the required minimum
yet.

**Topology caching.** For rows that share the same ``(network, origins,
destinations)`` triple, ``RunBatch()`` reuses the built topology
between rows rather than rebuilding it — a significant speedup for
projects that only vary decay parameters or search radius.


Next steps
----------

- :doc:`run_accessibility` — the per-row method for accessibility rows.
- :doc:`run_flow` — the per-row method for flow rows.
- :doc:`../tutorials/tutorial_2_accessibility` — a worked
  WalkScore-style batch example.
- :doc:`../api/una` — the :py:meth:`RunBatch` method signature.
