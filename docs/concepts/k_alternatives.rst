K-alternative paths (Plateau's method)
======================================

Real pedestrians don't always take the mathematically shortest route.
The corridor with more shops is a little longer but more pleasant. The
route through the park is longer still but more shaded. To model
pedestrian flow realistically, UNA needs to distribute each trip across
multiple *reasonable* alternative routes — not just the single
shortest one.

This page explains how UNA enumerates those alternatives, and how it
decides which alternatives are "close enough to shortest" to count.

.. note::

   Path enumeration is one of two flow engines, selected with
   ``flow_engine = "k_alternatives"``. Since 2.5.5 the *default* engine
   is ``aggregate_flow``, which distributes flow across the full detour
   envelope without enumeration — see :doc:`aggregate_flow`.

.. contents:: On this page
   :local:
   :depth: 1


Why not just the shortest path?
-------------------------------

If every trip took the single shortest network path, per-edge flow
would concentrate on a few "trunk" segments and be zero everywhere
else — a mathematically clean but observationally wrong picture.

Real pedestrian flow spreads across a corridor of viable routes. A
model that only counts the shortest cannot predict how flow
redistributes when one street is closed, how a second parallel
sidewalk absorbs foot traffic, or where new pedestrian-friendly
investment would create the largest marginal impact.

UNA's flow engine therefore enumerates *K alternative paths* between
each origin-destination pair and splits the trip volume across them.


The detour envelope — which alternatives count?
-----------------------------------------------

An "alternative" is any path from origin *o* to destination *d* whose
total cost stays within a **detour envelope** — a fixed budget above
the shortest path. Three envelope modes are available via
:py:data:`../user_guide/settings_reference:flow_detour_mode`:

.. list-table::
   :header-rows: 1
   :widths: 15 30 55

   * - Mode
     - Envelope
     - Meaning
   * - ``"ratio"``
     - ``shortest × flow_detour_ratio``
     - Proportional — 15 % detour allowed if
       ``flow_detour_ratio = 1.15``.
   * - ``"buffer"``
     - ``shortest + flow_detour_buffer``
     - Additive — up to ``flow_detour_buffer`` extra meters allowed.
   * - ``"min"``
     - ``min(ratio, buffer)``
     - Tighter of the two — recommended for pedestrians.

The ``"min"`` mode is the workhorse. On a very short trip (say 200 m),
a 15 % ratio would only permit 30 m of detour — too tight — so the
buffer (100 m) kicks in. On a long trip (say 2 km), a 100 m buffer
would only allow 5 % detour — too tight — so the ratio (15 %)
kicks in.


Enumerating alternatives — Plateau's penalty method
---------------------------------------------------

Finding *K* good alternative paths is a classical problem. UNA uses a
penalty-based method attributed to Plateau: run Dijkstra to get the
shortest path, then *penalize* the edges the shortest path used and
run Dijkstra again to find a route that avoids them. Repeat *K*
times.

Concretely, per origin-destination pair:

1. Run Dijkstra on the base weights → path :math:`P_1` (the shortest).
2. Multiply every edge in :math:`P_1` by
   :py:data:`../user_guide/settings_reference:flow_alternative_penalty_factor`.
3. Run Dijkstra on the penalized weights → path :math:`P_2`.
4. Multiply every edge in :math:`P_2` (from either iteration) by the
   penalty factor again.
5. Repeat until you have *K* alternatives or the next one exceeds the
   detour envelope.

**Why this works.** After each iteration, the edges recently used are
more expensive, so the next Dijkstra prefers unused parallel corridors.
The result is a diverse set of paths that mostly avoid each other, all
within the detour budget.


Choosing K and the penalty factor
---------------------------------

Two knobs shape how many and how diverse the alternatives are:

**flow_n_alternatives** —
:py:data:`../user_guide/settings_reference:flow_n_alternatives` — the
maximum number of alternatives to enumerate per OD pair. Higher
values discover more back-alley routes at the cost of runtime.

- ``10`` — a lean default. Fine for iteration and for OD pairs where
  a few obvious corridors exist.
- ``30``–``50`` — richer flow patterns on complex urban grids where
  multiple parallel routes exist within the detour envelope.
- Above 100 — diminishing returns; most OD pairs run out of usable
  alternatives well before this.

**flow_alternative_penalty_factor** —
:py:data:`../user_guide/settings_reference:flow_alternative_penalty_factor` —
the multiplier applied to used edges. Must be ≥ 1.0.

- ``1.5`` — mild. Alternatives can share substantial portions of the
  shortest path.
- ``2.0`` — moderate (the default). Alternatives are mostly disjoint
  from the shortest, but may share short connector segments.
- ``5.0`` and up — aggressive. Alternatives are forced far away from
  earlier paths, often exhausting the detour envelope quickly.

Reasonable pedestrian defaults: ``flow_n_alternatives = 30``,
``flow_alternative_penalty_factor = 1.5``.


How trips split across alternatives
-----------------------------------

Once alternatives are enumerated, the total trip volume between *o*
and *d* is split across them. Three splitting rules are available via
:py:data:`../user_guide/settings_reference:flow_path_detour_penalty`:

- ``"equal"`` (the default) — every alternative gets an equal share.
  Simple, agnostic. Appropriate when you don't have a calibrated
  route-choice model.
- ``"exponential"`` — shorter alternatives get exponentially more
  volume than longer ones. Controlled by
  :py:data:`../user_guide/settings_reference:flow_route_enumeration_beta`.
- ``"logistic"`` — S-shape falloff of volume with path cost above
  shortest. Controlled by
  :py:data:`../user_guide/settings_reference:flow_route_enumeration_logistic_midpoint`.

For each alternative :math:`P_i` with cost :math:`c_i` and shortest-path
cost :math:`c_1`, exponential and logistic weights sit on top of these
formulas:

- Exponential: :math:`w_i \propto e^{-\beta_\text{route}(c_i - c_1)}`
- Logistic:    :math:`w_i \propto \frac{1}{1 + e^{k(c_i - c_1 - m)}}`

The normalization ensures every OD pair's trips sum to exactly the
Huff-model-determined total for that pair.


A worked example
----------------

Consider a single origin-destination pair with:

- Shortest path cost = 500 m
- ``flow_detour_ratio`` = 1.2 → envelope = 600 m
- ``flow_n_alternatives`` = 5
- ``flow_alternative_penalty_factor`` = 2.0

Plateau's method might produce:

+---------+-----------+---------------------------+
| Path    | Cost (m)  | Notes                     |
+=========+===========+===========================+
| P₁      | 500       | Shortest.                 |
+---------+-----------+---------------------------+
| P₂      | 540       | Parallel corridor.        |
+---------+-----------+---------------------------+
| P₃      | 580       | Back-alley route.         |
+---------+-----------+---------------------------+
| P₄      | ≥ 600     | Rejected — over envelope. |
+---------+-----------+---------------------------+

Only three paths fit in the envelope. With ``flow_path_detour_penalty =
"equal"``, the OD trip volume splits into three even shares — one-third
each. With ``"exponential"``, P₁ (shortest) gets the most, P₂ and P₃
progressively less.


Trade-offs and caveats
----------------------

**Alternatives take time.** Each additional *K* is another Dijkstra
run per origin. With a 20 k-building network at K=50, that's a
million Dijkstra invocations per flow analysis. Prototype with K=10
and turns off; commit to K=30-50 with turns on for the production
run.

**Diminishing returns beyond K=50.** Most OD pairs saturate long
before that — the detour envelope only contains so many meaningfully
distinct routes.

**Penalty factor interacts with detour ratio.** A high penalty factor
combined with a tight detour ratio can exhaust the envelope in 3–5
iterations. If you're seeing significantly fewer alternatives than
``flow_n_alternatives``, loosen one or the other.

**Alternative enumeration is not per-mode.** UNA applies the same
alternative-search parameters to every OD pair regardless of trip
length. If short trips need fewer alternatives than long trips, the
``"min"`` detour mode is the main way to express that.


Related pages
-------------

- :doc:`../user_guide/run_flow` — the ``RunFlow()`` method reference.
- :doc:`../tutorials/tutorial_3_flow` — the ten-step flow walkthrough
  where step (d) demonstrates alternatives explicitly.
- :doc:`../user_guide/settings_reference` — the parameter descriptions.
