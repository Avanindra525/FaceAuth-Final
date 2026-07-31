function resolveApiBase() {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
  if (configured) return configured;
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1") return "http://localhost:8000";
  }
  return "https://faceauth-final.onrender.com";
}

export const API_BASE = resolveApiBase();

const REQUEST_TIMEOUT_MS = 15000;
  
type ApiOptions = {
  token?: string;
  method?: string;
  body?: unknown;
};

export function canvasToCompressedJpeg(canvas: HTMLCanvasElement, maxBytes = 300 * 1024) {
  const sourceWidth = canvas.width || 640;
  const sourceHeight = canvas.height || 480;
  const scale = Math.min(1, 640 / Math.max(sourceWidth, sourceHeight));
  const output = document.createElement("canvas");
  output.width = Math.max(1, Math.round(sourceWidth * scale));
  output.height = Math.max(1, Math.round(sourceHeight * scale));
  const context = output.getContext("2d");
  if (!context) throw new Error("Camera capture is unavailable.");
  context.drawImage(canvas, 0, 0, output.width, output.height);

  for (let quality = 0.82; quality >= 0.45; quality -= 0.08) {
    const dataUrl = output.toDataURL("image/jpeg", quality);
    const approximateBytes = Math.ceil((dataUrl.length - dataUrl.indexOf(",") - 1) * 0.75);
    if (approximateBytes <= maxBytes || quality <= 0.45) return dataUrl;
  }
  return output.toDataURL("image/jpeg", 0.45);
}

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
