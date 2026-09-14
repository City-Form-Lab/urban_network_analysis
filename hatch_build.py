"""Hatchling build hook: bake the date of the last git commit into the package.

At build/install time (including ``pip install git+https://...``, which
clones the repo first), this hook asks git for the date of the most
recent commit and writes it to
``src/urban_network_analysis/_build_info.py``. The package's ``about()``
function reads it back at runtime, so installed users can see how
current their copy is without touching git themselves.

If git is unavailable (e.g. building from a plain source archive), the
file records "unknown" and ``about()`` falls back gracefully.
"""

from __future__ import annotations

import os
import subprocess

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        commit_date = "unknown"
        try:
            commit_date = subprocess.check_output(
                ["git", "log", "-1", "--format=%cs"],  # e.g. 2026-09-14
                cwd=self.root,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip() or "unknown"
        except Exception:
            pass

        path = os.path.join(
            self.root, "src", "urban_network_analysis", "_build_info.py"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "# Auto-generated at build time by hatch_build.py — do not edit.\n"
                f'__commit_date__ = "{commit_date}"\n'
            )
