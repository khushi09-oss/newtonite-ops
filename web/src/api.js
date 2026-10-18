// One fetch wrapper. Errors carry the status and the parsed body so the item page can read a 409.
import { useRef } from "react";

export class ApiError extends Error {
  constructor(status, body) {
    const detail = body && body.detail;
    super((detail && detail.message) || (typeof detail === "string" ? detail : `Request failed (${status})`));
    this.status = status;
    this.body = body;
  }
}

export async function api(path, { method = "GET", body, key } = {}) {
  const headers = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  // Every write carries a key, so the server can replay a repeat instead of running it twice. Pass
  // one to reuse it on a retry; otherwise each call gets its own.
  if (method !== "GET") headers["Idempotency-Key"] = key || newKey();
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const newKey = () =>
  crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

// One key per thing a person is trying to do: sending the same form again (after a timeout, say)
// reuses it, so the server replays the first answer. A success retires it for the next one.
export function useRetryKey() {
  const ref = useRef(newKey());
  return { key: () => ref.current, done: () => (ref.current = newKey()) };
}
