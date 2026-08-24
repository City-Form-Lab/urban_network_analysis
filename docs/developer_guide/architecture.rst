Architecture overview
=====================

This page describes how UNA is organized internally — what lives where,
how data flows through the classes, and the design principles that
guide every extension. If you plan to modify UNA or add a new analysis,
read this first.

The authoritative version of this architecture is ``DESIGN.md`` at the
repository root; this page mirrors it and adds cross-references into
the rest of the docs.

.. contents:: On this page
   :local:
   :depth: 1


High-level layout
-----------------

::

   UNA                      ← public API, owns Settings and Topology;
    │                         also hosts batch orchestration + compositing
    ├── Settings           ← single dataclass; every parameter lives here
    ├── Topology           ← network + origin/destination/observer/obstacle
    │                        layers, spatial indices, snapping
    └── Engines/
          ├── Base                    ← abstract base + all export methods
          ├── Accessibility           ← Reach/Gravity/KNN (no elevation, no turns)
          ├── AccessibilityWElevation ← same + directional weights from z
          ├── AccessibilityWTurns     ← turn-aware line graph
          ├── Flow                    ← K-alternatives betweenness engine
          └── AggregateFlow           ← scalable gradient-overlap flow engine
                                        (standalone — builds its own graph,
                                        so the two flow engines evolve
                                        independently)

The physical layout of the repository mirrors this (src-layout — the
installable package lives under ``src/``):

::

   urban_network_analysis/     ← repository root
   ├── pyproject.toml          ← package metadata + dynamic version
   ├── DESIGN.md
   ├── README.md
   ├── LICENSE
   ├── setup/una.yml           ← conda environment for the dependencies
   ├── examples/               ← driver-script templates (UNA_Workspace.py,
   │                             UNA_Batch.py) — copy anywhere, edit, run
   ├── docs/                   ← this documentation (docs/Boston = tutorial data)
   └── src/
       └── urban_network_analysis/
           ├── __init__.py     ← __version__ + re-exports (UNA, Settings, Topology)
           ├── Logger.py
           ├── Settings.py
           ├── Topology.py
           ├── UNA.py
           └── Engines/
               ├── __init__.py
               ├── Base.py
               ├── Accessibility.py
               ├── AccessibilityWElevation.py
               ├── AccessibilityWTurns.py
               ├── Flow.py
               ├── AggregateFlow.py
               └── _betweenness_numba.py


Data flow through one run
-------------------------

For a single call to ``una.RunAccessibility()`` or ``una.RunFlow()``:

1. The user sets parameters on ``una.settings`` (a ``Settings``
   instance).
2. The run method calls ``settings.Validation()`` to catch invalid
   enum values, negative radii, or missing required inputs.
3. The run method builds the topology via
   ``topology.AddNetwork(settings)`` → ``AddOrigins(settings)`` →
   ``AddDestinations(settings)`` → and (conditionally)
   ``AddObservers(settings)`` and ``AddObstacles(settings)``.
4. Based on ``settings.turns`` and ``settings.elevation``, UNA
   instantiates the appropriate engine.
5. The engine runs its analysis (Dijkstra sweeps, path enumeration,
   accumulation) and stores results as numpy arrays on itself
   (``engine.reach``, ``engine.edge_flow``, etc.).
6. The engine calls one of ``Base``'s export methods to write results
   to disk in the formats enabled by
   :py:data:`../user_guide/settings_reference:output_geojson`,
   :py:data:`../user_guide/settings_reference:output_feather`, and
   :py:data:`../user_guide/settings_reference:output_csv`.

**Topology is rebuilt at every run.** It is not cached between calls
— engines do not mutate topology, and every ``Run*`` call starts from
a fresh network/origin/destination load. This adds a few seconds of
overhead per run but eliminates a class of "stale cache" bugs.


The four core classes
---------------------

Settings
~~~~~~~~

A single ``@dataclass`` in ``urban_network_analysis/Settings.py``. Every configurable
parameter lives here. Setting a value is:

.. code-block:: python

   una.settings.search_radius = 500

There are no hidden settings elsewhere in the codebase. See
:doc:`../user_guide/settings_reference` for the full field list.

Notable methods:

- ``Validation()`` — validates enums, ranges, required-when combinations.
- ``Reset()`` — restores all fields to defaults.
- ``ToDict(compact=True)`` — serialize only non-default fields; used
  for saving to JSON.
- ``ApplyRow(row: dict)`` — reset then apply one row of a CSV/JSON
  project pairing.

Topology
~~~~~~~~

Holds the network graph and the point layers attached to it. Owns
methods for loading and snapping each point-type layer:

- ``AddNetwork(settings)`` — read the line layer, build the graph,
  create a spatial index.
- ``AddOrigins(settings)`` — read the point layer, snap to nearest
  edge, capture weight column.
- ``AddDestinations(settings)`` — same as origins.
- ``AddObservers(settings)`` — load observer points if
  ``observer_points_file`` is set; snap; store snapping info.
- ``AddObstacles(settings)`` — load obstacle points; snap; compute
  per-arc and per-node penalty arrays.
- ``BuildClusters(...)`` — spatial clustering for parallel processing.
- ``BuildTurnPenalties(...)`` — precompute per-junction turn costs.

Also carries helper methods engines call directly, notably
``get_obstacle_arc_penalties()`` (per-arc AB/BA penalty arrays that
stack additively with any elevation penalty).

Engines
~~~~~~~

Each engine inherits from ``Base`` and implements ``Centrality(settings)``.
It stores its results as instance arrays and calls one of ``Base``'s
export methods to write to disk. No engine reads from disk directly
— all I/O passes through ``Base``.

- **Accessibility** — Dijkstra-based; produces per-origin Reach,
  Gravity, and KNN scores. Selected when
  ``turns=False`` and ``elevation=False``.
- **AccessibilityWElevation** — same math with directional arc
  weights from z-coordinates. Selected when ``elevation=True`` and
  ``turns=False``.
- **AccessibilityWTurns** — turn-aware routing via line-graph
  transformation. Selected when ``turns=True`` (handles both turns
  and elevation).
- **Flow** — per-edge betweenness with K-alternatives and Huff
  destination choice. Selected when ``flow_engine = "k_alternatives"``.
- **AggregateFlow** — scalable gradient-overlap flow engine, and the
  default (``flow_engine = "aggregate_flow"``). Standalone: it builds
  its own DiGraph/CSR pipeline (including obstacle injection,
  elevation weights, and origin virtual nodes) rather than sharing
  Flow's, so the two flow engines evolve independently.

See :doc:`engines_vs_tools` for what belongs inside the ``urban_network_analysis`` package and
what should live outside the package.

Base
~~~~

Provides all export logic. Engines do not implement their own file
I/O — they store results on themselves and call inherited methods
like:

- ``ExportAccessibilityResults(settings, folder_prefix, file_name)``
- ``ExportFlowResult(settings, folder_prefix, file_name)``
- ``ExportODM(settings, folder_prefix, file_name, format, speed)``

Plus private writers for the auxiliary output files:

- ``_write_observer_points()``
- ``_write_obstacle_points_usage()``
- ``_write_network_nodes_flow()``
- ``_write_destinations_used_origins()``

Format is controlled by the ``output_*`` settings; the
timestamp-subfolder convention is enforced when
:py:data:`../user_guide/settings_reference:output_wStamp` = True.


Engine selection logic
----------------------

``RunAccessibility()`` picks the engine on flags:

+-------------------------------+-------------------------------+
| Settings                      | Engine used                   |
+===============================+===============================+
| ``turns = True``              | ``AccessibilityWTurns``       |
| (elevation optional)          |                               |
+-------------------------------+-------------------------------+
| ``turns = False``             | ``AccessibilityWElevation``   |
| (elevation optional)          | (falls back to symmetric      |
|                               | weights, elevation = False)   |
+-------------------------------+-------------------------------+

``RunFlow()`` dispatches on ``flow_engine``: ``"k_alternatives"``
(default) → ``Flow``, ``"aggregate_flow"`` → ``AggregateFlow``. Within
``Flow``, turn-aware routing switches on internally when
``turns = True``; there is no separate class name to worry about. Both
engines populate the same result attributes, so export and batch
compositing work identically downstream.

``RunODM()`` uses the same dispatch as ``RunAccessibility()`` but
calls the engine's ``OD_Matrix()`` method instead of ``Centrality()``.


The Project workflow
--------------------

A project is a list of Settings snapshots. Each snapshot is a
deepcopy — mutating ``una.settings`` after saving to project does not
affect the saved entries. Full details on
:doc:`../user_guide/run_batch`.

Key methods:

- ``una.SaveSettingsToProject()`` — snapshot current settings.
- ``una.ExportProjectAsJSON(folder, file_name, compact=True)`` — write
  the whole project to disk.
- ``una.ExportProjectAsCSV(file_name)`` — same as CSV.
- ``una.RunBatch(analysis, pairing_file=None)`` — execute every
  snapshot with the matching Run* method (``analysis`` is
  ``"accessibility"`` or ``"flow"``). When any row sets
  ``batch_composite_output``, UNA's built-in batch compositor collects
  each row's engine result and writes a joint composite file at the
  end.
- ``una.ConvertProject_csv_to_json(csv_file, json_file, compact=True)``
  — convert a project pairing table between formats.


Design principles
-----------------

The architecture is guided by a few consistent principles. Follow
them when extending UNA.

**Single source of truth for parameters.** Every configurable value
lives on ``Settings``. If a new feature needs a parameter, add it to
``Settings`` — not to a constructor argument on some engine.

**Engines don't mutate topology.** An engine reads the topology and
writes results to itself. It never modifies the shared topology
state, so re-running the same topology through a different engine is
always safe.

**Rebuild topology per run.** Loses a bit of runtime but eliminates
cache-invalidation bugs. Deemed a worthwhile trade-off for maintenance.

**Explicit dispatch, not implicit magic.** The Run* methods dispatch
to engines with visible ``if`` statements in ``UNA.py``, not through
a registry or a decorator. New contributors can find the dispatch
logic by reading one file.

**Every output goes through Base.** Engines expose numeric arrays;
Base handles all serialization. Adding a new output format (say,
Parquet) means editing Base, not every engine.

**One prefix per subsystem.** Flow-specific settings all start with
``flow_``; obstacle-specific settings with ``obstacle_points_``. No
setting spans multiple subsystems.

**Scope discipline — a tight, contained core.** The the ``urban_network_analysis`` package
package deliberately owns only: topology loading and CSR construction,
impedance modeling (elevation, turns, obstacles), the Dijkstra-based
accessibility and flow engines, result export, and batch execution.
Anything else — visualization, notebooks, tutorials, custom analysis
scripts, downstream pipelines — lives *outside* the package as user
scripts (``UNA_Workspace.py``, ``UNA_Batch.py``) or separate
repositories. Rule of thumb: if a new capability doesn't strictly need
to touch the CSR or the Settings dataclass, it belongs outside
the ``urban_network_analysis`` package.


Related pages
-------------

- :doc:`engines_vs_tools` — engine scope and what belongs outside
  the package.
- :doc:`adding_a_tool` — a step-by-step pattern for adding new
  analyses.
- :doc:`conventions` — coding conventions for contributions.
- :doc:`../api/una` — the public UNA class API.
