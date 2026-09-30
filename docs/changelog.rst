Changelog
=========

This page will track changes in future UNA releases.

.. rubric:: v2.7.0

- **Facility Allocation engine.** New ``RunFacilityAllocation()``
  finds optimal locations for new facilities among candidate sites —
  UNA's take on location-allocation. Two problem types:
  ``max_access`` (place *p* facilities to maximize demand-weighted
  access to the nearest open facility; ArcGIS's *Maximize
  Attendance*) and ``min_facilities`` (fewest facilities covering all
  coverable demand; ArcGIS's *Maximize Coverage + Minimize
  Facilities*). Pre-existing facilities are pinned open via a column
  on the candidates layer (``fa_existing_facilities_column``). Deterministic
  greedy solver (near-optimal, any scale) or exact MILP
  (``fa_solver="exact"``, scipy/HiGHS, with automatic greedy
  fallback). Full impedance support: custom edge costs, elevation,
  turns, obstacles; ``search_radius`` acts as the service cutoff and
  unservable demand is warned about and reported. Outputs join onto
  the candidates and demand layers plus a JSON summary, and the
  selected facilities chain directly into ``RunFlow()`` as a
  destinations layer. A third problem type, ``max_patronage``,
  maximizes total trips generated under the gravity-cap
  trip-generation model with Huff-split per-facility patronage
  (candidate attractiveness via ``destination_weight_column``, as in
  ``RunFlow()``'s Huff model) — roughly
  ArcGIS's *Maximize Market Share*. Batch support:
  ``RunBatch("facility_allocation", pairing_file=...)`` with
  composite output on ``fa_access`` / ``fa_covered``.
  See :doc:`user_guide/run_facility_allocation`.

.. rubric:: v2.6.0

- **Turn-aware aggregate flow.** The ``aggregate_flow`` engine now
  supports ``turns = True`` natively via a line-graph (arc-state)
  search space, sharing the k_alternatives turn parameters and
  conventions. The turn-free code path is unchanged.
- **Automatic percentile gravity caps.**
  ``flow_gravity_cap`` accepts percentile strings (``"p95"``,
  ``"p99"``, ``"max"``); ``RunFlow()`` derives the numeric cap from an
  internal gravity accessibility pass consistent with the run's own
  impedance settings, with a p95→p99→max fallback and full logging.
- **Legacy destination-level decay.**
  ``flow_decay_method = "destination_decay"`` reproduces the Madina
  package's per-destination trip-generation convention, for
  comparability with older results.
- **Network cost fallback.** NaN or non-positive values in a custom
  ``network_weight_column`` now fall back to geometric segment length,
  with a logged warning listing the offending rows.

.. rubric:: Current release

First public release of the UNA Python package. See the
:doc:`getting_started/first_analysis` guide to get started and the
:doc:`user_guide/settings_reference` for the full parameter list.
