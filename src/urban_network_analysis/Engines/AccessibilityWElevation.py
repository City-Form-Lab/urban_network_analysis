### use importlib to impoer as needed. Reduce Dependencies to strictly necessary (Used by default components) and Optioa (used by optional components)l..
from __future__ import annotations
from cProfile import label
import os
os.environ['USE_PYGEOS'] = '0'
import math

import sys
from pathlib import Path
import time

import psutil
import heapq
import shapely


# This enables type hints to find elements further down in the code.
from ast import Tuple
from heapq import heappush, heappop

from typing import Dict, Self, TypeAlias #, TypedDict
from collections.abc import Collection, Callable
#from sklearn.cluster import KMeans
from concurrent.futures import ThreadPoolExecutor
from shapely.strtree import STRtree

import networkx as nx
import concurrent.futures as fr
import multiprocessing as mp
import geopandas as gpd
import numpy as np
import pandas as pd
import numba as nb

from ..Logger import Logger
from ..Topology import Topology, Network, AccessPoints
from .Base import Base

# ============================================================================
# AccessibilityWElevation Graph Engine Constants
# ============================================================================
DEFAULT_DESTINATION_NAME = 'destination'

# Numba JIT compilation settings
NUMBA_PARALLEL = False
NUMBA_CACHE = True
NUMBA_NOGIL = True
NUMBA_FASTMATH = True

# ============================================================================
# AccessibilityWElevation Jitted Functions
# ============================================================================

@nb.njit(
    parallel=NUMBA_PARALLEL,
    cache=NUMBA_CACHE,
    nogil=NUMBA_NOGIL,
    fastmath=NUMBA_FASTMATH,
)
def reach_gravity_knn_access(d_distance, d_weights, cutoff, gravity_beta, gravity_plateau, gravity_logistic_midpoint, knn_gravity_plateau, knn_weights, knn_gravity_beta, knn_decay, knn_gravity_logistic_midpoint, gravity_growth_rate, knn_gravity_growth_rate):
    """Calculate reach, gravity, and KNN accessibility metrics."""
    n_reach_filter = np.where(d_distance <= cutoff)[0]
    n_distances = d_distance[n_reach_filter]
    n_weights = d_weights[n_reach_filter]

    # Reach is simply the count of destinations within cutoff, weighted by destination weights
    reach = n_weights.sum()

    #Gravity is the weighted sum of destinations with distance decay. For exponential decay, this is simply sum of weights * exp(-beta * distance). For logistic decay, this is sum of weights * (1 - 1 / (1 + exp(-growth_rate * (distance - plateau - midpoint))))
    gravity_exponential = (n_weights / np.exp(gravity_beta * np.maximum(0, n_distances - gravity_plateau))).sum()

    gravity_logistic = (n_weights * (1 - 1 / (1 + np.exp(-gravity_growth_rate * (n_distances - gravity_plateau - gravity_logistic_midpoint))))).sum()

    # KNN
    # result should be sum of n_weights * knn_weight / decay_function(n_distances)

    knn = min(n_distances.shape[0], knn_weights.shape[0])

    if knn == 0:
        knn_access = 0.0
    else:
        idx                         = np.argsort(n_distances) # we sort the distances to find the nearest neighbors. We can then apply the knn_weights to the sorted weights and distances.
        n_distances_sorted          = n_distances[idx]
        n_weights_sorted            = n_weights[idx]

        n_distances_cropped         = n_distances_sorted[:knn]
        n_weights_cropped           = n_weights_sorted[:knn]
        knn_coefficients_cropped    = knn_weights[:knn]

        if knn_decay == "exponential":
            knn_access      = ((knn_coefficients_cropped * n_weights_cropped) * np.exp(-gravity_beta * np.maximum(0, n_distances_cropped-gravity_plateau))).sum()
        elif knn_decay == "logistic":
             knn_access      = ((knn_coefficients_cropped * n_weights_cropped) * (1 - 1 / (1 + np.exp(-gravity_growth_rate * (n_distances_cropped - gravity_plateau - gravity_logistic_midpoint))))).sum()
        else:
            knn_access = (knn_coefficients_cropped * n_weights_cropped).sum()

    return reach, gravity_exponential, gravity_logistic, knn_access

@nb.njit(
    parallel=NUMBA_PARALLEL,
    cache=NUMBA_CACHE,
    nogil=NUMBA_NOGIL,
    fastmath=NUMBA_FASTMATH,
)
def compact_vector_node_view_scope(
    o_terminal_idxs,
    o_terminal_weights,
    adjacency_pointer,
    adjacency_vector,
    adjacency_vector_weights,
    adjacynct_vector_network_node,
    cutoff,
    d_count,
):
    """Dijkstra's algorithm for compact vector-based graph traversal."""
    nd_node_count = d_count + adjacency_pointer.shape[0] - 1
    o_idx_start = o_terminal_idxs[0]
    o_idx_end = o_terminal_idxs[1]
    o_idx_start_weight = o_terminal_weights[0]
    o_idx_end_weight = o_terminal_weights[1]

    o_scope_weights = np.ones(nd_node_count, dtype=o_terminal_weights.dtype) + cutoff
    o_scope_pred = np.empty(0, dtype=o_terminal_idxs.dtype)

    # Add start node segment terminals to seen with their weights
    o_scope_weights[o_idx_start] = o_idx_start_weight
    o_scope_weights[o_idx_end] = o_idx_end_weight

    queue = [(o_idx_start_weight, o_idx_start)]
    weight, node = heappop(queue)

    if o_idx_end_weight < cutoff:
        heappush(queue, (o_idx_end_weight, o_idx_end))

    if o_idx_start_weight < cutoff:
        heappush(queue, (o_idx_start_weight, o_idx_start))

    while queue:
        weight, node = heappop(queue)

        node_start_pointer = adjacency_pointer[node]
        node_end_pointer = adjacency_pointer[node + 1]
        weights_neighbors = adjacency_vector_weights[node_start_pointer:node_end_pointer] + weight

        queue_neighbors = np.nonzero(
            (weights_neighbors <= cutoff)
            & (weights_neighbors < o_scope_weights[adjacency_vector[node_start_pointer:node_end_pointer]])
        )[0]
        
        for i in queue_neighbors:
            neighbor_weight = weights_neighbors[i]
            neighbor_node = adjacency_vector[node_start_pointer + i]
            o_scope_weights[neighbor_node] = neighbor_weight
            if adjacynct_vector_network_node[node_start_pointer + i]:
                if adjacency_pointer[neighbor_node + 1] - adjacency_pointer[neighbor_node] > 1:
                    heappush(queue, (neighbor_weight, neighbor_node))

    return o_scope_weights, o_scope_pred


@nb.njit(
    parallel=NUMBA_PARALLEL,
    cache=NUMBA_CACHE,
    nogil=NUMBA_NOGIL,
    fastmath=NUMBA_FASTMATH,
)
def adjust_destination_distances(
    scope_weights,
    d_terminal_idxs,
    d_terminal_weights,
    d_count,
):
    """
    Adjust destination distances to account for their position on edges.
    
    For each destination, calculates the actual distance by finding the minimum
    distance to both terminal nodes plus the distance from terminal to destination.
    """
    n_count = scope_weights.shape[0] - d_count
    d_distances = np.empty(d_count, dtype=scope_weights.dtype)
    
    for i in range(d_count):
        start_node = d_terminal_idxs[i, 0]
        end_node = d_terminal_idxs[i, 1]
        start_weight = d_terminal_weights[i, 0]
        end_weight = d_terminal_weights[i, 1]
        
        dist_to_start = scope_weights[start_node] + start_weight
        dist_to_end = scope_weights[end_node] + end_weight
        
        d_distances[i] = min(dist_to_start, dist_to_end)
    
    return d_distances


@nb.njit(
    parallel=True,
    cache=NUMBA_CACHE,
    nogil=NUMBA_NOGIL,
    fastmath=NUMBA_FASTMATH,
)
def od_compact_vector_node_view_scope(
    o_terminal_idxs,
    o_terminal_weights,
    adjacency_pointer,
    adjacency_vector,
    adjacency_vector_weights,
    adjacynct_vector_network_node,
    cutoff,
    d_count,
    d_terminal_idxs,
    d_terminal_weights,
):
    """Calculate full OD distance matrix."""
    o_count = o_terminal_idxs.shape[0]
    n_count = adjacency_pointer.shape[0] - 1

    od_distances = np.empty((o_count, d_count), dtype=adjacency_vector_weights.dtype)
    for i in nb.prange(o_count):
        scope_weights = compact_vector_node_view_scope(
            o_terminal_idxs[i],
            o_terminal_weights[i],
            adjacency_pointer,
            adjacency_vector,
            adjacency_vector_weights,
            adjacynct_vector_network_node,
            cutoff,
            d_count,
        )[0]
        # Adjust for destination positions on edges
        od_distances[i] = adjust_destination_distances(
            scope_weights,
            d_terminal_idxs,
            d_terminal_weights,
            d_count,
        )
    return od_distances


@nb.njit(
    parallel=True,
    cache=NUMBA_CACHE,
    nogil=NUMBA_NOGIL,
    fastmath=NUMBA_FASTMATH,
)
def integrated_scope_access(
    o_terminal_idxs,
    o_terminal_weights,
    adjacency_pointer,
    adjacency_vector,
    adjacency_vector_weights,
    adjacynct_vector_network_node,
    d_terminal_idxs,
    d_terminal_weights,
    d_weights,
    gravity_beta,
    gravity_plateau,
    gravity_logistic_midpoint,
    gravity_growth_rate,
    knn_decay,
    knn_weights,
    cutoff
):
    """Calculate reach, gravity, and KNN accessibility for all origins."""

    o_count = o_terminal_idxs.shape[0]
    n_count = adjacency_pointer.shape[0] - 1
    d_count = d_terminal_weights.shape[0]

    reach = np.empty(o_count, dtype=o_terminal_idxs.dtype)
    gravity_exponential = np.empty(o_count, dtype=adjacency_vector_weights.dtype)
    gravity_logistic = np.empty(o_count, dtype=adjacency_vector_weights.dtype)

    knn_access = np.empty(o_count, dtype=adjacency_vector_weights.dtype)

    for o_pos in nb.prange(o_count):
        scope_weights = compact_vector_node_view_scope(
            o_terminal_idxs[o_pos],
            o_terminal_weights[o_pos],
            adjacency_pointer,
            adjacency_vector,
            adjacency_vector_weights,
            adjacynct_vector_network_node,
            cutoff,
            d_count,
        )[0]
        
        # Adjust for destination positions on edges
        d_distance = adjust_destination_distances(
            scope_weights,
            d_terminal_idxs,
            d_terminal_weights,
            d_count,
        )

        reach[o_pos], gravity_exponential[o_pos], gravity_logistic[o_pos], knn_access[o_pos] = reach_gravity_knn_access(
            d_distance=d_distance,
            d_weights=d_weights,
            cutoff=cutoff,
            gravity_beta=gravity_beta,
            gravity_plateau=gravity_plateau,
            gravity_logistic_midpoint=gravity_logistic_midpoint,
            gravity_growth_rate=gravity_growth_rate,
            knn_gravity_plateau=gravity_plateau,
            knn_weights=knn_weights,
            knn_gravity_beta=gravity_beta,
            knn_decay=knn_decay,
            knn_gravity_logistic_midpoint=gravity_logistic_midpoint,
            knn_gravity_growth_rate=gravity_growth_rate,
        )
    return reach, gravity_exponential, gravity_logistic, knn_access



class AccessibilityWElevation(Base):
    """
    Accessibility metrics calculator with elevation-aware edge weights.

    Directional weights are computed internally from the node Z values stored
    in topology.network.z.  No prior call to BuildTopologyWElevation() is
    required.  If Z values are absent the engine falls back to symmetric
    weights and logs a warning.

    elevation_coefficient controls how strongly uphill travel is penalised:
        cost(A→B) = base_weight + elevation_coefficient * max(0, z_B - z_A)
        cost(B→A) = base_weight + elevation_coefficient * max(0, z_A - z_B)
    A value of 0.0 produces the same result as Accessibility.

    Results are stored separately in instance variables:
    - reach: Count of destinations within search radius
    - gravity_exponential: Weighted sum with exponential distance decay
    - gravity_logistic: Weighted sum with logistic distance decay
    - knn_access: K-nearest neighbors metric
    - od_matrix: Origin-destination distance matrix (if requested)
    """
    
    # ========================================================================
    # Minimal CompactNodeView Implementation (nested class)
    # ========================================================================
    class CompactNodeView:
        """Minimal CompactNodeView implementation for accessibility calculations."""
        
        def __init__(self, dtype=None, max_error=None):
            """Initialize graph engine attributes."""
            self.dtype = dtype
            self.max_error = max_error
            
            # Compact adjacency vectors
            self.adjacency_pointer = None
            self.adjacency_vector = None
            self.adjacency_vector_weights = None
            self.adjacynct_vector_network_node = None
            
            # Origin/destination data
            self.o_terminal_idxs = None
            self.o_terminal_weights = None 
            self.d_count = None
            self.d_terminal_idxs = None
            self.d_terminal_weights = None
            
            # Node lists for compatibility
            self.node_lists = {}
        
        def o_scope(self, o_idx, search_radius):
            """Calculate OD distance matrix."""
            return od_compact_vector_node_view_scope(
                self.o_terminal_idxs,
                self.o_terminal_weights,
                self.adjacency_pointer,
                self.adjacency_vector,
                self.adjacency_vector_weights,
                self.adjacynct_vector_network_node,
                search_radius,
                self.d_count,
                self.d_terminal_idxs,
                self.d_terminal_weights,
            )

        def o_access(self, o_idx, settings, d_weights):
            """Calculate reach, gravity, and KNN accessibility metrics."""
            return integrated_scope_access(
                o_terminal_idxs=self.o_terminal_idxs,
                o_terminal_weights=self.o_terminal_weights,
                adjacency_pointer=self.adjacency_pointer,
                adjacency_vector=self.adjacency_vector,
                adjacency_vector_weights=self.adjacency_vector_weights,
                adjacynct_vector_network_node=self.adjacynct_vector_network_node,
                d_terminal_idxs=self.d_terminal_idxs,
                d_terminal_weights=self.d_terminal_weights,
                d_weights=d_weights,
                gravity_beta=settings.gravity_beta,
                gravity_plateau=settings.gravity_plateau,
                gravity_logistic_midpoint=settings.gravity_logistic_midpoint,
                gravity_growth_rate=settings.gravity_decay_constant / settings.gravity_logistic_midpoint,
                knn_decay=settings.knn_decay,
                knn_weights=settings.knn_weights,
                cutoff=settings.search_radius,
            )
    
    # Minimal element list for node data storage
    class MinimalElementList:
        """Minimal element list storing node weight array."""
        
        def __init__(self, weights):
            self.node_weight = weights
    
    # ========================================================================
    # Result storage
    # ========================================================================
    reach: np.ndarray = None
    gravity: np.ndarray = None
    gravity_exponential: np.ndarray = None
    gravity_logistic: np.ndarray = None
    knn_access: np.ndarray = None
    od_matrix: np.ndarray = None

    result_prefix = ""

    # Graph engine instance
    graph_engine = None

    # Elevation penalty coefficient (set at construction time)
        
    def __init__(self, topology, settings=None):
        super().__init__(topology)
        if settings is None:
            raise ValueError("For AccessibilityWElevation, settings should be passed constructor.")
        self._initialize_graph_engine(settings)
        
        
    
    @staticmethod
    def _build_compact_graph_from_topology(topology, elevation_coefficient: float = 1.0):
        """
        Build CompactNodeView graph from Topology using directional elevation weights.

        Directional weights are computed here from topology.network.z:
          - forward  (start_node → end_node): weights[e] + coef * max(0, z_end - z_start)
          - backward (end_node → start_node): weights[e] + coef * max(0, z_start - z_end)

        Falls back to symmetric weights when z is None.

        Args:
            topology: Topology instance with network, origins, destinations.
            elevation_coefficient: Penalty per unit of elevation gain.

        Returns:
            CompactNodeView: Initialized graph engine ready for calculations.
        """
        
        if topology.network is None:
            raise ValueError("Network must be added to topology before building graph")
        if topology.origins is None:
            raise ValueError("Origins must be added to topology before building graph")
        if topology.destinations is None:
            raise ValueError("Destinations must be added to topology before building graph")
        
        # Extract network data
        start_nodes = topology.network.start_nodes
        end_nodes   = topology.network.end_nodes
        weights     = topology.network.weights.astype(np.float64)
        node_count  = topology.network.node_points.shape[0]

        # --- Elevation weights --------------------------------------------------
        # Compute directional weights from Z values stored on the network nodes.
        # No dependency on BuildTopologyWElevation — all computation happens here.
        if topology.network.z is not None and elevation_coefficient != 0.0:
            z           = topology.network.z
            elev_diff   = z[end_nodes].astype(np.float64) - z[start_nodes].astype(np.float64)
            ab_weights  = weights + elevation_coefficient * np.maximum(0.0, elev_diff)
            ba_weights  = weights + elevation_coefficient * np.maximum(0.0, -elev_diff)
            topology.logger.log(
                'AccessibilityWElevation',
                f"Directional elevation weights computed internally "
                f"(coefficient={elevation_coefficient}).",
                v=2,
            )
        else:
            ab_weights = weights
            ba_weights = weights
            # if elevation_coefficient != 0.0:
            #     topology.logger.log(
            #         'AccessibilityWElevation',
            #         "Warning: network has no Z values — elevation penalty will not be applied. "
            #         "Ensure the network file contains 3-D geometry.",
            #         v=1,
            #     )
        # ------------------------------------------------------------------------

        # Obstacle penalties — injected here so Dijkstra sees the full effective
        # cost on every arc before the search starts.
        #
        # get_obstacle_arc_penalties() returns two per-edge arrays (shape = n_edges):
        #   p_AB — extra cost when traversing each edge in the forward direction
        #   p_BA — extra cost when traversing each edge in the reverse direction
        #
        # It handles both snap types internally:
        #   edge-snapped obstacles → penalty stored directly on the host arc
        #   node-snapped obstacles → penalty distributed to all arcs entering
        #                            that node (end_nodes for AB, start_nodes for BA)
        #                            to avoid double-counting on traversal
        #
        # Returns (None, None) when no obstacles were loaded, so the check below
        # is the only guard needed.
        p_AB, p_BA = topology.get_obstacle_arc_penalties()
        if p_AB is not None:
            ab_weights = ab_weights + p_AB.astype(np.float64)
            ba_weights = ba_weights + p_BA.astype(np.float64)
            topology.logger.log(
                'AccessibilityWElevation',
                f"Obstacle penalties applied: AB total={p_AB.sum():.2f}, BA total={p_BA.sum():.2f}.",
                v=2,
            )
        # ------------------------------------------------------------------------

        # Extract origin data
        o_start_nodes   = topology.origins.edge_start_node
        o_end_nodes     = topology.origins.edge_end_node
        o_start_weights = topology.origins.weight_to_start.astype(np.float64)
        o_end_weights   = topology.origins.weight_to_end.astype(np.float64)

        # Extract destination data
        d_start_nodes   = topology.destinations.edge_start_node
        d_end_nodes     = topology.destinations.edge_end_node
        d_weights       = topology.destinations.node_weight.astype(np.float64)
        d_start_weights = topology.destinations.weight_to_start.astype(np.float64)
        d_end_weights   = topology.destinations.weight_to_end.astype(np.float64)

        # Partial-edge obstacle corrections for first and last edges.
        #
        # The CSR arc weights above capture the full-arc obstacle penalty, which
        # is correct for interior edges that are fully traversed.  But origins and
        # destinations sit mid-edge: the Dijkstra seed (origin) or terminal weight
        # (destination) only covers a PARTIAL traversal.  If an obstacle lies
        # between the access point and the endpoint being used, the partial path
        # crosses it — but the full-arc penalty on the CSR arc does not capture
        # this correctly for that partial segment.
        #
        # get_partial_edge_corrections() checks, for each access point, whether
        # any obstacle on its host edge lies between the access point and each
        # endpoint, and returns the additive correction to apply to the terminal
        # weight for that endpoint direction.
        o_corr_start, o_corr_end = topology.get_partial_edge_corrections(
            topology.origins, for_origins=True
        )
        if o_corr_start is not None:
            o_start_weights = o_start_weights + o_corr_start
            o_end_weights   = o_end_weights   + o_corr_end
            topology.logger.log(
                'AccessibilityWElevation',
                f"Origin partial-edge corrections applied: "
                f"start-side Σ={o_corr_start.sum():.2f}, end-side Σ={o_corr_end.sum():.2f}.",
                v=2,
            )

        d_corr_start, d_corr_end = topology.get_partial_edge_corrections(
            topology.destinations, for_origins=False
        )
        if d_corr_start is not None:
            d_start_weights = d_start_weights + d_corr_start
            d_end_weights   = d_end_weights   + d_corr_end
            topology.logger.log(
                'AccessibilityWElevation',
                f"Destination partial-edge corrections applied: "
                f"start-side Σ={d_corr_start.sum():.2f}, end-side Σ={d_corr_end.sum():.2f}.",
                v=2,
            )
        # ------------------------------------------------------------------------
        
        # Build adjacency lists in compact vector format (CSR-like structure)
        adjacency_pointer          = np.zeros(node_count + 1, dtype=np.int64)
        adjacency_vector           = []
        adjacency_vector_weights   = []
        adjacynct_vector_network_node = []
        
        # Count neighbours per node
        for node in range(node_count):
            mask_start = start_nodes == node
            mask_end   = end_nodes   == node
            count = np.sum(mask_start) + np.sum(mask_end)
            adjacency_pointer[node + 1] = adjacency_pointer[node] + count
        
        # Fill adjacency vectors with directional weights
        for node in range(node_count):
            # Forward edges: node is start_node → neighbour is end_node
            # Cost: AB_weights (uphill penalty applied if elevation rises A→B)
            mask_start       = start_nodes == node
            end_neighbors    = end_nodes[mask_start]
            neighbor_weights = ab_weights[mask_start]
            is_network       = np.ones(len(end_neighbors), dtype=np.bool_)
            
            adjacency_vector.extend(end_neighbors)
            adjacency_vector_weights.extend(neighbor_weights)
            adjacynct_vector_network_node.extend(is_network)
            
            # Backward edges: node is end_node → neighbour is start_node
            # Cost: BA_weights (uphill penalty applied if elevation rises B→A)
            mask_end         = end_nodes == node
            start_neighbors  = start_nodes[mask_end]
            neighbor_weights = ba_weights[mask_end]
            is_network       = np.ones(len(start_neighbors), dtype=np.bool_)
            
            adjacency_vector.extend(start_neighbors)
            adjacency_vector_weights.extend(neighbor_weights)
            adjacynct_vector_network_node.extend(is_network)

        # Create graph instance
        graph = AccessibilityWElevation.CompactNodeView(dtype=None, max_error=None)
        
        graph.topology = topology
        graph.logger   = topology.logger
        
        graph.adjacency_pointer              = adjacency_pointer
        graph.adjacency_vector               = np.array(adjacency_vector,           dtype=np.int64)
        graph.adjacency_vector_weights       = np.array(adjacency_vector_weights,   dtype=np.float64)
        graph.adjacynct_vector_network_node  = np.array(adjacynct_vector_network_node, dtype=np.bool_)
        
        graph.o_terminal_idxs    = np.array([o_start_nodes, o_end_nodes],     dtype=np.int64).T
        graph.o_terminal_weights = np.array([o_start_weights, o_end_weights], dtype=np.float64).T
        
        graph.d_count            = len(d_weights)
        graph.d_terminal_idxs    = np.array([d_start_nodes, d_end_nodes],     dtype=np.int64).T
        graph.d_terminal_weights = np.array([d_start_weights, d_end_weights], dtype=np.float64).T
        
        return graph
    
    def _initialize_graph_engine(self, settings=None):
        """Initialize the CompactNodeView graph engine from topology.

        The elevation coefficient is applied only when settings.elevation
        is True — a network file may carry z-coordinates while the user
        runs a flat (symmetric-weight) analysis. Same guard as
        AccessibilityWTurns._compute_directional_weights.
        """
        self.logger.log('AccessibilityWElevation', "Initializing graph engine...", v=2)
        self.graph_engine = self._build_compact_graph_from_topology(
            self.topology,
            settings.elevation_penalty if settings.elevation else 0.0,
        )
        self.logger.log('AccessibilityWElevation', "Graph engine initialized", v=1)
    
    def Centrality(self, settings)->None:
        """
        Calculate accessibility/centrality metrics for origins.
        
        Args:
            settings: Settings instance with search_radius, gravity_beta, etc.
        """

        self.logger.log('AccessibilityWElevation', f"Calculating centrality with radius (weight) {settings.search_radius}", v=1)
        
        if self.graph_engine is None:
            raise ValueError("Graph engine not initialized. Call _initialize_graph_engine() first.")

        d_weights = self.topology.destinations.node_weight
        reach, gravity_exponential, gravity_logistic, knn_access = self.graph_engine.o_access(
            o_idx=None,
            settings=settings,
            d_weights=d_weights,
        )
        
        if settings.calculate_reach:
            self.reach = reach
            self.logger.log('AccessibilityWElevation', f"Reach calculated: min={reach.min():.2f}, max={reach.max():.2f}, mean={reach.mean():.2f}", v=2)
        
        if settings.calculate_exponential_gravity:
            self.gravity_exponential = gravity_exponential
            self.logger.log('AccessibilityWElevation', f"Gravity (exponential) calculated: min={gravity_exponential.min():.2f}, max={gravity_exponential.max():.2f}, mean={gravity_exponential.mean():.2f}", v=2)

        if settings.calculate_logistic_gravity:
            self.gravity_logistic = gravity_logistic
            self.logger.log('AccessibilityWElevation', f"Gravity (logistic) calculated: min={gravity_logistic.min():.2f}, max={gravity_logistic.max():.2f}, mean={gravity_logistic.mean():.2f}", v=2)

        if settings.calculate_knn_access:
            self.knn_access = knn_access
            self.logger.log('AccessibilityWElevation', f"KNN access calculated: min={knn_access.min():.2f}, max={knn_access.max():.2f}, mean={knn_access.mean():.2f}", v=2)

        # return {
        #     'reach': self.reach,
        #     'gravity_exponential': self.gravity_exponential,
        #     'gravity_logistic': self.gravity_logistic,
        #     'knn_access': self.knn_access,
        # }
    
    def OD_Matrix(self, search_radius: float = 1000, pairwise: bool = False):
        """
        Calculate full origin-destination distance matrix.
        
        Args:
            search_radius: Distance threshold for accessibility (network units)
            pairwise: If True, return as pairwise format (origin_id, destination_id, distance).
                     If False, return full matrix (origins × destinations)

        Returns:
            np.ndarray or pd.DataFrame: 
                - If pairwise=False: OD distance matrix (origins × destinations)
                - If pairwise=True: DataFrame with columns [origin_id, destination_id, distance]
        """
        self.logger.log('AccessibilityWElevation', f"Calculating OD matrix with radius {search_radius}m (pairwise={pairwise})", v=1)
        
        if self.graph_engine is None:
            raise ValueError("Graph engine not initialized. Call _initialize_graph_engine() first.")
        
        od_matrix = self.graph_engine.o_scope(
            o_idx=None,
            search_radius=search_radius,
        )
        
        if pairwise:
            o_indices, d_indices = np.where(od_matrix <= search_radius)
            distances = od_matrix[o_indices, d_indices]
            
            self.od_matrix = pd.DataFrame({
                'origin_id': o_indices,
                'destination_id': d_indices,
                'distance': distances,
            })
            self.logger.log('AccessibilityWElevation', f"OD matrix (pairwise) calculated: {len(o_indices)} pairs, shape={self.od_matrix.shape}", v=2)
        else:
            self.od_matrix = od_matrix
            self.logger.log('AccessibilityWElevation', f"OD matrix (full) calculated: shape={od_matrix.shape}, dtype={od_matrix.dtype}", v=2)
        
        return self.od_matrix
