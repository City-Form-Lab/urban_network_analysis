Changelog
=========

This page will track changes in future UNA releases.

.. rubric:: v2.6.1

- **Network edge UID column in flow output.** The ``network_uid_column``
  setting lets you carry a unique-identifier column from your input
  network through to the flow output files. Set
  ``una.settings.network_uid_column = "__GUID"`` (or whatever the column
  is called) and the flow GeoJSON / feather / CSV will include that
  column alongside ``edge_id`` and ``flow``, so results can be joined
  back to the source network without relying on positional edge order.

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
