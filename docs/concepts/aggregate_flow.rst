Aggregate flow (gradient-overlap method)
========================================

The ``aggregate_flow`` engine is UNA's scalable alternative to
K-alternative path enumeration. Instead of discovering discrete paths
one by one, it computes — for every origin-destination pair — the
**maximum node gradient network**: the full extent of the street
network that can possibly be used given the search radius, detour
envelope, and impedance settings. Trip volume is then distributed
across that entire envelope in a single pass.

Since version 2.5.5 this is UNA's **default** flow engine
(``flow_engine = "aggregate_flow"``); select the K-alternatives engine
instead with:

.. code-block:: python

   una.settings.flow_engine = "k_alternatives"

Both flow engines populate the same outputs, so everything downstream
(exports, batch runs, visualization scripts) works unchanged.

.. contents:: On this page
   :local:
   :depth: 1


Why a second flow engine?
-------------------------

Path enumeration is faithful but expensive: each origin-destination
pair costs *K* penalized Dijkstra runs, and state-wide analyses with
hundreds of thousands of OD pairs become infeasible. The aggregate
engine replaces enumeration with two bounded Dijkstra sweeps per
origin (plus one per destination, computed once and reused) and a
linear-time loading pass — trading path-level fidelity for
near-linear scaling.

The trade is well-behaved because the quantity flow modeling actually
needs — expected volume per edge — does not require knowing each
discrete path, only how trip volume spreads across the corridor of
viable routes.


The max node gradient network
-----------------------------

For one OD pair, let ``d_o[v]`` be the shortest network distance from
the origin to node *v* (the *forward gradient*) and ``d_d[v]`` the
shortest distance from *v* to the destination (the *backward
gradient*). With ``budget`` the detour envelope
(:py:data:`../user_guide/settings_reference:flow_detour_mode` applied
to the OD's shortest cost), an arc *(u, v)* with cost *w* belongs to
the max node gradient network iff:

.. math::

   d_o[u] + w + d_d[v] \le \text{budget}

In words: the arc lies on *some* admissible walk from origin to
destination. This is the full overlap of the forward and backward
gradient trees — every street a plausible trip could touch, and
strictly more than the union of the two shortest-path trees. Because
the gradients are computed on the weighted graph, elevation penalties,
obstacle costs, and custom edge-cost columns shape the envelope
automatically.


Distributing flow across the envelope
-------------------------------------

Every admissible arc receives a share of the OD's trip volume,
weighted by how much of a detour the *best* route through that arc
implies:

.. math::

   \text{excess}(u,v) = d_o[u] + w + d_d[v] - d_\text{shortest}

   q(u,v) \propto \text{decay}(\text{excess}(u,v))

where *decay* is the curve chosen by
:py:data:`../user_guide/settings_reference:flow_path_detour_penalty`
with :py:data:`../user_guide/settings_reference:flow_route_enumeration_beta`
— the same parameters that weight discrete paths in the
k_alternatives engine. Arcs on the shortest path have zero excess and
carry the most flow; arcs on longer detours decay smoothly. With
``"equal"`` penalty the flow diffuses uniformly across the envelope
(rarely what you want — the engine logs a warning); a typical
configuration is:

.. code-block:: python

   una.settings.flow_path_detour_penalty    = "exponential"
   una.settings.flow_route_enumeration_beta = 0.05   # higher = sharper concentration

Each arc's share then travels shortest(origin → *u*) + arc +
shortest(*v* → destination) along the two gradient trees, accumulated
onto edges in two linear passes.


Guarantees
----------

By construction the engine conserves mass:

- **Origin edge = origin weight.** All flow leaves the origin through
  its snap-edge connectors, so the segment fronting the origin carries
  exactly the trips generated there.
- **Destination edge = trip volume.** The same amount arrives at the
  segment fronting the destination.
- **No edge exceeds the trip volume** for a single OD pair, and no
  trip walks past its own origin or destination point.
- **Dead-end streets carry zero flow.** Arcs whose continuation
  u-turns (the destination leg immediately returns through the arc's
  tail, or the origin leg arrived through its head) are excluded, which
  zeroes every cul-de-sac branch exactly.

Trip generation — origin weights, Huff destination shares, distance
decay (:py:data:`../user_guide/settings_reference:flow_decay_method`)
— follows the same math as the k_alternatives engine.


Current limitations
-------------------

- **Turn-aware routing** (``turns = True``) is not yet supported — the
  gradients run on the node graph, not the turn-expanded line graph.
  The engine raises a clear error rather than silently ignoring turns.
- **Assigned routing** (``origin_destination_id_column``) is not yet
  supported.
- **Route-alternatives export**
  (:py:data:`../user_guide/settings_reference:flow_output_routes`)
  requires discrete paths and is therefore a k_alternatives capability;
  with this engine the flag is ignored with a logged warning.
- Flow splits reflect arc-level decay weights rather than discrete
  path probabilities, so exact volumes on parallel routes differ
  slightly from a k_alternatives run with the same settings.


When to use which engine
------------------------

Use ``aggregate_flow`` (the default) when you want flow spread over
the complete envelope of viable streets, and for regional and
state-wide models where the OD count makes enumeration infeasible.
Switch to ``k_alternatives`` for turn-aware models, assigned OD
routing, and whenever you need the actual route geometries
(route-alternatives export).


Related pages
-------------

- :doc:`k_alternatives` — the enumeration method this engine
  complements.
- :doc:`gravity_and_decay` — the shared trip-generation math.
- :doc:`../user_guide/run_flow` — running either engine.
