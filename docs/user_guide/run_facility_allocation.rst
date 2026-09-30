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
  ``fa_required_column``. Truthy values (1 / TRUE / yes) mark
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


Solvers
-------

``fa_solver = "greedy"`` (default)
    Deterministic marginal-gain greedy. Both objectives are submodular,
    so greedy carries the classic :math:`(1 - 1/e)` near-optimality
    guarantee for ``max_access`` (and the standard set-cover guarantee
    for ``min_facilities``), and it scales to any problem size. The
    pick order is reported in the ``rank`` output column — facility 1
    is the single best site, facility 2 the best increment, and so on.

``fa_solver = "exact"``
    Optimal MILP via ``scipy.optimize.milp`` (HiGHS — already part of
    UNA's dependency set). Practical up to a few hundred candidates
    and ~3M demand-candidate pairs; beyond that, or if the solver
    fails or times out (600 s), the run **falls back to greedy with a
    logged warning** and records the fallback in the summary output.
    For exact runs the ``rank`` column is produced by re-ordering the
    optimal set greedily, so it stays meaningful.


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
   s.fa_required_column   = "existing"                     # column marking open libraries

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
``required``          1 = was a pre-existing facility
``rank``              0 = required; 1..k = pick order; -1 = not selected
``demand_served``     total demand weight assigned to this facility
``access_captured``   Σ demand weight × decay(distance) assigned here
===================  ======================================================

``<name>_facilities_selected.*`` — the same columns, filtered to only
the OPEN facilities (required + chosen). Maps the chosen configuration
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
