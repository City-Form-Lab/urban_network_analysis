Settings reference
==================

Every user-configurable UNA parameter is a field on a single ``Settings``
dataclass (``urban_network_analysis/Settings.py``). Setting a value on the ``una.settings``
instance is enough — the value flows into whichever engine
``una.RunAccessibility()`` or ``una.RunFlow()`` invokes.

This page lists every setting grouped by role. Field defaults reflect
UNA 2.5.5 and are validated at runtime by ``settings.Validation()`` — invalid
enum values, negative radii, and missing required inputs raise a ``ValueError``
before the engine runs.

.. contents:: On this page
   :local:
   :depth: 1


Input files
-----------

The four fields in this group tell UNA where to find your data. All paths are
resolved relative to ``data_folder``, which becomes the working directory for
every subsequent layer.

.. py:data:: data_folder

   :Type: ``str``
   :Default: ``""``

   Folder containing your network, origin, and destination files. All
   layer paths are resolved relative to this folder. On Windows use a raw
   string (``r"C:\path\to\Data"``) so backslashes are not interpreted as
   escapes. When UNA is driven from ``RunBatch`` and reads a project CSV, this
   field is auto-populated from the CSV's directory.

.. py:data:: network_file

   :Type: ``str``
   :Default: ``"Network.feather"``

   Filename of the street network layer. Any LineString GIS format works
   (``.geojson``, ``.feather``, ``.parquet``, ``.shp``, ``.gpkg``). Every
   ``LineString`` is treated as one edge; the endpoints of edges that share
   a coordinate become network nodes. If two edges only visually cross
   without sharing an endpoint, they are treated as an overpass/underpass —
   no routing connection.

.. py:data:: origins_file

   :Type: ``str``
   :Default: ``"origins.feather"``

   Point layer of origin locations (buildings, transit stops, analysis grid
   cells, whatever generates trips). UNA snaps each point onto its nearest
   network edge at runtime; you do not have to pre-snap them.

.. py:data:: destinations_file

   :Type: ``str``
   :Default: ``"destinations.feather"``

   Point layer of destinations (jobs, shops, schools, transit stops).
   Snapped identically to origins.

.. py:data:: network_weight_column

   :Type: ``str``
   :Default: ``"Geometric"``

   Column in the network layer that carries the per-edge cost that
   Dijkstra minimizes. The sentinel ``"Geometric"`` (the default) tells
   UNA to use the segment's ground length. Point at any numeric column
   name for a *perceived length* analysis — e.g. a column that multiplies
   geometric length by a pedestrian-quality factor derived from trees,
   sidewalk width, traffic noise.

.. py:data:: network_weight_default

   :Type: ``float``
   :Default: ``1``

   Fallback value used when a row in the weight column is missing or NaN.

.. py:data:: network_precision

   :Type: ``int``
   :Default: ``3``

   Number of decimal places to round edge weights to when building the
   internal graph. Higher precision is more faithful to the source data;
   lower precision keeps the graph slightly smaller. Default of 3 is fine
   for most planning applications.

.. py:data:: network_saved_nodes

   :Type: ``bool``
   :Default: ``False``

   When True, node IDs from the source network (``_node_start_id`` /
   ``_node_end_id`` columns) are preserved through processing so output
   files reference the original node identifiers. Leave False if you
   don't have those columns.

.. py:data:: network_load_nodes

   :Type: ``bool``
   :Default: ``True``

   When True and the input network has ``_node_start_id`` /
   ``_node_end_id`` columns, UNA reuses them instead of rebuilding node
   IDs from geometry. Speeds up loading for pre-processed feather files.


Selecting accessibility metrics
-------------------------------

The four ``calculate_*`` flags let you turn each accessibility metric on
or off. Enabling more metrics adds only marginal cost — the shortest-path
computation is shared — so leave them all on unless you specifically want
a lean output.

.. py:data:: calculate_reach

   :Type: ``bool``
   :Default: ``True``

   Computes **Reach**: the count of destinations within ``search_radius``,
   weighted by ``destination_weight_column`` if provided. Also known as a
   "cumulative opportunities" accessibility index. Simple to interpret —
   "how many bus stops within 500 m."

.. py:data:: calculate_exponential_gravity

   :Type: ``bool``
   :Default: ``True``

   Computes **Gravity (exponential decay)**: each reachable destination
   contributes ``weight × exp(-gravity_beta × distance)``. Closer
   destinations dominate; distant ones fade smoothly. The rate of decay
   is controlled by :py:data:`gravity_beta`.

.. py:data:: calculate_logistic_gravity

   :Type: ``bool``
   :Default: ``True``

   Computes **Gravity (logistic decay)**: an S-shape decay that stays
   near 1.0 for distances below the midpoint and falls off rapidly past
   it. Often a better fit for pedestrian behavior — "close is close, far
   is far, with a threshold in between." Controlled by
   :py:data:`gravity_logistic_midpoint`.

.. py:data:: calculate_knn_access

   :Type: ``bool``
   :Default: ``True``

   Computes **KNN access**: considers only the *k* nearest destinations
   per origin (with *k* set implicitly by the length of
   :py:data:`knn_weights`), then combines them into a composite index.
   Useful for WalkScore-style analyses where "the nearest three grocery
   stores" matters more than "every grocery store within 2 km".


Origins, destinations, and weights
----------------------------------

.. py:data:: origin_weight_column

   :Type: ``str``
   :Default: ``"Count"``

   Column in ``origins_file`` that carries a numeric weight per origin
   point — typically residents in a building, floor area, or number of
   employees. The sentinel ``"Count"`` gives every origin unit weight.
   For flow analyses (:py:data:`flow_origin_weights` = True), the weight
   directly scales trips generated at each origin.

.. py:data:: destination_weight_column

   :Type: ``str``
   :Default: ``"Count"``

   Column in ``destinations_file`` that carries a numeric attractiveness
   weight per destination — jobs, floor area, seating capacity, daily
   transit departures. A destination with weight 25 attracts 5× as much
   gravity-model flow as a destination with weight 5 at the same distance.

.. py:data:: origin_uid_column

   :Type: ``str``
   :Default: ``None``

   Column in ``origins_file`` used as a unique identifier for each origin
   point. Required for ``RunODM()`` (which reports pairwise distances by
   origin/destination ID). Optional otherwise, but recommended so output
   files can be joined back to your source data.

.. py:data:: origin_destination_id_column

   :Type: ``str``
   :Default: ``""``

   Column in ``origins_file`` holding a destination-group ID for
   per-origin routing assignment. When both this and
   :py:data:`destination_id_column` are set, each origin routes only
   to destinations whose ``destination_id_column`` matches — e.g. route
   each school only to bus stops in the same district. Leave both empty
   for the default "every origin routes to all reachable destinations"
   behavior.

.. py:data:: destination_id_column

   :Type: ``str``
   :Default: ``""``

   Column in ``destinations_file`` holding the group ID matched against
   :py:data:`origin_destination_id_column`. Also used as the destination
   UID in output files, so pick a column with meaningful, ideally unique
   values.

.. py:data:: default_cost

   :Type: ``float``
   :Default: ``1``

   Default weight assigned to a point if its weight column is missing or
   NaN. Used both for origins and destinations.


Search radius and nearest-destination filtering
-----------------------------------------------

.. py:data:: search_radius

   :Type: ``int``
   :Default: ``1500``

   Maximum network distance (in the same units as edge weights — meters
   by default) that any analysis considers. Destinations beyond
   ``search_radius`` from an origin are ignored. For pedestrian
   accessibility, 400–1500 m is typical; for bicycle analyses, 3000–5000 m
   is more appropriate.

.. py:data:: use_nearest_destination

   :Type: ``bool``
   :Default: ``False``

   When True, each origin routes only to its single nearest reachable
   destination. Useful for "closest facility" analyses (nearest hospital,
   nearest pharmacy). When False (default), origins consider all
   destinations within ``search_radius``.


Gravity decay parameters
------------------------

The four fields below shape the distance-decay curve that Gravity and
Flow analyses apply.

.. py:data:: gravity_beta

   :Type: ``float``
   :Default: ``0.001``

   Distance decay rate for the **exponential** gravity curve
   ``exp(-β × distance)``. A larger β makes decay sharper. β = 0.001
   gives a half-distance of ``ln(2)/β ≈ 693 m`` — sensible for walking.
   For very short-distance analyses (indoor navigation, campus scale),
   try β = 0.005. For biking, β = 0.0003–0.0005.

.. py:data:: gravity_logistic_midpoint

   :Type: ``int``
   :Default: ``500``

   Midpoint (in weight units) for the **logistic** gravity curve — the
   distance at which the decay factor equals 0.5. UNA uses the standard
   textbook convention ``k = ln(99) / midpoint``, so the 1 %/99 %
   endpoints sit symmetrically at ±midpoint from the inflection.

.. py:data:: gravity_plateau

   :Type: ``int``
   :Default: ``0``

   A "flat" no-decay zone (in weight units) around each origin, inside
   which no distance decay is applied. Useful when you want to treat all
   very-nearby destinations as equally attractive. Default 0 means decay
   starts immediately at zero distance.

.. py:data:: gravity_decay_constant

   :Type: ``float``
   :Default: ``ln(99) ≈ 4.595``

   Internal constant that anchors the logistic ln(99)/midpoint
   convention. Do not modify unless you know exactly what you are doing —
   changing it silently rescales every logistic result.


KNN accessibility parameters
----------------------------

.. py:data:: knn_weights

   :Type: ``tuple`` of floats
   :Default: ``(1.0, 1.0, 0.5)``

   Per-neighbor weights for the KNN accessibility index. The *length* of
   the tuple sets *k* (how many nearest destinations to consider); the
   *values* set each neighbor's contribution weight.
   ``(1.0,)`` gives WalkScore-style "only nearest counts";
   ``(1.0, 1.0, 0.5)`` counts three nearest with the third at half
   weight. **Note the trailing comma** in single-element tuples —
   ``(1,)`` is a tuple but ``(1)`` is just an integer in parentheses.

.. py:data:: knn_decay

   :Type: ``one of "none" | "exponential" | "logistic"``
   :Default: ``"logistic"``

   Distance-decay shape applied inside the KNN calculation.
   ``"logistic"`` is the recommended default — it tolerates a range of
   walk distances without penalizing them harshly. ``"none"`` disables
   decay (each neighbor contributes its raw weight regardless of
   distance).


Impedance factors — elevation and turns
---------------------------------------

Both elevation and turn penalties add cost onto specific edges before
Dijkstra runs. They stack additively with any obstacle penalties.

.. py:data:: elevation

   :Type: ``bool``
   :Default: ``False``

   Enable the elevation penalty. Requires the network geometry to carry
   z-coordinates (3D LineStrings). When enabled, uphill travel accrues
   :py:data:`elevation_penalty` extra cost per meter of vertical rise.
   Downhill travel is unpenalized (walking downhill is faster or neutral).

.. py:data:: elevation_penalty

   :Type: ``int``
   :Default: ``4``

   Cost added per meter of vertical rise. A value of 4 means every meter
   uphill feels like 4 extra meters of horizontal walking. Calibrate
   against your local terrain and empirical route-choice studies.

.. py:data:: turns

   :Type: ``bool``
   :Default: ``False``

   Enable turn penalties along routes. Both pedestrians and cyclists
   prefer to avoid unnecessary turns, and turn-aware routing is often
   what separates a defensible pedestrian flow model from a naive one.
   Turn-aware routing is 2–4× slower than turn-free routing.

.. py:data:: turn_threshold

   :Type: ``int``
   :Default: ``45``

   Angle (in degrees) above which a change of direction at a node counts
   as a "turn." A value of 45 penalizes any deviation of more than 45°
   from straight-ahead; higher thresholds only penalize sharper turns.

.. py:data:: turn_penalty

   :Type: ``int``
   :Default: ``32``

   Cost added per turn (once ``turn_threshold`` is exceeded). Calibrate
   against local route-choice data.


Clustering and parallelism
--------------------------

.. py:data:: clasters

   :Type: ``int``
   :Default: ``1``

   Number of spatial clusters used to split large origin sets for
   parallel processing. Set > 1 only when :py:data:`cluster_parallel`
   = True. Leave at 1 for datasets under ~5,000 origins.

.. py:data:: cluster_parallel

   :Type: ``bool``
   :Default: ``False``

   Enable cluster-parallel processing during the centrality loop. Boosts
   throughput on large jobs but adds a fixed setup cost — not worth it
   for small runs.

.. py:data:: cluster_workers

   :Type: ``int``
   :Default: ``1``

   Maximum worker processes when ``cluster_parallel`` is True. Defaults
   to 1; the ``RunAccessibility`` and ``RunFlow`` methods automatically
   raise this to ``max(1, cpu_count() - 1)`` when the turn-aware or flow
   engine runs.


Output formats and paths
------------------------

.. py:data:: output_folder

   :Type: ``str``
   :Default: ``None``

   Folder where results are written. When None (default), results land
   in a ``Results/`` subfolder of ``data_folder``.

.. py:data:: output_wStamp

   :Type: ``bool``
   :Default: ``True``

   When True, each run creates a timestamped subfolder
   (``YYYY-MM-DD_HHMM``) inside ``output_folder``. Keeps repeated runs
   from overwriting each other. Turn off if you want a single canonical
   output location.

.. py:data:: output_copy_source_data

   :Type: ``bool``
   :Default: ``False``

   When True, the original input layers are copied into the output
   folder for reproducibility. Off by default to save disk space.

.. py:data:: output_geojson

   :Type: ``bool``
   :Default: ``True``

   Write results as GeoJSON. Cross-platform, human-readable, but
   verbose. Recommended for small-to-medium datasets and for opening in
   QGIS.

.. py:data:: output_feather

   :Type: ``bool``
   :Default: ``True``

   Write results as Apache Feather (binary columnar format). Compact and
   fast to load in Python (``gpd.read_feather``). Recommended for
   large datasets and for pipeline-style workflows.

.. py:data:: output_csv

   :Type: ``bool``
   :Default: ``False``

   Write a CSV alongside the geo output — attributes only, no geometry.
   Handy for opening in Excel or joining to non-spatial data.

.. py:data:: output_shp

   :Type: ``bool``
   :Default: ``False``

   Write results as ESRI Shapefile. Not recommended unless a downstream
   tool requires it — Shapefile truncates column names to 10 characters
   and has a 2 GB size limit.

.. py:data:: csv_delimiter

   :Type: ``str``
   :Default: ``","``

   Separator character for CSV output. Use ``"\t"`` for TSV or ``";"``
   for European Excel locales.

.. py:data:: result_prefix

   :Type: ``str``
   :Default: ``""``

   Optional prefix prepended to every result column name. Useful when
   running multiple analyses into the same output folder so their
   columns don't clash (e.g. ``"school\_"``, ``"bus\_"``).

.. py:data:: output_file_name

   :Type: ``str``
   :Default: ``"Results"``

   Base filename for output files (extension added automatically).
   Change per run to keep outputs identifiable.


Flow analysis — engine selection
--------------------------------

.. py:data:: flow_engine

   :Type: ``one of "k_alternatives" | "aggregate_flow"``
   :Default: ``"aggregate_flow"``

   Which flow engine ``RunFlow()`` dispatches to.

   ``"aggregate_flow"`` (default since 2.5.5) computes flow over each
   OD pair's full gradient-overlap envelope in a single pass — no path
   enumeration — and scales to state-wide analyses where path
   enumeration becomes infeasible. Turn-aware routing and assigned
   routing are not yet supported by this engine. See
   :doc:`../concepts/aggregate_flow`.

   ``"k_alternatives"`` enumerates up to *K* discrete alternative paths
   per origin-destination pair with Plateau's penalty method — full
   path-level fidelity; required for turn-aware flow, assigned routing,
   and :py:data:`flow_output_routes`. See
   :doc:`../concepts/k_alternatives`.

   Both engines populate the same outputs, so everything downstream
   (exports, batch runs) works unchanged when switching.


Flow analysis — detour envelope and path enumeration
----------------------------------------------------

The following block controls how the Flow engine explores alternative
paths between each origin and destination. See
:doc:`../concepts/k_alternatives` for the underlying Plateau's penalty
method. Both flow engines honor the same detour envelope.

.. py:data:: flow_detour_ratio

   :Type: ``float``
   :Default: ``1.05``

   Maximum allowed path length as a ratio of the shortest path
   between an origin and destination. ``1.05`` allows paths up to 5 %
   longer than the shortest. Larger values enumerate more alternatives
   (and take more time). ``1.15`` is a good starting point for
   pedestrian flow modeling.

.. py:data:: flow_detour_buffer

   :Type: ``float``
   :Default: ``100``

   Maximum extra distance (in weight units) allowed for alternative
   paths. Used as an alternative or additional envelope alongside
   ``flow_detour_ratio`` depending on ``flow_detour_mode``.

.. py:data:: flow_detour_mode

   :Type: ``one of "ratio" | "buffer" | "min"``
   :Default: ``"ratio"``

   How to compute the detour envelope: ``"ratio"`` uses
   ``flow_detour_ratio`` alone, ``"buffer"`` uses ``flow_detour_buffer``
   alone, ``"min"`` uses the tighter of the two. ``"min"`` is
   recommended for realistic pedestrian modeling — the buffer prevents
   absurd long detours for very short trips, and the ratio prevents
   proportionally sensible detours from being clipped on long trips.


Flow — decay and trip-generation elasticity
-------------------------------------------

The Flow engine models each origin's trip generation as
``origin_weight × decay_factor × destination_probability``. The next
few fields control the decay factor and how it aggregates across
destinations.

.. py:data:: flow_decay

   :Type: ``bool``
   :Default: ``True``

   When True, applies distance decay to trip generation — origins with
   only distant destinations generate fewer trips than origins with
   nearby destinations. When False, every origin generates trips at its
   full weight regardless of destination proximity.

.. py:data:: flow_decay_curve

   :Type: ``one of "exponential" | "logistic"``
   :Default: ``"exponential"``

   Shape of the decay curve. ``"exponential"`` uses
   ``exp(-gravity_beta × distance)``. ``"logistic"`` uses the S-shape
   controlled by :py:data:`gravity_logistic_midpoint`.

.. py:data:: flow_decay_method

   :Type: ``one of "closest" | "gravity_cap"``
   :Default: ``"closest"``

   How the decay factor is aggregated across an origin's reachable
   destinations. ``"closest"`` uses the decay-curve value at the *nearest*
   destination — parameter-free and monotonic. ``"gravity_cap"`` uses
   ``min(1, gravity / flow_gravity_cap)``, where gravity is the sum of
   ``destination_weight × decay`` — captures destination density and
   allows explicit calibration. Both methods eliminate the
   non-monotonicity of earlier UNA versions where adding a far
   destination could *reduce* trip generation.

.. py:data:: flow_gravity_cap

   :Type: ``float``
   :Default: ``1``

   The gravity value above which an origin saturates at full
   trip-generation (factor = 1.0). Only used when
   ``flow_decay_method = "gravity_cap"``. Calibrate against your
   destination-weight scale — if median destination weight is 30, a cap
   of 30 means "one median-weight destination at zero distance saturates
   the origin".

.. py:data:: flow_path_detour_penalty

   :Type: ``one of "equal" | "exponential" | "logistic"``
   :Default: ``"equal"``

   How trip volume splits among routing alternatives.
   ``"equal"`` distributes trips uniformly across all valid alternatives.
   ``"exponential"`` and ``"logistic"`` shift more volume onto the
   shorter alternatives. Used by both flow engines: in
   ``k_alternatives`` it weights the enumerated paths; in
   ``aggregate_flow`` it weights every admissible arc of the gradient
   envelope by its detour excess — the same parameters control
   shortest-route concentration in both.

.. py:data:: flow_route_enumeration_beta

   :Type: ``float``
   :Default: ``0.0``

   Decay coefficient for the exponential path-choice penalty (when
   ``flow_path_detour_penalty = "exponential"``). Larger values shift
   volume more aggressively toward the shortest path.

.. py:data:: flow_route_enumeration_logistic_midpoint

   :Type: ``float``
   :Default: ``0.0``

   Midpoint for the logistic path-choice penalty (when
   ``flow_path_detour_penalty = "logistic"``). In the same units as
   detour distance.


Flow — origin, destination, and directional weighting
-----------------------------------------------------

.. py:data:: flow_origin_weights

   :Type: ``bool``
   :Default: ``True``

   When True, each origin generates trips scaled by its
   ``origin_weight_column`` value. When False, every origin generates
   exactly one trip regardless of its weight column.

.. py:data:: flow_destination_weights

   :Type: ``bool``
   :Default: ``True``

   When True, trip distribution across destinations follows a
   Huff-style gravity model:
   ``prob[d] = weight[d] × decay[d] / Σ (weight × decay)``. When False,
   trips split uniformly across reachable destinations regardless of
   attractiveness.

.. py:data:: flow_return_directional

   :Type: ``bool``
   :Default: ``False``

   When True, the output distinguishes AB (start→end) from BA
   (end→start) flow per edge. Useful when the network has one-way edges
   or when you want to visualize directional traffic. When False,
   contributions are averaged across both directions for a symmetric
   undirected result.


Observer points
---------------

Observer points are passive flow counters — snapped to an edge (or
node) and reporting the flow that passes through them, without
influencing routing. Used only by ``RunFlow()``. See
:doc:`observers_obstacles` for context.

.. py:data:: observer_points_file

   :Type: ``str`` or ``None``
   :Default: ``None``

   Point layer of observer locations. Leave None to skip observer
   tracking entirely. When set, each point must fall within snapping
   distance of a network edge.

.. py:data:: observer_points_uid_column

   :Type: ``str`` or ``None``
   :Default: ``None``

   Column carrying an ID for each observer point. Written into the
   output so observers can be joined back to your source data.

.. py:data:: observer_points_snap_to

   :Type: ``one of "edge" | "node"``
   :Default: ``"edge"``

   Whether observers snap to the nearest edge (default) or the nearest
   node. Snap to node when you want to count flow through an
   intersection; snap to edge when you want to count flow on a specific
   segment.


Obstacle points
---------------

Obstacle points add per-edge or per-node cost penalties without
requiring you to edit the underlying network. Used by both
``RunAccessibility()`` and ``RunFlow()``. Stack additively with
elevation, turns, and any custom cost column.

.. py:data:: obstacle_points_file

   :Type: ``str`` or ``None``
   :Default: ``None``

   Point layer of obstacles. Each point must have a numeric penalty
   value in the column named by :py:data:`obstacle_points_penalty_column`.

.. py:data:: obstacle_points_uid_column

   :Type: ``str`` or ``None``
   :Default: ``None``

   Column carrying an ID for each obstacle. Written into optional
   usage-tracking output.

.. py:data:: obstacle_points_penalty_column

   :Type: ``str``
   :Default: ``"penalty"``

   Column name for the numeric penalty (in edge-weight units) that each
   obstacle adds to its host edge. A penalty of 100 on a broken sidewalk
   makes routing feel like the segment is 100 m longer.

.. py:data:: obstacle_points_direction_column

   :Type: ``str`` or ``None``
   :Default: ``None``

   Column carrying per-obstacle direction values, one of ``"both"``,
   ``"AB"``, or ``"BA"``. When None (or the column is missing), every
   obstacle defaults to ``"both"``. Use for directional obstacles like
   a missing curb ramp that impedes one direction of travel only.

.. py:data:: obstacle_points_snap_to

   :Type: ``one of "edge" | "node"``
   :Default: ``"edge"``

   Whether obstacles snap to the nearest edge (default) or the nearest
   node. Edge-snapping penalizes a specific segment; node-snapping
   penalizes every path through the intersection.

.. py:data:: flow_track_obstacle_points_usage

   :Type: ``bool``
   :Default: ``False``

   When True, ``RunFlow()`` writes an extra output file with per-obstacle
   hit counters (AB / BA / total) — showing how often paths crossed each
   obstacle despite the penalty. Useful for prioritizing obstacle
   remediation ("which broken sidewalks still get the most foot
   traffic?").


K-nearest destinations cap
--------------------------

.. py:data:: flow_k_nearest_destinations

   :Type: ``int``
   :Default: ``0``

   Cap on how many destinations each origin routes to — the *k* nearest
   by network distance. ``0`` means "no user cap"; the engine's internal
   hard ceiling of 4000 still applies for memory safety.
   ``flow_k_nearest_destinations = 1`` is equivalent to
   ``use_nearest_destination = True``.

.. py:data:: flow_max_destinations_per_origin

   :Type: ``int``
   :Default: ``0``

   Deprecated alias for :py:data:`flow_k_nearest_destinations`. Kept
   for one release for backward compatibility. If both are set to
   different values, the new name wins and a warning is logged.


K-alternative paths (Plateau's method)
--------------------------------------

.. py:data:: flow_n_alternatives

   :Type: ``int``
   :Default: ``10``

   Maximum number of alternative paths enumerated per origin-destination
   pair. Each alternative is found by penalizing edges used in the
   previous shortest path and re-running Dijkstra. Larger values
   discover more back-alley routes at the cost of runtime.

.. py:data:: flow_alternative_penalty_factor

   :Type: ``float``
   :Default: ``2``

   Multiplier applied to edge weights on each iteration of the penalty
   method. Larger values force alternatives further off the shortest
   path; smaller values enumerate near-copies of the shortest. Must be
   ≥ 1.0.


Route-alternatives output (route-choice studies)
------------------------------------------------

These two fields let the ``k_alternatives`` engine export the complete
generated routes — full geometries, not flow numbers — so externally
observed routes (GPS traces, surveys) can be matched to their modeled
alternatives. See :doc:`run_flow` for the workflow.

.. py:data:: flow_route_id_column

   :Type: ``str`` or ``None``
   :Default: ``None``

   Name of a column present in **both** the origin and destination
   files (text or numeric). Each origin is routed only to the
   destination(s) sharing its value — one OD pair per route ID — and
   the value is written to every exported route. Internally this drives
   the same pairing machinery as
   :py:data:`origin_destination_id_column` /
   :py:data:`destination_id_column`; setting those two directly (with
   different column names per file) also works. Setting both this and
   the assigned-routing columns to conflicting names raises a
   validation error.

.. py:data:: flow_output_routes

   :Type: ``bool``
   :Default: ``False``

   When True, ``RunFlow()`` additionally exports every generated
   alternative path as ``<file_name>_routes.*`` (GeoJSON / feather /
   CSV per the ``output_*`` flags). One row per alternative with
   columns ``route_id``, ``origin_uid``, ``dest_uid``, ``alt_rank``
   (1 = shortest), ``route_cost``, ``n_edges``, ``edge_ids``, and a
   merged LineString geometry. Requires
   ``flow_engine = "k_alternatives"`` — with ``aggregate_flow`` the
   flag is ignored with a logged warning, since that engine does not
   enumerate individual routes.


Flow — output and tracking options
----------------------------------

.. py:data:: flow_debug_print_paths

   :Type: ``bool``
   :Default: ``False``

   When True, the engine prints a one-line summary of every emitted
   alternative path to stdout (destination index, total cost, arc count).
   Useful for small test runs; leave off in production — the output can
   overwhelm the terminal on large jobs.

.. py:data:: flow_track_origins_per_destination

   :Type: ``bool``
   :Default: ``False``

   When True, ``RunFlow()`` writes an extra output listing which
   origins routed to each destination. Useful for accessibility
   inequity analysis ("which residential blocks contribute to this
   school's foot traffic?").

.. py:data:: flow_compute_node_flow

   :Type: ``bool``
   :Default: ``False``

   When True, ``RunFlow()`` also computes per-node flow (in addition to
   per-edge). Adds a small overhead but produces the node-flow output
   file that many downstream visualizations expect.


Batch composite output
----------------------

These fields control ``RunBatch()``'s composite feature: merging the
per-row results of a project into joint output files with a sum
column. See :doc:`run_batch` for the mechanics and
:doc:`../tutorials/tutorial_2_accessibility` for a WalkScore-style
worked example.

.. py:data:: batch_composite_output

   :Type: ``bool``
   :Default: ``False``

   Master switch, set per project row. Rows with ``TRUE`` have their
   result column captured during the batch and merged into a composite
   output after all rows complete. Rows with ``FALSE`` still write
   their individual outputs but are left out of the composite.

.. py:data:: batch_composite_result_column

   :Type: ``Literal["reach", "gravity_exponential", "gravity_logistic", "knn_access", "edge_flow", "node_flow"]``
   :Default: ``"reach"``

   Which engine result the row contributes to the composite. Per-origin
   metrics (``reach``, ``gravity_*``, ``knn_access``) join onto the
   row's origins layer; ``edge_flow`` joins onto the network layer;
   ``node_flow`` onto network nodes.

.. py:data:: batch_composite_column_prefix

   :Type: ``str``
   :Default: ``""``

   Optional prefix prepended to each captured column's name in the
   composite file. Column names follow
   ``<prefix><metric>_<row name>`` — e.g. ``knn_access_Homes to Jobs``.

.. py:data:: batch_composite_sum_column_name

   :Type: ``str``
   :Default: ``"composite_sum"``

   Name of the final column that sums every captured column in the
   composite — the WalkScore-style total.


Other
-----

.. py:data:: progressbar

   :Type: ``bool``
   :Default: ``True``

   Show a progress bar during long-running calculations. Turn off when
   running from a non-interactive script or piping stdout to a file.

.. py:data:: logger_verbosity

   :Type: ``int``
   :Default: ``1``

   Log detail level. ``0`` = errors only, ``1`` = summary (default),
   ``2`` = detailed per-stage messages. Set to ``2`` when
   troubleshooting an unexpected result; ``0`` in production batch runs.

.. py:data:: name

   :Type: ``str``
   :Default: ``"Default"``

   Human-readable name for the current settings snapshot. Written into
   project files and used as the ``output_file_name`` when
   ``RunBatch()`` runs a project row. Set per row so batched outputs
   are self-labeling.


.. note::

   The settings above cover the full public surface of ``Settings``.
   A handful of internal fields (``_version``, and any attribute
   prefixed with an underscore) are not documented here — they are
   subject to change without notice.
