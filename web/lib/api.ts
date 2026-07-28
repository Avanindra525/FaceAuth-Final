export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ||
  "http://127.0.0.1:5000";

type ApiOptions = {
  token?: string;
  method?: string;
  body?: unknown;
};

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers.Authorization = `Bearer ${options.token}`;

  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method || (options.body === undefined ? "GET" : "POST"),
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    credentials: "include"
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const data = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const message =
      typeof data?.error === "string"
        ? data.error
        : typeof data?.reason === "string"
          ? data.reason
          : `Request failed with ${response.status}`;
    throw new Error(message);
  }

  return data as T;
}

export type Employee = {
  employeeId: string;
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
