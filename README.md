# urban-network-analysis (UNA)

Installable package build of UNA — accessibility and pedestrian-flow
metrics on street networks. Code version una-2.5.5.

## Install (development / testing)

From the folder containing `una-pkg`, in the `una` conda environment:

```bash
pip install -e ./una-pkg
```

Editable install: the environment imports directly from this folder, so
edits take effect on the next run with no reinstall. Requires Python >= 3.11.

## Use

```python
import urban_network_analysis as una

print(una.__version__)

project = una.UNA()
project.settings.data_folder  = "Boston"
project.settings.network_file = "network.geojson"
# ... same Settings API as the source-folder version ...
project.RunFlow()
```

This coexists with the `una-2.5.5` folder workflow — `from source
import UNA` and `import urban_network_analysis` are unrelated names, so
existing scripts keep working unchanged while the package is tested.

## Versioning

The version is encoded once, in
`src/urban_network_analysis/__init__.py` (`__version__ = "2.5.5"`).
`pyproject.toml` reads it from there at build time (hatchling dynamic
version). To cut a release: edit `__version__`, commit, `git tag v2.5.6`.
See the comment block in `pyproject.toml`.

## Layout

```
una-pkg/
├── pyproject.toml
├── README.md
├── examples/              # driver-script templates — copy anywhere, edit paths, run
│   ├── UNA_Workspace.py   # single analysis (engine comparison example)
│   └── UNA_Batch.py       # pairings-CSV batch driver
└── src/
    └── urban_network_analysis/
        ├── __init__.py        # __version__ + re-exports (UNA, Settings, Topology)
        ├── UNA.py             # public API: RunFlow / RunAccessibility / RunBatch
        ├── Settings.py
        ├── Topology.py
        ├── Logger.py
        └── Engines/           # Accessibility*, Flow, AggregateFlow, Base
```
