import React, { useEffect, useRef, useState, useCallback } from 'react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface PredictedCrossing {
  user_b_id: number;
  eta_seconds: number;
  min_distance_m: number;
  crossing_lat: number;
  crossing_lon: number;
  confidence: number;
  // enriched by server (animal persona of other user)
  animal_emoji?: string;
  animal_name?: string;
  hint?: string; // A/B/C experiment group hint text
}

interface HeatPoint {
  lat: number;
  lon: number;
}

interface NearByState {
  nearbyCount: number;
  crossings: PredictedCrossing[];
  heatPoints: HeatPoint[];
  radiusM: number;
  connected: boolean;
  userLat: number | null;
  userLon: number | null;
}

// ── Experiment notification copy (A/B/C groups) ───────────────────────────────
// Group is assigned server-side per user; passed in crossing.hint

function getCrossingText(c: PredictedCrossing): { title: string; body: string } {
  const eta = Math.round(c.eta_seconds / 60);
  const etaStr = eta <= 1 ? 'меньше минуты' : `~${eta} мин`;

  switch (c.hint) {
    case 'B': // hint with context
      return {
        title: `${c.animal_emoji || '🔴'} Пересечение через ${etaStr}`,
        body: `Знакомый тебе человек идёт навстречу`,
      };
    case 'C': // control — no notification rendered (filtered upstream)
      return { title: '', body: '' };
    default: // Group A — minimal
      return {
        title: `${c.animal_emoji || '🔴'} ${etaStr} до пересечения`,
        body: `${Math.round(c.min_distance_m)}м — ${c.animal_name || 'кто-то'}`,
      };
  }
}

// ── Main component ────────────────────────────────────────────────────────────

const NEARBY_SERVER = process.env.NEARBY_SERVER_URL || 'http://localhost:8000';
const WS_SERVER = NEARBY_SERVER.replace(/^http/, 'ws');

const NearByPanel: React.FC<{ tgUserId: number }> = ({ tgUserId }) => {
  const [state, setState] = useState<NearByState>({
    nearbyCount: 0,
    crossings: [],
    heatPoints: [],
    radiusM: 500,
    connected: false,
    userLat: null,
    userLon: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const watchIdRef = useRef<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  // ── GPS watch ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!navigator.geolocation) return;

    watchIdRef.current = navigator.geolocation.watchPosition(
      (pos) => {
        setState(s => ({
          ...s,
          userLat: pos.coords.latitude,
          userLon: pos.coords.longitude,
        }));
        sendLocationToWS(pos.coords.latitude, pos.coords.longitude, state.radiusM);
      },
      (err) => console.warn('[NearBy] Geo error:', err),
      { enableHighAccuracy: true, maximumAge: 8000, timeout: 10000 }
    );

    return () => {
      if (watchIdRef.current !== null)
        navigator.geolocation.clearWatch(watchIdRef.current);
    };
  }, []);

  // ── WebSocket ──────────────────────────────────────────────────────────────
  const connectWS = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(`${WS_SERVER}/ws/${tgUserId}`);
    wsRef.current = ws;

    ws.onopen = () => setState(s => ({ ...s, connected: true }));
    ws.onclose = () => {
      setState(s => ({ ...s, connected: false }));
      setTimeout(connectWS, 3000);
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'heatmap_update') {
          setState(s => ({
            ...s,
            heatPoints: msg.points || [],
            nearbyCount: msg.nearby_count ?? s.nearbyCount,
          }));
        }
        if (msg.type === 'crossing_alert') {
          setState(s => ({
            ...s,
            crossings: msg.crossings || [],
          }));
        }
      } catch { /* ignore */ }
    };
  }, [tgUserId]);

  useEffect(() => { connectWS(); }, [connectWS]);

  const sendLocationToWS = (lat: number, lon: number, radiusM: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'location', lat, lon, radius_m: radiusM }));
    }
  };

  // ── Radius change ──────────────────────────────────────────────────────────
  const onRadiusChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const r = parseInt(e.target.value);
    setState(s => ({ ...s, radiusM: r }));
    if (state.userLat && state.userLon) sendLocationToWS(state.userLat, state.userLon, r);
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  const activeCrossings = state.crossings.filter(
    c => c.hint !== 'C' && !dismissed.has(c.user_b_id)
  );

  return (
    <div className="nearby-panel" style={styles.panel}>
      {/* Header */}
      <div style={styles.header} onClick={() => setCollapsed(c => !c)}>
        <span style={styles.dot(state.connected)} />
        <span style={styles.headerText}>
          {state.nearbyCount > 0
            ? `${state.nearbyCount} рядом`
            : 'NearBy'}
        </span>
        <span style={styles.chevron}>{collapsed ? '▲' : '▼'}</span>
      </div>

      {!collapsed && (
        <>
          {/* Crossing alerts */}
          {activeCrossings.length > 0 && (
            <div style={styles.crossingList}>
              {activeCrossings.map(c => {
                const { title, body } = getCrossingText(c);
                return (
                  <div key={c.user_b_id} style={styles.crossingCard}>
                    <div style={styles.crossingTitle}>{title}</div>
                    <div style={styles.crossingBody}>{body}</div>
                    <div style={styles.crossingMeta}>
                      {Math.round(c.min_distance_m)}м • {Math.round(c.confidence * 100)}% уверенность
                    </div>
                    <button
                      style={styles.dismissBtn}
                      onClick={() => setDismissed(s => new Set(s).add(c.user_b_id))}
                    >✕</button>
                  </div>
                );
              })}
            </div>
          )}

          {/* Mini heatmap dots indicator */}
          <div style={styles.heatRow}>
            {state.heatPoints.slice(0, 12).map((_, i) => (
              <span key={i} style={styles.heatDot(i, state.heatPoints.length)} />
            ))}
            {state.heatPoints.length === 0 && (
              <span style={{ opacity: 0.4, fontSize: 12 }}>никого поблизости</span>
            )}
          </div>

          {/* Radius slider */}
          <div style={styles.sliderRow}>
            <span style={styles.sliderLabel}>📍 {state.radiusM}м</span>
            <input
              type="range"
              min={100} max={1000} step={50}
              value={state.radiusM}
              onChange={onRadiusChange}
              style={styles.slider}
            />
          </div>
        </>
      )}
    </div>
  );
};

// ── Inline styles (matches Telegram dark theme) ───────────────────────────────

const styles = {
  panel: {
    position: 'fixed' as const,
    bottom: 72,
    right: 12,
    width: 240,
    background: 'rgba(23, 33, 43, 0.95)',
    backdropFilter: 'blur(12px)',
    borderRadius: 16,
    border: '1px solid rgba(255,255,255,0.07)',
    color: '#fff',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    fontSize: 13,
    zIndex: 9999,
    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
    overflow: 'hidden' as const,
    userSelect: 'none' as const,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '10px 14px',
    cursor: 'pointer',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  dot: (connected: boolean) => ({
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: connected ? '#4CAF50' : '#666',
    flexShrink: 0,
  }),
  headerText: {
    flex: 1,
    fontWeight: 600,
    fontSize: 13,
  },
  chevron: {
    fontSize: 10,
    opacity: 0.5,
  },
  crossingList: {
    padding: '8px 10px 4px',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 6,
  },
  crossingCard: {
    background: 'rgba(255, 68, 68, 0.12)',
    border: '1px solid rgba(255, 68, 68, 0.25)',
    borderRadius: 10,
    padding: '8px 10px',
    position: 'relative' as const,
  },
  crossingTitle: {
    fontWeight: 600,
    fontSize: 12,
    marginBottom: 2,
  },
  crossingBody: {
    opacity: 0.75,
    fontSize: 11,
  },
  crossingMeta: {
    opacity: 0.45,
    fontSize: 10,
    marginTop: 4,
  },
  dismissBtn: {
    position: 'absolute' as const,
    top: 6,
    right: 8,
    background: 'none',
    border: 'none',
    color: 'rgba(255,255,255,0.4)',
    cursor: 'pointer',
    fontSize: 11,
    padding: 0,
  },
  heatRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    padding: '8px 14px 4px',
    flexWrap: 'wrap' as const,
  },
  heatDot: (i: number, total: number) => ({
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: `hsl(${0 + (i / Math.max(total, 1)) * 30}, 90%, 55%)`,
    opacity: 0.8,
  }),
  sliderRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 14px 10px',
  },
  sliderLabel: {
    fontSize: 11,
    opacity: 0.6,
    minWidth: 56,
  },
  slider: {
    flex: 1,
    accentColor: '#5288c1',
    cursor: 'pointer',
  },
};

export default NearByPanel;
