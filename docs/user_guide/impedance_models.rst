Impedance: elevation, turns, obstacles
======================================

**Impedance** is every extra cost UNA adds onto a route beyond its raw
geometric length. Pedestrians and cyclists don't perceive routes as
their surveyed distance — uphill segments feel longer, sharp turns
feel worse, broken pavement adds friction. UNA models each of these
through settings that stack additively onto the edge weights before
Dijkstra runs.

This page explains how the three impedance factors — elevation, turns,
obstacles — compose, how they interact with your network's cost
column, and how to calibrate each.

.. contents:: On this page
   :local:
   :depth: 1


The impedance stack
-------------------

Every edge that the routing algorithm sees ends up with a cost of:

.. code-block:: text

   effective_cost = base_edge_cost
                  + obstacle_penalty            (if obstacle_points_file is set)
                  + elevation_penalty × Δz      (if elevation = True and z > 0)
                  + turn_penalty                (if turns = True and angle > threshold)

**base_edge_cost** is whatever your
:py:data:`settings_reference:network_weight_column` supplies. By default
that's the segment's geometric length in meters; if you point the setting
at a perceived-length column instead, it's meters-as-the-pedestrian-
experiences-them (see :doc:`../tutorials/tutorial_1_networks` on
perceived length).

All four terms are in the **same units**. If your network cost is
meters, so are your obstacle penalties, elevation penalties, and turn
penalties. If your network cost is minutes, all four terms are minutes.
Mixing units silently breaks the model — pick one and stick with it.


Elevation
---------

**What it models.** Uphill walking is slower and more costly. Downhill
walking is not — walkers descend at their normal comfortable pace with
minimal effort, and the fatigue asymmetry between climbing and
descending is well-documented in route-choice research. UNA captures
this by adding a per-meter-of-rise penalty on uphill edges only.

**How it enters the graph.** Requires 3D LineStrings in
:py:data:`settings_reference:network_file` (each vertex has a z-value).
For every edge, UNA computes ``Δz = z_end − z_start``. The AB direction
pays ``elevation_penalty × max(0, Δz)`` extra; the BA direction pays
``elevation_penalty × max(0, −Δz)``.

**Settings:**

.. code-block:: python

   una.settings.elevation         = True
   una.settings.elevation_penalty = 4

**Calibration.** Typical values for walking are 3–6 (meters of
horizontal-equivalent added per meter of vertical rise). For biking,
8–20. These are calibrated in academic pedestrian route-choice studies.
A value of 4 means: every meter of rise feels like 4 extra meters of
horizontal walking — a 100 m climb feels like 500 m of ground distance
(the 100 m ground + 400 m elevation penalty).

**Runtime.** Cheap — elevation is a per-edge weight adjustment, so it
adds no complexity to the routing algorithm. The Accessibility engine used for
elevation-aware runs is called ``AccessibilityWElevation``.

**Interactions.** Elevation is directional (uphill costs more, downhill
doesn't). It stacks additively with obstacle penalties on the same edge.
It's fully compatible with turn penalties.


Turn penalties
--------------

**What it models.** Pedestrians and cyclists prefer straighter routes.
Every sharp change of direction costs perceptual and physical effort
— slowing down, checking traffic, adjusting stride. Route-choice
studies consistently find that turns are penalized well beyond the
tiny distance they add.

**How it enters the graph.** When
:py:data:`settings_reference:turns` = True, UNA builds a **line graph**
of the network — a graph where each original edge becomes a node, and
each valid pass-through at a junction becomes an edge with a turn cost.
Dijkstra then runs on this line graph, so every turn along a route
contributes its cost exactly once.

The turn cost is applied when the angle change at a junction exceeds
:py:data:`settings_reference:turn_threshold`. Below the threshold, the
turn is considered "going straight" and pays nothing.

**Settings:**

.. code-block:: python

   una.settings.turns          = True
   una.settings.turn_threshold = 45      # degrees
   una.settings.turn_penalty   = 32      # meters

**Calibration.** ``turn_threshold`` = 45° penalizes anything sharper
than a gentle curve. Lower values (30°) penalize even gentle bends;
higher (90°) only penalize sharp corners. ``turn_penalty`` = 32
means each turn costs 32 m of horizontal-equivalent walking. Typical
calibrated values for pedestrians are 30–40 m per turn.

**Runtime.** Expensive — the line-graph transformation makes routing
2–4× slower on dense urban networks. Expect 1–3 minutes on a 20,000-
origin Cambridge run with turns on, versus 30–60 seconds with turns off.
Prototype your parameters with turns off, then flip them on for the
final run.

**Interactions.** Turn penalties enter through a fundamentally different
mechanism (line graph) than the per-edge additive penalties. However,
edge-level costs (base cost + obstacle + elevation) still enter the
line graph as arc weights, so all four impedance components compose
correctly.


Obstacles
---------

**What it models.** Localized impediments to walking or biking —
broken pavement, missing curb ramps, construction zones, narrow
sidewalks — that don't warrant editing the network geometry but
should still affect routing.

**How it enters the graph.** Each obstacle sits at a specific point,
snaps to its nearest edge or node, and adds a
:py:data:`settings_reference:obstacle_points_penalty_column` cost onto
that host edge before Dijkstra runs. The penalty is directional
if the obstacle's ``direction`` value is ``"AB"`` or ``"BA"``, otherwise
it applies to both directions.

**Settings:**

.. code-block:: python

   una.settings.obstacle_points_file             = "SidewalkIssues.geojson"
   una.settings.obstacle_points_penalty_column   = "penalty_m"
   una.settings.obstacle_points_direction_column = "direction"    # optional
   una.settings.obstacle_points_snap_to          = "edge"

See :doc:`observers_obstacles` for the full obstacle model and
calibration table.

**Runtime.** Cheap — obstacles are just per-edge weight adjustments.

**Interactions.** Obstacles stack additively with elevation (an
obstacle on an uphill segment makes the uphill even more expensive)
and pass through the turn-aware line graph correctly.


A complete impedance example
----------------------------

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"       # 3D
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"
   una.settings.search_radius     = 800

   # Custom edge cost — perceived length column (optional)
   una.settings.network_weight_column = "perceived_length"

   # Elevation
   una.settings.elevation         = True
   una.settings.elevation_penalty = 4

   # Turns
   una.settings.turns          = True
   una.settings.turn_threshold = 45
   una.settings.turn_penalty   = 35

   # Obstacles
   una.settings.obstacle_points_file             = "sidewalk_issues.geojson"
   una.settings.obstacle_points_penalty_column   = "penalty_m"
   una.settings.obstacle_points_direction_column = "direction"

   una.RunAccessibility()

Every reachable destination's effective distance from every origin
combines: perceived edge lengths, elevation on uphill segments, turn
penalties at every sharp bend, and obstacle penalties at every
broken-sidewalk point along the route.


Choosing what to enable
-----------------------

For **quick prototyping** (parameter sweeps, demos, sanity
checks):

- Turns **off**, elevation **off**, obstacles **off**.
- Fastest possible iteration; a full-city analysis in seconds.

For a **defensible pedestrian model** (published research, policy
recommendations):

- Turns **on**, elevation **on**, obstacles **on** if you have
  inventoried data.
- 5–15× slower than the prototyping configuration, but produces
  routing that a route-choice researcher would recognize as
  realistic.

For a **before-vs-after scenario** (add a new bridge, close a street,
fix a sidewalk):

- Run twice with the same impedance settings, changing only the
  network or obstacle file between runs.
- Diff the per-edge output. That's your scenario impact.


Common calibration pitfalls
---------------------------

**Turn penalties too high erase all turning.** If ``turn_penalty`` is
larger than the ``search_radius``, routes with even one turn become
unreachable — the model returns empty output. Sanity-check by
running one origin and confirming it can reach at least some
destinations.

**Elevation without z-coordinates does nothing.** If your network file
is 2D (no z on the vertices), enabling elevation is a no-op and no
warning is printed. Confirm your network has 3D geometry before
expecting elevation effects.

**Obstacle penalty of 0 is a no-op.** Zero-penalty obstacles are
loaded but have no routing effect. Check your penalty column values.

**Units mismatch.** All four impedance components (base, elevation,
turn, obstacle) must be in the same units. If your base cost is meters
but your obstacle penalty is minutes, the model silently misbehaves.
Convert to a common unit before running.


Next steps
----------

- :doc:`observers_obstacles` — full detail on the obstacle model.
- :doc:`run_accessibility`, :doc:`run_flow`, :doc:`run_odm` — impedance
  applies to all three analyses.
- :doc:`../concepts/elevation_turns` — deeper math on elevation and
  turns.
- :doc:`../tutorials/tutorial_2_accessibility` — steps (g) and (h)
  show elevation and turns applied in sequence with numeric results.
