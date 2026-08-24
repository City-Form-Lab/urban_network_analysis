"""
AggregateFlow — scalable alternative flow engine for state-wide analyses.

Design
======
Trades path-level fidelity for near-linear scaling. Instead of enumerating
K alternative paths per OD pair (O(K · Dijkstra) per origin), this engine
computes flow over the OD pair's MAX NODE GRADIENT NETWORK — the maximum
extent of the network that can possibly be accessed given the user
parameters (search_radius, detour ratio/buffer, custom edge costs,
elevation gain) — using precomputed forward and backward NodeGradient
tables and predecessor trees.

Per-OD algorithm (via-arc gradient-overlap model)
=================================================

  1. d_o[v], pred_o[v] = distance / predecessor from the origin virtual
     node (bounded forward Dijkstra).
  2. d_d[v], pred_d[v] = distance / next-hop toward the destination
     virtual node (bounded backward Dijkstra on the reversed CSR;
     computed once per Centrality call, reused across all origins).
  3. d_shortest = d_o[d_node]; budget = cutoff(d_shortest)
     (flow_detour_mode: ratio / buffer / min).
  4. Max node gradient network = every arc (u, v) with weight w where

        d_o[u] + w + d_d[v] <= budget

     i.e. every arc that lies on SOME admissible walk from origin to
     destination. This is the full gradient-overlap envelope — strictly
     larger than the union of the two shortest-path trees.
  5. Each admissible arc is a VIA-ARC carrying weight

        q(u,v) = decay(excess),  excess = d_o[u] + w + d_d[v] - d_shortest

     where decay is flow_path_detour_penalty (with
     flow_route_enumeration_beta / logistic midpoint). Weights are
     normalized so that sum(q) = trip_volume. excess is the extra
     length of the BEST route through that arc, so the decay settings
     directly control how strongly flow concentrates on the shortest
     route and how it decays onto longer detours.
  6. The flow assigned to a via-arc travels
        shortest(o -> u)  +  arc(u,v)  +  shortest(v -> d)
     along the two predecessor trees. Loading is done with two O(n)
     tree-accumulation passes (descending d_o for origin legs,
     descending d_d for destination legs) — no per-path walking.
  7. Exclusions (all exact, all O(n)):
       * DEAD-END / U-TURN ARCS: an arc is skipped when pred_d[v] == u
         (its destination leg immediately returns through u) or
         pred_o[u] == v (its origin leg arrived through v). This zeroes
         every arc of every cul-de-sac branch — flow never enters
         dead-end streets.
       * SNAP-EDGE CROSSING: the origin and destination snap edges'
         own network arcs are never traversed (a trip cannot walk past
         its own origin/destination point), and legs contaminated by
         such a crossing are excluded via an O(n) marking pass over
         each predecessor tree.

Conservation guarantees (by construction):
  * Flow leaving the origin = trip volume: every via-arc's origin leg
    terminates through an origin connector arc, and connector arcs
    carry the origin snap edge id — so the segment fronting the origin
    accumulates exactly the origin weight.
  * The same amount arrives at the destination: every destination leg
    terminates through a destination connector arc — the destination
    snap edge accumulates exactly the trip volume.
  * Interior edges are bounded by the trip volume (each unit of flow
    crosses an edge at most once along its via-walk).

Trip volume, decay curve, Huff destination shares, elevation and custom
edge costs all come from Settings and behave identically to Flow. The
directional AB/BA split is preserved because arcs carry direction bits.

Interface
=========
Populates edge_flow / edge_flow_AB / edge_flow_BA / node_flow just like
Flow. ExportFlowResult (inherited from Base) writes them unchanged.
RunBatch's composite-output feature picks up edge_flow via getattr —
no downstream change.

Implementation
==============
Standalone engine — inherits only from Base, not from Flow. It builds
its own DiGraph -> CSR pipeline (network arcs + destination virtual
nodes + origin virtual nodes) rather than sharing Flow's, so the two
engines can evolve independently. This duplicates some plumbing
(digraph/CSR construction, Settings resolution) that is structurally
similar to Flow's, but deliberately does not carry over the pieces
Flow needs and this engine doesn't: k-alternatives enumeration, the
turn-aware line graph, assigned-routing, route/debug-print bookkeeping.
Turn-aware routing is not yet supported by this engine (see
docs/concepts/aggregate_flow.rst) -- _prepare_params raises a clear
error when settings.turns is True rather than silently ignoring it.

Design principle - trust Settings
==================================
Every parameter read here is declared in source/Settings.py with a
default; settings.Validation() runs before Centrality() (via
AggregateFlow._prepare_params). No getattr fallbacks, no
`settings.field or "default"` guards. Missing fields are fixed in
Settings.py, not here.
"""
from __future__ import annotations

import time
import threading
import concurrent.futures

import networkx as nx
import numpy as np
import numba as nb
from scipy.sparse import csr_matrix as _scipy_csr
from scipy.sparse.csgraph import dijkstra as _scipy_dijkstra

from ..Settings import Settings
from ..Topology import Topology
from .Base import Base
from . import _betweenness_numba as _bnumba


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


def _cutoff_for_shortest(shortest_d: float, mode: str, ratio: float, buffer: float) -> float:
    if mode == "ratio":
        return shortest_d * ratio
    if mode == "buffer":
        return shortest_d + buffer
    if mode == "min":
        return min(shortest_d * ratio, shortest_d + buffer)
    raise ValueError(f"Unknown detour_mode {mode!r}, expected one of {_VALID_MODES}.")


# ======================================================================
# Numba kernel — via-arc loading over the max node gradient network.
# All per-OD work happens here; the Python driver just assembles arrays.
# ======================================================================

# Decay-curve codes (semantics parallel Flow's `flow_path_detour_penalty`).
_DECAY_EQUAL       = 0
_DECAY_EXPONENTIAL = 1
_DECAY_LOGISTIC    = 2


@nb.njit(cache=True, inline='always')
def _find_arc(indptr, indices, u, v):
    """CSR arc index of (u -> v), or -1. Parallel arcs are collapsed to
    the min-weight one during digraph build, so the match is unique."""
    for ai in range(indptr[u], indptr[u + 1]):
        if indices[ai] == v:
            return ai
    return -1


@nb.njit(cache=True, inline='always')
def _decay(curve_id, beta, midpoint, excess):
    """Route-enumeration decay curve. Matches Flow's semantics.

    curve_id: 0 = equal (no decay), 1 = exponential, 2 = logistic.
    """
    if curve_id == _DECAY_EQUAL:
        return 1.0
    if curve_id == _DECAY_EXPONENTIAL:
        if excess <= 0.0:
            return 1.0
        return np.exp(-beta * excess)
    # Logistic (ln(99)/midpoint convention).
    k = np.log(99.0) / midpoint
    return 1.0 / (1.0 + np.exp(k * excess))


@nb.njit(cache=True, fastmath=True)
def _accumulate_od_flow(
    indptr, indices, weights, edge_id_of_arc, dir_of_arc,
    d_o, d_d, pred_o, pred_d,
    origin_virtual_node, dest_virtual_node,
    o_edge_id, d_edge_id,
    d_shortest, budget,
    decay_curve_id, decay_beta, decay_midpoint,
    trip_volume,
    n_net,
    out_AB, out_BA, out_node_flow,
):
    """Via-arc flow accumulator over the max node gradient network.

    Every admissible arc (d_o[u] + w + d_d[v] <= budget) receives a
    decay-weighted share of trip_volume; the share travels
    shortest(o->u) + arc + shortest(v->d) via the predecessor trees.
    Loading is two O(n) tree-accumulation passes. Returns the flow
    delivered at the destination virtual node (== trip_volume when the
    envelope is non-degenerate; the caller may log any gap).

    Exclusions: u-turn arcs (pred_d[v] == u or pred_o[u] == v — this
    zeroes all dead-end streets), the origin/destination snap edges'
    own network arcs, and legs contaminated by a snap-edge crossing.

    ``n_net`` is passed explicitly to distinguish network nodes from
    virtual nodes — out_node_flow.shape must NOT be used for that
    purpose (it is 0 when node flow is disabled).
    """
    n_nodes = d_o.shape[0]

    # ── 1. Reach mask: nodes inside the gradient-overlap envelope ────
    reach = np.zeros(n_nodes, dtype=nb.boolean)
    n_r = 0
    for v in range(n_nodes):
        dov = d_o[v]
        ddv = d_d[v]
        if dov < np.inf and ddv < np.inf and dov + ddv <= budget:
            reach[v] = True
            n_r += 1

    if not reach[dest_virtual_node] or not reach[origin_virtual_node]:
        return 0.0

    reach_nodes = np.empty(n_r, dtype=np.int64)
    p = 0
    for v in range(n_nodes):
        if reach[v]:
            reach_nodes[p] = v
            p += 1

    order_o = reach_nodes[np.argsort(d_o[reach_nodes])]   # ascending d_o
    order_d = reach_nodes[np.argsort(d_d[reach_nodes])]   # ascending d_d

    # ── 2. Leg contamination marking ──────────────────────────────────
    # cont_o[v]: the origin leg o->v crosses the DESTINATION snap edge.
    # cont_d[v]: the destination leg v->d crosses the ORIGIN snap edge.
    # (The origin tree never crosses the origin edge and the destination
    # tree never crosses the destination edge — connectors dominate.)
    # Ascending orders guarantee the predecessor is marked before v.
    cont_o = np.zeros(n_nodes, dtype=nb.boolean)
    cont_d = np.zeros(n_nodes, dtype=nb.boolean)
    for i in range(n_r):
        v = order_o[i]
        pv = pred_o[v]
        if pv < 0:
            continue
        if cont_o[pv]:
            cont_o[v] = True
        elif v < n_net and pv < n_net:
            ai = _find_arc(indptr, indices, pv, v)
            if ai >= 0 and edge_id_of_arc[ai] == d_edge_id:
                cont_o[v] = True
    for i in range(n_r):
        v = order_d[i]
        pv = pred_d[v]
        if pv < 0:
            continue
        if cont_d[pv]:
            cont_d[v] = True
        elif v < n_net and pv < n_net:
            ai = _find_arc(indptr, indices, v, pv)
            if ai >= 0 and edge_id_of_arc[ai] == o_edge_id:
                cont_d[v] = True

    # ── 3. Pass 1 — total decay weight over admissible via-arcs ──────
    q_sum = 0.0
    for i in range(n_r):
        u = reach_nodes[i]
        if cont_o[u]:
            continue
        for ai in range(indptr[u], indptr[u + 1]):
            x = indices[ai]
            if not reach[x] or cont_d[x]:
                continue
            arc_w = weights[ai]
            if d_o[u] + arc_w + d_d[x] > budget:
                continue
            eid = edge_id_of_arc[ai]
            if u < n_net and x < n_net and (eid == o_edge_id or eid == d_edge_id):
                continue                      # never walk past the O/D point
            if pred_d[x] == u or pred_o[u] == x:
                continue                      # u-turn / dead-end arc
            excess = d_o[u] + arc_w + d_d[x] - d_shortest
            if excess < 0.0:
                excess = 0.0
            q_sum += _decay(decay_curve_id, decay_beta, decay_midpoint, excess)

    if q_sum <= 0.0:
        return 0.0
    scale = trip_volume / q_sum

    # ── 4. Pass 2 — seed via-arc shares ───────────────────────────────
    # acc_o[u] collects flow that must be carried from the origin to u
    # (origin legs); acc_d[x] collects flow carried from x to the
    # destination (destination legs). The arc itself is loaded here.
    acc_o = np.zeros(n_nodes, dtype=np.float64)
    acc_d = np.zeros(n_nodes, dtype=np.float64)
    for i in range(n_r):
        u = reach_nodes[i]
        if cont_o[u]:
            continue
        for ai in range(indptr[u], indptr[u + 1]):
            x = indices[ai]
            if not reach[x] or cont_d[x]:
                continue
            arc_w = weights[ai]
            if d_o[u] + arc_w + d_d[x] > budget:
                continue
            eid = edge_id_of_arc[ai]
            if u < n_net and x < n_net and (eid == o_edge_id or eid == d_edge_id):
                continue
            if pred_d[x] == u or pred_o[u] == x:
                continue
            excess = d_o[u] + arc_w + d_d[x] - d_shortest
            if excess < 0.0:
                excess = 0.0
            q = scale * _decay(decay_curve_id, decay_beta, decay_midpoint, excess)

            if eid >= 0:
                if dir_of_arc[ai] == 0:
                    out_AB[eid] += q
                else:
                    out_BA[eid] += q
            acc_o[u] += q
            acc_d[x] += q

    # ── 5. Origin legs — tree accumulation in DESCENDING d_o ─────────
    # Processing farthest-first guarantees a node's accumulator is final
    # before its flow is pushed onto its predecessor arc.
    for i in range(n_r - 1, -1, -1):
        v = order_o[i]
        f = acc_o[v]
        if f <= 0.0:
            continue
        pv = pred_o[v]
        if pv < 0:
            continue
        ai = _find_arc(indptr, indices, pv, v)
        if ai >= 0:
            eid = edge_id_of_arc[ai]
            if eid >= 0:
                if dir_of_arc[ai] == 0:
                    out_AB[eid] += f
                else:
                    out_BA[eid] += f
        acc_o[pv] += f

    # ── 6. Destination legs — tree accumulation in DESCENDING d_d ────
    for i in range(n_r - 1, -1, -1):
        v = order_d[i]
        f = acc_d[v]
        if f <= 0.0:
            continue
        pv = pred_d[v]
        if pv < 0:
            continue
        ai = _find_arc(indptr, indices, v, pv)
        if ai >= 0:
            eid = edge_id_of_arc[ai]
            if eid >= 0:
                if dir_of_arc[ai] == 0:
                    out_AB[eid] += f
                else:
                    out_BA[eid] += f
        acc_d[pv] += f

    # ── 7. Node flow (network nodes only — explicit n_net check) ─────
    if out_node_flow.shape[0] > 0:
        for i in range(n_r):
            v = reach_nodes[i]
            if v < n_net:
                out_node_flow[v] += acc_o[v] + acc_d[v]

    return acc_d[dest_virtual_node]


# ======================================================================
# AggregateFlow — standalone engine, no dependency on Flow.
# ======================================================================

class AggregateFlow(Base):
    """Via-arc gradient-overlap engine — scalable alternative to
    K-alternatives.

    Root-cause architectural difference from Flow: AggregateFlow adds
    ORIGIN virtual nodes with connector arcs to its own CSR at build
    time (Flow adds them dynamically per-origin during K-alt). This
    makes origins fully symmetric with destinations, so the origin snap
    edge accumulates trip volume via connector traversal exactly the
    way the destination snap edge already does — no kernel special-case
    logic needed for edge attribution.
    """

    edge_flow:    np.ndarray = None
    edge_flow_AB: np.ndarray = None
    edge_flow_BA: np.ndarray = None
    node_flow:    np.ndarray = None

    _digraph: nx.DiGraph = None
    _n_network_nodes: int = 0
    _n_destinations:  int = 0
    _n_origins:       int = 0
    _first_origin_node: int = 0
    _node_xy: np.ndarray = None

    _csr_indptr:    np.ndarray = None
    _csr_indices:   np.ndarray = None
    _csr_weights:   np.ndarray = None
    _csr_edge_id:   np.ndarray = None
    _csr_direction: np.ndarray = None
    _csr_fwd = None
    _csr_rev = None

    def __init__(self, topology: Topology) -> None:
        super().__init__(topology)
        net  = topology.network
        dest = topology.destinations
        self._n_network_nodes = int(net.node_points.shape[0])
        self._n_destinations  = int(len(dest.node_weight))
        self._node_xy         = net.node_points[:, :2].astype(np.float64, copy=False)

    # ──────────────────────────────────────────────────────────────────
    # Settings validation
    # ──────────────────────────────────────────────────────────────────
    def _prepare_params(self, s: Settings) -> dict:
        """Resolve Settings into a flat runtime-params dict.

        Only the fields this engine actually reads are returned — it
        does not enumerate k alternatives, support assigned routing, or
        emit per-route/debug-print records, so those Settings fields
        (and their cross-checks) are Flow's concern, not this engine's.
        """
        if bool(s.turns):
            raise ValueError(
                "flow_engine='aggregate_flow' does not support turn-aware "
                "routing yet (settings.turns=True). Set turns=False, or use "
                "flow_engine='k_alternatives' for turn-aware flow."
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
                "AggregateFlow",
                "WARNING: flow_path_detour_penalty='exponential' but "
                "flow_route_enumeration_beta=0.  Every admissible arc "
                "will share equal weight (decay degenerates to flat).  "
                "Set flow_route_enumeration_beta > 0.", v=1,
            )
        if path_penalty == "logistic" and route_midpoint <= 0.0 and route_beta <= 0.0:
            self.logger.log(
                "AggregateFlow",
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
                    "AggregateFlow",
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
                    "AggregateFlow",
                    f"Logistic path penalty: auto-derived route beta = "
                    f"ln(99)/midpoint = {route_beta_effective:.6f} "
                    f"(midpoint={route_midpoint:.1f}); "
                    f"user-set flow_route_enumeration_beta={route_beta:.6f} ignored.", v=1,
                )
        else:
            route_beta_effective = route_beta

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
            search_radius     = float(s.search_radius),
            beta              = gravity_beta_effective,
            closest_dest      = bool(s.use_nearest_destination),
            ratio             = ratio,
            buffer            = buffer,
            mode              = mode,
            decay             = decay,
            decay_curve       = decay_curve,
            decay_method      = decay_method,
            gravity_cap       = gravity_cap,
            path_penalty      = path_penalty,
            route_beta        = route_beta_effective,
            route_midpoint    = route_midpoint,
            use_o_weights     = bool(s.flow_origin_weights),
            use_d_weights     = bool(s.flow_destination_weights),
            use_turns         = bool(s.turns),
            elevation         = bool(s.elevation),
            elevation_penalty = float(s.elevation_penalty),
            compute_node_flow = bool(s.flow_compute_node_flow),
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
        """Network arcs + destination virtual nodes + origin virtual
        nodes, all in one DiGraph. Origins are added AFTER destinations
        so node indices are [0, n_net) network, [n_net, n_net+n_dest)
        destinations, [n_net+n_dest, n_net+n_dest+n_orig) origins."""
        net   = self.topology.network
        dest  = self.topology.destinations
        orig  = self.topology.origins
        n_net  = self._n_network_nodes
        n_dest = self._n_destinations
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

        # Destination virtual nodes — BIDIRECTIONAL connectors (a trip
        # can approach the destination's snap edge from either endpoint).
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

        # Origin virtual nodes — UNIDIRECTIONAL connectors (V → o_start,
        # V → o_end) — origin is a source, not a sink, so we only need
        # forward arcs. Unidirectional also prevents a trip from one
        # origin from wandering INTO another origin's virtual node
        # (which would happen with bidirectional=True in a batch where
        # multiple origin virtual nodes coexist in the CSR).
        n_orig = int(len(orig.node_weight))
        self._n_origins         = n_orig
        self._first_origin_node = n_net + n_dest   # index of first V_o

        o_corr_start, o_corr_end = self.topology.get_partial_edge_corrections(
            orig, for_origins=True
        )

        for o_pos in range(n_orig):
            o_node  = self._first_origin_node + o_pos
            o_start = int(orig.edge_start_node[o_pos])
            o_end   = int(orig.edge_end_node[o_pos])
            near_id = int(orig.nearest_edge_id[o_pos])
            w_start = float(orig.weight_to_start[o_pos])
            w_end   = float(orig.weight_to_end[o_pos])
            if o_corr_start is not None:
                w_start += float(o_corr_start[o_pos])
                w_end   += float(o_corr_end[o_pos])
            G.add_node(o_node, node_type="origin", o_pos=o_pos)
            self._add_directed_connector(
                G, o_node, o_start, w_start, near_id, dir_bit=0,
                bidirectional=False,
            )
            if o_end != o_start:
                self._add_directed_connector(
                    G, o_node, o_end, w_end, near_id, dir_bit=1,
                    bidirectional=False,
                )

        self._digraph = G
        self.logger.log(
            "AggregateFlow",
            f"DiGraph: {n_net} net nodes, {n_dest} dest nodes, "
            f"{n_orig} origin nodes, {G.number_of_edges()} arcs.",
            v=1,
        )

    def _build_csr(self):
        """Build the CSR straight from the digraph, sized for network +
        destination + origin nodes together — no borrowed sizing hack
        needed since this engine owns its own digraph/CSR build."""
        n_total = self._n_network_nodes + self._n_destinations + self._n_origins
        indptr, indices, weights, edge_id, direction = (
            _bnumba.build_csr_from_digraph(self._digraph, n_total)
        )
        self._csr_indptr    = indptr
        self._csr_indices   = indices
        self._csr_weights   = weights
        self._csr_edge_id   = edge_id
        self._csr_direction = direction

        # Obstacle penalties compose additively with whatever the
        # digraph already has (custom edge costs, elevation-direction
        # penalties). This is the single point where obstacles enter
        # the aggregate-flow engine.
        if getattr(self.topology, "obstacles", None) is not None:
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
                        "AggregateFlow",
                        f"Applied obstacle penalties to CSR: "
                        f"AB Σ={p_AB.sum():.2f}, BA Σ={p_BA.sum():.2f} "
                        f"across {int(edge_valid.sum())} arcs.", v=1,
                    )

        self._csr_fwd = _scipy_csr(
            (self._csr_weights, indices, indptr.astype(np.int64)),
            shape=(n_total, n_total),
        )
        self._csr_rev = self._csr_fwd.T.tocsr()

    def Centrality(self, settings) -> None:
        """Compute per-edge aggregate flow. Populates self.edge_flow*."""
        ns = self._prepare_params(settings)

        self._assigned_stats      = {"no_match": 0, "unreachable": 0}
        self._assigned_stats_lock = threading.Lock()
        self._origin_tracking_records: list = []

        self.logger.log(
            "AggregateFlow",
            f"Centrality run (aggregate_flow, via-arc model): "
            f"search_radius={ns['search_radius']}, "
            f"detour_mode={ns['mode']}, ratio={ns['ratio']}, "
            f"buffer={ns['buffer']}, decay={ns['decay']}"
            + (f" (method={ns['decay_method']}, curve={ns['decay_curve']})"
               if ns['decay'] else "")
            + f", path_penalty={ns['path_penalty']}, "
            f"turns={ns['use_turns']}",
            v=1,
        )

        # Route-alternatives output is a k_alternatives capability — this
        # engine computes aggregate flow without enumerating routes.
        if settings.flow_output_routes:
            self.logger.log(
                "AggregateFlow",
                "WARNING: flow_output_routes=True is ignored — the "
                "aggregate_flow engine does not enumerate individual "
                "routes. Set flow_engine='k_alternatives' to generate "
                "route alternatives.", v=1,
            )

        # Warn when the settings produce a UNIFORM spread (equal weight
        # on every admissible arc) — the shortest path gets no preference
        # and flow diffuses evenly across the whole envelope. For
        # shortest-route concentration set
        #   flow_path_detour_penalty = "exponential"  (or "logistic")
        #   flow_route_enumeration_beta = 0.05        (higher = sharper)
        if ns["path_penalty"] == "equal":
            self.logger.log(
                "AggregateFlow",
                "WARNING: flow_path_detour_penalty='equal' — every "
                "admissible arc gets equal weight, so flow will diffuse "
                "uniformly across the gradient envelope. For "
                "shortest-route concentration set "
                "flow_path_detour_penalty='exponential' and "
                "flow_route_enumeration_beta > 0 (e.g. 0.05).", v=1,
            )
        elif ns["path_penalty"] == "exponential" and float(ns["route_beta"]) <= 0.0:
            self.logger.log(
                "AggregateFlow",
                "WARNING: flow_path_detour_penalty='exponential' but "
                "flow_route_enumeration_beta<=0 — decay degenerates to "
                "uniform. Set flow_route_enumeration_beta > 0 (e.g. 0.05) "
                "for shortest-route preference.", v=1,
            )

        self._build_digraph(
            elevation=ns["elevation"],
            elevation_penalty=ns["elevation_penalty"],
        )

        t_csr = time.perf_counter()
        self._build_csr()
        self.logger.log(
            "AggregateFlow",
            f"CSR build: {time.perf_counter()-t_csr:.2f}s "
            f"(n_arcs={int(len(self._csr_indices))})", v=1,
        )

        # Result buffers.
        n_edges = int(len(self.topology.network.geometry))
        n_net   = self._n_network_nodes
        self.edge_flow_AB = np.zeros(n_edges, dtype=np.float64)
        self.edge_flow_BA = np.zeros(n_edges, dtype=np.float64)
        if ns["compute_node_flow"]:
            self.node_flow = np.zeros(n_net, dtype=np.float64)
        else:
            self.node_flow = None

        # Not used by aggregate_flow but referenced by ExportFlowResult.
        self._elastic_knn      = None
        self._elastic_factors  = None

        # Precompute backward Dijkstras (distances + predecessor trees)
        # from destination virtual nodes.
        t_bwd = time.perf_counter()
        dest_grad_sparse = self._precompute_dest_gradients(ns)
        self.logger.log(
            "AggregateFlow",
            f"Backward gradients: {self._n_destinations} destinations "
            f"in {time.perf_counter()-t_bwd:.2f}s "
            f"(limit={self._gradient_limit(ns):.0f}).",
            v=1,
        )

        # Origin loop.
        self._process_origins_aggregate(dest_grad_sparse, ns)

        # Assemble undirected edge_flow.
        self.edge_flow = self.edge_flow_AB + self.edge_flow_BA
        self.has_flow_results = True

        # Observer counters report their edge's final flow.
        self._accumulate_observer_flows(self.edge_flow_AB, self.edge_flow_BA)

        self.logger.log(
            "AggregateFlow",
            f"Done — max={self.edge_flow.max():.4f}, "
            f"mean={self.edge_flow.mean():.4f}, "
            f"nonzero={int(np.count_nonzero(self.edge_flow))}",
            v=1,
        )

    def _accumulate_observer_flows(self, bw_AB, bw_BA) -> None:
        """Observer point flow accumulation.

        Observer flows are derived directly from the final edge_flow
        arrays: an observer on edge e simply *reports* the existing
        flow on that edge. No per-path walking needed.
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
            # For a node-snapped observer, flow_total = sum over edges
            # incident to the node of (bw_AB[e] if end==node else 0) +
            # (bw_BA[e] if start==node else 0). This is "incoming flow",
            # which equals "outgoing flow" for any closed routing.
            # flow_AB/BA are not well-defined at a node (multiple
            # incident edges) — leave as NaN.
            start_nodes = self.topology.network.start_nodes.astype(np.int64)
            end_nodes   = self.topology.network.end_nodes.astype(np.int64)
            snapped     = obs.snapped_node_id.astype(np.int64)
            for i in range(n_obs):
                n = int(snapped[i])
                incoming_ab = bw_AB[end_nodes == n].sum()
                incoming_ba = bw_BA[start_nodes == n].sum()
                self.observer_flow_total[i] = float(incoming_ab + incoming_ba)
            self.observer_flow_AB[:] = np.nan
            self.observer_flow_BA[:] = np.nan
        self.logger.log(
            "AggregateFlow",
            f"Observer points: {n_obs} counters "
            f"({obs.snap_to}-snapped); "
            f"total flow Σ={self.observer_flow_total.sum():.2f}, "
            f"max={self.observer_flow_total.max():.4f}.", v=1,
        )

    # ------------------------------------------------------------------
    # Gradient computation.
    # ------------------------------------------------------------------
    @staticmethod
    def _gradient_limit_for(ns) -> float:
        """Dijkstra expansion limit covering the largest possible
        envelope: cutoff(search_radius). Nodes beyond it can never be
        inside any OD's budget, so the bound is exact and preserves
        state-wide scaling."""
        return float(_cutoff_for_shortest(
            float(ns["search_radius"]), ns["mode"],
            float(ns["ratio"]), float(ns["buffer"]),
        ))

    def _gradient_limit(self, ns) -> float:
        return self._gradient_limit_for(ns)

    def _precompute_dest_gradients(self, ns):
        """Bounded backward Dijkstra from every destination virtual node.

        Returns a SPARSE gradient structure (indptr, nodes, dist, pred):
        destination d's finite gradient entries live in the slice
        [indptr[d], indptr[d+1]) of the parallel arrays nodes / dist /
        pred.  Dense per-destination arrays (n_dests × n_csr_nodes)
        are prohibitive at state scale — 43k destinations over ~1M CSR
        nodes would need hundreds of GB — while each bounded gradient
        only touches the nodes within the gradient limit (typically a
        few hundred to a few thousand).  The origin loop scatters each
        gradient into a reusable dense buffer per OD evaluation.
        """
        n_net    = self._n_network_nodes
        n_dest   = self._n_destinations
        n_total  = self._csr_indptr.shape[0] - 1
        limit    = self._gradient_limit(ns)
        dest_nodes = np.arange(n_net, n_net + n_dest, dtype=np.int64)

        # Chunked scipy calls bound the dense intermediate to ~0.8 GB.
        chunk = max(1, int(1e8 // max(n_total, 1)))
        idx_parts, dist_parts, pred_parts = [], [], []
        counts = np.zeros(n_dest, dtype=np.int64)
        for s in range(0, n_dest, chunk):
            e = min(s + chunk, n_dest)
            # self._build_csr stashes the reverse CSR at self._csr_rev.
            dist, preds = _scipy_dijkstra(
                self._csr_rev, directed=True,
                indices=dest_nodes[s:e],
                limit=limit,
                return_predecessors=True,
            )
            if dist.ndim == 1:
                dist, preds = dist[None, :], preds[None, :]
            finite = np.isfinite(dist)
            for k in range(e - s):
                cols = np.where(finite[k])[0]
                counts[s + k] = cols.shape[0]
                idx_parts.append(cols.astype(np.int64))
                dist_parts.append(dist[k, cols].astype(np.float64))
                pred_parts.append(preds[k, cols].astype(np.int32))
        indptr = np.zeros(n_dest + 1, dtype=np.int64)
        np.cumsum(counts, out=indptr[1:])
        nodes = np.concatenate(idx_parts) if idx_parts else np.zeros(0, np.int64)
        dist  = np.concatenate(dist_parts) if dist_parts else np.zeros(0, np.float64)
        pred  = np.concatenate(pred_parts) if pred_parts else np.zeros(0, np.int32)
        self.logger.log(
            "AggregateFlow",
            f"Gradient storage: {nodes.shape[0]:,} finite entries "
            f"(~{nodes.shape[0]*20/1e6:.0f} MB sparse vs "
            f"~{n_dest*n_total*12/1e9:.1f} GB dense).",
            v=2,
        )
        return indptr, nodes, dist, pred

    # ------------------------------------------------------------------
    # Origin driver.
    # ------------------------------------------------------------------
    def _process_origins_aggregate(self, dest_grad_sparse, ns) -> None:
        """Loop over origins; per-OD run the via-arc loading kernel."""
        g_indptr, g_nodes, g_dist, g_pred = dest_grad_sparse
        n_total_nodes = self._csr_indptr.shape[0] - 1
        n_net       = self._n_network_nodes
        n_dest      = self._n_destinations
        origins     = self.topology.origins
        dest        = self.topology.destinations
        n_origins   = int(len(origins.node_weight))

        # Decay-curve id for the via-arc weighting.
        curve_name = ns["path_penalty"]
        if curve_name == "equal":
            decay_curve_id = _DECAY_EQUAL
        elif curve_name == "exponential":
            decay_curve_id = _DECAY_EXPONENTIAL
        else:
            decay_curve_id = _DECAY_LOGISTIC
        decay_beta     = float(ns["route_beta"])
        decay_midpoint = float(ns["route_midpoint"]) if ns["route_midpoint"] > 0 else 200.0

        # Trip-generation params.
        radius            = float(ns["search_radius"])
        gravity_beta      = float(ns["beta"])
        use_nearest       = bool(ns["closest_dest"])
        decay_on          = bool(ns["decay"])
        decay_curve_dcy   = ns["decay_curve"]
        use_o_weights     = bool(ns["use_o_weights"])
        use_d_weights     = bool(ns["use_d_weights"])
        dest_weights      = np.asarray(dest.node_weight, dtype=np.float64)
        dest_node_ids     = np.arange(n_net, n_net + n_dest, dtype=np.int64)
        dest_edge_ids     = np.asarray(dest.nearest_edge_id, dtype=np.int64)

        mode   = ns["mode"]
        ratio  = float(ns["ratio"])
        buffer = float(ns["buffer"])
        grad_limit = self._gradient_limit(ns)

        # Threading configuration.  self.num_threads is inherited from
        # Base and initialised from topology.num_threads (defaults to
        # cpu_count-1 there).  No Settings field needed — parallelism
        # is a per-Topology capability, not a per-analysis choice.
        n_threads = max(1, int(self.num_threads))
        n_edges   = self.edge_flow_AB.shape[0]

        # Per-thread local output buffers.  We accumulate into these to
        # avoid contention on the shared self.edge_flow_AB/BA/node arrays;
        # a single-threaded reduction at the end sums the per-thread
        # partials into the final result.  Static striped work assignment
        # (thread i handles origins [i, i+N, i+2N, …]) makes the run
        # deterministic across invocations.
        local_AB   = [np.zeros(n_edges, dtype=np.float64) for _ in range(n_threads)]
        local_BA   = [np.zeros(n_edges, dtype=np.float64) for _ in range(n_threads)]
        if self.node_flow is not None:
            local_node = [np.zeros(n_net, dtype=np.float64) for _ in range(n_threads)]
        else:
            _empty_node = np.zeros(0, dtype=np.float64)
            local_node  = [_empty_node] * n_threads
        local_n_gap    = [0]   * n_threads
        local_worst    = [0.0] * n_threads

        # Progress tracking (~100 log lines over the run, thread-safe).
        progress_lock = threading.Lock()
        n_done        = [0]                          # boxed for closure
        log_every     = max(1, n_origins // 100)

        def _process_stripe(slot: int) -> None:
            """Process origins assigned to this thread via static striping."""
            buf_AB   = local_AB[slot]
            buf_BA   = local_BA[slot]
            buf_node = local_node[slot]
            # Reusable dense views of the sparse destination gradients:
            # scatter before each kernel call, reset after. Two arrays
            # per thread (~12 bytes x n_csr_nodes) instead of dense
            # per-destination storage.
            dd_buf = np.full(n_total_nodes, np.inf, dtype=np.float64)
            pd_buf = np.full(n_total_nodes, -9999, dtype=np.int32)

            for o_pos in range(slot, n_origins, n_threads):
                o_weight = float(origins.node_weight[o_pos])
                if use_o_weights and o_weight == 0.0:
                    # Empty-weight origin — still counts as processed for
                    # progress bookkeeping.
                    pass
                else:
                    if not use_o_weights:
                        o_weight = 1.0

                    # Origin virtual node index (added to CSR by
                    # _build_digraph) and the origin snap edge id.
                    origin_virtual = self._first_origin_node + o_pos
                    o_edge_id      = int(origins.nearest_edge_id[o_pos])

                    # Forward Dijkstra from the origin virtual node
                    # (single source, bounded by the gradient limit)
                    # with predecessors.  scipy_dijkstra releases the
                    # GIL for its native inner loop, so threading here
                    # scales across cores.
                    d_o, pred_o = _scipy_dijkstra(
                        self._csr_fwd, directed=True,
                        indices=origin_virtual, limit=grad_limit,
                        return_predecessors=True,
                    )

                    d_shortest_arr = d_o[dest_node_ids]

                    trip_vols = _compute_trip_volumes(
                        o_weight, dest_weights, d_shortest_arr,
                        radius, gravity_beta, decay_on, decay_curve_dcy,
                        use_nearest, use_d_weights,
                        ns["decay_method"], float(ns["gravity_cap"]),
                    )

                    for d_idx in range(n_dest):
                        d_shortest = float(d_shortest_arr[d_idx])
                        trip_vol   = float(trip_vols[d_idx])
                        if trip_vol <= 0.0 or not np.isfinite(d_shortest):
                            continue
                        if d_shortest > radius:
                            continue

                        budget = float(_cutoff_for_shortest(
                            d_shortest, mode, ratio, buffer,
                        ))

                        # Scatter this destination's sparse gradient into
                        # the reusable dense buffers for the kernel.
                        s0, s1 = g_indptr[d_idx], g_indptr[d_idx + 1]
                        cols = g_nodes[s0:s1]
                        dd_buf[cols] = g_dist[s0:s1]
                        pd_buf[cols] = g_pred[s0:s1]

                        delivered = _accumulate_od_flow(
                            self._csr_indptr, self._csr_indices, self._csr_weights,
                            self._csr_edge_id, self._csr_direction,
                            d_o, dd_buf, pred_o, pd_buf,
                            origin_virtual, int(dest_node_ids[d_idx]),
                            o_edge_id, int(dest_edge_ids[d_idx]),
                            d_shortest, budget,
                            decay_curve_id, decay_beta, decay_midpoint,
                            trip_vol,
                            n_net,
                            buf_AB, buf_BA, buf_node,
                        )

                        # Reset only the touched entries.
                        dd_buf[cols] = np.inf
                        pd_buf[cols] = -9999

                        gap = abs(delivered - trip_vol)
                        if gap > 1e-6 * max(1.0, trip_vol):
                            local_n_gap[slot] += 1
                            if gap > local_worst[slot]:
                                local_worst[slot] = gap

                # Progress update — cheap counter increment under lock,
                # periodic log line with rate + ETA.
                with progress_lock:
                    n_done[0] += 1
                    done = n_done[0]
                    if done % log_every == 0 or done == n_origins:
                        elapsed = time.perf_counter() - t_loop
                        rate    = done / elapsed if elapsed > 0 else 0.0
                        eta_min = (n_origins - done) / rate / 60.0 if rate > 0 else 0.0
                        self.logger.log(
                            "AggregateFlow",
                            f"  origin {done:,}/{n_origins:,} "
                            f"({100.0*done/n_origins:.1f}%, "
                            f"{rate:.0f}/s, ETA {eta_min:.1f} min)",
                            v=1,
                        )

        self.logger.log(
            "AggregateFlow",
            f"Origin loop: {n_origins:,} origins across {n_threads} thread(s) "
            f"(static striped assignment).", v=1,
        )
        t_loop = time.perf_counter()
        if n_threads == 1:
            # Fast-path — avoid thread-pool spawn overhead for tiny runs.
            _process_stripe(0)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
                futures = [pool.submit(_process_stripe, slot)
                           for slot in range(n_threads)]
                # .result() re-raises any exception from the worker.
                for f in concurrent.futures.as_completed(futures):
                    f.result()

        # Reduce per-thread partials into the shared result arrays.
        for slot in range(n_threads):
            self.edge_flow_AB += local_AB[slot]
            self.edge_flow_BA += local_BA[slot]
            if self.node_flow is not None:
                self.node_flow += local_node[slot]

        n_gap     = sum(local_n_gap)
        worst_gap = max(local_worst) if local_worst else 0.0

        self.logger.log(
            "AggregateFlow",
            f"Origin loop: {n_origins:,} origins in "
            f"{time.perf_counter()-t_loop:.2f}s ({n_threads} thread(s)).",
            v=1,
        )
        if n_gap > 0:
            self.logger.log(
                "AggregateFlow",
                f"WARNING: {n_gap} OD pair(s) delivered less than their "
                f"trip volume (worst gap={worst_gap:.6f}). Rerun with "
                f"verbosity 2 for per-OD detail.",
                v=1,
            )


# ======================================================================
# Trip-volume helper — same semantics as Flow's inline math.
# ======================================================================

def _compute_trip_volumes(
    o_weight, dest_weights, d_shortest_arr,
    radius, gravity_beta, decay_on, decay_curve, use_nearest, use_d_weights,
    decay_method, gravity_cap,
):
    """Return per-destination trip volume for this origin.

    Mirrors Flow's trip-generation math:
      * decay off → equal share of o_weight across reachable destinations
        (or 100% to nearest if use_nearest).
      * decay on, method='closest' → total = o_weight · decay(nearest);
        split by Huff share (weight · decay).
      * decay on, method='gravity_cap' → total = o_weight · min(Σg/cap, 1);
        split by Huff share.
    """
    n_dest = d_shortest_arr.shape[0]
    trips  = np.zeros(n_dest, dtype=np.float64)

    reachable = np.isfinite(d_shortest_arr) & (d_shortest_arr <= radius)
    if not reachable.any():
        return trips

    if not decay_on:
        if use_nearest:
            i_near = int(np.argmin(np.where(reachable, d_shortest_arr, np.inf)))
            dw = float(dest_weights[i_near]) if use_d_weights else 1.0
            trips[i_near] = o_weight * dw
            return trips
        dw_arr = dest_weights.astype(np.float64) if use_d_weights \
                 else np.ones(n_dest, dtype=np.float64)
        dw_arr = dw_arr * reachable
        total  = dw_arr.sum()
        if total > 0.0:
            trips = o_weight * dw_arr / total
        return trips

    # Decay on — per-destination decay factor.
    if decay_curve == "logistic":
        k = np.log(99.0) / max(radius, 1.0)
        decay = np.where(
            reachable,
            1.0 / (1.0 + np.exp(k * (d_shortest_arr - radius / 2.0))),
            0.0,
        )
    else:
        decay = np.where(reachable, np.exp(-gravity_beta * d_shortest_arr), 0.0)

    dw_arr = dest_weights.astype(np.float64) if use_d_weights \
             else np.ones(n_dest, dtype=np.float64)
    gravity = decay * dw_arr

    if use_nearest:
        i_near = int(np.argmin(np.where(reachable, d_shortest_arr, np.inf)))
        trips[i_near] = o_weight * decay[i_near]
        return trips

    total_g = gravity.sum()
    if total_g <= 0.0:
        return trips

    if decay_method == "gravity_cap":
        cap        = max(gravity_cap, 1e-12)
        total_trip = o_weight * min(total_g / cap, 1.0)
    else:
        i_near     = int(np.argmin(np.where(reachable, d_shortest_arr, np.inf)))
        total_trip = o_weight * decay[i_near]

    trips = total_trip * gravity / total_g
    return trips
