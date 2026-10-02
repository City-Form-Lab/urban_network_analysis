Tutorial 5 — Facility allocation and patronage estimation in Cambridge
======================================================================

This tutorial examines the ``una.RunFacilityAllocation()`` engine —
UNA's tool for choosing the best locations for new facilities among
candidate sites, and for estimating the patronage that facilities
(existing or proposed) draw from surrounding demand. It corresponds to
what ArcGIS Network Analyst calls *location-allocation*, with UNA's
problem types mapping to ESRI's Maximize Attendance (``max_access``),
Maximize Coverage + Minimize Facilities (``min_facilities``), and,
approximately, Maximize Market Share (``max_patronage``). All travel is
computed over the pedestrian network, towards the facility, with the
same impedance machinery as every other UNA engine.

**Scenario.** The City of Cambridge wants to open after-school
community learning centers, hosted in existing school buildings. Three
schools already run such programs — King Open School, Peabody
Elementary School, and Cambridge Rindge & Latin — and the city asks:
how well do these three serve residents today, where should additional
centers go, how many would it take to serve everyone, and how many
users can each location expect? Demand is the residential population
of every building; the service cutoff is an 800 m walk.

**Lineage.** Users of the UNA Rhino toolbox will recognize this
engine's ancestry: an evaluation run of ``max_access`` corresponds to
the Rhino Closest Facility tool (each origin allocated once to its
nearest facility; ``demand_served`` = Reach at the facility,
``access_captured`` = Gravity), and ``max_patronage`` descends from the
Find Patronage tool's Huff model (Huff 1963; Sevtsuk & Kalvo 2017). One
convention differs: the Rhino tool either split one inelastic trip per
origin (Decay=Off) or decayed each destination's trips individually
(Decay=On — preserved in the flow engines as
``flow_decay_method="destination_decay"``); ``max_patronage`` instead
uses the monotonic gravity-cap participation model, which a siting
objective requires. New here is the optimization layer: choosing which
facilities to open.

Data files (in ``docs/Boston/`` in the UNA repository):

- **Network:** ``20260703_PercLenNetwork_InnerCore.geojson`` — 69,957
  pedestrian edges (as in Tutorials 2–4).
- **Demand (origins):**
  ``Cambridge_building_centroids_pop2020.geojson`` — 14,751 buildings;
  column ``pop2020`` holds 118,705 residents in total (many
  non-residential buildings carry 0).
- **Candidates (destinations):**
  ``Cambridge_school_candidates.geojson`` — 60 school locations with
  columns ``name``, ``employees`` (staff size), ``existing`` (1 for
  the three schools already operating a center, 0 otherwise), and
  ``attract`` (employees ÷ their mean — the normalized attractiveness
  used in step h).

.. note::

   The candidates layer illustrates the general input convention:
   demand comes in as the origins layer, candidate facilities as the
   destinations layer, and already-operating facilities are marked by
   1/TRUE/yes values in the column named by
   ``fa_existing_facilities_column``. Existing facilities are always
   kept open — the optimizer sites new facilities around them.

.. contents:: On this page
   :local:
   :depth: 1


(a) Baseline — how well do the three existing centers serve Cambridge?
----------------------------------------------------------------------

With the default ``fa_new_facilities = 0``, the engine performs an
*evaluation run*: no siting, just the allocation of demand to the
existing facilities as they stand. Access is measured with exponential
distance decay (β = 0.002 — the decay factor falls to about 0.20 at
the 800 m cutoff).

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder          = r"Boston"
   una.settings.network_file         = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file         = "Cambridge_building_centroids_pop2020.geojson"
   una.settings.origin_weight_column = "pop2020"
   una.settings.destinations_file    = "Cambridge_school_candidates.geojson"
   una.settings.fa_existing_facilities_column = "existing"

   una.settings.search_radius    = 800          # service cutoff (meters)
   una.settings.flow_decay       = True
   una.settings.flow_decay_curve = "exponential"
   una.settings.gravity_beta     = 0.002

   # Both lines below are the defaults, written out for clarity:
   una.settings.fa_problem_type   = "max_access"
   una.settings.fa_new_facilities = 0            # evaluation run - no new siting

   una.RunFacilityAllocation()

.. note::

   Because this run uses ``max_access``, the facilities output reports
   decay-weighted ``access_captured`` — assuming that everyone visits
   their nearest available destination, how much demand would accrue
   to each destination facility, with the given distance decay
   applied. This is different from expected patronage estimated with
   the Huff probability model. Patronage estimation requires the
   ``max_patronage`` problem type and its trip-generation cap,
   introduced in steps (e)–(f), where this same baseline is
   re-evaluated in trip units.

The run writes three outputs: ``Results_facilities.*`` (all 60
candidates with selection columns), ``Results_facilities_selected.*``
(only the open facilities — drag this into QGIS to map the
configuration), and ``Results_demand.*`` (every building with its
assigned facility, network distance, access value, and covered flag),
plus a ``Results_summary.json``. The ``demand_served`` column in the
``Results_facilities.*`` file shows that only 24.6% of Cambridge
residents (29,189 of 118,705 total in Cambridge) live within the 800 m
cutoff of an existing center. The ``access_captured`` column in the
``Results_facilities.*`` file shows that of the 11,233 residents for
whom the Cambridge Rindge & Latin School is the nearest existing
facility, only 3,986 would be captured by that facility, with the
difference lost due to the distance decay that lowers the likelihood
of farther residents to actually walk to the facility.

.. note::

   The "Results" stem of these file names comes from
   ``una.settings.output_file_name`` (default "Results"). Setting a
   distinct name per run — e.g. "baseline", "plus3" — keeps the
   outputs of this tutorial's successive steps apart.

.. list-table::
   :header-rows: 1
   :widths: 40 30 30

   * - Existing center
     - demand_served (residents)
     - access_captured
   * - Cambridge Rindge & Latin
     - 11,233
     - 3,986
   * - King Open School
     - 10,163
     - 3,855
   * - Peabody Elementary School
     - 7,793
     - 2,725

.. figure:: /_static/tutorials/t5_baseline_coverage.png
   :width: 90%

Demand layer colored by ``covered``, with the three existing centers
symbolized. Uncovered areas concentrate in East Cambridge,
Cambridgeport, and North/West Cambridge.


(b) Site three new centers — max_access, greedy
-----------------------------------------------

If we wanted to site three additional centers, what would be the best
locations for them so that residents' decay-weighted access to their
nearest open center is maximized? Only two lines change:

.. code-block:: python

   una.settings.fa_problem_type   = "max_access"   # (default)
   una.settings.fa_new_facilities = 3
   una.RunFacilityAllocation()

The greedy solver adds facilities one at a time, each pick maximizing
the marginal gain given everything already open: pick 1 = James F Farr
Academy (East Cambridge), pick 2 = Graham & Parks Elementary (West
Cambridge), pick 3 = Martin Luther King After School (Cambridgeport).
The total ``access_captured`` rises from 10,566 to 24,442 (+131%) and
``demand_served`` from 24.6% to 53.4% of Cambridge residents. Note
that even though the Martin Luther King and James F. Farr Academy
800 m walksheds overlap due to the proximity of the two facilities,
``max_access`` assigns every demand point to its closest facility,
effectively shrinking the catchment areas to points that are truly
nearest to them. The ``rank`` column records the pick order — and
because greedy picks are nested, this single run also answers the
p = 1 and p = 2 questions: the best single addition is Farr Academy
alone, followed by Graham & Parks Elementary.

.. figure:: /_static/tutorials/t5_max_access_picks.png
   :width: 90%

Selected facilities (pick order 1–3) alongside the existing centers;
demand points colored by their assigned facility.


(c) Verify with the exact solver
--------------------------------

.. code-block:: python

   una.settings.fa_solver = "exact"
   una.RunFacilityAllocation()

The exact solver formulates the whole problem as a mixed-integer
program (HiGHS via scipy) and proves its answer optimal. Here it
returns the same objective, 24,442 — greedy was already optimal — but
names two different facilities. Look closely: the schools layer
contains co-located duplicate points (two Peabody entries, two Martin
Luther King entries at the same addresses), so several
distinct-looking facility sets are exactly equivalent. Multiple optima
are common in siting problems; the objective value, not the name list,
is what the two solvers agree on.

.. note::

   How do Greedy and Exact solvers differ? **Greedy** opens the best
   facility first, locks it in, and repeats — fast at any scale,
   providing a near-optimal result with a proven worst-case bound, and
   its pick order is itself useful. The **Exact** solver weighs all
   combinations jointly and returns a provably best set, at a
   computational cost that grows steeply with problem size (with
   automatic fallback to greedy when the problem becomes too large).
   ``max_patronage`` (steps f–h) always uses greedy — its saturating
   objective does not fit the linear form the exact solver needs.


(d) How many centers are needed to serve everyone? — min_facilities
-------------------------------------------------------------------

Now, let's turn the question around: what is the *smallest* number of
centers needed to cover every resident within a maximum allowable walk
length? Coverage is a yes/no question, so distance decay is switched
off; ``fa_new_facilities`` is ignored — we cannot know in advance how
many facilities to ask for, this number itself is the answer to this
problem.

.. code-block:: python

   una.settings.fa_problem_type = "min_facilities"
   una.settings.flow_decay      = False
   una.settings.fa_solver       = "greedy"     # then repeat with "exact"
   una.RunFacilityAllocation()

Greedy needs 24 new centers (27 sites in total with the existing
three) to cover 85.7% of residents — everyone who lives within 800 m
of any school at all. The remaining 14.3% are unservable at this
cutoff from any candidate: since facility allocations are limited to
the candidate locations, no subset of the given candidates can reach
all residents in Cambridge within a maximum 800 m walk. The engine
reports this in a warning. The exact solver does one better: 23 new
centers achieve the same complete coverage — an example of how the
**exact** solver beats **greedy**, worth the extra compute when the
recommendation is for a real decision.

.. figure:: /_static/tutorials/t5_min_facilities_exact.png
   :width: 90%

The exact ``min_facilities`` solution. Red points are the 23 selected
facilities (plus the three existing centers); blue dots are served
buildings and gray dots are buildings that remain unserved — farther
than 800 m from every candidate site.


(e) From access to patronage — the trip-generation model
---------------------------------------------------------

.. note::

   The two problem types also differ in HOW demand is allocated. In
   ``max_access``, each building gives its entire demand (population
   here) to the single most accessible open center — effectively the
   nearest by network distance, since destination weights are ignored
   in that mode — and exactly zero to every other, even one that is
   just a few meters farther; a center that is second-nearest to
   everyone captures nothing. In ``max_patronage``, demand from each
   building is split fractionally across all open centers in reach, by
   Huff shares proportional to destination attractiveness × distance
   decay — a larger center slightly farther away receives a larger
   share, and can even be a building's primary destination. Neither
   mode double-counts: the per-facility columns always partition the
   demand and facility allocations sum to the given demand totals.
   Choose by behavior: strictly-nearest services (elementary schools,
   polling places) fit ``max_access``; destinations people split
   visits among, where size matters (libraries, retail, parks), fit
   ``max_patronage``.

Access scores rank locations, but a planner budgeting staff wants
expected users. UNA's ``max_patronage`` problem type uses the same
gravity-cap trip-generation model as the flow engines (Tutorial 3):
each building generates trips in proportion to ``min(1, G/cap)``,
where G is its total gravity to all open facilities — participation
grows with accessibility until it saturates at the cap — and trips
split among open facilities by Huff shares. This tutorial uses the
simplest defensible cap: **cap = 1**. With unit attractiveness, a
building's gravity to a single center is just its decay value, which
equals 1 only at distance zero — so cap = 1 says full participation
requires standing at the facility's door, and everyone else
participates with probability equal to their decayed accessibility.
(Calibrated studies replace 1 with an empirically derived cap — see
the gravity-cap conventions in :doc:`../concepts/gravity_and_decay` —
but the cap must then be pinned across all compared scenarios, and the
logic below is unchanged.)

One empirical remark grounds the door-logic here: across Cambridge,
the largest baseline gravity to an existing center is 0.855 — no
building stands at a center's door, so at cap = 1 nobody participates
fully in the baseline.


(f) Baseline patronage of the three existing centers
-----------------------------------------------------

.. code-block:: python

   una.settings.fa_problem_type   = "max_patronage"
   una.settings.fa_new_facilities = 0        # evaluation run, examine only existing
   una.settings.flow_gravity_cap  = 1.0
   una.settings.flow_decay        = True
   una.RunFacilityAllocation()

The three existing centers can expect roughly 10,566 user-trips in
total — and that number should look familiar: it is exactly run (a)'s
objective. With cap = 1 and non-overlapping catchments, each
building's participation factor is ``min(1, decay/1) = decay`` itself,
so ``patronage`` reproduces ``access_captured`` from (a) exactly for
each facility. The two problem types measure the same thing in this
case — the comparison below is the proof, and the divergence begins
only in step (g), when catchments start to overlap.

.. list-table::
   :header-rows: 1
   :widths: 40 30 30

   * - Existing center
     - demand_served
     - patronage (trips)
   * - Cambridge Rindge & Latin
     - 11,233
     - 3,986
   * - King Open School
     - 10,163
     - 3,855
   * - Peabody Elementary School
     - 7,793
     - 2,725

.. note::

   Where does the decay act? Only once, in both modes. In
   ``max_access``, an origin's value is decay(d) for trips assigned
   strictly to their single nearest center (the "closest" convention).
   In ``max_patronage``, the same decay terms are summed over ALL
   reachable centers and passed through ``min(1, G/cap)``; generated
   trips are then allocated in full — no second decay is applied. At
   cap = 1, a single reachable center gives factor = decay exactly,
   which is why (f) equals (a). The models part ways only where
   catchments overlap: an origin that can reach two centers sums both
   decays — something ``max_access`` never does, since it only looks
   at the nearest destination — so additional nearby capacity raises
   trip generation with ``max_patronage`` and generates genuinely new
   trips. Step (g) illustrates this.


(g) Add three centers under the patronage objective
---------------------------------------------------

.. code-block:: python

   una.settings.fa_new_facilities = 3
   una.RunFacilityAllocation()

Total expected trips rise from 10,566 to 28,376 (+169%). Picks 1 and 2
are familiar from the access run (James F. Farr Academy, Martin Luther
King School), but the third pick changes: instead of Graham & Parks in
the west, the patronage objective chooses *Violeta Montessori*, close
to already-served central neighborhoods. This demonstrates the
doubling-up mechanism mentioned above: residents whom one center only
half-activates are worth reinforcing — a second nearby center sums
into their gravity score and lifts their participation toward 1 — and
that can beat reaching a whole new, but less accessible center (which
is all that the ``max_access`` solver values). Compare the two runs'
maps to see how the ``max_access`` and ``max_patronage`` logics
disagree.

The outputs also quantify how the opening of new centers can reduce
patronage at existing centers: King Open's catchment pool shrinks from
10,163 to 7,184 residents (and Rindge's from 11,233 to 9,297) as the
new centers split their shares — visible directly by comparing
``demand_served`` between runs (f) and (g). Note their patronage does
NOT fall: the pooled residents now participate more because their
demand is elastic with respect to destination access — better access
increases demand, offsetting the smaller shares.

.. figure:: /_static/tutorials/t5_patronage_config.png
   :width: 90%

Sites chosen under the ``max_patronage`` objective: Violeta Montessori
joins Farr Academy and Martin Luther King, reinforcing the already
partly-served central neighborhoods.

.. figure:: /_static/tutorials/t5_access_config.png
   :width: 90%

For comparison, the ``max_access`` configuration from run (b): Graham
& Parks Elementary serves the west instead. The two objectives agree
on picks 1–2 and disagree on the third.


(h) Facility size as attractiveness
-----------------------------------

So far every candidate was equally attractive. In ``max_patronage``,
the destinations layer's weight column can also supply facility
attractiveness — the role destination weights play in the flow
engines' Huff destination choice model. But a caution first: the
cap = 1 door-logic BREAKS if raw weights that vary by destination are
used, because a building at a facility's door then has gravity equal
to the facility's weight, not 1 — with staff counts in the hundreds,
everyone near any school would saturate instantly and the model would
degenerate to coverage. The fix is to normalize attractiveness to a
mean of 1: the candidates layer's ``attract`` column is employees ÷
70.6 (the candidate mean), so Rindge & Latin carries 4.96, the largest
school 14.2, and cap = 1 now reads "as well-served as standing at the
door of an average-sized facility." The same units discipline applies
with calibrated caps: derive the cap under the same weighting you run
with. (Note that an equivalent alternative would be to keep the raw
employee weights and instead raise ``flow_gravity_cap`` to the average
staff count — the model is invariant to scaling the weights and the
cap together.)

.. code-block:: python

   una.settings.destination_weight_column = "attract"
   una.settings.flow_gravity_cap          = 1.0    # unchanged
   una.RunFacilityAllocation()

The picks change again — Graham & Parks, Prospect Hill Charter,
Fletcher Maynard Academy — and total trips rise to 36,095, because
large schools now activate their surroundings faster. The starkest
line in the table is Rindge & Latin: at ≈5× average attractiveness it
fully saturates its catchment — patronage 11,071 ≈ its entire demand
pool (100%) is patronizing the destination. Size steers both which
sites win and how trips split. Use this variant when you can defend
the attractiveness values (floor area, program capacity), and compare
totals with run (g) only loosely — the choice sets and weighting
differ even though the normalization keeps the units aligned.

.. figure:: /_static/tutorials/t5_attract_weighted.png
   :width: 90%

The attractiveness-weighted configuration of run (h): large schools
(high ``attract`` values) draw demand from farther afield and dominate
the patronage table.


(i) From chosen sites to street-level flows
-------------------------------------------

The selected facilities are just a destinations layer, so the chosen
configuration can be fed straight into ``RunFlow()`` (Tutorial 3) to
estimate which streets the walk-to-center trips will use — siting and
footfall from one impedance model. We carry forward the configuration
the tutorial ends on, run (h): its ``..._facilities_selected.geojson``
output is directly usable as the flow run's destinations. The
consistency rule is to mirror the siting run's full behavioral model —
the same trip-generation method (``"gravity_cap"`` after
``max_patronage``; ``"closest"`` would follow a ``max_access``
siting), the same pinned cap, and the same destination weights feeding
the Huff split:

.. code-block:: python

   una.settings.destinations_file  = r"...\Results\...\Results_facilities_selected.geojson"  # from run (h)
   una.settings.flow_engine        = "aggregate_flow"
   una.settings.flow_decay_method  = "gravity_cap"   # matches (h)
   una.settings.flow_gravity_cap   = 1.0             # pinned, as in (h)
   una.settings.destination_weight_column = "attract" # same attractiveness as (h)
   una.RunFlow()

.. figure:: /_static/tutorials/t5_flow_chained.png
   :width: 90%

Aggregate pedestrian flows to run (h)'s six open centers, with edge
width proportional to ``edge_flow``. The approaches to the large,
high-``attract`` schools carry the heaviest flows.

The ready-to-edit file ``examples/UNA_FacilityAllocation.py`` wraps
this whole two-part workflow (Part 1 siting, Part 2 flow) into a
single script.


What we covered
---------------

The evaluation run (the default, ``fa_new_facilities = 0``) measures
how existing facilities serve demand; ``max_access`` sites new
facilities to maximize decay-weighted proximity; ``min_facilities``
finds the smallest number and placement of facilities that covers all
coverable demand; ``max_patronage`` maximizes expected trips under the
gravity-cap participation model, with destination choice Huff
splitting, overlap accounting, and optional facility attractiveness.
Greedy solving gives fast, nested, near-optimal answers and a
pick-order ranking; the exact solver gives precisely optimal results
that are sound for decision-making (and found a smaller
``min_facilities`` answer here), but is computationally more costly on
large datasets. Throughout, the Facility Allocation engine reused UNA
conventions you already know: origins/destinations layers,
``search_radius`` as cutoff, distance decay from the flow engines, and
outputs that join back onto your input layers — plus a
selected-facilities layer that connects directly to flow analysis.

*Exercises: (1) Re-run (b) with p = 5 — where do picks 4 and 5 go, and
how much does each add? (2) Lower the cutoff to 600 m in (d): how many
centers does full coverage now take? (3) In (g), replace β = 0.002
with the logistic curve — do the picks change? (4) Using runs (f) and
(g), compute each existing center's activation rate before and after
the additions.*
