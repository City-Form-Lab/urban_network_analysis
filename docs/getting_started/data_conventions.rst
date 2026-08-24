Data conventions
================

Every UNA analysis takes at least three GIS layers: a **network**, a
set of **origins**, and a set of **destinations**. Two optional layers —
**observer points** and **obstacle points** — extend flow analyses with
counters and cost penalties respectively.

This page covers what those layers must contain, what file formats UNA
accepts, and the coordinate system rules that determine whether an
analysis succeeds or silently produces empty output.


The three required layers
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 25 55

   * - Layer
     - Geometry type
     - What it represents
   * - Network
     - ``LineString``
     - Path segments (sidewalks, streets, cycle paths, corridors) along
       which pedestrians or cyclists can travel.
   * - Origins
     - ``Point``
     - Locations where trips begin — buildings, transit boarding
       points, analysis grid cells.
   * - Destinations
     - ``Point``
     - Locations where trips end — jobs, shops, transit alighting
       points, parks, schools.

UNA snaps origin and destination points onto the nearest network edge
at runtime; you do not have to pre-snap them yourself.


The network layer
-----------------

Every ``LineString`` in the network file becomes one edge in UNA's
internal graph. Two edges connect at a node if and only if they share
an endpoint exactly — this is the single most important rule to
understand about UNA data.

**Correctly split — trips flow.** If two edges meet at an
intersection and both terminate at the same shared coordinate, UNA
treats them as connected and trips can route through the intersection.

**Un-split intersections — trips cannot pass.** If one edge ends
somewhere along another edge without splitting it, the two are
disconnected as far as routing is concerned. This is the most common
cause of "why is my accessibility so low?" — the network looks fine
visually but has unintentional dead-ends in the middle.

**Overpasses and underpasses — no shared node.** Two edges that cross
in plan view without sharing an endpoint are interpreted as an
overpass or underpass — visually crossing but not connected. This is
how UNA represents bridges, tunnels, and grade separations. You do not
need z-coordinates to model this; the absence of a shared node is
enough.

.. figure:: /_static/tutorials/t1_image1.png
   :alt: Three topology cases
   :width: 100%

   **The three topology cases.** (a) Correctly split — all segments
   share an endpoint and trips can flow. (b) A T-junction where one
   curve dead-ends on another that wasn't split: trips cannot pass
   through. (c) Two curves crossing without a shared node —
   interpreted as an overpass/underpass, not a connection.

.. tip::

   **Clean your network before running UNA.** Use QGIS's *Vector →
   Geometry Tools → Split with Lines* or the equivalent in ArcGIS Pro,
   Rhinoceros 3D, or CAD software. Every visual intersection in your
   study area should become a topological one before you load the file.


The network cost column
~~~~~~~~~~~~~~~~~~~~~~~

Every edge carries a **cost attribute** — the number that Dijkstra's
algorithm minimizes when finding shortest paths. By default this is
the segment's geometric length in meters (or whatever unit your
projected CRS uses).

You can also supply a custom cost column with **perceived length** —
meters as the pedestrian experiences them, not as the surveyor measures
them. A pleasant tree-lined street might have a quality factor of 0.8
(each meter feels like 0.8 m), a noisy arterial might score 1.4 (each
meter feels like 1.4 m). UNA does not prescribe how you derive these
factors; it will route correctly on whatever numeric column you supply.

.. figure:: /_static/tutorials/t1_image5.png
   :alt: Objective vs perceived length
   :width: 90%

   Two routes between an origin O and a destination D. The arterial is
   shorter in ground meters but feels longer because of noise, traffic,
   and pollution; the park path is geometrically longer but feels
   shorter. With perceived length as the cost column, the engine
   prefers the park path.

Point at your custom column via ``settings.network_weight_column``:

.. code-block:: python

   una.settings.network_weight_column = "perceived_length"   # instead of "Geometric"

The sentinel ``"Geometric"`` (the default) means "use the segment's
ground length."


Optional: elevation via 3D geometry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If your network file uses 3D LineStrings (each vertex has a z-value),
UNA can additionally apply an :py:data:`../user_guide/settings_reference:elevation_penalty`
that makes uphill segments feel longer. See :doc:`../concepts/elevation_turns`
for the math. If your file is 2D only, leave
:py:data:`../user_guide/settings_reference:elevation` = False.


The origins and destinations layers
-----------------------------------

Both are Point layers. UNA snaps every point onto its nearest network
edge automatically at runtime.

Each layer typically carries a **weight column** — a numeric attribute
quantifying how much the point matters in the analysis:

- **Origin weights** typically represent trip generators: residents in
  a building, floor area, number of employees.
- **Destination weights** typically represent attractiveness: jobs at
  a workplace, floor area of a shop, seating capacity, daily transit
  departures.

Weights enter the math directly. In a Gravity analysis, a destination
with weight 25 attracts 5× as much flow as a destination with weight 5
at the same distance. In a Flow analysis, an origin with weight 100
generates 10× as many trips as an origin with weight 10.

.. figure:: /_static/tutorials/t1_image3.png
   :alt: Origins and destinations with weights
   :width: 70%

   A small network with origin points (blue squares) and destination
   points (red circles). Symbol size and the number inside each marker
   show the point's weight. Both classes snap to the nearest edge
   automatically.

Point at your weight columns via:

.. code-block:: python

   una.settings.origin_weight_column      = "residents"
   una.settings.destination_weight_column = "employees"

Use ``"Count"`` (case-sensitive, the default sentinel) if you want
every point to count as 1 without touching your file.


CRS: all layers must match
--------------------------

Every layer — network, origins, destinations, and any observer or
obstacle layer you use — **must share the same coordinate reference
system**. UNA reads the network's CRS as the reference and refuses to
load subsequent layers if their CRS differs.

We strongly recommend a **projected CRS in meters**. Two reasons:

1. UNA's distance calculations, gravity decay parameters, and detour
   ratios all assume the cost units are meters (or whatever your
   projected unit is). Geographic coordinates (lat/lon degrees) would
   produce meaningless numbers.
2. Every ``search_radius``, ``turn_penalty``, ``elevation_penalty``, and
   ``flow_gravity_cap`` value you set is interpreted in those same
   units. Mixing units silently breaks the model.

For the Boston tutorial data, that CRS is **EPSG:6491** (NAD83(2011)
Massachusetts Mainland, meters). For US data, use a state-plane or
UTM zone. For a European city, use ETRS89 / UTM or the local national
grid.

When you open the data in QGIS, set the project CRS to match:

- **Project → Properties → CRS**
- Search for your projection code (e.g. ``6491``) and select it.


File formats
------------

UNA reads any format that GeoPandas can read. In practice you'll
choose between:

.. list-table::
   :header-rows: 1
   :widths: 15 20 25 40

   * - Format
     - Extension
     - Load speed
     - When to use
   * - GeoJSON
     - ``.geojson``
     - Slow (text parsing)
     - Small datasets, tutorials, when you want to open
       the file in a text editor to inspect it.
   * - Feather
     - ``.feather``
     - Very fast (binary)
     - Large datasets and batch pipelines. Preserves geometry
       and attributes losslessly. The recommended format for
       production runs.
   * - Parquet
     - ``.parquet``
     - Very fast (binary)
     - Same speed as Feather with slightly smaller files.
       Compatible with more analytical tooling (Spark, DuckDB).
   * - Shapefile
     - ``.shp`` (+ sidecars)
     - Medium
     - Only if a downstream tool requires it. Shapefile truncates
       column names to 10 characters and has a 2 GB size limit.
   * - GeoPackage
     - ``.gpkg``
     - Medium
     - When you want multiple layers in one SQLite-backed file.

UNA infers the format from the file extension, so a workspace file
that references ``20260703_PercLenNetwork_InnerCore.geojson`` and one that references
``20260703_PercLenNetwork_InnerCore.feather`` work identically as long as the file exists.


Where UNA looks for files
-------------------------

The workspace file sets one path (``data_folder``) and three relative
filenames:

.. code-block:: python

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"

At runtime UNA joins each filename onto ``data_folder`` to build the
full path. In the example above, it looks for
``Boston/20260703_PercLenNetwork_InnerCore.geojson`` first, then origins, then
destinations.

**Recommended layout — a project folder with script and data together**
(UNA itself is pip-installed, so it does not need to be anywhere near
your data):

.. code-block:: text

   my_project/               ← any folder, anywhere on disk
   ├── UNA_Workspace.py      ← copied from the repo's examples/ folder
   └── Boston/               ← tutorial data
       ├── 20260703_PercLenNetwork_InnerCore.geojson
       ├── Cambridge_building_centroids.geojson
       └── MA_bus_stops.geojson

With this layout, ``data_folder = r"Boston"`` resolves correctly
when ``UNA_Workspace.py`` runs from ``my_project/``. This is the
pattern the tutorials use.

**Alternative — absolute path.** If your data lives somewhere else on
disk, use an absolute path:

.. code-block:: python

   # Windows
   una.settings.data_folder = r"C:\Users\yourname\Documents\my_project\Boston"

   # macOS or Linux
   una.settings.data_folder = "/Users/yourname/Documents/my_project/Boston"

**Alternative — sub-folder layout.** If you organize files by type inside
``data_folder``, include the subfolder in each filename:

.. code-block:: python

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "network/20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "origins/Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "destinations/MA_bus_stops.geojson"


Optional layers: observers and obstacles
----------------------------------------

Two additional Point layers extend Flow analyses:

- **Observer points** — passive flow counters. Attached to specific
  edges to count how much flow passes through, without influencing
  routing. Loaded via ``observer_points_file``. See
  :doc:`../user_guide/observers_obstacles`.
- **Obstacle points** — cost-adding penalties. Attached to specific
  edges to make those edges feel longer (broken sidewalks, missing
  curb ramps, construction zones). Loaded via ``obstacle_points_file``.
  Affects both accessibility and flow analyses.

Both use the same file-format rules and CRS-matching requirements as
origins and destinations.


The tutorial dataset
--------------------

The Boston tutorial dataset ships in the UNA repository at
``docs/Boston/``. Copy it into a ``Boston/`` folder inside your project
folder (see `Where UNA looks for files`_ above), or point
``data_folder`` at your clone's ``docs/Boston`` directly.

The dataset covers the Boston inner core (Cambridge + Boston core),
all in EPSG:6491. The core files you'll use in the tutorials are:

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - File
     - Points / Edges
     - Notes
   * - ``20260703_PercLenNetwork_InnerCore.geojson``
     - 69,957 edges
     - Pedestrian network with ``Geometric`` and ``PercLength``
       (perceived length) cost columns. A ``_3D`` twin carries
       z-coordinates for the elevation penalty.
   * - ``Cambridge_building_centroids.geojson``
     - 14,751 points
     - Every building in Cambridge (unit weight).
   * - ``Cambridge_building_centroids_pop2020.geojson``
     - 14,751 points
     - Same buildings with a ``pop2020`` population weight
       (for residential trip generation).
   * - ``MA_bus_stops.geojson``
     - 6,661 points
     - MBTA-region bus stops; includes ``weekly_departures``.
   * - ``MA_transit_stations.geojson``
     - 6,919 points
     - All transit stops across the MBTA region.
   * - ``Cambridge_transit_stations.geojson`` / ``Cambridge_metro_stations.geojson``
     - 434 / 18 points
     - Transit and rapid-transit stations in and around Cambridge.
   * - ``schools_cambridge.geojson``
     - 60 points
     - School locations in Cambridge.
   * - ``MIT_SAP_flow_origin.geojson``
     - 1 point
     - Single-origin demo used in the flow tutorial (weight 100).
   * - ``HarvardHC_and_GSD.geojson``
     - 2 points
     - Two named destinations used in the flow tutorial
       (Harvard Housing Center, weight 1; Harvard GSD, weight 3).

Point layers carry either a ``weight`` column, a named weight
attribute (``pop2020``, ``weekly_departures``), or the unit-weight
``Count`` sentinel, plus category-specific attributes.


Next steps
----------

- Run your first analysis end-to-end: :doc:`first_analysis`.
- Understand the full UNA_Workspace.py structure:
  :doc:`../user_guide/workspace_walkthrough`.
- Deep dive on impedance factors:
  :doc:`../user_guide/impedance_models`.
