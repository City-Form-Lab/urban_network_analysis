from __future__ import annotations
import copy
import os
import sys
import math
import heapq
from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path

import numpy as np
import pandas as pd

try:
    from tqdm import tqdm as _tqdm_impl
    _TQDM_AVAILABLE = True
except ImportError:
    _TQDM_AVAILABLE = False


def _noop(iterable=None, **kwargs):
    if iterable is not None:
        return iterable

    class _Dummy:
        def update(self, n=1):
            pass

        def set_postfix(self, **kw):
            pass

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    return _Dummy()


class _SimpleBar:
    def __init__(self, iterable=None, total=None, desc=""):
        if iterable is not None:
            self.iterable = iterable
            self.total = total if total is not None else len(iterable)
        else:
            self.iterable = None
            self.total = total
        self.desc = desc
        self.count = 0
        self.step = max(1, int(self.total / 20)) if self.total is not None else 1
        if self.total is not None:
            print(f"{self.desc}: 0/{self.total} (0.0%)", end="\r", flush=True)

    def __iter__(self):
        for item in self.iterable:
            yield item
            self.update(1)

    def update(self, n=1):
        self.count += n
        if self.total is None:
            return
        if self.count >= self.total or self.count % self.step == 0:
            pct = 100.0 * self.count / self.total
            print(f"{self.desc}: {self.count}/{self.total} ({pct:.1f}%)", end="\r", flush=True)

    def close(self):
        if self.total is not None:
            print(f"{self.desc}: {self.total}/{self.total} (100.0%)")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _bar(show: bool, iterable=None, **kwargs):
    if show and _TQDM_AVAILABLE:
        return _tqdm_impl(iterable, **kwargs) if iterable is not None else _tqdm_impl(**kwargs)
    if show:
        if iterable is not None:
            return _SimpleBar(iterable=iterable, **kwargs)
        return _SimpleBar(**kwargs)
    return _noop(iterable, **kwargs)

from ..Logger import Logger
from ..Topology import Topology, Network, AccessPoints
from .Base import Base


class AccessibilityWTurns(Base):
    """
    Accessibility calculator with optional elevation and turn penalties.

    This engine uses a directed edge-state graph so turn penalties can be
    applied on every ordered (from_node, via_node, to_node) triple.
    """

    class StateGraph:
        """Minimal directed state graph representing oriented edges."""

        def __init__(self):
            self.state_start_node = None
            self.state_end_node = None
            self.state_weight = None
            self.adjacency_pointer = None
            self.adjacency_vector = None
            self.adjacency_vector_weights = None
            self.n_states = 0
            self.n_nodes = 0

    def __init__(self, topology, settings=None, use_cluster_splitting: bool = True):
        super().__init__(topology)
        if settings is None:
            raise ValueError("AccessibilityWTurns requires a Settings instance.")
        self.settings = settings
        self.graph_engine = None
        self.use_cluster_splitting = use_cluster_splitting and bool(settings.turns and settings.clasters > 1)
        if not self.use_cluster_splitting:
            self._initialize_graph_engine(settings)

    @staticmethod
    def _compute_directional_weights(topology, elevation_coefficient: float):
        start_nodes = topology.network.start_nodes
        end_nodes = topology.network.end_nodes
        weights = topology.network.weights.astype(np.float64)

        if topology.network.z is not None and elevation_coefficient != 0.0:
            z = topology.network.z.astype(np.float64)
            elev_diff = z[end_nodes] - z[start_nodes]
            ab_weights = weights + elevation_coefficient * np.maximum(0.0, elev_diff)
            ba_weights = weights + elevation_coefficient * np.maximum(0.0, -elev_diff)
            topology.logger.log(
                'AccessibilityWTurns',
                f"Directional elevation weights computed (coefficient={elevation_coefficient}).",
                v=2,
            )
        else:
            ab_weights = weights
            ba_weights = weights
            if elevation_coefficient != 0.0:
                topology.logger.log(
                    'AccessibilityWTurns',
                    "Warning: network Z values are missing. Elevation penalty will not be applied.",
                    v=1,
                )

        # Inject full-arc obstacle penalties into directional weights.
        # Interior edges are fully traversed, so adding the penalty to the arc
        # weight is correct.  Partial-edge corrections for the first (origin) and
        # last (destination) edges are handled separately in Centrality().
        p_AB, p_BA = topology.get_obstacle_arc_penalties()
        if p_AB is not None:
            ab_weights = ab_weights + p_AB.astype(np.float64)
            ba_weights = ba_weights + p_BA.astype(np.float64)
            topology.logger.log(
                'AccessibilityWTurns',
                f"Obstacle penalties applied: AB total={p_AB.sum():.2f}, BA total={p_BA.sum():.2f}.",
                v=2,
            )

        return ab_weights, ba_weights

    @staticmethod
    def _build_turn_penalty_map(topology, turn_angle_threshold: float, turn_penalty: float):
        if topology.network is None:
            raise ValueError("Network must be added to topology before building turn penalty map.")

        start_nodes = topology.network.start_nodes
        end_nodes = topology.network.end_nodes
        geometry = topology.network.geometry
        n_edges = len(start_nodes)
        n_nodes = topology.network.node_points.shape[0]

        geom_array = geometry.to_numpy()
        outgoing_at_start = np.zeros((n_edges, 2), dtype=np.float64)
        outgoing_at_end = np.zeros((n_edges, 2), dtype=np.float64)

        for e in range(n_edges):
            coords = np.asarray(geom_array[e].coords)[:, :2]
            if len(coords) < 2:
                continue

            v = coords[1] - coords[0]
            norm = np.linalg.norm(v)
            if norm > 0.0:
                outgoing_at_start[e] = v / norm

            v = coords[-2] - coords[-1]
            norm = np.linalg.norm(v)
            if norm > 0.0:
                outgoing_at_end[e] = v / norm

        node_to_edges = [[] for _ in range(n_nodes)]
        for e in range(n_edges):
            node_to_edges[int(start_nodes[e])].append(e)
            node_to_edges[int(end_nodes[e])].append(e)

        cos_threshold = float(np.cos(np.deg2rad(turn_angle_threshold)))
        turns = {}

        for via_node in range(n_nodes):
            incident = node_to_edges[via_node]
            if not incident:
                continue

            edge_data = []
            for e in incident:
                if start_nodes[e] == via_node:
                    edge_data.append((int(end_nodes[e]), outgoing_at_start[e]))
                else:
                    edge_data.append((int(start_nodes[e]), outgoing_at_end[e]))

            for from_node, out_vec_in in edge_data:
                for to_node, out_vec_out in edge_data:
                    cos_angle = float(-np.dot(out_vec_in, out_vec_out))
                    if cos_angle < -1.0:
                        cos_angle = -1.0
                    elif cos_angle > 1.0:
                        cos_angle = 1.0
                    if cos_angle < cos_threshold:
                        turns[(from_node, via_node, to_node)] = float(turn_penalty)
        return turns

    @classmethod
    def _build_state_graph_from_topology(cls, topology, settings):
        if topology.network is None:
            raise ValueError("Network must be added to topology before building graph.")
        if topology.origins is None or topology.destinations is None:
            raise ValueError("Origins and destinations must be added to topology before building graph.")

        turns = cls._build_turn_penalty_map(
            topology,
            settings.turn_threshold,
            settings.turn_penalty,
        ) if settings.turns else {}

        start_nodes = topology.network.start_nodes.astype(np.int64)
        end_nodes = topology.network.end_nodes.astype(np.int64)
        n_edges = len(start_nodes)
        n_nodes = topology.network.node_points.shape[0]

        ab_weights, ba_weights = cls._compute_directional_weights(topology, settings.elevation_penalty if settings.elevation else 0.0)

        n_states = n_edges * 2
        state_start_node = np.empty(n_states, dtype=np.int64)
        state_end_node = np.empty(n_states, dtype=np.int64)
        state_weight = np.empty(n_states, dtype=np.float64)

        # state 2*e   = edge e forward: start -> end
        # state 2*e+1 = edge e backward: end -> start
        for e in range(n_edges):
            state_start_node[2 * e] = start_nodes[e]
            state_end_node[2 * e] = end_nodes[e]
            state_weight[2 * e] = ab_weights[e]

            state_start_node[2 * e + 1] = end_nodes[e]
            state_end_node[2 * e + 1] = start_nodes[e]
            state_weight[2 * e + 1] = ba_weights[e]

        node_to_state = [[] for _ in range(n_nodes)]
        for s in range(n_states):
            node_to_state[int(state_start_node[s])].append(s)

        use_turns = bool(settings.turns)

        adjacency_pointer = np.zeros(n_states + 1, dtype=np.int64)
        total_transitions = 0
        for s in range(n_states):
            via_node = int(state_end_node[s])
            total_transitions += len(node_to_state[via_node])
            adjacency_pointer[s + 1] = total_transitions

        adjacency_vector = np.empty(total_transitions, dtype=np.int64)
        adjacency_vector_weights = np.empty(total_transitions, dtype=np.float64)

        for s in range(n_states):
            start = adjacency_pointer[s]
            end = adjacency_pointer[s + 1]
            via_node = int(state_end_node[s])
            prev_node = int(state_start_node[s])
            write_idx = start

            for next_state in node_to_state[via_node]:
                next_end = int(state_end_node[next_state])
                pen = 0.0
                if use_turns:
                    pen = turns.get((prev_node, via_node, next_end), 0.0)
                adjacency_vector[write_idx] = next_state
                adjacency_vector_weights[write_idx] = state_weight[next_state] + pen
                write_idx += 1

        graph = cls.StateGraph()
        graph.state_start_node = state_start_node
        graph.state_end_node = state_end_node
        graph.state_weight = state_weight
        graph.adjacency_pointer = adjacency_pointer
        graph.adjacency_vector = adjacency_vector
        graph.adjacency_vector_weights = adjacency_vector_weights
        graph.n_states = n_states
        graph.n_nodes = n_nodes
        return graph

    def _obstacle_terminal_corrections(self):
        """Return partial-edge obstacle corrections for origins and destinations.

        The full-arc obstacle penalty in the StateGraph weights is correct for
        interior edges (fully traversed).  For the first edge (origin) and last
        edge (destination), only part of the edge is traversed, so the penalty
        may be over- or under-counted.  This method returns additive corrections
        to apply to the terminal weights before Dijkstra seeding and distance
        adjustment.

        Returns:
            (o_corr_start, o_corr_end, d_corr_start, d_corr_end)
            All four are float64 arrays or None when no edge-snapped obstacles
            are loaded.
        """
        o_corr_start, o_corr_end = self.topology.get_partial_edge_corrections(
            self.topology.origins, for_origins=True
        )
        d_corr_start, d_corr_end = self.topology.get_partial_edge_corrections(
            self.topology.destinations, for_origins=False
        )
        return o_corr_start, o_corr_end, d_corr_start, d_corr_end

    @staticmethod
    def _dijkstra_state_graph(graph, origin_states, origin_weights, cutoff):
        n_states = graph.n_states
        dist = np.full(n_states, np.inf, dtype=np.float64)
        visited = np.zeros(n_states, dtype=np.bool_)
        queue = []

        for state_idx, weight in zip(origin_states, origin_weights):
            if weight < dist[state_idx]:
                dist[state_idx] = weight
                heapq.heappush(queue, (weight, int(state_idx)))

        while queue:
            weight, state_idx = heapq.heappop(queue)
            if weight != dist[state_idx]:
                continue
            if weight > cutoff:
                break
            start = graph.adjacency_pointer[state_idx]
            end = graph.adjacency_pointer[state_idx + 1]
            for ptr in range(start, end):
                next_state = int(graph.adjacency_vector[ptr])
                next_weight = weight + float(graph.adjacency_vector_weights[ptr])
                if next_weight < dist[next_state] and next_weight <= cutoff:
                    dist[next_state] = next_weight
                    heapq.heappush(queue, (next_weight, next_state))

        return dist

    @staticmethod
    def _distance_to_nodes(graph, state_distances):
        node_dist = np.full(graph.n_nodes, np.inf, dtype=np.float64)
        for state_idx in range(graph.n_states):
            node = int(graph.state_end_node[state_idx])
            d = state_distances[state_idx]
            if d < node_dist[node]:
                node_dist[node] = d
        return node_dist

    @staticmethod
    def _destination_distances(node_distances, d_terminal_idxs, d_terminal_weights):
        d_count = d_terminal_idxs.shape[0]
        distances = np.empty(d_count, dtype=np.float64)
        for i in range(d_count):
            start_node = int(d_terminal_idxs[i, 0])
            end_node = int(d_terminal_idxs[i, 1])
            distances[i] = min(
                node_distances[start_node] + float(d_terminal_weights[i, 0]),
                node_distances[end_node] + float(d_terminal_weights[i, 1]),
            )
        return distances

    @staticmethod
    def _access_metrics(d_distances, d_weights, settings):
        if d_distances.size == 0:
            return 0.0, 0.0, 0.0, 0.0

        within_cutoff = d_distances <= settings.search_radius
        filtered_distances = d_distances[within_cutoff]
        filtered_weights = d_weights[within_cutoff]

        reach = float(filtered_weights.sum())
        gravity_exponential = float(
            (filtered_weights / np.exp(settings.gravity_beta * np.maximum(0.0, filtered_distances - settings.gravity_plateau))).sum()
        )
        gravity_logistic = float(
            (filtered_weights * (1.0 - 1.0 / (1.0 + np.exp(-settings.gravity_decay_constant / settings.gravity_logistic_midpoint * (filtered_distances - settings.gravity_plateau - settings.gravity_logistic_midpoint))))).sum()
        )

        knn = min(filtered_distances.shape[0], settings.knn_weights.shape[0])
        if knn == 0:
            knn_access = 0.0
        else:
            idx = np.argsort(filtered_distances)
            nearest_distances = filtered_distances[idx][:knn]
            nearest_weights = filtered_weights[idx][:knn]
            knn_coefficients = settings.knn_weights[:knn]
            if settings.knn_decay == "exponential":
                knn_access = float((knn_coefficients * nearest_weights / np.exp(settings.gravity_beta * np.maximum(0.0, nearest_distances - settings.gravity_plateau))).sum())
            elif settings.knn_decay == "logistic":
                knn_access = float(
                    (knn_coefficients * nearest_weights * (1.0 - 1.0 / (1.0 + np.exp(-settings.gravity_decay_constant / settings.gravity_logistic_midpoint * (nearest_distances - settings.gravity_plateau - settings.gravity_logistic_midpoint))))).sum()
                )
            else:
                knn_access = float((knn_coefficients * nearest_weights).sum())

        return reach, gravity_exponential, gravity_logistic, knn_access

    def _initialize_graph_engine(self, settings=None):
        self.logger.log('AccessibilityWTurns', 'Initializing turn-aware graph engine...', v=2)
        self.graph_engine = self._build_state_graph_from_topology(self.topology, settings)
        self.logger.log('AccessibilityWTurns', 'Turn-aware graph engine initialized', v=1)

    def _build_cluster_topology(self, cluster_id: int):
        edge_ilocs = np.asarray(self.topology.network.clasters_byId[cluster_id], dtype=np.int64)
        origin_ilocs = np.asarray(self.topology.origins.clasters_byId[cluster_id], dtype=np.int64)
        destination_ilocs = np.asarray(self.topology.destinations.clasters_byId[cluster_id], dtype=np.int64)

        cluster_topology = Topology()
        cluster_topology.logger = self.topology.logger
        cluster_topology.crs = self.topology.crs
        cluster_topology.has_clusters = False
        cluster_topology.num_clusters = 1

        cluster_network = Network(self.topology.logger)
        cluster_network.node_points = self.topology.network.node_points
        cluster_network.z = self.topology.network.z
        cluster_network.start_nodes = self.topology.network.start_nodes[edge_ilocs]
        cluster_network.end_nodes = self.topology.network.end_nodes[edge_ilocs]
        cluster_network.weights = self.topology.network.weights[edge_ilocs]
        cluster_network.lengths = self.topology.network.lengths[edge_ilocs] if self.topology.network.lengths is not None else None
        cluster_network.geometry = self.topology.network.geometry.iloc[edge_ilocs].reset_index(drop=True)
        cluster_network.AB_weights = None
        cluster_network.BA_weights = None
        cluster_network.mask = None
        cluster_network.turns = None
        cluster_network.edge_sindex = None
        cluster_topology.network = cluster_network

        cluster_origins = AccessPoints(self.topology.logger)
        cluster_origins.geometry = self.topology.origins.geometry.iloc[origin_ilocs].reset_index(drop=True)
        cluster_origins.node_weight = self.topology.origins.node_weight[origin_ilocs]
        cluster_origins.uid = self.topology.origins.uid[origin_ilocs] if self.topology.origins.uid is not None else None
        cluster_origins.edge_start_node = self.topology.origins.edge_start_node[origin_ilocs]
        cluster_origins.edge_end_node = self.topology.origins.edge_end_node[origin_ilocs]
        cluster_origins.weight_to_start = self.topology.origins.weight_to_start[origin_ilocs]
        cluster_origins.weight_to_end = self.topology.origins.weight_to_end[origin_ilocs]
        cluster_topology.origins = cluster_origins

        cluster_destinations = AccessPoints(self.topology.logger)
        cluster_destinations.geometry = self.topology.destinations.geometry.iloc[destination_ilocs].reset_index(drop=True)
        cluster_destinations.node_weight = self.topology.destinations.node_weight[destination_ilocs]
        cluster_destinations.uid = self.topology.destinations.uid[destination_ilocs] if self.topology.destinations.uid is not None else None
        cluster_destinations.edge_start_node = self.topology.destinations.edge_start_node[destination_ilocs]
        cluster_destinations.edge_end_node = self.topology.destinations.edge_end_node[destination_ilocs]
        cluster_destinations.weight_to_start = self.topology.destinations.weight_to_start[destination_ilocs]
        cluster_destinations.weight_to_end = self.topology.destinations.weight_to_end[destination_ilocs]
        cluster_topology.destinations = cluster_destinations

        return cluster_topology, origin_ilocs

    def _process_cluster(self, cluster_id: int):
        cluster_topology, origin_ilocs = self._build_cluster_topology(cluster_id)

        if (cluster_topology.network.weights is None
                or len(cluster_topology.network.weights) == 0
                or len(cluster_topology.origins.geometry) == 0
                or len(cluster_topology.destinations.geometry) == 0):
            n = len(origin_ilocs)
            zero = np.zeros(n, dtype=np.float64)
            return origin_ilocs, zero, zero, zero, zero

        cluster_settings = copy.copy(self.settings)
        cluster_settings.progressbar = False
        cluster_settings.logger_verbosity = 0

        cluster_topology.logger = Logger(verbosity=cluster_settings.logger_verbosity)
        engine = AccessibilityWTurns(cluster_topology, cluster_settings, use_cluster_splitting=False)
        engine.Centrality(cluster_settings, show_progress=False)
        return (
            origin_ilocs,
            engine.reach if engine.reach is not None else np.zeros(len(origin_ilocs), dtype=np.float64),
            engine.gravity_exponential if engine.gravity_exponential is not None else np.zeros(len(origin_ilocs), dtype=np.float64),
            engine.gravity_logistic if engine.gravity_logistic is not None else np.zeros(len(origin_ilocs), dtype=np.float64),
            engine.knn_access if engine.knn_access is not None else np.zeros(len(origin_ilocs), dtype=np.float64),
        )

    def _clustered_centrality(self, settings):
        if not self.topology.has_clusters or self.topology.num_clusters != settings.clasters:
            self.topology.BuildClusters(settings.clasters, settings.search_radius)

        cluster_ids = sorted(self.topology.cluster_masks.keys())
        total_origins = len(self.topology.origins.geometry)

        self.logger.log(
            'AccessibilityWTurns',
            f"Processing {len(cluster_ids)} clusters {'in parallel' if settings.cluster_parallel else 'sequentially'} with turn-aware centrality.",
            v=1,
        )

        reach = np.zeros(total_origins, dtype=np.float64) if settings.calculate_reach else None
        gravity_exponential = np.zeros(total_origins, dtype=np.float64) if settings.calculate_exponential_gravity else None
        gravity_logistic = np.zeros(total_origins, dtype=np.float64) if settings.calculate_logistic_gravity else None
        knn_access = np.zeros(total_origins, dtype=np.float64) if settings.calculate_knn_access else None

        show_progress = bool(settings.progressbar)
        cluster_bar = _bar(show_progress, total=len(cluster_ids), desc='Clusters')
        try:
            if settings.cluster_parallel and len(cluster_ids) > 1:
                workers = min(max(1, settings.cluster_workers), len(cluster_ids))
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(self._process_cluster, cid): cid for cid in cluster_ids}
                    for future in as_completed(futures):
                        origin_ilocs, cluster_reach, cluster_ge, cluster_gl, cluster_knn = future.result()
                        if reach is not None:
                            reach[origin_ilocs] = cluster_reach
                        if gravity_exponential is not None:
                            gravity_exponential[origin_ilocs] = cluster_ge
                        if gravity_logistic is not None:
                            gravity_logistic[origin_ilocs] = cluster_gl
                        if knn_access is not None:
                            knn_access[origin_ilocs] = cluster_knn
                        cluster_bar.update(1)
            else:
                for cluster_id in cluster_ids:
                    origin_ilocs, cluster_reach, cluster_ge, cluster_gl, cluster_knn = self._process_cluster(cluster_id)
                    if reach is not None:
                        reach[origin_ilocs] = cluster_reach
                    if gravity_exponential is not None:
                        gravity_exponential[origin_ilocs] = cluster_ge
                    if gravity_logistic is not None:
                        gravity_logistic[origin_ilocs] = cluster_gl
                    if knn_access is not None:
                        knn_access[origin_ilocs] = cluster_knn
                    cluster_bar.update(1)
        finally:
            cluster_bar.close()

        if settings.calculate_reach:
            self.reach = reach
        if settings.calculate_exponential_gravity:
            self.gravity_exponential = gravity_exponential
        if settings.calculate_logistic_gravity:
            self.gravity_logistic = gravity_logistic
        if settings.calculate_knn_access:
            self.knn_access = knn_access

    def Centrality(self, settings, show_progress=None):
        self.logger.log('AccessibilityWTurns', f"Calculating centrality with radius {settings.search_radius}", v=1)
        if self.use_cluster_splitting:
            self._clustered_centrality(settings)
            return
        if self.graph_engine is None:
            raise ValueError('Graph engine not initialized. Call _initialize_graph_engine() first.')

        o_count = self.topology.origins.geometry.shape[0]
        d_weights = self.topology.destinations.node_weight.astype(np.float64)
        d_terminal_idxs = np.array([self.topology.destinations.edge_start_node, self.topology.destinations.edge_end_node], dtype=np.int64).T
        d_terminal_weights = np.array([self.topology.destinations.weight_to_start, self.topology.destinations.weight_to_end], dtype=np.float64).T

        o_start_nodes = self.topology.origins.edge_start_node.astype(np.int64)
        o_end_nodes = self.topology.origins.edge_end_node.astype(np.int64)
        o_start_weights = self.topology.origins.weight_to_start.astype(np.float64)
        o_end_weights = self.topology.origins.weight_to_end.astype(np.float64)

        # Partial-edge obstacle corrections for first and last edges.
        o_corr_start, o_corr_end, d_corr_start, d_corr_end = self._obstacle_terminal_corrections()
        if o_corr_start is not None:
            o_start_weights   = o_start_weights   + o_corr_start
            o_end_weights     = o_end_weights     + o_corr_end
            d_terminal_weights = d_terminal_weights + np.stack([d_corr_start, d_corr_end], axis=1)

        state_graph = self.graph_engine
        node_start_to_states = [[] for _ in range(state_graph.n_nodes)]
        for state_idx in range(state_graph.n_states):
            node_start_to_states[int(state_graph.state_start_node[state_idx])].append(state_idx)

        reach = np.empty(o_count, dtype=np.float64)
        gravity_exponential = np.empty(o_count, dtype=np.float64)
        gravity_logistic = np.empty(o_count, dtype=np.float64)
        knn_access = np.empty(o_count, dtype=np.float64)

        if show_progress is None:
            show_progress = bool(settings.progressbar)
        show_progress = bool(show_progress)
        bar = _bar(show_progress, total=o_count, desc='Origins')
        try:
            for origin_idx in range(o_count):
                origin_states = []
                origin_weights = []
                for state_idx in node_start_to_states[int(o_start_nodes[origin_idx])]:
                    origin_states.append(state_idx)
                    origin_weights.append(o_start_weights[origin_idx] + state_graph.state_weight[state_idx])
                for state_idx in node_start_to_states[int(o_end_nodes[origin_idx])]:
                    origin_states.append(state_idx)
                    origin_weights.append(o_end_weights[origin_idx] + state_graph.state_weight[state_idx])

                if len(origin_states) == 0:
                    reach[origin_idx] = 0.0
                    gravity_exponential[origin_idx] = 0.0
                    gravity_logistic[origin_idx] = 0.0
                    knn_access[origin_idx] = 0.0
                    bar.update(1)
                    continue

                origin_states = np.array(origin_states, dtype=np.int64)
                origin_weights = np.array(origin_weights, dtype=np.float64)
                state_distances = self._dijkstra_state_graph(state_graph, origin_states, origin_weights, float(settings.search_radius))
                node_distances = self._distance_to_nodes(state_graph, state_distances)
                d_distances = self._destination_distances(node_distances, d_terminal_idxs, d_terminal_weights)
                reach[origin_idx], gravity_exponential[origin_idx], gravity_logistic[origin_idx], knn_access[origin_idx] = self._access_metrics(d_distances, d_weights, settings)
                bar.update(1)
        finally:
            bar.close()

        if settings.calculate_reach:
            self.reach = reach
        if settings.calculate_exponential_gravity:
            self.gravity_exponential = gravity_exponential
        if settings.calculate_logistic_gravity:
            self.gravity_logistic = gravity_logistic
        if settings.calculate_knn_access:
            self.knn_access = knn_access

    def OD_Matrix(self, search_radius: float = 1000, pairwise: bool = False):
        if self.graph_engine is None:
            raise ValueError('Graph engine not initialized. Call _initialize_graph_engine() first.')

        o_count = self.topology.origins.geometry.shape[0]
        d_weights = self.topology.destinations.node_weight.astype(np.float64)
        d_terminal_idxs = np.array([self.topology.destinations.edge_start_node, self.topology.destinations.edge_end_node], dtype=np.int64).T
        d_terminal_weights = np.array([self.topology.destinations.weight_to_start, self.topology.destinations.weight_to_end], dtype=np.float64).T

        o_start_nodes = self.topology.origins.edge_start_node.astype(np.int64)
        o_end_nodes = self.topology.origins.edge_end_node.astype(np.int64)
        o_start_weights = self.topology.origins.weight_to_start.astype(np.float64)
        o_end_weights = self.topology.origins.weight_to_end.astype(np.float64)

        # Partial-edge obstacle corrections for first and last edges.
        o_corr_start, o_corr_end, d_corr_start, d_corr_end = self._obstacle_terminal_corrections()
        if o_corr_start is not None:
            o_start_weights    = o_start_weights   + o_corr_start
            o_end_weights      = o_end_weights     + o_corr_end
            d_terminal_weights = d_terminal_weights + np.stack([d_corr_start, d_corr_end], axis=1)

        state_graph = self.graph_engine
        node_start_to_states = [[] for _ in range(state_graph.n_nodes)]
        for state_idx in range(state_graph.n_states):
            node_start_to_states[int(state_graph.state_start_node[state_idx])].append(state_idx)

        od_matrix = np.full((o_count, len(d_weights)), np.inf, dtype=np.float64)

        for origin_idx in range(o_count):
            origin_states = []
            origin_weights = []
            for state_idx in node_start_to_states[int(o_start_nodes[origin_idx])]:
                origin_states.append(state_idx)
                origin_weights.append(o_start_weights[origin_idx] + state_graph.state_weight[state_idx])
            for state_idx in node_start_to_states[int(o_end_nodes[origin_idx])]:
                origin_states.append(state_idx)
                origin_weights.append(o_end_weights[origin_idx] + state_graph.state_weight[state_idx])

            if len(origin_states) == 0:
                continue

            origin_states = np.array(origin_states, dtype=np.int64)
            origin_weights = np.array(origin_weights, dtype=np.float64)
            state_distances = self._dijkstra_state_graph(state_graph, origin_states, origin_weights, float(search_radius))
            node_distances = self._distance_to_nodes(state_graph, state_distances)
            d_distances = self._destination_distances(node_distances, d_terminal_idxs, d_terminal_weights)
            od_matrix[origin_idx, :] = d_distances

        if pairwise:
            o_idxs, d_idxs = np.where(od_matrix <= search_radius)
            distances = od_matrix[o_idxs, d_idxs]
            self.od_matrix = pd.DataFrame({
                'origin_id': o_idxs,
                'destination_id': d_idxs,
                'distance': distances,
            })
        else:
            self.od_matrix = od_matrix
        return self.od_matrix
