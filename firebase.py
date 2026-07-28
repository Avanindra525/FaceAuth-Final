import os
import json
import FaceAu_admin
from FaceAu_admin import credentials

FaceAu_json = os.getenv("FaceAu_CREDENTIALS")

cred = credentials.Certificate(json.loads(FaceAu_json))

if not FaceAu_admin._apps:
    FaceAu_admin.initialize_app(cred)