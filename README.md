# UNA — Urban Network Analysis

UNA is a free, open-source Python package for computing **accessibility**
and **pedestrian-flow** metrics on spatial networks, developed by
Andres Sevtsuk and Raul Kalvo at the
[MIT City Form Lab](https://cityform.mit.edu). It loads a spatial network,
weighted origin and destination point layers, and optionally obstacle or observed point layers, and runs analyses to answer two families of questions:

- **Accessibility** — what can each origin reach on the network within a
  given walking range? Metrics: Reach (cumulative opportunities),
  exponential and logistic gravity, and K-nearest-neighbor access.
- **Flow** — how many trips does each street segment carry between
  origins and destinations? Two route-choice engines: `aggregate_flow`
  (the default — spreads each OD pair's trips over the full envelope of
  viable routes; scales to regional and state-wide models) and
  `k_alternatives` (enumerates discrete alternative paths; supports
  turn-aware routing and route-geometry export. Suitable for neighborhood scale analyses).

Impedance can go beyond geometric length: custom "perceived length"
edge costs, uphill elevation penalties, turn penalties, and obstacle
penalties. Hot loops are Numba-compiled and searches are radius-bounded,
which keeps large-scale runs feasible. Results export to GeoJSON,
Feather, and CSV for mapping in QGIS or any GIS.

## Installation

Two steps — a conda environment for the geospatial dependencies, then
the package itself. Requires Python ≥ 3.11.

```bash
# 1. environment (una.yml is in this repo's setup/ folder)
conda env create -f setup/una.yml
conda activate una

# 2. the package
pip install git+https://github.com/asevtsuk/urban_network_analysis.git
```

To modify the code, clone and install in editable mode instead:

```bash
git clone https://github.com/asevtsuk/urban_network_analysis.git
pip install -e ./urban_network_analysis
```

## Quick start

```python
import urban_network_analysis as una

project = una.UNA()

project.settings.data_folder       = "Boston"     # this repo's docs/Boston has tutorial data
project.settings.network_file      = "20260703_PercLenNetwork_InnerCore.geojson"
project.settings.origins_file      = "Cambridge_building_centroids.geojson"
project.settings.destinations_file = "MA_bus_stops.geojson"

project.settings.search_radius             = 500
project.settings.destination_weight_column = "weekly_departures"

project.RunAccessibility()
```

Ready-to-edit driver scripts are in [`examples/`](examples/) —
`UNA_Workspace.py` for single analyses and `UNA_Batch.py` for running
many analyses from a pairings CSV.

## Documentation and tutorials

Full documentation lives in [`docs/`](docs/): installation, a
first-analysis walkthrough, the complete settings reference, concept
pages on the gravity/decay models and both flow engines, and four
hands-on tutorials (networks, accessibility, flow, and design-impact
analysis). The tutorial dataset — a pedestrian network of the Boston
inner core with building, transit, and amenity layers — ships in
[`docs/Boston/`](docs/Boston/), so the tutorials run out of the box.

Build the docs locally with Sphinx:

```bash
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

## Versioning

The package version is defined once, in
`src/urban_network_analysis/__init__.py` (`__version__`), and read by
the build system at install time. Check yours with:

```python
import urban_network_analysis
print(urban_network_analysis.__version__)
```

## Citing UNA

If you use UNA in academic work, please cite:

- Sevtsuk, A., & Mekonnen, M. (2012). Urban network analysis: A new
  toolbox for ArcGIS. *Revue Internationale de Géomatique*, 22(2),
  287–305. https://doi.org/10.3166/RIG.22.287-305
- Sevtsuk, A. (2021). Estimating pedestrian flows on street networks:
  Revisiting the betweenness index. *Journal of the American Planning
  Association*, 87(4). https://doi.org/10.1080/01944363.2020.1864758
- Sevtsuk, A., & Kalvo, R. (2024). Modeling pedestrian activity in cities 
  with urban network analysis. Environment and Planning B: Urban Analytics 
  and City Science, 52(2). https://doi.org/10.1177/23998083241261766
- Sevtsuk, A., & Alhassan, A. (2025). Madina Python package: Scalable
  urban network analysis for modeling pedestrian and bicycle trips in
  cities. *Journal of Transport Geography*, 123, 104130.
  https://doi.org/10.1016/j.jtrangeo.2025.104130

## License

MIT — see [LICENSE](LICENSE).
