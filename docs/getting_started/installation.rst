Installation
============

UNA runs on Python 3.11 and depends on ``geopandas``, ``scipy``,
``networkx``, ``numba``, and a handful of other scientific-Python
packages. The recommended setup uses **Miniconda** and a single
``una.yml`` environment file that ships with the package. It takes
about ten minutes.


Prerequisites
-------------

- **Miniconda** (Python distribution and environment manager) —
  install from `Anaconda's Miniconda page
  <https://www.anaconda.com/docs/getting-started/miniconda/main>`_.

  - macOS: choose the *Apple Silicon* ``.pkg`` if your Mac is M1/M2/M3/M4,
    otherwise the Intel installer.
  - Windows: choose *Miniconda3 Windows 64-bit* (``.exe``). During
    install, tick **Add Miniconda3 to my PATH** if asked.

  Verify by opening a Terminal (macOS) or Anaconda Prompt (Windows) and
  running:

  .. code-block:: bash

     conda --version

- **QGIS** (for viewing networks and results) —
  install from `qgis.org/download <https://qgis.org/download/>`_. QGIS
  3.44 LTR or 4.0.3 both work.

- **Visual Studio Code** (for editing and running Python scripts) —
  `code.visualstudio.com <https://code.visualstudio.com>`_. Install the
  *Python*, *Pylance*, and *Jupyter* extensions by Microsoft.


Installing UNA
--------------

Installation is two steps: create a conda environment with the heavy
geospatial dependencies, then ``pip install`` the UNA package into it.

**Step 1 — create the environment.** Download `una.yml
<https://github.com/City-Form-Lab/urban_network_analysis/blob/main/setup/una.yml>`_
(or use your clone's ``setup/`` folder), then in a Terminal (macOS) or
Anaconda Prompt (Windows):

.. code-block:: bash

   conda env create -f una.yml

That command installs every runtime dependency — ``geopandas``,
``scipy``, ``networkx``, ``numba``, ``pyogrio``, ``shapely``,
``pyarrow``, ``psutil``, ``scikit-learn``, ``threadpoolctl``,
``openblas``, and ``tqdm`` — into a new conda environment named
``una``.

Activate the environment:

.. code-block:: bash

   conda activate una

You should see ``(una)`` prepended to your shell prompt. Every UNA
command from here on assumes this environment is active.

**Step 2 — install the UNA package.** Straight from GitHub:

.. code-block:: bash

   pip install git+https://github.com/City-Form-Lab/urban_network_analysis.git

Or, if you plan to modify the code, clone the repository and install it
in editable mode so your edits take effect without reinstalling:

.. code-block:: bash

   git clone https://github.com/City-Form-Lab/urban_network_analysis.git
   pip install -e ./urban_network_analysis


Verifying the install
---------------------

Run a quick import check:

.. code-block:: bash

   python -c "import urban_network_analysis; print(urban_network_analysis.__version__)"

If the version number prints, you are ready to run your first analysis. Continue to
:doc:`first_analysis`.


Selecting the interpreter in VS Code
------------------------------------

When you open your project folder in Visual Studio Code, tell VS
Code to use the new environment's Python interpreter:

- ``Cmd + Shift + P`` on macOS, ``Ctrl + Shift + P`` on Windows
- Type ``Python: Select Interpreter``
- Pick **Python 3.11 (una)**

VS Code will now use the ``una`` environment when you run
``UNA_Workspace.py`` from the editor or the built-in terminal.


Useful conda commands
---------------------

List every conda environment on your machine:

.. code-block:: bash

   conda env list

Update the ``una`` environment after the ``una.yml`` file changes:

.. code-block:: bash

   conda env update -f una.yml --prune

Remove the environment entirely (to start over):

.. code-block:: bash

   conda env remove -n una


Troubleshooting
---------------

**"conda env create" hangs or fails.** conda's solver can take several
minutes to reconcile all dependencies. If it fails outright, try:

.. code-block:: bash

   conda config --set solver libmamba
   conda env create -f una.yml

This uses conda's newer, faster ``libmamba`` solver, which handles the
UNA dependency set more gracefully.

**"import geopandas" fails after install.** The environment probably
isn't active. Run ``conda activate una`` and try again. If it still
fails, verify with ``conda env list`` that the environment was created
successfully — you should see ``una`` in the list with a path pointing
to your Miniconda folder.

**Wrong Python version in VS Code.** VS Code sometimes remembers a
previous interpreter. Use ``Cmd/Ctrl + Shift + P → Python: Select
Interpreter`` to explicitly pick **Python 3.11 (una)**. The interpreter
choice is stored per-folder; you'll do this once for each project you
open.
