RunFlow()
=========

``una.RunFlow()`` computes per-edge pedestrian or bicycle flows across
the network, given origin and destination point layers. It is UNA's
customized betweenness-centrality analysis, tuned specifically for active
mobility modeling.

.. contents:: On this page
   :local:
   :depth: 1


What it does
------------

Conceptually, ``RunFlow()``:

1. Loads network, origins, destinations, and (optionally) observer and
   obstacle layers.
2. For each origin, identifies the set of destinations within
   :py:data:`settings_reference:search_radius`.
3. Distributes each origin's trip generation across those destinations
   using a Huff-style gravity model (when
   :py:data:`settings_reference:flow_destination_weights` = True).
4. For each origin-destination pair, enumerates *K* alternative paths
   using Plateau's penalty method (up to
   :py:data:`settings_reference:flow_n_alternatives`).
5. Splits the trip between those alternatives (weighted by
   :py:data:`settings_reference:flow_path_detour_penalty` and the
   detour envelope).
6. Accumulates every trip's contribution onto each edge it traverses,
   producing a per-edge flow value.
7. Exports the per-edge flow (and optionally per-node flow, observer
   counters, and obstacle usage) as a GeoDataFrame.

The engine handles turn-aware routing, elevation penalties, obstacle
costs, per-origin destination assignment, and directional (AB/BA) flow
tracking — all controllable through settings.


Choosing an engine
------------------

``RunFlow()`` dispatches to one of two flow engines based on
:py:data:`settings_reference:flow_engine`:

.. list-table::
   :header-rows: 1
   :widths: 25 40 35

   * - Engine
     - How it routes
     - When to use
   * - ``"aggregate_flow"`` (default since 2.5.5)
     - Distributes each trip across the OD pair's full
       gradient-overlap envelope in one pass — no path enumeration.
     - Large and state-wide runs; whenever flow should spread over the
       complete envelope of viable streets. Turns and assigned routing
       not yet supported.
   * - ``"k_alternatives"``
     - Enumerates up to *K* discrete alternative paths per OD pair
       (Plateau's penalty method) and splits the trip among them.
     - Path-level fidelity; turn-aware routing; assigned routing;
       route-alternatives export.

Both engines populate the same result arrays and produce the same
output files, so switching engines is a one-line change. Within the
``k_alternatives`` engine, turn-aware routing switches on internally
when :py:data:`settings_reference:turns` = True; there is no separate
class name to worry about. See :doc:`../concepts/k_alternatives` and
:doc:`../concepts/aggregate_flow` for the math behind each.


The same origin–destination pair (MIT SAP → Harvard Housing Center,
origin weight 100) through both engines, with identical settings:

.. list-table::
   :widths: 50 50

   * - .. image:: /_static/main/K_alternative_flow_overlap1.01_DR1.2.jpg
          :alt: K-alternative flow
          :width: 100%
     - .. image:: /_static/main/Aggregate_flow_DR1.2.jpg
          :alt: Aggregate flow
          :width: 100%
   * - **K-alternative flow**, detour ratio 1.2 — trips split across
       discrete alternative paths.
     - **Aggregate flow**, detour ratio 1.2 — trips spread across the
       full detour envelope.

Minimum required settings
-------------------------

The three data layers plus a search radius:

.. code-block:: python

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids_pop2020.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"
   una.settings.search_radius     = 500

   una.RunFlow()

Every other flow parameter has a sensible default: exponential decay
turned on, detour ratio 1.05, ten alternatives per OD pair, origin and
destination weights read from the ``"Count"`` sentinel column.


Key parameters to think about
-----------------------------

For a realistic pedestrian-flow model, the parameters that most affect
the shape of the output:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Setting
     - Effect
   * - :py:data:`settings_reference:flow_detour_ratio`
     - How much longer than shortest an "acceptable" alternative can be.
       ``1.15`` allows 15 % detours — a reasonable pedestrian default.
   * - :py:data:`settings_reference:flow_detour_mode`
     - ``"ratio"``, ``"buffer"``, or ``"min"``. ``"min"`` is
       recommended — combines proportional ratio with an absolute
       buffer so short trips aren't unfairly clipped.
   * - :py:data:`settings_reference:flow_n_alternatives`
     - Cap on paths per OD pair. ``10`` is lean, ``30–50`` gives richer
       flow patterns.
   * - :py:data:`settings_reference:flow_decay_method`
     - ``"closest"`` (default) makes trip generation depend on distance
       to the nearest destination. ``"gravity_cap"`` makes it depend on
       total gravity — requires calibration.
   * - :py:data:`settings_reference:turns`
     - Turn-aware routing. 2–4× slower but usually essential for a
       defensible model.
   * - :py:data:`settings_reference:elevation`
     - Uphill penalty. Requires 3D LineStrings in the network file.

See :doc:`../concepts/k_alternatives` for the math behind path
enumeration, and :doc:`../concepts/gravity_and_decay` for the trip
generation math.


Optional inputs
---------------

**Observer points.** Passive counters that report the flow passing
through them, without influencing routing. Load via
:py:data:`settings_reference:observer_points_file`. See
:doc:`observers_obstacles`.

**Obstacle points.** Cost-adding penalties on specific edges or nodes.
Load via :py:data:`settings_reference:obstacle_points_file`. Affects
routing decisions. See :doc:`observers_obstacles`.

**Per-origin destination assignment.** When both
:py:data:`settings_reference:origin_destination_id_column` and
:py:data:`settings_reference:destination_id_column` are set, each origin
routes only to destinations whose ID matches — useful for scenarios
like "route each school only to bus stops in the same district."


What gets exported
------------------

Output lands in ``<data_folder>/Results/flow_<timestamp>/`` (unless
overridden). The primary files:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - File
     - Contents
   * - ``<file_name>.feather`` / ``.geojson``
     - Per-edge flow — one row per network edge, columns for total
       flow and (when
       :py:data:`settings_reference:flow_return_directional` = True)
       AB/BA components.
   * - ``<file_name>_network_nodes.*``
     - Per-node flow, written when
       :py:data:`settings_reference:flow_compute_node_flow` = True
       (default).
   * - ``<file_name>_observer_points.*``
     - Observer point flow counters — one row per observer with
       ``flow_AB``, ``flow_BA``, ``flow_total``. Written when
       observer points are configured.
   * - ``<file_name>_obstacle_points_usage.*``
     - Obstacle hit counters — one row per obstacle with ``hits_AB``,
       ``hits_BA``, ``hits_total``. Written when
       :py:data:`settings_reference:flow_track_obstacle_points_usage`
       = True.
   * - ``<file_name>_destinations_used_origins.*``
     - Per-destination list of contributing origins. Written when
       :py:data:`settings_reference:flow_track_origins_per_destination`
       = True.
   * - ``<file_name>_routes.*``
     - Complete generated route geometries — one row per alternative
       path. Written when
       :py:data:`settings_reference:flow_output_routes` = True
       (``k_alternatives`` engine only). See below.


Exporting route alternatives
----------------------------

For route-choice studies you often need the *routes themselves* — not
betweenness estimates — so modeled alternatives can be compared against
externally observed routes from GPS traces or surveys. Two settings
turn this on:

.. code-block:: python

   una.settings.flow_route_id_column = "route_id"   # column in BOTH origin and destination files
   una.settings.flow_output_routes   = True

   una.settings.flow_path_detour_penalty = "exponential"
   una.settings.flow_route_enumeration_beta = 0.05

   una.RunFlow()

Prepare each observed route's origin and destination point externally,
give both the same ``route_id`` value (text or numeric), and UNA
generates the K alternatives per pair — exported as
``<file_name>_routes.geojson`` / ``.feather`` / ``.csv`` with one row
per alternative:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Column
     - Contents
   * - ``route_id``
     - The value shared by the origin and destination — joins the
       generated alternatives back to the observed route.
   * - ``origin_uid`` / ``dest_uid``
     - Endpoints of the OD pair.
   * - ``alt_rank``
     - 1 = shortest path, 2..K = successive alternatives.
   * - ``route_cost``
     - Actual (un-penalized) path cost, connector partials included.
   * - ``n_edges`` / ``edge_ids``
     - The network edge ids composing the route —
       space-separated, handy for map-matching against GPS traces.
   * - ``geometry``
     - Merged LineString of the route's edges.

Notes: the number of alternatives per pair is controlled by
:py:data:`settings_reference:flow_n_alternatives` and the detour
envelope; route geometries include the full first/last snap edges (they
are not trimmed at the exact origin/destination snap points); and the
normal flow/betweenness outputs are still written alongside. Requires
``flow_engine = "k_alternatives"``.


Where results also live
-----------------------

Numeric outputs are also accessible on the ``una.flow`` instance:

.. code-block:: python

   una.RunFlow()
   edge_flow_total = una.flow.edge_flow
   observer_totals = una.flow.observer_flow_total

Handy for plotting or further pandas manipulation without re-reading
the file.


Runtime and calibration tips
----------------------------

**Prototype cheap, then commit.** For iteration, keep ``turns = False``,
``elevation = False``, ``flow_detour_ratio = 1.05``, and
``flow_n_alternatives = 10``. A 10,000-origin run finishes in seconds.
Turn on the expensive knobs for the final production run.

**Scale expectations.** For pedestrian analyses on a city-sized network:

- 100 origins × 500 destinations × K=10 → seconds.
- 10,000 origins × 500 destinations × K=10 → 1–3 minutes.
- 20,000 origins × 500 destinations × K=50 + turns + elevation →
  10–30 minutes.

**Calibrate against counts if you can.** UNA's flow output is
descriptive on its own but becomes *predictive* when you fit its
edge-by-edge output to real pedestrian counts (from cameras, intercept
surveys, automated counters). Once fitted, the model answers "what if"
scenarios — the point of doing the analysis at all.


Example — homes to bus stops in Cambridge
-----------------------------------------

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids_pop2020.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"

   una.settings.search_radius             = 500
   una.settings.origin_weight_column      = "pop2020"
   una.settings.destination_weight_column = "weekly_departures"

   una.settings.flow_decay                = True
   una.settings.flow_decay_curve          = "exponential"
   una.settings.gravity_beta              = 0.001

   una.settings.flow_detour_ratio         = 1.15
   una.settings.flow_detour_mode          = "min"
   una.settings.flow_n_alternatives       = 30

   una.RunFlow()

Produces per-edge foot-traffic estimates for every residential-to-transit
trip in Cambridge, with the Huff destination-choice model splitting each
building's trips across nearby stops in proportion to daily departures.


Next steps
----------

- :doc:`run_accessibility` — per-origin scores instead of per-edge
  flow.
- :doc:`run_batch` — automate multiple flow scenarios from a CSV.
- :doc:`observers_obstacles` — passive counters and cost-penalty
  points.
- :doc:`../tutorials/tutorial_3_flow` — ten-step walkthrough with
  worked examples and figures.
- :doc:`../concepts/k_alternatives` — Plateau's penalty method
  explained.
- :doc:`../concepts/aggregate_flow` — the scalable gradient-overlap
  engine explained.
