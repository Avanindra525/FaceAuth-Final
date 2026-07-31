"use client";

import { Camera, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, api, type AuthResult } from "@/lib/api";

type Message = {
  type: "success" | "error" | "info";
  text: string;
};

function MessageBox({ message }: { message: Message | null }) {
  if (!message) return null;
  return <div className={`message ${message.type === "info" ? "" : message.type}`}>{message.text}</div>;
}

export default function LoginPage() {
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [employeeId, setEmployeeId] = useState("");
  const [facingMode, setFacingMode] = useState<"user" | "environment">("user");
  const [message, setMessage] = useState<Message | null>(null);

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setReady(false);
  }, []);

  const startCamera = useCallback(async (mode: "user" | "environment" = "user") => {
    try {
      stopCamera();
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: mode, width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        setReady(true);
      }
    } catch {
      setMessage({ type: "error", text: "Camera unavailable." });
    }
  }, [stopCamera]);

  useEffect(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("employee");
    void startCamera("user");
    return stopCamera;
  }, [startCamera, stopCamera]);

  async function switchCamera() {
    const nextMode = facingMode === "user" ? "environment" : "user";
    setFacingMode(nextMode);
    await startCamera(nextMode);
  }

  function capture() {
    const video = videoRef.current;
    if (!video || !ready) throw new Error("Start the camera before login.");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Camera capture is unavailable.");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.86);
  }

  async function login() {
    setLoading(true);
    try {
      if (!employeeId.trim()) throw new Error("Employee ID is required.");
      const result = await api<AuthResult>("/api/auth/verify", {
        body: { employeeId: employeeId.trim(), image: capture(), rememberMe }
      });
      if (!result.verified || !result.tokens?.accessToken || !result.employee?.employeeId) {
        throw new Error(result.reason || "Face not recognized.");
      }
      localStorage.setItem("faceauth.token", result.tokens.accessToken);
      localStorage.setItem("faceauth.employeeId", result.employee.employeeId);
      localStorage.setItem("token", result.tokens.accessToken);
      localStorage.setItem("employee", JSON.stringify(result.employee));
      sessionStorage.clear();
      stopCamera();
      router.replace("/");
    } catch (error) {
      setMessage({ type: "error", text: error instanceof Error ? error.message : "Cannot connect to server." });
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="brand">
          <div className="brand-mark">BH</div>
          <div>
            <h1>FaceAuth</h1>
            <p>Biometric attendance console</p>
          </div>
        </div>
        <MessageBox message={message} />
        <div className="form-grid single">
          <input
            className="input"
            onChange={(event) => setEmployeeId(event.target.value)}
            placeholder="Employee ID"
            value={employeeId}
          />
        </div>
        <video className="camera" muted playsInline ref={videoRef} />
        <div className="toolbar">
          <button className="button secondary" onClick={() => void startCamera()} type="button" disabled={ready}>
            <Camera size={17} />
            Camera
          </button>
          <button className="button secondary" onClick={() => void switchCamera()} type="button" disabled={loading}>
            Switch
          </button>
          <button className="button" onClick={() => void login()} type="button" disabled={loading || !ready || !employeeId.trim()}>
            <ShieldCheck size={17} />
            Login
          </button>
          <label className="toolbar">
            <input checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} type="checkbox" />
            Remember me
          </label>
        </div>
        <p className="api-note">{API_BASE || "API URL is not configured."}</p>
      </section>
    </main>
  );
}
