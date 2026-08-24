"""UNA — Urban Network Analysis.

Accessibility and pedestrian-flow metrics on street networks.

Typical use::

    import urban_network_analysis as una

    project = una.UNA()
    project.settings.data_folder  = "Boston"
    project.settings.network_file = "network.geojson"
    ...
    project.RunFlow()

The single source of truth for the package version is ``__version__``
below; pyproject.toml reads it at build time (hatchling dynamic
version), so a release is cut by editing this one line.
"""

__version__ = "2.5.5"

from .UNA import UNA
from .Settings import Settings
from .Topology import Topology

__all__ = ["UNA", "Settings", "Topology", "__version__"]
