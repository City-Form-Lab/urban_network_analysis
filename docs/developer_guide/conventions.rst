Coding conventions
==================

Conventions the UNA codebase follows and expects contributors to
respect. These aren't stylistic preferences but consistency rules that
keep the codebase legible as it grows.

.. contents:: On this page
   :local:
   :depth: 1


Python style
------------

**PEP 8, with some slack.** Line length up to ~100 characters is
acceptable; strict 79 is not enforced. Use ``black``-style formatting
if you have a preference, but existing code isn't formatted with it —
don't reflow entire files gratuitously.

**Type hints on public API.** Every method on ``UNA``, ``Settings``,
and ``Topology`` that's part of the public surface should have type
hints on parameters and return values. Internal helper methods can
skip them but shouldn't lie about them.

**Docstrings on public API.** Every public method needs a docstring
that explains what it does, what its parameters mean, and what it
returns (if anything). Use Google-style or NumPy-style; both are
parsed by ``sphinx-napoleon`` in the docs build.

.. code-block:: python

   def RunAccessibility(self) -> None:
       """
       Compute per-origin accessibility scores.

       Reads settings, builds topology, dispatches to the appropriate
       engine, and exports results to disk.

       Raises:
           ValueError: If required settings are missing or invalid.
       """


Logging
-------

**Every log message goes through the topology logger.** No
``print`` statements in the ``urban_network_analysis`` package. The logger is available on
``self.topology.logger`` for engines and on ``self.logger`` for tools
that stash it in ``__init__``:

.. code-block:: python

   self.logger.log("MyTool", "Loaded 12,345 zones from disk", v=1)

**Verbosity levels:**

- ``v=0`` — errors only.
- ``v=1`` — summary messages the user should always see.
- ``v=2`` — detailed diagnostic messages, per-cluster or per-batch
  progress.
- ``v=3`` — very verbose; only useful for debugging.

Follow existing tag naming: the first argument to ``.log()`` is the
subsystem name (``"AccessibilityWElevation"``, ``"BuildAccessPoints"``)
so users can grep the log for a specific step.


Errors and validation
---------------------

**Fail loudly, early, and with useful messages.** ``Settings.Validation()``
runs first and raises ``ValueError`` with a clear message describing
what's wrong and how to fix it:

.. code-block:: python

   errors.append(
       f"flow_gravity_cap must be > 0 when "
       f"flow_decay_method='gravity_cap'; got {cap}. Set it to the "
       f"gravity value (sum of destination_weight × distance_decay "
       f"within search_radius) at which an origin's trip generation "
       f"should saturate at 100%."
   )

Prefer messages that:

1. State the observed problem.
2. Say what's expected instead.
3. Point at the setting name to change.

**Raise, don't return None.** Errors surface as exceptions, not as
sentinel return values. This lets Python's normal error-handling
machinery work.


CRS handling
------------

**Every loaded layer must match the network CRS.** ``Topology.crs``
is set when ``AddNetwork`` runs, and every subsequent ``Add*`` call
validates the loaded layer against it. Copy the pattern from
``BuildAccessPoints``:

.. code-block:: python

   if self.crs is not None:
       if gdf.crs is None:
           raise ValueError(
               f"{label} file has no CRS defined, but network CRS is {self.crs}. "
               f"Please assign CRS {self.crs} to the file before loading."
           )
       elif not gdf.crs.equals(self.crs):
           raise ValueError(
               f"{label} file CRS ({gdf.crs}) does not match network CRS ({self.crs}). "
               f"Please reproject to {self.crs} before loading."
           )


Output handling
---------------

**Every output goes through Base.** Engines don't call
``gpd.to_file`` or ``pd.to_csv`` directly. If you need a new output
type, add a private writer to ``urban_network_analysis/Engines/Base.py`` and call it
from the appropriate ``Export*`` method.

**Respect the output flags.** Every output writer checks
``settings.output_geojson``, ``settings.output_feather``,
``settings.output_csv`` and only writes the formats the user asked
for.

**Timestamp subfolder.** If ``settings.output_wStamp`` is True (the
default), every run creates a subfolder named
``<folder_prefix><timestamp>`` where ``<timestamp>`` is
``YYYY-MM-DD_HHMM``. This is standard across every Base export
method — copy the pattern, don't reinvent it.


State management
----------------

**Engines are stateless consumers of Topology.** No engine
mutates the shared topology state. If you need to modify edge weights
(e.g. for obstacle handling), do it via ``Topology`` helpers like
``get_obstacle_arc_penalties()`` — never by writing back to
``topology.network.weights``.

**Results live on the instance.** Every engine or tool stores its
outputs as attributes on ``self``. Downstream code composes at the
``UNA`` level by reading ``una.accessibility.reach``,
``una.flow.edge_flow``, etc.

**No hidden global state.** No module-level mutable dictionaries, no
class-level ``list`` defaults, no thread-local shortcuts. Everything a
run needs comes from the Settings and Topology passed in.


Settings extensions
-------------------

**One prefix per subsystem.** Flow-specific settings all start with
``flow_``. Obstacle-specific settings all start with
``obstacle_points_``. Observer settings all start with
``observer_points_``. New tools add a new prefix. No setting spans
multiple subsystems.

**Validate at Settings-load time, not at engine runtime.** Every
enum and range check goes in ``Settings.Validation()`` so errors
surface before any heavy work starts. The Literal-enforcement loop
handles enum values automatically; you only need to add
numeric-range and required-when-X-is-True conditionals.

**Compact serialization for JSON.** ``Settings.ToDict(compact=True)``
should return only fields that differ from their defaults. This keeps
saved projects small and diff-friendly. If you add a field, make sure
its default is set on the dataclass — never compute the default
inside ``__post_init__`` if you can avoid it.


Testing
-------

There's no formal test suite in the repo today. If you're adding a
new engine or tool, a minimal smoke test in a Jupyter notebook
against the Boston tutorial data is expected — one run that exercises every
enabled setting, with the output inspected visually in QGIS.

For features that touch the math directly (new decay curve, new
gravity-cap variant), a numerical test against hand-computed
expected values on a tiny synthetic network is strongly preferred.


Documentation
-------------

**New features come with docs.** Adding a new engine, tool, or point
type without documenting it is not complete. At minimum:

- A user_guide page describing what it does and when to use it.
- Cross-references from ``settings_reference.rst`` if new Settings
  fields are involved.
- An update to ``index.rst`` if a top-level page is added.
- Docstrings on new public API surfaces (autodoc picks these up).

**Code examples in docs must run.** Every ``.. code-block:: python``
in the docs should be runnable as-is against the current API.
Broken examples are a bug, not an oversight.


Versioning and changelog
------------------------

UNA follows loose semantic versioning: ``MAJOR.MINOR.PATCH``.
Patch releases fix bugs. Minor releases add features. Major
releases break the API.

When you land a feature or bug fix, add a bullet to
``docs/changelog.rst`` under the "Unreleased" section. When a
release cuts, rename that section to the released version number
with the date.


Deprecation
-----------

**Deprecate before removing.** When renaming a Settings field or a
method, keep the old name as a deprecated alias for one release. The
old name should log a warning when used, pointing at the new name.
Reference example: ``flow_max_destinations_per_origin`` is a
deprecated alias for ``flow_k_nearest_destinations`` and will be
removed in a future release.


Related pages
-------------

- :doc:`architecture` — the codebase layout these conventions apply
  to.
- :doc:`engines_vs_tools` — which category your contribution belongs
  in.
- :doc:`adding_a_tool` — the specific pattern for adding a new tool.
