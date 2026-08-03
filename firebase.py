"""Firebase Admin initialization helpers.

Firebase Admin is initialized exactly once per process. ``get_firestore_client``
is idempotent: the first call initializes the SDK, and every later call reuses
the already-initialized app. It is never initialized on a per-request basis.
"""

import json
import logging
import os

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger("firebase")
logger.setLevel(logging.INFO)


def get_firestore_client():
    if not firebase_admin._apps:
        raw_credentials = os.getenv("FIREBASE_CREDENTIALS")
        if not raw_credentials:
            raise RuntimeError("FIREBASE_CREDENTIALS environment variable is missing.")
        try:
            credential_data = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            raise RuntimeError("FIREBASE_CREDENTIALS must contain valid Firebase service account JSON.") from exc
        logger.info("Initializing Firebase Admin SDK (first call in this process).")
        firebase_admin.initialize_app(credentials.Certificate(credential_data))
    else:
        logger.debug("Reusing already-initialized Firebase Admin SDK.")
    return firestore.client()
