# Deployment Stabilization TODO

## Backend / Deployment
- [ ] 1. `scripts/setup_models.py` — provision + validate bundled `buffalo_s` model with readable diagnostics (no network).
- [ ] 2. `render.yaml` — buildCommand runs `setup_models.py` before pip install; keep `buffalo_s` consistent.
- [ ] 3. `.gitignore` — allow committing the 2 required ONNX files; ignore unnecessary large model files; ignore all local env files.
- [ ] 4. `services/biometrics.py` — add `diagnose_models()`; use it for startup diagnostics and readable errors.
- [ ] 5. `app.py` — standard `{success,message,data}` JSON envelope; public register/update-face; `exists` check; `lastLogin` update; enhanced startup diagnostics.
- [ ] 6. `firebase.py` — confirm single init; add init logging; never per-request init.

## Frontend
- [ ] 7. `web/lib/api.ts` — unwrap `data` from standard envelope; parse `message` for errors; add register helpers.
- [ ] 8. `web/app/page.tsx` — landing page (Home) with Login + Register Employee; redirect authed users to dashboard.
- [ ] 9. `web/app/register/page.tsx` — NEW public Register / Update Face page (Employee ID, Name, Department, 5-frame capture, quality guidance).
- [ ] 10. `web/app/dashboard/page.tsx` — NEW employee dashboard (Name, ID, Department, Last Login, Logout).
- [ ] 11. `web/app/console/page.tsx` — move existing admin console here (preserve functionality; update redirects).
- [ ] 12. `web/app/login/page.tsx` — spinner, disable while verifying, Register link, redirect to /dashboard.
- [ ] 13. `web/app/globals.css` — landing / register / dashboard / spinner styles.

## Security / Cleanup
- [ ] 14. Remove `web/.env.local` from git tracking; ensure serviceAccountKey.json and all local env files ignored.
- [ ] 15. Remove dead code / duplicate init; confirm single InsightFace + Firebase initialization.

## Verification
- [ ] 16. Verify Python files import; run `scripts/setup_models.py`.
- [ ] 17. Verify frontend builds (`npm run build`).
- [ ] 18. Full audit — no `buffalo_l` references, no runtime downloads, single model config.

