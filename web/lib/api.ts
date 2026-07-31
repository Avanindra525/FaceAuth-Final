export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "";

const REQUEST_TIMEOUT_MS = 15000;
  
type ApiOptions = {
  token?: string;
  method?: string;
  body?: unknown;
};

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function userMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && error.name === "AbortError") return "Cannot connect to server.";
  if (error instanceof SyntaxError) return "Cannot connect to server.";
  return "Cannot connect to server.";
}

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  if (!API_BASE) throw new ApiError("Cannot connect to server.");

  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers.Authorization = `Bearer ${options.token}`;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response: Response;

  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: options.method || (options.body === undefined ? "GET" : "POST"),
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "include",
      signal: controller.signal
    });
  } catch (error) {
    throw new ApiError(userMessage(error));
  } finally {
    clearTimeout(timeout);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const contentType = response.headers.get("content-type") || "";
  const looksLikeHtml = text.trimStart().startsWith("<!DOCTYPE") || text.trimStart().startsWith("<html");
  if (looksLikeHtml || (text && !contentType.includes("application/json"))) {
    throw new ApiError("Cannot connect to server.", response.status);
  }

  let data: Record<string, unknown> = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new ApiError("Cannot connect to server.", response.status);
  }

  if (!response.ok) {
    const message =
      typeof data?.error === "string"
        ? data.error
        : typeof data?.reason === "string"
          ? data.reason
          : `Request failed with ${response.status}`;
    throw new ApiError(message, response.status);
  }

  return data as T;
}

export type Employee = {
  employeeId: string;
  documentId?: string;
  name: string;
  email: string;
  department: string;
  designation: string;
  phone: string;
  status?: string;
  role?: string;
  createdAt?: string;
};

export type Dashboard = {
  metrics: Record<string, number>;
  currentUser?: Employee;
  lastLogin?: Record<string, unknown> | null;
  recentLogins?: Array<Record<string, unknown>>;
  recentAttendance?: Array<Record<string, unknown>>;
  recentActivity: Array<Record<string, unknown>>;
};

export type AuthResult = {
  verified: boolean;
  employee?: Employee;
  tokens?: {
    accessToken: string;
    expiresAt: string;
  };
  attendance?: string;
  confidence?: number;
  reason?: string;
  guidance?: string[];
  retry?: boolean;
};
