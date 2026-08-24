Observers and obstacles
=======================

Beyond the standard origin and destination layers, UNA supports two
optional point layers that extend flow and accessibility analyses in
different directions:

- **Observer points** are passive flow counters. They report the flow
  that passes through them--conceptually, observers are like human traffic counters that simply tally the flow they observe at a given location, without influencing that flow themselves. Since flow can vary at different locations on a network edge, observers allow you to capture flow at a specific location.
- **Obstacle points** are cost-adding penalties. They make specific
  edges (or nodes) feel longer to the routing engine, changing which
  paths get chosen.

Both are optional. When their file settings are left ``None`` (the
default), neither layer is loaded and analyses proceed exactly as
before.

.. contents:: On this page
   :local:
   :depth: 1


Observer points
---------------

An observer point is analogous to a real-world pedestrian counter — a
camera, a tube counter, or an intercept-survey site. It sits at a
specific location on the network and reports how much flow crosses it,
after the flow engine has run.

**Observers do not influence routing.** They are pure output
instruments. Whether or not you configure observers, the flow field
across the network is identical.

When to use them
~~~~~~~~~~~~~~~~

- You have real counter locations from field data and want to check
  which counters would light up in the model.
- You want to sample flow at a handful of specific intersections
  without exporting the full per-edge output.
- You want directional (AB vs BA) flow at specific points — the
  observer output includes ``flow_AB``, ``flow_BA``, and
  ``flow_total`` per point.


Which engines use them
~~~~~~~~~~~~~~~~~~~~~~

Observers are loaded only by ``RunFlow()``. They make no sense for
accessibility, which produces per-origin scores rather than per-edge
flow.

Configuration
~~~~~~~~~~~~~

.. code-block:: python

   una.settings.observer_points_file       = "tut4_observers_main_st.geojson"
   una.settings.observer_points_uid_column = "counter_id"
   una.settings.observer_points_snap_to    = "edge"   # or "node"

Snap semantics:

- ``"edge"`` (default) — each observer snaps to its nearest edge. Its
  flow is the flow on that edge.
- ``"node"`` — each observer snaps to its nearest node. Its flow is
  the sum of flow through arcs incident to that node.

Output
~~~~~~

After ``RunFlow()`` finishes, a ``<file_name>_observer_points.feather``
/ ``.geojson`` file lands alongside the main per-edge output. Columns:

+---------------------+-------------------------------------------+
| Column              | Meaning                                   |
+=====================+===========================================+
| ``observer_idx``    | Zero-based row index.                     |
+---------------------+-------------------------------------------+
| ``observer_uid``    | UID from ``observer_points_uid_column``   |
|                     | (if configured).                          |
+---------------------+-------------------------------------------+
| ``flow_AB``         | Flow in the network's AB direction.       |
+---------------------+-------------------------------------------+
| ``flow_BA``         | Flow in the network's BA direction.       |
+---------------------+-------------------------------------------+
| ``flow_total``      | ``flow_AB + flow_BA``.                    |
+---------------------+-------------------------------------------+
| ``geometry``        | The observer's original point geometry.   |
+---------------------+-------------------------------------------+


Obstacle points
---------------

An obstacle point adds a **cost penalty** to a specific edge or node —
without you having to edit the underlying network file. Broken sidewalks,
missing curb ramps, construction zones, narrow crossings, or any other
localized impediment can be represented this way.

**Obstacles do influence routing.** They enter the graph as extra edge
cost before Dijkstra runs, so the engine actively routes around them
when a cheap alternative exists.

When to use them
~~~~~~~~~~~~~~~~

- You've inventoried pedestrian-infrastructure problems in your study
  area and want the model to reflect them.
- You want to test what-if scenarios — "how would flows redistribute
  if we repaired this broken sidewalk?" — without modifying the
  network geometry.
- You want directional penalties (a missing curb ramp that impedes
  crossing in one direction but not the other).


Which engines use them
~~~~~~~~~~~~~~~~~~~~~~

``RunAccessibility()``, ``RunFlow()``, and ``RunODM()`` all respect
obstacles — the penalty enters the network's edge costs before routing,
so accessibility scores, flows, and OD cost matrices alike reflect it.
The penalty composes additively with whatever cost the edge already
carries: geometric or perceived length, elevation, and turn costs.
Obstacle penalties stack additively with elevation and turn penalties
— they enter through the same edge-weight modification pipeline. See
:doc:`impedance_models` for how they compose.

Configuration
~~~~~~~~~~~~~

.. code-block:: python

   una.settings.obstacle_points_file             = "sidewalk_issues.geojson"
   una.settings.obstacle_points_uid_column       = "obstacle_id"
   una.settings.obstacle_points_penalty_column   = "penalty"        # required
   una.settings.obstacle_points_direction_column = "direction"      # optional
   una.settings.obstacle_points_snap_to          = "edge"           # or "node"

The obstacle file must contain a **penalty column** — the numeric
cost (in edge-weight units, meters by default) that each obstacle
adds to its host edge. A penalty of 100 on a broken sidewalk makes
routing feel like the segment is 100 m longer than it really is.

The optional **direction column** takes per-row values ``"both"``,
``"AB"``, or ``"BA"``:

- ``"both"`` (the default when the column is missing) — penalty
  applied to both arc directions of the host edge.
- ``"AB"`` — penalty applied only to the network's AB direction of
  travel. Useful for one-way impediments like a missing curb ramp
  that only affects, say, stepping down from a sidewalk to a
  crosswalk.
- ``"BA"`` — penalty applied only to the BA direction.

Snap semantics:

- ``"edge"`` (default) — obstacle snaps to its nearest edge; the
  penalty modifies traversal cost on that specific segment.
- ``"node"`` — obstacle snaps to its nearest node; the penalty
  modifies every arc entering the node (single-sided to avoid
  double-counting).


Tracking obstacle usage
~~~~~~~~~~~~~~~~~~~~~~~

By default, the flow engine simply uses obstacles as routing costs and
does not report which routes crossed them. To also get **per-obstacle
hit counts** — a diagnostic showing how much flow still went through
each obstacle despite the penalty — enable:

.. code-block:: python

   una.settings.flow_track_obstacle_points_usage = True

A ``<file_name>_obstacle_points_usage`` output file will then be
written, one row per obstacle with ``hits_AB``, ``hits_BA``, and
``hits_total`` columns.

This is useful for prioritizing infrastructure remediation: obstacles
with high hits are the "most-crossed despite being bad" locations —
the ones a pedestrian-friendliness investment would help the most.


Calibrating obstacle penalties
------------------------------

Penalty values are in edge-weight units (meters by default). Rough
starting points for pedestrian modeling:

+--------------------------------+-----------------------+
| Obstacle type                  | Suggested penalty (m) |
+================================+=======================+
| Missing curb ramp              |            15–30 m    |
+--------------------------------+-----------------------+
| Broken / uneven pavement       |            30–60 m    |
+--------------------------------+-----------------------+
| Narrow sidewalk (< 1 m)        |            50–100 m   |
+--------------------------------+-----------------------+
| No sidewalk at all             |          100–200 m    |
+--------------------------------+-----------------------+
| Active construction zone       |          150–300 m    |
+--------------------------------+-----------------------+
| Blocked / impassable           |             > 500 m   |
+--------------------------------+-----------------------+

These are illustrative. Calibrate against local route-choice studies
if you have them. Very large penalties (thousands of meters)
effectively remove the obstacle's edge from the graph.


A combined example
------------------

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids_pop2020.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"
   una.settings.search_radius     = 500

   # Observer counters at real intercept-survey locations
   una.settings.observer_points_file       = "tut4_observers_main_st.geojson"
   una.settings.observer_points_uid_column = "counter_id"

   # Sidewalk-quality obstacles from a field inventory
   una.settings.obstacle_points_file             = "sidewalk_issues.geojson"
   una.settings.obstacle_points_penalty_column   = "penalty_m"
   una.settings.obstacle_points_direction_column = "direction"
   una.settings.flow_track_obstacle_points_usage = True

   una.RunFlow()

Produces three outputs alongside the standard per-edge flow file:

- ``<file>_observer_points.geojson`` — flow at each real counter
  location, ready to compare with observed counts.
- ``<file>_obstacle_points_usage.geojson`` — how many trips crossed
  each broken-sidewalk obstacle despite its penalty.
- ``<file>_network_nodes.geojson`` — per-node flow (on by default via
  :py:data:`settings_reference:flow_compute_node_flow`).


Common questions
----------------

**"What if my observer / obstacle point is far from any edge?"**
Snapping uses spatial nearest-neighbor with no distance cap.
Sanity-check your CRS if a point ends up on a wildly wrong edge —
mismatched CRS is the most common cause.

**"Can I combine a directional obstacle with an already-directional
network?"**
Yes. Direction ``AB`` / ``BA`` refers to the network's edge orientation,
i.e., which direction the LineString is drawn. If your network has
one-way segments, the obstacle's direction is interpreted relative to
that.

**"Do obstacles affect ODM output?"**
Yes — :doc:`run_odm` runs on the same graph, so obstacle-modified
distances flow through to ODM output.

**"Can observers and obstacles coexist on the same edge?"**
Yes. An observer measures whatever flow happens on the edge, including
whatever the obstacle's penalty has reshaped.


Next steps
----------

- :doc:`impedance_models` — how obstacle penalties compose with
  elevation and turn penalties.
- :doc:`run_flow` — the flow method reference.
- :doc:`../getting_started/data_conventions` — file-format and CRS
  rules for observer and obstacle layers.
