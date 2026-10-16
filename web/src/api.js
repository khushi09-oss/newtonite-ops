// One fetch wrapper. Errors carry the status and the parsed body so the item page can read a 409.

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
  if (key) headers["Idempotency-Key"] = key;
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
