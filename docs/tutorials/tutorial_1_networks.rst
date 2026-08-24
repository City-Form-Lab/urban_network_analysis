Tutorial 1 — Networks, concepts, and object classes
===================================================

Urban Network Analysis (UNA) models the built environment as a
**spatial network**: a system of path segments along which pedestrians
or cyclists can travel. Three things describe the network completely:
(i) the **edges** you can move along, (ii) the **nodes** where edges
meet (intersections, dead-ends), and (iii) the **point objects**
attached to edges — origins and destinations — that serve as the sources and sinks of the
analysis. This tutorial introduces the concepts and conventions.

.. contents:: On this page
   :local:
   :depth: 1


The network as a graph
----------------------

A network is a set of **edges** (line segments — typically sidewalks,
streets, paths, or corridors) connected at **nodes** (the shared
endpoints of edges). UNA reads any GIS line layer and treats every
LineString as one edge. For the engine to route trips through an
intersection, every edge that meets there must terminate at exactly
the same coordinate — they must **share an endpoint**. Curves that
merely cross visually without a shared endpoint are not connected as
far as routing is concerned.

.. figure:: /_static/tutorials/t1_image1.png
   :alt: Three topology cases
   :width: 100%

   **Three topology cases.** (a) Correctly split — all segments share
   an endpoint and trips can flow. (b) A T-junction where one curve
   dead-ends on another that wasn't split: trips cannot pass through.
   (c) Two curves crossing without a shared node — interpreted as an
   overpass/underpass, not a connection.

Before any UNA analysis, the network must be cleaned so that every
visual intersection is also a topological one — the curves split at
the crossing and sharing an endpoint there. It is normal to have
dead-ends at the boundary of the study area (cul-de-sacs, network
edge); the problem is *unintentional* dead-ends mid-network caused by
un-split crossings. Pre-cleaning the network is the user's
responsibility, typically done in QGIS, ArcGIS Pro, Rhinoceros 3D, or
CAD software.


3D structures — overpasses and underpasses
------------------------------------------

Real street networks are not perfectly flat. Bridges, tunnels,
pedestrian skybridges, and grade separations let two routes cross in
plan view without actually meeting. UNA represents these by exploiting
the topology rule above: two crossing curves that do not share an
endpoint at the crossing are interpreted as an overpass/underpass —
they cross in 2D but are not connected. No separate Z-coordinate is
needed to model this; the absence of a shared node is enough. If you
*do* have elevation data on the network (z-values), UNA can
additionally apply an **elevation penalty** to uphill walking — but
that is a cost adjustment, not a topology mechanism (see
:doc:`../concepts/elevation_turns`).


Origin and destination points
-----------------------------

Beyond the network itself, every UNA analysis takes two classes of
**point objects**:

- **Origins** — where trips begin: residential buildings, household
  locations, transit boarding points, analysis grid cells.
- **Destinations** — where trips end: jobs, shops, transit alighting
  points, parks, schools.

Each point is automatically snapped onto the nearest network edge at
runtime; you do not pre-snap them. What you do provide is an optional
**weight attribute** — a numeric column quantifying how much the point
matters: an origin's residents, a destination's jobs, floor area, or
seating capacity, etc. Weights enter the math directly: a destination with
weight 25 attracts five times as much gravity-model flow as one with
weight 5 at the same distance.

.. figure:: /_static/tutorials/t1_image3.png
   :alt: Origins and destinations with weights
   :width: 70%

   A small network with origin points (blue squares) and destination
   points (red circles). Symbol size and the number inside each marker
   show the point's weight. Both classes snap to the nearest edge
   automatically.


Edge attributes — objective vs. perceived length
------------------------------------------------

Every edge carries a **cost attribute** — the value the routing
algorithm minimizes when computing shortest paths. By default this is
the segment's geometric length in meters. But the cost column does not
have to be geometric length: you can substitute a **perceived
length** — meters as the pedestrian experiences them, not as the
surveyor measures them. Perceived lengths are a powerful way of
folding route attributes (sidewalk presence and width, trees, ground
floor businesses, traffic noise, heat) into segment costs.

.. figure:: /_static/tutorials/t1_image5.png
   :alt: Objective vs perceived length
   :width: 90%

   Two routes between an origin O and a destination D. The arterial is
   shorter in ground meters but feels longer because of noise, traffic,
   and pollution. The park path is geometrically longer but feels
   shorter. With perceived length as the cost attribute, the engine
   prefers the park path even though it is physically further.

Perceived length is typically computed by multiplying each segment's
geometric length by a **quality factor** derived from the street's
pedestrian environment. A pleasant tree-lined street might score 0.8
(each meter feels like 0.8 m); a noisy arterial 1.4. UNA does not
prescribe how you derive the factors — typically from pedestrian
route-choice studies estimating Willingness To Walk, Distance
Equivalence, or Value of Distance for different route attributes — but
it routes correctly on whatever cost column you supply. Configure it
via :py:data:`../user_guide/settings_reference:network_weight_column`,
which defaults to ``"Geometric"`` (ground length).

.. seealso::

   For literature reference on measured perceived cost factors of
   different street attributes for pedestrians, see:

   - Sevtsuk, A., Li, X., Basu, R., & Kalvo, R. (2021). A big data
     approach to understanding pedestrian route choice preferences —
     Evidence from San Francisco. *Travel Behaviour and Society*,
     25(October), 41–51.
     https://doi.org/10.1016/j.tbs.2021.05.010
   - Basu, R., & Sevtsuk, A. (2022). How do street attributes affect
     willingness-to-walk? City-wide pedestrian route choice analysis
     using big data from Boston and San Francisco. *Transportation
     Research Part A*, 163, 1–19.
     https://doi.org/10.1016/j.tra.2022.06.007
   - Sevtsuk, A., & Basu, R. (2022). The role of turns in pedestrian
     route choice: a clarification. *Journal of Transport Geography*.
     https://doi.org/10.1016/j.jtrangeo.2022.103392
   - Basu, R., Colaninno, N., Alhassan, A., & Sevtsuk, A. (2024). Hot
     and Bothered: Exploring the Effect of Heat on Pedestrian Behavior
     and Accessibility. *Cities*, 155.
     https://doi.org/10.1016/j.cities.2024.105435


Overview of UNA analyses
------------------------

With the network, point objects, and edge weights described above,
UNA's engines can (i) compute fine-grain pedestrian or bike
accessibilities, (ii) output origin-destination cost matrices, and
(iii) estimate pedestrian or cycling flows over networks.

**Accessibility — "what can each origin reach?"** UNA computes a
numeric accessibility score at every origin point, summarizing how
well-connected that location is to surrounding destinations within a
chosen walking or biking radius. Three indices are provided:

- **Reach** counts the number (or summed weights) of destinations
  within a radius — a "cumulative opportunities" index.
- **Gravity** weights each destination by an inverse function of its
  network distance, so nearby destinations contribute more.
- **KNN accessibility** looks at the *k* nearest destinations of each
  type (e.g. the 3 closest grocery stores, the closest school) and can
  combine them into one composite score (using the UNA_Batch function) — conceptually similar to a
  WalkScore-style index.

**Flow estimation — "which streets are the trips likely to use?"**
Given trips from origins to destinations, UNA's **flow** analysis
computes how many pass through each network edge, producing a
per-segment flow estimate. OD pairs can represent any trip purpose:
homes to schools, homes to transit, employees to lunch, tourists to
landmarks. The result is a network map where every edge carries its
expected traffic load — the basis for identifying high-traffic
corridors, targeting pedestrian-friendly investment, and revealing
which routes would gain or lose flow with changes to the built
environment. Flow analysis can also output flow at network nodes, and return directional estimates for segments (A>B versus B>A, where A refers to the start of segment geometry).

.. figure:: /_static/tutorials/t1_image7.png
   :alt: Accessibility vs flow outputs
   :width: 100%

   The two families of UNA outputs. Left: per-origin accessibility
   scores (color = score; one number per point). Right: per-edge flow
   (thickness and color = trip volume; one number per edge).

**From estimates to calibrated predictive models.** Estimated flows
are descriptive on their own, but become *predictive* when calibrated
against observed pedestrian or bicycle counts — a morning of intercept
surveys or a week of automated counter data. Once calibrated, the same
model answers scenario questions: how would volumes change if nearby
land uses changed, or if a street upgrade changed its perceived
length? These are the "what-if" questions planning agencies need.

.. seealso::

   For examples of pedestrian volume models with calibrated flows based
   on observed pedestrian counts, see:

   - Sevtsuk, A., Basu, R., Liu, L., Alhassan, A., & Kollar, J. (2026).
     Spatial Distribution of Foot-traffic in New York City and
     Applications for Urban Planning. *Nature Cities*.
     https://doi.org/10.1038/s44284-025-00383-y
   - Sevtsuk, A., Kollar, J., Pratama, D., Haddad, J., Basu, R.,
     Alhassan, A., Chancey, B., Makhlouf, R., Halabi, J., &
     Abou-Zeid, M. (2024). Pedestrian-Oriented Development in Beirut:
     A Framework for Estimating Urban Design Impacts on Pedestrian
     Flows through Modeling, Participatory Design, and Scenario
     Analysis. *Cities*.
     https://doi.org/10.1016/j.cities.2024.104927
   - Sevtsuk, A., Basu, R., & Chancey, B. (2021). We shape our
     buildings, but do they then shape us? A longitudinal analysis of
     pedestrian flows and development activity in Melbourne.
     *PLOS ONE*.
     https://doi.org/10.1371/journal.pone.0257534
   - Sevtsuk, A. (2021). Estimating pedestrian flows on street
     networks: revisiting the betweenness index. *Journal of the
     American Planning Association*, 87(4), 512–526.
     https://doi.org/10.1080/01944363.2020.1864758


Summary
-------

The network is a graph of edges joined at shared endpoints; crossings
without shared endpoints become overpasses/underpasses; weighted
origins and destinations are point objects that snap to edges; and the
edge cost can be either ground length or a perceived length capturing
the pedestrian experience of the street. These four concepts are the
foundation for every accessibility and flow analysis in
:doc:`tutorial_2_accessibility`, :doc:`tutorial_3_flow`, and
:doc:`tutorial_4_design_impact`.
