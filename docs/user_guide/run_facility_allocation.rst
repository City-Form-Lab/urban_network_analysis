RunFacilityAllocation()
=======================

``RunFacilityAllocation()`` finds the best locations for new
facilities among a set of candidate sites — UNA's take on what ArcGIS
and the operations-research literature call *location-allocation*.
Given demand points, candidate facility locations, and optionally a
set of facilities that already exist, it chooses which candidates to
open and reports which facility each demand point would use.

All travel is evaluated **towards the facility** over the network,
with the same impedance machinery as every other UNA engine: custom
edge costs (``network_weight_column``), uphill elevation penalties
(``elevation``), turn penalties (``turns``), and obstacle points all
apply. ``search_radius`` is the **service cutoff** — demand beyond it
cannot be served by any facility and is reported as uncovered.


.. note::

   **For UNA Rhino toolbox users.** This engine descends from two
   Rhino toolbox tools. An *evaluation run* of ``max_access``
   (``fa_new_facilities = 0``) corresponds to the Rhino **Closest
   Facility** tool: each origin is allocated once to its nearest
   facility within the radius, and the per-facility outputs match —
   ``demand_served`` is the Reach summarized at the facility,
   ``access_captured`` the Gravity (exponential decay). An evaluation
   run of ``max_patronage`` corresponds to the Rhino **Find
   Patronage** tool's Huff model (Huff 1963; Sevtsuk & Kalvo 2017),
   with two differences: trip generation here is *elastic* — origins
   generate trips per the gravity-cap participation model rather than
   each emitting one trip that is merely split — and attractiveness
   enters linearly (the Rhino alpha exponent is fixed at 1; apply a
   power transform to the weight column for other alphas). What is
   new relative to the Rhino toolbox is the optimization layer:
   choosing *which* facilities to open (``fa_new_facilities`` ≥ 1,
   ``min_facilities``, the greedy/exact solvers, and the marginal-gain
   ranking).

Inputs
------

Demand, candidates, and existing facilities arrive through the layers
you already know:

- **Demand = the origins layer.** ``origins_file`` with
  ``origin_weight_column`` (population, households, students, …).
  Unit weights are used when the column is absent or
  ``flow_origin_weights = False``.
- **Candidates = the destinations layer.** ``destinations_file``, one
  point per potential site.
- **Existing facilities = a column on the candidates layer**, named by
  ``fa_existing_facilities_column``. Truthy values (1 / TRUE / yes) mark
  facilities that are already in operation — they are always kept open
  and the optimizer sites new facilities *around* them: a candidate
  next to an existing facility scores a low marginal gain because that
  demand is already served.

``destination_weight_column`` is ignored by ``max_access`` and
``min_facilities``. In ``max_patronage`` it supplies facility
**attractiveness** — the same role destination weights play in
``RunFlow()``'s Huff model (hypothesized sizes for proposed sites,
measured sizes for existing ones; unit values when unset).


Problem types
-------------

``fa_problem_type = "max_access"`` (default)
    Open ``fa_new_facilities`` additional facilities so that total
    demand-weighted access to the nearest open facility is maximized:

    .. math::

       \max \;\; \sum_i w_i \cdot f(d_i^*)

    where :math:`d_i^*` is demand point *i*'s network distance to its
    nearest open facility and :math:`f` is the distance-decay function.
    The decay mirrors the flow engines' ``flow_decay_method="closest"``
    trip-generation factor exactly (``flow_decay``, ``flow_decay_curve``,
    ``gravity_beta``; the logistic curve uses midpoint
    ``search_radius/2``). With ``flow_decay = False`` the factor is 1
    for any reachable facility, so the objective becomes **covered
    demand** — pure coverage maximization. Corresponds to ArcGIS's
    *Maximize Attendance*.

``fa_problem_type = "min_facilities"``
    Open as **few** facilities as possible while covering every demand
    point that can be covered within the cutoff. The facility count is
    the *output*; ``fa_new_facilities`` is ignored. Demand with no
    candidate in range is reported as unservable (with a warning) and
    excluded. Corresponds to ArcGIS's *Maximize Coverage + Minimize
    Facilities*.

``fa_problem_type = "max_patronage"``
    Open ``fa_new_facilities`` additional facilities to maximize the
    **total trips generated**, under the same gravity-cap
    trip-generation model the flow engines use
    (:doc:`../concepts/gravity_and_decay`):

    .. math::

       \max \;\; \sum_i w_i \cdot \min\!\left(1,\;
           \frac{\sum_{j \in \text{open}} g_{ij}}{\text{cap}}\right),
       \qquad g_{ij} = A_j \cdot f(d_{ij})

    where :math:`A_j` is candidate attractiveness
    (``destination_weight_column`` on the candidates layer —
    hypothesized size for proposed sites, measured size for existing
    ones; unit values when unset — exactly the role destination
    weights play in ``RunFlow()``'s Huff model) and cap is
    ``flow_gravity_cap`` (**numeric
    required** — derive a percentile value from a ``RunFlow`` or
    accessibility run first). Trips are split among open facilities by
    Huff share, so the per-facility outputs show *patronage including
    cannibalization*: opening a site near an existing facility visibly
    reduces the existing facility's numbers. Unlike ``max_access``,
    this objective rewards **doubling up** on demand that a single
    facility only partly activates — with an unsaturated cap, a second
    facility near heavy demand can beat a first facility near light
    demand. Roughly corresponds to ArcGIS's *Maximize Market Share*.
    Greedy solver only (the saturating objective does not linearize;
    it is submodular, so greedy keeps its guarantee).


.. note::

   **How demand is allocated — the key behavioral difference.**
   ``max_access`` (and ``min_facilities``) use *all-or-nothing nearest
   assignment*: each demand point gives its entire weight to the single
   open facility with the best decay value — effectively the nearest by
   network impedance, since destination weights are ignored in these
   modes — and exactly zero to every other facility, even one a few
   meters farther. A facility that is second-nearest to everyone
   captures nothing. ``max_patronage`` instead allocates each demand
   point *fractionally* across all open facilities in reach, by Huff
   shares proportional to attractiveness × decay — so a larger
   facility slightly farther away receives a larger share and can even
   be a demand point's primary destination. Neither mode ever
   double-counts: in both, the per-facility columns partition the
   demand, and their sums equal the run totals. Choose by how users of
   the facility type behave: strictly-nearest services (elementary
   schools, polling places) → ``max_access``; destinations people
   split their visits among, where size matters (libraries, retail,
   parks) → ``max_patronage``.

.. tip::

   The default ``fa_new_facilities = 0`` (with
   ``fa_existing_facilities_column`` set) makes either max mode a pure
   **evaluation run**: no siting —
   demand is allocated to the existing facilities as they stand. In
   ``max_patronage`` this computes baseline patronage of the current
   configuration, the natural "before" to compare any siting scenario
   against.


Solvers
-------

The two solvers differ in *how they choose the set of facilities* —
a classic speed-versus-guarantee trade.

``fa_solver = "greedy"`` (default)
    Builds the answer one facility at a time: open the candidate that
    adds the most, lock it in, repeat — never reconsidering earlier
    picks. Fast at any problem size, deterministic, and the pick order
    comes out as the ``rank`` column: facility 1 is the best single
    site, facility 2 the best *addition* given facility 1, and so on
    (so one run at p = 5 contains the nested answers for p = 1…4).
    The catch: it can miss combinations that only work *together* —
    two individually mediocre sites flanking a demand cluster that no
    single site covers well will never be opened, because each looks
    weak alone. Theory bounds the damage (the objectives are
    submodular, giving the classic :math:`(1 - 1/e) \approx 63\%`
    worst-case guarantee for ``max_access``); in practice on real
    geographic data greedy is typically within a percent or two of
    optimal, and often exactly optimal.

``fa_solver = "exact"``
    Considers all facility combinations *jointly* (a mixed-integer
    program solved by HiGHS via ``scipy.optimize.milp`` — already in
    UNA's dependency set) and returns a **provably optimal** set,
    interactions included. The costs: runtime grows steeply with
    problem size — practical up to a few hundred candidates and ~3M
    demand-candidate pairs; beyond that, or if the solver fails or
    times out (600 s), the run **falls back to greedy with a logged
    warning** and records the fallback in the summary output. Exact
    answers arrive as an unordered set, so the ``rank`` column is
    reconstructed by greedily re-ordering the optimal set — readable,
    but not part of the optimality proof.

    Not available for ``max_patronage`` — its saturating
    trip-generation objective does not fit the linear form the MILP
    needs, so that problem type **always uses greedy** (with the same
    near-optimality guarantee).

**Which to use:** greedy for exploration, large cases, and whenever
the incremental "which site first?" ranking is the story; exact when
the facility count is small and the decision warrants "provably best"
(e.g. a formal siting recommendation). Running both and seeing them
agree is itself a useful robustness check.


Example
-------

.. code-block:: python

   import urban_network_analysis as una

   project = una.UNA()
   s = project.settings

   s.data_folder          = "Portland"
   s.network_file         = "sidewalks_3D.geojson"
   s.origins_file         = "building_centroids.geojson"   # demand
   s.origin_weight_column = "pop2020"
   s.destinations_file    = "library_sites.geojson"        # candidates
   s.fa_existing_facilities_column   = "existing"                     # column marking open libraries

   s.search_radius        = 800          # service cutoff
   s.elevation            = True
   s.flow_decay           = True
   s.flow_decay_curve     = "exponential"
   s.gravity_beta         = 0.002

   s.fa_problem_type      = "max_access"
   s.fa_new_facilities    = 2
   s.fa_solver            = "greedy"

   project.RunFacilityAllocation()


Outputs
-------

Written to ``output_folder`` per the ``output_*`` format flags:

``<name>_facilities.*`` — the candidates layer with:

===================  ======================================================
``selected``          1 = open in the chosen configuration
``existing``          1 = was a pre-existing facility
``rank``              0 = existing; 1..k = pick order; -1 = not selected
``demand_served``     total demand weight assigned to this facility
                      (Huff-split fractions in ``max_patronage``)
``access_captured``   Σ demand weight × decay(distance) assigned here.
                      In ``max_patronage`` runs this column is named
                      ``patronage`` instead — the estimated trips
                      landing at each facility (Huff split; sums to the
                      summary's ``total_trips``, and existing
                      facilities' values reveal cannibalization).
===================  ======================================================

.. note::

   **Reading** ``demand_served`` **vs** ``patronage`` (``max_patronage``
   runs). ``demand_served`` is the facility's *catchment pool*: each
   demand point's weight, divided among open facilities by Huff share —
   who the facility draws from, in population terms, before asking
   whether they travel. ``patronage`` applies each demand point's
   trip-generation factor ``min(1, G_i/cap)`` to that pool: the
   *estimated visits actually arriving*. The gap between the two is
   unrealized demand — a facility with ``demand_served = 1000`` but
   ``patronage = 400`` has a catchment whose overall accessibility is
   too weak to activate most trips; where the catchment is saturated
   (``G ≥ cap`` for everyone) the columns are equal. The ratio
   ``patronage / demand_served`` is thus a per-facility *activation
   rate* — low values flag places where an additional nearby facility
   would unlock further trips. The binary "has any access at all" lives
   on the demand layer instead (``covered``), and in patronage runs the
   demand layer's ``access`` column holds the trip-generation factor.

``<name>_facilities_selected.*`` — the same columns, filtered to only
the OPEN facilities (existing + chosen). Maps the chosen configuration
directly — no filter step — and is directly usable as a
``destinations_file`` for a follow-up ``RunFlow()``. The full
``_facilities`` table remains the analytic record, including the
evaluated-but-rejected candidates.

``<name>_demand.*`` — the origins layer with:

======================  ===================================================
``assigned_facility``    uid of the facility this demand point uses
``distance``             network cost to it (NaN if uncovered)
``access``               decay(distance) (0 if uncovered)
``covered``              1/0
======================  ===================================================

``<name>_summary.json`` — problem type, solver actually used, objective
value, facility counts, and covered/uncovered demand totals.


Chaining into a flow analysis
-----------------------------

The selected facilities are just a destinations layer, so the chosen
configuration can be fed straight back into :doc:`run_flow` to
estimate the street-level pedestrian flows it would generate — siting
and footfall from the same impedance model:

.. code-block:: python

   import geopandas as gpd

   # Take the selection straight from the engine's in-memory results
   # (timestamp-proof — no need to locate the run's output subfolder).
   e    = project.facility_allocation
   dest = project.topology.destinations
   fac  = gpd.GeoDataFrame(
       {"uid": list(dest.uid), "selected": e.fa_selected},
       geometry=gpd.GeoSeries(dest.geometry).reset_index(drop=True),
       crs=getattr(dest.geometry, "crs", None),
   )
   fac[fac["selected"] == 1].to_file("Portland/chosen_libraries.geojson",
                                     driver="GeoJSON")

   s.destinations_file = "chosen_libraries.geojson"
   s.flow_engine       = "aggregate_flow"
   s.flow_decay        = True
   s.flow_decay_method = "closest"
   project.RunFlow()

A ready-to-edit driver covering the full workflow is in
``examples/UNA_FacilityAllocation.py``.


Batch runs
----------

``RunBatch("facility_allocation", pairing_file=...)`` runs one
facility-allocation analysis per pairing-table row — the ``fa_*``
fields are ordinary Settings columns, so scenario sweeps (different
cutoffs, facility counts, problem types, candidate layers) are one
CSV. Rows with ``batch_composite_output = TRUE`` and
``batch_composite_result_column = "fa_access"`` (or ``"fa_covered"``)
also merge their per-demand results into one composite file joined on
the shared demand layer, exactly like accessibility composites — handy
for comparing, say, access under 1 vs. 2 vs. 3 new facilities side by
side in QGIS.


Notes and conventions
---------------------

- **Ties and determinism.** Every solver path is deterministic; on
  equal marginal gain the lower candidate index wins, and a demand
  point with equal access to two open facilities is assigned to the
  nearer one.
- **Turn-aware runs** inherit the flow engines' turn conventions:
  transitions through an origin's or destination's connector arcs are
  never charged a turn, so a turn onto the facility's own snap edge is
  free — consistent with :doc:`../concepts/elevation_turns`.
- **Scenario comparisons.** To compare "2 new libraries" against "4
  new libraries", run twice and diff the ``_demand`` outputs; the
  ``rank`` column of a single larger run already contains the nested
  answer for every smaller ``fa_new_facilities`` under greedy.
