Gravity and decay models
========================

This page explains the math behind UNA's four accessibility metrics
(Reach, Gravity exponential, Gravity logistic, KNN) and the two flow
trip-generation aggregation methods (closest, gravity_cap). Everything
here is what runs under the hood when you call
:doc:`../user_guide/run_accessibility` or
:doc:`../user_guide/run_flow`.

.. contents:: On this page
   :local:
   :depth: 1


The basic setup
---------------

For every origin *o*, UNA runs a Dijkstra sweep on the network up to a
maximum cost of
:py:data:`../user_guide/settings_reference:search_radius`. This yields
a distance ``d(o, d)`` — network distance from origin *o* to
destination *d* — for every reachable destination.

Each destination *d* carries a weight ``w(d)`` from
:py:data:`../user_guide/settings_reference:destination_weight_column`
(or 1.0 if the sentinel ``"Count"`` is used). Each accessibility metric
below is a different function of these ``d(o, d)`` and ``w(d)`` values.


Reach
-----

Reach is the simplest: sum the weights of every destination within the
search radius.

.. math::

   \mathrm{Reach}(o) = \sum_{d \in D(o)} w(d)

where ``D(o)`` is the set of destinations reachable within
``search_radius`` from origin *o*.

With unit weights, Reach counts the number of reachable destinations —
a simple *cumulative opportunities* index. With attractiveness weights,
Reach becomes a weighted sum: a bus stop serving 100 daily departures
contributes 100 to Reach, a stop serving 10 contributes 10.

**When Reach is the right choice.** For simple communication ("how
many bus stops within 500 m") and for cases where you don't want
distance to shape the score at all — every destination inside the
radius counts equally.


Gravity — exponential decay
---------------------------

Gravity penalizes distance. Each destination contributes its weight
scaled by an exponentially decreasing factor:

.. math::

   \mathrm{Gravity}(o) = \sum_{d \in D(o)} w(d) \cdot e^{-\beta \cdot d(o, d)}

where β is
:py:data:`../user_guide/settings_reference:gravity_beta`. Because
``exp(-β·d)`` is between 0 and 1 for any positive distance, Gravity is
always less than or equal to Reach at the same origin.

**Half-distance.** The distance at which a destination contributes half
its raw weight — its "half-life" — is:

.. math::

   d_{1/2} = \frac{\ln 2}{\beta}

+---------+---------------------+
| β       | Half-distance       |
+=========+=====================+
| 0.001   | ~693 m (walking)    |
+---------+---------------------+
| 0.003   | ~231 m (short walk) |
+---------+---------------------+
| 0.0005  | ~1386 m (biking)    |
+---------+---------------------+

**When exponential is the right choice.** Classical gravity models;
when you have an empirically calibrated β from route-choice research
or from fitting to observed data; when the underlying behavior is
"any small distance still costs a bit."


Gravity — logistic decay
------------------------

Logistic decay stays near 1.0 for distances well below a midpoint, then
falls off rapidly past it — an S-shape. It's often a better fit for
walking behavior: close is close, far is far, with a fairly sharp
threshold between the two.

.. math::

   \mathrm{Gravity}_{\text{log}}(o) = \sum_{d \in D(o)} w(d) \cdot \frac{1}{1 + e^{k \cdot (d - m)}}

where *m* is
:py:data:`../user_guide/settings_reference:gravity_logistic_midpoint`
(the distance at which the decay factor is 0.5) and *k* is the
steepness parameter.

**The ln(99)/midpoint convention.** UNA auto-derives *k* from *m*
using the textbook convention:

.. math::

   k = \frac{\ln 99}{m}

which places the 1 %/99 % endpoints of the curve symmetrically at
distances of ``0`` and ``2m`` (with ``d = m`` at exactly 50 %).
Setting a midpoint of 500 m therefore gives a curve that starts near
1.0, drops to 0.5 at 500 m, and reaches nearly zero by 1000 m —
without you having to think about *k* at all.

**When logistic is the right choice.** When you know a plausible
"comfortable walking distance" for your context and want the model to
respect it as a threshold; when you want a curve that's more forgiving
of small distances than exponential; for pedestrian analyses in
general.


KNN — the k nearest matter
--------------------------

KNN treats "how many opportunities within the radius" differently: it
looks at only the *k* nearest reachable destinations and weights each
by both a per-neighbor weight and a decay factor.

.. math::

   \mathrm{KNN}(o) = \sum_{i=1}^{k} c_i \cdot w(d_i) \cdot \text{decay}\big(d(o, d_i)\big)

where ``d_1, d_2, …, d_k`` are the *k* nearest destinations sorted by
network distance, ``c_i`` is the *i*-th value of
:py:data:`../user_guide/settings_reference:knn_weights` (the tuple),
and the decay function is set by
:py:data:`../user_guide/settings_reference:knn_decay` (``"none"``,
``"exponential"``, or ``"logistic"``).

- ``knn_weights = (1.0,)`` → *k* = 1: only the nearest destination
  counts. WalkScore-style "distance to nearest pharmacy."
- ``knn_weights = (1.0, 1.0, 0.5)`` → *k* = 3: the three nearest count,
  with the third at half weight. A defensible "you generally need
  more than one option" formulation.
- ``knn_weights = (0.04,) * 25`` → *k* = 25 each at weight 0.04.
  Useful for job-access studies where "diversity of opportunities
  within reach" matters more than "the very nearest job."

**When KNN is the right choice.** WalkScore-style composite
accessibility across many destination categories; studies where "only
a few destinations really matter" (nearest hospital, nearest
grocery store); when you want to separate the "how many" question
from the "how attractive is each" question.


Plateau — a no-decay zone
-------------------------

For all three decay curves, you can optionally set a *plateau* —
a "flat" zone around each origin inside which no decay is applied.
Controlled by
:py:data:`../user_guide/settings_reference:gravity_plateau`.

Mathematically:

.. math::

   d_{\text{effective}} = \max\big(0, d(o, d) - p\big)

where *p* is the plateau distance. Every destination within *p* of the
origin contributes at its full raw weight; only destinations beyond *p*
see decay.

**Use case.** When you want to treat all very-nearby destinations as
equally attractive (an origin at the center of a shopping district
shouldn't discriminate between three equally-close shops), or when
your data has snap distances that shouldn't count as travel effort.

Default is 0 — decay starts at zero distance.


Flow trip generation
--------------------

The Flow engine uses the same decay math to shape *trip generation*
— how many trips each origin actually emits — not just how each
origin's score is computed.

Setting :py:data:`../user_guide/settings_reference:flow_decay` = True
enables trip-generation decay. Then
:py:data:`../user_guide/settings_reference:flow_decay_method` selects
one of two aggregation schemes.

Closest — parameter-free
~~~~~~~~~~~~~~~~~~~~~~~~

.. math::

   \text{factor}(o) = \text{decay}\big(\min_{d \in D(o)} d(o, d)\big)

The trip-generation factor is evaluated at the distance to the origin's
*nearest* destination. If the nearest is close, the factor is near 1
(full trip generation). If the nearest is far, the factor is small
(low trip generation).

**Why closest is monotonic and simple.** Adding a farther destination
never changes the factor (since it doesn't change the *nearest*).
Adding a closer destination always increases the factor. No
calibration knob is needed beyond the decay curve parameters — the
factor is fully determined by ``gravity_beta`` (for exponential) or
``gravity_logistic_midpoint`` (for logistic).

**When closest is the right choice.** Most cases. This is the
recommended default. Cheap, monotonic, easy to reason about.

Gravity cap — density-aware
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::

   \text{factor}(o) = \min\!\left(1, \frac{\sum_{d \in D(o)} w(d) \cdot \text{decay}(d(o, d))}{G_{\text{cap}}}\right)

where ``G_cap`` is
:py:data:`../user_guide/settings_reference:flow_gravity_cap`. The
numerator is the Huff-style gravity sum — sum of ``weight × decay``
across all reachable destinations. When that sum exceeds ``G_cap``, the
factor saturates at 1.

**Why gravity_cap captures density.** An origin with three nearby
destinations reaches saturation faster than an origin with only one.
Both may reach the factor = 1 plateau if their destinations are close
enough or numerous enough, but destinations-poor origins never do.

**When gravity_cap is the right choice.** When "how many nearby
options" is the right proxy for trip generation intensity — think
"more nearby shops means more trips generated by a residential
building." Requires calibration of ``G_cap`` against your
destination-weight scale.


Huff destination choice
-----------------------

Once total trip generation for an origin is determined, the Flow
engine splits those trips across reachable destinations using a
Huff-style gravity model:

.. math::

   P(d \mid o) = \frac{w(d) \cdot \text{decay}(d(o, d))}{\sum_{d' \in D(o)} w(d') \cdot \text{decay}(d(o, d'))}

Enabled when
:py:data:`../user_guide/settings_reference:flow_destination_weights` =
True. The share of trips going to destination *d* is proportional to
its weight-times-decay value, normalized across all destinations.

When ``flow_destination_weights = False``, trips split uniformly across
reachable destinations regardless of attractiveness — every reachable
destination gets 1/n of the total.


Choosing a decay model
----------------------

**Quick heuristics:**

- Studying "cumulative opportunities" → **Reach**.
- Studying activity access with a smooth distance penalty → **Gravity
  exponential** (with a calibrated β).
- Studying pedestrian activity with a comfortable-walking-distance
  threshold → **Gravity logistic** (with a plausible midpoint).
- Studying WalkScore-style composites across many destination
  categories → **KNN** (usually logistic decay).
- Modeling flow trip generation for the first time → **flow_decay =
  True, method = "closest"**.
- Modeling flow with a density-based generation model → **method =
  "gravity_cap"**, calibrate cap against destination weights.


Related pages
-------------

- :doc:`../user_guide/run_accessibility` — how to compute Reach,
  Gravity, and KNN.
- :doc:`../user_guide/run_flow` — how flow trip generation and
  destination choice fit together.
- :doc:`../tutorials/tutorial_2_accessibility` — worked examples of
  each metric on the Boston tutorial data.
- :doc:`../user_guide/settings_reference` — the parameter descriptions.
