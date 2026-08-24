# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

from __future__ import annotations
import os
import sys
from datetime import datetime

# Make the package importable so autodoc can find UNA / Settings / Topology.
# Docs live at the repo root next to src/, so this points at ../src, where
# the urban_network_analysis package lives (src-layout).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "src")))

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------
project   = "UNA"
author    = "MIT City Form Lab — Andres Sevtsuk, Raul Kalvo"
copyright = f"{datetime.now().year}, {author}. Released under the MIT License."

# Version string is read from Settings if possible, else falls back.
version = "2.5.5"
release = "2.5.5"

# ---------------------------------------------------------------------------
# Sphinx extensions
# ---------------------------------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",        # pull docstrings from source
    "sphinx.ext.autosummary",    # generate API stubs
    "sphinx.ext.napoleon",       # parse Google-style / NumPy-style docstrings
    "sphinx.ext.viewcode",       # link to highlighted source
    "sphinx.ext.intersphinx",    # link to numpy / geopandas docs
    "sphinx_copybutton",         # copy button on code blocks
    "myst_parser",               # allow .md alongside .rst (for DESIGN.md)
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring  = True
napoleon_include_init_with_doc = False

intersphinx_mapping = {
    "python":    ("https://docs.python.org/3",       None),
    "numpy":     ("https://numpy.org/doc/stable/",   None),
    "geopandas": ("https://geopandas.org/en/stable/", None),
    "shapely":   ("https://shapely.readthedocs.io/en/stable/", None),
}

# Files parsed as documentation. Support .rst (default) and .md via MyST.
source_suffix = {
    ".rst": "restructuredtext",
    ".md":  "markdown",
}

# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = "UNA — Urban Network Analysis"
html_short_title = "UNA"

html_theme_options = {
    # navigation_depth=1 keeps the sidebar showing only top-level page
    # titles — one entry per .rst file. Increase to 2 if you want the
    # sidebar to expand H2 subsections within the currently-viewed page.
    "navigation_depth": 1,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "prev_next_buttons_location": "both",
    "style_external_links": True,
}

# Copy-button styling: hide the >>> and $ prompts when copying code.
copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

# ---------------------------------------------------------------------------
# Other
# ---------------------------------------------------------------------------
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
add_module_names = False    # cleaner autodoc titles
