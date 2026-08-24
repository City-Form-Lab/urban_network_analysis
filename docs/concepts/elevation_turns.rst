Elevation and turn penalties
============================

Two of UNA's most consequential impedance factors — elevation and turn
penalties — have their own mathematical machinery worth understanding.
This page explains how each modifies the network graph before Dijkstra
runs and how they interact with the base edge cost.

For a practical overview of when to enable each, see
:doc:`../user_guide/impedance_models`.

.. contents:: On this page
   :local:
   :depth: 1


Elevation
---------

Real pedestrians don't experience uphill and downhill walking
symmetrically. Climbing a 100 m rise takes real effort — measured in
studies as roughly 3–6 times the effort of walking the same horizontal
distance. Descending the same 100 m is nearly free.

UNA models this asymmetry by adding a per-meter-of-rise penalty on
uphill arcs only.

The math
~~~~~~~~

Requires 3D LineStrings in
:py:data:`../user_guide/settings_reference:network_file` (each vertex
has a z-value). For every edge with start node *s* and end node *e*,
UNA computes the elevation difference:

.. math::

   \Delta z = z_e - z_s

The engine then builds directional arc weights. The AB direction of
travel (start → end) pays extra cost proportional to the positive part
of Δz:

.. math::

   w_{AB} = w_\text{base} + \text{elevation\_penalty} \cdot \max(0, \Delta z)

The BA direction (end → start) pays extra cost proportional to the
positive part of −Δz:

.. math::

   w_{BA} = w_\text{base} + \text{elevation\_penalty} \cdot \max(0, -\Delta z)

Only one direction of a given edge is ever penalized — the uphill one.
Downhill is unpenalized.

**Where** *w*\ :sub:`base` is the edge's cost from
:py:data:`../user_guide/settings_reference:network_weight_column`
(geometric length by default), and elevation_penalty is
:py:data:`../user_guide/settings_reference:elevation_penalty`.

A worked example
~~~~~~~~~~~~~~~~

Consider an edge of geometric length 100 m rising 20 m from start to
end, with elevation_penalty = 4:

- **AB direction** (uphill): base 100 + 4 × 20 = **180 m** effective
  cost.
- **BA direction** (downhill): base 100 + 4 × 0 = **100 m** effective
  cost.

The same 100 m ground segment feels like 180 m going up and 100 m
going down.

Calibrating elevation_penalty
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

+---------+----------------------------------+
| Value   | Interpretation                   |
+=========+==================================+
| 2       | Fit but casual walker on gentle  |
|         | slopes.                          |
+---------+----------------------------------+
| 4       | Typical walking calibration      |
|         | (the default).                   |
+---------+----------------------------------+
| 6       | Older or slower pedestrian, or   |
|         | steep terrain.                   |
+---------+----------------------------------+
| 10      | Casual biking on flat terrain.   |
+---------+----------------------------------+
| 20      | Biking with substantial climbs.  |
+---------+----------------------------------+

These are illustrative starting points. Calibrate against local
route-choice studies if you have them.


Turn penalties
--------------

Pedestrians and cyclists prefer straight-ahead routes. Every sharp
change of direction adds perceptual and physical cost — slowing down,
checking for traffic, adjusting stride. UNA models this by penalizing
turns above a threshold angle.

Unlike elevation, which is a per-edge weight adjustment, turns require
a *transformation* of the graph itself.

The line graph
~~~~~~~~~~~~~~

When :py:data:`../user_guide/settings_reference:turns` = True, UNA
constructs a **line graph** of the network:

- Every directed *arc* in the original network becomes a *node* in
  the line graph.
- Every valid pair of consecutive arcs (arc A into a junction, arc B
  out of the same junction) becomes an *edge* in the line graph.
- The weight of each line-graph edge is the cost of arc B plus a
  turn cost at the junction between A and B.

Dijkstra then runs on this line graph, so every turn along a route
contributes its cost exactly once — and the shortest path in the
line graph is the true shortest turn-aware route in the original
network.

The line graph has substantially more nodes and edges than the
original — usually 2–4× as many — which is why turn-aware routing
is 2–4× slower.

Turn detection
~~~~~~~~~~~~~~

For each junction between an incoming arc A (from node *u* → *v*) and
outgoing arc B (from node *v* → *w*), UNA measures the angular change
between the two arc directions at node *v*.

Let :math:`\theta_A` be the compass bearing of arc A approaching *v*,
and :math:`\theta_B` the bearing of arc B leaving *v*. The turn angle
is:

.. math::

   \alpha = |\theta_B - \theta_A|_{\text{mod } 360}

with the modular difference taken so that :math:`\alpha \in [0, 180]`.
A value of 0 means "going straight through the junction" (no
direction change). A value of 90 means "hard left or right." A value
of 180 means "U-turn."

The turn is penalized when α exceeds
:py:data:`../user_guide/settings_reference:turn_threshold`:

.. math::

   \text{turn\_cost} = \begin{cases}
     \text{turn\_penalty} & \text{if } \alpha > \text{turn\_threshold} \\
     0 & \text{otherwise}
   \end{cases}

Below the threshold, the direction change is considered "going
straight" (a gentle curve, a slight jog) and pays nothing. Above the
threshold, the full turn_penalty is added regardless of angle — the
penalty is not a continuous function of the angle.

A worked example
~~~~~~~~~~~~~~~~

Consider two edges meeting at a junction:

- Edge A points north-east (bearing 45°) into the junction.
- Edge B leaves the junction heading east (bearing 90°).

Turn angle: :math:`|90 - 45| = 45°`.

With ``turn_threshold = 45``, this is exactly at the threshold. UNA
uses strict inequality (α > threshold), so this specific turn is
*not* penalized. A slightly sharper turn (edge B at bearing 91°)
would be.

With ``turn_penalty = 32``, that slightly sharper turn adds 32 m of
effective route cost.

Calibrating turn parameters
~~~~~~~~~~~~~~~~~~~~~~~~~~~

+---------------+----------------------------------------+
| Value         | Interpretation                         |
+===============+========================================+
| threshold=30° | Penalizes even gentle bends.           |
+---------------+----------------------------------------+
| threshold=45° | Default — penalizes anything sharper   |
|               | than a gentle curve.                   |
+---------------+----------------------------------------+
| threshold=60° | Only penalizes hard turns.             |
+---------------+----------------------------------------+
| threshold=90° | Only penalizes right-angle+ corners.   |
+---------------+----------------------------------------+

+---------------+----------------------------------------+
| penalty       | Interpretation                         |
+===============+========================================+
| 15            | Mild — most routes barely change.      |
+---------------+----------------------------------------+
| 32            | Default — noticeable route             |
|               | reshaping, calibrated against          |
|               | pedestrian preferences.                |
+---------------+----------------------------------------+
| 50–80         | Aggressive — routes strongly prefer    |
|               | fewer turns.                           |
+---------------+----------------------------------------+
| > 200         | Turns become nearly forbidden; use     |
|               | with caution or you'll get empty       |
|               | output for many origins.               |
+---------------+----------------------------------------+


How the two combine with base cost and obstacles
------------------------------------------------

For every arc, the final effective cost seen by Dijkstra is:

.. math::

   w_{\text{eff}} = w_{\text{base}} + \text{obstacle\_penalty} + \text{elevation\_term}

The turn cost is added *at the transition between arcs* in the line
graph, not on the arc itself. So the total cost of a route that
traverses arcs :math:`a_1, a_2, \ldots, a_n` with turns
:math:`t_1, t_2, \ldots, t_{n-1}` at the intervening junctions is:

.. math::

   C_\text{route} = \sum_i w_{\text{eff}}(a_i) + \sum_j \text{turn\_cost}(t_j)

All four contributions — base, obstacle, elevation, turn — are in the
same units (meters by default). Mixing units silently breaks the
model.


Common pitfalls
---------------

**Elevation without z-coordinates.** If your network file is 2D (no z
on the vertices), enabling
:py:data:`../user_guide/settings_reference:elevation` is a silent
no-op. Confirm your network has 3D geometry before expecting
elevation effects.

**Turn penalties too high.** If ``turn_penalty > search_radius``,
routes with even one turn exceed the origin's search budget and
become unreachable. Sanity-check by running a single origin and
confirming it reaches some destinations.

**Threshold and penalty are not additive.** A 60° turn pays exactly
turn_penalty, not 60° × turn_penalty. The threshold is a gate, the
penalty is a fixed cost.

**Elevation and turns are orthogonal.** Turn detection uses only the
2D geometry, so elevation and turn behaviors stack without
interference. Both can be on simultaneously.


Related pages
-------------

- :doc:`../user_guide/impedance_models` — practical guide to
  configuring elevation, turns, and obstacles together.
- :doc:`../user_guide/observers_obstacles` — obstacle penalties.
- :doc:`../tutorials/tutorial_2_accessibility` — steps (g) and (h)
  demonstrate elevation and turns in sequence.
- :doc:`../tutorials/tutorial_3_flow` — same in the flow context.
