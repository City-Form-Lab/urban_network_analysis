Adding a new analysis
=====================

This page walks through the concrete steps to extend UNA with a new
analysis. Read :doc:`engines_vs_tools` first — it decides whether your
capability belongs inside the ``urban_network_analysis`` package as an engine, as an extension of
an existing engine, or outside the package as a user script.

.. note::

   Earlier versions of this page described a ``urban_network_analysis/Tools/``
   template. That extension point has been retired (see
   :doc:`engines_vs_tools`); the patterns below reflect the current
   architecture.

.. contents:: On this page
   :local:
   :depth: 1


The engine template
-------------------

An engine is a class in ``urban_network_analysis/Engines/`` with three
responsibilities: constructor takes the Topology; ``Centrality(settings)``
does the work; results are stored on ``self`` and written through
``Base``'s export methods.

.. code-block:: python

   # source/Engines/MyEngine.py

   from .Base import Base

   class MyEngine(Base):
       """One-paragraph description of what this engine computes."""

       def __init__(self, topology):
           super().__init__(topology)
           self.my_result = None      # populated by Centrality()

       def Centrality(self, settings):
           """Run the analysis. Stores result arrays on self."""
           # 1. Resolve parameters (trust Settings — no getattr guards).
           # 2. Build / reuse the graph.
           # 3. Compute.
           # 4. Assign numpy arrays to self.
           ...

Key conventions to lock in:

- **Single computation method: ``Centrality``.** Uniform across every
  engine. Don't invent per-engine verbs — new users will hunt for
  them.
- **Constructor takes ``topology`` only.** Settings flow in through
  ``Centrality(settings)``, not the constructor.
- **Results live on ``self``.** Same pattern as ``self.reach`` and
  ``self.edge_flow``. Downstream code (batch compositing, user
  scripts) consumes them after the run.
- **Export goes through ``Base``.** Engines expose numeric arrays;
  ``Base`` owns serialization and the output-folder / timestamp
  conventions.
- **Trust Settings.** Every parameter the engine reads is declared in
  ``Settings.py`` with a default, and ``settings.Validation()`` runs
  before the engine. No ``getattr(settings, "foo", default)`` guards,
  no ``settings.foo or "default"`` fallbacks — if a field is missing,
  the fix is in ``Settings.py``, not in the engine.


Inherit when you can — the AggregateFlow example
------------------------------------------------

If your engine shares graph construction with an existing one,
inherit from it and override only what genuinely differs. This is the
pattern ``AggregateFlow`` uses — the reference example for a new flow
engine:

.. code-block:: python

   class AggregateFlow(Flow):
       # reuses Flow's _prepare_params, DiGraph/CSR build,
       # obstacle injection, elevation weights, connectors …

       def _build_digraph(self, ...):
           super()._build_digraph(...)
           # add engine-specific graph elements

       def Centrality(self, settings):
           # engine-specific computation; populates the same
           # result attributes Flow does (edge_flow, edge_flow_AB, …)
           ...

Populating the *same result attributes* as the sibling engine is what
makes the rest of the system (export, batch compositing) work without
any downstream changes.


Settings convention
-------------------

Give the new analysis a single flat prefix in ``Settings``:

.. code-block:: python

   # In Settings.py
   myengine_method: Literal["fast", "exact"] = "fast"
   myengine_zones_file: str | None = None

**Flat, not nested.** UNA's CSV/JSON project ingestion
(:doc:`../user_guide/run_batch`) reads flat key→value mappings.
Sub-dataclasses would force nested JSON or dotted column names in
CSVs — more brittle and harder to author by hand.

**Validation lives in Settings.** Add enum checks, range checks, and
required-when conditionals to ``Settings.Validation()``, same as
``flow_decay_method``. Engines then trust the values outright.

**Dispatch flags are Settings too.** A routing-model variant of an
existing analysis should be a Literal flag that selects between
sibling engines — the way ``flow_engine`` chooses ``Flow`` vs
``AggregateFlow`` — rather than a separate ``Run*`` method.


How UNA wraps an engine
-----------------------

The wrapper method in ``urban_network_analysis/UNA.py`` follows the same visible
shape as ``RunAccessibility`` / ``RunFlow``:

.. code-block:: python

   def RunMyAnalysis(self):
       self.settings.Validation()
       self.topology.AddNetwork(self.settings)
       # + AddOrigins / AddDestinations as needed

       self.myengine = MyEngine(self.topology)
       self.myengine.Centrality(self.settings)
       self.myengine.ExportMyResult(
           self.settings,
           folder_prefix="myanalysis_",
           file_name=self.settings.output_file_name,
       )

The pattern stays the same everywhere: validate → build topology →
instantiate → compute → export. Dispatch is explicit ``if`` logic in
``UNA.py`` — no registries, no decorators.


Batch support
-------------

If the analysis should work with the project workflow
(:doc:`../user_guide/run_batch`), extend ``UNA.RunBatch``'s dispatch.
Because ``ApplyRow`` uses field-name matching, any new settings can be
authored in a project CSV without extra plumbing.


When it doesn't belong in the ``urban_network_analysis`` package
----------------------------------------------------------------

Per the scope-discipline rule (:doc:`engines_vs_tools`), a capability
that doesn't need to touch the CSR or the Settings dataclass should be
a user script instead. The pattern:

.. code-block:: python

   # my_analysis.py — outside source/
   from urban_network_analysis import UNA

   una = UNA()
   una.settings.data_folder  = r"Boston"
   # … configure …
   una.RunFlow()

   flows = una.flow.edge_flow          # numpy array, ready to use
   # … your visualization / summary / pipeline code …

This keeps the package core reviewable while giving external code
full access to results.


Checklist for a new engine PR
-----------------------------

Before you open a pull request:

- [ ] Settings fields added with one flat prefix, with defaults.
- [ ] ``Settings.Validation()`` extended with the new Literal / range
  / required-when checks.
- [ ] Engine class in ``urban_network_analysis/Engines/`` with ``__init__`` and
  ``Centrality``; inherits ``Base`` or a sibling engine.
- [ ] Results stored as instance arrays; export through ``Base``.
- [ ] UNA wrapper method (or dispatch branch) in ``urban_network_analysis/UNA.py``.
- [ ] ``RunBatch`` dispatch updated if relevant.
- [ ] No ``getattr(settings, …)`` guards — trust Settings.
- [ ] Docstrings on the class and public methods.
- [ ] Documentation: settings entries in
  :doc:`../user_guide/settings_reference`, a concepts page if the
  method warrants one, cross-links from ``index.rst``.


Related pages
-------------

- :doc:`architecture` — the overall codebase layout.
- :doc:`engines_vs_tools` — deciding where a new capability belongs.
- :doc:`conventions` — coding conventions.
