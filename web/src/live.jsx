import { createContext, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

// One server-sent events connection per tab. While it is up, pages poll once a minute as a safety
// net; when it drops, the browser reconnects by itself and pages fall back to polling every 15 s.
const Live = createContext(false);

export function useLiveConnected() {
  return useContext(Live);
}

export function pollEvery(connected) {
  return connected ? 60000 : 15000;
}

export function LiveUpdates({ itemId, onEnded, children }) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (typeof EventSource === "undefined") return undefined;
    const source = new EventSource(itemId ? `/api/stream?item=${itemId}` : "/api/stream");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false); // EventSource retries on its own
    source.addEventListener("item", (e) => {
      const { id, version } = JSON.parse(e.data);
      const cached = qc.getQueryData(["item", id]);
      if (!cached || cached.version !== version) {
        qc.invalidateQueries({ queryKey: ["item", id] });
        qc.invalidateQueries({ queryKey: ["inbox"] });
      }
    });
    source.addEventListener("notifications", () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["inbox"] });
    });
    source.addEventListener("ended", (e) => {
      // Signed out elsewhere, or no longer allowed to see this item: stop, and let the page find out.
      source.close();
      setConnected(false);
      onEnded(JSON.parse(e.data).reason);
    });
    return () => source.close();
  }, [itemId, qc, onEnded]);

  return <Live.Provider value={connected}>{children}</Live.Provider>;
}
