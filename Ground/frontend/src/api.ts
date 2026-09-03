import type { Aircraft, Artifact, EventItem, FlashJob } from "./types";

export class ApiError extends Error {
  code?: string;
  constructor(message: string, code?: string) {
    super(message);
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const detail = (body as { message?: string; code?: string }) ?? {};
    throw new ApiError(
      detail.message || `HTTP ${response.status} ${response.statusText}`,
      detail.code
    );
  }
  return body as T;
}

function json(method: string, payload?: unknown): RequestInit {
  return {
    method,
    headers: payload === undefined ? undefined : { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload)
  };
}

export const api = {
  health: () => request<{ ok: boolean }>("/api/health"),

  aircraft: {
    list: () => request<{ aircraft: Aircraft[] }>("/api/aircraft"),
    detail: (id: string) =>
      request<{ aircraft: Aircraft }>(`/api/aircraft/${encodeURIComponent(id)}`),
    command: (id: string, op: string, params: Record<string, unknown> = {}) =>
      request<{ request_id: string; accepted: boolean }>(
        `/api/aircraft/${encodeURIComponent(id)}/commands`,
        json("POST", { op, params })
      ),
    params: (id: string) =>
      request<{ params: Record<string, unknown> }>(
        `/api/aircraft/${encodeURIComponent(id)}/params`
      ),
    writeParams: (id: string, values: Record<string, unknown>) =>
      request<{ params: Record<string, unknown> }>(
        `/api/aircraft/${encodeURIComponent(id)}/params`,
        json("PUT", { values })
      )
  },

  registry: {
    register: (payload: {
      aircraft_id: string;
      name?: string;
      model?: string;
      static_ip?: string;
    }) => request<{ aircraft: Aircraft }>("/api/v2/aircraft", json("POST", payload)),
    createClaim: (aircraft_id: string) =>
      request<{ claim_id: string; code: string; expires_at: number }>(
        "/api/v2/claims",
        json("POST", { aircraft_id })
      ),
    listClaims: () =>
      request<{ claims: unknown[] }>("/api/v2/claims")
  },

  artifacts: {
    list: () => request<{ artifacts: Artifact[] }>("/api/artifacts"),
    upload: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<{ artifact: Artifact }>("/api/artifacts", {
        method: "POST",
        body: form
      });
    },
    remove: (id: string) =>
      request<{ deleted: string }>(`/api/artifacts/${encodeURIComponent(id)}`, {
        method: "DELETE"
      })
  },

  flash: {
    start: (aircraftId: string, artifactId: string) =>
      request<{ job: FlashJob; accepted: boolean }>(
        `/api/aircraft/${encodeURIComponent(aircraftId)}/flash`,
        json("POST", { artifact_id: artifactId })
      ),
    list: (aircraftId?: string) => {
      const query = aircraftId
        ? `?aircraft_id=${encodeURIComponent(aircraftId)}`
        : "";
      return request<{ jobs: FlashJob[] }>(`/api/flash-jobs${query}`);
    },
    get: (jobId: string) =>
      request<{ job: FlashJob }>(`/api/flash-jobs/${encodeURIComponent(jobId)}`)
  },

  events: {
    list: (limit = 200) =>
      request<{ events: EventItem[] }>(`/api/events?limit=${limit}`)
  }
};
