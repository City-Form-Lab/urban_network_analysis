UNA
===

The top-level ``UNA`` class is the entry point for every analysis. It owns
a ``Settings`` instance and a ``Topology`` instance, and dispatches to the
appropriate engine when you call one of its ``Run*`` methods.

.. autoclass:: urban_network_analysis.UNA.UNA
   :members: RunAccessibility, RunFlow, RunODM, RunBatch,
             SaveSettings, LoadSettings, SaveSettingsToProject,
             ExportProjectAsJSON, ExportProjectAsCSV,
             ConvertProject_csv_to_json, ConvertGeoJson_to_Feather,
             ClearProject, PrintSettings, DataValidation
   :undoc-members:
