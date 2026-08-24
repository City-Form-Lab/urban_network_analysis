"""
Flow — directed, elevation/turn-aware, cluster-aware edge flow engine.

v4 changes (relative to v3):
  * Sole path-enumeration mode is k_alternatives (Plateau's penalty method).
    All DFS-based enumeration paths and their settings, helpers, buffers,
    backward-Dijkstra caching, A* neighbor ordering, elastic buffer retry,
    streamed output, and Numba DFS kernel dispatch have been removed.
  * The CSR build still uses _betweenness_numba.build_csr_from_digraph and
    the per-edge accumulator still uses _betweenness_numba.accumulate_paths_numba.
    The DFS kernel in that module is no longer called.

v2.4.3 changes (relative to v2.4.2):
  * Trip-generation factor is now a single per-origin scalar, not a
    per-destination decay multiplier.  This eliminates the
    non-monotonicity bug where adding destinations farther than the
    median could DECREASE the origin's total trip generation.
  * `flow_decay_method` now selects the factor's aggregation:
        "closest"     factor = curve(min(d) - plateau).  Default.
        "gravity_cap" factor = min(1, gravity / flow_gravity_cap)
                      where `gravity` is the Huff numerator (sum of
                      d_weight × distance_decay within search_radius, or
                      destination count if d_weights = False).  When
                      flow_decay = False, this collapses to raw
                      reach (no decay applied).
  * `flow_decay_curve` selects the shape used in "closest"
    mode and in the gravity sum that feeds "gravity_cap":
    "exponential" or "logistic".
  * `flow_gravity_cap` (float, default 0.0) — required when
    decay_method='gravity_cap'.
  * Legacy KNN-elastic settings removed:
        flow_elastic_origin_weight,
        flow_elastic_min_factor,
        flow_elastic_low_percentile.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np

try:
    from tqdm import tqdm as _tqdm_impl
    _TQDM_AVAILABLE = True
except ImportError:
    _TQDM_AVAILABLE = False


def _noop(iterable=None, **kwargs):
    if iterable is not None:
        return iterable

    class _Dummy:
        def update(self, n=1): pass
        def set_postfix(self, **kw): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return _Dummy()


def _bar(show: bool, iterable=None, **kwargs):
    if show and _TQDM_AVAILABLE:
        return _tqdm_impl(iterable, **kwargs) if iterable is not None else _tqdm_impl(**kwargs)
    return _noop(iterable, **kwargs)


from ..Logger import Logger
from ..Topology import Topology
from ..Settings import Settings
from .Base import Base
from . import _betweenness_numba as _bnumba

try:
    from scipy.sparse import csr_matrix as _scipy_csr
    from scipy.sparse.csgraph import dijkstra as _scipy_dijkstra
    _SCIPY_OK = True
except Exception:
    _SCIPY_OK = False


_VALID_METHODS = ("none", "exponential", "logistic")
_VALID_MODES   = ("ratio", "buffer", "min")


def _normalize_method(name) -> str:
    if name is None:
        return "none"
    n = str(name).strip().lower()
    if n in ("exp", "exponent", "exponential"):
        return "exponential"
    if n in ("logit", "logistic"):
        return "logistic"
    if n in ("", "none", "off", "false"):
        return "none"
    raise ValueError(
        f"Unknown decay/penalty method: {name!r}. Expected one of "
        f"{_VALID_METHODS} (or 'exp', 'exponent', 'logit', 'off')."
    )


# Geometric turn-cost helper.  Returns turn_penalty_amt if the deviation from
# straight-ahead exceeds turn_threshold_deg; 0 otherwise.  Only network nodes
# count as real intersections — turns at origin/destination virtual nodes
# are always 0.
def _turn_cost_geometric(node_xy, n_net, prev_node, curr_node, next_node,
                         turn_threshold_deg, turn_penalty_amt):
    if prev_node >= n_net or curr_node >= n_net or next_node >= n_net:
        return 0.0
    a = node_xy[prev_node]; b = node_xy[curr_node]; c = node_xy[next_node]
    raw = math.degrees(
        math.atan2(c[1] - b[1], c[0] - b[0])
        - math.atan2(a[1] - b[1], a[0] - b[0])
    )
    ang = raw + 360.0 if raw < 0.0 else raw
    deviation = abs(round(ang) - 180)
    return turn_penalty_amt if deviation > turn_threshold_deg else 0.0


def _cutoff_for_shortest(shortest_d: float, mode: str, ratio: float, buffer: float) -> float:
    if mode == "ratio":
        return shortest_d * ratio
    if mode == "buffer":
        return shortest_d + buffer
    if mode == "min":
        return min(shortest_d * ratio, shortest_d + buffer)
    raise ValueError(f"Unknown detour_mode {mode!r}, expected one of {_VALID_MODES}.")


def _tol_for(shortest_d: float) -> float:
    return max(1e-9, abs(shortest_d) * 1e-9)


# Hard per-origin destination ceiling — protects flat path buffers from
# absurd allocations even when the user sets a very high
# `flow_k_nearest_destinations` (and its deprecated alias
# `flow_max_destinations_per_origin`).
_MAX_DESTS_HARD_CEIL = 4000


def _effective_max_dests(user_cap: int) -> int:
    """
    Resolve the user setting `flow_k_nearest_destinations` into
    an actual integer ceiling.  0 means "no user cap" → fall back to the
    hard ceiling.  Any other value is clamped to the hard ceiling.
    """
    if user_cap <= 0:
        return _MAX_DESTS_HARD_CEIL
    return min(int(user_cap), _MAX_DESTS_HARD_CEIL)


# ──────────────────────────────────────────────────────────────────────
# Turn-aware line-graph construction.
#
# To model intersection turn penalties in shortest-path search, we expand
# the original directed graph into its "line graph" (a.k.a. edge graph,
# state graph), where each STATE represents being-in-the-middle-of-an-arc
# rather than being-at-a-node.
#
# State space:
#   * States 0 .. n_arcs - 1   : L_a, one per directional arc a in the
#                                original CSR.  head(L_a) = csr_indices[a]
#                                (the node where arc a ends).
#   * States n_arcs .. n_arcs + n_net - 1 : PRE-u, one per network node u.
#                                Used as the search source: starting the
#                                Dijkstra at PRE-u means "I'm about to
#                                take a first arc out of u."
#
# Line edges:
#   * For each consecutive pair (a = ?→y, b = y→z):
#       L_a → L_b   with weight = csr_weights[b] + turn_cost(src(a), y, z)
#   * For each PRE-u and each outgoing arc b starting at u:
#       PRE-u → L_b with weight = csr_weights[b]  (no turn penalty at start)
#
# The turn cost depends only on geometry, so it's precomputed once.
# scipy Dijkstra on this expanded CSR yields shortest paths that fully
# account for per-intersection turn costs.  Path reconstruction walks
# the predecessors chain: each line state L_a encodes the original arc
# index a, from which we recover (eid, dir) for the accumulator.
#
# Memory scales as ~ sum_v (in_deg(v) * out_deg(v)) — typically 3-5×
# the original CSR for street networks (avg degree ~3).
# ──────────────────────────────────────────────────────────────────────

def _build_turn_aware_line_csr(
    csr_indptr: np.ndarray,
    csr_indices: np.ndarray,
    n_net: int,
    n_dest: int,
    node_xy: np.ndarray,
    turn_threshold_deg: float,
    turn_penalty_amt: float,
):
    """
    Construct the line-graph CSR for turn-aware path search.

    Returns:
        line_indptr      : int64[n_line_nodes + 1]
        line_indices     : int32[n_line_edges]  (target line node IDs)
        line_arc_b       : int32[n_line_edges]  (original arc index for each line edge)
        line_turn_costs  : float64[n_line_edges]  (precomputed turn cost)
        n_line_nodes     : int
        n_arcs           : int  (== state-ID offset for PRE-u states)
        arc_source       : int32[n_arcs]  (source node of each original arc)
        dest_goal_states : dict[int → np.ndarray[int32]]  (per-destination goal states)
    """
    n_arcs = int(csr_indices.shape[0])
    n_total_orig = n_net + n_dest

    # arc_source[a] = source node of arc a (i.e., the u whose CSR slice contains a)
    arc_source = np.empty(n_arcs, dtype=np.int32)
    for u in range(n_total_orig):
        s, e = int(csr_indptr[u]), int(csr_indptr[u + 1])
        if e > s:
            arc_source[s:e] = u

    # Number of line nodes: one per arc + one PRE-u per network node.
    n_line_nodes = n_arcs + n_net

    # Step 1: count outgoing line edges per line node.
    # For L_a state (line node a, a in [0, n_arcs)):
    #   number of outgoing edges = out_deg of head(a) = csr_indptr[head(a)+1] - csr_indptr[head(a)]
    # For PRE-u state (line node n_arcs + u, u in [0, n_net)):
    #   number of outgoing edges = out_deg of u
    head_arr = csr_indices  # csr_indices[a] = head(a) for arc a < n_arcs
    out_deg_per_node = (csr_indptr[1:] - csr_indptr[:-1]).astype(np.int32)
    out_deg_for_La  = out_deg_per_node[head_arr[:n_arcs]]
    out_deg_for_PRE = out_deg_per_node[:n_net]

    line_indptr = np.empty(n_line_nodes + 1, dtype=np.int64)
    line_indptr[0] = 0
    line_indptr[1:n_arcs + 1] = np.cumsum(out_deg_for_La)
    base = line_indptr[n_arcs]
    line_indptr[n_arcs + 1:] = base + np.cumsum(out_deg_for_PRE)

    n_line_edges = int(line_indptr[-1])
    line_indices    = np.empty(n_line_edges, dtype=np.int32)
    line_arc_b      = np.empty(n_line_edges, dtype=np.int32)
    line_turn_costs = np.zeros(n_line_edges, dtype=np.float64)

    # Step 2: fill L_a outgoing edges and their turn costs.
    for a in range(n_arcs):
        head_a = int(head_arr[a])
        s_b = int(csr_indptr[head_a])
        e_b = int(csr_indptr[head_a + 1])
        s_line = int(line_indptr[a])
        # The outgoing arcs from head_a are CSR slice [s_b, e_b); their
        # global arc indices are s_b, s_b+1, ..., e_b-1.
        n_b = e_b - s_b
        if n_b > 0:
            arc_b_block = np.arange(s_b, e_b, dtype=np.int32)
            line_indices[s_line:s_line + n_b] = arc_b_block  # L_b node ID == b
            line_arc_b  [s_line:s_line + n_b] = arc_b_block
            # Turn cost depends on (src(a), head_a, head_b = csr_indices[b]).
            src_a = int(arc_source[a])
            if src_a < n_net and head_a < n_net:
                for j in range(n_b):
                    b = int(arc_b_block[j])
                    head_b = int(head_arr[b])
                    if head_b < n_net:
                        line_turn_costs[s_line + j] = _turn_cost_geometric(
                            node_xy, n_net, src_a, head_a, head_b,
                            turn_threshold_deg, turn_penalty_amt,
                        )
            # else: at least one of src(a)/head_a is a virtual node — no turn cost.

    # Step 3: fill PRE-u outgoing edges.  Initial transitions have zero
    # turn cost (no "incoming direction" before the start of the path).
    for u in range(n_net):
        s_b = int(csr_indptr[u])
        e_b = int(csr_indptr[u + 1])
        s_line = int(line_indptr[n_arcs + u])
        n_b = e_b - s_b
        if n_b > 0:
            arc_b_block = np.arange(s_b, e_b, dtype=np.int32)
            line_indices[s_line:s_line + n_b] = arc_b_block
            line_arc_b  [s_line:s_line + n_b] = arc_b_block
            # line_turn_costs already zero-initialized.

    # Step 4: per-destination goal-state lists.
    # The "goal" for destination d (node id n_net + d_pos) is "any L_a
    # where head(a) == d_node".
    dest_goal_states = {}
    for d_pos in range(n_dest):
        d_node = n_net + d_pos
        # arcs ending at d_node in the original CSR
        mask = (head_arr[:n_arcs] == d_node)
        goal_arcs = np.where(mask)[0].astype(np.int32)
        dest_goal_states[d_pos] = goal_arcs

    return (line_indptr, line_indices, line_arc_b, line_turn_costs,
            n_line_nodes, n_arcs, arc_source, dest_goal_states)


# ──────────────────────────────────────────────────────────────────────
# Plateau's penalty method for K alternative paths.
#
# Repeatedly runs forward Dijkstra from origin → destination(s).  After
# each iteration, multiplies the weights of edges used by the emitted
# paths by `penalty_factor` in a working copy of the graph.  This makes
# previously-used edges less attractive in the next search, forcing
# subsequent paths to be structurally distinct.
#
# Crucially: emitted path weights are reported using the ORIGINAL
# (un-penalized) edge weights, so downstream path-penalty / decay
# calculations operate on real lengths.  The detour-budget cutoff is
# enforced post-reconstruction against the actual (un-penalized) path
# length.
# ──────────────────────────────────────────────────────────────────────

def _enumerate_k_alternatives_paths(
    csr_indptr: np.ndarray,
    csr_indices: np.ndarray,
    csr_weights: np.ndarray,
    csr_edge_id: np.ndarray,
    csr_direction: np.ndarray,
    origin_neighbors: np.ndarray,
    origin_weights_in: np.ndarray,
    origin_edge_ids: np.ndarray,
    origin_dirs: np.ndarray,
    origin_n: int,
    d_node_ids: np.ndarray,
    d_cutoffs: np.ndarray,
    d_tols: np.ndarray,
    K: int,
    penalty_factor: float,
    n_total: int,
    # output buffers (caller-allocated, sized for at least K * n_dest paths)
    out_path_edges: np.ndarray,
    out_path_offsets: np.ndarray,
    out_path_weights: np.ndarray,
    out_path_dest_idx: np.ndarray,
    debug_print_paths: bool = False,
    debug_origin_tag: str = "",
    # Optional debug-only inputs for elevation-gain reporting.
    debug_arc_source=None,
    debug_node_z=None,
    debug_elevation_penalty: float = 0.0,
):
    """
    Plateau's penalty method.  Runs K Dijkstras from origin; after each,
    penalizes weights of edges used in the emitted path(s).  Returns
    (n_paths, n_edges_out).
    """
    n_dest = int(d_node_ids.shape[0])
    if n_dest == 0 or K <= 0:
        out_path_offsets[0] = 0
        return 0, 0

    work_weights  = csr_weights.astype(np.float64, copy=True)
    work_origin_w = origin_weights_in.astype(np.float64, copy=True)

    out_n_paths = 0
    out_n_edges = 0
    max_total_paths      = out_path_offsets.shape[0] - 1
    max_total_path_edges = out_path_edges.shape[0]

    # Pre-cast indptr to int64 once (scipy_csr requires it).
    indptr_i64 = csr_indptr.astype(np.int64, copy=False)

    for iteration in range(int(K)):
        # Build scipy CSR with current (possibly penalized) weights.
        sparse_g = _scipy_csr(
            (work_weights, csr_indices, indptr_i64),
            shape=(n_total, n_total),
        )

        # For each first-hop option, run Dijkstra and track the best per-dest.
        best_dist      = np.full(n_dest, np.inf, dtype=np.float64)
        best_first_hop = np.full(n_dest, -1, dtype=np.int32)
        first_hop_data = []  # list of (hop_idx, distances, predecessors)

        for hop_idx in range(int(origin_n)):
            start_node = int(origin_neighbors[hop_idx])
            conn_w     = float(work_origin_w[hop_idx])
            # Note: no `limit` passed to Dijkstra.  Per-edge weights
            # accumulate penalty over iterations, so a penalized path's
            # weight can vastly exceed its actual length.  Setting a tight
            # limit (e.g. detour cutoff) causes Dijkstra to prematurely
            # prune searches that would otherwise reconstruct to a path
            # whose ACTUAL length is within budget.  The post-reconstruction
            # cutoff check (against orig_w below) is the true budget enforcer;
            # Dijkstra here is just a search heuristic.
            distances, predecessors = _scipy_dijkstra(
                sparse_g, directed=True,
                indices=start_node,
                return_predecessors=True,
            )
            first_hop_data.append((hop_idx, distances, predecessors))
            for d_idx in range(n_dest):
                d_node = int(d_node_ids[d_idx])
                total  = conn_w + float(distances[d_node])
                if total < best_dist[d_idx]:
                    best_dist[d_idx]      = total
                    best_first_hop[d_idx] = hop_idx

        # Reconstruct one path per destination using its best first-hop.
        # Collect arc indices used so we can penalize them after this iteration.
        # NB: best_dist[d_idx] above is PENALIZED distance.  We don't use it
        # for the cutoff check — see post-reconstruction check below.
        arcs_to_penalize: list = []
        origin_arcs_to_penalize: list = []
        for d_idx in range(n_dest):
            hop_idx = int(best_first_hop[d_idx])
            if hop_idx < 0:
                continue
            if not np.isfinite(best_dist[d_idx]):
                # No path found at all (Dijkstra returned inf) — skip.
                continue

            # Locate the (distances, predecessors) for the winning hop.
            distances, predecessors = None, None
            for stored in first_hop_data:
                if stored[0] == hop_idx:
                    distances, predecessors = stored[1], stored[2]
                    break
            if predecessors is None:
                continue

            d_node     = int(d_node_ids[d_idx])
            start_node = int(origin_neighbors[hop_idx])

            # Reconstruct: walk back from d_node to start_node via predecessors.
            packed_edges: list = []
            arc_indices: list  = []
            node = d_node
            ok = True
            while node != start_node:
                prev = int(predecessors[node])
                if prev < 0:
                    ok = False
                    break
                # Find arc prev → node in the CSR slice.  In multigraphs
                # pick the lowest-current-weight one (which is what
                # Dijkstra used).
                arc_idx = -1
                arc_w_min = np.inf
                for k in range(int(csr_indptr[prev]), int(csr_indptr[prev+1])):
                    if int(csr_indices[k]) == node:
                        w_k = float(work_weights[k])
                        if w_k < arc_w_min:
                            arc_w_min = w_k
                            arc_idx = k
                if arc_idx < 0:
                    ok = False
                    break
                arc_indices.append(arc_idx)
                eid = int(csr_edge_id[arc_idx])
                dr  = int(csr_direction[arc_idx])
                packed_edges.append((eid << 1) | dr)
                node = prev

            if not ok:
                continue

            # Origin connector arc (last popped, but first traversed).
            ohop_eid = int(origin_edge_ids[hop_idx])
            ohop_dr  = int(origin_dirs[hop_idx])
            packed_edges.append((ohop_eid << 1) | ohop_dr)

            # ── U-turn suppression ─────────────────────────────────────
            # Two adjacent packed edges referencing the same physical
            # edge_id represent a u-turn: the origin (or destination)
            # connector arc plus the same origin/destination edge as a
            # network arc going the other direction. Real pedestrian
            # trips do not backtrack through the same segment they just
            # entered, so we drop these paths from the K-alternatives
            # enumeration. packed_edges (before reverse) has:
            #   [0]  = destination connector arc
            #   [1]  = last network arc into d_start / d_end
            #   [-1] = origin connector arc
            #   [-2] = first network arc from o_start / o_end
            # Destination u-turn ↔ [0] and [1] share edge_id.
            # Origin      u-turn ↔ [-1] and [-2] share edge_id.
            #
            # Important: when we drop a u-turn path we must STILL queue
            # its arcs for penalization; otherwise the K-alternatives
            # outer loop's "no arcs to penalize → terminate" check
            # concludes there are no more paths and stops enumerating.
            n_pe = len(packed_edges)
            if n_pe >= 2 and (
                (packed_edges[0]  >> 1) == (packed_edges[1]  >> 1) or
                (packed_edges[-1] >> 1) == (packed_edges[-2] >> 1)
            ):
                arcs_to_penalize.extend(arc_indices)
                origin_arcs_to_penalize.append(hop_idx)
                continue

            # Reverse: path-order from origin-overlay → destination.
            packed_edges.reverse()
            n_edges_this_path = len(packed_edges)

            # Recompute weight with ORIGINAL (un-penalized) weights so the
            # downstream path penalty curve sees the real path length.
            orig_w = float(origin_weights_in[hop_idx])
            for ai in arc_indices:
                orig_w += float(csr_weights[ai])

            # Enforce the user's detour budget against the ACTUAL
            # (un-penalized) path length.
            #
            # Important: like the u-turn skip above, an over-budget
            # candidate must STILL have its arcs queued for
            # penalization. The penalty method explores in PENALIZED
            # order, not true-length order, so one over-budget candidate
            # does not mean no further within-budget alternatives exist.
            # Without penalization here, arcs_to_penalize stays empty and
            # the outer loop's early-termination check stops the whole
            # enumeration after the shortest path.
            if orig_w > d_cutoffs[d_idx] + d_tols[d_idx] + 1e-9:
                arcs_to_penalize.extend(arc_indices)
                origin_arcs_to_penalize.append(hop_idx)
                continue

            # Check buffer room and emit.
            if (out_n_paths >= max_total_paths
                    or out_n_edges + n_edges_this_path > max_total_path_edges):
                break
            out_path_offsets[out_n_paths]  = out_n_edges
            out_path_dest_idx[out_n_paths] = d_idx
            out_path_weights[out_n_paths]  = orig_w
            for packed in packed_edges:
                out_path_edges[out_n_edges] = packed
                out_n_edges += 1
            out_n_paths += 1

            if debug_print_paths:
                elev_str = "elev=disabled"
                if debug_node_z is not None and debug_arc_source is not None:
                    elev_gain = 0.0
                    for ai in arc_indices:
                        src_n  = int(debug_arc_source[ai])
                        head_n = int(csr_indices[ai])
                        z_src  = debug_node_z[src_n]
                        z_head = debug_node_z[head_n]
                        if np.isfinite(z_src) and np.isfinite(z_head):
                            dz = float(z_head) - float(z_src)
                            if dz > 0.0:
                                elev_gain += dz
                    elev_cost = elev_gain * float(debug_elevation_penalty)
                    elev_str = (
                        f"elev_gain={elev_gain:.2f}m "
                        f"(elev_cost={elev_cost:.2f})"
                    )
                print(
                    f"[paths]{debug_origin_tag} iter={iteration} "
                    f"dest_idx={d_idx} cost={orig_w:.2f} arcs={n_edges_this_path} "
                    f"(turns=disabled) {elev_str}"
                )

            # Queue arcs for penalization at end of iteration.
            arcs_to_penalize.extend(arc_indices)
            origin_arcs_to_penalize.append(hop_idx)

        # Apply penalties for the next iteration.
        if penalty_factor != 1.0:
            for ai in arcs_to_penalize:
                work_weights[ai] *= penalty_factor
            for hi in origin_arcs_to_penalize:
                work_origin_w[hi] *= penalty_factor

        # Early termination: if no path was emitted this iteration, no
        # point continuing — no further alternatives exist.
        if not arcs_to_penalize and not origin_arcs_to_penalize:
            break

    if out_n_paths < max_total_paths + 1:
        out_path_offsets[out_n_paths] = out_n_edges
    return out_n_paths, out_n_edges


# ──────────────────────────────────────────────────────────────────────
# Turn-aware variant of the K-alternatives enumerator.
#
# Operates on the precomputed line graph (states = arcs in the original
# CSR + PRE-u "ready to leave node u" states).  scipy Dijkstra runs on
# the line CSR, which gives turn-correct shortest paths for each
# iteration.  Penalty propagates correctly because the line CSR weights
# are recomputed from work_weights[line_arc_b] + line_turn_costs each
# iteration — multiplying work_weights[a] makes every transition that
# uses arc a more expensive.
# ──────────────────────────────────────────────────────────────────────

def _enumerate_k_alternatives_paths_turns(
    csr_indptr: np.ndarray,
    csr_indices: np.ndarray,
    csr_weights: np.ndarray,
    csr_edge_id: np.ndarray,
    csr_direction: np.ndarray,
    arc_source: np.ndarray,
    line_indptr: np.ndarray,
    line_indices: np.ndarray,
    line_arc_b: np.ndarray,
    line_turn_costs: np.ndarray,
    n_line_nodes: int,
    n_arcs_static: int,
    n_net: int,
    node_xy: np.ndarray,
    turn_threshold_deg: float,
    turn_penalty_amt: float,
    origin_neighbors: np.ndarray,
    origin_weights_in: np.ndarray,
    origin_edge_ids: np.ndarray,
    origin_dirs: np.ndarray,
    origin_n: int,
    d_node_ids: np.ndarray,
    d_goal_state_lists: list,         # list of int32 arrays, one per active dest
    d_cutoffs: np.ndarray,
    d_tols: np.ndarray,
    K: int,
    penalty_factor: float,
    out_path_edges: np.ndarray,
    out_path_offsets: np.ndarray,
    out_path_weights: np.ndarray,
    out_path_dest_idx: np.ndarray,
    debug_print_paths: bool = False,
    debug_origin_tag: str = "",
    # Optional debug-only inputs for elevation-gain reporting.
    debug_node_z=None,
    debug_elevation_penalty: float = 0.0,
):
    n_dest = int(d_node_ids.shape[0])
    if n_dest == 0 or K <= 0:
        out_path_offsets[0] = 0
        return 0, 0

    work_weights  = csr_weights.astype(np.float64, copy=True)
    work_origin_w = origin_weights_in.astype(np.float64, copy=True)

    out_n_paths = 0
    out_n_edges = 0
    max_total_paths      = out_path_offsets.shape[0] - 1
    max_total_path_edges = out_path_edges.shape[0]

    line_indptr_i64 = line_indptr.astype(np.int64, copy=False)

    for iteration in range(int(K)):
        # Refresh line CSR weights from current work_weights + static turn costs.
        line_weights = work_weights[line_arc_b] + line_turn_costs

        sparse_g = _scipy_csr(
            (line_weights, line_indices, line_indptr_i64),
            shape=(n_line_nodes, n_line_nodes),
        )

        # Best path per destination across origin-overlay hops.
        best_dist       = np.full(n_dest, np.inf, dtype=np.float64)
        best_first_hop  = np.full(n_dest, -1, dtype=np.int32)
        best_goal_state = np.full(n_dest, -1, dtype=np.int32)
        first_hop_data  = []   # list of (hop_idx, distances, predecessors)

        for hop_idx in range(int(origin_n)):
            u = int(origin_neighbors[hop_idx])
            pre_u = n_arcs_static + u  # PRE-u state ID
            conn_w = float(work_origin_w[hop_idx])

            distances, predecessors = _scipy_dijkstra(
                sparse_g, directed=True,
                indices=pre_u,
                return_predecessors=True,
            )
            first_hop_data.append((hop_idx, distances, predecessors))

            for d_idx in range(n_dest):
                goal_states = d_goal_state_lists[d_idx]
                if goal_states.size == 0:
                    continue
                gs_distances = distances[goal_states]
                best_in_goals = int(np.argmin(gs_distances))
                gs_dist = float(gs_distances[best_in_goals])
                if not np.isfinite(gs_dist):
                    continue
                total = conn_w + gs_dist
                if total < best_dist[d_idx]:
                    best_dist[d_idx]       = total
                    best_first_hop[d_idx]  = hop_idx
                    best_goal_state[d_idx] = int(goal_states[best_in_goals])

        # Reconstruct one path per destination using its best first-hop.
        arcs_to_penalize: list = []
        origin_arcs_to_penalize: list = []

        for d_idx in range(n_dest):
            hop_idx = int(best_first_hop[d_idx])
            if hop_idx < 0:
                continue
            if not np.isfinite(best_dist[d_idx]):
                continue

            distances, predecessors = None, None
            for stored in first_hop_data:
                if stored[0] == hop_idx:
                    distances, predecessors = stored[1], stored[2]
                    break
            if predecessors is None:
                continue

            # Walk predecessors from goal_state back through L_a states
            # to a PRE-u node.  Each L_a state's ID *is* the arc index.
            goal_state = int(best_goal_state[d_idx])
            arc_indices: list = []
            packed_edges: list = []
            node = goal_state
            ok = True
            steps = 0
            while node < n_arcs_static:
                a = node
                arc_indices.append(a)
                eid = int(csr_edge_id[a])
                dr  = int(csr_direction[a])
                packed_edges.append((eid << 1) | dr)
                prev = int(predecessors[node])
                if prev < 0:
                    ok = False
                    break
                node = prev
                steps += 1
                if steps > n_line_nodes:  # safety bound
                    ok = False
                    break

            if not ok:
                continue

            # Order arcs from origin → destination.
            arc_indices.reverse()
            packed_edges.reverse()

            # Prepend the origin connector arc (origin-overlay → first network node).
            ohop_eid = int(origin_edge_ids[hop_idx])
            ohop_dr  = int(origin_dirs[hop_idx])
            packed_edges.insert(0, (ohop_eid << 1) | ohop_dr)
            n_edges_this_path = len(packed_edges)

            # ── U-turn suppression ─────────────────────────────────────
            # Adjacent packed edges referencing the same physical edge_id
            # represent a u-turn: the origin (or destination) connector
            # arc plus that same edge as a network arc going the other
            # way. Real pedestrian trips do not backtrack through the
            # same segment they just entered, so we drop these paths
            # from the K-alternatives enumeration.
            # After reverse + insert(0, connector), packed_edges is in
            # trip order (origin → destination):
            #   [0]  = origin connector arc
            #   [1]  = first network arc (from o_start / o_end)
            #   [-1] = destination connector arc
            #   [-2] = last network arc (into d_start / d_end)
            # Origin      u-turn ↔ [0] and [1]   share edge_id.
            # Destination u-turn ↔ [-1] and [-2] share edge_id.
            #
            # Important: when we drop a u-turn path we must STILL queue
            # its arcs for penalization; otherwise the K-alternatives
            # outer loop's "no arcs to penalize → terminate" check
            # concludes there are no more paths and stops enumerating.
            if n_edges_this_path >= 2 and (
                (packed_edges[0]  >> 1) == (packed_edges[1]  >> 1) or
                (packed_edges[-1] >> 1) == (packed_edges[-2] >> 1)
            ):
                arcs_to_penalize.extend(arc_indices)
                origin_arcs_to_penalize.append(hop_idx)
                continue

            # Recompute weight with ORIGINAL (un-penalized) edge weights
            # plus the static turn costs along the path.
            orig_w = float(origin_weights_in[hop_idx])
            if arc_indices:
                orig_w += float(csr_weights[arc_indices[0]])
                for i in range(1, len(arc_indices)):
                    a_prev = arc_indices[i - 1]
                    a_curr = arc_indices[i]
                    orig_w += float(csr_weights[a_curr])
                    src_prev  = int(arc_source[a_prev])
                    curr_node = int(csr_indices[a_prev])
                    next_node = int(csr_indices[a_curr])
                    if src_prev < n_net and curr_node < n_net and next_node < n_net:
                        orig_w += _turn_cost_geometric(
                            node_xy, n_net, src_prev, curr_node, next_node,
                            turn_threshold_deg, turn_penalty_amt,
                        )

            # Over-budget candidates must still be penalized (same
            # reasoning as the u-turn skip): the penalty method explores
            # in penalized order, so an over-budget candidate does not
            # imply that no within-budget alternatives remain. Without
            # this, the outer loop's early-termination check stops the
            # enumeration after the shortest path.
            if orig_w > d_cutoffs[d_idx] + d_tols[d_idx] + 1e-9:
                arcs_to_penalize.extend(arc_indices)
                origin_arcs_to_penalize.append(hop_idx)
                continue

            if (out_n_paths >= max_total_paths
                    or out_n_edges + n_edges_this_path > max_total_path_edges):
                break
            out_path_offsets[out_n_paths]  = out_n_edges
            out_path_dest_idx[out_n_paths] = d_idx
            out_path_weights[out_n_paths]  = orig_w
            for packed in packed_edges:
                out_path_edges[out_n_edges] = packed
                out_n_edges += 1
            out_n_paths += 1

            if debug_print_paths:
                # Count how many junctions along this path exceeded the
                # turn threshold (i.e., contributed a nonzero turn cost).
                n_turns_this_path = 0
                turn_cost_total   = 0.0
                for i in range(1, len(arc_indices)):
                    a_prev = arc_indices[i - 1]
                    a_curr = arc_indices[i]
                    src_prev  = int(arc_source[a_prev])
                    curr_node = int(csr_indices[a_prev])
                    next_node = int(csr_indices[a_curr])
                    if src_prev < n_net and curr_node < n_net and next_node < n_net:
                        tc = _turn_cost_geometric(
                            node_xy, n_net, src_prev, curr_node, next_node,
                            turn_threshold_deg, turn_penalty_amt,
                        )
                        if tc > 0:
                            n_turns_this_path += 1
                            turn_cost_total  += tc
                elev_str = "elev=disabled"
                if debug_node_z is not None:
                    elev_gain = 0.0
                    for ai in arc_indices:
                        src_n  = int(arc_source[ai])
                        head_n = int(csr_indices[ai])
                        z_src  = debug_node_z[src_n]
                        z_head = debug_node_z[head_n]
                        if np.isfinite(z_src) and np.isfinite(z_head):
                            dz = float(z_head) - float(z_src)
                            if dz > 0.0:
                                elev_gain += dz
                    elev_cost = elev_gain * float(debug_elevation_penalty)
                    elev_str = (
                        f"elev_gain={elev_gain:.2f}m "
                        f"(elev_cost={elev_cost:.2f})"
                    )
                print(
                    f"[paths]{debug_origin_tag} iter={iteration} "
                    f"dest_idx={d_idx} cost={orig_w:.2f} "
                    f"arcs={len(arc_indices)} turns={n_turns_this_path} "
                    f"(turn_cost_total={turn_cost_total:.2f}) {elev_str}"
                )

            arcs_to_penalize.extend(arc_indices)
            origin_arcs_to_penalize.append(hop_idx)

        if penalty_factor != 1.0:
            for ai in arcs_to_penalize:
                work_weights[ai] *= penalty_factor
            for hi in origin_arcs_to_penalize:
                work_origin_w[hi] *= penalty_factor

        if not arcs_to_penalize and not origin_arcs_to_penalize:
            break

    if out_n_paths < max_total_paths + 1:
        out_path_offsets[out_n_paths] = out_n_edges
    return out_n_paths, out_n_edges


class Flow(Base):

    edge_flow:    np.ndarray = None
    edge_flow_AB: np.ndarray = None
    edge_flow_BA: np.ndarray = None
    node_flow:    np.ndarray = None

    _digraph: nx.DiGraph = None
    _n_network_nodes: int = 0
    _n_destinations: int = 0
    _node_xy: np.ndarray = None

    # Route-alternatives records (v2.5.2) — populated by Centrality when
    # settings.flow_output_routes is True.
    _route_records: list = None
    _route_records_lock = None

    # CSR state populated at Centrality entry.
    _csr_indptr: np.ndarray = None
    _csr_indices: np.ndarray = None
    _csr_weights: np.ndarray = None
    _csr_edge_id: np.ndarray = None
    _csr_direction: np.ndarray = None
    _csr_fwd = None
    _csr_rev = None

    # Turn-aware line-graph state (populated only when settings.turns=True).
    _line_indptr:      np.ndarray = None
    _line_indices:     np.ndarray = None
    _line_arc_b:       np.ndarray = None
    _line_turn_costs:  np.ndarray = None
    _line_n_nodes:     int        = 0
    _n_arcs_static:    int        = 0
    _arc_source:       np.ndarray = None
    _dest_goal_states: dict       = None

    def __init__(self, topology: Topology) -> None:
        super().__init__(topology)
        net   = topology.network
        dest  = topology.destinations
        self._n_network_nodes = int(net.node_points.shape[0])
        self._n_destinations  = int(len(dest.node_weight))
        self._node_xy         = net.node_points[:, :2].astype(np.float64, copy=False)
        # Per-edge endpoint lookup (used only when node betweenness is
        # requested).  Shape: (n_edges, 2):
        #   col 0 = "to" node for direction-0 arcs (start→end traversal)
        #   col 1 = "to" node for direction-1 arcs (end→start traversal)
        # An arc with (eid, dir_bit) resolves to-node as
        # edge_endpoints[eid, dir_bit].
        n_edges = int(len(net.start_nodes))
        self._edge_endpoints = np.column_stack([
            net.end_nodes  [:n_edges].astype(np.int32, copy=False),
            net.start_nodes[:n_edges].astype(np.int32, copy=False),
        ])

    # ──────────────────────────────────────────────────────────────────
    # Settings validation
    # ──────────────────────────────────────────────────────────────────

    def _prepare_params(self, s: Settings) -> dict:
        """Resolve Settings into a flat runtime-params dict.

        Settings.Validation() is assumed to have already run (field-level
        checks live there).  This method only handles engine/topology
        cross-checks, deprecation warnings that need the logger, and the
        derived value computation (effective betas, alias resolution, etc.).
        """
        if not _SCIPY_OK:
            raise RuntimeError(
                "Flow engine requires scipy.sparse.csgraph.  "
                "Install scipy (>= 1.7) to proceed."
            )

        # Topology cross-check: assigned routing requires the topology to
        # have been built with the matching ID columns.
        ord_col = (s.origin_destination_id_column or "").strip()
        dst_col = (s.destination_id_column        or "").strip()
        assigned_routing_active = bool(ord_col and dst_col)
        if assigned_routing_active:
            if getattr(self.topology.origins, "assigned_dest_id", None) is None:
                raise RuntimeError(
                    "origin_destination_id_column is set but the topology "
                    "was built without it.  Rebuild the topology after "
                    "setting origin_destination_id_column (or delete the "
                    "topology cache)."
                )
            if getattr(self.topology.destinations, "id_to_dest_indices", None) is None:
                raise RuntimeError(
                    "destination_id_column is set but the topology was "
                    "built without it.  Rebuild the topology after "
                    "setting destination_id_column (or delete the "
                    "topology cache)."
                )

        ratio        = float(s.flow_detour_ratio)
        buffer       = float(s.flow_detour_buffer)
        mode         = str(s.flow_detour_mode).strip().lower()
        decay        = bool(s.flow_decay)
        decay_curve  = _normalize_method(s.flow_decay_curve)
        decay_method = str(s.flow_decay_method).strip().lower()
        gravity_cap  = float(s.flow_gravity_cap)

        raw_pp = str(s.flow_path_detour_penalty).strip().lower()
        path_penalty = "equal" if raw_pp in ("", "equal", "uniform", "flat") else _normalize_method(raw_pp)

        gravity_midpoint = float(s.gravity_logistic_midpoint)
        route_beta       = float(s.flow_route_enumeration_beta)
        route_midpoint   = float(s.flow_route_enumeration_logistic_midpoint)

        if path_penalty == "exponential" and route_beta <= 0.0:
            self.logger.log(
                "Flow",
                "WARNING: flow_path_detour_penalty='exponential' but "
                "flow_route_enumeration_beta=0.  All paths in each OD "
                "bundle will share equal weight (decay degenerates to flat).  "
                "Set flow_route_enumeration_beta > 0.", v=1,
            )
        if path_penalty == "logistic" and route_midpoint <= 0.0 and route_beta <= 0.0:
            self.logger.log(
                "Flow",
                "WARNING: flow_path_detour_penalty='logistic' but "
                "neither flow_route_enumeration_logistic_midpoint nor "
                "flow_route_enumeration_beta is set.  Set the midpoint "
                "to the path cost at which split weight = 0.5; "
                "k = ln(99)/midpoint is auto-derived.", v=1,
            )

        # Derive effective betas from midpoints (logistic convention:
        # k = ln(99)/midpoint places the 1%/99% endpoints symmetrically).
        gravity_beta_raw = float(s.gravity_beta)
        if decay and decay_method == "closest" and decay_curve == "logistic" and gravity_midpoint > 0.0:
            gravity_beta_effective = s.gravity_decay_constant / gravity_midpoint
            if gravity_beta_raw > 0.0 and abs(gravity_beta_raw - gravity_beta_effective) > 1e-9:
                self.logger.log(
                    "Flow",
                    f"Logistic decay (closest mode): auto-derived gravity "
                    f"beta = ln(99)/midpoint = {gravity_beta_effective:.6f} "
                    f"(midpoint={gravity_midpoint:.1f}); "
                    f"user-set gravity_beta={gravity_beta_raw:.6f} ignored.", v=1,
                )
        else:
            gravity_beta_effective = gravity_beta_raw

        if path_penalty == "logistic" and route_midpoint > 0.0:
            route_beta_effective = s.gravity_decay_constant / route_midpoint
            if route_beta > 0.0 and abs(route_beta - route_beta_effective) > 1e-9:
                self.logger.log(
                    "Flow",
                    f"Logistic path penalty: auto-derived route beta = "
                    f"ln(99)/midpoint = {route_beta_effective:.6f} "
                    f"(midpoint={route_midpoint:.1f}); "
                    f"user-set flow_route_enumeration_beta={route_beta:.6f} ignored.", v=1,
                )
        else:
            route_beta_effective = route_beta

        # Resolve deprecated k_nearest alias; warn if both are set.
        k_nearest_new = int(s.flow_k_nearest_destinations)
        k_nearest_old = int(s.flow_max_destinations_per_origin)
        if k_nearest_new > 0 and k_nearest_old > 0 and k_nearest_new != k_nearest_old:
            self.logger.log(
                "Flow",
                f"WARNING: both flow_k_nearest_destinations={k_nearest_new} and "
                f"the deprecated alias flow_max_destinations_per_origin={k_nearest_old} "
                f"are set with different values.  Using the new name ({k_nearest_new}).", v=1,
            )
        elif k_nearest_old > 0 and k_nearest_new == 0:
            self.logger.log(
                "Flow",
                f"DEPRECATION: flow_max_destinations_per_origin={k_nearest_old} "
                f"has been renamed to flow_k_nearest_destinations.  "
                f"The old name still works but will be removed in a future release.", v=1,
            )
        k_nearest = k_nearest_new if k_nearest_new > 0 else k_nearest_old

        # Topology cross-check: cluster buffer must cover search_radius.
        if self.topology.has_clusters:
            buf = getattr(self.topology, "cluster_buffer_radius", None)
            if buf is not None and buf < float(s.search_radius) - 1e-9:
                raise ValueError(
                    f"Topology clusters were built with buffer={buf}m but "
                    f"settings.search_radius={s.search_radius}m. Rebuild "
                    f"clusters via topology.BuildClusters(n, search_radius)."
                )

        return dict(
            search_radius              = float(s.search_radius),
            beta                       = gravity_beta_effective,
            plateau                    = float(s.gravity_plateau),
            closest_dest               = bool(s.use_nearest_destination),
            ratio                      = ratio,
            buffer                     = buffer,
            mode                       = mode,
            decay                      = decay,
            decay_curve                = decay_curve,
            decay_method               = decay_method,
            gravity_cap                = gravity_cap,
            gravity_midpoint           = gravity_midpoint,
            path_penalty               = path_penalty,
            route_beta                 = route_beta_effective,
            route_midpoint             = route_midpoint,
            use_o_weights              = bool(s.flow_origin_weights),
            use_d_weights              = bool(s.flow_destination_weights),
            use_turns                  = bool(s.turns),
            turn_thresh                = float(s.turn_threshold),
            turn_amt                   = float(s.turn_penalty),
            elevation                  = bool(s.elevation),
            elevation_penalty          = float(s.elevation_penalty),
            k_nearest                  = k_nearest,
            n_alternatives             = int(s.flow_n_alternatives),
            alternative_penalty_factor = float(s.flow_alternative_penalty_factor),
            assigned_routing           = assigned_routing_active,
            track_origins_per_dest     = bool(s.flow_track_origins_per_destination),
            compute_node_flow          = bool(s.flow_compute_node_flow),
            return_directional         = bool(s.flow_return_directional),
            debug_print_paths          = bool(s.flow_debug_print_paths),
            output_routes              = bool(s.flow_output_routes),
        )

    # ──────────────────────────────────────────────────────────────────
    # Directed-graph build (NetworkX) + CSR build
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _add_directed_connector(G, net_node, virtual_node, weight, edge_id, dir_bit, bidirectional=True):
        if not G.has_edge(net_node, virtual_node):
            G.add_edge(net_node, virtual_node, weight=weight, id=edge_id, dir=dir_bit)
        elif weight < G[net_node][virtual_node]["weight"]:
            G[net_node][virtual_node].update(weight=weight, id=edge_id, dir=dir_bit)
        if bidirectional:
            if not G.has_edge(virtual_node, net_node):
                G.add_edge(virtual_node, net_node, weight=weight, id=edge_id, dir=dir_bit)
            elif weight < G[virtual_node][net_node]["weight"]:
                G[virtual_node][net_node].update(weight=weight, id=edge_id, dir=dir_bit)

    def _build_digraph(self, elevation=False, elevation_penalty=4.0):
        net   = self.topology.network
        dest  = self.topology.destinations
        n_net = self._n_network_nodes
        n_dest= self._n_destinations
        has_z    = (net.z is not None) and elevation
        z_values = net.z if has_z else None

        G = nx.DiGraph()
        G.add_nodes_from(range(n_net))

        for i in range(len(net.start_nodes)):
            u = int(net.start_nodes[i])
            v = int(net.end_nodes[i])
            w = float(net.weights[i])
            if has_z:
                dz_uv = max(0.0, float(z_values[v]) - float(z_values[u]))
                dz_vu = max(0.0, float(z_values[u]) - float(z_values[v]))
                w_uv  = w + elevation_penalty * dz_uv
                w_vu  = w + elevation_penalty * dz_vu
            else:
                w_uv = w_vu = w
            if G.has_edge(u, v):
                if w_uv < G[u][v]["weight"]:
                    G[u][v].update(weight=w_uv, id=i, dir=0)
            else:
                G.add_edge(u, v, weight=w_uv, id=i, dir=0)
            if G.has_edge(v, u):
                if w_vu < G[v][u]["weight"]:
                    G[v][u].update(weight=w_vu, id=i, dir=1)
            else:
                G.add_edge(v, u, weight=w_vu, id=i, dir=1)

        d_corr_start, d_corr_end = self.topology.get_partial_edge_corrections(
            dest, for_origins=False
        )
        for d_pos in range(n_dest):
            d_node  = n_net + d_pos
            d_start = int(dest.edge_start_node[d_pos])
            d_end   = int(dest.edge_end_node[d_pos])
            near_id = int(dest.nearest_edge_id[d_pos])
            w_start = float(dest.weight_to_start[d_pos])
            w_end   = float(dest.weight_to_end[d_pos])
            if d_corr_start is not None:
                w_start += float(d_corr_start[d_pos])
                w_end   += float(d_corr_end[d_pos])
            G.add_node(d_node, node_type="destination", d_pos=d_pos)
            self._add_directed_connector(G, d_start, d_node, w_start, near_id, dir_bit=0)
            if d_end != d_start:
                self._add_directed_connector(G, d_end, d_node, w_end, near_id, dir_bit=1)

        self._digraph = G
        self.logger.log(
            "Flow",
            f"DiGraph: {n_net} net nodes, {n_dest} dest nodes, "
            f"{G.number_of_edges()} arcs.",
            v=1,
        )

    def _build_csr(self, use_turns: bool = False,
                   turn_threshold_deg: float = 45.0,
                   turn_penalty_amt: float = 0.0,
                   elevation: bool = False,
                   elevation_penalty: float = 0.0):
        n_total = self._n_network_nodes + self._n_destinations
        indptr, indices, weights, edge_id, direction = (
            _bnumba.build_csr_from_digraph(self._digraph, n_total)
        )
        self._csr_indptr    = indptr
        self._csr_indices   = indices
        self._csr_weights   = weights
        self._csr_edge_id   = edge_id
        self._csr_direction = direction

        # v2.4.4 — inject obstacle penalties into CSR weights, BEFORE
        # any Dijkstra / line-graph construction.  Obstacles compose
        # additively with whatever the digraph already has (custom edge
        # costs, elevation-direction penalties, turn penalties).  This
        # is the single point where obstacles enter the flow
        # engine — line-graph rebuild below will pick them up too.
        if (getattr(self.topology, "obstacles", None) is not None):
            p_AB, p_BA = self.topology.get_obstacle_arc_penalties()
            if p_AB is not None:
                edge_valid = self._csr_edge_id >= 0
                ab_mask    = edge_valid & (self._csr_direction == 0)
                ba_mask    = edge_valid & (self._csr_direction == 1)
                self._csr_weights[ab_mask] += p_AB[
                    self._csr_edge_id[ab_mask].astype(np.int64)
                ]
                self._csr_weights[ba_mask] += p_BA[
                    self._csr_edge_id[ba_mask].astype(np.int64)
                ]
                if self.logger is not None:
                    self.logger.log(
                        "Flow",
                        f"Applied obstacle penalties to CSR: "
                        f"AB Σ={p_AB.sum():.2f}, BA Σ={p_BA.sum():.2f} "
                        f"across {int(edge_valid.sum())} arcs.", v=1,
                    )

        self._csr_fwd = _scipy_csr(
            (self._csr_weights, indices, indptr.astype(np.int64)),
            shape=(n_total, n_total),
        )
        # _csr_rev kept for elastic-Phase-1 helpers / future use.
        self._csr_rev = self._csr_fwd.T.tocsr()

        # Always compute arc_source — needed by the per-path debug print
        # (turn count, elevation gain) regardless of turns/elevation flags.
        n_arcs = int(indices.shape[0])
        arc_source = np.empty(n_arcs, dtype=np.int32)
        for u in range(n_total):
            s, e = int(indptr[u]), int(indptr[u + 1])
            if e > s:
                arc_source[s:e] = u
        self._arc_source    = arc_source
        self._n_arcs_static = n_arcs

        # Stash node z-values (network nodes only — virtual destinations
        # have no elevation).  Used by the per-path debug print when
        # elevation is enabled.  Pad with NaN for virtual nodes so the
        # node index space matches the CSR shape.
        net_z = getattr(self.topology.network, "z", None)
        if elevation and net_z is not None:
            z_full = np.full(n_total, np.nan, dtype=np.float64)
            z_full[:self._n_network_nodes] = np.asarray(net_z, dtype=np.float64)
            self._node_z           = z_full
            self._elevation_penalty = float(elevation_penalty)
        else:
            self._node_z            = None
            self._elevation_penalty = 0.0

        # When turns are enabled, also build the turn-aware line graph.
        if use_turns:
            t0 = time.perf_counter()
            (line_indptr, line_indices, line_arc_b, line_turn_costs,
             n_line_nodes, n_arcs_static, arc_source,
             dest_goal_states) = _build_turn_aware_line_csr(
                self._csr_indptr, self._csr_indices,
                self._n_network_nodes, self._n_destinations,
                self._node_xy,
                turn_threshold_deg, turn_penalty_amt,
            )
            self._line_indptr      = line_indptr
            self._line_indices     = line_indices
            self._line_arc_b       = line_arc_b
            self._line_turn_costs  = line_turn_costs
            self._line_n_nodes     = n_line_nodes
            self._n_arcs_static    = n_arcs_static
            self._arc_source       = arc_source
            self._dest_goal_states = dest_goal_states
            dt = time.perf_counter() - t0
            n_turn_nonzero = int(np.count_nonzero(line_turn_costs))
            self.logger.log(
                "Flow",
                f"Turn-aware line graph built in {dt:.2f}s: "
                f"{n_line_nodes:,} states, {int(line_indptr[-1]):,} transitions "
                f"({n_turn_nonzero:,} carry a nonzero turn penalty; "
                f"threshold={turn_threshold_deg:.1f}°, "
                f"per-turn penalty={turn_penalty_amt:.2f}).",
                v=1,
            )
        else:
            self._line_indptr      = None
            self._line_indices     = None
            self._line_arc_b       = None
            self._line_turn_costs  = None
            self._line_n_nodes     = 0
            self._n_arcs_static    = 0
            self._arc_source       = None
            self._dest_goal_states = None

    # ──────────────────────────────────────────────────────────────────
    # Per-origin worker (k_alternatives only)
    # ──────────────────────────────────────────────────────────────────

    def _process_origin_csr(
        self,
        o_pos, o_weight,
        o_start, o_end, near_id, w_start, w_end,
        dest_filter_arr, ns,
        n_edges, n_total_nodes,
        out_path_edges, out_path_offsets,
        out_path_weights, out_path_dest_idx,
        d_idx_lookup_buf,
        local_bw_AB, local_bw_BA,
        local_node_bw=None,
    ):
        n_net  = self._n_network_nodes
        n_dest = self._n_destinations

        # Build origin-overlay arcs (one or two — origin connects to one
        # or both endpoints of its nearest edge).
        if o_end != o_start:
            origin_n = 2
            origin_neighbors = np.array([o_start, o_end], dtype=np.int32)
            origin_weights   = np.array([w_start, w_end], dtype=np.float64)
            origin_edge_ids  = np.array([near_id, near_id], dtype=np.int32)
            origin_dirs      = np.array([0, 1], dtype=np.int8)
        else:
            origin_n = 1
            origin_neighbors = np.array([o_start], dtype=np.int32)
            origin_weights   = np.array([w_start], dtype=np.float64)
            origin_edge_ids  = np.array([near_id], dtype=np.int32)
            origin_dirs      = np.array([0], dtype=np.int8)

        # ----- preflight: forward Dijkstra to learn shortest dists ----
        # k_alternatives needs per-destination d_shortest (for the cutoff
        # envelope and for downstream per-path-penalty normalization).
        # When turns are enabled the preflight runs on the line graph so
        # d_shortest accounts for per-intersection turn costs; otherwise
        # one multi-source Dijkstra on the original CSR is sufficient.
        use_turns_engine = bool(ns.get("use_turns", False)) and (
            self._line_indptr is not None
        )

        fwd_limit = float(ns["search_radius"]) - float(min(origin_weights))
        if fwd_limit < 0:
            return

        if not use_turns_engine:
            fwd = _scipy_dijkstra(
                self._csr_fwd, directed=True,
                indices=origin_neighbors,
                limit=fwd_limit,
                return_predecessors=False,
            )
            dist_from_o = np.minimum.reduce(
                [fwd[i] + origin_weights[i] for i in range(origin_n)]
            )
        else:
            # Turn-aware preflight on the line graph.  scipy_dijkstra
            # doesn't support per-source initial distances, so we run one
            # Dijkstra per first-hop PRE-u state and aggregate manually.
            n_total_orig = n_net + n_dest
            base_line_weights = (
                self._csr_weights[self._line_arc_b] + self._line_turn_costs
            )
            sparse_line_pre = _scipy_csr(
                (base_line_weights, self._line_indices,
                 self._line_indptr.astype(np.int64)),
                shape=(self._line_n_nodes, self._line_n_nodes),
            )
            hop_distances: list = []
            for i in range(origin_n):
                u = int(origin_neighbors[i])
                pre_u = self._n_arcs_static + u
                per_hop_limit = float(ns["search_radius"]) - float(origin_weights[i])
                if per_hop_limit < 0:
                    hop_distances.append(None)
                    continue
                d_arr = _scipy_dijkstra(
                    sparse_line_pre, directed=True,
                    indices=pre_u,
                    limit=per_hop_limit,
                    return_predecessors=False,
                )
                hop_distances.append(d_arr)

            # Per-destination shortest total (origin connector + best goal state).
            dist_from_o = np.full(n_total_orig, np.inf, dtype=np.float64)
            for d_pos_iter in range(n_dest):
                d_node_iter = n_net + d_pos_iter
                goal_states = self._dest_goal_states[d_pos_iter]
                if goal_states.size == 0:
                    continue
                best_total = np.inf
                for i in range(origin_n):
                    d_arr = hop_distances[i]
                    if d_arr is None:
                        continue
                    gs_d = float(d_arr[goal_states].min())
                    if not np.isfinite(gs_d):
                        continue
                    total = float(origin_weights[i]) + gs_d
                    if total < best_total:
                        best_total = total
                dist_from_o[d_node_iter] = best_total

        # ----- eligible destinations within search_radius -------------
        d_node_ids_full = dest_filter_arr
        eligible_mask = dist_from_o[d_node_ids_full] <= ns["search_radius"]
        d_node_ids = d_node_ids_full[eligible_mask].astype(np.int32, copy=False)
        if d_node_ids.size == 0:
            return
        d_shortest_arr = dist_from_o[d_node_ids]

        # ---- per-origin destination assignment -----------------------
        if ns["assigned_routing"]:
            origins      = self.topology.origins
            destinations = self.topology.destinations
            origin_id = origins.assigned_dest_id[o_pos]
            allowed = destinations.id_to_dest_indices.get(origin_id, None)
            if allowed is None or len(allowed) == 0:
                with self._assigned_stats_lock:
                    self._assigned_stats["no_match"] += 1
                if self.logger is not None:
                    self.logger.log(
                        "Flow",
                        f"  origin idx {o_pos}: assigned ID {origin_id!r} "
                        f"matches no destinations — skipped.", v=2,
                    )
                return
            allowed_nodes = np.asarray(allowed, dtype=np.int32) + n_net
            keep_mask = np.isin(d_node_ids, allowed_nodes)
            d_node_ids     = d_node_ids[keep_mask]
            d_shortest_arr = d_shortest_arr[keep_mask]
            if d_node_ids.size == 0:
                with self._assigned_stats_lock:
                    self._assigned_stats["unreachable"] += 1
                if self.logger is not None:
                    self.logger.log(
                        "Flow",
                        f"  origin idx {o_pos}: assigned ID {origin_id!r} "
                        f"not reachable within {ns['search_radius']}m — skipped.",
                        v=2,
                    )
                return

        if ns["closest_dest"]:
            i = int(np.argmin(d_shortest_arr))
            d_node_ids     = d_node_ids[i:i+1]
            d_shortest_arr = d_shortest_arr[i:i+1]

        n_dest_active = d_node_ids.shape[0]
        n_max_dest    = _effective_max_dests(ns["k_nearest"])
        if n_dest_active > n_max_dest:
            n_orig_active = int(n_dest_active)
            order = np.argsort(d_shortest_arr)[:n_max_dest]
            d_node_ids     = d_node_ids[order]
            d_shortest_arr = d_shortest_arr[order]
            n_dest_active  = n_max_dest
            if self.logger is not None:
                self.logger.log(
                    "Flow",
                    f"  origin idx {o_pos}: {n_orig_active} destinations "
                    f"reachable, clamped to nearest {n_max_dest}.", v=1,
                )

        # ----- per-OD cutoffs / tolerances ----------------------------
        if ns["mode"] == "ratio":
            d_cutoffs = d_shortest_arr * ns["ratio"]
        elif ns["mode"] == "buffer":
            d_cutoffs = d_shortest_arr + ns["buffer"]
        else:
            d_cutoffs = np.minimum(
                d_shortest_arr * ns["ratio"], d_shortest_arr + ns["buffer"],
            )
        d_tols = np.maximum(1e-4, np.abs(d_shortest_arr) * 1e-5)

        # ----- destination gravities ----------------------------------
        d_weights = self.topology.destinations.node_weight.astype(np.float64)
        if ns["use_d_weights"] and not ns["closest_dest"]:
            d_idx_in_dest = d_node_ids - n_net
            base_w = d_weights[d_idx_in_dest]
            eff = np.maximum(0.0, d_shortest_arr - ns["plateau"])
            grav = base_w * np.exp(-ns["beta"] * eff) if ns["decay"] else base_w
            grav_sum = float(grav.sum())
            if grav_sum <= 0.0:
                return
        else:
            grav = np.ones(n_dest_active, dtype=np.float64)
            grav_sum = float(n_dest_active)

        # ----- d_idx_lookup --------------------------------------------
        d_idx_lookup_buf.fill(-1)
        for k in range(n_dest_active):
            d_idx_lookup_buf[d_node_ids[k]] = k

        # ----- enumerate K alternative paths --------------------------
        # NB: the `n_total` argument to scipy's CSR shape must match the
        # original CSR (which covers network + destination nodes only —
        # the virtual origin node lives outside the CSR and is handled
        # via `origin_neighbors`/`origin_weights` overlays).  This is
        # n_net + n_dest, distinct from the `n_total_nodes` size used to
        # allocate `d_idx_lookup_buf` (which needs the +1 origin slot).
        n_total_for_csr = int(n_net + n_dest)
        _dbg_paths = bool(ns.get("debug_print_paths", False))
        _dbg_tag   = f" o_pos={o_pos}"
        t_origin = time.perf_counter()
        if not use_turns_engine:
            n_paths, n_edges_out = _enumerate_k_alternatives_paths(
                self._csr_indptr, self._csr_indices, self._csr_weights,
                self._csr_edge_id, self._csr_direction,
                origin_neighbors, origin_weights, origin_edge_ids, origin_dirs,
                int(origin_n),
                d_node_ids, d_cutoffs, d_tols,
                int(ns["n_alternatives"]),
                float(ns["alternative_penalty_factor"]),
                n_total_for_csr,
                out_path_edges, out_path_offsets, out_path_weights, out_path_dest_idx,
                debug_print_paths=_dbg_paths,
                debug_origin_tag=_dbg_tag,
                debug_arc_source=self._arc_source if _dbg_paths else None,
                debug_node_z=self._node_z if _dbg_paths else None,
                debug_elevation_penalty=self._elevation_penalty,
            )
        else:
            # Turn-aware enumeration on the line graph.  Build the per-
            # active-destination goal-state lists from the precomputed
            # dict; small slices, negligible overhead.
            d_goal_state_lists = [
                self._dest_goal_states[int(d_node_ids[k]) - n_net]
                for k in range(n_dest_active)
            ]
            # Warn (once per origin) about destinations that have no incoming
            # arcs in the CSR — these are unreachable in the line graph and
            # silently contribute nothing.  Common cause: a destination point
            # that snapped poorly to the network during topology build.
            if self.logger is not None:
                empty_dests = [
                    int(d_node_ids[k]) - n_net
                    for k in range(n_dest_active)
                    if d_goal_state_lists[k].size == 0
                ]
                if empty_dests:
                    self.logger.log(
                        "Flow",
                        f"  origin idx {o_pos}: {len(empty_dests)} "
                        f"destination(s) have no incoming arcs in the "
                        f"line graph and will contribute nothing. "
                        f"Destination array indices: {empty_dests[:10]}"
                        + (" ..." if len(empty_dests) > 10 else "")
                        + ".  Likely cause: poor snap to the network during "
                        f"topology build; check the destination geometry.",
                        v=1,
                    )
            n_paths, n_edges_out = _enumerate_k_alternatives_paths_turns(
                self._csr_indptr, self._csr_indices, self._csr_weights,
                self._csr_edge_id, self._csr_direction,
                self._arc_source,
                self._line_indptr, self._line_indices,
                self._line_arc_b, self._line_turn_costs,
                int(self._line_n_nodes), int(self._n_arcs_static),
                int(n_net), self._node_xy,
                float(ns["turn_thresh"]), float(ns["turn_amt"]),
                origin_neighbors, origin_weights, origin_edge_ids, origin_dirs,
                int(origin_n),
                d_node_ids, d_goal_state_lists, d_cutoffs, d_tols,
                int(ns["n_alternatives"]),
                float(ns["alternative_penalty_factor"]),
                out_path_edges, out_path_offsets, out_path_weights, out_path_dest_idx,
                debug_print_paths=_dbg_paths,
                debug_origin_tag=_dbg_tag,
                debug_node_z=self._node_z if _dbg_paths else None,
                debug_elevation_penalty=self._elevation_penalty,
            )

        if self.logger is not None:
            k_req = int(ns["n_alternatives"])
            if n_paths < k_req:
                self.logger.log(
                    "Flow",
                    f"  origin idx {o_pos}: k_alternatives emitted "
                    f"{int(n_paths)}/{k_req} paths "
                    f"(remaining alternatives infeasible — penalty saturated "
                    f"the detour budget).  Lower "
                    f"`flow_alternative_penalty_factor` or widen "
                    f"`flow_detour_ratio` to extract more.", v=2,
                )
            else:
                self.logger.log(
                    "Flow",
                    f"  origin idx {o_pos}: k_alternatives emitted "
                    f"all {int(n_paths)} requested paths.", v=2,
                )

        if n_paths == 0:
            return

        # ----- record origin-per-destination tracking ----------------
        if ns["track_origins_per_dest"]:
            unique_local_dests = np.unique(out_path_dest_idx[:n_paths])
            origin_uid = self.topology.origins.uid[o_pos]
            for d_local in unique_local_dests:
                d_node_id  = int(d_node_ids[d_local])
                dest_array_idx = d_node_id - n_net
                self._origin_tracking_records.append(
                    (origin_uid, dest_array_idx, float(o_weight))
                )

        # ----- record route alternatives for export (v2.5.2) ----------
        # Captures every emitted path (packed edge list, cost, rank) so
        # ExportFlowResult can write full route geometries.  The route id
        # is the origin's assigned id (flow_route_id_column /
        # origin_destination_id_column) when pairing is active, else the
        # origin uid.  Buffers are reused per-origin, hence the .copy().
        if ns["output_routes"]:
            origins_obj = self.topology.origins
            o_uid = origins_obj.uid[o_pos]
            route_id_val = (origins_obj.assigned_dest_id[o_pos]
                            if ns["assigned_routing"] else o_uid)
            recs = []
            rank_count: dict = {}
            for j in range(int(n_paths)):
                s_off  = int(out_path_offsets[j])
                e_off  = int(out_path_offsets[j + 1])
                packed = out_path_edges[s_off:e_off].copy()
                d_arr_idx = int(d_node_ids[int(out_path_dest_idx[j])]) - n_net
                r = rank_count.get(d_arr_idx, 0) + 1
                rank_count[d_arr_idx] = r
                recs.append((
                    route_id_val, o_uid, d_arr_idx, r,
                    float(out_path_weights[j]), packed,
                ))
            with self._route_records_lock:
                self._route_records.extend(recs)

        # ----- per-path factor (penalty + decay + trip-prob + o_w) ----
        # Penalty operates on EXCESS length over shortest (OD-independent).
        _path_shortest = d_shortest_arr[out_path_dest_idx[:n_paths]]
        _path_excess   = np.maximum(0.0, out_path_weights[:n_paths] - _path_shortest)
        if ns["path_penalty"] == "exponential":
            penalties = np.exp(-ns["route_beta"] * _path_excess)
        elif ns["path_penalty"] == "logistic":
            penalties = 1.0 / (1.0 + np.exp(
                ns["route_beta"] * (_path_excess - ns["route_midpoint"])
            ))
        else:
            penalties = np.ones(n_paths, dtype=np.float64)

        path_dest_idx = out_path_dest_idx[:n_paths]
        sum_penalty = np.zeros(n_dest_active, dtype=np.float64)
        np.add.at(sum_penalty, path_dest_idx, penalties)
        with np.errstate(divide="ignore", invalid="ignore"):
            denom = sum_penalty[path_dest_idx]
            path_probs = np.where(denom > 0, penalties / denom, 0.0)

        # v2.4.3 — Per-origin trip-generation factor.
        #
        # Replaces v2.4.2's OD-level `decays[d]` multiplier with a single
        # scalar that depends only on properties of the origin's
        # destination set as a whole.  This eliminates the
        # non-monotonicity bug where adding farther destinations could
        # reduce total trip generation (because the weighted-average of
        # `decay × trip_prob` was getting pulled toward zero by the
        # far-tail decays).
        #
        # Two aggregation methods (chosen via flow_decay_method):
        #   "closest"     factor = curve(min(d) - plateau)
        #                  where curve is exponential or logistic.
        #                  Parameter-free relative to the closest dest;
        #                  farther dests do not change trip generation.
        #   "gravity_cap" factor = min(1, gravity / gravity_cap)
        #                  where gravity = Σ d_weight × distance_decay
        #                  within search_radius (or destination count if
        #                  use_d_weights=False).  Honors decay_curve.
        if not ns["decay"]:
            origin_factor = 1.0
        elif ns["decay_method"] == "closest":
            min_d   = float(d_shortest_arr.min())
            eff_min = max(0.0, min_d - ns["plateau"])
            if ns["decay_curve"] == "logistic":
                origin_factor = 1.0 / (1.0 + math.exp(
                    ns["beta"] * (eff_min - ns["gravity_midpoint"])
                ))
            else:
                origin_factor = math.exp(-ns["beta"] * eff_min)
        else:  # "gravity_cap"
            # We compute the gravity sum independently for the factor —
            # do NOT reuse `grav_sum` from the Huff trip-distribution
            # block above, because in `closest_dest` mode that block sets
            # grav=ones / grav_sum=count (a Huff trick for uniform
            # probability when there's only 1 destination).  That dummy
            # value is wrong for the gravity_cap factor; we need the real
            # weighted, decayed gravity to the reachable destination(s).
            # Also: honors decay_curve (the closest method does the same),
            # for consistent calibration across the two methods.
            eff_gc = np.maximum(0.0, d_shortest_arr - ns["plateau"])
            if ns["decay_curve"] == "logistic":
                decay_arr = 1.0 / (1.0 + np.exp(
                    ns["beta"] * (eff_gc - ns["gravity_midpoint"])
                ))
            else:
                decay_arr = np.exp(-ns["beta"] * eff_gc)
            if ns["use_d_weights"]:
                d_idx_in_dest_gc = d_node_ids - n_net
                gc_input = float((d_weights[d_idx_in_dest_gc] * decay_arr).sum())
            else:
                gc_input = float(decay_arr.sum())
            origin_factor = min(1.0, gc_input / ns["gravity_cap"])

        if ns["use_d_weights"] and not ns["closest_dest"]:
            trip_prob = grav / grav_sum
        else:
            trip_prob = np.full(n_dest_active, 1.0 / n_dest_active)

        per_path_factor = path_probs * trip_prob[path_dest_idx]
        if ns["use_o_weights"]:
            per_path_factor *= o_weight
        if origin_factor != 1.0:
            per_path_factor *= origin_factor

        # ----- Numba accumulator (replaces Python loop) ---------------
        compute_nodes = bool(ns.get("compute_node_flow", False)) and (
            local_node_bw is not None
        )
        if compute_nodes:
            node_bw_arg = local_node_bw
            o_node_id_arg = np.int32(self._n_network_nodes + self._n_destinations)
            o_start_arg = np.int32(o_start)
            o_end_arg   = np.int32(o_end)
            d_node_ids_arg = d_node_ids
        else:
            node_bw_arg     = np.empty(0, dtype=np.float64)
            o_node_id_arg   = np.int32(0)
            o_start_arg     = np.int32(0)
            o_end_arg       = np.int32(0)
            d_node_ids_arg  = np.empty(0, dtype=np.int32)
        _bnumba.accumulate_paths_numba(
            out_path_edges, out_path_offsets, out_path_dest_idx,
            np.int32(n_paths),
            per_path_factor,
            np.int32(n_edges),
            local_bw_AB, local_bw_BA,
            bool(compute_nodes),
            self._edge_endpoints,
            np.int32(self._n_network_nodes),
            node_bw_arg,
            o_node_id_arg, o_start_arg, o_end_arg, d_node_ids_arg,
        )

        dt = time.perf_counter() - t_origin
        if self.logger is not None and dt > 5.0:
            self.logger.log(
                "Flow",
                f"  origin idx {o_pos} took {dt:.1f}s "
                f"({int(n_dest_active)} dests, {int(n_paths)} paths).", v=1,
            )

    # ──────────────────────────────────────────────────────────────────
    # Per-origin parallel orchestration (single path; CSR + k_alternatives)
    # ──────────────────────────────────────────────────────────────────

    def _process_origins_parallel(
        self, cluster_dest_nodes, n_edges, ns, show_progress=True,
    ):
        n_threads = max(1, int(self.num_threads))
        n_net   = self._n_network_nodes
        n_dest  = self._n_destinations
        n_total_nodes = n_net + n_dest + 1   # +1 origin slot
        origins = self.topology.origins

        local_AB = [np.zeros(n_edges, dtype=np.float64) for _ in range(n_threads)]
        local_BA = [np.zeros(n_edges, dtype=np.float64) for _ in range(n_threads)]
        if ns["compute_node_flow"]:
            local_node_bw = [np.zeros(n_net, dtype=np.float64)
                             for _ in range(n_threads)]
        else:
            local_node_bw = [None] * n_threads

        # Per-thread output buffers sized for k_alternatives.
        # K paths × n_max_dest destinations × thin per-path overhead.
        n_max_dest = max(1, max(len(v) for v in cluster_dest_nodes.values()))
        n_max_dest = min(n_max_dest, _effective_max_dests(ns["k_nearest"]))
        K          = int(ns["n_alternatives"])
        # Upper bound on emitted paths per origin = K * n_max_dest.  In
        # practice the algorithm stops early when penalties saturate.
        max_total_paths      = int(n_max_dest * K)
        # Pessimistic average path length for buffer sizing.  Real paths
        # at urban scale are 5-30 arcs; 100 leaves headroom.
        max_total_path_edges = int(max_total_paths * 100)

        def _make_buffers():
            return dict(
                d_idx_lookup     = np.full(n_total_nodes, -1, dtype=np.int32),
                out_path_edges   = np.empty(max_total_path_edges, dtype=np.int32),
                out_path_offsets = np.empty(max_total_paths + 1, dtype=np.int32),
                out_path_weights = np.empty(max_total_paths, dtype=np.float64),
                out_path_dest_idx= np.empty(max_total_paths, dtype=np.int32),
            )
        thread_bufs = [_make_buffers() for _ in range(n_threads)]
        cluster_dest_arrs = {
            cid: np.asarray(sorted(s), dtype=np.int32)
            for cid, s in cluster_dest_nodes.items()
        }

        slot_lock = threading.Lock()
        tid_to_slot: dict = {}

        def slot_for_current_thread():
            tid = threading.get_ident()
            s = tid_to_slot.get(tid)
            if s is not None:
                return s
            with slot_lock:
                if tid not in tid_to_slot:
                    tid_to_slot[tid] = len(tid_to_slot)
                return tid_to_slot[tid]

        o_corr_start, o_corr_end = self.topology.get_partial_edge_corrections(
            origins, for_origins=True
        )

        # Build origin work list (one entry per origin in each cluster).
        work: list = []
        for cid in cluster_dest_nodes.keys():
            for o in self.topology.origins.clasters_byId[cid]:
                work.append((cid, int(o)))
        n_total = len(work)

        self.logger.log(
            "Flow",
            f"Origin-parallel: {n_total} origins, {n_threads} threads, "
            f"{len(cluster_dest_nodes)} clusters; engine=k_alternatives.",
            v=1,
        )

        def task(cid, o_pos):
            try:
                o_weight = float(origins.node_weight[o_pos])
                if ns["use_o_weights"] and o_weight == 0:
                    return
                if not ns["use_o_weights"]:
                    o_weight = 1.0
                # v2.4.3: per-origin trip-generation factor is computed
                # inline in _process_origin_csr (no Phase-1 pass).
                o_start = int(origins.edge_start_node[o_pos])
                o_end   = int(origins.edge_end_node[o_pos])
                near_id = int(origins.nearest_edge_id[o_pos])
                w_start = float(origins.weight_to_start[o_pos])
                w_end   = float(origins.weight_to_end[o_pos])
                if o_corr_start is not None:
                    w_start += float(o_corr_start[o_pos])
                    w_end   += float(o_corr_end[o_pos])
                slot = slot_for_current_thread()
                bufs = thread_bufs[slot]
                self._process_origin_csr(
                    o_pos, o_weight, o_start, o_end, near_id, w_start, w_end,
                    cluster_dest_arrs[cid], ns,
                    n_edges, n_total_nodes,
                    bufs["out_path_edges"], bufs["out_path_offsets"],
                    bufs["out_path_weights"], bufs["out_path_dest_idx"],
                    bufs["d_idx_lookup"],
                    local_AB[slot], local_BA[slot],
                    local_node_bw=local_node_bw[slot],
                )
            except Exception as exc:
                import traceback as _tb
                self.logger.log(
                    "Flow",
                    f"Origin {o_pos} (cluster {cid}) raised: {exc!r}\n"
                    + _tb.format_exc(),
                    v=0,
                )

        bar = _bar(show_progress, total=n_total, desc="Origins",
                   unit="origin", dynamic_ncols=True)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(task, cid, o) for cid, o in work]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:
                    self.logger.log(
                        "Flow", f"Task error: {exc!r}", v=0,
                    )
                bar.update(1)
        bar.close()

        bw_AB = np.zeros(n_edges, dtype=np.float64)
        bw_BA = np.zeros(n_edges, dtype=np.float64)
        for arr in local_AB:
            bw_AB += arr
        for arr in local_BA:
            bw_BA += arr
        if ns["compute_node_flow"]:
            node_bw = np.zeros(n_net, dtype=np.float64)
            for arr in local_node_bw:
                if arr is not None:
                    node_bw += arr
            self._node_bw_partial = node_bw
        return bw_AB, bw_BA

    # ──────────────────────────────────────────────────────────────────
    # v2.4.3 NOTE — _compute_elastic_factors() and
    # _write_origins_elastic_weights() are gone.  The per-origin
    # trip-generation factor is now derived inside _process_origin_csr
    # from the destinations actually reached within search_radius
    # (see the "Per-origin trip-generation factor" block there).
    # ──────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────────────────────────

    def Centrality(self, settings: Settings) -> dict:
        ns = self._prepare_params(settings)

        import threading as _th
        self._assigned_stats      = {"no_match": 0, "unreachable": 0}
        self._assigned_stats_lock = _th.Lock()
        self._origin_tracking_records: list = []
        self._route_records: list = []
        self._route_records_lock  = _th.Lock()

        self.logger.log(
            "Flow",
            f"Centrality run (k_alternatives, K={ns['n_alternatives']}, "
            f"penalty={ns['alternative_penalty_factor']}): "
            f"search_radius={ns['search_radius']}, "
            f"detour_mode={ns['mode']}, ratio={ns['ratio']}, buffer={ns['buffer']}, "
            f"decay={ns['decay']}"
            + (
                f" (method={ns['decay_method']}, curve={ns['decay_curve']}"
                + (f", gravity_cap={ns['gravity_cap']:.2f}"
                   if ns['decay_method'] == 'gravity_cap' else "")
                + ")"
                if ns['decay'] else ""
            )
            + ", "
            f"path_penalty={ns['path_penalty']}, "
            f"turns={ns['use_turns']}"
            + (f" (thresh={ns['turn_thresh']:.0f}°, penalty={ns['turn_amt']:.1f})"
               if ns['use_turns'] else '')
            + ", "
            f"k_nearest_dests="
            + (f"{ns['k_nearest']}" if ns['k_nearest'] > 0
               else f"none (hard ceiling={_MAX_DESTS_HARD_CEIL})")
            + (", assigned_routing=ON" if ns['assigned_routing'] else ''),
            v=1,
        )

        self._build_digraph(elevation=ns["elevation"], elevation_penalty=ns["elevation_penalty"])

        t_csr = time.perf_counter()
        self._build_csr(
            use_turns=ns["use_turns"],
            turn_threshold_deg=ns["turn_thresh"],
            turn_penalty_amt=ns["turn_amt"],
            elevation=ns["elevation"],
            elevation_penalty=ns["elevation_penalty"],
        )
        self.logger.log(
            "Flow",
            f"CSR build: {time.perf_counter()-t_csr:.2f}s "
            f"(n_arcs={int(len(self._csr_indices))})", v=1,
        )

        # v2.4.3: no more Phase-1 elastic factors pass.  The per-origin
        # trip-generation factor is computed inline in
        # _process_origin_csr from the destinations actually reached
        # within search_radius — either the "closest" or "gravity_cap"
        # method (see _prepare_params / _process_origin_csr).  These
        # attributes are kept (always None) so ExportFlowResult can
        # cleanly skip the legacy per-origin-elastic-weights writer.
        self._elastic_knn      = None
        self._elastic_factors  = None

        n_edges = int(len(self.topology.network.weights))
        show_progress = bool(settings.progressbar)
        n_net = self._n_network_nodes

        # Single execution path: per-cluster destination set → parallel
        # k_alternatives.  When there are no clusters, treat the whole
        # network as one cluster (cluster id 0).
        cluster_dest_nodes: dict = {}
        if not self.topology.has_clusters:
            cluster_dest_nodes[0] = frozenset(
                range(n_net, n_net + self._n_destinations)
            )
            # Synthesize a single-cluster origin list if topology has no
            # per-cluster origin groupings.
            origins = self.topology.origins
            if not hasattr(origins, "clasters_byId") or 0 not in getattr(
                    origins, "clasters_byId", {}):
                # Defensive: build a minimal mapping covering all origins.
                if not hasattr(origins, "clasters_byId") or origins.clasters_byId is None:
                    origins.clasters_byId = {}
                origins.clasters_byId[0] = list(range(int(len(origins.node_weight))))
        else:
            n_clusters = len(self.topology.cluster_masks)
            for cid in range(n_clusters):
                dest_ilocs = self.topology.destinations.clasters_byId[cid]
                cluster_dest_nodes[cid] = frozenset(
                    n_net + int(d) for d in dest_ilocs
                )

        bw_AB, bw_BA = self._process_origins_parallel(
            cluster_dest_nodes, n_edges, ns, show_progress,
        )

        self.edge_flow_AB = bw_AB
        self.edge_flow_BA = bw_BA
        self.edge_flow    = bw_AB + bw_BA
        self.node_flow    = getattr(self, "_node_bw_partial", None)
        self._node_bw_partial    = None

        self._accumulate_observer_flows(bw_AB, bw_BA)

        # v2.4.4 — Obstacle usage tracking (optional).
        # Same idea: per-obstacle "hits" = edge flow on the host edge,
        # subset by the obstacle's direction.
        self.obstacle_hits_AB    = None
        self.obstacle_hits_BA    = None
        self.obstacle_hits_total = None
        track_obs = bool(settings.flow_track_obstacle_points_usage)
        obst = getattr(self.topology, "obstacles", None)
        if track_obs and obst is not None and len(obst.geometry) > 0:
            n_obst = int(len(obst.geometry))
            self.obstacle_hits_AB    = np.zeros(n_obst, dtype=np.float64)
            self.obstacle_hits_BA    = np.zeros(n_obst, dtype=np.float64)
            if getattr(obst, "snap_to", "edge") == "edge":
                edge_ids = obst.nearest_edge_id.astype(np.int64)
                dirs     = obst.direction
                for i in range(n_obst):
                    e = int(edge_ids[i])
                    d = dirs[i]
                    if d in ("both", "ab"):
                        self.obstacle_hits_AB[i] += bw_AB[e]
                    if d in ("both", "ba"):
                        self.obstacle_hits_BA[i] += bw_BA[e]
            else:  # node-snapped
                start_nodes = self.topology.network.start_nodes.astype(np.int64)
                end_nodes   = self.topology.network.end_nodes.astype(np.int64)
                snapped     = obst.snapped_node_id.astype(np.int64)
                for i in range(n_obst):
                    n = int(snapped[i])
                    incoming_ab = bw_AB[end_nodes == n].sum()
                    incoming_ba = bw_BA[start_nodes == n].sum()
                    self.obstacle_hits_AB[i] = float(incoming_ab)
                    self.obstacle_hits_BA[i] = float(incoming_ba)
            self.obstacle_hits_total = self.obstacle_hits_AB + self.obstacle_hits_BA
            self.logger.log(
                "Flow",
                f"Obstacle usage: {n_obst} points tracked; "
                f"total hits Σ={self.obstacle_hits_total.sum():.2f}.", v=1,
            )

        self.logger.log(
            "Flow",
            f"Done — total min={self.edge_flow.min():.4f}, "
            f"max={self.edge_flow.max():.4f}, "
            f"mean={self.edge_flow.mean():.4f}, "
            f"nonzero={int(np.count_nonzero(self.edge_flow))}; "
            f"AB max={bw_AB.max():.4f}, BA max={bw_BA.max():.4f}",
            v=1,
        )

        if ns["assigned_routing"]:
            n_origins  = len(self.topology.origins.node_weight)
            stats      = self._assigned_stats
            no_match   = stats["no_match"]
            unreach    = stats["unreachable"]
            served     = n_origins - no_match - unreach
            self.logger.log(
                "Flow",
                f"Assigned-routing summary: "
                f"{served}/{n_origins} origins served; "
                f"{no_match} skipped (assigned ID matched no destination); "
                f"{unreach} skipped (assigned destinations not reachable "
                f"within {ns['search_radius']:.0f}m). "
                "Check the join column / search_radius if these counts "
                "are unexpectedly high.",
                v=1,
            )

        return {
            "edge_flow":    self.edge_flow,
            "edge_flow_AB": self.edge_flow_AB,
            "edge_flow_BA": self.edge_flow_BA,
        }

    def _accumulate_observer_flows(self, bw_AB, bw_BA) -> None:
        """v2.4.4 — Observer point flow accumulation.

        Observer flows are derived directly from the final edge_flow
        arrays: an observer on edge e simply *reports* the existing
        flow on that edge. No per-path walking needed. Shared by every
        flow engine (Flow and AggregateFlow both call this after
        populating edge_flow_AB / edge_flow_BA).
        """
        self.observer_flow_AB    = None
        self.observer_flow_BA    = None
        self.observer_flow_total = None
        obs = getattr(self.topology, "observer_points", None)
        if obs is None or len(obs.geometry) == 0:
            return
        n_obs = int(len(obs.geometry))
        self.observer_flow_AB    = np.zeros(n_obs, dtype=np.float64)
        self.observer_flow_BA    = np.zeros(n_obs, dtype=np.float64)
        self.observer_flow_total = np.zeros(n_obs, dtype=np.float64)
        if getattr(obs, "snap_to", "edge") == "edge":
            edge_ids = obs.nearest_edge_id.astype(np.int64)
            self.observer_flow_AB[:]    = bw_AB[edge_ids]
            self.observer_flow_BA[:]    = bw_BA[edge_ids]
            self.observer_flow_total[:] = self.observer_flow_AB + self.observer_flow_BA
        else:  # snap_to == "node"
            # For a node-snapped observer, flow_total = sum over
            # edges incident to the node of (bw_AB[e] if end==node
            # else 0) + (bw_BA[e] if start==node else 0).  This is
            # "incoming flow", which equals "outgoing flow" for any
            # closed routing.  flow_AB/BA are not well-defined at a
            # node (multiple incident edges) — leave as NaN.
            start_nodes = self.topology.network.start_nodes.astype(np.int64)
            end_nodes   = self.topology.network.end_nodes.astype(np.int64)
            snapped     = obs.snapped_node_id.astype(np.int64)
            for i in range(n_obs):
                n = int(snapped[i])
                # AB arcs arriving at n.
                incoming_ab = bw_AB[end_nodes == n].sum()
                # BA arcs arriving at n (BA arc on edge e goes end→start).
                incoming_ba = bw_BA[start_nodes == n].sum()
                self.observer_flow_total[i] = float(incoming_ab + incoming_ba)
            self.observer_flow_AB[:] = np.nan
            self.observer_flow_BA[:] = np.nan
        self.logger.log(
            "Flow",
            f"Observer points: {n_obs} counters "
            f"({obs.snap_to}-snapped); "
            f"total flow Σ={self.observer_flow_total.sum():.2f}, "
            f"max={self.observer_flow_total.max():.4f}.", v=1,
        )


