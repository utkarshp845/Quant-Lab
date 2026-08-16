import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../api/client";
import type { LiveQuote, StreamConnectionStatus, StreamMessage, StreamProvider } from "../types/marketData";

const WS_BASE = API_BASE.replace(/^http/, "ws");
const MIN_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 15000;

interface UseQuoteStreamResult {
  status: StreamConnectionStatus;
  statusDetail: string | null;
  quote: LiveQuote | null;
  lastUpdated: Date | null;
}

/**
 * Subscribes to the backend's WebSocket relay of a provider's real-time
 * market-data stream for one symbol -- GET (WebSocket)
 * /api/market-data/stream, see backend/app/api/market_data_stream.py
 * for the exact protocol. No API credentials are ever part of this
 * connection or any message on it; they stay backend-side (see
 * backend/app/streaming/*_stream.py). Alpaca (v0.1.12) and Massive
 * (v0.1.13) supported so far -- see StreamProvider's definition.
 *
 * Named generically (not useAlpacaQuoteStream, its v0.1.12 name) since
 * switching `provider` reconnects to a different upstream through the
 * exact same protocol -- one hook, not one per provider.
 *
 * Reconnects on its own if the WebSocket to OUR backend drops (a
 * browser network blip, the tab waking from sleep, the backend
 * restarting, ...), independent of -- and in addition to -- the
 * backend's own reconnection to the upstream provider. Those are two
 * different failure modes (browser<->backend vs. backend<->provider)
 * and both need handling for updates to "just resume" without a
 * manual refresh.
 */
export function useQuoteStream(symbol: string, provider: StreamProvider): UseQuoteStreamResult {
  const [status, setStatus] = useState<StreamConnectionStatus>("connecting");
  const [statusDetail, setStatusDetail] = useState<string | null>(null);
  const [quote, setQuote] = useState<LiveQuote | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Reconnect bookkeeping lives in a ref, not state -- it's mutated on
  // a timer/socket-event schedule that doesn't need a re-render itself,
  // only the status/quote updates derived from it do.
  const reconnectDelayRef = useRef(MIN_RECONNECT_DELAY_MS);

  useEffect(() => {
    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    reconnectDelayRef.current = MIN_RECONNECT_DELAY_MS;

    // Switching providers means a brand new connection, not a stale
    // quote left over from whichever provider we watched before --
    // clear it immediately rather than showing e.g. a Massive quote
    // labeled "connecting" while Alpaca's stream is still spinning up.
    setQuote(null);
    setLastUpdated(null);

    const connect = () => {
      if (cancelled) return;
      const url = `${WS_BASE}/market-data/stream?symbol=${encodeURIComponent(symbol)}&provider=${provider}`;
      socket = new WebSocket(url);

      socket.onmessage = (event) => {
        if (cancelled) return;
        let msg: StreamMessage;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return; // an unparseable frame should never crash the UI
        }
        if (msg.type === "status") {
          setStatus(msg.status);
          setStatusDetail(msg.detail);
          if (msg.status === "connected") reconnectDelayRef.current = MIN_RECONNECT_DELAY_MS;
        } else if (msg.type === "quote") {
          setQuote(msg.quote);
          setLastUpdated(new Date());
        }
      };

      // onerror carries no useful detail in browsers (the WebSocket
      // spec deliberately withholds it) and onclose always fires right
      // after -- let onclose own the status update and reconnect
      // scheduling so there's exactly one code path for both, not two
      // racing ones.
      socket.onclose = () => {
        if (cancelled) return;
        const delay = reconnectDelayRef.current;
        setStatus("disconnected");
        setStatusDetail(`Reconnecting in ${Math.round(delay / 1000)}s…`);
        reconnectTimer = setTimeout(() => {
          reconnectDelayRef.current = Math.min(delay * 2, MAX_RECONNECT_DELAY_MS);
          connect();
        }, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [symbol, provider]);

  return { status, statusDetail, quote, lastUpdated };
}
