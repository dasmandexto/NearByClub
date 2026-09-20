"""
Vector store — keeps recent GPS history per user in Redis.
Computes movement vectors and triggers crossing detection.
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional
import redis.asyncio as aioredis
from dotenv import load_dotenv

from engine.vector_engine import (
    UserVector, PredictedCrossing,
    compute_vector, find_all_crossings
)

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
GPS_HISTORY_SIZE = 5          # keep last N GPS points per user
GPS_HISTORY_TTL  = 300        # seconds — discard stale GPS history
ACTIVE_USER_TTL  = 120        # seconds — user considered "active" (moving)
MIN_SPEED_MPS    = 0.5        # below this = stationary, don't predict


class VectorStore:
    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self):
        self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    async def close(self):
        if self._redis:
            await self._redis.aclose()

    # ── GPS history ────────────────────────────────────────────────────────────

    async def push_location(self, user_id: int, lat: float, lon: float) -> Optional[UserVector]:
        """
        Append a GPS point to user history.
        Returns a UserVector if the user has enough history to compute direction.
        """
        key = f"gps:{user_id}"
        point = json.dumps({
            "lat": lat, "lon": lon,
            "ts": datetime.utcnow().isoformat()
        })

        pipe = self._redis.pipeline()
        pipe.rpush(key, point)
        pipe.ltrim(key, -GPS_HISTORY_SIZE, -1)
        pipe.expire(key, GPS_HISTORY_TTL)
        await pipe.execute()

        return await self._build_vector(user_id)

    async def _build_vector(self, user_id: int) -> Optional[UserVector]:
        key = f"gps:{user_id}"
        raw_points = await self._redis.lrange(key, 0, -1)

        if len(raw_points) < 2:
            return None

        positions = []
        for p in raw_points:
            d = json.loads(p)
            positions.append((d["lat"], d["lon"], datetime.fromisoformat(d["ts"])))

        result = compute_vector(positions)
        if not result:
            return None

        dlat, dlon, speed, heading = result
        if speed < MIN_SPEED_MPS:
            return None  # user is stationary

        lat, lon, _ = positions[-1]
        return UserVector(
            user_id=user_id,
            lat=lat,
            lon=lon,
            dlat_per_sec=dlat,
            dlon_per_sec=dlon,
            speed_mps=round(speed, 2),
            heading_deg=round(heading, 1),
            recorded_at=positions[-1][2]
        )

    # ── Active vector registry ─────────────────────────────────────────────────

    async def register_vector(self, vec: UserVector):
        """Store computed vector for a user so others can query against it."""
        key = f"vec:{vec.user_id}"
        data = json.dumps({
            "user_id": vec.user_id,
            "lat": vec.lat,
            "lon": vec.lon,
            "dlat": vec.dlat_per_sec,
            "dlon": vec.dlon_per_sec,
            "speed": vec.speed_mps,
            "heading": vec.heading_deg,
            "ts": vec.recorded_at.isoformat()
        })
        await self._redis.set(key, data, ex=ACTIVE_USER_TTL)
        # Add to active set (for scan)
        await self._redis.sadd("active_vectors", str(vec.user_id))
        await self._redis.expire("active_vectors", ACTIVE_USER_TTL)

    async def get_all_active_vectors(self, exclude_user_id: int) -> list[UserVector]:
        """Fetch all currently active user vectors."""
        member_ids = await self._redis.smembers("active_vectors")
        vectors = []
        for uid_str in member_ids:
            uid = int(uid_str)
            if uid == exclude_user_id:
                continue
            raw = await self._redis.get(f"vec:{uid}")
            if raw:
                d = json.loads(raw)
                vectors.append(UserVector(
                    user_id=d["user_id"],
                    lat=d["lat"], lon=d["lon"],
                    dlat_per_sec=d["dlat"], dlon_per_sec=d["dlon"],
                    speed_mps=d["speed"], heading_deg=d["heading"],
                    recorded_at=datetime.fromisoformat(d["ts"])
                ))
        return vectors

    # ── Main: update location and find crossings ───────────────────────────────

    async def update_and_predict(
        self,
        user_id: int,
        lat: float,
        lon: float
    ) -> list[PredictedCrossing]:
        """
        Full pipeline:
        1. Push GPS point
        2. Build vector
        3. Register vector
        4. Find crossings against all other active vectors
        Returns list of predicted crossings (may be empty).
        """
        vec = await self.push_location(user_id, lat, lon)
        if not vec:
            return []

        await self.register_vector(vec)
        pool = await self.get_all_active_vectors(exclude_user_id=user_id)

        if not pool:
            return []

        return find_all_crossings(vec, pool)


# Singleton
vector_store = VectorStore()
