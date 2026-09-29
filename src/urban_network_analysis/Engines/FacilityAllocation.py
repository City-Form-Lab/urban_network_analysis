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
    Fewest facilities covering all coverable demand (Phase 2 — not yet
    implemented; corresponds to ArcGIS's "Maximize Coverage + Minimize
    Facilities").

Architecture
------------
Subclasses AggregateFlow purely to REUSE its network machinery — the
directed graph with origin/destination virtual nodes, the CSR build
(with obstacle penalties and directional elevation costs), and the
bounded backward Dijkstras from destination virtual nodes. Because the
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
        if bool(s.turns):
            raise ValueError(
                "FacilityAllocation does not support turns=True yet "
                "(turn-aware costs arrive in a later phase). Set "
                "settings.turns = False."
            )
        problem = str(s.fa_problem_type).strip().lower()
        if problem == "min_facilities":
            raise NotImplementedError(
                "fa_problem_type='min_facilities' arrives in Phase 2. "
                "Currently available: 'max_access'."
            )
        solver = str(s.fa_solver).strip().lower()
        if solver == "exact":
            self.logger.log(
                "FacilityAllocation",
                "NOTE: fa_solver='exact' (MILP) arrives in Phase 2 — "
                "falling back to the greedy solver (near-optimal: "
                "(1-1/e) guarantee, deterministic).", v=1,
            )
            solver = "greedy"

        return dict(
            problem           = problem,
            solver            = solver,
            p_new             = int(s.fa_new_facilities),
            required_column   = (s.fa_required_column or "").strip(),
            search_radius     = float(s.search_radius),
            decay_on          = bool(s.flow_decay),
            decay_curve       = str(s.flow_decay_curve).strip().lower(),
            gravity_beta      = float(s.gravity_beta),
            elevation         = bool(s.elevation),
            elevation_penalty = float(s.elevation_penalty),
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
            + f", elevation={ns['elevation']}.",
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

        # 2. Backward gradients from every candidate virtual node,
        #    bounded at the cutoff (see _gradient_limit override).
        t0 = time.perf_counter()
        g_indptr, g_nodes, g_dist, _g_pred = self._precompute_dest_gradients(ns)
        self.logger.log(
            "FacilityAllocation",
            f"Backward gradients: {self._n_destinations} candidates in "
            f"{time.perf_counter()-t0:.2f}s (limit={ns['search_radius']:.0f}).",
            v=1,
        )

        # 3. Harvest the demand→candidate cost matrix from the origin
        #    virtual-node entries of each gradient.
        cand_demand, cand_cost = self._extract_od_costs(
            g_indptr, g_nodes, g_dist, ns["search_radius"]
        )

        # 4. Decay-weighted access values g_ij.
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

        # 7. Greedy selection.
        selected, rank = self._greedy_max_access(
            n_orig, cand_demand, cand_gain, w, required, ns["p_new"]
        )

        # 8. Allocation of demand to the chosen configuration + outputs.
        self._allocate(
            n_orig, cand_demand, cand_cost, cand_gain, w, selected,
            rank, required,
        )
        self.has_flow_results = False   # this engine produces no edge flow

    # ──────────────────────────────────────────────────────────────────
    # Stage helpers.
    # ──────────────────────────────────────────────────────────────────
    def _extract_od_costs(self, g_indptr, g_nodes, g_dist, cutoff):
        """Per-candidate arrays of (demand index, cost) within cutoff.

        Origin virtual nodes occupy CSR indices
        [_first_origin_node, _first_origin_node + n_origins); the
        backward gradient of candidate d holds, at those indices, the
        exact shortest 'towards facility' cost origin→candidate
        (connector arcs included on both ends).
        """
        first_o = self._first_origin_node
        n_dest  = self._n_destinations
        cand_demand, cand_cost = [], []
        n_entries = 0
        for d in range(n_dest):
            lo, hi = g_indptr[d], g_indptr[d + 1]
            nodes  = g_nodes[lo:hi]
            dist   = g_dist[lo:hi]
            m      = (nodes >= first_o) & (dist <= cutoff + 1e-9)
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
        fac = gpd.GeoDataFrame(
            {
                "uid":             np.asarray(dest.uid),
                "selected":        self.fa_selected,
                "required":        self.fa_required,
                "rank":            self.fa_rank,
                "demand_served":   self.fa_demand_served,
                "access_captured": self.fa_access_captured,
            },
            geometry=gpd.GeoSeries(dest.geometry).reset_index(drop=True),
            crs=getattr(dest.geometry, "crs", None),
        )
        write_gdf_outputs(
            fac, settings, output_folder, file_name + "_facilities",
            self.logger, "FacilityAllocation", desc="facilities",
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
