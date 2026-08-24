Your first analysis
===================

This page walks you through your first UNA analysis end-to-end — from a
blank workspace file to a map of bus-stop accessibility for every building
in Cambridge, MA. It takes about ten minutes.

By the end you will have:

- A working ``UNA_Workspace.py`` in your local package folder,
- A feather file and GeoJSON file with per-origin accessibility scores,
- The result visualized in QGIS.


Prerequisites
-------------

Before you start, make sure you have:

1. **UNA installed** — the ``una`` conda environment ready to activate.
   See :doc:`installation`.

2. **The Boston tutorial data** placed inside your project folder —
   the assumed layout is:

   .. code-block:: text

      my_project/              ← any folder (contains UNA_Workspace.py,
      │                          copied from the repo's examples/ folder)
      └── Boston/              ← tutorial data (ships in the repo at docs/Boston)

   The three files we'll use are:

   - ``20260703_PercLenNetwork_InnerCore.geojson`` — the Boston
     inner-core pedestrian network (69,957 edges).
   - ``Cambridge_building_centroids.geojson`` — every building in
     Cambridge (14,751 point centroids).
   - ``MA_bus_stops.geojson`` — 6,661 MBTA-region bus stops, with a
     ``weekly_departures`` column giving weekly departures per stop.

3. **QGIS installed** — for opening and styling the output. Any version
   3.44+ or 4.0.3+ works.


Step 1. Open the workspace file
-------------------------------

Open Visual Studio Code and open your project folder. In the file
tree on the left, click ``UNA_Workspace.py``. This is where you edit
UNA settings.

Open a Terminal panel in VS Code (**Terminal → New Terminal**) and
activate the conda environment:

.. code-block:: bash

   conda activate una

You'll run every command from this terminal for the rest of the tutorial.


Step 2. Configure the analysis
------------------------------

Delete everything currently in ``UNA_Workspace.py`` and paste the
following:

.. code-block:: python

   from urban_network_analysis import UNA

   una = UNA()

   una.settings.data_folder       = r"Boston"
   una.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
   una.settings.origins_file      = "Cambridge_building_centroids.geojson"
   una.settings.destinations_file = "MA_bus_stops.geojson"

   una.settings.search_radius              = 500
   una.settings.destination_weight_column  = "weekly_departures"

   una.RunAccessibility()

That's it — nine lines. Save the file (**File → Save**, or ``Ctrl+S`` /
``Cmd+S``).

Here's what each block does:

- **Lines 1–3** load UNA and create an ``una`` instance.
- **Lines 5–8** point at the three data layers (network + origins +
  destinations). ``r"Boston"`` assumes the ``Boston`` data folder
  sits inside your project folder; adjust the path if your
  layout differs.
- **Line 10** sets the search radius to 500 m — buildings can reach any
  bus stop within a 500-meter network walk.
- **Line 11** tells UNA to weight each bus stop by its
  ``weekly_departures`` column, so a stop with many weekly departures counts
  more than a stop with few.
- **Line 13** runs the accessibility analysis.


Step 3. Run it
--------------

In the VS Code terminal, run:

.. code-block:: bash

   python UNA_Workspace.py

You should see log output that looks something like:

.. code-block:: text

   [UNA] Instance created.
   [Network added] Network added from 20260703_PercLenNetwork_InnerCore.geojson, with 69957 edges.
   [Origins added] Origins added from Cambridge_building_centroids.geojson, with 14751 points.
   [Destinations added] Destinations added from MA_bus_stops.geojson, with 6661 points.
   [UNA Accessibility] Using AccessibilityWElevation (no elevation — symmetric weights).
   [Export Results] Combined results (with geometry) exported to Boston/Results/accessibility_2026-01-15_1042/Results.feather

The exact folder name will differ (it's timestamped). The whole run
takes about 30 seconds on a modern laptop.


Step 4. Find the output
-----------------------

Navigate to ``Boston/Results/accessibility_<timestamp>/`` (the
``Results`` folder is created inside your ``data_folder``). You should
see:

- ``Results.feather`` — the analysis output in Feather format.
- ``Results.geojson`` — the same output in GeoJSON (easier to open in
  QGIS).

The GeoJSON has one point per origin (~20,000 buildings) with the
following columns:

+-------------------+---------------------------------------------------+
| Column            | Meaning                                           |
+===================+===================================================+
| ``geometry``      | Building centroid location.                       |
+-------------------+---------------------------------------------------+
| ``reach``         | Weighted count of bus stops within 500 m          |
|                   | (sum of ``weekly_departures`` values).            |
+-------------------+---------------------------------------------------+
| ``gravity_``      | Gravity-weighted accessibility (exponential       |
| ``exponential``   | decay).                                           |
+-------------------+---------------------------------------------------+
| ``gravity_``      | Gravity-weighted accessibility (logistic          |
| ``logistic``      | decay).                                           |
+-------------------+---------------------------------------------------+
| ``knn_logistic``  | K-nearest-neighbor composite accessibility.       |
+-------------------+---------------------------------------------------+


Step 5. Visualize in QGIS
-------------------------

Open QGIS. In the Browser panel, navigate to your
``Boston/Results/accessibility_<timestamp>/`` folder and drag
``Results.geojson`` onto the map canvas.

Set the project CRS to match the data:

- **Project → Properties → CRS**
- Search ``6491`` and select **EPSG:6491** (NAD83(2011)
  Massachusetts Mainland).

You should see 14,751 building centroids across Cambridge, all colored
identically at first. To make the accessibility visible:

1. Right-click the layer → **Properties** → **Symbology**.
2. Change **Single symbol** to **Graduated** at the top.
3. Set **Value** to ``reach``.
4. Click **Classify**, then choose a color ramp (Viridis is a good
   default).
5. Click **OK**.

Now the map is colored by accessibility: dark points are buildings with
low transit access, bright points are buildings with many daily bus
departures within 500 m. You should see a clear pattern — Harvard
Square, Central Square, and Kendall Square lit up, peripheral
neighborhoods dark.

To load the network as a background:

- Drag ``20260703_PercLenNetwork_InnerCore.geojson`` (from the ``Boston/`` folder, not
  from Results) onto the canvas.
- Style it with a thin gray line.

You now have a real accessibility map showing how many weekly bus departures are within a 500m walkshed from each address point in Cambridge, MA.


.. figure:: /_static/main/Reach_to_bus_stops500m.png
   :alt: Reach to bus stops 500m
   :width: 100%

   **Reach to bus stops in Cambridge, MA in a 500m walkshed (weighted by weekly departures at each stop)** 
   

What you built
--------------

This tutorial ran the simplest possible accessibility analysis: Reach
(bus stops within 500 m, weighted by daily departures) plus three
companion metrics that came for free. The pattern you'd see in the map —
central density, peripheral scarcity — is the empirical signature every
transit-accessibility analysis in every city produces.

To go deeper, try modifying the workspace file and re-running. Some
small experiments:

- Change ``search_radius`` to ``800`` and rerun. How does the pattern
  spread outward?
- Set ``una.settings.use_nearest_destination = True``. Now each
  building's accessibility depends only on its single nearest stop. How
  does the map change?
- Set ``una.settings.elevation = True`` and rerun. Buildings on
  Toomemägi (the central hill) should get slightly lower scores.


Next steps
----------

- For a full annotated tour of every ``UNA_Workspace.py`` block, see
  :doc:`../user_guide/workspace_walkthrough`.
- For the step-by-step accessibility tutorial with 8
  progressive variations on this analysis, see
  :doc:`../tutorials/tutorial_2_accessibility`.
- For flow analysis (per-edge pedestrian trip counts), see
  :doc:`../user_guide/run_flow` or
  :doc:`../tutorials/tutorial_3_flow`.
- For the underlying math (Reach, Gravity, KNN), see
  :doc:`../concepts/gravity_and_decay`.
