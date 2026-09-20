"""
Vector Intersection Engine
Predicts when two moving users will cross paths within the next N minutes.

Math:
  - Each user has a position (lat, lon) and velocity vector (dlat/s, dlon/s)
  - We compute D(t) = haversine(A(t), B(t)) for t in [0, MAX_SECONDS]
  - Find minimum D(t) — if < CROSSING_THRESHOLD_M and occurs within window → crossing predicted
"""
import math
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


# ── Constants ─────────────────────────────────────────────────────────────────
CROSSING_THRESHOLD_M = 60    # within 60m = "crossing"
MIN_PREDICT_SEC      = 60    # ignore crossings in < 1 min (already too close)
MAX_PREDICT_SEC      = 420   # predict up to 7 minutes ahead
STEP_SEC             = 5     # time resolution for simulation
EARTH_RADIUS_M       = 6_371_000


# ── Data types ────────────────────────────────────────────────────────────────
@dataclass
class UserVector:
    user_id: int
    lat: float
    lon: float
    dlat_per_sec: float   # degrees/second northward
    dlon_per_sec: float   # degrees/second eastward
    speed_mps: float      # metres/second (for display)
    heading_deg: float    # 0=North, 90=East, etc.
    recorded_at: datetime


@dataclass
class PredictedCrossing:
    user_a_id: int
    user_b_id: int
    eta_seconds: int          # seconds from now until closest approach
    min_distance_m: float     # predicted closest distance
    crossing_lat: float       # approximate crossing point
    crossing_lon: float
    confidence: float         # 0.0 – 1.0


# ── Haversine distance ─────────────────────────────────────────────────────────
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Returns distance in metres between two WGS-84 coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ── Build velocity vector from GPS history ─────────────────────────────────────
def compute_vector(positions: list[tuple[float, float, datetime]]) -> Optional[tuple[float, float, float, float]]:
    """
    Given a list of (lat, lon, timestamp) sorted oldest→newest (min 2 points),
    returns (dlat_per_sec, dlon_per_sec, speed_mps, heading_deg).
    Returns None if not enough data or user is stationary.
    """
    if len(positions) < 2:
        return None

    # Use last two positions for vector
    lat0, lon0, t0 = positions[-2]
    lat1, lon1, t1 = positions[-1]

    dt = (t1 - t0).total_seconds()
    if dt < 1:
        return None

    dlat = (lat1 - lat0) / dt
    dlon = (lon1 - lon0) / dt

    # Speed in m/s
    dist = haversine_m(lat0, lon0, lat1, lon1)
    speed = dist / dt

    # Heading
    y = math.sin(math.radians(lon1 - lon0)) * math.cos(math.radians(lat1))
    x = (math.cos(math.radians(lat0)) * math.sin(math.radians(lat1))
         - math.sin(math.radians(lat0)) * math.cos(math.radians(lat1))
         * math.cos(math.radians(lon1 - lon0)))
    heading = (math.degrees(math.atan2(y, x)) + 360) % 360

    return dlat, dlon, speed, heading


# ── Core prediction ────────────────────────────────────────────────────────────
def predict_crossing(a: UserVector, b: UserVector) -> Optional[PredictedCrossing]:
    """
    Simulate both users' future positions step by step.
    Returns a PredictedCrossing if paths will come within CROSSING_THRESHOLD_M,
    or None if no crossing is predicted within MAX_PREDICT_SEC.
    """
    min_dist = float("inf")
    min_t = -1
    min_lat = a.lat
    min_lon = a.lon

    for t in range(0, MAX_PREDICT_SEC + STEP_SEC, STEP_SEC):
        # Projected positions
        lat_a = a.lat + a.dlat_per_sec * t
        lon_a = a.lon + a.dlon_per_sec * t
        lat_b = b.lat + b.dlat_per_sec * t
        lon_b = b.lon + b.dlon_per_sec * t

        dist = haversine_m(lat_a, lon_a, lat_b, lon_b)

        if dist < min_dist:
            min_dist = dist
            min_t = t
            # Crossing point = midpoint
            min_lat = (lat_a + lat_b) / 2
            min_lon = (lon_a + lon_b) / 2

        # Early exit if paths are diverging after minimum found
        if t > 60 and dist > min_dist * 1.5:
            break

    if min_dist > CROSSING_THRESHOLD_M or min_t < MIN_PREDICT_SEC:
        return None

    # Confidence: higher when ETA is sooner and distance is smaller
    eta_score = 1.0 - (min_t / MAX_PREDICT_SEC)
    dist_score = 1.0 - (min_dist / CROSSING_THRESHOLD_M)
    confidence = round((eta_score * 0.6 + dist_score * 0.4), 2)

    return PredictedCrossing(
        user_a_id=a.user_id,
        user_b_id=b.user_id,
        eta_seconds=min_t,
        min_distance_m=round(min_dist, 1),
        crossing_lat=round(min_lat, 6),
        crossing_lon=round(min_lon, 6),
        confidence=confidence
    )


# ── Batch: find all crossings for a given user against a pool ─────────────────
def find_all_crossings(
    target: UserVector,
    pool: list[UserVector],
    max_results: int = 5
) -> list[PredictedCrossing]:
    """
    Returns predicted crossings for `target` against all users in `pool`,
    sorted by ETA (soonest first).
    """
    crossings = []
    for other in pool:
        if other.user_id == target.user_id:
            continue
        result = predict_crossing(target, other)
        if result:
            crossings.append(result)

    crossings.sort(key=lambda c: c.eta_seconds)
    return crossings[:max_results]
