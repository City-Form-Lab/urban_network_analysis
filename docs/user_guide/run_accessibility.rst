RunAccessibility()
==================

``una.RunAccessibility()`` computes per-origin accessibility scores against
a set of destinations. It is the primary entry point for every Reach,
Gravity, and KNN analysis and produces one row per origin point with one
column per enabled metric.

.. contents:: On this page
   :local:
   :depth: 1


What it does
------------

At each origin point, ``RunAccessibility()``:

1. Snaps the origin onto its nearest network edge.
2. Runs a Dijkstra sweep outward from the origin up to
   :py:data:`settings_reference:search_radius`.
3. Collects every reachable destination within that radius.
4. Computes any of the four requested metrics — Reach, Gravity
   exponential, Gravity logistic, KNN — from the collected destination
   set.
5. Stores the results as arrays on the engine instance and exports them
   to ``settings.output_folder``.

The origin remains "at" its snapped-edge location for the purposes of
distance measurement — UNA correctly accounts for the fraction of the
host edge between the origin and its two endpoints.


Engine selection
----------------

UNA automatically dispatches to one of two accessibility engines based on
your settings flags:

+----------------------+-----------------------------+
| Settings             | Engine used                 |
+======================+=============================+
| ``turns = True``     | ``AccessibilityWTurns``     |
| (elevation optional) | (handles both turns and     |
|                      | elevation via line graph)   |
+----------------------+-----------------------------+
| ``turns = False``    | ``AccessibilityWElevation`` |
| (elevation optional) | (directional weights;       |
|                      | falls back to symmetric     |
|                      | when elevation = False)     |
+----------------------+-----------------------------+

You never have to instantiate either engine yourself.

.. note::

   The turn-aware engine is 2–4× slower than the turn-free one on dense
   urban networks. For iteration, prototype with ``turns = False`` and
   flip it on for the final production run.


Minimum required settings
-------------------------

The three data layer settings and a search radius:

.. code-block:: python

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"
   una.settings.search_radius     = 500

   una.RunAccessibility()

Every other setting has a sensible default. The four ``calculate_*``
flags default to ``True``, so you'll get all four metrics in the output
without touching them.


Which metrics to enable
-----------------------

Enabling more metrics adds only marginal cost — the shortest-path
computation is shared. Turn a metric off only when its output would be
noise for your study:

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Metric
     - Flag
     - When to use
   * - Reach
     - :py:data:`settings_reference:calculate_reach`
     - Cumulative-opportunities studies; simple communication
       ("how many bus stops within 500 m").
   * - Gravity exponential
     - :py:data:`settings_reference:calculate_exponential_gravity`
     - Smooth distance decay; classical gravity models; when you
       have an empirically calibrated β.
   * - Gravity logistic
     - :py:data:`settings_reference:calculate_logistic_gravity`
     - S-shape decay with a threshold; often a better fit for
       walking behavior; when you know a plausible midpoint.
   * - KNN access
     - :py:data:`settings_reference:calculate_knn_access`
     - "Only the *k* nearest matter" studies; WalkScore-style
       composite indices.


Optional inputs — obstacles
---------------------------

``RunAccessibility()`` respects the obstacle-points layer if configured
via :py:data:`settings_reference:obstacle_points_file`. Obstacles add
penalties to specific edges (or nodes) before Dijkstra runs, so they
appear in every accessibility metric equally. Observer points are
**not** loaded — they only make sense when full paths are enumerated
(see :doc:`run_flow`).

See :doc:`observers_obstacles` for the full obstacle model.


What gets exported
------------------

For each enabled metric, one column is written into a GeoDataFrame keyed
by origin point. The output folder resolves to
``<data_folder>/Results/accessibility_<timestamp>/`` unless you override
it with :py:data:`settings_reference:output_folder` and
:py:data:`settings_reference:output_wStamp`.

Output columns:

+----------------------------+----------------------------+
| Column                     | When present               |
+============================+============================+
| ``reach``                  | ``calculate_reach = True`` |
+----------------------------+----------------------------+
| ``gravity_exponential``    | ``calculate_exponential``  |
|                            | ``_gravity = True``        |
+----------------------------+----------------------------+
| ``gravity_logistic``       | ``calculate_logistic``     |
|                            | ``_gravity = True``        |
+----------------------------+----------------------------+
| ``knn`` or ``knn_<decay>`` | ``calculate_knn_access =`` |
| (e.g. ``knn_logistic``)    | ``True``. Suffix reflects  |
|                            | ``knn_decay``.             |
+----------------------------+----------------------------+

Column names are prefixed by
:py:data:`settings_reference:result_prefix` if set — useful when
merging multiple analyses into the same layer.

Files are written in the formats enabled by
:py:data:`settings_reference:output_geojson`,
:py:data:`settings_reference:output_feather`, and
:py:data:`settings_reference:output_csv`.


Where results also live
-----------------------

After ``RunAccessibility()`` returns, the results are also available on
the ``una.accessibility`` instance:

.. code-block:: python

   una.RunAccessibility()
   print(una.accessibility.reach.mean())
   print(una.accessibility.gravity_logistic.max())

This is handy when you want to feed the numbers into a downstream Python
step (a matplotlib chart, a pandas groupby) without reading the exported
file back in.


Example — walkability to bus stops in Cambridge
-----------------------------------------------

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"

   una.settings.search_radius              = 500
   una.settings.destination_weight_column  = "weekly_departures"

   una.settings.calculate_reach               = True
   una.settings.calculate_logistic_gravity    = True
   una.settings.calculate_knn_access          = True
   una.settings.knn_weights                   = (1.0, 1.0, 0.5)
   una.settings.gravity_logistic_midpoint     = 400

   una.settings.elevation         = True
   una.settings.elevation_penalty = 4

   una.RunAccessibility()

Produces four columns (``reach``, ``gravity_exponential``,
``gravity_logistic``, ``knn_logistic``) at every Cambridge building centroid,
weighted by daily bus departures, with elevation penalized on uphill
segments.


Common questions
----------------

**"Why do some origins have all zeros?"**
Those origins have no destinations within ``search_radius`` — either
they're in a peripheral area of the study, or their host edge is on a
disconnected fragment of the network. Check
:doc:`../getting_started/data_conventions` for network-cleaning tips.

**"Can I run RunAccessibility() twice with different settings?"**
Yes — each call rebuilds the topology from scratch. There's no cached
state that would leak between calls. Just be aware that ``output_wStamp
= True`` creates a new timestamped subfolder each time, so nothing gets
overwritten.

**"Do I have to run RunAccessibility() before RunFlow()?"**
No — the two methods are fully independent. Each rebuilds its own
topology and runs its own engine.

**"How do I run this against many destination categories?"**
Use :doc:`run_batch`. It's designed for exactly this — one CSV row per
destination category.


Next steps
----------

- :doc:`run_flow` — the flow analogue.
- :doc:`run_batch` — automate multiple runs from a CSV.
- :doc:`../tutorials/tutorial_2_accessibility` — nine-step walkthrough
  with worked numbers.
- :doc:`../concepts/gravity_and_decay` — the math behind each metric.
