Topology
========

The ``Topology`` class holds the network graph plus origin, destination,
observer, and obstacle point layers. It is owned by UNA and rebuilt on
every ``Run*`` call.

.. autoclass:: urban_network_analysis.Topology.Topology
   :members: AddNetwork, AddOrigins, AddDestinations,
             AddObservers, AddObstacles,
             BuildClusters, BuildTurnPenalties, Evaluate,
             get_obstacle_arc_penalties, get_partial_edge_corrections
   :undoc-members:
