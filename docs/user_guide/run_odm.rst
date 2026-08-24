RunODM()
========

``una.RunODM()`` computes an **origin-destination distance/duration
matrix** — one row per reachable (origin, destination) pair with the
network distance and estimated walking (or biking) duration between
them. It is UNA's answer to "how long does it take to get from A to B?"
across every pair of interest.

.. contents:: On this page
   :local:
   :depth: 1


What it does
------------

``RunODM()``:

1. Loads network, origins, and destinations.
2. Runs Dijkstra from every origin to identify every reachable
   destination within :py:data:`settings_reference:search_radius`.
3. Records the network distance for each reachable pair.
4. Converts distance to duration using a user-supplied walking or
   biking speed.
5. Writes the resulting matrix to disk in your chosen format.

The output is a long-format table — one row per pair — not a
wide-format matrix. Long format is easier to filter, join, and load
into pandas or a database.


Fully independent
-----------------

``RunODM()`` builds its own topology and its own engine from scratch. It
does **not** require you to call ``RunAccessibility()`` or ``RunFlow()``
first. You can call it as the first (or only) analysis in a script.


Required settings
-----------------

Two identifier columns are mandatory so that output rows are labeled
with real IDs rather than internal indices:

.. code-block:: python

   una.settings.origin_uid_column     = "building_id"     # required
   una.settings.destination_id_column = "stop_id"         # required

If either is missing, ``RunODM()`` raises a ``ValueError`` before any
work starts.

Beyond those two, the usual data settings apply:

.. code-block:: python

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"
   una.settings.search_radius     = 1500


Speed and duration
------------------

``RunODM()`` accepts a ``speed`` argument in km/h. This is used only to
compute the ``duration`` column; the ``distance`` column is always the
raw network distance in the units of your cost column (meters, by
default).

.. code-block:: python

   una.RunODM(format="Sqlite", speed=5.0)     # 5 km/h — typical walk
   una.RunODM(format="Sqlite", speed=15.0)    # 15 km/h — typical bike

Duration is computed as
``distance ÷ (speed × 1000 / 60)`` — minutes for meter-scale networks.


Output formats
--------------

Four formats are supported via the ``format`` argument:

.. list-table::
   :header-rows: 1
   :widths: 15 25 60

   * - Format
     - Extension
     - When to use
   * - ``"Sqlite"`` (default)
     - ``.sqlite``
     - Fastest random access for downstream queries; ideal when the
       matrix is large or you'll query it many times.
   * - ``"feather"``
     - ``.feather``
     - Fastest to load whole into a pandas DataFrame.
   * - ``"csv"``
     - ``.csv``
     - Human-readable; easy to open in Excel.
   * - ``"tsv"``
     - ``.tsv``
     - Same as CSV but tab-separated.

The format name is case-insensitive.


Output schema
-------------

Every row has four columns:

+---------------+------------------------------------------------------+
| Column        | Meaning                                              |
+===============+======================================================+
| ``origin``    | UID from ``origin_uid_column``.                      |
+---------------+------------------------------------------------------+
|``destination``| UID from ``destination_id_column``.                  |
+---------------+------------------------------------------------------+
| ``distance``  | Network distance in cost-column units                |
|               | (meters by default).                                 |
+---------------+------------------------------------------------------+
| ``duration``  | Minutes to traverse ``distance`` at ``speed`` km/h.  |
+---------------+------------------------------------------------------+

Only *reachable* pairs appear. If an origin cannot reach a given
destination within ``search_radius``, that row is omitted rather than
recorded with an infinite distance.


Engine dispatch
---------------

``RunODM()`` respects
:py:data:`settings_reference:turns` and
:py:data:`settings_reference:elevation` the same way
``RunAccessibility()`` does:

- ``turns = True`` → turn-aware engine (2–4× slower, more realistic).
- ``elevation = True`` → uphill segments are penalized.

Obstacle points also enter the routing costs if configured. Observer
points do not apply to ``RunODM()`` — they only make sense with the
Flow engine.


Where the output lands
----------------------

Files land in ``<data_folder>/Results/ODM_<timestamp>/`` unless you
override :py:data:`settings_reference:output_folder` or
:py:data:`settings_reference:output_wStamp`.


Example — Cambridge building-to-busstop OD matrix
-------------------------------------------------

.. code-block:: python

   from urban_network_analysis import UNA
   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"

   una.settings.origin_uid_column     = "id"
   una.settings.destination_id_column = "id"

   una.settings.search_radius = 1500

   una.RunODM(format="feather", speed=5.0)

Produces a feather file with one row per reachable
(building, bus stop) pair, columns ``origin | destination | distance |
duration``, walkable within 1.5 km at 5 km/h.


Common questions
----------------

**"How big is the output?"**
Roughly ``n_origins × avg_reachable_destinations`` rows. For 20,000
buildings and ~30 average bus stops each within 1.5 km, expect ~600,000
rows — a few MB in feather, ~30 MB in CSV.

**"Can I join the result back to my origin layer?"**
Yes — the ``origin`` and ``destination`` columns carry your original
UIDs, so a straight join in QGIS or pandas works.

**"How is walking speed calibrated?"**
UNA does not calibrate speed for you. The default is whatever you pass
to ``RunODM(speed=...)``. Typical pedestrian speeds are 4.5–5.5 km/h
(2.8–3.4 mph). For biking, 12–18 km/h. If your network uses perceived
lengths, remember that ``duration`` is speed times *perceived* length,
not ground length.

**"Do I need this if I already have RunAccessibility?"**
Different outputs. ``RunAccessibility()`` collapses reachability into
per-origin summary scores. ``RunODM()`` keeps every pair explicit —
useful when you want to plot travel-time distributions, feed a mode
choice model, or answer "how many buildings are within 10 minutes of
this bus stop?"


Next steps
----------

- :doc:`run_accessibility` — collapsed per-origin summaries.
- :doc:`run_flow` — per-edge flow modeling.
- :doc:`run_batch` — batch multiple ODM runs from a CSV.
