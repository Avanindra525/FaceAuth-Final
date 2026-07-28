"""FaceAuth Enterprise Flask API.

Biometric routes fail closed: Firestore, InsightFace and MediaPipe must be
available. There is no image-hash or mock-recognition path in this service.
"""
print("RUNNING THIS APP.PY")
import csv
import io
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, firestore
import jwt
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from services.biometrics import (BiometricError, average_embeddings, cosine_similarity,
                                 get_engine, random_challenge)

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
CORS(app, resources={r"/api/*": {"origins": os.getenv("CORS_ORIGIN", "http://localhost:3000")}}, supports_credentials=True)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"], storage_uri="memory://")
JWT_SECRET = os.getenv("JWT_SECRET")
THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.80"))
MODEL_VERSION = os.getenv("INSIGHTFACE_MODEL", "buffalo_l")


def utcnow(): return datetime.now(timezone.utc)
def iso(): return utcnow().isoformat()

def db():
    """Return Firestore client using serviceAccountKey.json"""
    if not firebase_admin._apps:
        cred = credentials.Certificate(
            Path(__file__).parent / "serviceAccountKey.json"
        )
        firebase_admin.initialize_app(cred)

    return firestore.client()

def clean_employee(value):
    value = dict(value)
    value.pop("embeddingVector", None)
    return value

def log(collection, payload):
    db().collection(collection).add({**payload, "timestamp": iso()})

def security(event, detail, employee_id=None, severity="warning"):
    log("security_logs", {"event": event, "detail": detail, "employeeId": employee_id, "severity": severity,
                          "ip": request.headers.get("X-Forwarded-For", request.remote_addr)})

def audit(action, actor, detail):
    log("audit_logs", {"action": action, "actor": actor, "detail": detail})

def issue_tokens(employee, remember=False):
    if not JWT_SECRET: raise RuntimeError("JWT_SECRET must be configured.")
    now = utcnow(); session_id = secrets.token_urlsafe(24)
    expiry = now + timedelta(days=30 if remember else 1)
    claims = {"sub": employee["employeeId"], "role": employee.get("role", "Employee"), "sid": session_id,
              "iat": now, "exp": expiry}
    token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    db().collection("sessions").document(session_id).set({"employeeId": employee["employeeId"], "createdAt": iso(),
        "expiresAt": expiry.isoformat(), "active": True, "device": request.user_agent.string})
    return {"accessToken": token, "expiresAt": expiry.isoformat()}

def require_auth(fn):
    """Require a valid JWT session without role restriction."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token: return {"error": "Authentication is required."}, 401
        try:
            claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            session = db().collection("sessions").document(claims["sid"]).get()
            if not session.exists or not session.to_dict().get("active"): raise jwt.InvalidTokenError
        except Exception:
            return {"error": "Your session is invalid or expired."}, 401
        request.identity = claims
        return fn(*args, **kwargs)
    return wrapped

def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not token: return {"error": "Authentication is required."}, 401
            try:
                claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
                session = db().collection("sessions").document(claims["sid"]).get()
                if not session.exists or not session.to_dict().get("active"): raise jwt.InvalidTokenError
            except Exception:
                return {"error": "Your session is invalid or expired."}, 401
            if roles and claims.get("role") not in roles:
                security("unauthorized_request", fn.__name__, claims.get("sub"), "high")
                return {"error": "You do not have permission for this action."}, 403
            request.identity = claims
            return fn(*args, **kwargs)
        return wrapped
    return decorator

def employee_by_id(employee_id):
    snap = db().collection("employees").document(employee_id).get()
    return snap.to_dict() if snap.exists else None

def all_active_employees():
    return [doc.to_dict() for doc in db().collection("employees").where("status", "==", "active").stream()]

def attendance(employee_id):
    key = f"{employee_id}_{utcnow().date().isoformat()}"; ref = db().collection("attendance").document(key); existing = ref.get()
    if existing.exists and not existing.to_dict().get("clockOut"):
        ref.update({"clockOut": iso(), "status": "completed"}); return "clock_out"
    ref.set({"employeeId": employee_id, "date": utcnow().date().isoformat(), "clockIn": iso(), "clockOut": None, "status": "present"})
    return "clock_in"

@app.errorhandler(BiometricError)
def biometric_error(error):
    security("biometric_rejected", str(error), severity="warning")
    return jsonify({"error": str(error)}), 422

@app.errorhandler(RuntimeError)
def runtime_error(error):
    app.logger.exception(error)
    return jsonify({"error": "A required secure service is unavailable."}), 503


@app.get("/health")
def health():
    db()  # Verify Firestore connection

    return {
        "status": "ok",
        "mode": "biometric",
        "modelVersion": MODEL_VERSION,
        "threshold": THRESHOLD
    }


@app.route("/api/employees", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def employees():
    if request.method == "GET":
        department, search = request.args.get("department"), request.args.get("search", "").lower()
        page, size = max(1, int(request.args.get("page", 1))), min(100, max(1, int(request.args.get("pageSize", 20))))
        rows = [e for e in all_active_employees() if (not department or e.get("department") == department) and
                (not search or search in (e.get("name", "") + e.get("employeeId", "") + e.get("email", "")).lower())]
        return {"items": [clean_employee(e) for e in rows[(page-1)*size:page*size]], "total": len(rows), "page": page, "pageSize": size}
    body = request.get_json(silent=True) or {}
    required = ("employeeId", "name", "email", "department", "designation", "phone")
    if any(not str(body.get(key, "")).strip() for key in required): return {"error": "Employee ID, name, email, department, designation and phone are required."}, 400
    if employee_by_id(body["employeeId"]): return {"error": "Employee ID already exists."}, 409
    frames = body.get("frames", [])
    engine = get_engine(); results = [engine.extract(image) for image in frames]
    embedding = average_embeddings([result.embedding for result in results])
    employee = {key: body[key].strip() for key in required}
    employee.update({"status": "active", "role": body.get("role", "Employee"), "createdAt": iso(), "lastUpdated": iso(),
                     "embeddingVector": embedding, "modelVersion": MODEL_VERSION})
    db().collection("employees").document(employee["employeeId"]).set(employee)
    audit("employee.enrolled", "system", employee["employeeId"])
    log("notifications", {"type": "employee_registered", "employeeId": employee["employeeId"], "message": "Enrollment completed."})
    return clean_employee(employee), 201

@app.route("/api/employees/<employee_id>", methods=["GET", "PATCH", "DELETE"])
def employee(employee_id):
    current = employee_by_id(employee_id)
    if not current: return {"error": "Employee was not found."}, 404
    if request.method == "GET": return clean_employee(current)
    if request.method == "DELETE":
        db().collection("employees").document(employee_id).update({"status": "deleted", "lastUpdated": iso()}); audit("employee.deleted", "system", employee_id)
        return "", 204
    changes = {key: value for key, value in (request.get_json(silent=True) or {}).items() if key in {"name", "email", "department", "designation", "phone", "status", "role"}}
    changes["lastUpdated"] = iso(); db().collection("employees").document(employee_id).update(changes); audit("employee.updated", "system", employee_id)
    return clean_employee({**current, **changes})

@app.post("/api/auth/challenge")
@limiter.limit("10 per minute")
def auth_challenge():
    challenge_id, challenge = secrets.token_urlsafe(18), random_challenge()
    db().collection("auth_challenges").document(challenge_id).set({"challenge": challenge, "createdAt": iso(), "used": False, "attempts": 0})
    return {"challengeId": challenge_id, "challenge": challenge, "expiresIn": 90}

@app.post("/api/auth/verify")
@limiter.limit("8 per minute")
def verify_auth():
    started = time.perf_counter(); body = request.get_json(silent=True) or {}; employee_id = body.get("employeeId", "")
    emp = employee_by_id(employee_id)
    if not emp or emp.get("status") != "active":
        security("failed_login", "Employee not found or inactive", employee_id); return {"verified": False, "reason": "Authentication failed."}, 401
    challenge_doc = db().collection("auth_challenges").document(body.get("challengeId", "")).get()
    if not challenge_doc.exists or challenge_doc.to_dict().get("used"):
        security("failed_login", "Invalid or reused liveness challenge", employee_id); return {"verified": False, "reason": "Request a new liveness challenge."}, 400
    challenge_data = challenge_doc.to_dict(); frames = body.get("frames") or ([body["image"]] if body.get("image") else [])
    if not isinstance(frames, list) or not 5 <= len(frames) <= 10:
        return {"verified": False, "reason": "Capture five to ten camera frames before verification."}, 400
    batch = get_engine().extract_batch(frames)
    liveness = get_engine().liveness(frames[batch.best_frame], challenge_data["challenge"], body.get("observations"))
    if not liveness["passed"]:
        db().collection("auth_challenges").document(body["challengeId"]).update({"used": True})
        security("liveness_failure", liveness["reason"], employee_id, "high")
        log("login_history", {"employeeId": employee_id, "status": "failed", "livenessPassed": False, "spoofDetected": True, "similarityScore": 0})
        return {"verified": False, "reason": liveness["reason"], "liveness": liveness}, 401
    similarity = cosine_similarity(batch.embedding, emp["embeddingVector"])
    elapsed = round((time.perf_counter() - started) * 1000)
    attempt = min(max(int(challenge_data.get("attempts", 0)), 0), 2)
    # Keep the production threshold. A borderline score earns fresh frames, not a weaker decision.
    if 0.75 <= similarity < 0.85 and attempt < 2:
        db().collection("auth_challenges").document(body["challengeId"]).update({"attempts": attempt + 1})
        return {"verified": False, "retry": True, "similarity": similarity, "confidence": round(similarity * 100, 1),
                "guidance": batch.guidance or ["Hold still and look directly at the camera."], "attempt": attempt + 1,
                "reason": "We need a few clearer frames to confirm your identity."}
    db().collection("auth_challenges").document(body["challengeId"]).update({"used": True})
    verified = similarity >= THRESHOLD and (similarity >= 0.85 or attempt >= 2)
    history = {"employeeId": employee_id, "timestamp": iso(), "device": request.user_agent.string, "browser": request.user_agent.browser,
               "ip": request.headers.get("X-Forwarded-For", request.remote_addr), "location": None, "authenticationTime": elapsed,
               "similarityScore": similarity, "status": "success" if verified else "failed", "livenessPassed": True, "spoofDetected": False,
               "acceptedFrames": batch.accepted, "discardedFrames": batch.discarded}
    log("login_history", history)
    if not verified:
        security("failed_login", f"Low similarity: {similarity:.3f}", employee_id); return {"verified": False, "similarity": similarity, "guidance": batch.guidance, "reason": "Face did not match."}, 401
    event = attendance(employee_id); tokens = issue_tokens(emp, bool(body.get("rememberMe")))
    audit("auth.success", employee_id, f"score={similarity:.3f}")
    return {"verified": True, "employee": clean_employee(emp), "similarity": similarity, "confidence": round(similarity * 100, 1),
            "verificationTime": elapsed, "attendance": event, "tokens": tokens, "acceptedFrames": batch.accepted, "discardedFrames": batch.discarded}

@app.post("/api/identity/verify")
@limiter.limit("10 per minute")
def verify_identity():
    started = time.perf_counter(); face = get_engine().extract((request.get_json(silent=True) or {}).get("image", ""))
    ranked = sorted(((cosine_similarity(face.embedding, employee["embeddingVector"]), employee) for employee in all_active_employees()), reverse=True, key=lambda result: result[0])
    similarity, employee = ranked[0] if ranked else (0.0, None); found = employee is not None and similarity >= THRESHOLD
    audit("identity.lookup", "system", f"found={found}; score={similarity:.3f}")
    return {"found": found, "similarity": similarity, "confidence": round(similarity * 100, 1), "verificationTime": round((time.perf_counter()-started)*1000),
            "employee": clean_employee(employee) if found else None}

@app.get("/api/dashboard")
def dashboard():
    today = utcnow().date().isoformat(); employees = all_active_employees()
    logins = [d.to_dict() for d in db().collection("login_history").stream()]; alerts = [d.to_dict() for d in db().collection("security_logs").stream()]
    successes = [row for row in logins if row.get("status") == "success"]; failures = [row for row in logins if row.get("status") == "failed"]
    avg = round(sum(row.get("authenticationTime", 0) for row in successes) / len(successes), 1) if successes else 0
    return {"metrics": {"employees": len(employees), "todayAttendance": sum(1 for d in db().collection("attendance").stream() if d.to_dict().get("date") == today),
            "liveUsers": sum(1 for d in db().collection("sessions").stream() if d.to_dict().get("active")), "failedAttempts": len(failures),
            "securityAlerts": len(alerts), "successRate": round((len(successes)/len(logins))*100, 1) if logins else 0, "averageLoginTime": avg,
            "livenessFailures": sum(1 for item in alerts if item.get("event") == "liveness_failure"), "spoofAttempts": sum(1 for item in alerts if item.get("event") == "liveness_failure")},
            "recentActivity": sorted(logins + alerts, key=lambda row: row.get("timestamp", ""), reverse=True)[:10]}

@app.get("/api/audit")
def audits(): return jsonify([doc.to_dict() for doc in db().collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(100).stream()])
@app.get("/api/security-logs")
def security_logs(): return jsonify([doc.to_dict() for doc in db().collection("security_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(100).stream()])

@app.get("/api/reports/employees.csv")
def export_employees():
    output = io.StringIO(); columns = ["employeeId", "name", "email", "department", "designation", "phone", "status", "createdAt"]
    writer = csv.DictWriter(output, fieldnames=columns); writer.writeheader(); writer.writerows(clean_employee(e) for e in all_active_employees())
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=employees.csv"})

@app.post("/api/auth/logout")
@require_roles("Admin", "HR", "Employee", "Security Officer")
def logout():
    db().collection("sessions").document(request.identity["sid"]).update({"active": False, "endedAt": iso()})
    return "", 204

@app.get("/api/attendance/employee/<employee_id>")
@require_auth
def attendance_history(employee_id):
    """Return attendance history for a specific employee."""
    records = []
    for doc in db().collection("attendance").where("employeeId", "==", employee_id).order_by("date", direction=firestore.Query.DESCENDING).limit(90).stream():
        records.append(doc.to_dict())
    return {"items": records, "total": len(records)}

@app.get("/api/attendance/employee/<employee_id>/summary")
@require_auth
def attendance_summary(employee_id):
    """Return monthly attendance summary for a specific employee."""
    records = [doc.to_dict() for doc in db().collection("attendance").where("employeeId", "==", employee_id).stream()]
    now = utcnow()
    current_month = now.month
    current_year = now.year
    month_records = [r for r in records if r.get("date","").startswith(f"{current_year}-{current_month:02d}")]
    present_days = len(month_records)
    late_days = sum(1 for r in month_records if r.get("status") == "late")
    total_hours = 0.0
    for r in month_records:
        if r.get("clockIn") and r.get("clockOut"):
            try:
                cin = datetime.fromisoformat(r["clockIn"])
                cout = datetime.fromisoformat(r["clockOut"])
                total_hours += (cout - cin).total_seconds() / 3600
            except (ValueError, TypeError):
                pass
    return {
        "month": f"{current_year}-{current_month:02d}",
        "presentDays": present_days,
        "lateDays": late_days,
        "absentDays": max(0, (now.replace(day=1) - timedelta(days=1)).day - present_days),
        "totalHours": round(total_hours, 1),
        "averageHours": round(total_hours / max(present_days, 1), 1)
    }

@app.post("/api/attendance/clock")
@require_auth
def clock():
    """Manual clock in or clock out for the authenticated user."""
    body = request.get_json(silent=True) or {}
    employee_id = body.get("employeeId", request.identity.get("sub"))
    if not employee_id:
        return {"error": "Employee ID is required."}, 400
    emp = employee_by_id(employee_id)
    if not emp or emp.get("status") != "active":
        return {"error": "Employee not found or inactive."}, 404
    key = f"{employee_id}_{utcnow().date().isoformat()}"
    ref = db().collection("attendance").document(key)
    existing = ref.get()
    if existing.exists and not existing.to_dict().get("clockOut"):
        ref.update({"clockOut": iso(), "status": "completed"})
        audit("attendance.clock_out", employee_id, "Manual clock out")
        return {"status": "clocked_out", "clockOut": iso(), "employeeId": employee_id}
    ref.set({"employeeId": employee_id, "date": utcnow().date().isoformat(), "clockIn": iso(), "clockOut": None, "status": "present"})
    audit("attendance.clock_in", employee_id, "Manual clock in")
    return {"status": "clocked_in", "clockIn": iso(), "employeeId": employee_id}

def seed_firebase_test_employee():
    """Explicit, opt-in Firestore connection test requested during development."""
    if os.getenv("SEED_FIREBASE_TEST_USER", "false").lower() != "true": return
    db().collection("employees").document("EMP001").set({"employeeId": "EMP001", "name": "Test User", "status": "inactive", "createdAt": iso()})
    print("Connected Successfully")

if __name__ == "__main__": app.run(port=5000, debug=True)
