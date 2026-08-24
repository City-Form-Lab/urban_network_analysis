UNA_Workspace.py — annotated walkthrough
=========================================

Most UNA analyses are driven from a single Python script: ``UNA_Workspace.py``,
sitting in the root of the package folder. You open it in Visual Studio Code
(or any editor), change a handful of settings to match your data and research
question, and run the file. That's the entire workflow.

This page walks through every block of the default ``UNA_Workspace.py`` and
explains what it does, why you'd change it, and what happens when you don't.
By the end you should be able to write a workspace file for your own dataset
without opening the Settings reference.

.. tip::

   Keep the :doc:`settings_reference` open in another tab while you read this
   page. Every setting mentioned here has a longer description there.


Anatomy of the script
---------------------

A workspace file has five parts, in order:

1. **Import and instantiate** — the two lines that create the UNA object.
2. **Data files** — pointing at your network, origins, and destinations.
3. **Analysis parameters** — search radius, decay, weights, turns, elevation.
4. **Output preferences** — where results land, what formats to write.
5. **Run** — one line per analysis you want to run.

Everything else in the file is optional. You can delete anything you're not
using without breaking the script.


Part 1: Boot
------------

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

Line 1 imports the ``UNA`` class. Line 2 creates an instance called ``una``.
From this point on, every parameter is set as an attribute on
``una.settings``, and every analysis is a method call on ``una``.

You never need to instantiate ``Topology`` or an engine yourself — UNA
does that automatically when you call ``una.RunAccessibility()`` or
``una.RunFlow()``.


Part 2: Data files
------------------

.. code-block:: python

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "MIT_SAP_flow_origin.geojson"
   una.settings.destinations_file = "HarvardHC_and_GSD.geojson"

**data_folder** is the working folder where UNA will look for every input
file and (by default) write every output. It's the only path you specify
in full — the three ``*_file`` fields below are resolved relative to it.

Since UNA is pip-installed, your driver script and data can live
anywhere — a simple project folder looks like this:

.. code-block:: text

   my_project/
   ├── UNA_Workspace.py     ← copied from the repo's examples/ folder
   └── Boston/              ← tutorial data (or your own data folder)

With this layout, ``r"Boston"`` resolves correctly when
``UNA_Workspace.py`` runs from ``my_project/``. Relative paths depend
on the working directory, though, so an absolute path is the more
robust choice:

.. code-block:: python

   # Windows — use a raw string so backslashes are treated literally
   una.settings.data_folder = r"C:\Users\yourname\Documents\my_project\Boston"

   # macOS or Linux — forward slashes or raw strings both work
   una.settings.data_folder = "/Users/yourname/Documents/my_project/Boston"

**network_file**, **origins_file**, and **destinations_file** are the
three data layers every analysis needs. UNA accepts any GIS format that
GeoPandas can read — GeoJSON, Feather, Parquet, Shapefile, GeoPackage.
For large jobs, Feather is dramatically faster to load than GeoJSON.

.. note::

   All three layers **must share the same coordinate reference system
   (CRS)**. For the Boston tutorial data that's EPSG:6491 (NAD83(2011)
   Massachusetts Mainland, meters). UNA reads the network's CRS and
   refuses to load an origins or destinations file with a mismatched CRS —
   this catches a common source of "why is my output empty?" bugs.


Part 3: Which accessibility metrics to compute
----------------------------------------------

.. code-block:: python

   una.settings.calculate_reach               = True
   una.settings.calculate_exponential_gravity = True
   una.settings.calculate_logistic_gravity    = True
   una.settings.calculate_knn_access          = True

The four ``calculate_*`` flags select which accessibility metrics
``RunAccessibility()`` produces. All four default to ``True``, and the
default is usually right — enabling more metrics adds only marginal
compute cost because the shortest-path calculation is shared across them.

Leave them all commented out in your workspace file to accept the
defaults; explicitly set to ``False`` to disable one you know you don't
need.

.. seealso::

   :doc:`../concepts/gravity_and_decay` explains the math behind each
   metric.


Part 4: Search radius and decay
-------------------------------

.. code-block:: python

   una.settings.search_radius = 3000
   una.settings.gravity_beta  = 0.001

**search_radius** is the maximum network distance any analysis considers,
in the units of your network cost column (meters by default). Destinations
beyond this radius from an origin are simply ignored.

Choose the radius based on the mode you're modeling:

- Pedestrian accessibility: 400–1500 m
- Bike accessibility: 3000–5000 m
- Transit access shed around a bus stop: 500–800 m

**gravity_beta** is the exponential decay coefficient — how fast a
destination's contribution to Gravity Accessibility falls off with
distance. The half-distance (where a destination contributes 50 % of its
raw weight) is ``ln(2) / β``:

+---------+--------------------------+
| β       | Half-distance            |
+=========+==========================+
| 0.001   | ~693 m (walking)         |
+---------+--------------------------+
| 0.003   | ~231 m (short walk)      |
+---------+--------------------------+
| 0.0005  | ~1386 m (biking)         |
+---------+--------------------------+

If you're using logistic decay instead of exponential, set
``gravity_logistic_midpoint`` (in the same units as ``search_radius``)
and UNA will auto-derive the appropriate steepness.


Part 5: Nearest-destination filter
----------------------------------

.. code-block:: python

   una.settings.use_nearest_destination = False

Leave ``False`` for the standard case: origins consider every reachable
destination within the search radius.

Set to ``True`` for a *closest-facility* analysis — each origin looks
only at its single nearest destination. Useful for "distance to nearest
hospital," "distance to nearest transit stop," or the initial step of a
route-choice simulation.

For an intermediate case ("distance to the *k* nearest" rather than 1),
use ``flow_k_nearest_destinations`` in Flow analyses.


Part 6: Flow decay method
-------------------------

.. code-block:: python

   una.settings.flow_decay        = True
   una.settings.flow_decay_method = "gravity_cap"
   una.settings.flow_gravity_cap  = 3.0     # required when method = "gravity_cap"

These three fields shape the trip-generation curve for ``RunFlow()`` —
how many trips each origin actually generates as a function of the
destinations it can reach.

**flow_decay** turns decay on or off. When ``False``, every origin
generates its full origin-weight in trips regardless of destination
proximity — useful for baseline "what if all buildings generated equal
trips?" analyses, but not realistic pedestrian behavior.

**flow_decay_method** controls how the decay factor is aggregated across
an origin's reachable destinations:

- ``"closest"`` (the default) — the factor is evaluated at the origin's
  *nearest* destination. Parameter-free, monotonic, and easy to reason
  about.
- ``"gravity_cap"`` — the factor is ``min(1, gravity / flow_gravity_cap)``,
  where gravity is the Huff sum of ``destination_weight × decay``.
  Captures destination density and lets you explicitly set the saturation
  threshold. Requires calibration against your destination-weight scale.

**flow_gravity_cap** is only used when method is ``"gravity_cap"``. Pick a
value in the same units as your gravity sum: if median destination
weight is 30, a cap of 30 means "one median-weight destination at zero
distance saturates the origin at full trip generation."


Part 7: Weights
---------------

.. code-block:: python

   una.settings.flow_origin_weights       = True
   una.settings.origin_weight_column      = "weight"

   una.settings.flow_destination_weights  = True
   una.settings.destination_weight_column = "weight"

**flow_origin_weights** toggles whether origins generate trips in
proportion to a numeric weight column (residents in a building, floor
area, employees). When ``False``, each origin generates exactly one trip
regardless of its weight column.

**origin_weight_column** names the attribute in ``origins_file`` that
carries the numeric weight. Use ``"Count"`` (case-sensitive) as a
sentinel meaning "give every origin unit weight" without touching the
file.

**flow_destination_weights** enables Huff-style destination choice:
trips split across destinations in proportion to ``weight × decay``, so
a destination that's twice as attractive at the same distance gets twice
the share. When ``False``, trips split uniformly across all reachable
destinations regardless of attractiveness.

**destination_weight_column** names the attribute — jobs, retail floor
area, seating capacity, daily transit departures.

.. tip::

   For accessibility analyses (``RunAccessibility()``), the destination
   weight column is used directly in Reach and Gravity. For flow
   analyses (``RunFlow()``), it enters the Huff trip-distribution math.
   In both cases, the same column name works.


Part 8: Detour envelope
-----------------------

.. code-block:: python

   una.settings.flow_detour_ratio               = 1.15
   una.settings.flow_detour_mode                = "ratio"
   una.settings.flow_n_alternatives             = 50
   una.settings.flow_alternative_penalty_factor = 1.5

These fields shape how ``RunFlow()`` enumerates alternative paths between
each origin and destination. Real pedestrians don't always take the
mathematically shortest route — the Flow engine emulates this by finding
*K* alternative paths and splitting trips across them.

**flow_detour_ratio** — the maximum allowed detour, as a ratio of the
shortest path. ``1.15`` means "consider paths up to 15 % longer than the
shortest." ``1.0`` disables alternatives entirely (shortest path only).

**flow_detour_mode** — one of ``"ratio"``, ``"buffer"``, or ``"min"``.
The ``"min"`` mode combines a proportional ratio with an absolute buffer
(``flow_detour_buffer``) and uses the tighter of the two. Recommended
for realistic pedestrian modeling: the buffer prevents absurd
multi-kilometer detours on very short trips, and the ratio prevents
proportionally sensible detours from being clipped on long trips.

**flow_n_alternatives** — how many alternative paths to enumerate per OD
pair before stopping. Higher values find more back-alley routes at the
cost of runtime. ``10`` is a lean default; ``30–50`` gives richer flow
patterns on complex urban grids.

**flow_alternative_penalty_factor** — multiplier applied to edge weights
between successive alternative-path searches. Larger values force each
new alternative further off the shortest path; smaller values enumerate
close cousins of the shortest. Must be ≥ 1.0. Try 1.5–2.5 for pedestrian
networks.

.. seealso::

   :doc:`../concepts/k_alternatives` explains Plateau's penalty method
   in detail.


Part 9: Turn penalties
----------------------

.. code-block:: python

   una.settings.turns          = False
   una.settings.turn_threshold = 45
   una.settings.turn_penalty   = 35

**turns** enables turn-aware routing. When ``True``, every change of
direction sharper than ``turn_threshold`` degrees at a network node adds
``turn_penalty`` cost-units to the route. Pedestrians and cyclists prefer
to avoid turns when possible; a defensible pedestrian flow model usually
has turns on.

.. warning::

   Turn-aware routing is roughly 2–4× slower than turn-free routing on
   dense urban networks. Expect 1–3 minutes for a 14,751-origin Cambridge
   run on a laptop with turns on, vs. 30–60 seconds without.
   Prototype your parameters with turns off, then turn them on for the
   final run.

**turn_threshold** — the angle (in degrees) above which a change of
direction counts as a turn. 45° penalizes anything sharper than a gentle
curve. Lower values (30°) penalize even gentle bends; higher values (90°)
only penalize sharp corners.

**turn_penalty** — the cost added per turn, in the same units as your
edge weights. 32–35 is a plausible starting point for meters-scale
pedestrian networks. Calibrate against local route-choice studies if
you have them.


Part 10: Elevation penalties
----------------------------

.. code-block:: python

   una.settings.elevation         = True
   una.settings.elevation_penalty = 4

**elevation** enables the elevation penalty. Requires the network
geometry to carry z-coordinates (3D LineStrings). When enabled, uphill
travel accrues an extra cost proportional to vertical rise; downhill
travel is unpenalized.

**elevation_penalty** — extra cost per meter of vertical rise. A value
of ``4`` means every meter uphill feels like 4 extra meters of horizontal
walking. Typical calibrated values for walking are 3–6. For biking, 8–20.

.. tip::

   Elevation and turn penalties stack additively on route cost. Turn
   detection uses only the 2D geometry, so the two are orthogonal.


Part 11: Output settings (optional)
-----------------------------------

.. code-block:: python

   # una.settings.output_folder    = "MyResults"
   # una.settings.output_file_name = "Boston_1500m"
   # una.settings.output_wStamp    = True
   # una.settings.result_prefix    = "walk_"

All four are optional. If you leave them at their defaults, results land
in ``<data_folder>/Results/<timestamp>/`` with generic filenames.

**output_folder** — where results go. Default:
``<data_folder>/Results/``.

**output_file_name** — the filename stem for outputs. Default:
``"Results"``. Set it to something meaningful (``"Cambridge_bus_1500m"``) so
months later you can still tell which file is what.

**output_wStamp** — when ``True`` (default), every run creates a
timestamped subfolder so repeated runs don't overwrite each other. Set
to ``False`` if you want a single canonical output location.

**result_prefix** — prepended to every result column name. Useful when
running multiple analyses into the same output folder — set to
``"school_"`` for one run and ``"bus_"`` for another so their columns
don't clash when you join them in QGIS.


Part 12: Run
------------

.. code-block:: python

   una.RunAccessibility()
   una.RunFlow()

The last two lines invoke the analyses. You can run either or both, in
either order.

**RunAccessibility()** computes Reach, Gravity (exponential and
logistic), and KNN indices at every origin point. Output: one row per
origin, one column per enabled metric.

**RunFlow()** computes per-edge trip volumes across the whole network.
Output: one row per network edge, with columns for total flow and
optionally per-direction (AB / BA) flow.

Both methods rebuild the topology from scratch — you don't have to run
one before the other, and there's no state carried between them.


A minimal workspace file
------------------------

If you strip out every optional and defaulted setting, a working
``UNA_Workspace.py`` can be as short as:

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"

   una.settings.search_radius     = 500

   una.RunAccessibility()

Eight lines, one analysis. All the other decay, weight, turn, and
elevation settings default sensibly for a pedestrian-scale walkability
analysis.


Common workspace patterns
-------------------------

**Change one parameter, rerun.** The core workflow. Edit the file, save,
run ``python UNA_Workspace.py``. The tutorials use this pattern
repeatedly — each step changes one setting and shows what changes in the
output.

**Save your settings for later.** Once you have a working configuration:

.. code-block:: python

   una.SaveSettings(r"Settings\boston_1500m.json")

Reload it in a fresh session:

.. code-block:: python

   una.LoadSettings(r"Settings\boston_1500m.json")
   una.RunAccessibility()

**Print current settings.** For debugging when a run produces unexpected
output, dump the full settings snapshot:

.. code-block:: python

   una.PrintSettings()

**Batch runs.** When you need to run the same analysis with different
parameters (multiple radii, multiple destination categories, multiple
scenarios), use the :doc:`Project workflow <run_batch>` instead of
editing the workspace file by hand.


Next steps
----------

- Run your first analysis end-to-end:
  :doc:`../getting_started/first_analysis`
- Understand the decay math: :doc:`../concepts/gravity_and_decay`
- Understand K-alternative paths: :doc:`../concepts/k_alternatives`
- Batch many runs at once: :doc:`run_batch`
- Deeper on any single setting: :doc:`settings_reference`
