"""Firebase Admin initialization helpers."""

import json
import os

import firebase_admin
from firebase_admin import credentials, firestore


def get_firestore_client():
    if not firebase_admin._apps:
        raw_credentials = os.getenv("FIREBASE_CREDENTIALS")
        if not raw_credentials:
            raise RuntimeError("FIREBASE_CREDENTIALS environment variable is missing.")
        try:
            credential_data = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            raise RuntimeError("FIREBASE_CREDENTIALS must contain valid Firebase service account JSON.") from exc
        firebase_admin.initialize_app(credentials.Certificate(credential_data))
    return firestore.client()
