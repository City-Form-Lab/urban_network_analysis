
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
# Accessibility Graph Engine Constants
# ============================================================================
DEFAULT_DESTINATION_NAME = 'destination'

# Numba JIT compilation settings
NUMBA_PARALLEL = False
NUMBA_CACHE = True
NUMBA_NOGIL = True
NUMBA_FASTMATH = True

# ============================================================================
# Accessibility Jitted Functions
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

    # plateau_distance = np.where(knn_distances - knn_gravity_plateau > 0, knn_distances - knn_gravity_plateau, 0)
    # knn_access = (knn_weights[:knn] / np.exp(beta * plateau_distance)).sum()

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
    
    # gravity_growth_rate = math.log(1000) / settings.gravity_logistic_midpoint

    # gravity_beta = settings.gravity_beta
    # gravity_plateau = settings.gravity_plateau
    # gravity_logistic_midpoint = settings.gravity_logistic_midpoint
    # knn_decay = settings.knn_decay
    # knn_weights = settings.knn_weights
    
    # cutoff = settings.search_radius

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
            gravity_plateau=gravity_plateau, # not implemented yet
            gravity_logistic_midpoint=gravity_logistic_midpoint, # not implemented yet
            gravity_growth_rate=gravity_growth_rate,
            knn_gravity_plateau=gravity_plateau,
            knn_weights=knn_weights,
            knn_gravity_beta = gravity_beta,
            knn_decay = knn_decay,
            knn_gravity_logistic_midpoint = gravity_logistic_midpoint,
            knn_gravity_growth_rate = gravity_growth_rate

        )
    return reach, gravity_exponential, gravity_logistic, knn_access



class Accessibility(Base):
    """
    Accessibility metrics calculator using CompactNodeView graph engine.
    
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
            
            # Topology and logger references
            # self.topology = None
            # self.logger = None
            
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
            
            # Decay parameters
            # self.decay_beta = 1.0
            # self.plateau = 500
        
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
                cutoff=settings.search_radius
                
            )
            #d_weights=self.node_lists[DEFAULT_DESTINATION_NAME].node_weight,
    
    # Minimal element list for node data storage
    class MinimalElementList:
        """Minimal element list storing node weight array."""
        
        def __init__(self, weights):
            self.node_weight = weights
    
    # ========================================================================
    # Result storage
    # ========================================================================
    reach: np.ndarray = None # Built at Centrality() and stored as instance variable for access
    gravity: np.ndarray = None # Built at Centrality() and stored as instance variable for access
    gravity_exponential: np.ndarray = None # Built at Centrality() and stored as instance variable for access
    gravity_logistic: np.ndarray = None # Built at Centrality() and stored as instance variable for access
    knn_access: np.ndarray = None # Built at Centrality() and stored as instance variable for access
    od_matrix: np.ndarray = None # Built at OD_Matrix() 
    
    result_prefix = "" # Optional prefix for result variable names (e.g. "NYC" to create reach_NYC, gravity_exponential_NYC, etc.)

    # Graph engine instance
    graph_engine = None
    
    def __init__(self, topology):
        super().__init__(topology)
        self._initialize_graph_engine()
    
    @staticmethod
    def _build_compact_graph_from_topology(topology):
        """
        Build CompactNodeView graph from Topology (static method for reusability).
        
        This method converts the Topology data into the compact vector format
        required by CompactNodeView.
        
        Args:
            topology: Topology instance with network, origins, destinations
            
        Returns:
            CompactNodeView: Initialized graph engine ready for calculations
        """
        
        if topology.network is None:
            raise ValueError("Network must be added to topology before building graph")
        if topology.origins is None:
            raise ValueError("Origins must be added to topology before building graph")
        if topology.destinations is None:
            raise ValueError("Destinations must be added to topology before building graph")
        
        # Extract network data
        start_nodes = topology.network.start_nodes
        end_nodes = topology.network.end_nodes
        weights = topology.network.weights.astype(np.float64)
        node_count = topology.network.node_points.shape[0]
        
        # Extract origin data
        o_start_nodes = topology.origins.edge_start_node
        o_end_nodes = topology.origins.edge_end_node
        o_start_weights = topology.origins.weight_to_start.astype(np.float64)
        o_end_weights = topology.origins.weight_to_end.astype(np.float64)
        
        # Extract destination data
        d_start_nodes = topology.destinations.edge_start_node
        d_end_nodes = topology.destinations.edge_end_node
        d_weights = topology.destinations.node_weight.astype(np.float64)
        d_start_weights = topology.destinations.weight_to_start.astype(np.float64)
        d_end_weights = topology.destinations.weight_to_end.astype(np.float64)
        
        # Build adjacency lists in compact vector format
        # Create CSR-like structure: pointer array and value arrays
        adjacency_pointer = np.zeros(node_count + 1, dtype=np.int64)
        adjacency_vector = []
        adjacency_vector_weights = []
        adjacynct_vector_network_node = []
        
        # Count neighbors per node
        for node in range(node_count):
            mask_start = start_nodes == node
            mask_end = end_nodes == node
            count = np.sum(mask_start) + np.sum(mask_end)
            adjacency_pointer[node + 1] = adjacency_pointer[node] + count
        
        # Fill adjacency vectors
        for node in range(node_count):
            # Add end nodes from edges starting at this node
            mask_start = start_nodes == node
            end_neighbors = end_nodes[mask_start]
            neighbor_weights = weights[mask_start]
            is_network = np.ones(len(end_neighbors), dtype=np.bool_)
            
            adjacency_vector.extend(end_neighbors)
            adjacency_vector_weights.extend(neighbor_weights)
            adjacynct_vector_network_node.extend(is_network)
            
            # Add start nodes from edges ending at this node
            mask_end = end_nodes == node
            start_neighbors = start_nodes[mask_end]
            neighbor_weights = weights[mask_end]
            is_network = np.ones(len(start_neighbors), dtype=np.bool_)
            
            adjacency_vector.extend(start_neighbors)
            adjacency_vector_weights.extend(neighbor_weights)
            adjacynct_vector_network_node.extend(is_network)

        # Create graph instance with required dtype and max_error arguments
        graph = Accessibility.CompactNodeView(dtype=None, max_error=None)
        
        # Store static reference to topology and logger
        graph.topology = topology
        graph.logger = topology.logger
        
        # Store compact adjacency data
        graph.adjacency_pointer = adjacency_pointer
        graph.adjacency_vector = np.array(adjacency_vector, dtype=np.int64)
        graph.adjacency_vector_weights = np.array(adjacency_vector_weights, dtype=np.float64)
        graph.adjacynct_vector_network_node = np.array(adjacynct_vector_network_node, dtype=np.bool_)
        
        # Store origin data
        graph.o_terminal_idxs = np.array([o_start_nodes, o_end_nodes], dtype=np.int64).T
        graph.o_terminal_weights = np.array([o_start_weights, o_end_weights], dtype=np.float64).T
        
        # Store destination data
        graph.d_count = len(d_weights)
        # graph.d_knn_weights = d_weights  # KNN weights (destination importance)
        graph.d_terminal_idxs = np.array([d_start_nodes, d_end_nodes], dtype=np.int64).T
        graph.d_terminal_weights = np.array([d_start_weights, d_end_weights], dtype=np.float64).T
        
        # Create minimal node_lists structure for o_access() compatibility
        # graph.node_lists = {
        #     DEFAULT_DESTINATION_NAME: Accessibility.MinimalElementList(d_weights)
        # }
        
        # Initialize decay parameters with defaults
        # graph.decay_beta = 1.0
        # graph.plateau = 500
        
        return graph
    
    def _initialize_graph_engine(self):
        """Initialize the CompactNodeView graph engine from topology."""
        self.logger.log('Accessibility', "Initializing graph engine...", v=2)
        self.graph_engine = self._build_compact_graph_from_topology(self.topology)
        self.logger.log('Accessibility', "Graph engine initialized", v=1)
    
    def Centrality(
        self, 
        settings: Settings
    )->None:
        """
        Calculate accessibility/centrality metrics for origins.
        
        Args:
            search_radius: Distance threshold for accessibility (network units)
            beta: Distance decay parameter (1.0 = no decay, higher = faster decay)
            plateau: Distance threshold before decay applies (for KNN metric)
            metric: Which metrics to calculate - 'reach', 'gravity', 'knn', or 'all'
            knn_koef: Coefficients for KNN metric calculation (length determines K in KNN)
        """

        self.logger.log('Accessibility', f"Calculating centrality with radius (weight) {settings.search_radius}", v=1)
        
        if self.graph_engine is None:
            raise ValueError("Graph engine not initialized. Call _initialize_graph_engine() first.")

        # There might be better to use centrality settings attribute to pass it to through the graph engine, but for now we can just pass it as arguments to o_access() and then to the jitted functions.

        # self.result_prefix = settings.results_prefix

        d_weights = self.topology.destinations.node_weight
        # Calculate accessibility metrics
        reach, gravity_exponential, gravity_logistic, knn_access = self.graph_engine.o_access(
            o_idx=None,
            settings = settings,
            d_weights = d_weights
        )
        
        # Store results
        if settings.calculate_reach:
            self.reach = reach
            self.logger.log('Accessibility', f"Reach calculated: min={reach.min():.2f}, max={reach.max():.2f}, mean={reach.mean():.2f}", v=2)
        
        if settings.calculate_exponential_gravity:
            self.gravity_exponential = gravity_exponential
            self.logger.log('Accessibility', f"Gravity (exponential) calculated: min={gravity_exponential.min():.2f}, max={gravity_exponential.max():.2f}, mean={gravity_exponential.mean():.2f}", v=2)

        if settings.calculate_logistic_gravity:
            self.gravity_logistic = gravity_logistic
            self.logger.log('Accessibility', f"Gravity (logistic) calculated: min={gravity_logistic.min():.2f}, max={gravity_logistic.max():.2f}, mean={gravity_logistic.mean():.2f}", v=2)

        if settings.calculate_knn_access:
            self.knn_access = knn_access
            self.logger.log('Accessibility', f"KNN access calculated: min={knn_access.min():.2f}, max={knn_access.max():.2f}, mean={knn_access.mean():.2f}", v=2)
        
        # return {
        #     'reach': self.reach,
        #     'gravity_exponential': self.gravity_exponential,
        #     'gravity_logistic': self.gravity_logistic,
        #     'knn_access': self.knn_access
        # }
    
    def OD_Matrix(self, search_radius: float = 1000, pairwise: bool = False):
        """
        Calculate full origin-destination distance matrix.
        
        Args:
            search_radius: Distance threshold for accessibility (network units)
            pairwise: If True, return as pairwise format (origin_id, destination_id, distance).
                     If False, return full matrix (origins × destinations)
            ¶
        Returns:
            np.ndarray or pd.DataFrame: 
                - If pairwise=False: OD distance matrix (origins × destinations)
                - If pairwise=True: DataFrame with columns [origin_id, destination_id, distance]
        """
        self.logger.log('Accessibility', f"Calculating OD matrix with radius {search_radius}m (pairwise={pairwise})", v=1)
        
        if self.graph_engine is None:
            raise ValueError("Graph engine not initialized. Call _initialize_graph_engine() first.")
        
        # Calculate OD matrix
        od_matrix = self.graph_engine.o_scope(
            o_idx=None,
            search_radius=search_radius
        )
        
        if pairwise:
            # Convert to pairwise format using vectorized numpy operations
            o_indices, d_indices = np.where(od_matrix <= search_radius)
            distances = od_matrix[o_indices, d_indices]
            
            self.od_matrix = pd.DataFrame({
                'origin_id': o_indices,
                'destination_id': d_indices,
                'distance': distances
            })
            self.logger.log('Accessibility', f"OD matrix (pairwise) calculated: {len(o_indices)} pairs, shape={self.od_matrix.shape}", v=2)
        else:
            self.od_matrix = od_matrix
            self.logger.log('Accessibility', f"OD matrix (full) calculated: shape={od_matrix.shape}, dtype={od_matrix.dtype}", v=2)
        
        return self.od_matrix
    
    