"use client";

import { Camera, RefreshCw, UserPlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, ApiError, canvasToCompressedJpeg, registerEmployee, type RegistrationResult } from "@/lib/api";

type Message = {
  type: "success" | "error" | "info";
  text: string;
};

function MessageBox({ message }: { message: Message | null }) {
  if (!message) return null;
  return <div className={`message ${message.type === "info" ? "" : message.type}`}>{message.text}</div>;
}

export default function RegisterPage() {
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);
  const [employeeId, setEmployeeId] = useState("");
  const [name, setName] = useState("");
  const [department, setDepartment] = useState("");
  const [email, setEmail] = useState("");
  const [frames, setFrames] = useState<string[]>([]);
  const [guidance, setGuidance] = useState<string[]>([]);
  const [detectedExists, setDetectedExists] = useState(false);
  const [registered, setRegistered] = useState<RegistrationResult | null>(null);
  const [capturing, setCapturing] = useState(false);

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setReady(false);
  }, []);

  const startCamera = useCallback(async () => {
    try {
      stopCamera();
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        setReady(true);
      }
    } catch {
      setMessage({ type: "error", text: "Camera unavailable. Allow camera access and try again." });
    }
  }, [stopCamera]);

  useEffect(() => {
    void startCamera();
    return stopCamera;
  }, [startCamera, stopCamera]);

  const captureFrame = useCallback((): string => {
    const video = videoRef.current;
    if (!video || !ready) throw new Error("Start the camera before capturing frames.");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Camera capture is unavailable.");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvasToCompressedJpeg(canvas);
  }, [ready]);

  async function captureBurst(count: number) {
    if (capturing) return;
    setCapturing(true);
    setGuidance([]);
    try {
      const burst: string[] = [];
      for (let index = 0; index < count; index += 1) {
        burst.push(captureFrame());
        await new Promise((resolve) => setTimeout(resolve, 220));
      }
      setFrames(burst);
      setMessage({ type: "info", text: `${burst.length} frames captured. Hold still and look at the camera.` });
    } catch (error) {
      setMessage({ type: "error", text: error instanceof Error ? error.message : "Capture failed." });
    } finally {
      setCapturing(false);
    }
  }

  async function submit() {
    setLoading(true);
    setMessage(null);
    try {
      if (!employeeId.trim()) throw new Error("Employee ID is required.");
      if (!name.trim()) throw new Error("Employee name is required.");
      if (!department.trim()) throw new Error("Department is required.");
      if (frames.length < 3) throw new Error("Capture at least three face frames.");

      const result = await registerEmployee({
        employeeId: employeeId.trim(),
        name: name.trim(),
        department: department.trim(),
        email: email.trim() || undefined,
        frames: frames.slice(0, 5),
        updateFace: detectedExists
      });

      setRegistered(result);
      if (detectedExists) {
        setMessage({ type: "success", text: `Face updated for ${result.name} (${result.employeeId}).` });
      } else {
        setMessage({ type: "success", text: `Registered ${result.name} (${result.employeeId}). You can now login with your face.` });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setDetectedExists(true);
        setMessage({
          type: "info",
          text: `${employeeId.trim()} is already registered. Update the name/department if needed and click "Update Face" to replace the existing face.`
        });
      } else {
        setMessage({ type: "error", text: error instanceof Error ? error.message : "Registration failed." });
      }
    } finally {
      setLoading(false);
    }
  }

  const canSubmit = Boolean(employeeId.trim() && name.trim() && department.trim() && frames.length >= 3 && !loading && !capturing);

  return (
    <main className="login-shell">
      <section className="login-panel wide">
        <div className="brand">
          <div className="brand-mark">BH</div>
          <div>
            <h1>FaceAuth</h1>
            <p>Employee registration</p>
          </div>
        </div>

        <MessageBox message={message} />

        {registered ? (
          <div className="grid">
            <div className="card">
              <h3>{detectedExists ? "Face updated" : "Registration complete"}</h3>
              <p className="api-note">
                <strong>{registered.name}</strong> ({registered.employeeId}) — {registered.department}.
              </p>
            </div>
            <div className="toolbar">
              <button className="button" onClick={() => router.push("/login")} type="button">
                Go to Login
              </button>
              <button className="button secondary" onClick={() => { setRegistered(null); setFrames([]); setDetectedExists(false); }} type="button">
                Register another
              </button>
            </div>
          </div>
        ) : (
          <div className="camera-grid">
            <div className="card">
              <video className="camera" muted playsInline ref={videoRef} />
              <div className="toolbar">
                <button className="button secondary" onClick={() => void startCamera()} type="button" disabled={ready}>
                  <Camera size={17} />
                  Start
                </button>
                <button className="button secondary" onClick={stopCamera} type="button" disabled={!ready}>
                  Stop
                </button>
                <button
                  className="button"
                  disabled={!ready || capturing}
                  onClick={() => void captureBurst(5)}
                  type="button"
                >
                  {capturing ? "Capturing…" : "Capture 5"}
                  {capturing && <span className="spinner small" />}
                </button>
              </div>
              <div className="capture-strip">
                {frames.map((frame, index) => (
                  <img alt={`Frame ${index + 1}`} key={frame.slice(-24) + index} src={frame} />
                ))}
              </div>
              {guidance.length > 0 && (
                <ul className="guidance">
                  {guidance.map((item) => <li key={item}>{item}</li>)}
                </ul>
              )}
              <p className="api-note">
                Capture 5 clear frames with good lighting. One face per frame. Quality is validated before saving.
              </p>
            </div>

            <div className="card">
              <div className="form-grid">
                <input className="input" onChange={(event) => setEmployeeId(event.target.value)} placeholder="Employee ID *" value={employeeId} />
                <input className="input" onChange={(event) => setName(event.target.value)} placeholder="Employee Name *" value={name} />
                <input className="input" onChange={(event) => setDepartment(event.target.value)} placeholder="Department *" value={department} />
                <input className="input" onChange={(event) => setEmail(event.target.value)} placeholder="Email (optional)" value={email} />
              </div>
              <div className="toolbar">
                <button className="button" disabled={!canSubmit} onClick={() => void submit()} type="button">
                  <UserPlus size={17} />
                  {detectedExists ? "Update Face" : "Register Employee"}
                  {loading && <span className="spinner small" />}
                </button>
                <Link className="button secondary" href="/login">
                  Back to Login
                </Link>
              </div>
              {detectedExists && (
                <p className="api-note warning-text">
                  This Employee ID already exists. The new face will replace the stored embedding — no duplicate record is created.
                </p>
              )}
            </div>
          </div>
        )}

        <p className="api-note">{API_BASE}</p>
      </section>
    </main>
  );
}

