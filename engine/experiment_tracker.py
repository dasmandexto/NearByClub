"""
Experiment tracker — measures whether a user changed direction
after receiving a crossing alert.

Methodology:
  1. When crossing alert is sent → snapshot user's current vector (heading, speed)
  2. Poll user's vector 30s, 60s, 120s after alert
  3. Compare heading delta → classify as APPROACH, AVOID, or NO_CHANGE
  4. Store result in DB for analysis
"""
import math
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from db.models import async_session
from sqlalchemy import Column, Integer, Float, String, DateTime, BigInteger
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger("ExperimentTracker")


# ── Behaviour classification ───────────────────────────────────────────────────

class BehaviourLabel(str, Enum):
    APPROACH   = "APPROACH"    # heading changed toward crossing point
    AVOID      = "AVOID"       # heading changed away from crossing point
    NO_CHANGE  = "NO_CHANGE"   # heading delta < threshold
    LOST       = "LOST"        # user stopped moving / GPS lost


HEADING_CHANGE_THRESHOLD_DEG = 15   # delta below this = NO_CHANGE
SPEED_DROP_THRESHOLD_MPS     = 0.4  # if speed drops a lot = slowed down (sub-label)


@dataclass
class HeadingSnapshot:
    user_id: int
    heading_deg: float
    speed_mps: float
    lat: float
    lon: float
    taken_at: datetime


@dataclass
class ExperimentResult:
    user_id: int
    crossing_user_b_id: int
    experiment_group: str          # A / B / C
    heading_before: float
    heading_after_30s: Optional[float]
    heading_after_60s: Optional[float]
    heading_after_120s: Optional[float]
    behaviour_30s: Optional[BehaviourLabel]
    behaviour_60s: Optional[BehaviourLabel]
    crossing_lat: float
    crossing_lon: float
    eta_seconds: int
    confidence: float
    alert_sent_at: datetime


# ── Heading delta helper ───────────────────────────────────────────────────────

def heading_delta(h1: float, h2: float) -> float:
    """Smallest signed angle between two headings (−180 to +180)."""
    delta = (h2 - h1 + 180) % 360 - 180
    return abs(delta)


def bearing_to_point(from_lat: float, from_lon: float,
                     to_lat: float, to_lon: float) -> float:
    """Compass bearing from one point to another."""
    y = math.sin(math.radians(to_lon - from_lon)) * math.cos(math.radians(to_lat))
    x = (math.cos(math.radians(from_lat)) * math.sin(math.radians(to_lat))
         - math.sin(math.radians(from_lat)) * math.cos(math.radians(to_lat))
         * math.cos(math.radians(to_lon - from_lon)))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def classify_behaviour(
    heading_before: float,
    heading_after: float,
    speed_after: float,
    crossing_lat: float,
    crossing_lon: float,
    user_lat: float,
    user_lon: float,
) -> BehaviourLabel:
    """
    Compare heading before/after the alert.
    Also checks if new heading moves toward or away from the predicted crossing point.
    """
    delta = heading_delta(heading_before, heading_after)

    if delta < HEADING_CHANGE_THRESHOLD_DEG:
        return BehaviourLabel.NO_CHANGE

    bearing_to_cross = bearing_to_point(user_lat, user_lon, crossing_lat, crossing_lon)

    delta_before_to_cross = heading_delta(heading_before, bearing_to_cross)
    delta_after_to_cross  = heading_delta(heading_after,  bearing_to_cross)

    if delta_after_to_cross < delta_before_to_cross:
        return BehaviourLabel.APPROACH  # new heading is closer to crossing point
    else:
        return BehaviourLabel.AVOID


# ── Tracker ────────────────────────────────────────────────────────────────────

class ExperimentTracker:

    def __init__(self, vector_store):
        self._store = vector_store
        self._results: list[ExperimentResult] = []   # in-memory; flush to DB periodically

    async def on_crossing_alert_sent(
        self,
        user_id: int,
        crossing_user_b_id: int,
        experiment_group: str,
        crossing_lat: float,
        crossing_lon: float,
        eta_seconds: int,
        confidence: float,
    ):
        """
        Called immediately after a crossing alert is pushed to the client.
        Schedules follow-up measurements at +30s, +60s, +120s.
        """
        # Snapshot current vector
        snapshot_before = await self._snapshot(user_id)
        if not snapshot_before:
            logger.debug(f"[Experiment] No vector for user {user_id} at alert time")
            return

        result = ExperimentResult(
            user_id=user_id,
            crossing_user_b_id=crossing_user_b_id,
            experiment_group=experiment_group,
            heading_before=snapshot_before.heading_deg,
            heading_after_30s=None,
            heading_after_60s=None,
            heading_after_120s=None,
            behaviour_30s=None,
            behaviour_60s=None,
            crossing_lat=crossing_lat,
            crossing_lon=crossing_lon,
            eta_seconds=eta_seconds,
            confidence=confidence,
            alert_sent_at=datetime.utcnow(),
        )

        asyncio.create_task(self._measure_followups(result, snapshot_before))

    async def _snapshot(self, user_id: int) -> Optional[HeadingSnapshot]:
        """Grab current vector from Redis store."""
        vecs = await self._store.get_all_active_vectors(exclude_user_id=-1)
        for v in vecs:
            if v.user_id == user_id:
                return HeadingSnapshot(
                    user_id=user_id,
                    heading_deg=v.heading_deg,
                    speed_mps=v.speed_mps,
                    lat=v.lat,
                    lon=v.lon,
                    taken_at=datetime.utcnow(),
                )
        return None

    async def _measure_followups(
        self,
        result: ExperimentResult,
        before: HeadingSnapshot,
    ):
        """Measure heading at +30s and +60s after alert."""
        for delay, label in [(30, "30s"), (60, "60s")]:
            await asyncio.sleep(delay)
            snap = await self._snapshot(result.user_id)

            if not snap or snap.speed_mps < 0.3:
                behaviour = BehaviourLabel.LOST
                heading = None
            else:
                heading = snap.heading_deg
                behaviour = classify_behaviour(
                    heading_before=before.heading_deg,
                    heading_after=heading,
                    speed_after=snap.speed_mps,
                    crossing_lat=result.crossing_lat,
                    crossing_lon=result.crossing_lon,
                    user_lat=snap.lat,
                    user_lon=snap.lon,
                )

            if label == "30s":
                result.heading_after_30s = heading
                result.behaviour_30s = behaviour
            else:
                result.heading_after_60s = heading
                result.behaviour_60s = behaviour

            logger.info(
                f"[Experiment] user={result.user_id} group={result.experiment_group} "
                f"t=+{label} heading_before={before.heading_deg:.1f}° "
                f"heading_after={heading} → {behaviour}"
            )

        self._results.append(result)
        await self._flush_if_needed()

    async def _flush_if_needed(self):
        """Flush results to DB every 20 records."""
        if len(self._results) >= 20:
            await self.flush_to_db()

    async def flush_to_db(self):
        """Write accumulated results to PostgreSQL."""
        if not self._results:
            return

        batch = self._results[:]
        self._results.clear()

        async with async_session() as session:
            for r in batch:
                row = ExperimentRow(
                    user_id=r.user_id,
                    crossing_user_b_id=r.crossing_user_b_id,
                    experiment_group=r.experiment_group,
                    heading_before=r.heading_before,
                    heading_after_30s=r.heading_after_30s,
                    heading_after_60s=r.heading_after_60s,
                    behaviour_30s=r.behaviour_30s.value if r.behaviour_30s else None,
                    behaviour_60s=r.behaviour_60s.value if r.behaviour_60s else None,
                    crossing_lat=r.crossing_lat,
                    crossing_lon=r.crossing_lon,
                    eta_seconds=r.eta_seconds,
                    confidence=r.confidence,
                    alert_sent_at=r.alert_sent_at,
                )
                session.add(row)
            await session.commit()

        logger.info(f"[Experiment] Flushed {len(batch)} results to DB")

    async def get_summary(self) -> dict:
        """Quick in-memory stats for dashboard."""
        groups: dict[str, dict] = {}
        for r in self._results:
            g = r.experiment_group
            if g not in groups:
                groups[g] = {"total": 0, "APPROACH": 0, "AVOID": 0, "NO_CHANGE": 0, "LOST": 0}
            groups[g]["total"] += 1
            if r.behaviour_30s:
                groups[g][r.behaviour_30s.value] += 1
        return groups


# ── DB model for results ───────────────────────────────────────────────────────

from db.models import Base

class ExperimentRow(Base):
    __tablename__ = "experiment_results"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    user_id             = Column(Integer, nullable=False, index=True)
    crossing_user_b_id  = Column(Integer, nullable=False)
    experiment_group    = Column(String(2), nullable=False)   # A / B / C
    heading_before      = Column(Float)
    heading_after_30s   = Column(Float, nullable=True)
    heading_after_60s   = Column(Float, nullable=True)
    behaviour_30s       = Column(String(16), nullable=True)
    behaviour_60s       = Column(String(16), nullable=True)
    crossing_lat        = Column(Float)
    crossing_lon        = Column(Float)
    eta_seconds         = Column(Integer)
    confidence          = Column(Float)
    alert_sent_at       = Column(DateTime)
