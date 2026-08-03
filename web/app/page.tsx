"use client";

import { Fingerprint, LogIn, ShieldCheck, UserPlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";

export default function LandingPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const savedToken = localStorage.getItem("faceauth.token") || localStorage.getItem("token") || "";
    if (savedToken) {
      router.replace("/dashboard");
      return;
    }
    setChecking(false);
  }, [router]);

  if (checking) {
    return (
      <main className="landing-shell">
        <span className="spinner" />
      </main>
    );
  }

  return (
    <main className="landing-shell">
      <section className="landing-panel">
        <div className="brand center">
          <div className="brand-mark">BH</div>
          <div>
            <h1>FaceAuth</h1>
            <p>Biometric attendance &amp; employee authentication</p>
          </div>
        </div>

        <p className="landing-tagline">
          Sign in with your face, or register a new employee by capturing face frames.
          Your Employee ID and face are matched securely — no passwords to remember.
        </p>

        <div className="toolbar column">
          <Link className="button primary-lg" href="/login">
            <LogIn size={18} />
            Login with Face
          </Link>
          <Link className="button secondary-lg" href="/register">
            <UserPlus size={18} />
            Register Employee
          </Link>
          <Link className="button ghost-sm" href="/console">
            <ShieldCheck size={16} />
            Admin Console
          </Link>
        </div>

        <div className="feature-row">
          <div className="feature">
            <Fingerprint size={20} />
            <span>Face recognition</span>
          </div>
          <div className="feature">
            <UserPlus size={20} />
            <span>Self registration</span>
          </div>
          <div className="feature">
            <ShieldCheck size={20} />
            <span>JWT secure</span>
          </div>
        </div>

        <p className="api-note center">{API_BASE}</p>
      </section>
    </main>
  );
}

