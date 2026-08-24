UNA — Urban Network Analysis
=============================

**UNA** is an open-source Python package for modeling pedestrian and bicycle activity
along street networks. It computes fine-grain **accessibility** metrics —
Reach, Gravity, and KNN indices — at every origin point in a study area, and
estimates pedestrian or cycling trip **flows** between origin–destination
pairs over networks.

.. figure:: /_static/main/NYC_homes_to_amenities.jpeg
   :alt: Foot-traffic volumes in NYC
   :width: 100%

   **Estimated Foot-traffic volumes in NYC (5-6PM on weekdays).** (Sevtsuk, A., Basu, R., Liu, L., Alhassan, A., & Kollar, J. (2026). Spatial Distribution of Foot-traffic in New York City and Applications for Urban Planning. Nature Cities. https://doi.org/10.1038/s44284-025-00383-y


The package is developed at the `MIT City Form Lab <https://cityform.mit.edu/>`_
in the Department of Urban Studies and Planning at MIT, and is released under the
:doc:`MIT License <getting_started/license>`.

If you have never used UNA before, start with
:doc:`getting_started/first_analysis` — a ten-minute walkthrough that produces
your first accessibility map. If you already know the concepts and just want the
parameters, go straight to the :doc:`user_guide/settings_reference`.

These docs cover UNA version **2.5.5**.

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   getting_started/installation
   getting_started/data_conventions
   getting_started/first_analysis
   getting_started/cheat_sheet
   getting_started/license

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   user_guide/workspace_walkthrough
   user_guide/settings_reference
   user_guide/run_accessibility
   user_guide/run_flow
   user_guide/run_odm
   user_guide/run_batch
   user_guide/observers_obstacles
   user_guide/impedance_models

.. toctree::
   :maxdepth: 2
   :caption: Tutorials

   tutorials/tutorial_1_networks
   tutorials/tutorial_2_accessibility
   tutorials/tutorial_3_flow
   tutorials/tutorial_4_design_impact

.. toctree::
   :maxdepth: 2
   :caption: Concepts

   concepts/gravity_and_decay
   concepts/k_alternatives
   concepts/aggregate_flow
   concepts/elevation_turns

.. toctree::
   :maxdepth: 2
   :caption: Developer Guide

   developer_guide/architecture
   developer_guide/engines_vs_tools
   developer_guide/adding_a_tool
   developer_guide/conventions

.. toctree::
   :maxdepth: 1
   :caption: Reference

   api/una
   api/settings
   api/topology
   changelog


Citing UNA
----------

If you use UNA in academic work, please cite our recent paper in the
*Journal of Transport Geography*:

    Sevtsuk, A. (2025). Urban Network Analysis for pedestrian and bicycle
    modeling. *Journal of Transport Geography*.
    https://www.sciencedirect.com/science/article/pii/S0966692325000213


Contact & Contributions
-----------------------

The package is maintained by **Andres Sevtsuk** and **Raul Kalvo** at the
MIT City Form Lab. Bug reports, feature requests, and contributions are welcome
via the project's GitHub repository (link forthcoming). For questions about
academic collaboration or teaching engagements, contact Prof. Sevtsuk at
``asevtsuk@mit.edu``.
