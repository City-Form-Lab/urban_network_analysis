"""
Tier-2 fast path for ParallelBetweenness_v2 (v2.2).

Three Numba-compiled pieces, all `@njit(nogil=True, cache=True)`:

  * `enumerate_paths_dfs_numba(...)` — iterative DFS over CSR with
    origin overlay.  Produces packed (eid<<1 | dir) path edges in a
    flat output buffer.  Bounded by `max_paths_per_OD` and a new
    `max_dfs_iterations` counter (replaces the time-budget that
    Numba 0.65 in some envs rejects via `time.perf_counter`).

  * `accumulate_paths_numba(...)` — replaces the Python accumulator
    loop with a Numba inner loop.  For dense origins (~10 M packed
    edge entries) this drops accumulation cost from ~3–5 s to
    ~30–80 ms per origin.

  * `build_csr_from_digraph(G, n_total_nodes)` — flatten an nx.DiGraph
    into CSR arrays.  Pure Python.
"""
from __future__ import annotations

import math

import numpy as np

try:
    from numba import njit
    _NUMBA_OK = True
except Exception:                   # pragma: no cover
    _NUMBA_OK = False


# ──────────────────────────────────────────────────────────────────────
# CSR builder
# ──────────────────────────────────────────────────────────────────────

def build_csr_from_digraph(G, n_total_nodes: int):
    deg = np.zeros(n_total_nodes + 1, dtype=np.int64)
    for u in G.nodes:
        deg[u + 1] = G.out_degree(u)
    indptr = np.cumsum(deg).astype(np.int32)
    n_arcs = int(indptr[-1])
    indices   = np.empty(n_arcs, dtype=np.int32)
    weights   = np.empty(n_arcs, dtype=np.float64)
    edge_id   = np.empty(n_arcs, dtype=np.int32)
    direction = np.empty(n_arcs, dtype=np.int8)
    cursor = indptr[:-1].copy()
    for u, v, data in G.edges(data=True):
        i = cursor[u]
        indices[i]   = v
        weights[i]   = float(data["weight"])
        edge_id[i]   = int(data["id"])
        direction[i] = int(data["dir"])
        cursor[u]    = i + 1
    return indptr, indices, weights, edge_id, direction


# ──────────────────────────────────────────────────────────────────────
# Numba DFS path enumeration
# ──────────────────────────────────────────────────────────────────────

if _NUMBA_OK:

    @njit(nogil=True, cache=True, fastmath=False)
    def _enumerate_paths_dfs_numba(
        indptr,                # int32[n_total_nodes+1]
        indices,               # int32[n_arcs]
        weights,               # float64[n_arcs]
        edge_id,               # int32[n_arcs]
        direction,             # int8[n_arcs]
        o_node_id,             # int32
        origin_n,              # int32
        origin_neighbors,      # int32[<=2]
        origin_weights,        # float64[<=2]
        origin_edge_ids,       # int32[<=2]
        origin_dirs,           # int8[<=2]
        d_node_ids,            # int32[n_dest_active]
        d_idx_lookup,          # int32[n_total_nodes]
        d_cutoffs,             # float64[n_dest_active]
        d_tols,                # float64[n_dest_active]
        bwd_dist,              # float32[n_dest_active, n_total_nodes]
        max_paths_per_OD,
        max_dfs_iterations,    # int64; 0 disables
        use_turns,
        node_xy,               # float64[n_net, 2]
        n_net,
        turn_thresh,
        turn_amt,
        # Workspace (caller pre-allocates)
        visited,               # bool[n_total_nodes]
        edge_stack_buf,        # int32[max_path_len]
        weight_stack,          # float64[max_dfs_depth + 1]
        capped,                # bool[n_dest_active]
        paths_per_dest,        # int32[n_dest_active]
        fr_node, fr_prev,
        fr_iter_pos, fr_iter_end,
        fr_pushed_key,
        # Output (caller pre-allocates)
        out_path_edges,        # int32[max_total_path_edges]
        out_path_offsets,      # int32[max_total_paths + 1]
        out_path_weights,      # float64[max_total_paths]
        out_path_dest_idx,     # int32[max_total_paths]
    ):
        """
        Returns (n_paths, n_edges_written, n_capped, iter_budget_exceeded,
                 buf_overflow).
        """
        n_total_nodes = indptr.shape[0] - 1
        n_dest_active = d_node_ids.shape[0]
        max_total_paths      = out_path_offsets.shape[0] - 1
        max_total_path_edges = out_path_edges.shape[0]
        max_path_len         = edge_stack_buf.shape[0]

        for k in range(n_total_nodes):
            visited[k] = False
        for k in range(n_dest_active):
            capped[k] = False
            paths_per_dest[k] = 0

        visited[o_node_id] = True
        weight_stack[0] = 0.0

        sp_frame = 0
        sp_edge  = 0

        fr_node[0]      = o_node_id
        fr_prev[0]      = o_node_id
        fr_iter_pos[0]  = 0
        fr_iter_end[0]  = origin_n
        fr_pushed_key[0]= 0

        out_n_paths   = 0
        out_n_edges   = 0
        n_capped      = 0
        iter_budget_exceeded = 0
        buf_overflow  = 0

        n_iters = 0
        iter_limit_active = max_dfs_iterations > 0

        while sp_frame >= 0:

            if iter_limit_active:
                n_iters += 1
                if n_iters > max_dfs_iterations:
                    iter_budget_exceeded = 1
                    break

            ipos = fr_iter_pos[sp_frame]
            iend = fr_iter_end[sp_frame]

            if ipos >= iend:
                if sp_frame > 0:
                    visited[fr_node[sp_frame]] = False
                    if fr_pushed_key[sp_frame] == 1:
                        sp_edge -= 1
                sp_frame -= 1
                continue

            node = fr_node[sp_frame]
            prev = fr_prev[sp_frame]

            if node == o_node_id:
                neighbor = origin_neighbors[ipos]
                arc_w    = origin_weights[ipos]
                arc_eid  = origin_edge_ids[ipos]
                arc_dir  = origin_dirs[ipos]
            else:
                neighbor = indices[ipos]
                arc_w    = weights[ipos]
                arc_eid  = edge_id[ipos]
                arc_dir  = direction[ipos]

            fr_iter_pos[sp_frame] = ipos + 1

            if visited[neighbor]:
                continue

            step_w = arc_w
            if use_turns and prev < n_net and node < n_net and neighbor < n_net:
                a0 = node_xy[prev, 0]; a1 = node_xy[prev, 1]
                b0 = node_xy[node, 0]; b1 = node_xy[node, 1]
                c0 = node_xy[neighbor, 0]; c1 = node_xy[neighbor, 1]
                raw = math.degrees(
                    math.atan2(c1 - b1, c0 - b0)
                    - math.atan2(a1 - b1, a0 - b0)
                )
                if raw < 0.0:
                    raw += 360.0
                ang_round = round(raw)
                deviation = ang_round - 180
                if deviation < 0:
                    deviation = -deviation
                if deviation > turn_thresh:
                    step_w = step_w + turn_amt

            new_w = weight_stack[sp_frame] + step_w
            key   = (arc_eid << 1) | arc_dir

            # FIX (v2.3): dedup on EID ONLY, not (eid, dir).  A path that
            # both traverses an edge X (one direction) and uses the
            # destination connector for a dest on X (the other direction)
            # otherwise gets X counted twice, which inflates the per-edge
            # betweenness at every destination's parent edge.  Keep
            # whichever direction occurs FIRST along the path; the
            # directional split (bw_AB / bw_BA) is preserved through the
            # `key` (which still carries dir on the low bit).
            will_push = True
            arc_eid_only = key >> 1
            for k in range(sp_edge):
                if (edge_stack_buf[k] >> 1) == arc_eid_only:
                    will_push = False
                    break

            d_idx = d_idx_lookup[neighbor]
            if d_idx >= 0:
                if not capped[d_idx]:
                    if new_w <= d_cutoffs[d_idx] + d_tols[d_idx]:
                        if paths_per_dest[d_idx] < max_paths_per_OD:
                            edges_this_path = sp_edge + (1 if will_push else 0)
                            if (out_n_paths < max_total_paths and
                                out_n_edges + edges_this_path <= max_total_path_edges):
                                out_path_offsets[out_n_paths]  = out_n_edges
                                out_path_dest_idx[out_n_paths] = d_idx
                                out_path_weights[out_n_paths]  = new_w
                                for k in range(sp_edge):
                                    out_path_edges[out_n_edges] = edge_stack_buf[k]
                                    out_n_edges += 1
                                if will_push:
                                    out_path_edges[out_n_edges] = key
                                    out_n_edges += 1
                                out_n_paths += 1
                                paths_per_dest[d_idx] += 1
                            else:
                                buf_overflow = 1
                                for jj in range(n_dest_active):
                                    if not capped[jj]:
                                        capped[jj] = True
                                        n_capped  += 1
                                break
                        else:
                            capped[d_idx] = True
                            n_capped += 1

            has_target = False
            for k in range(n_dest_active):
                if visited[d_node_ids[k]]:
                    continue
                if capped[k]:
                    continue
                rem = bwd_dist[k, neighbor]
                if not (rem == rem):    # NaN check (np.inf cmp is fine, NaN not)
                    continue
                if rem >= 1e18:         # treat very large / inf as unreachable
                    continue
                if rem + new_w <= d_cutoffs[k] + d_tols[k]:
                    has_target = True
                    break

            if not has_target:
                continue

            visited[neighbor] = True
            if will_push:
                if sp_edge >= max_path_len:
                    visited[neighbor] = False
                    continue
                edge_stack_buf[sp_edge] = key
                sp_edge += 1

            sp_frame += 1
            if sp_frame >= fr_node.shape[0]:
                visited[neighbor] = False
                if will_push:
                    sp_edge -= 1
                sp_frame -= 1
                continue
            weight_stack[sp_frame]  = new_w
            fr_node[sp_frame]       = neighbor
            fr_prev[sp_frame]       = node
            fr_pushed_key[sp_frame] = 1 if will_push else 0
            if neighbor == o_node_id:
                fr_iter_pos[sp_frame] = 0
                fr_iter_end[sp_frame] = origin_n
            else:
                fr_iter_pos[sp_frame] = indptr[neighbor]
                fr_iter_end[sp_frame] = indptr[neighbor + 1]

        if out_n_paths < max_total_paths + 1:
            out_path_offsets[out_n_paths] = out_n_edges

        return out_n_paths, out_n_edges, n_capped, iter_budget_exceeded, buf_overflow


    @njit(nogil=True, cache=True, fastmath=False)
    def _accumulate_paths_numba(
        out_path_edges,        # int32[]:  (eid<<1 | dir)
        out_path_offsets,      # int32[max_paths+1]
        out_path_dest_idx,     # int32[max_paths]
        n_paths,               # int32
        per_path_factor,       # float64[n_paths]
        n_edges,               # int32
        local_bw_AB,           # float64[n_edges]
        local_bw_BA,           # float64[n_edges]
        # v2.11 — per-network-node accumulation (optional)
        compute_nodes,         # bool
        edge_endpoints,        # int32[n_edges, 2]; col0 = end_nodes[eid]
                               #                   col1 = start_nodes[eid]
        n_network_nodes,       # int32; nodes >= this are virtual
        local_node_bw,         # float64[n_network_nodes]; used iff compute_nodes
        # Path-reconstruction helpers (only used when compute_nodes)
        o_node_id,             # int32: virtual origin node id
        o_start, o_end,        # int32: network nodes at origin's access edge
        d_node_ids,            # int32[n_dest_active]: destination virtual node ids
    ):
        """
        Accumulate per-path contributions into local_bw_AB / local_bw_BA,
        and optionally into local_node_bw.

        Per-node policy: "1.0 include" — every network node the path
        visits gets full contribution.  Virtual nodes (origin overlay
        and destination connectors) are filtered out by the
        `node_id < n_network_nodes` check.

        Path-node reconstruction is done here (rather than recorded in
        the DFS) by walking arcs in order and tracking the current
        node.  Three cases per arc:
          * First arc out of the virtual origin → to-node is o_start
            (if dir==0) or o_end (if dir==1).
          * Last arc into a virtual destination → to-node is the
            destination's virtual node id (from d_node_ids[dest_idx]).
          * Interior arc on a network edge → to-node is whichever
            endpoint of the edge isn't the current node.
        """
        for p in range(n_paths):
            start = out_path_offsets[p]
            end   = out_path_offsets[p + 1]
            contrib = per_path_factor[p]
            if compute_nodes:
                d_node_actual = d_node_ids[out_path_dest_idx[p]]
                current_node  = o_node_id
            for k in range(start, end):
                packed = out_path_edges[k]
                eid = packed >> 1
                dr  = packed & 1
                if 0 <= eid < n_edges:
                    if dr == 0:
                        local_bw_AB[eid] += contrib
                    else:
                        local_bw_BA[eid] += contrib
                    if compute_nodes:
                        # Determine actual to-node for this arc.
                        if k == end - 1:
                            to_node = d_node_actual
                        elif current_node == o_node_id:
                            # First arc (origin overlay).
                            to_node = o_start if dr == 0 else o_end
                        else:
                            a = edge_endpoints[eid, 0]
                            b = edge_endpoints[eid, 1]
                            to_node = b if current_node == a else a
                        if to_node < n_network_nodes:
                            local_node_bw[to_node] += contrib
                        current_node = to_node

else:                                                       # pragma: no cover

    def _enumerate_paths_dfs_numba(*args, **kwargs):
        raise RuntimeError("Numba is not available; install numba.")

    def _accumulate_paths_numba(*args, **kwargs):
        raise RuntimeError("Numba is not available; install numba.")


# Public re-exports
enumerate_paths_dfs_numba = _enumerate_paths_dfs_numba
accumulate_paths_numba    = _accumulate_paths_numba
NUMBA_AVAILABLE           = _NUMBA_OK
