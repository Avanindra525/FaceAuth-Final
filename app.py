"""FaceAuth Enterprise Flask API.

Biometric routes fail closed: Firestore and InsightFace must be
available. There is no image-hash or mock-recognition path in this service.
"""
import csv
import io
import os
import secrets
import time
import traceback
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from firebase_admin import firestore
import jwt
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException

from firebase import get_firestore_client
from services.biometrics import BiometricError, MODELS_DIR, PROJECT_ROOT, _model_available, average_embeddings, cosine_similarity, get_engine

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)

app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", str(8 * 1024 * 1024)))
CORS(
    app,
    resources={r"/api/*": {"origins": [
        origin.strip() for origin in os.getenv(
            "CORS_ORIGINS",
            r"http://localhost:3000,http://localhost:5173,https://.*\.vercel\.app,https://.*\.onrender\.com"
        ).split(",") if origin.strip()
    ]}},
    supports_credentials=True,
)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"], storage_uri="memory://")

JWT_SECRET = os.getenv("JWT_SECRET")
THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.50"))
MODEL_VERSION = os.getenv("INSIGHTFACE_MODEL", "buffalo_s")

for name in ("INSIGHTFACE_MODEL", "JWT_SECRET", "SECRET_KEY", "DATABASE_URL", "FIREBASE_CREDENTIALS"):
    if not os.getenv(name):
        app.logger.warning("%s is not configured.", name)

@app.route("/")
def home():
    return {
        "status": "running",
        "project": "FaceAuth Enterprise",
        "message": "Backend is live!"
    }



def utcnow(): return datetime.now(timezone.utc)
def iso(): return utcnow().isoformat()

def db():
    return get_firestore_client()

def clean_employee(value):
    value = dict(value)
    value.pop("embeddingVector", None)
    value.pop("faceImage", None)
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

def require_request_auth():
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return {"error": "Authentication is required."}, 401
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        session = db().collection("sessions").document(claims["sid"]).get()
        if not session.exists or not session.to_dict().get("active"):
            raise jwt.InvalidTokenError
    except Exception:
        return {"error": "Your session is invalid or expired."}, 401
    request.identity = claims
    return None

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

def best_face_match(embedding):
    candidates = [
        (cosine_similarity(embedding, employee["embeddingVector"]), employee)
        for employee in all_active_employees()
        if employee.get("embeddingVector")
    ]
    return max(candidates, key=lambda result: result[0]) if candidates else (0.0, None)

def attendance(employee_id):
    employee = employee_by_id(employee_id) or {}
    key = f"{employee_id}_{utcnow().date().isoformat()}"; ref = db().collection("attendance").document(key); existing = ref.get()
    if existing.exists and not existing.to_dict().get("clockOut"):
        ref.update({"clockOut": iso(), "status": "completed"}); return "clock_out"
    ref.set({"employeeId": employee_id, "name": employee.get("name"), "date": utcnow().date().isoformat(), "clockIn": iso(), "clockOut": None, "status": "present"})
    return "clock_in"

@app.errorhandler(BiometricError)
def biometric_error(error):
    app.logger.warning("BiometricError: %s", error, exc_info=True)
    try:
        security("biometric_rejected", str(error), severity="warning")
    except Exception:
        app.logger.exception("Failed to write biometric rejection log.")
    return jsonify({"error": str(error)}), 422

@app.errorhandler(RuntimeError)
def runtime_error(error):
    app.logger.exception(error)
    return jsonify({"error": "Biometric service unavailable"}), 503

@app.errorhandler(Exception)
def unhandled_error(error):
    if isinstance(error, HTTPException):
        return jsonify({"error": error.description}), error.code
    app.logger.error("Unhandled %s: %s\n%s", type(error).__name__, error, traceback.format_exc())
    return jsonify({"error": "Server unavailable."}), 500


@app.get("/health")
def health():
    db()  # Verify Firestore connection

    # Check if model is available WITHOUT calling get_engine() or FaceAnalysis
    biometric_ok = _model_available(os.getenv("INSIGHTFACE_MODEL", "buffalo_s"))

    return {
        "status": "ok",
        "biometric": biometric_ok,
        "projectRoot": PROJECT_ROOT,
        "modelsDir": MODELS_DIR,
        "modelVersion": MODEL_VERSION,
        "threshold": THRESHOLD
    }


@app.route("/api/employees", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def employees():
    auth_error = require_request_auth()
    if auth_error:
        return auth_error
    if request.method == "GET":
        department, search = request.args.get("department"), request.args.get("search", "").lower()
        page, size = max(1, int(request.args.get("page", 1))), min(100, max(1, int(request.args.get("pageSize", 20))))
        rows = [e for e in all_active_employees() if (not department or e.get("department") == department) and
                (not search or search in (e.get("name", "") + e.get("employeeId", "") + e.get("email", "")).lower())]
        return {"items": [clean_employee(e) for e in rows[(page-1)*size:page*size]], "total": len(rows), "page": page, "pageSize": size}
    body = request.get_json(silent=True) or {}
    required = ("employeeId", "name", "email", "department", "designation", "phone")
    if any(not str(body.get(key, "")).strip() for key in required): return {"error": "Employee ID, name, email, department, designation and phone are required."}, 400
    existing_employee = employee_by_id(body["employeeId"])
    frames = body.get("frames", [])
    engine = get_engine(); results = [engine.extract(image) for image in frames]
    embedding = average_embeddings([result.embedding for result in results])
    if existing_employee:
        if not body.get("updateFace"):
            return {"error": "Face already exists. Use Update Face."}, 409
        updates = {"embeddingVector": embedding, "modelVersion": MODEL_VERSION, "lastUpdated": iso(), "faceImage": frames[0] if frames else None}
        db().collection("employees").document(body["employeeId"]).update(updates)
        audit("employee.face_updated", "system", body["employeeId"])
        return clean_employee({**existing_employee, **updates})
    employee = {key: body[key].strip() for key in required}
    employee.update({"status": "active", "role": body.get("role", "Employee"), "createdAt": iso(), "lastUpdated": iso(),
                     "documentId": employee["employeeId"], "embeddingVector": embedding, "modelVersion": MODEL_VERSION,
                     "faceImage": frames[0] if frames else None})
    db().collection("employees").document(employee["employeeId"]).set(employee)
    audit("employee.enrolled", "system", employee["employeeId"])
    log("notifications", {"type": "employee_registered", "employeeId": employee["employeeId"], "message": "Enrollment completed."})
    return clean_employee(employee), 201

@app.route("/api/employees/<employee_id>", methods=["GET", "PATCH", "DELETE"])
def employee(employee_id):
    auth_error = require_request_auth()
    if auth_error:
        return auth_error
    current = employee_by_id(employee_id)
    if not current: return {"error": "Employee was not found."}, 404
    if request.method == "GET": return clean_employee(current)
    if request.method == "DELETE":
        db().collection("employees").document(employee_id).update({"status": "deleted", "lastUpdated": iso()}); audit("employee.deleted", "system", employee_id)
        return {"success": True, "deleted": True, "employeeId": employee_id}
    changes = {key: value for key, value in (request.get_json(silent=True) or {}).items() if key in {"name", "email", "department", "designation", "phone", "status", "role"}}
    changes["lastUpdated"] = iso(); db().collection("employees").document(employee_id).update(changes); audit("employee.updated", "system", employee_id)
    return clean_employee({**current, **changes})

@app.post("/api/auth/verify")
@limiter.limit("8 per minute")
def verify_auth():
    """Face verification endpoint with full error containment.

    Every execution path returns JSON. The worker never crashes.
    A 10-second wall-clock guard prevents hung biometric requests.
    """
    started = time.perf_counter()
    deadline = started + 10.0

    def elapsed_ms():
        return round((time.perf_counter() - started) * 1000)

    def remaining_seconds():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("Biometric verification timeout.")
        return remaining

    def log_stage(stage):
        app.logger.info("verify_auth %s elapsed_ms=%s", stage, elapsed_ms())

    def finish(payload, status=200):
        log_stage("response returned")
        app.logger.info("Verify request completed elapsed_ms=%s status=%s", elapsed_ms(), status)
        return payload, status

    def fail(payload, status):
        app.logger.warning("Verify request failed elapsed_ms=%s status=%s payload=%s", elapsed_ms(), status, payload)
        return payload, status

    app.logger.info("Verify request started")
    try:
        log_stage("request received")
        body = request.get_json(silent=True) or {}
        log_stage("json parsed")
        employee_id = (body.get("employeeId") or body.get("staffId") or "").strip()

        if not employee_id:
            return fail({"success": False, "verified": False, "reason": "Employee ID is required."}, 400)

        log_stage("employee lookup started")
        emp = employee_by_id(employee_id)
        log_stage("employee lookup completed")
        remaining_seconds()
        if not emp or emp.get("status") != "active":
            security("failed_login", "Employee not found or inactive", employee_id)
            return fail({"success": False, "verified": False, "reason": "Employee not found."}, 401)

        if not emp.get("embeddingVector"):
            return fail({"success": False, "verified": False, "reason": "Registered face is missing. Update Face."}, 409)

        # get_engine() may raise RuntimeError if model is missing / OOM — this is caught below
        log_stage("get_engine started")
        engine = get_engine(timeout_seconds=remaining_seconds())
        log_stage("get_engine completed")

        log_stage("face extract started")
        face = engine.extract(body.get("image", ""), timeout_seconds=remaining_seconds())
        log_stage("face extract completed")

        log_stage("cosine similarity started")
        similarity = cosine_similarity(face.embedding, emp["embeddingVector"])
        log_stage("cosine similarity completed")
        elapsed = elapsed_ms()
        verified = similarity >= THRESHOLD

        history = {"employeeId": employee_id, "timestamp": iso(), "device": request.user_agent.string,
                   "browser": request.user_agent.browser,
                   "ip": request.headers.get("X-Forwarded-For", request.remote_addr), "location": None,
                   "authenticationTime": elapsed,
                   "similarityScore": similarity, "status": "success" if verified else "failed"}
        log_stage("login history write started")
        log("login_history", history)
        log_stage("login history write completed")
        remaining_seconds()

        if not verified:
            security("failed_login", f"Low similarity: {similarity:.3f}", employee_id)
            return fail({"success": False, "verified": False, "score": similarity, "similarity": similarity,
                    "reason": "Face mismatch."}, 401)

        log_stage("attendance update started")
        event = attendance(employee_id)
        log_stage("attendance update completed")
        remaining_seconds()
        log_stage("token issue started")
        tokens = issue_tokens(emp, bool(body.get("rememberMe")))
        log_stage("token issue completed")
        remaining_seconds()
        log_stage("audit write started")
        audit("auth.success", employee_id, f"score={similarity:.3f}")
        log_stage("audit write completed")
        return finish({"success": True, "matched": True, "verified": True, "score": similarity,
                "employee": clean_employee(emp), "similarity": similarity,
                "confidence": round(similarity * 100, 1),
                "verificationTime": elapsed, "attendance": event, "tokens": tokens})

    except TimeoutError as exc:
        app.logger.error("Verify request failed: timeout elapsed_ms=%s error=%s", elapsed_ms(), exc, exc_info=True)
        return {"success": False, "message": "Biometric verification timeout."}, 503
    except RuntimeError as exc:
        app.logger.error("Verify request failed: RuntimeError elapsed_ms=%s error=%s", elapsed_ms(), exc, exc_info=True)
        return {"success": False, "verified": False, "reason": "Biometric service unavailable."}, 503
    except BiometricError as exc:
        app.logger.warning("Verify request failed: BiometricError elapsed_ms=%s error=%s", elapsed_ms(), exc)
        return {"success": False, "verified": False, "reason": str(exc)}, 422
    except Exception as exc:
        app.logger.error("Verify request failed: unexpected elapsed_ms=%s error=%s", elapsed_ms(), exc, exc_info=True)
        return {"success": False, "verified": False, "reason": "Server error."}, 500

@app.post("/api/identity/verify")
@limiter.limit("10 per minute")
def verify_identity():
    started = time.perf_counter()
    try:
        face = get_engine().extract((request.get_json(silent=True) or {}).get("image", ""))
        similarity, employee = best_face_match(face.embedding)
        found = employee is not None and similarity >= THRESHOLD
        audit("identity.lookup", "system", f"found={found}; score={similarity:.3f}")
        return {"found": found, "similarity": similarity, "confidence": round(similarity * 100, 1),
                "verificationTime": round((time.perf_counter() - started) * 1000),
                "employee": clean_employee(employee) if found else None}
    except RuntimeError as exc:
        app.logger.error("verify_identity RuntimeError: %s", exc)
        return {"found": False, "error": "Biometric service unavailable."}, 503
    except BiometricError as exc:
        app.logger.warning("verify_identity BiometricError: %s", exc)
        return {"found": False, "error": str(exc)}, 422
    except Exception as exc:
        app.logger.error("verify_identity unexpected error: %s", exc, exc_info=True)
        return {"found": False, "error": "Server error."}, 500

@app.get("/api/dashboard")
@require_auth
def dashboard():
    today = utcnow().date().isoformat(); employees = all_active_employees()
    logins = [d.to_dict() for d in db().collection("login_history").stream()]; alerts = [d.to_dict() for d in db().collection("security_logs").stream()]
    attendance_rows = [d.to_dict() for d in db().collection("attendance").stream()]
    successes = [row for row in logins if row.get("status") == "success"]; failures = [row for row in logins if row.get("status") == "failed"]
    avg = round(sum(row.get("authenticationTime", 0) for row in successes) / len(successes), 1) if successes else 0
    current_user = clean_employee(employee_by_id(request.identity.get("sub")) or {})
    user_logins = [row for row in successes if row.get("employeeId") == request.identity.get("sub")]
    return {"metrics": {"employees": len(employees), "todayAttendance": sum(1 for d in attendance_rows if d.get("date") == today),
            "liveUsers": sum(1 for d in db().collection("sessions").stream() if d.to_dict().get("active")), "failedAttempts": len(failures),
            "securityAlerts": len(alerts), "successRate": round((len(successes)/len(logins))*100, 1) if logins else 0, "averageLoginTime": avg},
            "currentUser": current_user,
            "lastLogin": sorted(user_logins, key=lambda row: row.get("timestamp", ""), reverse=True)[0] if user_logins else None,
            "recentLogins": sorted(logins, key=lambda row: row.get("timestamp", ""), reverse=True)[:10],
            "recentAttendance": sorted(attendance_rows, key=lambda row: row.get("clockIn", ""), reverse=True)[:10],
            "recentActivity": sorted(logins + alerts, key=lambda row: row.get("timestamp", ""), reverse=True)[:10]}

@app.get("/api/audit")
@require_auth
def audits(): return jsonify([doc.to_dict() for doc in db().collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(100).stream()])
@app.get("/api/security-logs")
@require_auth
def security_logs(): return jsonify([doc.to_dict() for doc in db().collection("security_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(100).stream()])

@app.get("/api/reports/employees.csv")
@require_auth
def export_employees():
    output = io.StringIO(); columns = ["employeeId", "name", "email", "department", "designation", "phone", "status", "createdAt"]
    writer = csv.DictWriter(output, fieldnames=columns); writer.writeheader(); writer.writerows(clean_employee(e) for e in all_active_employees())
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=employees.csv"})

@app.post("/api/auth/logout")
@require_roles("Admin", "HR", "Employee", "Security Officer")
def logout():
    db().collection("sessions").document(request.identity["sid"]).update({"active": False, "endedAt": iso()})
    return {"success": True}

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
    ref.set({"employeeId": employee_id, "name": emp.get("name"), "date": utcnow().date().isoformat(), "clockIn": iso(), "clockOut": None, "status": "present"})
    audit("attendance.clock_in", employee_id, "Manual clock in")
    return {"status": "clocked_in", "clockIn": iso(), "employeeId": employee_id}

def seed_firebase_test_employee():
    """Explicit, opt-in Firestore connection test requested during development."""
    if os.getenv("SEED_FIREBASE_TEST_USER", "false").lower() != "true": return
    db().collection("employees").document("EMP001").set({"employeeId": "EMP001", "name": "Test User", "status": "inactive", "createdAt": iso()})
    print("Connected Successfully")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
