Cheat sheet
===========

A task-oriented reference. Each recipe lists the **minimum** settings you
need to change from defaults to accomplish one specific analysis. Copy
the block, edit the data paths, run — that's it. Every recipe links
back to the relevant reference page if you want to go deeper.

Everything below assumes the standard preamble:

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()
   una.settings.data_folder   = r"Boston"
   una.settings.output_folder = r"../Output"
   una.settings.network_file  = "network.geojson"
   una.settings.origins_file  = "origins.geojson"

Anything shown in a recipe *replaces or adds to* those defaults.

.. contents:: On this page
   :local:
   :depth: 1


Accessibility recipes
---------------------

Count destinations within walking distance (Reach)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"How many bus stops sit within 500 m of every building?"

.. code-block:: python

   una.settings.destinations_file    = "bus_stops.geojson"
   una.settings.search_radius        = 500
   una.settings.calculate_reach      = True    # count destinations

   una.RunAccessibility()

Output column: ``reach``. See :doc:`../user_guide/run_accessibility`.

Gravity with distance decay (exponential)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"Give closer destinations more weight than distant ones, using an
exponential decay."

.. code-block:: python

   una.settings.destinations_file        = "shops.geojson"
   una.settings.search_radius            = 1000
   una.settings.calculate_gravity        = True
   una.settings.gravity_decay_method     = "exponential"
   una.settings.gravity_beta             = 0.002   # β; larger = steeper decay

   una.RunAccessibility()

A useful mnemonic: with β = 0.002, half-weight distance is ln(2)/β ≈ 347 m.
See :doc:`../concepts/gravity_and_decay` for the calibration table.

Gravity with logistic decay
~~~~~~~~~~~~~~~~~~~~~~~~~~~

"Weight destinations at 100% out to some midpoint, then drop off
smoothly."

.. code-block:: python

   una.settings.calculate_gravity        = True
   una.settings.gravity_decay_method     = "logistic"
   una.settings.gravity_logistic_midpoint = 400     # 50% weight at 400 m
   una.settings.gravity_logistic_steepness_distance = 600  # 1% by 600 m

The ln(99)/midpoint convention means "at midpoint the weight is 0.5;
at steepness_distance the weight is 0.01." See
:doc:`../concepts/gravity_and_decay`.

K-nearest destinations (KNN)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"How far is it, on average, to the 5 nearest supermarkets?"

.. code-block:: python

   una.settings.destinations_file        = "supermarkets.geojson"
   una.settings.search_radius            = 5000    # generous cap
   una.settings.calculate_knn            = True
   una.settings.knn_k                    = 5
   una.settings.knn_weights              = (1.0, 1.0, 1.0, 1.0, 1.0)  # equal weights

Output columns include ``knn_mean_dist``. Change tuple weights to bias
toward the closest few. See :doc:`../user_guide/run_accessibility`.

All four metrics at once
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.settings.calculate_reach              = True
   una.settings.calculate_gravity            = True
   una.settings.calculate_gravity_logistic   = True
   una.settings.calculate_knn                = True
   una.settings.knn_k                        = 3

   una.RunAccessibility()

One Dijkstra sweep per origin computes all requested metrics — no cost
penalty for asking for four instead of one.


Flow recipes
------------

Pedestrian flow between homes and shops
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"Estimate walking flow on every street segment, given a trip
generation model."

.. code-block:: python

   una.settings.origins_file          = "homes.geojson"
   una.settings.destinations_file     = "shops.geojson"
   una.settings.search_radius         = 800
   una.settings.flow_decay_method     = "gravity_cap"
   una.settings.flow_gravity_cap      = 100    # gravity at which trip gen saturates
   una.settings.flow_k_alternatives   = 3      # 3 alternative paths per OD
   una.settings.flow_penalty          = 1.15   # penalty multiplier for K-alt

   una.RunFlow()

See :doc:`../user_guide/run_flow` and :doc:`../concepts/k_alternatives`.

Every origin sends all trips to its closest destination
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"Everyone walks to their nearest school."

.. code-block:: python

   una.settings.flow_decay_method     = "closest"
   una.settings.flow_k_alternatives   = 1      # single shortest path

Simpler and faster than the gravity-cap model. Use when the "closest
destination" assumption is reasonable (schools, transit stops).

Huff destination choice
~~~~~~~~~~~~~~~~~~~~~~~

"Split each origin's trips across destinations by attractiveness /
distance, not just to the nearest one."

.. code-block:: python

   una.settings.flow_huff                    = True
   una.settings.flow_huff_alpha              = 1.0    # attraction exponent
   una.settings.flow_huff_beta               = 0.002  # distance decay exponent
   una.settings.destinations_weight_column   = "sqft" # attractiveness column

Origins split their outbound trip volume across destinations
proportional to (destination_weight^α) × exp(−β × distance).


OD Matrix recipes
-----------------

Origin → destination distance matrix
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"Give me the shortest-path distance from every origin to every
destination."

.. code-block:: python

   una.settings.search_radius     = 2000
   una.settings.odm_output_format = "long"    # or "wide"

   una.RunODM()

For large N × M, prefer ``"long"`` format (one row per OD pair) — it
compresses better and is easier to filter in downstream tools.


Impedance recipes
-----------------

Turn on elevation
~~~~~~~~~~~~~~~~~

"My network has z-coordinates; make uphill cost more than downhill."

.. code-block:: python

   una.settings.elevation           = True
   una.settings.elevation_penalty   = 4        # meters of horizontal per meter of climb

Only affects results when your network is 3D. See
:doc:`../concepts/elevation_turns` for calibration.

Turn on turn penalties
~~~~~~~~~~~~~~~~~~~~~~

"Penalize sharp turns; prefer straighter routes."

.. code-block:: python

   una.settings.turns               = True
   una.settings.turn_threshold      = 45       # degrees; above → penalty applies
   una.settings.turn_penalty        = 32       # meters added per penalized turn

Roughly 2–4× slower than turn-agnostic runs because the engine builds
a line graph. See :doc:`../concepts/elevation_turns`.

Both at once
~~~~~~~~~~~~

.. code-block:: python

   una.settings.elevation           = True
   una.settings.elevation_penalty   = 4
   una.settings.turns               = True
   una.settings.turn_threshold      = 45
   una.settings.turn_penalty        = 32

The turn engine handles both automatically.


Points recipes
--------------

Add observers (passive counters)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"How many trips pass every bench in the park?"

.. code-block:: python

   una.settings.observer_points_file          = "benches.geojson"
   una.settings.observer_points_uid_column    = "bench_id"

   una.RunFlow()

Observers don't affect routing — they just count trips that pass
through the arc they snap to. See :doc:`../user_guide/observers_obstacles`.

Add obstacles (cost-adders)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

"Pedestrian bridges are inconvenient — add a 30 m penalty for using
them."

.. code-block:: python

   una.settings.obstacle_points_file          = "bridges.geojson"
   una.settings.obstacle_points_uid_column    = "bridge_id"
   una.settings.obstacle_points_penalty_column = "cost_m"    # per-obstacle penalty

Obstacles add to arc cost during Dijkstra — routes route around them.


Project & batch recipes
-----------------------

Save current settings as a project entry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"I've configured settings for one scenario; snapshot them."

.. code-block:: python

   una.settings.scenario_name = "baseline_reach_500"
   una.SaveSettingsToProject()

Export a project to JSON or CSV
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.ExportProjectAsJSON(folder=r"../Projects", file_name="boston_batch.json", compact=True)
   una.ExportProjectAsCSV(file_name=r"../Projects/boston_batch.csv")

Run every entry in a project
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.RunBatch(project_type="accessibility")   # or "flow", "odm"

Every snapshot is executed with the matching ``Run*`` method. See
:doc:`../user_guide/run_batch`.


Utility recipes
---------------

Print all current settings
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.PrintSettings()

Save / load one settings block to JSON
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.SaveSettings(r"../Configs/my_run.json")
   una.LoadSettings(r"../Configs/my_run.json")

Reset settings to defaults
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.settings.Reset()

Convert an old CSV project to JSON
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.ConvertProject_csv_to_json(
       csv_file  = r"../Projects/old.csv",
       json_file = r"../Projects/new.json",
       compact   = True,
   )

Convert a GeoJSON to Feather for faster loading
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.ConvertGeoJson_to_Feather(
       input_path  = r"Boston/20260703_PercLenNetwork_InnerCore.geojson",
       output_path = r"Boston/20260703_PercLenNetwork_InnerCore.feather",
   )


Output-format recipes
---------------------

Choose which output formats to write
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.settings.output_geojson  = True     # default
   una.settings.output_feather  = False
   una.settings.output_csv      = False

Turn off the timestamp subfolder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.settings.output_wStamp = False    # write straight into output_folder

Useful for scripted pipelines where a fixed output path is required.


Debugging recipes
-----------------

Quieter or louder logs
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.settings.logger_verbosity = 0     # errors only
   una.settings.logger_verbosity = 1     # default — top-level progress
   una.settings.logger_verbosity = 2     # detailed per-batch messages
   una.settings.logger_verbosity = 3     # very verbose — for debugging

Validate settings without running
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.settings.Validation()

Raises ``ValueError`` describing the first invalid combination it
finds — great for CI-style config checks.

Data-validation dry run
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   una.DataValidation()

Loads every input layer, snaps points, and reports counts without
running the analysis. Fastest way to catch a bad file path or CRS
mismatch.


When things look wrong
----------------------

**All origins report zero reach.** Increase ``search_radius``, or check
that ``destinations_file`` snaps to the same connected component as
``origins_file``.

**Elevation setting has no effect.** Confirm your network file has
3D geometry (z-coordinates on the vertices).

**Turn-aware run gives empty output.** Reduce ``turn_penalty`` — if
it exceeds ``search_radius``, single-turn routes exceed the search
budget and every origin becomes disconnected from its destinations.

**Flow output shows one big number and zeros elsewhere.** You probably
set ``flow_decay_method="closest"`` but expected gravity-cap behavior.
Double-check the setting.

**CRS mismatch on layer load.** UNA fails loudly here; the fix is
always "reproject the layer to match ``network_file``'s CRS."

**Everything works but output looks pixelated in QGIS.** Turn on
"Anti-aliasing" in QGIS view settings — this is a rendering issue,
not a UNA bug.


Related pages
-------------

- :doc:`../user_guide/settings_reference` — every setting, with prose.
- :doc:`../user_guide/workspace_walkthrough` — line-by-line explanation
  of the reference workspace script.
- :doc:`../concepts/gravity_and_decay` — the math behind the decay
  methods.
- :doc:`../concepts/k_alternatives` — the K-alternatives paths method.
- :doc:`../concepts/elevation_turns` — the elevation and turn
  penalty math.
