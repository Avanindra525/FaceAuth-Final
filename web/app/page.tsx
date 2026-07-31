"use client";

import {
  Activity,
  Camera,
  Clock,
  Download,
  FileClock,
  Fingerprint,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  ShieldCheck,
  UserPlus,
  Users
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, ApiError, api, type AuthResult, type Dashboard, type Employee } from "@/lib/api";

type Tab = "dashboard" | "employees" | "enroll" | "verify" | "attendance" | "logs";

type Message = {
  type: "success" | "error" | "info";
  text: string;
};

const emptyEmployee = {
  employeeId: "",
  name: "",
  email: "",
  department: "",
  designation: "",
  phone: "",
  role: "Employee"
};

const nav = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "employees", label: "Employees", icon: Users },
  { id: "enroll", label: "Enroll", icon: UserPlus },
  { id: "verify", label: "Verify", icon: Fingerprint },
  { id: "attendance", label: "Attendance", icon: Clock },
  { id: "logs", label: "Logs", icon: FileClock }
] as const;

function useCamera() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [ready, setReady] = useState(false);

  const start = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: 640, height: 480 },
      audio: false
    });
    streamRef.current = stream;
    if (videoRef.current) {
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
      setReady(true);
    }
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setReady(false);
  }, []);

  const capture = useCallback(() => {
    const video = videoRef.current;
    if (!video || !ready) throw new Error("Start the camera before capture.");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Camera capture is unavailable.");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.86);
  }, [ready]);

  useEffect(() => stop, [stop]);

  return { videoRef, ready, start, stop, capture };
}

function MessageBox({ message }: { message: Message | null }) {
  if (!message) return null;
  return <div className={`message ${message.type === "info" ? "" : message.type}`}>{message.text}</div>;
}

function formatMetric(key: string) {
  return key.replace(/([A-Z])/g, " $1").replace(/^./, (char) => char.toUpperCase());
}

export default function Home() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("dashboard");
  const [message, setMessage] = useState<Message | null>(null);
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [auditRows, setAuditRows] = useState<Array<Record<string, unknown>>>([]);
  const [securityRows, setSecurityRows] = useState<Array<Record<string, unknown>>>([]);
  const [attendanceRows, setAttendanceRows] = useState<Array<Record<string, unknown>>>([]);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [enrollForm, setEnrollForm] = useState(emptyEmployee);
  const [verifyId, setVerifyId] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [enrollFrames, setEnrollFrames] = useState<string[]>([]);
  const [verifyFrames, setVerifyFrames] = useState<string[]>([]);
  const [authReady, setAuthReady] = useState(false);

  const enrollCamera = useCamera();
  const verifyCamera = useCamera();

  useEffect(() => {
    const savedToken = localStorage.getItem("faceauth.token") || "";
    const savedEmployee = localStorage.getItem("faceauth.employeeId") || "";
    setToken(savedToken);
    setEmployeeId(savedEmployee);
    setVerifyId(savedEmployee);
    setAuthReady(true);
    if (!savedToken) router.replace("/login");
  }, [router]);

  const clearSession = useCallback((notice = "Please login again.") => {
    localStorage.removeItem("faceauth.token");
    localStorage.removeItem("faceauth.employeeId");
    localStorage.removeItem("token");
    localStorage.removeItem("employee");
    sessionStorage.clear();
    document.cookie.split(";").forEach((cookie) => {
      const name = cookie.split("=")[0]?.trim();
      if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
    });
    setToken("");
    setEmployeeId("");
    setVerifyId("");
    setMessage({ type: "error", text: notice });
    router.replace("/login");
  }, [router]);

  const showError = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) {
      clearSession("Session expired.");
      return;
    }
    setMessage({ type: "error", text: error instanceof Error ? error.message : "Something went wrong." });
  }, [clearSession]);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    try {
      setDashboard(await api<Dashboard>("/api/dashboard", { token }));
      setMessage(null);
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }, [showError, token]);

  const loadEmployees = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<{ items: Employee[] }>("/api/employees?pageSize=100", { token });
      setEmployees(data.items);
      setMessage(null);
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }, [showError, token]);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const [audit, security] = await Promise.all([
        api<Array<Record<string, unknown>>>("/api/audit", { token }),
        api<Array<Record<string, unknown>>>("/api/security-logs", { token })
      ]);
      setAuditRows(audit);
      setSecurityRows(security);
      setMessage(null);
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }, [showError, token]);

  const loadAttendance = useCallback(async () => {
    if (!employeeId) {
      setMessage({ type: "error", text: "Sign in or enter an employee ID first." });
      return;
    }
    setLoading(true);
    try {
      const [history, monthSummary] = await Promise.all([
        api<{ items: Array<Record<string, unknown>> }>(`/api/attendance/employee/${employeeId}`, { token }),
        api<Record<string, unknown>>(`/api/attendance/employee/${employeeId}/summary`, { token })
      ]);
      setAttendanceRows(history.items);
      setSummary(monthSummary);
      setMessage(null);
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }, [employeeId, showError, token]);

  useEffect(() => {
    if (!authReady || !token) return;
    if (tab === "dashboard") void loadDashboard();
    if (tab === "employees") void loadEmployees();
    if (tab === "logs") void loadLogs();
    if (tab === "attendance") void loadAttendance();
  }, [authReady, loadAttendance, loadDashboard, loadEmployees, loadLogs, tab, token]);

  const title = useMemo(() => nav.find((item) => item.id === tab)?.label || "Dashboard", [tab]);

  async function captureBurst(camera: ReturnType<typeof useCamera>, count: number, setter: (frames: string[]) => void) {
    const frames: string[] = [];
    for (let index = 0; index < count; index += 1) {
      frames.push(camera.capture());
      await new Promise((resolve) => setTimeout(resolve, 180));
    }
    setter(frames);
  }

  async function enrollEmployee() {
    setLoading(true);
    try {
      if (enrollFrames.length < 3) throw new Error("Capture at least three enrollment frames.");
      await api<Employee>("/api/employees", {
        token,
        body: { ...enrollForm, frames: enrollFrames.slice(0, 5) }
      });
      setEnrollForm(emptyEmployee);
      setEnrollFrames([]);
      setMessage({ type: "success", text: "Employee enrolled successfully." });
      await loadEmployees();
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }

  async function verifyEmployee() {
    setLoading(true);
    try {
      if (!verifyId.trim()) throw new Error("Employee ID is required.");
      const image = verifyCamera.capture();
      const result = await api<AuthResult>("/api/auth/verify", {
        body: {
          employeeId: verifyId.trim(),
          image,
          rememberMe
        }
      });
      if (result.tokens?.accessToken && result.employee?.employeeId) {
        localStorage.setItem("faceauth.token", result.tokens.accessToken);
        localStorage.setItem("faceauth.employeeId", result.employee.employeeId);
        localStorage.setItem("token", result.tokens.accessToken);
        localStorage.setItem("employee", JSON.stringify(result.employee));
        setToken(result.tokens.accessToken);
        setEmployeeId(result.employee.employeeId);
      }
      setMessage({
        type: result.verified ? "success" : "error",
        text: result.verified
          ? `Verified ${result.employee?.name || verifyId}. Attendance: ${result.attendance || "recorded"}.`
          : result.reason || "Face not recognized."
      });
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }

  async function identifyFace() {
    setLoading(true);
    try {
      const image = verifyCamera.capture();
      const result = await api<{ found: boolean; employee?: Employee; confidence: number; similarity: number }>(
        "/api/identity/verify",
        { body: { image } }
      );
      setMessage({
        type: result.found ? "success" : "error",
        text: result.found
          ? `Matched ${result.employee?.name} with ${result.confidence}% confidence.`
          : "No matching employee was found."
      });
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }

  async function clockAttendance() {
    setLoading(true);
    try {
      const result = await api<{ status: string }>("/api/attendance/clock", {
        token,
        body: { employeeId }
      });
      setMessage({ type: "success", text: `Attendance ${result.status.replace("_", " ")}.` });
      await loadAttendance();
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    try {
      if (token) await api<void>("/api/auth/logout", { token, body: {} });
    } catch {
      // Local sign-out still clears stale sessions when the server token has expired.
    }
    clearSession("Signed out.");
  }

  async function exportCsv() {
    try {
      if (!API_BASE) throw new Error("Cannot connect to server.");
      const response = await fetch(`${API_BASE}/api/reports/employees.csv`, {
        headers: { Authorization: `Bearer ${token}` },
        credentials: "include"
      });
      if (response.status === 401) {
        clearSession("Session expired.");
        return;
      }
      if (!response.ok) throw new Error("Cannot connect to server.");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "employees.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      showError(error);
    }
  }

  if (!authReady || !token) return null;

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">BH</div>
          <div>
            <h1>FaceAu</h1>
            <p>Biometric attendance console</p>
          </div>
        </div>
        <nav className="nav" aria-label="Primary">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={tab === item.id ? "active" : ""}
                key={item.id}
                onClick={() => setTab(item.id)}
                type="button"
              >
                <Icon size={18} />
                {item.label}
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="main">
        <div className="topbar">
          <div>
            <h2>{title}</h2>
            <p>{employeeId ? `Session employee: ${employeeId}` : "No employee session is active"}</p>
          </div>
          <div className="toolbar">
            <span className="api-pill">{API_BASE}</span>
            <button className="button secondary" onClick={() => void logout()} type="button" title="Sign out">
              <LogOut size={17} />
              Sign out
            </button>
          </div>
        </div>

        <MessageBox message={message} />

        {tab === "dashboard" && (
          <div className="grid">
            <div className="toolbar">
              <button className="button secondary" onClick={() => void loadDashboard()} type="button" disabled={loading}>
                <RefreshCw size={17} />
                Refresh
              </button>
            </div>
            <div className="grid metrics">
              {Object.entries(dashboard?.metrics || {}).map(([key, value]) => (
                <div className="card" key={key}>
                  <div className="metric-label">{formatMetric(key)}</div>
                  <div className="metric-value">{value}</div>
                </div>
              ))}
            </div>
            <div className="card">
              <h3>Current User</h3>
              <div className="activity-list">
                <div className="activity-item">
                  <strong>{dashboard?.currentUser?.name || employeeId || "Signed in"}</strong>
                  <span>{dashboard?.currentUser?.department || ""} {dashboard?.currentUser?.designation || ""}</span>
                </div>
                <div className="activity-item">
                  <strong>Last Login</strong>
                  <span>{String(dashboard?.lastLogin?.timestamp || "No previous login recorded")}</span>
                </div>
              </div>
            </div>
            <div className="card">
              <h3>Recent Attendance</h3>
              <div className="activity-list">
                {(dashboard?.recentAttendance || []).map((row, index) => (
                  <div className="activity-item" key={index}>
                    <strong>{String(row.name || row.employeeId || "Employee")}</strong>
                    <span>{String(row.date || "")} {String(row.status || "")}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="card">
              <h3>Recent Activity</h3>
              <div className="activity-list">
                {(dashboard?.recentActivity || []).map((row, index) => (
                  <div className="activity-item" key={index}>
                    <strong>{String(row.event || row.action || row.status || "activity")}</strong>
                    <span>{String(row.detail || row.employeeId || row.timestamp || "")}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {tab === "employees" && (
          <div className="card">
            <div className="toolbar">
              <button className="button secondary" onClick={() => void loadEmployees()} type="button" disabled={loading}>
                <RefreshCw size={17} />
                Refresh
              </button>
              <button className="button secondary" onClick={() => void exportCsv()} type="button">
                <Download size={17} />
                CSV
              </button>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>Department</th>
                    <th>Designation</th>
                    <th>Email</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {employees.map((employee) => (
                    <tr key={employee.employeeId}>
                      <td>{employee.employeeId}</td>
                      <td>{employee.name}</td>
                      <td>{employee.department}</td>
                      <td>{employee.designation}</td>
                      <td>{employee.email}</td>
                      <td>
                        <span className={`status ${employee.status === "active" ? "ok" : "bad"}`}>
                          {employee.status || "active"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "enroll" && (
          <div className="camera-grid">
            <div className="card">
              <video className="camera" muted playsInline ref={enrollCamera.videoRef} />
              <div className="toolbar">
                <button className="button secondary" onClick={() => void enrollCamera.start()} type="button">
                  <Camera size={17} />
                  Start
                </button>
                <button className="button secondary" onClick={enrollCamera.stop} type="button">
                  Stop
                </button>
                <button
                  className="button"
                  disabled={!enrollCamera.ready}
                  onClick={() => void captureBurst(enrollCamera, 5, setEnrollFrames)}
                  type="button"
                >
                  Capture 5
                </button>
              </div>
              <div className="capture-strip">
                {enrollFrames.map((frame, index) => (
                  <img alt={`Enrollment frame ${index + 1}`} key={frame.slice(-24) + index} src={frame} />
                ))}
              </div>
            </div>
            <div className="card">
              <div className="form-grid">
                {Object.entries(enrollForm).map(([key, value]) => (
                  <input
                    className="input"
                    key={key}
                    onChange={(event) => setEnrollForm((current) => ({ ...current, [key]: event.target.value }))}
                    placeholder={formatMetric(key)}
                    value={value}
                  />
                ))}
              </div>
              <div className="toolbar">
                <button className="button" disabled={loading} onClick={() => void enrollEmployee()} type="button">
                  <UserPlus size={17} />
                  Enroll employee
                </button>
              </div>
            </div>
          </div>
        )}

        {tab === "verify" && (
          <div className="camera-grid">
            <div className="card">
              <video className="camera" muted playsInline ref={verifyCamera.videoRef} />
              <div className="toolbar">
                <button className="button secondary" onClick={() => void verifyCamera.start()} type="button">
                  <Camera size={17} />
                  Start
                </button>
                <button className="button secondary" onClick={verifyCamera.stop} type="button">
                  Stop
                </button>
                <button
                  className="button"
                  disabled={!verifyCamera.ready}
                  onClick={() => void captureBurst(verifyCamera, 1, setVerifyFrames)}
                  type="button"
                >
                  Capture
                </button>
              </div>
              <div className="capture-strip">
                {verifyFrames.slice(0, 5).map((frame, index) => (
                  <img alt={`Verification frame ${index + 1}`} key={frame.slice(-24) + index} src={frame} />
                ))}
              </div>
            </div>
            <div className="card">
              <div className="form-grid">
                <input
                  className="input"
                  onChange={(event) => setVerifyId(event.target.value)}
                  placeholder="Employee ID"
                  value={verifyId}
                />
                <label className="toolbar">
                  <input checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} type="checkbox" />
                  Remember me
                </label>
              </div>
              <div className="toolbar">
                <button className="button" disabled={loading} onClick={() => void verifyEmployee()} type="button">
                  <ShieldCheck size={17} />
                  Verify login
                </button>
                <button className="button secondary" disabled={loading || !verifyCamera.ready} onClick={() => void identifyFace()} type="button">
                  <Fingerprint size={17} />
                  Identify
                </button>
              </div>
            </div>
          </div>
        )}

        {tab === "attendance" && (
          <div className="grid">
            <div className="card">
              <div className="toolbar">
                <input className="input" onChange={(event) => setEmployeeId(event.target.value)} placeholder="Employee ID" value={employeeId} />
                <button className="button secondary" onClick={() => void loadAttendance()} type="button" disabled={loading}>
                  <RefreshCw size={17} />
                  Refresh
                </button>
                <button className="button" onClick={() => void clockAttendance()} type="button" disabled={loading || !token}>
                  <Clock size={17} />
                  Clock
                </button>
              </div>
              {summary && (
                <div className="grid metrics">
                  {Object.entries(summary).map(([key, value]) => (
                    <div className="card" key={key}>
                      <div className="metric-label">{formatMetric(key)}</div>
                      <div className="metric-value">{String(value)}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="card table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Clock In</th>
                    <th>Clock Out</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {attendanceRows.map((row, index) => (
                    <tr key={index}>
                      <td>{String(row.date || "")}</td>
                      <td>{String(row.clockIn || "")}</td>
                      <td>{String(row.clockOut || "")}</td>
                      <td>{String(row.status || "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "logs" && (
          <div className="grid">
            <div className="toolbar">
              <button className="button secondary" onClick={() => void loadLogs()} type="button" disabled={loading}>
                <Activity size={17} />
                Refresh logs
              </button>
            </div>
            <div className="card table-wrap">
              <h3>Audit</h3>
              <table>
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Action</th>
                    <th>Actor</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {auditRows.map((row, index) => (
                    <tr key={index}>
                      <td>{String(row.timestamp || "")}</td>
                      <td>{String(row.action || "")}</td>
                      <td>{String(row.actor || "")}</td>
                      <td>{String(row.detail || "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card table-wrap">
              <h3>Security</h3>
              <table>
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Event</th>
                    <th>Employee</th>
                    <th>Severity</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {securityRows.map((row, index) => (
                    <tr key={index}>
                      <td>{String(row.timestamp || "")}</td>
                      <td>{String(row.event || "")}</td>
                      <td>{String(row.employeeId || "")}</td>
                      <td>{String(row.severity || "")}</td>
                      <td>{String(row.detail || "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
