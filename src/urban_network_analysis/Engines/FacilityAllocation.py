"""FacilityAllocation — optimal siting of facilities among candidates.

UNA's take on what ESRI and the OR literature call location-allocation.
Demand points (origins layer) are served by facilities chosen from a
candidate layer (destinations layer); facilities already in operation
are marked by a truthy value in ``settings.fa_required_column`` and are
always kept open. Travel is always evaluated TOWARDS the facility, and
``search_radius`` is the service cutoff: demand beyond it attends
nothing and counts as uncovered.

Problem types (``settings.fa_problem_type``):

``"max_access"``
    Open ``fa_new_facilities`` additional facilities so that total
    demand-weighted access to the nearest open facility is maximized:

        maximize  Σ_i  w_i · decay(d_i*)      d_i* = dist to nearest open

    The decay factor mirrors the flow engines' decay_method="closest"
    trip-generation convention (flow_decay / flow_decay_curve /
    gravity_beta; logistic uses k = ln(99)/search_radius with midpoint
    at search_radius/2). With flow_decay=False the factor is 1 for any
    reachable facility and the objective degenerates to covered demand
    (pure coverage maximization). Corresponds to ArcGIS's "Maximize
    Attendance".

``"min_facilities"``
    Open as few facilities as possible while covering every demand
    point that CAN be covered within the cutoff (demand with no
    candidate in range is reported as unservable and excluded).
    ``fa_new_facilities`` is ignored — the facility count is the
    output. Corresponds to ArcGIS's "Maximize Coverage + Minimize
    Facilities".

``"max_patronage"``
    Open ``fa_new_facilities`` additional facilities to maximize
    TOTAL TRIPS GENERATED under the flow engines' gravity-cap
    trip-generation model:

        maximize  Σ_i w_i · min(1, Σ_{j open} g_ij / cap)
        g_ij = attraction_j · decay(d_ij),  cap = flow_gravity_cap

    (numeric cap required). Facility attractiveness comes from
    ``destination_weight_column`` — the same role destination weights
    play in RunFlow's Huff model (unit values when unset; supply
    hypothesized sizes for candidate sites). Trips
    are Huff-split across open facilities in the outputs, so
    per-facility patronage shows cannibalization of required
    facilities by newly opened neighbors. Roughly ArcGIS's "Maximize
    Market Share". Greedy solver only.

Solvers (``settings.fa_solver``): ``"greedy"`` (default — submodular /
set-cover greedy, deterministic, scales to anything) or ``"exact"``
(MILP via scipy.optimize.milp/HiGHS; falls back to greedy with a logged
warning when oversized, unavailable, or non-optimal).

Architecture
------------
Subclasses AggregateFlow purely to REUSE its network machinery — the
directed graph with origin/destination virtual nodes, the CSR build
(with obstacle penalties and directional elevation costs), the bounded
backward Dijkstras from destination virtual nodes, and, when
settings.turns is on, the turn-aware line graph (costs then include
turn penalties, evaluated in the direction of travel towards the
facility). Because the
backward gradient from facility d assigns a distance to every node it
reaches — including ORIGIN virtual nodes — the sparse gradients ARE the
demand→facility cost matrix; this engine just harvests the origin
entries. No AggregateFlow code is modified, and no flow is computed.

The selection stage is a greedy marginal-gain solver (submodular
objective → (1 − 1/e) near-optimality guarantee), deterministic, with
required facilities pre-seeded.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import geopandas as gpd

from ..Settings import Settings
from ..Topology import Topology
from .AggregateFlow import AggregateFlow
from .Base import resolve_output_folder, write_gdf_outputs


class FacilityAllocation(AggregateFlow):
    """Facility-allocation engine (see module docstring)."""

    # ── Result arrays ────────────────────────────────────────────────
    # Per-demand (origin) arrays — exported joined on the origins layer.
    fa_assigned_facility: np.ndarray = None   # candidate index, -1 = unserved
    fa_assigned_uid:      np.ndarray = None   # candidate uid of the assigned facility
    fa_distance:          np.ndarray = None   # network cost to it (NaN if unserved)
    fa_access:            np.ndarray = None   # decay(distance) (0 if unserved)
    fa_covered:           np.ndarray = None   # 1/0

    # Per-candidate (destination) arrays — exported joined on candidates.
    fa_selected:          np.ndarray = None   # 1 = open (required or chosen)
    fa_required:          np.ndarray = None   # 1 = pre-existing facility
    fa_rank:              np.ndarray = None   # greedy pick order (1..p); 0 = required; -1 = not selected
    fa_demand_served:     np.ndarray = None   # Σ w_i of demand assigned here
    fa_access_captured:   np.ndarray = None   # Σ w_i · decay(d_ij) of demand assigned here

    summary: dict = None

    # ──────────────────────────────────────────────────────────────────
    # Parameter resolution (independent of AggregateFlow's
    # _prepare_params — this engine reads far fewer fields).
    # ──────────────────────────────────────────────────────────────────
    def _prepare_fa_params(self, s: Settings) -> dict:
        problem = str(s.fa_problem_type).strip().lower()
        solver  = str(s.fa_solver).strip().lower()

        gravity_cap = 0.0
        if problem == "max_patronage":
            cap = s.flow_gravity_cap
            if isinstance(cap, str):
                raise ValueError(
                    "fa_problem_type='max_patronage' requires a NUMERIC "
                    "flow_gravity_cap (the gravity value at which trip "
                    f"generation saturates); got the percentile string "
                    f"{cap!r}. Percentile caps are a RunFlow feature — "
                    "derive the number there (una.resolved_gravity_cap) "
                    "or from an accessibility run, then set it here."
                )
            gravity_cap = float(cap)
            if gravity_cap <= 0.0:
                raise ValueError(
                    "fa_problem_type='max_patronage' requires "
                    f"flow_gravity_cap > 0; got {gravity_cap}."
                )
            if solver == "exact":
                self.logger.log(
                    "FacilityAllocation",
                    "NOTE: fa_solver='exact' is not available for "
                    "max_patronage (the saturating trip-generation "
                    "objective does not linearize) — using greedy "
                    "(near-optimal: the objective is submodular).", v=1,
                )
                solver = "greedy"

        return dict(
            problem           = problem,
            solver            = solver,
            p_new             = int(s.fa_new_facilities),
            required_column   = (s.fa_required_column or "").strip(),
            gravity_cap       = gravity_cap,
            search_radius     = float(s.search_radius),
            decay_on          = bool(s.flow_decay),
            decay_curve       = str(s.flow_decay_curve).strip().lower(),
            gravity_beta      = float(s.gravity_beta),
            elevation         = bool(s.elevation),
            elevation_penalty = float(s.elevation_penalty),
            use_turns         = bool(s.turns),
            turn_thresh       = float(s.turn_threshold),
            turn_amt          = float(s.turn_penalty),
            use_o_weights     = bool(s.flow_origin_weights),
        )

    # Bound the backward Dijkstras at the service cutoff itself — no
    # detour inflation (that is a flow-envelope concept; here the
    # gradient is only read AT origin virtual nodes, where it equals the
    # shortest demand→facility cost).
    def _gradient_limit(self, ns) -> float:
        return float(ns["search_radius"])

    # ──────────────────────────────────────────────────────────────────
    # Main entry point.
    # ──────────────────────────────────────────────────────────────────
    def Centrality(self, settings: Settings) -> None:
        """Compute the facility allocation. Populates the fa_* arrays."""
        ns = self._prepare_fa_params(settings)

        self.logger.log(
            "FacilityAllocation",
            f"Run: problem={ns['problem']}, solver={ns['solver']}, "
            f"new_facilities={ns['p_new']}, cutoff={ns['search_radius']}, "
            f"decay={ns['decay_on']}"
            + (f" (curve={ns['decay_curve']}, beta={ns['gravity_beta']})"
               if ns["decay_on"] else "")
            + f", elevation={ns['elevation']}, turns={ns['use_turns']}"
            + (f" (threshold={ns['turn_thresh']:.0f} deg, "
               f"penalty={ns['turn_amt']:.1f})" if ns["use_turns"] else "")
            + ".",
            v=1,
        )
        if ns["decay_on"] and ns["decay_curve"] != "logistic" \
                and ns["gravity_beta"] <= 0.0:
            self.logger.log(
                "FacilityAllocation",
                "NOTE: flow_decay=True with gravity_beta=0 — the decay "
                "factor is 1 everywhere, so the objective reduces to "
                "covered demand (same as flow_decay=False).", v=1,
            )

        # 1. Network machinery — inherited from AggregateFlow unchanged.
        self._build_digraph(
            elevation=ns["elevation"],
            elevation_penalty=ns["elevation_penalty"],
        )
        self._build_csr()

        # 2. Backward gradients from every candidate, bounded at the
        #    cutoff (see _gradient_limit override). Turn-aware runs use
        #    the inherited line-graph machinery: origin STATES occupy a
        #    contiguous block exactly like origin virtual nodes do in
        #    the node graph, so the same harvesting works on both paths.
        t0 = time.perf_counter()
        if ns["use_turns"]:
            self._build_line_graph_turns(ns)
            self.logger.log(
                "FacilityAllocation",
                f"Line graph (turn-aware): {self._lg_n_states:,} states "
                f"(threshold={ns['turn_thresh']:.0f} deg, "
                f"penalty={ns['turn_amt']:.1f}).", v=1,
            )
            g_indptr, g_nodes, g_dist, _g_pred = \
                self._precompute_dest_gradients_turns(ns)
            first_o = int(self._lg_n_arcs)          # origin states block
        else:
            g_indptr, g_nodes, g_dist, _g_pred = \
                self._precompute_dest_gradients(ns)
            first_o = int(self._first_origin_node)  # origin virtual nodes
        self.logger.log(
            "FacilityAllocation",
            f"Backward gradients: {self._n_destinations} candidates in "
            f"{time.perf_counter()-t0:.2f}s (limit={ns['search_radius']:.0f}, "
            f"turns={ns['use_turns']}).",
            v=1,
        )

        # 3. Harvest the demand→candidate cost matrix from the origin
        #    entries of each gradient.
        cand_demand, cand_cost = self._extract_od_costs(
            g_indptr, g_nodes, g_dist, ns["search_radius"], first_o
        )

        # 4. Decay-weighted values g_ij. For max_patronage, facility
        #    attractiveness multiplies in (g_ij = dest_weight_j × decay)
        #    — destination weights play exactly the role they play in
        #    RunFlow's Huff model, already loaded via
        #    destination_weight_column (unit values when unset).
        if ns["problem"] == "max_patronage":
            attraction = np.asarray(
                self.topology.destinations.node_weight, dtype=np.float64
            )
            bad = ~np.isfinite(attraction) | (attraction < 0.0)
            if bad.any():
                raise ValueError(
                    f"max_patronage: destination_weight_column has "
                    f"{int(bad.sum())} NaN/negative value(s) on the "
                    f"candidates layer (rows "
                    f"{np.where(bad)[0][:10].tolist()}). Attractiveness "
                    f"multiplies into every gravity term — fix the "
                    f"source data."
                )
            if np.allclose(attraction, 1.0):
                self.logger.log(
                    "FacilityAllocation",
                    "max_patronage: destination_weight_column not set "
                    "(or all 1) — every candidate gets unit "
                    "attractiveness.", v=1,
                )
            cand_gain = [
                attraction[j] * self._decay_of(cand_cost[j], ns)
                for j in range(self._n_destinations)
            ]
        else:
            cand_gain = [
                self._decay_of(cost, ns) for cost in cand_cost
            ]

        # 5. Required facilities.
        required = self._read_required_mask(settings, ns["required_column"])

        # 6. Demand weights.
        origins = self.topology.origins
        n_orig  = int(len(origins.node_weight))
        w = np.asarray(origins.node_weight, dtype=np.float64) \
            if ns["use_o_weights"] else np.ones(n_orig, dtype=np.float64)

        # 6b. Unservable demand: no candidate at all within the cutoff.
        #     No facility configuration can serve these points — they are
        #     excluded from every objective and reported as uncovered.
        coverable = np.zeros(n_orig, dtype=bool)
        for o in cand_demand:
            coverable[o] = True
        n_unserv = int(n_orig - coverable.sum())
        if n_unserv > 0:
            w_unserv = float(w[~coverable].sum())
            self.logger.log(
                "FacilityAllocation",
                f"WARNING: {n_unserv:,} of {n_orig:,} demand points "
                f"(weight {w_unserv:,.1f}) have NO candidate within the "
                f"cutoff ({ns['search_radius']:.0f}) — unservable by any "
                f"configuration; they count as uncovered in the outputs. "
                f"Increase search_radius or add candidates to reach them.",
                v=1,
            )

        # 7. Facility selection — problem type × solver dispatch.
        #    Every path returns (selected mask, rank array); 'exact'
        #    falls back to greedy with a logged warning if the MILP is
        #    oversized, unavailable, or fails.
        self._solver_used = ns["solver"]
        self._problem     = ns["problem"]
        if ns["problem"] != "min_facilities" and ns["p_new"] == 0:
            # Evaluation run: no siting — allocate demand to the
            # required facilities as they stand (baseline access /
            # patronage of the existing configuration).
            if not required.any():
                self.logger.log(
                    "FacilityAllocation",
                    "WARNING: fa_new_facilities=0 and no required "
                    "facilities — nothing is open, so every output will "
                    "be zero/uncovered. Set fa_required_column (to "
                    "evaluate existing facilities) or fa_new_facilities "
                    ">= 1 (to site new ones).", v=1,
                )
            else:
                self.logger.log(
                    "FacilityAllocation",
                    f"fa_new_facilities=0 — evaluation run: allocating "
                    f"demand to the {int(required.sum())} required "
                    f"facilities only (no new siting).", v=1,
                )
        if ns["problem"] == "min_facilities":
            if ns["solver"] == "exact":
                selected, rank = self._milp_min_facilities(
                    n_orig, cand_demand, w, required, coverable
                )
            else:
                selected, rank = self._greedy_min_facilities(
                    n_orig, cand_demand, w, required, coverable
                )
        elif ns["problem"] == "max_patronage":
            selected, rank = self._greedy_max_patronage(
                n_orig, cand_demand, cand_gain, w, required,
                ns["p_new"], ns["gravity_cap"],
            )
        else:  # max_access
            if ns["solver"] == "exact":
                selected, rank = self._milp_max_access(
                    n_orig, cand_demand, cand_gain, w, required, ns["p_new"]
                )
            else:
                selected, rank = self._greedy_max_access(
                    n_orig, cand_demand, cand_gain, w, required, ns["p_new"]
                )

        # 8. Allocation of demand to the chosen configuration + outputs.
        if ns["problem"] == "max_patronage":
            self._allocate_patronage(
                n_orig, cand_demand, cand_cost, cand_gain, w, selected,
                rank, required, ns["gravity_cap"],
            )
        else:
            self._allocate(
                n_orig, cand_demand, cand_cost, cand_gain, w, selected,
                rank, required,
            )
        self.has_flow_results = False   # this engine produces no edge flow

    # ──────────────────────────────────────────────────────────────────
    # Stage helpers.
    # ──────────────────────────────────────────────────────────────────
    def _extract_od_costs(self, g_indptr, g_nodes, g_dist, cutoff, first_o):
        """Per-candidate arrays of (demand index, cost) within cutoff.

        ``first_o`` is the index of the first origin entry in the
        gradient's index space: origin VIRTUAL NODES in the node graph
        (turns=False), origin STATES in the line graph (turns=True).
        Either way origins occupy the contiguous block
        [first_o, first_o + n_origins), and the backward gradient of
        candidate d holds, at those indices, the exact shortest
        'towards facility' cost origin→candidate (connector arcs on
        both ends; turn penalties included on the turn-aware path).
        """
        n_orig  = self._n_origins
        n_dest  = self._n_destinations
        cand_demand, cand_cost = [], []
        n_entries = 0
        for d in range(n_dest):
            lo, hi = g_indptr[d], g_indptr[d + 1]
            nodes  = g_nodes[lo:hi]
            dist   = g_dist[lo:hi]
            m      = ((nodes >= first_o) & (nodes < first_o + n_orig)
                      & (dist <= cutoff + 1e-9))
            o_idx  = (nodes[m] - first_o).astype(np.int64)
            cand_demand.append(o_idx)
            cand_cost.append(dist[m].astype(np.float64))
            n_entries += int(o_idx.shape[0])
        self.logger.log(
            "FacilityAllocation",
            f"Demand-candidate cost matrix: {n_entries:,} finite pairs "
            f"within cutoff ({self._n_origins:,} demand × {n_dest:,} "
            f"candidates).", v=1,
        )
        return cand_demand, cand_cost

    @staticmethod
    def _decay_of_arr(dist, radius, decay_on, decay_curve, beta):
        """Decay factor per distance — the flow engines' 'closest'
        trip-generation convention (see _compute_trip_volumes)."""
        if not decay_on:
            return np.ones_like(dist)
        if decay_curve == "logistic":
            k = np.log(99.0) / max(radius, 1.0)
            return 1.0 / (1.0 + np.exp(k * (dist - radius / 2.0)))
        return np.exp(-beta * dist)

    def _decay_of(self, dist, ns):
        return self._decay_of_arr(
            dist, ns["search_radius"], ns["decay_on"],
            ns["decay_curve"], ns["gravity_beta"],
        )

    def _read_required_mask(self, settings: Settings, column: str) -> np.ndarray:
        """Boolean mask of required (pre-existing) candidates.

        The AccessPoints object keeps only geometry/weights/uid, so the
        required flag is re-read from the candidates source file; row
        order is preserved (BuildAccessPoints does reset_index on the
        same file).
        """
        import os
        n_dest = self._n_destinations
        if not column:
            return np.zeros(n_dest, dtype=bool)
        source = os.path.join(settings.data_folder, settings.destinations_file)
        gdf = gpd.read_file(source).reset_index(drop=True)
        if column not in gdf.columns:
            raise ValueError(
                f"fa_required_column='{column}' not found in the "
                f"candidates file {settings.destinations_file}. "
                f"Available columns: {list(gdf.columns)}."
            )
        if len(gdf) != n_dest:
            raise ValueError(
                f"Candidates file re-read for fa_required_column returned "
                f"{len(gdf)} rows but topology holds {n_dest} candidates — "
                f"the file changed on disk mid-run."
            )
        vals = gdf[column]
        mask = np.zeros(n_dest, dtype=bool)
        for i, v in enumerate(vals):
            if pd.isna(v):
                continue
            s = str(v).strip().lower()
            mask[i] = s not in ("", "0", "0.0", "false", "no", "none")
        self.logger.log(
            "FacilityAllocation",
            f"Required facilities: {int(mask.sum())} of {n_dest} "
            f"candidates (column '{column}').", v=1,
        )
        return mask

    def _greedy_max_patronage(self, n_orig, cand_demand, cand_gain, w,
                              required, p_new, cap):
        """Greedy maximization of total trips generated:

            objective = Σ_i w_i · min(1, G_i / cap),
            G_i = Σ_{j open} g_ij,   g_ij = attraction_j · decay(d_ij)

        The gravity-cap trip-generation model from the flow engines:
        adding facilities raises participation until it saturates at
        the cap. Monotone submodular (concave of a modular function),
        so greedy carries the (1 − 1/e) guarantee. Deterministic; ties
        break on the lower candidate index.
        """
        n_dest   = self._n_destinations
        selected = required.copy()
        rank     = np.full(n_dest, -1, dtype=np.int64)
        rank[required] = 0

        G = np.zeros(n_orig, dtype=np.float64)      # Σ g over open set
        for j in np.where(required)[0]:
            np.add.at(G, cand_demand[j], cand_gain[j])

        def obj(G_arr):
            return float((w * np.minimum(1.0, G_arr / cap)).sum())

        base = obj(G)
        self.logger.log(
            "FacilityAllocation",
            f"Baseline trips (required facilities only, cap={cap:g}): "
            f"{base:,.2f}.", v=1,
        )

        for step in range(1, p_new + 1):
            best_j, best_gain = -1, 0.0
            factor = np.minimum(1.0, G / cap)
            for j in range(n_dest):
                if selected[j]:
                    continue
                o, g = cand_demand[j], cand_gain[j]
                if o.shape[0] == 0:
                    continue
                gain = float((w[o] * (
                    np.minimum(1.0, (G[o] + g) / cap) - factor[o]
                )).sum())
                if gain > best_gain + 1e-12:
                    best_j, best_gain = j, gain
            if best_j < 0:
                self.logger.log(
                    "FacilityAllocation",
                    f"WARNING: stopping after {step-1} of {p_new} new "
                    f"facilities — every remaining candidate adds zero "
                    f"trips (demand it reaches is already saturated at "
                    f"the cap, or it reaches none).", v=1,
                )
                break
            selected[best_j] = True
            rank[best_j] = step
            np.add.at(G, cand_demand[best_j], cand_gain[best_j])
            self.logger.log(
                "FacilityAllocation",
                f"Pick {step}: candidate {best_j} (marginal trips "
                f"{best_gain:,.2f}; total now {obj(G):,.2f}).", v=1,
            )
        return selected, rank

    def _greedy_max_access(self, n_orig, cand_demand, cand_gain, w,
                           required, p_new):
        """Greedy submodular maximization of Σ_i w_i · best_g[i].

        best_g[i] tracks the access value demand i gets from the
        currently open set. Each round opens the candidate with the
        largest marginal gain Σ_i w_i · max(0, g_ij − best_g[i]).
        Deterministic: ties break on the lower candidate index.
        """
        n_dest   = self._n_destinations
        selected = required.copy()
        rank     = np.full(n_dest, -1, dtype=np.int64)
        rank[required] = 0

        best_g = np.zeros(n_orig, dtype=np.float64)
        for j in np.where(required)[0]:
            o, g = cand_demand[j], cand_gain[j]
            np.maximum.at(best_g, o, g)

        base_obj = float((w * best_g).sum())
        self.logger.log(
            "FacilityAllocation",
            f"Baseline objective (required facilities only): "
            f"{base_obj:,.2f}.", v=1,
        )

        for step in range(1, p_new + 1):
            best_j, best_gain = -1, 0.0
            for j in range(n_dest):
                if selected[j]:
                    continue
                o, g = cand_demand[j], cand_gain[j]
                if o.shape[0] == 0:
                    continue
                gain = float((w[o] * np.maximum(0.0, g - best_g[o])).sum())
                if gain > best_gain + 1e-12:
                    best_j, best_gain = j, gain
            if best_j < 0:
                self.logger.log(
                    "FacilityAllocation",
                    f"WARNING: stopping after {step-1} of {p_new} new "
                    f"facilities — no remaining candidate adds any "
                    f"access (every further candidate is redundant or "
                    f"reaches no demand within the cutoff).", v=1,
                )
                break
            selected[best_j] = True
            rank[best_j] = step
            o, g = cand_demand[best_j], cand_gain[best_j]
            np.maximum.at(best_g, o, g)
            self.logger.log(
                "FacilityAllocation",
                f"Pick {step}: candidate {best_j} "
                f"(marginal gain {best_gain:,.2f}; objective now "
                f"{float((w * best_g).sum()):,.2f}).", v=1,
            )
        return selected, rank

    def _greedy_min_facilities(self, n_orig, cand_demand, w, required,
                               coverable):
        """Weighted set-cover greedy: open the fewest facilities that
        cover every coverable demand point within the cutoff.

        Starts from the required set; each round opens the candidate
        covering the largest still-uncovered demand weight (ties break
        on the lower candidate index) until nothing coverable remains
        uncovered. fa_new_facilities is ignored by design.
        """
        n_dest   = self._n_destinations
        selected = required.copy()
        rank     = np.full(n_dest, -1, dtype=np.int64)
        rank[required] = 0

        covered = np.zeros(n_orig, dtype=bool)
        for j in np.where(required)[0]:
            covered[cand_demand[j]] = True

        target = coverable & ~covered
        step = 0
        while target.any():
            best_j, best_wt = -1, 0.0
            for j in range(n_dest):
                if selected[j]:
                    continue
                o = cand_demand[j]
                if o.shape[0] == 0:
                    continue
                wt = float(w[o[target[o]]].sum())
                if wt > best_wt + 1e-12:
                    best_j, best_wt = j, wt
            if best_j < 0:      # cannot happen while target ⊆ coverable
                break
            step += 1
            selected[best_j] = True
            rank[best_j] = step
            covered[cand_demand[best_j]] = True
            target = coverable & ~covered
            self.logger.log(
                "FacilityAllocation",
                f"Pick {step}: candidate {best_j} (newly covered weight "
                f"{best_wt:,.2f}; uncovered coverable remaining "
                f"{float(w[target].sum()):,.2f}).", v=1,
            )
        self.logger.log(
            "FacilityAllocation",
            f"min_facilities (greedy): full coverage of coverable demand "
            f"with {step} new + {int(required.sum())} required facilities.",
            v=1,
        )
        return selected, rank

    # ──────────────────────────────────────────────────────────────────
    # Exact MILP solvers (scipy.optimize.milp / HiGHS). Both fall back
    # to greedy — with a logged warning — when the problem is oversized,
    # scipy.milp is unavailable, or the solver does not return an
    # optimal solution.
    # ──────────────────────────────────────────────────────────────────
    _MILP_MAX_PAIRS = 3_000_000   # x-variable guard for max_access
    _MILP_TIME_LIMIT = 600        # seconds

    def _milp_fallback(self, reason, greedy_fn, *args):
        self.logger.log(
            "FacilityAllocation",
            f"WARNING: fa_solver='exact' fell back to greedy — {reason}",
            v=1,
        )
        self._solver_used = "greedy (fallback from exact)"
        return greedy_fn(*args)

    def _milp_max_access(self, n_orig, cand_demand, cand_gain, w,
                         required, p_new):
        """Exact p-median-with-decay MILP.

        Variables: y_j ∈ {0,1} (open), x_p ∈ [0,1] (assignment fraction
        of demand i(p) to candidate j(p)); LP-integral in x for fixed y.
            maximize   Σ_p w_i(p)·g_p·x_p
            s.t.       Σ_{p∈i} x_p ≤ 1          (each demand once)
                       x_p ≤ y_j(p)             (only open facilities)
                       Σ_{j∉required} y_j ≤ p_new
                       y_j = 1  ∀ j required
        """
        try:
            from scipy.optimize import milp, LinearConstraint, Bounds
            from scipy import sparse as sp
        except ImportError:
            return self._milp_fallback(
                "scipy.optimize.milp unavailable (needs scipy >= 1.9).",
                self._greedy_max_access,
                n_orig, cand_demand, cand_gain, w, required, p_new,
            )

        n_dest  = self._n_destinations
        pair_o  = np.concatenate(cand_demand) if cand_demand else np.zeros(0, np.int64)
        pair_g  = np.concatenate(cand_gain)   if cand_gain else np.zeros(0, np.float64)
        pair_j  = np.concatenate([
            np.full(cand_demand[j].shape[0], j, dtype=np.int64)
            for j in range(n_dest)
        ]) if n_dest else np.zeros(0, np.int64)
        n_pairs = int(pair_o.shape[0])
        if n_pairs == 0:
            return self._milp_fallback(
                "no demand-candidate pairs within the cutoff.",
                self._greedy_max_access,
                n_orig, cand_demand, cand_gain, w, required, p_new,
            )
        if n_pairs > self._MILP_MAX_PAIRS:
            return self._milp_fallback(
                f"{n_pairs:,} demand-candidate pairs exceed the exact-MILP "
                f"guard ({self._MILP_MAX_PAIRS:,}); greedy is near-optimal "
                f"((1-1/e) guarantee) at this scale.",
                self._greedy_max_access,
                n_orig, cand_demand, cand_gain, w, required, p_new,
            )

        n_var = n_dest + n_pairs                      # [y | x]
        c = np.zeros(n_var, dtype=np.float64)
        c[n_dest:] = -(w[pair_o] * pair_g)            # milp minimizes

        rows_pairs = np.arange(n_pairs)
        # (1) Σ_{p∈i} x_p ≤ 1 for each demand with any pair.
        A1 = sp.csr_matrix(
            (np.ones(n_pairs), (pair_o, n_dest + rows_pairs)),
            shape=(n_orig, n_var),
        )
        # (2) x_p − y_j(p) ≤ 0.
        A2 = sp.csr_matrix(
            (
                np.concatenate([np.ones(n_pairs), -np.ones(n_pairs)]),
                (
                    np.concatenate([rows_pairs, rows_pairs]),
                    np.concatenate([n_dest + rows_pairs, pair_j]),
                ),
            ),
            shape=(n_pairs, n_var),
        )
        # (3) Σ_{j∉required} y_j ≤ p_new.
        row3 = np.zeros((1, n_var))
        row3[0, :n_dest] = (~required).astype(np.float64)
        A3 = sp.csr_matrix(row3)

        A  = sp.vstack([A1, A2, A3], format="csr")
        ub = np.concatenate([
            np.ones(n_orig), np.zeros(n_pairs), np.array([float(p_new)]),
        ])
        constraints = LinearConstraint(A, -np.inf, ub)

        lb = np.zeros(n_var)
        hb = np.ones(n_var)
        lb[:n_dest][required] = 1.0                   # required forced open
        integrality = np.zeros(n_var)
        integrality[:n_dest] = 1                      # y binary, x continuous

        res = milp(
            c=c, constraints=constraints,
            bounds=Bounds(lb, hb), integrality=integrality,
            options={"time_limit": self._MILP_TIME_LIMIT},
        )
        if res.status != 0 or res.x is None:
            return self._milp_fallback(
                f"MILP did not reach optimality (status={res.status}: "
                f"{res.message}).",
                self._greedy_max_access,
                n_orig, cand_demand, cand_gain, w, required, p_new,
            )

        selected = res.x[:n_dest] > 0.5
        selected |= required
        self.logger.log(
            "FacilityAllocation",
            f"max_access (exact MILP): objective {-res.fun:,.2f} with "
            f"{int(selected.sum() - required.sum())} new + "
            f"{int(required.sum())} required facilities "
            f"({n_pairs:,} pairs, {n_var:,} variables).", v=1,
        )
        rank = self._rank_selection(selected, required, cand_demand,
                                    cand_gain, w)
        return selected, rank

    def _milp_min_facilities(self, n_orig, cand_demand, w, required,
                             coverable):
        """Exact set-cover MILP: minimize the number of NEW facilities
        subject to every coverable demand point being covered.
            minimize   Σ_{j∉required} y_j
            s.t.       Σ_{j covers i} y_j ≥ 1   ∀ coverable i
                       y_j = 1  ∀ j required
        """
        try:
            from scipy.optimize import milp, LinearConstraint, Bounds
            from scipy import sparse as sp
        except ImportError:
            return self._milp_fallback(
                "scipy.optimize.milp unavailable (needs scipy >= 1.9).",
                self._greedy_min_facilities,
                n_orig, cand_demand, w, required, coverable,
            )

        n_dest = self._n_destinations
        pair_o = np.concatenate(cand_demand) if cand_demand else np.zeros(0, np.int64)
        pair_j = np.concatenate([
            np.full(cand_demand[j].shape[0], j, dtype=np.int64)
            for j in range(n_dest)
        ]) if n_dest else np.zeros(0, np.int64)

        cov_idx = np.where(coverable)[0]
        if cov_idx.shape[0] == 0:
            self.logger.log(
                "FacilityAllocation",
                "min_facilities: no coverable demand — nothing to open.",
                v=1,
            )
            rank = np.full(n_dest, -1, dtype=np.int64)
            rank[required] = 0
            self._solver_used = "exact"
            return required.copy(), rank

        remap = np.full(n_orig, -1, dtype=np.int64)
        remap[cov_idx] = np.arange(cov_idx.shape[0])
        A = sp.csr_matrix(
            (np.ones(pair_o.shape[0]), (remap[pair_o], pair_j)),
            shape=(cov_idx.shape[0], n_dest),
        )
        constraints = LinearConstraint(A, 1.0, np.inf)

        c = (~required).astype(np.float64)            # count new only
        lb = np.zeros(n_dest)
        lb[required] = 1.0
        res = milp(
            c=c, constraints=constraints,
            bounds=Bounds(lb, np.ones(n_dest)),
            integrality=np.ones(n_dest),
            options={"time_limit": self._MILP_TIME_LIMIT},
        )
        if res.status != 0 or res.x is None:
            return self._milp_fallback(
                f"MILP did not reach optimality (status={res.status}: "
                f"{res.message}).",
                self._greedy_min_facilities,
                n_orig, cand_demand, w, required, coverable,
            )

        selected = res.x > 0.5
        selected |= required
        self.logger.log(
            "FacilityAllocation",
            f"min_facilities (exact MILP): full coverage with "
            f"{int(round(res.fun))} new + {int(required.sum())} required "
            f"facilities.", v=1,
        )
        # Rank the chosen new facilities by coverage contribution
        # (greedy order restricted to the optimal set).
        gain_unit = [np.ones_like(cand_demand[j], dtype=np.float64)
                     for j in range(n_dest)]
        rank = self._rank_selection(selected, required, cand_demand,
                                    gain_unit, w)
        return selected, rank

    def _rank_selection(self, selected, required, cand_demand, cand_gain, w):
        """Order an already-chosen facility set for the fa_rank output:
        greedy marginal-gain ordering restricted to the selected set
        (required = 0, then 1..k by contribution). Deterministic."""
        n_dest = self._n_destinations
        rank = np.full(n_dest, -1, dtype=np.int64)
        rank[required] = 0
        n_orig = int(len(self.topology.origins.node_weight))
        best_g = np.zeros(n_orig, dtype=np.float64)
        for j in np.where(required)[0]:
            np.maximum.at(best_g, cand_demand[j], cand_gain[j])
        remaining = list(np.where(selected & ~required)[0])
        step = 0
        while remaining:
            best_j, best_gain = remaining[0], -1.0
            for j in remaining:
                o, g = cand_demand[j], cand_gain[j]
                gain = float((w[o] * np.maximum(0.0, g - best_g[o])).sum()) \
                       if o.shape[0] else 0.0
                if gain > best_gain + 1e-12:
                    best_j, best_gain = j, gain
            step += 1
            rank[best_j] = step
            np.maximum.at(best_g, cand_demand[best_j], cand_gain[best_j])
            remaining.remove(best_j)
        return rank

    def _allocate(self, n_orig, cand_demand, cand_cost, cand_gain, w,
                  selected, rank, required):
        """Assign each demand point to its best open facility; fill all
        fa_* result arrays and the summary dict."""
        n_dest = self._n_destinations

        assigned = np.full(n_orig, -1, dtype=np.int64)
        best_g   = np.full(n_orig, 0.0, dtype=np.float64)
        best_d   = np.full(n_orig, np.inf, dtype=np.float64)

        for j in np.where(selected)[0]:
            o, g, c = cand_demand[j], cand_gain[j], cand_cost[j]
            # better access wins; on (near-)equal access the nearer wins
            better = (g > best_g[o] + 1e-12) | (
                np.isclose(g, best_g[o], rtol=0.0, atol=1e-12)
                & (c < best_d[o] - 1e-9)
            )
            idx = o[better]
            assigned[idx] = j
            best_g[idx]   = g[better]
            best_d[idx]   = c[better]

        covered = assigned >= 0
        self.fa_assigned_facility = assigned
        self.fa_distance          = np.where(covered, best_d, np.nan)
        self.fa_access            = np.where(covered, best_g, 0.0)
        self.fa_covered           = covered.astype(np.int64)

        dest_uid = np.asarray(self.topology.destinations.uid)
        self.fa_assigned_uid = np.array(
            [dest_uid[j] if j >= 0 else None for j in assigned], dtype=object
        )

        self.fa_selected        = selected.astype(np.int64)
        self.fa_required        = required.astype(np.int64)
        self.fa_rank            = rank
        self.fa_demand_served   = np.zeros(n_dest, dtype=np.float64)
        self.fa_access_captured = np.zeros(n_dest, dtype=np.float64)
        np.add.at(self.fa_demand_served,   assigned[covered], w[covered])
        np.add.at(self.fa_access_captured, assigned[covered],
                  (w * best_g)[covered])

        total_w = float(w.sum())
        cov_w   = float(w[covered].sum())
        self.summary = {
            "problem_type":           getattr(self, "_problem", "max_access"),
            "solver":                 getattr(self, "_solver_used", "greedy"),
            "objective_total_access": float((w * best_g).sum()),
            "n_selected":             int(selected.sum()),
            "n_required":             int(required.sum()),
            "n_new":                  int(selected.sum() - required.sum()),
            "demand_total":           total_w,
            "demand_covered":         cov_w,
            "demand_uncovered":       total_w - cov_w,
            "pct_covered":            100.0 * cov_w / total_w if total_w > 0 else 0.0,
        }
        self.logger.log(
            "FacilityAllocation",
            f"Allocation done: objective={self.summary['objective_total_access']:,.2f}, "
            f"{self.summary['n_new']} new + {self.summary['n_required']} required "
            f"facilities open, demand covered "
            f"{cov_w:,.1f}/{total_w:,.1f} ({self.summary['pct_covered']:.1f}%).",
            v=1,
        )

    def _allocate_patronage(self, n_orig, cand_demand, cand_cost,
                            cand_gain, w, selected, rank, required, cap):
        """Fill the fa_* arrays for max_patronage.

        Per-demand: trips_i = w_i · min(1, G_i/cap); trips split among
        open facilities by Huff share g_ij / G_i. Per-facility
        ``demand_served`` and ``access_captured`` hold the Huff-split
        demand weight and trips respectively — so required facilities'
        numbers show cannibalization by newly opened neighbors.
        ``assigned_facility``/``distance`` report each demand point's
        LARGEST-share open facility (its primary destination).
        """
        n_dest = self._n_destinations

        G        = np.zeros(n_orig, dtype=np.float64)
        best_g   = np.zeros(n_orig, dtype=np.float64)
        best_d   = np.full(n_orig, np.inf, dtype=np.float64)
        assigned = np.full(n_orig, -1, dtype=np.int64)

        open_idx = np.where(selected)[0]
        for j in open_idx:
            o, g, c = cand_demand[j], cand_gain[j], cand_cost[j]
            np.add.at(G, o, g)
            better = (g > best_g[o] + 1e-12) | (
                np.isclose(g, best_g[o], rtol=0.0, atol=1e-12)
                & (c < best_d[o] - 1e-9)
            )
            idx = o[better]
            assigned[idx] = j
            best_g[idx]   = g[better]
            best_d[idx]   = c[better]

        covered = G > 0.0
        factor  = np.minimum(1.0, G / cap)
        trips   = w * factor

        self.fa_assigned_facility = assigned
        self.fa_distance          = np.where(covered, best_d, np.nan)
        self.fa_access            = factor            # trip-generation factor
        self.fa_covered           = covered.astype(np.int64)

        dest_uid = np.asarray(self.topology.destinations.uid)
        self.fa_assigned_uid = np.array(
            [dest_uid[j] if j >= 0 else None for j in assigned], dtype=object
        )

        # Huff split of demand weight and trips across open facilities.
        self.fa_selected        = selected.astype(np.int64)
        self.fa_required        = required.astype(np.int64)
        self.fa_rank            = rank
        self.fa_demand_served   = np.zeros(n_dest, dtype=np.float64)
        self.fa_access_captured = np.zeros(n_dest, dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            for j in open_idx:
                o, g = cand_demand[j], cand_gain[j]
                share = np.where(G[o] > 0.0, g / G[o], 0.0)
                self.fa_demand_served[j]   = float((w[o] * share).sum())
                self.fa_access_captured[j] = float((trips[o] * share).sum())

        total_w   = float(w.sum())
        cov_w     = float(w[covered].sum())
        total_tr  = float(trips.sum())
        self.summary = {
            "problem_type":           getattr(self, "_problem", "max_patronage"),
            "solver":                 getattr(self, "_solver_used", "greedy"),
            "objective_total_access": total_tr,   # = total trips generated
            "total_trips":            total_tr,
            "gravity_cap":            float(cap),
            "n_selected":             int(selected.sum()),
            "n_required":             int(required.sum()),
            "n_new":                  int(selected.sum() - required.sum()),
            "demand_total":           total_w,
            "demand_covered":         cov_w,
            "demand_uncovered":       total_w - cov_w,
            "pct_covered":            100.0 * cov_w / total_w if total_w > 0 else 0.0,
        }
        self.logger.log(
            "FacilityAllocation",
            f"Allocation done (max_patronage): total trips "
            f"{total_tr:,.2f}, {self.summary['n_new']} new + "
            f"{self.summary['n_required']} required facilities open, "
            f"demand covered {cov_w:,.1f}/{total_w:,.1f} "
            f"({self.summary['pct_covered']:.1f}%).",
            v=1,
        )

    # ──────────────────────────────────────────────────────────────────
    # Export.
    # ──────────────────────────────────────────────────────────────────
    def ExportFacilityAllocationResults(self, settings: Settings,
                                        folder_prefix: str = "",
                                        file_name: str = "") -> None:
        """Write <file_name>_facilities.* (candidates layer + selection
        columns) and <file_name>_demand.* (origins layer + allocation
        columns) per the settings.output_* flags."""
        output_folder = resolve_output_folder(settings, folder_prefix)
        file_name = file_name or "facility_allocation"

        dest = self.topology.destinations
        # In max_patronage runs the captured quantity IS estimated trips
        # (the Huff-split patronage landing at each facility), so the
        # column is named accordingly; in access/coverage runs it is the
        # decay-weighted access captured.
        captured_col = ("patronage"
                        if getattr(self, "_problem", "") == "max_patronage"
                        else "access_captured")
        fac = gpd.GeoDataFrame(
            {
                "uid":           np.asarray(dest.uid),
                "selected":      self.fa_selected,
                "required":      self.fa_required,
                "rank":          self.fa_rank,
                "demand_served": self.fa_demand_served,
                captured_col:    self.fa_access_captured,
            },
            geometry=gpd.GeoSeries(dest.geometry).reset_index(drop=True),
            crs=getattr(dest.geometry, "crs", None),
        )
        write_gdf_outputs(
            fac, settings, output_folder, file_name + "_facilities",
            self.logger, "FacilityAllocation", desc="facilities",
        )

        # Convenience layer: only the OPEN facilities (required +
        # chosen), same columns — maps the chosen configuration without
        # a filter step, and is directly usable as a destinations_file
        # for a follow-up RunFlow(). The full candidates table above
        # remains the analytic record (the "no" decisions).
        fac_sel = fac[fac["selected"] == 1].reset_index(drop=True)
        write_gdf_outputs(
            fac_sel, settings, output_folder,
            file_name + "_facilities_selected",
            self.logger, "FacilityAllocation", desc="selected facilities",
        )

        orig = self.topology.origins
        dem = gpd.GeoDataFrame(
            {
                "uid":               np.asarray(orig.uid),
                "weight":            np.asarray(orig.node_weight, dtype=np.float64),
                "assigned_facility": self.fa_assigned_uid,
                "distance":          self.fa_distance,
                "access":            self.fa_access,
                "covered":           self.fa_covered,
            },
            geometry=gpd.GeoSeries(orig.geometry).reset_index(drop=True),
            crs=getattr(orig.geometry, "crs", None),
        )
        write_gdf_outputs(
            dem, settings, output_folder, file_name + "_demand",
            self.logger, "FacilityAllocation", desc="demand",
        )

        import os, json
        summary_path = os.path.join(output_folder, file_name + "_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(self.summary, f, indent=2)
        self.logger.log(
            "FacilityAllocation", f"Saved summary → {summary_path}", v=1,
        )
