"use client";

import { BadgeCheck, Building2, Clock, LogOut, UserCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { API_BASE, ApiError, api, type Dashboard } from "@/lib/api";

type Message = {
  type: "success" | "error";
  text: string;
};

function MessageBox({ message }: { message: Message | null }) {
  if (!message) return null;
  return <div className={`message ${message.type}`}>{message.text}</div>;
}

function formatDateTime(value?: unknown) {
  if (!value) return "No previous login recorded";
  const text = String(value);
  if (!text || text === "undefined" || text === "null") return "No previous login recorded";
  try {
    return new Date(text).toLocaleString();
  } catch {
    return text;
  }
}

export default function DashboardPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<Dashboard | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const [clocking, setClocking] = useState(false);

  const token = typeof window !== "undefined" ? (localStorage.getItem("faceauth.token") || localStorage.getItem("token") || "") : "";
  const storedEmployee = typeof window !== "undefined" ? localStorage.getItem("employee") : null;
  const employeeName = storedEmployee ? (JSON.parse(storedEmployee)?.name ?? "") : "";

  const clearSession = useCallback((notice: string) => {
    localStorage.removeItem("faceauth.token");
    localStorage.removeItem("faceauth.employeeId");
    localStorage.removeItem("token");
    localStorage.removeItem("employee");
    router.replace("/login");
    setMessage({ type: "error", text: notice });
  }, [router]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api<Dashboard>("/api/dashboard", { token });
      setData(result);
      setMessage(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearSession("Session expired. Please login again.");
      } else {
        setMessage({ type: "error", text: error instanceof Error ? error.message : "Failed to load dashboard." });
      }
    } finally {
      setLoading(false);
    }
  }, [clearSession, token]);

  useEffect(() => {
    if (!token) {
      router.replace("/login");
      return;
    }
    void load();
  }, [load, router, token]);

  async function clock() {
    setClocking(true);
    try {
      const employeeId = localStorage.getItem("faceauth.employeeId") || "";
      const result = await api<{ status: string }>("/api/attendance/clock", {
        token,
        body: { employeeId }
      });
      setMessage({ type: "success", text: `Attendance ${result.status.replace("_", " ")}.` });
      await load();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearSession("Session expired. Please login again.");
      } else {
        setMessage({ type: "error", text: error instanceof Error ? error.message : "Clock failed." });
      }
    } finally {
      setClocking(false);
    }
  }

  async function logout() {
    try {
      if (token) await api<void>("/api/auth/logout", { token, body: {} });
    } catch {
      // Local sign-out still clears stale sessions.
    }
    localStorage.removeItem("faceauth.token");
    localStorage.removeItem("faceauth.employeeId");
    localStorage.removeItem("token");
    localStorage.removeItem("employee");
    router.replace("/login");
  }

  const currentUser = data?.currentUser;
  const employeeId = typeof window !== "undefined" ? (localStorage.getItem("faceauth.employeeId") || currentUser?.employeeId || "") : "";

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">BH</div>
          <div>
            <h1>FaceAu</h1>
            <p>Employee dashboard</p>
          </div>
        </div>
        <div className="nav">
          <a className="nav-link active" href="/dashboard"><UserCircle size={18} /> My Dashboard</a>
          <LinkButton href="/login" icon={<LogOut size={18} />} onClick={() => void logout()} label="Logout" />
        </div>
      </aside>

      <section className="main">
        <div className="topbar">
          <div>
            <h2>Employee Dashboard</h2>
            <p>{employeeId ? `Session employee: ${employeeId}` : "No employee session"}</p>
          </div>
          <div className="toolbar">
            <span className="api-pill">{API_BASE}</span>
            <button className="button secondary" onClick={() => void logout()} type="button" title="Sign out">
              <LogOut size={17} />
              Logout
            </button>
          </div>
        </div>

        <MessageBox message={message} />

        {loading ? (
          <div className="card center"><span className="spinner" /></div>
        ) : (
          <div className="grid">
            <div className="grid metrics two">
              <div className="card">
                <div className="metric-label">Employee Name</div>
                <div className="metric-value medium">{currentUser?.name || employeeName || "—"}</div>
              </div>
              <div className="card">
                <div className="metric-label">Employee ID</div>
                <div className="metric-value medium">{currentUser?.employeeId || employeeId || "—"}</div>
              </div>
              <div className="card">
                <div className="metric-label">Department</div>
                <div className="metric-value medium">{currentUser?.department || "—"}</div>
              </div>
              <div className="card">
                <div className="metric-label">Designation</div>
                <div className="metric-value medium">{currentUser?.designation || "Employee"}</div>
              </div>
            </div>

            <div className="card">
              <h3><Clock size={17} /> Last Login</h3>
              <p className="api-note bigger">{formatDateTime(data?.lastLogin?.timestamp)}</p>
              <div className="toolbar">
                <button className="button" disabled={clocking} onClick={() => void clock()} type="button">
                  <BadgeCheck size={17} />
                  {clocking ? "Clocking…" : "Clock In / Out"}
                  {clocking && <span className="spinner small" />}
                </button>
              </div>
            </div>

            <div className="card">
              <h3><Building2 size={17} /> Recent Activity</h3>
              <div className="activity-list">
                {(data?.recentActivity || []).slice(0, 6).map((row, index) => (
                  <div className="activity-item" key={index}>
                    <strong>{String(row.event || row.action || row.status || "activity")}</strong>
                    <span>{String(row.detail || row.employeeId || "")}</span>
                    <span>{String(row.timestamp ? formatDateTime(row.timestamp) : "")}</span>
                  </div>
                ))}
                {(data?.recentActivity || []).length === 0 && <p className="api-note">No recent activity.</p>}
              </div>
            </div>

            <div className="card">
              <h3>Recent Logins</h3>
              <div className="activity-list">
                {(data?.recentLogins || []).slice(0, 6).map((row, index) => (
                  <div className="activity-item" key={index}>
                    <strong>{String(row.employeeId || "")}</strong>
                    <span>{String(row.status === "success" ? "Success" : "Failed")}</span>
                    <span>{String(row.timestamp ? formatDateTime(row.timestamp) : "")}</span>
                  </div>
                ))}
                {(data?.recentLogins || []).length === 0 && <p className="api-note">No login history yet.</p>}
              </div>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}

function LinkButton({ href, icon, label, onClick }: { href: string; icon: React.ReactNode; label: string; onClick?: () => void }) {
  return (
    <a className="nav-link" href={href} onClick={onClick}>
      {icon} {label}
    </a>
  );
}

