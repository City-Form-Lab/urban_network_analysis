Engines and scope discipline
============================

UNA's codebase ships several **engines** — Accessibility, its
elevation and turn variants, Flow, and AggregateFlow. This page
explains what qualifies as an engine, the project vocabulary, and the
scope-discipline rule that decides whether a new capability belongs
inside the the ``urban_network_analysis`` package package at all.

.. note::

   Earlier drafts of UNA's design (through 2.5.1) reserved a parallel
   ``urban_network_analysis/Tools/`` folder for non-scalar analyses. That concept has
   been retired: the package core is deliberately kept small, and
   capabilities that once pointed at Tools have found simpler homes —
   route-alternatives export became a Flow engine capability
   (:py:data:`../user_guide/settings_reference:flow_output_routes`),
   and batch compositing lives inside ``UNA.py`` as part of batch
   orchestration.

.. contents:: On this page
   :local:
   :depth: 1


What an engine is
-----------------

**Engines** are Dijkstra-based network traversers that produce scalar
metrics aligned with network entities — per-edge flow, per-node flow,
per-origin score. They share a strict interface: implement
``Centrality(settings)``, store results as instance arrays, call one
of ``Base``'s export methods. Their output is always "a number
attached to every edge / node / origin" (plus, in Flow's case,
optional auxiliary outputs like route geometries that piggyback on the
same run).

Engines live in ``urban_network_analysis/Engines/``. Each engine owns
its full pipeline: the two flow engines (``Flow`` and
``AggregateFlow``) deliberately build their DiGraph/CSR machinery
independently — structurally similar code, but no shared graph build —
so they can evolve without coupling.


The project vocabulary — three terms
------------------------------------

UNA uses three terms with distinct, non-overlapping meanings:

+--------------+---------------------------------------------------------------+
| Term         | Meaning                                                       |
+==============+===============================================================+
| **Engine**   | A class that runs Dijkstra (or a path-enumeration variant)    |
|              | and produces per-edge / per-node / per-origin scalar arrays.  |
|              | Selected by ``RunAccessibility()`` / ``RunFlow()`` based on   |
|              | Settings flags. Lives in ``Engines/``.                        |
+--------------+---------------------------------------------------------------+
| **Analysis** | The top-level category flag on ``RunBatch(analysis=…)`` —     |
|              | currently ``"accessibility"`` or ``"flow"``. Tells RunBatch   |
|              | which Run* method (and therefore which engine) each row       |
|              | dispatches to.                                                |
+--------------+---------------------------------------------------------------+
| **Project**  | A saved list of ``Settings`` snapshots — "a batch of runs     |
|              | expressed as data." Materialized as ``project.json`` or       |
|              | ``pairings.csv``. It is data, not code.                       |
+--------------+---------------------------------------------------------------+

The three are orthogonal: a **project** is a batch of any **analysis**,
and each row of a project ultimately triggers one **engine**.


Scope discipline — what belongs in the ``urban_network_analysis`` package
-------------------------------------------------------------------------

The the ``urban_network_analysis`` package package is deliberately kept small and single-purpose.
It owns:

- network topology loading and CSR construction,
- impedance modeling (elevation, turns, obstacles),
- Dijkstra-based accessibility and flow engines,
- result export (feather / geojson / csv),
- batch execution across a list of ``Settings`` snapshots.

Anything else — visualization, notebooks, tutorials, custom
analysis scripts, plugin integrations, downstream data pipelines —
lives **outside** the package as user scripts (``UNA_Workspace.py``,
``UNA_Batch.py``) or as separate repositories.

**Rule of thumb:** if a new capability doesn't strictly need to touch
the CSR or the Settings dataclass, it belongs outside the ``urban_network_analysis`` package.
This keeps the package easy to review, easy to distribute, and easy
for external contributors to build tools on top of.


Decision guide for new contributions
------------------------------------

When you propose a new capability, ask in order:

1. **Is the output a per-edge / per-node / per-origin scalar computed
   on the network?** → New engine in ``Engines/``. Inherit ``Base``
   (or an existing engine when you can reuse its graph build), and
   implement ``Centrality``. See :doc:`adding_a_tool`.

2. **Is it a variant of an existing engine's routing model?** → A new
   Settings flag that dispatches to a sibling engine class, the way
   ``flow_engine`` selects between ``Flow`` and ``AggregateFlow``.

3. **Is it an additional output derivable from an engine's run?** →
   Extend the engine + a writer in ``Base`` (or the engine itself),
   the way route-alternatives export extends Flow.

4. **Is it visualization, reporting, or a workflow around UNA?** →
   Outside the package: a user script or separate repository that
   imports UNA and consumes ``una.flow`` / ``una.accessibility``
   arrays or the exported files.


Composition at the UNA level
----------------------------

Scripts and downstream code that consume engine output should compose
at the ``UNA`` level:

.. code-block:: python

   una.RunAccessibility()          # populates una.accessibility.reach etc.
   my_summary(una.accessibility)   # your script, outside the package

Engine results stay available on the instance after every run
(``una.flow.edge_flow``, ``una.accessibility.reach``), precisely so
external tools never need to reach into engine internals.


Related pages
-------------

- :doc:`architecture` — high-level layout of UNA.
- :doc:`adding_a_tool` — step-by-step pattern for adding a new
  analysis.
- :doc:`conventions` — coding conventions for contributions.
