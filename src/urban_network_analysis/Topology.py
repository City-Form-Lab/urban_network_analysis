### use importlib to impoer as needed. Reduce Dependencies to strictly necessary (Used by default components) and Optioa (used by optional components)l..
from __future__ import annotations
from cProfile import label
import os
os.environ['USE_PYGEOS'] = '0'
import math

import sys
import time

import psutil
import heapq
import shapely


# This enables type hints to find elements further down in the code.
from ast import Tuple
from heapq import heappush, heappop

from pathlib import Path
from typing import Dict, Self, TypeAlias #, TypedDict
from collections.abc import Collection, Callable
from sklearn.cluster import KMeans
from concurrent.futures import ThreadPoolExecutor
from shapely.strtree import STRtree
from shapely.geometry import Point, LineString

import networkx as nx
import concurrent.futures as fr
import multiprocessing as mp
import geopandas as gpd
import numpy as np
import pandas as pd
import numba as nb
import time
__version__ = '0.2.0' 

from .Logger import Logger

## GLOBAL CONSTANTS ##
ACCEPTIBLE_GEOMETRY_SOURCES = str|Path|gpd.GeoDataFrame

class Topology:

    """

    DESCRIPTION:

        This is the main class for the Topology module. 
        It contains the network and access points, as well as the logger for the module. 
        It also contains methods for adding networks and access points.

    """

    network : Network = None

    origins : AccessPoints = None
    destinations : AccessPoints = None

    # v2.4.4 — new point object types.
    observer_points : AccessPoints = None   # passive flow counters (flow only)
    obstacles       : AccessPoints = None   # cost-adding penalty points (all engines)

    # Per-arc obstacle-penalty arrays (computed in AddObstacles).  When
    # `obstacles` is None, both are None.  Otherwise:
    #   obstacle_arc_penalty_AB[n_edges] — penalty added to each edge's
    #                                      AB arc (network.start→end direction).
    #   obstacle_arc_penalty_BA[n_edges] — penalty added to each edge's
    #                                      BA arc.
    #   obstacle_node_penalty[n_nodes]   — penalty for node-snapped
    #                                      obstacles; applied to arcs
    #                                      ENTERING the node.
    obstacle_arc_penalty_AB = None
    obstacle_arc_penalty_BA = None
    obstacle_node_penalty   = None

    access_points : AccessPoints = None

    num_threads : int = mp.cpu_count() - 1 if mp.cpu_count() > 1 else 1

    has_clusters : bool = False

    cluster_masks : Dict[int, shapely.geometry.Polygon] = None

    logger : Logger = None

    num_clusters : int = None

    crs = None  # CRS read from the network file; all subsequent layers must match

    
    def __init__(self, verbosity:int=1, num_threads:int=None):

        self.logger = Logger(verbosity=verbosity)

        if num_threads is not None:
            self.num_threads = num_threads

        self.network = Network(self.logger)
        self.num_clasters = 1
        # self.access_points = AccessPoints(self.logger)

    def AddNetwork(self, settings: Settings):
       
        """
        DESCRIPTION:

                This function adds a network to the topology from a given source file. It reads the network data, processes it to extract geometries and weights, and builds the topological representation of the network. The network cost can be either geometric (length of edges) or set by an attribute in the source data.

        PARAMETERS:

            Network cost can be either "Geometric" or set by attribute name.

        """

        source_file         = os.path.join(settings.data_folder, settings.network_file)

        if not os.path.isfile(source_file):
            raise FileNotFoundError(f"Network file not found: {source_file}")

        # A reloaded network invalidates previously built clusters (their
        # edge/origin/destination assignments reference the old layers).
        self.has_clusters = False

        network_cost        = settings.network_weight_column
        default_cost        = settings.network_weight_default

        gdf = self._get_gdf(source_file, keep_columns=None)
        invalid_types = gdf.geometry.geom_type[gdf.geometry.geom_type != 'LineString'].unique().tolist()
        if invalid_types:
            raise ValueError(
                f"Network file must contain LineString (line/polyline) geometries, "
                f"but the file contains: {', '.join(invalid_types)}. "
                f"Please provide a file with line or polyline geometries."
            )
        self.crs = gdf.crs
        if self.crs is None:
            self.logger.log("Network added", "Warning: Network file has no CRS defined.", v=1)
        else:
            self.logger.log("Network added", f"CRS set to {self.crs}.", v=2)
        gdf = gdf.reset_index(drop=True)
        self.logger.log("Network added", f"Network added from source {source_file}, with {len(gdf)} edges.", v=1)

        self.network.geometry   = gdf.geometry
        self.network.weights    = gdf.geometry.length.values.astype(np.float64) if network_cost == "Geometric" else gdf[network_cost].values.astype(np.float64)
        self.network.lengths    = gdf.geometry.length.values.astype(np.float64) 
        has_node_data = False
        
        if settings.network_load_nodes:
            if "_node_start_id" in gdf.columns and "_node_end_id" in gdf.columns:
                has_node_data = True
            
            self.network.start_nodes = gdf["_node_start_id"].values.astype(np.int64) if "_node_start_id" in gdf.columns else None
            self.network.end_nodes = gdf["_node_end_id"].values.astype(np.int64) if "_node_end_id" in gdf.columns else None

            if self.network.start_nodes  is None:
                has_node_data = False

        self.network.BuildTopology(discard_redundant_edges=False, precision=settings.network_precision)

        # self.BuildTurnPenalties( turn_angle_threshold=settings.turn_threshold, turn_penalty=settings.turn_penalty, store_zero_penalties=False)

        self.logger.log('create topology', f"edge_list_dict created", v=2)

    def AddOrigins(self, settings: Settings):

        # source_file : str, cost_attribute: str = "Count", default_cost: float = 1, uid_attribute: str = None
        source_file = os.path.join(settings.data_folder, settings.origins_file)
        cost_attribute = settings.origin_weight_column if hasattr(settings, 'origin_weight_column') else "Count"
        default_cost = settings.default_cost if hasattr(settings, 'default_cost') else 1
        uid_attribute = settings.origin_uid_column if hasattr(settings, 'origin_uid_column') else None

        # self.logger.log('Add origins', f"Adding origins from source {source_file} with cost attribute '{cost_attribute}' and default cost {default_cost}.", v=2)

        if not os.path.isfile(source_file):
            raise FileNotFoundError(f"Origins file not found: {source_file}")
        self.origins = self.BuildAccessPoints(source_file, cost_attribute, default_cost, uid_attribute, label="Origins")
        # Cluster assignments are derived from the loaded layers — a fresh
        # origins object has no clasters_byId, so any previously built
        # clusters are invalid. Reset the flag so engines rebuild them
        # (RunBatch reloads layers per row; without this, row 2+ of a
        # clustered batch crashes with KeyError on clasters_byId).
        self.has_clusters = False
        self.logger.log("Origins added", f"Origins added from source {source_file}, with {len(self.origins.geometry)} points.", v=1)

    def AddDestinations(self, settings: Settings):

        source_file = os.path.join(settings.data_folder, settings.destinations_file)
        cost_attribute = settings.destination_weight_column if hasattr(settings, 'destination_weight_column') else "Count"
        default_cost = settings.default_cost if hasattr(settings, 'default_cost') else 1
        uid_attribute = settings.destination_id_column if hasattr(settings, 'destination_id_column') else None

        if not os.path.isfile(source_file):
            raise FileNotFoundError(f"Destinations file not found: {source_file}")
        self.destinations = self.BuildAccessPoints(source_file, cost_attribute, default_cost, uid_attribute, label="Destinations")
        # Fresh destinations invalidate previously built clusters (see
        # AddOrigins) — force a rebuild on the next clustered run.
        self.has_clusters = False
        self.logger.log("Destinations added", f"Destinations added from source {source_file}, with {len(self.destinations.geometry)} points.", v=1)

    # ──────────────────────────────────────────────────────────────────
    # v2.4.4 — Observer points (passive flow counters, flow only)
    # ──────────────────────────────────────────────────────────────────

    def AddObservers(self, settings: Settings):
        """
        Load a Point layer of observer points.  These are passive
        counters: they snap to the network (edge by default, optionally
        node) and the flow engine writes per-point flow_AB /
        flow_BA / flow_total counters in an extra output file.

        Observer points do NOT influence routing.  They are not used
        by the accessibility engines.
        """
        file = settings.observer_points_file
        if not file:
            self.observer_points = None
            return
        source_file = os.path.join(settings.data_folder, file)
        if not os.path.isfile(source_file):
            raise FileNotFoundError(f"Observer points file not found: {source_file}")
        uid_attribute = settings.observer_points_uid_column
        snap_to       = str(settings.observer_points_snap_to).lower().strip()
        if snap_to not in {"edge", "node"}:
            raise ValueError(
                f"observer_points_snap_to must be 'edge' or 'node'; got {snap_to!r}."
            )
        # Re-use BuildAccessPoints — observer points snap exactly like
        # origins/destinations.  We pass cost_attribute="Count" so
        # node_weight comes out as unit weights (observers carry no
        # weight by design — confirmed with user).
        self.observer_points = self.BuildAccessPoints(
            source_file, cost_attribute="Count", default_cost=1,
            uid_attribute=uid_attribute, label="Observer points",
        )
        # Resolve snap target.  For "node" mode, pick whichever of the
        # host edge's two endpoints is closest to the observer point.
        self.observer_points.snap_to = snap_to
        if snap_to == "node":
            # Closest endpoint = whichever has smaller weight_to_*.
            ap = self.observer_points
            choose_start = ap.weight_to_start <= ap.weight_to_end
            ap.snapped_node_id = np.where(
                choose_start,
                ap.edge_start_node,
                ap.edge_end_node,
            ).astype(np.int64)
        else:
            self.observer_points.snapped_node_id = None
        self.logger.log(
            "Observer points added",
            f"{len(self.observer_points.geometry)} observer points, "
            f"snap_to={snap_to}.",
            v=1,
        )

    # ──────────────────────────────────────────────────────────────────
    # v2.4.4 — Obstacle points (cost-adding, used by ALL engines)
    # ──────────────────────────────────────────────────────────────────

    def AddObstacles(self, settings: Settings):
        """
        Load a Point layer of obstacle points.  Each obstacle adds a
        penalty (in the same units as network edge weights) to the cost
        of traversing its host edge or node.  Used by both flow
        and accessibility engines.

        Settings used:
            obstacle_points_file
            obstacle_points_uid_column
            obstacle_points_penalty_column   (REQUIRED column in file)
            obstacle_points_direction_column ('both'|'AB'|'BA'; default 'both')
            obstacle_points_snap_to          ("edge" | "node")
        """
        file = settings.obstacle_points_file
        if not file:
            self.obstacles = None
            self.obstacle_arc_penalty_AB = None
            self.obstacle_arc_penalty_BA = None
            self.obstacle_node_penalty   = None
            return
        source_file = os.path.join(settings.data_folder, file)
        if not os.path.isfile(source_file):
            raise FileNotFoundError(f"Obstacle points file not found: {source_file}")
        penalty_col = str(settings.obstacle_points_penalty_column)
        uid_attribute = settings.obstacle_points_uid_column
        direction_col = settings.obstacle_points_direction_column
        snap_to       = str(settings.obstacle_points_snap_to).lower().strip()
        if snap_to not in {"edge", "node"}:
            raise ValueError(
                f"obstacle_points_snap_to must be 'edge' or 'node'; got {snap_to!r}."
            )

        # Read the raw point file to recover penalty + direction columns.
        gdf_raw = self._get_gdf(source_file, keep_columns=None)
        if penalty_col not in gdf_raw.columns:
            raise ValueError(
                f"obstacle_points_penalty_column='{penalty_col}' not found in "
                f"{source_file}.  Available columns: {list(gdf_raw.columns)}."
            )
        penalties = gdf_raw[penalty_col].values.astype(np.float64)
        if (penalties < 0).any():
            n_bad = int((penalties < 0).sum())
            raise ValueError(
                f"obstacle penalty column '{penalty_col}' has {n_bad} negative "
                f"values; penalties must be >= 0."
            )
        if direction_col is not None and direction_col in gdf_raw.columns:
            directions = gdf_raw[direction_col].astype(str).str.lower().str.strip().values
        else:
            directions = np.array(["both"] * len(gdf_raw), dtype=object)
        # Validate directions.
        bad_dir = [d for d in np.unique(directions) if d not in {"both", "ab", "ba"}]
        if bad_dir:
            raise ValueError(
                f"obstacle direction column has invalid values: {bad_dir}.  "
                f"Allowed: 'both', 'AB', 'BA'."
            )

        # Use BuildAccessPoints to snap.  We pass the penalty values
        # as `cost_attribute` so they live on `node_weight` — convenient
        # for the output writer.
        self.obstacles = self.BuildAccessPoints(
            source_file, cost_attribute=penalty_col, default_cost=0,
            uid_attribute=uid_attribute, label="Obstacle points",
        )
        self.obstacles.snap_to   = snap_to
        self.obstacles.direction = directions
        self.obstacles.penalty   = penalties

        # Compute per-arc penalty arrays.
        n_edges = int(len(self.network.weights))
        n_nodes = int(self.network.node_points.shape[0]) if hasattr(self.network, "node_points") else None
        if n_nodes is None:
            # Fallback: derive from start_nodes / end_nodes.
            n_nodes = int(max(self.network.start_nodes.max(),
                              self.network.end_nodes.max())) + 1

        self.obstacle_arc_penalty_AB = np.zeros(n_edges, dtype=np.float64)
        self.obstacle_arc_penalty_BA = np.zeros(n_edges, dtype=np.float64)
        self.obstacle_node_penalty   = np.zeros(n_nodes, dtype=np.float64)

        edge_ids = self.obstacles.nearest_edge_id.astype(np.int64)
        if snap_to == "edge":
            for i in range(len(edge_ids)):
                eid = int(edge_ids[i])
                pen = float(penalties[i])
                d   = directions[i]
                if d == "both":
                    self.obstacle_arc_penalty_AB[eid] += pen
                    self.obstacle_arc_penalty_BA[eid] += pen
                elif d == "ab":
                    self.obstacle_arc_penalty_AB[eid] += pen
                else:  # "ba"
                    self.obstacle_arc_penalty_BA[eid] += pen
        else:  # "node"
            # For node-snapped obstacles, snap each to its closest
            # endpoint of the host edge and add the penalty to that
            # node's incoming-arc bucket.
            choose_start = self.obstacles.weight_to_start <= self.obstacles.weight_to_end
            snapped_nodes = np.where(
                choose_start,
                self.obstacles.edge_start_node,
                self.obstacles.edge_end_node,
            ).astype(np.int64)
            self.obstacles.snapped_node_id = snapped_nodes
            for i in range(len(snapped_nodes)):
                n = int(snapped_nodes[i])
                self.obstacle_node_penalty[n] += float(penalties[i])

        # Also keep the snapped_node_id attribute for both modes (so
        # downstream code can look up where an obstacle lives).
        if snap_to == "edge":
            self.obstacles.snapped_node_id = None

        self.logger.log(
            "Obstacles added",
            f"{len(self.obstacles.geometry)} obstacle points, "
            f"snap_to={snap_to}.  Total per-arc penalty added: "
            f"AB={self.obstacle_arc_penalty_AB.sum():.2f}, "
            f"BA={self.obstacle_arc_penalty_BA.sum():.2f}; "
            f"node-penalty total={self.obstacle_node_penalty.sum():.2f}.",
            v=1,
        )

    # ──────────────────────────────────────────────────────────────────
    # v2.4.4 — Helper: per-arc obstacle penalties for engine CSR builds
    # ──────────────────────────────────────────────────────────────────

    def get_obstacle_arc_penalties(self):
        """
        Return (penalty_AB, penalty_BA) arrays of shape (n_edges,) —
        the per-arc obstacle penalty to add to each direction of every
        network edge.  Combines edge-snapped and node-snapped
        obstacles (node penalties are added to arcs ENTERING the node,
        to avoid double-counting on traversal).

        Returns (None, None) when no obstacles are loaded.
        """
        if self.obstacles is None:
            return None, None
        p_AB = self.obstacle_arc_penalty_AB.copy()
        p_BA = self.obstacle_arc_penalty_BA.copy()
        if self.obstacle_node_penalty is not None:
            p_AB += self.obstacle_node_penalty[self.network.end_nodes]
            p_BA += self.obstacle_node_penalty[self.network.start_nodes]
        return p_AB, p_BA

    def get_partial_edge_corrections(self, access_points, for_origins: bool = False):
        """
        Compute partial-edge obstacle corrections for origins or destinations.

        When an obstacle sits on the same edge as an origin or destination, the
        full-arc penalty injected into the CSR may over- or under-count the cost
        for the partial traversal between the access point and the edge endpoint.
        This method returns per-point additive corrections that fix the terminal
        weights used during Dijkstra seeding (origins) or distance adjustment
        (destinations).

        Only applies to edge-snapped obstacles.  Node-snapped obstacles are fully
        handled by get_obstacle_arc_penalties() and need no correction here.

        Args:
            access_points: An AccessPoints instance (topology.origins or
                           topology.destinations).
            for_origins:   True  → point travels TOWARD the endpoint (seed weights).
                                   start-side uses BA penalty, end-side uses AB penalty.
                           False → point is reached FROM the endpoint (terminal weights).
                                   start-side uses AB penalty, end-side uses BA penalty.

        Returns:
            (corr_start, corr_end) — float64 arrays of shape (n_points,), or
            (None, None) if no edge-snapped obstacles are loaded.

        Crossing condition (same for origins and destinations):
            start-side crossed  ←  obs.weight_to_start < point.weight_to_start
            end-side   crossed  ←  obs.weight_to_end   < point.weight_to_end
        """
        if self.obstacles is None or getattr(self.obstacles, 'snap_to', 'edge') != 'edge':
            return None, None

        n_pts        = len(access_points.nearest_edge_id)
        corr_start   = np.zeros(n_pts, dtype=np.float64)
        corr_end     = np.zeros(n_pts, dtype=np.float64)

        obs_edge_ids   = self.obstacles.nearest_edge_id.astype(np.int64)
        obs_wt_start   = self.obstacles.weight_to_start   # d(start_node, obstacle)
        obs_wt_end     = self.obstacles.weight_to_end     # d(obstacle, end_node)
        obs_penalties  = self.obstacles.penalty
        obs_directions = self.obstacles.direction          # 'ab' | 'ba' | 'both'

        pt_edge_ids  = access_points.nearest_edge_id.astype(np.int64)
        pt_wt_start  = access_points.weight_to_start      # d(start_node, point)
        pt_wt_end    = access_points.weight_to_end        # d(point, end_node)

        # Build a lookup: edge_id → list of obstacle indices for fast iteration.
        from collections import defaultdict
        edge_to_obs = defaultdict(list)
        for oi in range(len(obs_edge_ids)):
            edge_to_obs[int(obs_edge_ids[oi])].append(oi)

        for pi in range(n_pts):
            eid = int(pt_edge_ids[pi])
            if eid not in edge_to_obs:
                continue  # no obstacle on this edge

            for oi in edge_to_obs[eid]:
                pen = float(obs_penalties[oi])
                d   = obs_directions[oi]  # 'ab', 'ba', or 'both'

                # Does the partial traversal from start_node toward the point
                # cross this obstacle?  True when obstacle is between start_node
                # and the point (obs closer to start_node than the point is).
                crosses_start = float(obs_wt_start[oi]) < float(pt_wt_start[pi])

                # Does the partial traversal from end_node toward the point
                # cross this obstacle?  True when obstacle is between end_node
                # and the point.
                crosses_end   = float(obs_wt_end[oi])   < float(pt_wt_end[pi])

                if for_origins:
                    # Origin travels TOWARD the endpoint:
                    #   P → start_node  is BA direction
                    #   P → end_node    is AB direction
                    if crosses_start and d in ('ba', 'both'):
                        corr_start[pi] += pen
                    if crosses_end and d in ('ab', 'both'):
                        corr_end[pi] += pen
                else:
                    # Destination is reached FROM the endpoint:
                    #   start_node → P  is AB direction
                    #   end_node   → P  is BA direction
                    if crosses_start and d in ('ab', 'both'):
                        corr_start[pi] += pen
                    if crosses_end and d in ('ba', 'both'):
                        corr_end[pi] += pen

        return corr_start, corr_end

    def BuildClusters(self, n_clusters: int = 10, search_radius: float = 100, random_state: int = 42, cluster_concave_ratio: float = 0.23):

        """
        DSCRIPTION:

            We collect indeces for for network, origins and desintation.

        PARAMETERS:
            n_clusters: int = 10, 
            search_radius: float = 100, 
            random_state: int = 42, 
            cluster_concave_ratio: float = 0.23

        
        STEPS:
            1. Validate that network, origins and destinations are added before building clusters.
            2. Use KMeans clustering to group origins into n_clusters based on their coordinates.


        """
        
        if self.network is None or self.origins is None or self.destinations is None:
            raise ValueError("Network, origins and destinations must be added before building clusters.")
        
        self.num_clusters = n_clusters

        self.logger.log("Build clusters", f"Building clusters with n_clusters={n_clusters} and random_state={random_state}.", v=1)

        kMeans = KMeans(n_clusters=n_clusters, random_state=random_state)

        kMeans.fit(self.origins.xy)

        self.logger.log("Build clusters", f"Clusters built successfully.", v=1)

        self.cluster_masks = dict()
        self.origins.clasters_byId = dict()
        self.destinations.clasters_byId = dict()
        self.network.clasters_byId = dict()

        for i in range(n_clusters):
            cluster_origin_ilocs = np.where(kMeans.labels_ == i)[0]
            cluster_origin_points = self.origins.geometry.iloc[cluster_origin_ilocs] # Point sets

            self.origins.clasters_byId[i] = cluster_origin_ilocs

            point_set = shapely.unary_union(self.origins.geometry.iloc[cluster_origin_ilocs])

            cluster_mask = shapely.concave_hull(
                geometry = point_set,
                ratio = cluster_concave_ratio,
            ).buffer(search_radius)

            self.cluster_masks[i] = cluster_mask

            self.logger.log("Build clusters", f"Cluster {i}: {len(cluster_origin_ilocs)} origins.", v=3)

            # select destinations

            #destination_ilocs = self.destinations.sindex.query(cluster_mask, predicate='intersects')

            destination_ilocs = self.destinations.geometry.sindex.query(cluster_mask, predicate='intersects')
            self.destinations.clasters_byId[i] = destination_ilocs
            # self.destinations.clasters_byId[i] = self.destinations.geometry.iloc[destination_ilocs] 

            self.logger.log("Build clusters", f"Cluster {i}: {len(destination_ilocs)} destinations intersect cluster mask.", v=3)

            # select network edges

            cluster_edge_ilocs = self.network.edge_sindex.query(cluster_mask, predicate='intersects')

            self.network.clasters_byId[i] = cluster_edge_ilocs 

            self.logger.log("Build clusters", f"Cluster {i}: {len(cluster_edge_ilocs)} network edges intersect cluster mask.", v=3)

        self.has_clusters = True

            # # Find the nearest network node for each origin point in the cluster
            # nearest_node_indices = self.network.edge_sindex.nearest(cluster_origin_points, return_all=False)
            # nearest_nodes = self.network.node_points[nearest_node_indices]

            # # Here you can store or process the nearest nodes for each cluster as needed
            # # For example, you could create a new attribute in the origins to store the cluster assignment
            # self.origins.cluster_labels[cluster_origin_indices] = i

    def BuildTurnPenalties(
        self,
        turn_angle_threshold: float = 45.0,
        turn_penalty: float = 32.0,
        store_zero_penalties: bool = False,
    ) -> None:
        """Delegate to Network.BuildTurnPenalties — see that method for full docs."""
        if self.network is None:
            raise ValueError("Network must be added before building turn penalties.")
        self.network.BuildTurnPenalties(
            turn_angle_threshold=turn_angle_threshold,
            turn_penalty=turn_penalty,
            store_zero_penalties=store_zero_penalties,
        )

    def BuildAccessPoints(self, source_file : str, cost_attribute: str = "Count", default_cost: float = 1, uid_attribute: str = None, label: str = "Access points") -> AccessPoints:


        """
        DESCRIPTION:

            We build access points and return them to variabe. 

        STEPS:

            1. Read the access point data from the source file and filter to keep only point geometries.
            2. Extract the geometry and cost/weight for each access point. If cost_attribute
            3. Build the AccessPoints object using the extracted geometries and weights.
            4. Join the access points to the network.
            5. Update the access points with the nearest edge information.

        RETURNS:

            Access points

        """




        gdf = self._get_gdf(source_file, keep_columns=None)
        invalid_types = gdf.geometry.geom_type[gdf.geometry.geom_type != 'Point'].unique().tolist()
        if invalid_types:
            raise ValueError(
                f"{label} file must contain Point geometries, "
                f"but the file contains: {', '.join(invalid_types)}. "
                f"Please provide a file with point geometries."
            )
        if self.crs is not None:
            if gdf.crs is None:
                raise ValueError(
                    f"{label} file has no CRS defined, but network CRS is {self.crs}. "
                    f"Please assign CRS {self.crs} to the file before loading."
                )
            elif not gdf.crs.equals(self.crs):
                raise ValueError(
                    f"{label} file CRS ({gdf.crs}) does not match network CRS ({self.crs}). "
                    f"Please reproject to {self.crs} before loading."
                )
        gdf = gdf.reset_index(drop=True)
        
        self.logger.log("Build AccessPoints", f"Access points are built from source {source_file}, with {len(gdf)} points.", v=1)  

        pts        = gdf.geometry
        # Resolve weights column.  "Count" is the historical sentinel for
        # "unit weights".  Any other configured column is looked up in the
        # GeoDataFrame; if it's missing, fall back to unit weights and log
        # a warning rather than crashing — useful when the betweenness
        # engine has *_weights=False (column never gets used downstream)
        # and the user just hasn't bothered to keep the column name in sync
        # with the source file.
        if cost_attribute == "Count":
            weights = np.ones(len(gdf), dtype=np.float64)
        elif cost_attribute in gdf.columns:
            weights = gdf[cost_attribute].values.astype(np.float64)
            n_nan = int(np.isnan(weights).sum())
            if n_nan > 0:
                # A single NaN weight poisons every downstream sum it
                # enters (Huff denominators, gravity totals), turning
                # whole flow/accessibility outputs into NaN. Warn loudly
                # and identify the rows so the source data can be fixed.
                bad_rows = np.where(np.isnan(weights))[0][:10].tolist()
                self.logger.log(
                    "Build AccessPoints",
                    f"WARNING: {label} weight column '{cost_attribute}' "
                    f"contains {n_nan} NaN value(s) (feature rows "
                    f"{bad_rows}{'…' if n_nan > 10 else ''}). NaN weights "
                    f"propagate through gravity and Huff-allocation sums "
                    f"and will produce NaN results wherever these points "
                    f"are reachable. Fix or remove these features in the "
                    f"source file, or use a clean weight column.",
                    v=0,
                )
        else:
            self.logger.log(
                "Build AccessPoints",
                f"{label} file has no column '{cost_attribute}' — "
                f"falling back to unit weights.  Available columns: "
                f"{list(gdf.columns)}.  If you intended to use a real "
                f"weight column, set {label.lower()}_weight_column to a "
                f"column that exists in the file; otherwise this fallback "
                f"is harmless when flow_{label.lower()}_weights=False.",
                v=1,
            )
            weights = np.ones(len(gdf), dtype=np.float64)
        uid        = np.arange(len(gdf)) if not uid_attribute else gdf[uid_attribute]

        raw        = gdf

        pt = AccessPoints(self.logger)

        pt.geometry = pts
        pt.node_weight = weights
        pt.uid = uid


        max_workers = min(self.num_threads, len(weights))


        if max_workers >= 1:

            executer = ThreadPoolExecutor(max_workers=max_workers)
            
            geometry_chuncks = np.array_split(pts, max_workers) # It is set of points

            self.logger.log('Build AccessPoints', f"There are {len(pts)} points in {max_workers} chunks", v=3)
            # self.logger.log('Build AccessPoints', f"array_split done", v=3)

            edge_geometry = self.network.geometry # : gpd.GeoSeries 
            find_edge_iloc = lambda x: self.network.edge_sindex.nearest(x, return_all=False)  # returns nearest-edge index/indices for the input geometry (scalar or array depending on sindex API)
            
            # Run the nearest-query in parallel over the pre-split geometry chunks and collect results per chunk.
            edge_iloc_chunks = list(executer.map(find_edge_iloc, geometry_chuncks))  # each item is an array/list of nearest-edge ilocs for that chunk
            
            edge_ilocs = np.concatenate([chunk[1] for chunk in edge_iloc_chunks])
            #pts_ilocs = np.concatenate([chunk[0] for chunk in edge_iloc_chunks]) # do i need that?

            self.logger.log('Build AccessPoints', f"Nearest edge is found", v=3)

            edge_geometry_chunks = [self.network.geometry[edge_iloc_chunk[1]] for edge_iloc_chunk in edge_iloc_chunks]

            find_project = lambda lines, points: shapely.line_locate_point(
                np.asarray(lines),
                np.asarray(points),
                normalized=True,
            )

            project_chunks = list(executer.map(find_project, edge_geometry_chunks, geometry_chuncks))

            # self.logger.log('Build AccessPoints', f"Projection done", v=3)

            find_weight_to_node = lambda ilocs, projections: np.vstack(
                [self.network.weights[ilocs[1]] * p for p in (projections, 1-projections)]
            )

            #second_elements = np.array([sub[1] for sub in edge_iloc_chunks])

            weight_to_node = np.hstack(list(executer.map(find_weight_to_node, edge_iloc_chunks, project_chunks)))
            # w1 = list(executer.map(find_weight_to_start, edge_iloc_chunks, project_chunks))
            # self.logger.log('Build AccessPoints', f"weight_to_start done", v=3)
            pt.weight_to_start = weight_to_node[0]
            pt.weight_to_end = weight_to_node[1]

            # this is optional but it is good to have the actual nearest point on the edge for potential future use (e.g. for visualization or more accurate distance calculations)

            pt.nearest_edge_point = gpd.array.GeometryArray(
                np.concatenate(
                    list(executer.map(lambda edges, projects: edges.interpolate(projects, normalized=True), edge_geometry_chunks, project_chunks))
                    )
                )

            # NB: pt.node_weight was already set above from the resolved
            # `weights` (real column values, "Count" sentinel ones, or
            # missing-column fallback ones).  A defensive reassignment
            # used to live here referencing an undefined `source_geometry`
            # variable; it was dead code (the condition never fired) and
            # has been removed.

            pt.edge_start_node = self.network.start_nodes[edge_ilocs] #BUG
            pt.edge_end_node = self.network.end_nodes[edge_ilocs] #BUG

            pt.nearest_edge_id = edge_ilocs

            executer.shutdown(wait=True)

            self.logger.log('Build AccessPoints', f"Done", v=3)

            return pt

        else:
            pass


        return pt

    def Evaluate(self, raise_on_error: bool = False) -> dict:
        """
        Runs spatial sanity checks on loaded layers.

        Useful when CRS labels are missing or incorrect and geometry coordinates
        may not visually align. Does not rely on CRS metadata — all checks are
        based on the actual coordinate values.

        Checks performed:
            1. bbox_overlap        — network bounding box must intersect each
                                     point layer's bounding box.
            2. coord_system        — infers whether each layer looks like
                                     geographic (lon/lat, |x|≤180, |y|≤90) or
                                     projected, and warns when they differ.
            3. snap_distance       — median distance from each access point to
                                     its snapped position on the network edge;
                                     large values (> 5 % of network extent) are
                                     a strong indicator of a CRS mismatch even
                                     when bboxes happen to overlap.

        Parameters:
            raise_on_error : bool
                If True, raises ValueError on the first failed check.
                Default False — all checks run and results are returned.

        Returns:
            dict  —  { check_name: { 'passed': bool, 'message': str }, ... }
        """

        results = {}

        def _record(name: str, passed: bool, message: str):
            results[name] = {'passed': passed, 'message': message}
            self.logger.log('Evaluate', ('OK  ' if passed else 'WARN') + f' [{name}] {message}', v=1)
            if not passed and raise_on_error:
                raise ValueError(f"Topology evaluation failed [{name}]: {message}")

        # ── network loaded ────────────────────────────────────────────────
        if self.network is None or self.network.geometry is None:
            _record('network_loaded', False, "No network loaded; evaluation cannot proceed.")
            return results
        _record('network_loaded', True, "Network is loaded.")

        net_bounds = self.network.geometry.total_bounds   # (minx, miny, maxx, maxy)
        net_bbox   = shapely.box(*net_bounds)
        net_extent = max(net_bounds[2] - net_bounds[0], net_bounds[3] - net_bounds[1])

        def _coord_system(bounds):
            """Returns 'geographic' when coords look like lon/lat, else 'projected'."""
            minx, miny, maxx, maxy = bounds
            if abs(minx) <= 180 and abs(maxx) <= 180 and abs(miny) <= 90 and abs(maxy) <= 90:
                return 'geographic'
            return 'projected'

        net_coord_type = _coord_system(net_bounds)

        layers = [('origins', self.origins), ('destinations', self.destinations)]

        for layer_name, layer in layers:
            if layer is None or layer.geometry is None:
                continue

            pt_bounds    = layer.geometry.total_bounds
            pt_bbox      = shapely.box(*pt_bounds)
            pt_coord_type = _coord_system(pt_bounds)

            # ── 1. bounding box overlap ───────────────────────────────────
            if net_bbox.intersects(pt_bbox):
                _record(
                    f'bbox_overlap_{layer_name}', True,
                    f"{layer_name.capitalize()} bbox overlaps network bbox."
                )
            else:
                _record(
                    f'bbox_overlap_{layer_name}', False,
                    f"{layer_name.capitalize()} bbox does NOT overlap network bbox. "
                    f"Network: {tuple(round(v,1) for v in net_bounds)}, "
                    f"{layer_name}: {tuple(round(v,1) for v in pt_bounds)}. "
                    f"Likely CRS mismatch or wrong data files."
                )

            # ── 2. coordinate system appearance ──────────────────────────
            if net_coord_type != pt_coord_type:
                _record(
                    f'coord_system_{layer_name}', False,
                    f"Network appears {net_coord_type} but {layer_name} appears "
                    f"{pt_coord_type}. Check CRS assignment on both layers."
                )
            else:
                _record(
                    f'coord_system_{layer_name}', True,
                    f"Both network and {layer_name} appear to use "
                    f"{net_coord_type} coordinates."
                )

            # ── 3. snap distance (only if access points have been snapped) ─
            if layer.nearest_edge_point is not None:
                snap_threshold = net_extent * 0.05   # 5 % of network extent
                distances = shapely.distance(
                    np.asarray(layer.geometry),
                    np.asarray(layer.nearest_edge_point),
                )
                median_dist = float(np.median(distances))
                max_dist    = float(np.max(distances))
                pct         = median_dist / net_extent * 100 if net_extent > 0 else 0.0

                if median_dist > snap_threshold:
                    _record(
                        f'snap_distance_{layer_name}', False,
                        f"{layer_name.capitalize()} median snap distance is "
                        f"{median_dist:.2f} ({pct:.1f}% of network extent, max {max_dist:.2f}). "
                        f"Points are far from the network — check for CRS mismatch."
                    )
                else:
                    _record(
                        f'snap_distance_{layer_name}', True,
                        f"{layer_name.capitalize()} median snap distance: "
                        f"{median_dist:.2f} (max {max_dist:.2f})."
                    )

        failed = [k for k, v in results.items() if not v['passed']]
        if failed:
            self.logger.log('Evaluate', f"{len(failed)} check(s) failed: {', '.join(failed)}", v=1)
        else:
            self.logger.log('Evaluate', "All checks passed.", v=1)

        return results

    ### EXPORTERS ###

    def ExportNetworkNodes(self, output_file: str):

        attr = {"degrees": self.network.degrees, "node_id": np.arange(len(self.network.node_points))}

        if self.network.node_flow is not None:
            attr["node_flow"] = self.network.node_flow

        points = [shapely.Point(xy) for xy in self.network.node_points]
        gdf=gpd.GeoDataFrame(attr, geometry=points, crs="EPSG:3301")
        gdf.to_file( output_file, driver='GeoJSON')

        self.logger.log('export network nodes', f"Network nodes exported to {output_file}", v=1)

    def ExportNetworkNodesGradient(self, output_file: str):
        """
        Export network nodes with gradient distances from origins.
        
        Each column represents distances from a specific source origin node.
        network.node_gradient is expected to be a dict where:
            key: source origin node ID
            value: np.array of distances to all network nodes (inf for unreachable)
        
        Parameters:
            output_file: str - path to output file (.feather, .parquet, .geojson)
        """
        
        # Start with basic node attributes
        attr = {
            "degrees": self.network.degrees,
            "node_id": np.arange(len(self.network.node_points))
        }
        
        # Add gradient distances for each source origin
        if self.network.node_gradient and len(self.network.node_gradient) > 0:
            for source_node_id, distances in self.network.node_gradient.items():
                # Ensure distances is numpy array and matches node count
                distances = np.asarray(distances, dtype=np.float64)
                if len(distances) != len(self.network.node_points):
                    self.logger.log(
                        'export network gradient',
                        f"Distance array size {len(distances)} doesn't match node count {len(self.network.node_points)} for source {source_node_id}",
                        v=2
                    )
                
                # Use source node ID as column name
                attr[f"distance_from_{source_node_id}"] = distances
            
            self.logger.log(
                'export network gradient',
                f"Added {len(self.network.node_gradient)} gradient source(s)",
                v=2
            )
        else:
            self.logger.warn(
                'export network gradient',
                "No gradients found in network.node_gradient",
                v=2
            )
        
        # Create GeoDataFrame with points
        points = [shapely.Point(xy) for xy in self.network.node_points]
        gdf = gpd.GeoDataFrame(attr, geometry=points, crs="EPSG:3301")
        
        # Determine output format from file extension
        _, file_extension = os.path.splitext(output_file)
        file_extension = file_extension.lower()
        
        if file_extension == '.feather':
            gdf.to_feather(output_file)
        elif file_extension == '.parquet':
            gdf.to_parquet(output_file)
        elif file_extension in ('.geojson', '.json'):
            gdf.to_file(output_file, driver='GeoJSON')
        else:
            # Default to feather format
            gdf.to_feather(output_file)
        
        self.logger.log(
            'export network nodes gradient',
            f"Network nodes with {len(self.network.node_gradient)} gradient(s) exported to {output_file}",
            v=1
        )

    def ExportNetworkLinks(self, output_file: str):

        


        gdf=gpd.GeoDataFrame({ "weight": self.network.weights}, geometry= self.network.geometry, crs="EPSG:3301")
        # gdf.to_file( output_file, driver='GeoJSON')

        gdf.to_feather( output_file)

        self.logger.log('export network links', f"Network links exported to {output_file}", v=1)

    def ExportNetwork(self, output_file: str, geometry_column: str = "geometry", keep_z: bool = False, connected: bool = False, export_filtered_edges: bool = False):

        self.network.ExportNetwork(output_file, geometry_column, keep_z, connected=connected, export_filtered_edges=export_filtered_edges)

        self.logger.log('export network', f"Network exported to {output_file}", v=1)

    def ExportClusters(self, output_folder: str):

        polygon_masks = []
        clasters = []

        attr = {
            "clasters": list(self.cluster_masks.keys()),
        }

        for cluster_id, mask in self.cluster_masks.items():
            polygon_masks.append(mask)
            clasters.append(cluster_id)

        gdf = gpd.GeoDataFrame(attr, geometry=polygon_masks, crs="EPSG:3301")

        output_file = os.path.join(output_folder, f"debug_cluster_mask.feather")
        gdf.to_feather(output_file)
        self.logger.log('export cluster masks', f"Cluster mask exported to {output_file}", v=1)

        # origins

        self.origins.ExportClusters(os.path.join(output_folder, f"debug_origins_with_clusters.feather"))
        self.logger.log('export origins with clusters', f"Origins with clusters exported to {output_file}", v=1)

        # destinations

        self.destinations.ExportClusters(os.path.join(output_folder, f"debug_destinations_with_clusters.feather"))  
        self.logger.log('export destinations with clusters', f"Destinations with clusters exported to {output_file}", v=1)

        # network

        self.network.ExportClusters(os.path.join(output_folder, f"debug_network_with_clusters.feather"))
        self.logger.log('export network with clusters', f"Network with clusters exported to {output_file}", v=1)

    @staticmethod
    def _get_gdf(source, keep_columns, ) -> gpd.GeoDataFrame:

        if not isinstance(source, ACCEPTIBLE_GEOMETRY_SOURCES.__args__):
            raise TypeError(f"Unsupported source of type {type(source)}, supported source types are {ACCEPTIBLE_GEOMETRY_SOURCES.__args__}")
        
        
        if isinstance(keep_columns, list) :
            keep_columns += ['geometry']
        
        if isinstance(source, gpd.GeoDataFrame):
            gdf = source.copy(deep=True) if keep_columns is None else source[keep_columns].copy(deep=True)
                 
        try:
            source = Path(source)
        except:
            raise ValueError(f'Source {source} is neither an existing layer, a GeoDataFrame nor a valid path to a file.')
        
            ## Check extension then decide loader..
        if not os.path.isfile(source):
            raise ValueError(f'Source {source} is not a path to existing file.')
        
        ## Check file type and decide what reader to use.
        _, file_extension = os.path.splitext(source)


        if file_extension.lower() == '.feather':
            gdf =  gpd.read_feather(source, columns=keep_columns)
        elif file_extension.lower() == '.parquet':
            gdf =  gpd.read_parquet(source, columns=keep_columns)
        else:
            gdf =  gpd.read_file(source, engine='pyogrio', use_arrow=True)

        #gdf =  GeoDataFrameLayer.validate_gdf(gdf)       
        return gdf

class Network:

    logger: Logger = None

    # Per Edge
    # start_points: np.ndarray = None
    # end_points: np.ndarray = None
    
    # Topological Nodes
    node_points: np.ndarray = None  # it is node index
    start_nodes: np.ndarray = None # index is same as edge index, value is start node index for that edge
    end_nodes: np.ndarray = None # index is same as edge index, value is end node index for that edge
    degrees: np.ndarray = None # index is same as node index, value is degree of that node

    z :np.ndarray = None # elevation values for each node, optional but can be used for elevation-based impedance factors

    node_gradient: Dict[int, np.ndarray] = None # optional, can be used to store precomputed node gradients for accessibility calculations

    geometry: gpd.GeoSeries = None # this is original geometry. 
    weights: np.ndarray = None
    lengths: np.ndarray = None
    AB_weights: np.ndarray = None # optional, can be used to store separate weights for A->B and B->A if needed for directed graphs or elevation-based penalties
    BA_weights: np.ndarray = None # optional, can be used to store separate weights for A->B and B->A if needed for directed graphs or elevation-based penalties

    node_flow : np.ndarray = None # optional, stores node flow values after computation
    edge_flow : np.ndarray = None # optional, stores edge flow values after computation

    mask: np.ndarray = None # optional, can be used to store boolean mask for edges that are included in the topology (e.g. after discarding redundant edges or applying filters)

    turns: Dict[Tuple[int, int, int], float] = None # optional, can be used to store turn penalties for specific node triples (from_node, via_node, to_node)

    edge_sindex: STRtree = None

    num_threads : int = mp.cpu_count() - 1 if mp.cpu_count() > 1 else 1

    clasters_byId : Dict[int, np.ndarray] = None

    def __init__(self, logger ):
        self.logger = logger
        self.node_gradient = {}


    def BuildTopology(self, discard_redundant_edges: bool = False, precision: int = 6):

        """
        DESCRIPTION:

            Builds the topological representation of the network from the input geometries
            and weights. Extracts edge start/end points, identifies unique nodes, maps edges
            to those nodes, computes node degrees, and creates a spatial index.

        STEPS:
            1. Validate geometry and weights; default weights to geometric length if missing.
            2. Extract edge start and end coordinates from the input geometries.
            3. Identify unique nodes and map each edge to its start/end node indices.
            4. If discard_redundant_edges=True, drop parallel edges keeping the lowest weight.
            5. Create a spatial index (STRtree) for the edge geometries.

        """

        if self.geometry is None:
            raise ValueError("No geometry found for building topology. Please add a network with valid geometry before building topology.")
        if self.weights is None:
            self.logger.warn("No weights found for building topology. Defaulting to geometric length as weight.")
            self.weights = self.geometry.length.values

        if hasattr(self.geometry, 'is_empty'):
            empty_mask = self.geometry.is_empty
        else:
            empty_mask = np.array([geom.is_empty for geom in self.geometry], dtype=bool)
        if np.any(empty_mask):
            self.logger.log(
                'BuildTopology',
                f"Removing {np.count_nonzero(empty_mask)} empty geometries from network before topology build.",
                v=1,
            )
            if hasattr(self.geometry, 'reset_index'):
                self.geometry = self.geometry[~empty_mask].reset_index(drop=True)
            else:
                self.geometry = self.geometry[~empty_mask]
            self.weights = self.weights[~empty_mask]

        start_points, end_points = self._edge_start_and_end_points(
            self.geometry.to_numpy() if not isinstance(self.geometry, np.ndarray) else self.geometry,
            precision=precision,
        )

        self.node_points, self.start_nodes, self.end_nodes, self.degrees = \
            self._edge_start_and_end_nodes_2d(start_points, end_points)

        edge_degrees = self._calculate_edge_node_degrees(self.start_nodes, self.end_nodes, self.degrees)
        self.start_degrees = edge_degrees['start_degree']
        self.end_degrees = edge_degrees['end_degree']
        self.min_degrees = edge_degrees['min_degree']
        self.max_degrees = edge_degrees['max_degree']

        if self.node_points.shape[1] == 3:
            self.z = self.node_points[:, 2].copy()
            self.node_points = self.node_points[:, :2]
        else:
            self.z = None

        self.logger.log('BuildTopology', f"Topology built with {self.node_points.shape[0]} nodes and {self.start_nodes.shape[0]} edges.", v=2)

        if discard_redundant_edges:
            unique_edges = self._get_unique_edges(self.start_nodes, self.end_nodes, self.weights)
            self.logger.log('build topology', f"{self.start_nodes.shape[0] - unique_edges.shape[0]} redundant edges dropped", v=2)

            self.start_nodes = self.start_nodes[unique_edges]
            self.end_nodes = self.end_nodes[unique_edges]
            self.weights = self.weights[unique_edges]
            self.geometry = self.geometry[unique_edges]

        # I do not know why i need to export sindex
        # https://geopandas.org/en/latest/docs/reference/api/geopandas.GeoDataFrame.sindex.html
        self.edge_sindex = self.geometry.sindex

        self.logger.log('create topology', f"edge_sindex created", v=2)



    def BuildTopologyWElevation(self, discard_redundant_edges: bool = False, use_elevation: bool = False, elevation_coeficent: float = 1.0, precision: int = 6, load_nodes: bool = False):

        """
        DESCRIPTION:

            This function builds the topological representation of the network from the input geometries and weights. It extracts edge start and end points, identifies unique nodes, maps edges to these nodes, and creates a spatial index for efficient querying. It can also identify and discard redundant edges based on weights.
        
        STEPS:
            1. Validate that geometry and weights are available. If weights are missing, default to geometric length.
            2. Extract edge start and end coordinates from the input geometries.   
            3. Identify unique nodes from the start and end coordinates, and map each edge to its corresponding start and end node indices. Also calculate node degrees.
            4. If discard_redundant_edges=True, identify and retain a unique set of edges (e.g., keep the edge with minimal weight for each node pair).
            5. If load_nodes=True, load node information from the network file.
            6. Create a spatial index (STRtree) for the edge geometries

        """

        raise DeprecationWarning("BuildTopologyWElevation is deprecated. Please use BuildTopology and then apply elevation-based weight adjustments separately if needed.")


        if self.geometry is None:
            raise ValueError("No geometry found for building topology. Please add a network with valid geometry before building topology.")
        if self.weights is None:
            self.logger.warn("No weights found for building topology. Defaulting to geometric length as weight.")
            self.weights = self.geometry.length.values
       
        start_points, end_points = self._edge_start_and_end_points( self.geometry.to_numpy() if not isinstance(self.geometry, np.ndarray) else self.geometry, precision=precision)
        

        # If load_nodes is True, reorganize node_points to be indexed by node ID from the data
        if load_nodes:
            self.logger.log('BuildTopology', f"Loading nodes from edge topology...", v=2)

            # Collect all unique node IDs from start_nodes and end_nodes
            all_node_ids = np.concatenate([self.start_nodes, self.end_nodes])
            unique_node_ids = np.unique(all_node_ids)
            max_node_id = unique_node_ids.max()
            
            self.logger.log('BuildTopology', f"Found {len(unique_node_ids)} unique node IDs, max ID: {max_node_id}", v=3)
            
            node_points_by_id = {}
            node_edges_by_idx = {}

            for topo_node_idx, (start_node_id, end_node_id) in enumerate(zip(self.start_nodes, self.end_nodes)):
                # Add location for start node (if not already seen)
                if start_node_id not in node_points_by_id:
                    node_points_by_id[start_node_id] = start_points[topo_node_idx]
                
                if start_node_id not in node_edges_by_idx:
                    node_edges_by_idx[start_node_id] = []

                node_edges_by_idx[start_node_id].append(topo_node_idx)

                # Add location for end node (if not already seen)
                if end_node_id not in node_points_by_id:
                    node_points_by_id[end_node_id] = end_points[topo_node_idx]
                if end_node_id not in node_edges_by_idx:
                    node_edges_by_idx[end_node_id] = []

                node_edges_by_idx[end_node_id].append(topo_node_idx)
            
            num_dims = end_points.shape[1]
            self.node_points = np.full((max_node_id + 1, num_dims), np.nan, dtype=np.float64)

            for k in node_points_by_id:
                self.node_points[k, :] = node_points_by_id[k]
            
            # Update degrees array to match the new indexing
            self.degrees = np.zeros(max_node_id + 1, dtype=np.int32)
            for node_idx in range(len(self.degrees)):

                if node_idx not in node_edges_by_idx:
                    self.degrees[node_idx] = 0
                else:
                    self.degrees[node_idx] = len(node_edges_by_idx[node_idx])
                
            self.logger.log('BuildTopology', f"Reorganized node_points to ID-indexed format: shape {self.node_points.shape}", v=2)

        else:

            # self.node_points, self.start_nodes, self.end_nodes, self.degrees = self._edge_start_and_end_nodes(start_points, end_points)
            self.node_points, self.start_nodes, self.end_nodes, self.degrees = self._edge_start_and_end_nodes_2d(start_points, end_points)
            # self.node_points, self.start_nodes, self.end_nodes, self.degrees = self._edge_start_and_end_nodes(start_points, end_points)





        edge_degrees = self._calculate_edge_node_degrees(self.start_nodes, self.end_nodes, self.degrees)
        self.start_degrees = edge_degrees['start_degree']
        self.end_degrees = edge_degrees['end_degree']
        self.min_degrees = edge_degrees['min_degree']
        self.max_degrees = edge_degrees['max_degree']

        if self.node_points.shape[1] == 3:
            self.z = self.node_points[:, 2].copy()
            self.node_points = self.node_points[:, :2]
        else:
            self.z = None


        self.logger.log('BuildTopology', f"Topology built with {self.node_points.shape[0]} nodes and {self.start_nodes.shape[0]} edges.", v=2)

        if (use_elevation and self.z is not None):
            self.logger.log('BuildTopology', f"Applying elevation-based weight adjustments.", v=2)
            self.AB_weights = np.empty_like(self.weights)
            self.BA_weights = np.empty_like(self.weights)

            for i in range(len(self.weights)):
                elev_from = self.z[self.start_nodes[i]]
                elev_to = self.z[self.end_nodes[i]]
                base_weight = self.weights[i]

                self.AB_weights[i] = self._calculate_edge_weight(base_weight, elev_from, elev_to, elevation_coeficent)
                self.BA_weights[i] = self._calculate_edge_weight(base_weight, elev_to, elev_from, elevation_coeficent)

            # If using elevation-based weights, we can choose to replace the original weights with the adjusted ones or keep them separate. Here we keep them separate for flexibility.
            # If you want to replace the original weights with the adjusted ones (e.g., AB_weights), you can uncomment the following line:
            # self.weights = self.AB_weights

        if not use_elevation:

            if discard_redundant_edges:
                unique_edges = self._get_unique_edges(self.start_nodes, self.end_nodes, self.weights)
                self.logger.log('build topology', f"{self.start_nodes.shape[0] - unique_edges.shape[0]} redundant edges dropped", v=2)

                self.start_points = start_points[unique_edges]
                self.end_points = end_points[unique_edges]
                self.start_nodes = self.start_nodes[unique_edges]
                self.end_nodes = self.end_nodes[unique_edges]
                self.weights = self.weights[unique_edges]
                self.geometry = self.geometry[unique_edges]

        # I do not know why i need to export sindex
        # https://geopandas.org/en/latest/docs/reference/api/geopandas.GeoDataFrame.sindex.html
        self.edge_sindex = self.geometry.sindex

        self.logger.log('create topology', f"edge_sindex created", v=2)

    def BuildTurnPenalties(
        self,
        turn_angle_threshold: float = 45.0,
        turn_penalty: float = 32.0,
        store_zero_penalties: bool = False,
    ) -> None:

        """
        DESCRIPTION:

            Computes turn penalties for every ordered (from_node, via_node, to_node) triple
            that can arise during graph traversal and stores them in self.turns.

            The angle is measured using only the edge segment *closest to via_node*:
              - for the incoming edge: last segment (coords[-2] → coords[-1] when via_node
                is end_node, or reversed first segment when via_node is start_node).
              - for the outgoing edge: first segment from via_node.
            If an edge has exactly two points the whole edge is its own single segment.

            Angle convention:
              0°  = straight through (no deviation)
              90° = right/left turn
              180° = U-turn (same edge traversed back)

            A penalty of `turn_penalty` is recorded whenever the angle exceeds
            `turn_angle_threshold`.  Zero-penalty entries are omitted by default.

        PARAMETERS:

            turn_angle_threshold : float
                Deviation from straight in degrees above which penalty is applied.
                Default 45°.
            turn_penalty : float
                Cost added to traversal when turn angle > threshold.  Default 32
                (same units as edge weights).
            store_zero_penalties : bool
                If True, entries with penalty 0 are also stored — useful for
                analysis or to distinguish "no entry" from "entry with cost 0".
                Default False (recommended for Dijkstra — smaller dict, faster
                single-lookup via .get()).

        LOOKUP (recommended)::

            penalty = self.turns.get((from_node, via_node, to_node), 0)

            Single hash lookup returning 0 when no penalty exists.  No two-step
            existence check needed — .get() with a default is always one lookup.
            Storing only non-zero entries keeps the dict small and cache-friendly.

        RESULT SIZE:

            A degree-N node produces N² ordered (from, via, to) triples.
            degree 1 (dead-end) → 1 triple (the U-turn)
            degree 2             → 4 triples
            degree 3             → 9 triples

        """

        if self.geometry is None or self.start_nodes is None or self.end_nodes is None:
            raise ValueError(
                "Network topology must be built before calling BuildTurnPenalties."
            )

        n_edges = len(self.start_nodes)
        n_nodes = len(self.node_points)
        geom_array = self.geometry.to_numpy()

        # ------------------------------------------------------------------
        # Precompute outgoing unit vectors at each endpoint for every edge.
        #
        #   outgoing_at_start[e]  — unit vector from start_node along the
        #                           first segment (away from start_node).
        #   outgoing_at_end[e]    — unit vector from end_node along the last
        #                           segment reversed (away from end_node).
        # ------------------------------------------------------------------
        outgoing_at_start = np.zeros((n_edges, 2), dtype=np.float64)
        outgoing_at_end   = np.zeros((n_edges, 2), dtype=np.float64)

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

        # ------------------------------------------------------------------
        # Build node → incident-edge list.
        # ------------------------------------------------------------------
        node_to_edges: list = [[] for _ in range(n_nodes)]
        for e in range(n_edges):
            node_to_edges[self.start_nodes[e]].append(e)
            node_to_edges[self.end_nodes[e]].append(e)

        # ------------------------------------------------------------------
        # For each via_node, iterate all ordered (e_in, e_out) pairs and
        # record the turn penalty.
        # ------------------------------------------------------------------
        cos_threshold = float(np.cos(np.deg2rad(turn_angle_threshold)))
        turns: dict = {}

        for via_node in range(n_nodes):
            incident = node_to_edges[via_node]
            if not incident:
                continue

            # (neighbor_node, outgoing_unit_vec_from_via_node_along_this_edge)
            edge_data = []
            for e in incident:
                if self.start_nodes[e] == via_node:
                    edge_data.append((int(self.end_nodes[e]),   outgoing_at_start[e]))
                else:
                    edge_data.append((int(self.start_nodes[e]), outgoing_at_end[e]))

            for from_node, out_vec_in in edge_data:
                for to_node, out_vec_out in edge_data:
                    # turn_angle = arccos( -dot(out_vec_in, out_vec_out) )
                    # 0° = straight through, 180° = U-turn.
                    cos_angle = float(-np.dot(out_vec_in, out_vec_out))
                    if cos_angle < -1.0:
                        cos_angle = -1.0
                    elif cos_angle > 1.0:
                        cos_angle = 1.0

                    if cos_angle < cos_threshold:
                        turns[(from_node, via_node, to_node)] = turn_penalty
                    elif store_zero_penalties:
                        turns[(from_node, via_node, to_node)] = 0.0

        self.turns = turns
        self.logger.log(
            'BuildTurnPenalties',
            f"{len(turns)} turn entries stored "
            f"({'all incl. zero' if store_zero_penalties else 'non-zero only'}), "
            f"threshold={turn_angle_threshold}°, penalty={turn_penalty}",
            v=1,
        )

    def ExportClusters(self, output_file: str):

        geo_sets = []
        attr = {}

        clusters = []

        for cluster_id, cluster in self.clasters_byId.items():
            geo_sets.append(self.geometry.iloc[cluster])
            clusters.append(np.full(cluster.shape[0], cluster_id))
        
        geo_array = np.concatenate(geo_sets)
        attr["clasters"] = np.concatenate(clusters  )

        gdf = gpd.GeoDataFrame(attr, geometry=geo_array, crs="EPSG:3301")
        gdf.to_feather(output_file)

    def ExportNetwork(self, output_file: str, geometry_type: str = "geometry", keep_z: bool = False, connected: bool = False, export_filtered_edges: bool = False):
        
        """
        DESCRIPTION:
            Exports the network topology to a file with different geometry representations.
        
        PARAMETERS:
            output_file: str - path to the output file (e.g., "network.feather")
            geometry_type: str - type of geometry to export:
                "geometry" - original edge geometries with attributes
                "links" - start and end points as separate point geometries
            keep_z: bool - whether to keep the Z coordinate in the exported geometries
            connected: bool - whether to export only edges that are part of the connected network (edges with max_degree > 1)
            export_filtered_edges: bool - if True and mask exists, export only edges where mask=True
            
        """
        
        edge_mask = self.max_degrees > 1 if connected else np.ones(self.max_degrees.shape[0], dtype=bool)

        # Apply filtered edges mask if requested
        if export_filtered_edges and self.mask is not None:
            edge_mask = edge_mask & self.mask
            self.logger.log('ExportNetwork', f"Exporting {np.sum(edge_mask)} filtered edges out of {len(self.mask)} total edges", v=1)

        if self.mask is not None:
            mask = self.mask[edge_mask]

        geometry = self.geometry[edge_mask]
        weights = self.weights[edge_mask]
        lengths = self.lengths[edge_mask]
        start_nodes = self.start_nodes[edge_mask]
        end_nodes = self.end_nodes[edge_mask]
        start_degrees = self.start_degrees[edge_mask]
        end_degrees = self.end_degrees[edge_mask]
        min_degrees = self.min_degrees[edge_mask]
        max_degrees = self.max_degrees[edge_mask]
        AB_weights = self.AB_weights[edge_mask] if self.AB_weights is not None else None
        BA_weights = self.BA_weights[edge_mask] if self.BA_weights is not None else None

        edge_flow = None
        if self.edge_flow is not None:
            edge_flow = self.edge_flow[edge_mask]


        if geometry_type == "geometry":
            # Export original edge geometries with topology attributes
            attr = {
                "weight": weights,
                "length": lengths,
                "_node_start_id": start_nodes,
                "_node_end_id": end_nodes,
                "start_degree": start_degrees,
                "end_degree": end_degrees,
                "min_degree": min_degrees,
                "max_degree": max_degrees
            }

            if edge_flow is not None:
                attr["edge_flow"] = edge_flow

            if self.mask is not None:
                attr["mask"] = mask
            
            # Add optional directional weights if available
            if AB_weights is not None:
                attr["AB_weight"] = AB_weights
            if BA_weights is not None:
                attr["BA_weight"] = BA_weights
            
            # Add optional Z coordinates if available and requested
            if keep_z and self.z is not None:
                attr["z_start"] = self.z[start_nodes]
                attr["z_end"] = self.z[end_nodes]
            
            gdf = gpd.GeoDataFrame(attr, geometry=geometry, crs="EPSG:3301")

            
        elif geometry_type == "links":
            # Export start and end points as separate point geometries
            # Create geometries for start and end points
            if keep_z and self.z is not None:
                start_points = [shapely.Point(xy) for xy in self.node_points[start_nodes]]
                end_points = [shapely.Point(xy) for xy in self.node_points[end_nodes]]
            else:
                start_points = [shapely.Point(xy) for xy in self.node_points[start_nodes, :2]]
                end_points = [shapely.Point(xy) for xy in self.node_points[end_nodes, :2]]
            
            # Build base attributes
            start_attr = {
                "weight": weights,
                "length": lengths,
                "node_id": start_nodes,
                "node_degree": start_degrees,
                "min_degree": min_degrees,
                "point_type": "start"
            }
            
            end_attr = {
                "weight": weights,
                "length": lengths,
                "node_id": end_nodes,
                "node_degree": end_degrees,
                "min_degree": min_degrees,
                "point_type": "end"
            }
            
            # Add optional directional weights if available
            if AB_weights is not None:
                start_attr["AB_weight"] = AB_weights
            if BA_weights is not None:
                start_attr["BA_weight"] = BA_weights
            if AB_weights is not None:
                end_attr["AB_weight"] = AB_weights
            if BA_weights is not None:
                end_attr["BA_weight"] = BA_weights
            
            # Create separate GeoDataFrames for start and end points
            start_gdf = gpd.GeoDataFrame(
                start_attr,
                geometry=start_points,
                crs="EPSG:3301"
            )
            
            end_gdf = gpd.GeoDataFrame(
                end_attr,
                geometry=end_points,
                crs="EPSG:3301"
            )
            
            gdf = pd.concat([start_gdf, end_gdf], ignore_index=True)
        else:
            raise ValueError(f"Unknown geometry_type: {geometry_type}. Must be 'geometry' or 'links'.")
        
        # Determine output format from file extension
        _, file_extension = os.path.splitext(output_file)
        file_extension = file_extension.lower()
        
        if file_extension == '.feather':
            gdf.to_feather(output_file)
        elif file_extension == '.parquet':
            gdf.to_parquet(output_file)
        elif file_extension == '.geojson' or file_extension == '.json':
            gdf.to_file(output_file, driver='GeoJSON')
        else:
            # Default to feather format
            gdf.to_feather(output_file)
        
        self.logger.log('export network', f"Network exported to {output_file} with geometry_type='{geometry_type}'", v=1)

    #### PRIIVATE HELPER FUNCTIONS FOR BUILDING TOPOLOGY ###

    @classmethod 
    def _create_edge_list_dict(
        cls,
        geometry:column_type=None,
        weight:np.ndarray = None,
        start_points:np.ndarray= None,
        end_points:np.ndarray = None,
        source_idx:np.ndarray = None, 
        discard_redundant_edges:bool = False,
        ) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:

        """

        DESCRIPTION: 
        ----------- 

            This function extracts edge start/end points and nodes from input geometries, and compiles them into a dictionary format suitable for initializing a GDFGraphElementList representing edges. It can also identify and discard redundant edges (parallel edges between the same node pairs) based on weights.
            
        PARAMETERS
        ----------

            cls: Class object (the @classmethod receiver). Usually GDFTopologyManager or a subclass — used to call other class helpers (e.g. _edge_start_and_end_points, _edge_start_and_end_nodes, _get_unique_edges).

            geometry: column_type | None: Array-like of edge geometries (GeoSeries or ndarray of LineString/MultiLineString). If a GeoSeries is passed the method converts it to a numpy array; if None, start_points/end_points must be supplied.

            weight: np.ndarray | None: 1-D numeric array of per-edge costs/weights aligned with geometry. Used to break ties / choose shortest when discard_redundant_edges=True. May be None if caller computed lengths earlier.

            start_points: np.ndarray | None: Optional precomputed array of edge start coordinates (shape: n_edges x 2 or x3). If provided, the method skips extracting start coordinates from geometry.

            end_points: np.ndarray | None: Optional precomputed array of edge end coordinates (same shape constraints as start_points). If provided, extraction from geometry is skipped.

            source_idx: np.ndarray | None: Array of original indices/IDs for each input geometry (e.g., gdf.index.values or a selected idxs array). Carried through into returned edge dict so results can be mapped back to the source layer.

            discard_redundant_edges: bool: If True, the method identifies redundant/parallel edges (same node pair, both directions) and retains a unique set (keeps the edge with minimal weight). Requires weight to choose which to keep.

            
        INTERNAL METHODS        
        ----------------
        
            _edge_start_and_end_points() collects start and end coordinates from input geometries.
            _edge_start_and_end_nodes() prepares topological node indices from start/end coordinates.
        
        """


        ## TO ensure partial output of this function can be used as input during clusterin, inputs to this function must be strictly arrays (Bo geoseries, pd.Series) 
        if (start_points is None) or (end_points is None):
            start_points, end_points = cls._edge_start_and_end_points(
                poly_line_data = geometry.to_numpy() if not isinstance(geometry, np.ndarray) else geometry
            )

        node_points, start_nodes, end_nodes, degrees = \
            cls._edge_start_and_end_nodes(start_points, end_points)
        
        # TODO: Make sure these values are minimal ndarrays, not GeoSeries. 
        if discard_redundant_edges:
            unique_edges = cls._get_unique_edges(start_nodes, end_nodes, weight)
            #cls.logger.warn(f"{start_nodes.shape[0] - unique_edges.shape[0]} redundant edges dropped")
            return dict(
                source_idx = source_idx[unique_edges], 
                start_points = start_points[unique_edges], 
                end_points = end_points[unique_edges],
                start_nodes = start_nodes[unique_edges], 
                end_nodes = end_nodes[unique_edges],
                weight = weight[unique_edges],
                source_geometry = geometry[unique_edges]
            ), dict(node_points=node_points, degrees=degrees)
        else:
            return dict(
                source_idx = source_idx, 
                start_points = start_points, 
                end_points = end_points,
                start_nodes = start_nodes, 
                end_nodes = end_nodes,
                weight = weight,
                source_geometry = geometry
            ), dict(node_points=node_points, degrees=degrees)

    @staticmethod
    def _edge_start_and_end_points(poly_line_data, precision: int = 4):
        
        # if we do not use return_index=True we get much cleaner coords, but we need index to find start and end points.

        # FIX (v2.3): original code tested only the FIRST geometry for Z;
        # if the first edge happens to be flat (2D), Z values on later edges
        # were silently discarded — defeating the elevation penalty for the
        # whole network.  Check ALL geometries; if any has Z, include_z=True
        # (flat edges will get z=0 which is correct).
        if np.asarray(shapely.has_z(poly_line_data)).any():
            coords, index =  shapely.get_coordinates(poly_line_data, return_index=True, include_z = True)
        else:
            coords, index =  shapely.get_coordinates(poly_line_data, return_index=True)
        
        ## first-point index
        index = np.nonzero(np.r_[1, np.diff(index)[:-1]])[0]  ## This finds the first occurance. it works but I don't understand how ()stackoverflow.com/questions/432112
        network_line_starts = coords[index]

        ## Last-point index
        index = np.roll(index - 1, -1)
        index[-1] = coords.shape[0]-1

        network_line_ends = coords[index]

        if precision is not None:
            network_line_starts = np.round(network_line_starts, decimals=precision)
            network_line_ends = np.round(network_line_ends, decimals=precision)

        return network_line_starts, network_line_ends


    @staticmethod
    def _edge_start_and_end_nodes(start_points, end_points):
        
        raise NotImplementedError("This function will be deprecated. Use _edge_start_and_end_nodes_2d instead for 2D networks or as a template for implementing this function with 3D support and precision handling.")

        """
        DESCRIPTION:
        -----------

            This function processes edge start and end coordinates to identify unique nodes, map edges to these nodes, and calculate node degrees.

        PARAMETERS:
        ---------- 

            start_points: np.ndarray: 2-D numeric array of shape (n_edges, 2 or 3) containing the start coordinates of each edge.
            end_points: np.ndarray: 2-D numeric array of shape (n_edges, 2 or 3) containing the end coordinates of each edge.

            they share indexing, i.e. start_points[i] and end_points[i] are the start and end coordinates of edge i.
            

        RETURNS:
        -------

            node_points: np.ndarray: 2-D numeric array of shape (n_nodes, 2 or 3) containing the unique node coordinates derived from the edge start and end points. 

            edge_start_node: np.ndarray: 1-D int32 array of length n_edges, where edge_start_node[i] is the index of the start node for edge i in node_points.

            edge_end_node: np.ndarray: 1-D int32 array of length n_edges, where edge_end_node[i] is the index of the end node for edge i in node_points.

            degrees: np.ndarray: 1-D int32 array of length n_nodes, where degrees[j] is the degree (number of connected edges) of node j.
        
        """
        
        
        point_xy = np.vstack((start_points, end_points))
        del start_points, end_points

        # the point_xy input is a n by 2 matrix. where the start points are the first half, and end point  of an edge are the second half..

        # input setup
        point_count = point_xy.shape[0]
        edge_count = int(point_count / 2)

        # Produces an int32 array of length 2 * edge_count: [0,0,...(edge_count times), 1,1,...(edge_count times)].
        # Purpose: label each stacked point as a start (0) or end (1).
        point_types = np.repeat(np.array([0, 1], dtype=np.int32), repeats=edge_count)

        # Produces int32 edge ids [0, 1, ..., edge_count-1]. One id per input edge.
        edge_ids = np.arange(edge_count, dtype=np.int32)

        # Concatenates the edge id array twice → length 2 * edge_count: [0,1,...,edge_count-1, 0,1,...,edge_count-1].
        # Purpose: after stacking start and end coordinates with np.vstack((start_points, end_points)), this maps each point (start then end) back to the original edge index.
        point_segment_ids = np.hstack((edge_ids, edge_ids))


        # sort by x then by y ## np.lexsort is not supported with njit sorting makes it faster to find uniques.
        sorter = np.lexsort((point_xy[:, 1], point_xy[:, 0]), axis=0)
        sorted_point_xy = point_xy[sorter, :]
        del point_xy

        # Resorting inputs:
        sorted_point_types = point_types[sorter]
        del point_types

        sorted_point_segment_ids = point_segment_ids[sorter]
        del point_segment_ids, sorter

        # finding unique points (which are from now on, refereed to as nodes.)
        node_points, _, node_idxs, degrees = np.unique(
            sorted_point_xy,
            axis=0,
            return_index=True,
            return_inverse=True,
            return_counts=True,
        )

        if np.__version__ == '2.0.0':
            node_idxs = node_idxs.reshape(-1) # fix bug in numpy 2.0.0 giving wrong dimension

        # edge data
        edge_start_node = np.zeros(edge_count, dtype=np.int32)
        edge_end_node = np.zeros(edge_count, dtype=np.int32)

        stsrt_node_filter = (sorted_point_types == 0)
        end_node_filter = (sorted_point_types == 1)
        del sorted_point_types
        
        edge_start_node[sorted_point_segment_ids[stsrt_node_filter]] = node_idxs[stsrt_node_filter]
        edge_end_node[sorted_point_segment_ids[end_node_filter]] = node_idxs[end_node_filter]

        return node_points, edge_start_node, edge_end_node, degrees

    @staticmethod
    def _edge_start_and_end_nodes_2d(start_points, end_points):
        
        """
        DESCRIPTION:
        -----------

            Processes edge start and end coordinates to identify unique nodes, map edges to
            those nodes, and compute node degrees. Deduplication is 2D-only (Z ignored) so
            that floating-point Z variation never splits nodes that share the same XY.
            Coordinate rounding is expected to have been applied upstream by
            _edge_start_and_end_points; no rounding is performed here.

        PARAMETERS:
        ----------

            start_points: np.ndarray - shape (n_edges, 2) or (n_edges, 3). Start coordinate
                          of each edge. Index i corresponds to edge i.
            end_points:   np.ndarray - same shape. End coordinate of each edge.

        RETURNS:
        -------

            node_points:       np.ndarray, shape (n_nodes, 2) or (n_nodes, 3). Unique node
                               coordinates. Z preserved from first occurrence if present.
            edge_start_node:   np.ndarray, int32, length n_edges. edge_start_node[i] is the
                               index into node_points for the start node of edge i.
            edge_end_node:     np.ndarray, int32, length n_edges. Same for end nodes.
            node_degrees:      np.ndarray, int32, length n_nodes. Number of edges connected
                               to each node.
        
        """

        edge_count = start_points.shape[0]
        has_z = start_points.shape[1] > 2

        # Stack [start_0, ..., start_n-1, end_0, ..., end_n-1] using only XY for dedup.
        # Stacking order guarantees inverse_indices[:edge_count] = start nodes,
        # inverse_indices[edge_count:] = end nodes — no auxiliary mapping arrays needed.
        all_points_2d = np.vstack((start_points[:, :2], end_points[:, :2]))

        # np.unique return_inverse maps every row in all_points_2d to its unique-node index.
        unique_points_2d, unique_idx, inverse_indices, degrees = np.unique(
            all_points_2d,
            axis=0,
            return_index=True,
            return_inverse=True,
            return_counts=True,
        )

        if inverse_indices.ndim > 1:
            inverse_indices = inverse_indices.reshape(-1)

        # Direct slice: no scatter assignment needed.
        edge_to_start_node = inverse_indices[:edge_count].astype(np.int32)
        edge_to_end_node   = inverse_indices[edge_count:].astype(np.int32)

        # Preserve Z for unique nodes using first-occurrence index from np.unique.
        if has_z:
            all_z = np.hstack((start_points[:, 2], end_points[:, 2]))
            unique_points = np.column_stack((unique_points_2d, all_z[unique_idx]))
        else:
            unique_points = unique_points_2d

        return unique_points, edge_to_start_node, edge_to_end_node, degrees

    @staticmethod
    def _get_unique_edges(start_nodes, end_nodes, weights):

        # (N,2) endpoints
        edges = np.column_stack((start_nodes, end_nodes))

        # normalize so (A,B) == (B,A)
        edges_norm = np.sort(edges, axis=1)

        # build a (N,3) key: [u, v, w]
        keys = np.column_stack((edges_norm, weights))

        # unique rows by (u, v, w), keep first occurrence
        _, keep_idx = np.unique(keys, axis=0, return_index=True)

        # optional: keep original order
        keep_idx = np.sort(keep_idx)

        return keep_idx

    @staticmethod
    def _calculate_edge_node_degrees(start_nodes, end_nodes, node_degrees):
        """
        Calculate node degrees for each edge endpoint.

        DESCRIPTION:
            Maps node degrees to edge start and end points. For each edge, retrieves the degree 
            (connectivity count) at its start node, end node, and calculates the minimum.

        PARAMETERS:
            start_nodes: np.ndarray - 1-D int array of start node indices (length n_edges)
            end_nodes: np.ndarray - 1-D int array of end node indices (length n_edges)
            node_degrees: np.ndarray - 1-D int array of degree for each node (length n_nodes)

        RETURNS:
            dict with three keys (all shape n_edges):
                'start_degree': np.ndarray - degree at start node for each edge
                'end_degree': np.ndarray - degree at end node for each edge
                'min_degree': np.ndarray - minimum of start and end degree for each edge

        EXAMPLE:
            result = _calculate_edge_node_degrees(start_nodes, end_nodes, degrees)
            edge_start_deg = result['start_degree']  # degrees[start_nodes[i]]
            edge_end_deg = result['end_degree']      # degrees[end_nodes[i]]
            edge_min_deg = result['min_degree']      # min(start, end) for each edge
        """
        
        # Map node degrees to edges
        start_degree = node_degrees[start_nodes]
        end_degree = node_degrees[end_nodes]
        
        # Calculate minimum degree per edge
        min_degree = np.minimum(start_degree, end_degree)
        max_degree = np.maximum(start_degree, end_degree)


        return {
            'start_degree': start_degree,
            'end_degree': end_degree,
            'min_degree': min_degree,
            'max_degree': max_degree
        }

    @staticmethod
    def _calculate_edge_weight(base_weight, elev_from, elev_to, elevation_coeficent: float):
        """
        Calculate edge weight with elevation-based penalties.

        DESCRIPTION:
            Applies elevation-based cost adjustment to account for uphill travel difficulty.

        PARAMETERS:
            base_weight: float - Base weight/cost of the edge (e.g., geometric length)
            elev_from: float - Starting elevation of the edge
            elev_to: float - Ending elevation of the edge
            settings: object - Settings object (optional, for future enhancement with elevation penalty parameters)

        RETURNS:
            float - Adjusted weight accounting for elevation changes

        LOGIC:
            - Positive elevation change (uphill): Adds penalty proportional to elevation gain
              Conversion ratio: 1 meter elevation gain ≈ 4 (default) meters horizontal equivalent distance
            - Negative elevation change (downhill): No adjustment
            - Flat terrain: Returns base_weight unchanged
        """
        elev_diff = elev_to - elev_from

        if elev_diff > 0:
            # Uphill penalty: add equivalent horizontal distance cost
            elevation_penalty = elevation_coeficent * elev_diff 
            adjusted_weight = base_weight + elevation_penalty
        elif elev_diff < 0:
            adjusted_weight = base_weight
        else:
            # Flat terrain: no adjustment
            adjusted_weight = base_weight

        return adjusted_weight

class AccessPoints:

    """
    DESCRIPTION:

        this function is mapping of pointset.

    """

    num_threads : int = mp.cpu_count() - 1 if mp.cpu_count() > 1 else 1

    geometry: gpd.GeoSeries = None
    node_weight: np.ndarray = None
    uid: np.ndarray = None

    nearest_edge_point: gpd.array.GeometryArray = None # this is the point on the edge where the access point is projected to. 
    
    nearest_edge_id: np.ndarray = None

    edge_start_node: np.ndarray = None
    edge_end_node: np.ndarray = None

    weight_to_start: np.ndarray = None
    weight_to_end: np.ndarray = None

    clasters : np.ndarray = None
    clasters_byId : Dict[int, np.ndarray] = None

    raw: gpd.GeoSeries = None    # it is a copy of weights. ??!!

    def __init__(self, logger ):

        self.logger = logger
        self.geometry: gpd.GeoSeries = None
        self.node_weight: np.ndarray = None

        self.nearest_edge_point: gpd.array.GeometryArray = None # this is the point on the edge where the access point is projected to. 
        
        self.nearest_edge_id: np.ndarray = None

        self.edge_start_node: np.ndarray = None
        self.edge_end_node: np.ndarray = None

        self.weight_to_start: np.ndarray = None
        self.weight_to_end: np.ndarray = None

        self.clasters_byId = {}

    @property
    def xy(self) -> np.ndarray:
        
        # return np.column_stack((self.geometry.x, self.geometry.y))
        return np.column_stack((self.nearest_edge_point.x, self.nearest_edge_point.y))
        
    def ExportConnections(self, output_file: str):

        attr = {
            "node_weight": self.node_weight,
            "nearest_edge_id": self.nearest_edge_id,
            "edge_start_node": self.edge_start_node,
            "edge_end_node": self.edge_end_node,
            "weight_to_start": self.weight_to_start,
            "weight_to_end": self.weight_to_end
        }


        lines = [LineString([origin, point]) for origin, point in zip(self.geometry, self.nearest_edge_point)]
        gdf = gpd.GeoDataFrame(attr, geometry=lines, crs="EPSG:3301")

        gdf.to_feather( output_file)

        # gdf=gpd.GeoDataFrame({ "weight_to_start": self.weight_to_start, "weight_to_end": self.weight_to_end}, geometry= self.geometry, crs="EPSG:3301")
        # gdf.to_file( output_file, driver='GeoJSON')

        self.logger.log('export access points', f"Access points exported to {output_file}", v=1)

    def ExportClusters(self, output_file: str):

        pts_sets = []
        attr = {}

        clusters = []

        for cluster_id, cluster in self.clasters_byId.items():
            pts_sets.append(self.geometry.iloc[cluster])
            clusters.append(np.full(cluster.shape[0], cluster_id))

        pts_array = np.concatenate(pts_sets)
        attr["clusters"] = np.concatenate(clusters)

        gdf = gpd.GeoDataFrame(attr, geometry=pts_array, crs="EPSG:3301")
        gdf.to_feather(output_file)


    # ——————————————————————————————————————————————————————————————————————
    # v2.1 ADDITIONS (appended at module bottom to avoid touching long class bodies)
    # ——————————————————————————————————————————————————————————————————————

    def _topology_to_pickle(self, path: str) -> None:
        """
        Serialise the entire Topology (network arrays, origins, destinations,
        clusters, spatial indexes) to disk via pickle.

        The Logger object is non-picklable on some setups; we drop it before
        saving and restore a fresh one on load.
        """
        import pickle, copy
        snap = copy.copy(self)             # shallow — we don't deep-copy arrays
        snap.logger = None
        if snap.network is not None:
            snap.network = copy.copy(snap.network)
            snap.network.logger = None
        if snap.origins is not None:
            snap.origins = copy.copy(snap.origins)
            snap.origins.logger = None
        if snap.destinations is not None:
            snap.destinations = copy.copy(snap.destinations)
            snap.destinations.logger = None
        with open(path, "wb") as fh:
            pickle.dump(snap, fh, protocol=pickle.HIGHEST_PROTOCOL)


    @classmethod
    def _topology_from_pickle(cls, path: str, verbosity: int = 1):
        """
        Load a Topology previously saved via `to_pickle`. Re-attaches a fresh
        Logger so logging continues to work.
        """
        import pickle
        with open(path, "rb") as fh:
            topo = pickle.load(fh)
        # Re-attach a fresh logger
        topo.logger = Logger(verbosity=verbosity)
        if getattr(topo, "network", None) is not None:
            topo.network.logger = topo.logger
        if getattr(topo, "origins", None) is not None:
            topo.origins.logger = topo.logger
        if getattr(topo, "destinations", None) is not None:
            topo.destinations.logger = topo.logger
        return topo


    Topology.to_pickle = _topology_to_pickle
    Topology.from_pickle = _topology_from_pickle
