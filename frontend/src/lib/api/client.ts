import { getAccessToken, getWorkspaceId, setWorkspaceId } from "@/lib/supabase";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function detailMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const obj = detail as { message?: string; error?: string };
    return obj.message || obj.error || fallback;
  }
  return fallback;
}

export async function api<T = unknown>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  const token = await getAccessToken();
  if (!token) {
    throw new ApiError("Authentication required", 401);
  }

  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  const wid = getWorkspaceId();
  if (wid) headers.set("X-Workspace-Id", wid);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, { ...options, headers });
  const ct = res.headers.get("content-type") || "";
  let data: unknown = null;
  if (ct.includes("application/json")) {
    data = await res.json();
  } else if (!res.ok) {
    data = { detail: await res.text() };
  }

  if (res.status === 401) {
    throw new ApiError("Session expired — sign in again", 401, data);
  }
  if (!res.ok) {
    throw new ApiError(detailMessage(data, res.statusText || "Request failed"), res.status, data);
  }

  if (data && typeof data === "object" && "workspace_id" in data) {
    const id = (data as { workspace_id?: string }).workspace_id;
    if (id) setWorkspaceId(id);
  }

  return data as T;
}

export async function downloadAuthenticated(url: string, filename = "download.pdf") {
  const token = await getAccessToken();
  if (!token) throw new ApiError("Authentication required", 401);
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const wid = getWorkspaceId();
  if (wid) headers["X-Workspace-Id"] = wid;
  const res = await fetch(url, { headers });
  if (res.status === 401) throw new ApiError("Session expired — sign in again", 401);
  if (!res.ok) throw new ApiError("Download failed", res.status);
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.open(objectUrl, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}
