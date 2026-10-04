import { useEffect, useRef } from "react";

type Handler = (event: string, payload: any) => void;
const handlers = new Set<Handler>();
let socket: WebSocket | null = null;
let retry = 0;
let closedByUs = false;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws/events/`);
  socket.onopen = () => {
    retry = 0;
  };
  socket.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      handlers.forEach((h) => h(msg.event, msg.payload));
    } catch {
      /* ignore */
    }
  };
  socket.onclose = (e) => {
    socket = null;
    if (closedByUs || e.code === 4401) return;
    retry = Math.min(retry + 1, 6);
    setTimeout(() => handlers.size && connect(), 1000 * 2 ** retry);
  };
}

export function sendEvent(data: Record<string, unknown>) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
}

export function closeEvents() {
  closedByUs = true;
  socket?.close();
  socket = null;
}

/** Підписка на події реального часу (одне WebSocket-з'єднання на вкладку). */
export function useEvents(handler: Handler) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const h: Handler = (ev, p) => ref.current(ev, p);
    handlers.add(h);
    closedByUs = false;
    if (!socket) connect();
    return () => {
      handlers.delete(h);
    };
  }, []);
}
