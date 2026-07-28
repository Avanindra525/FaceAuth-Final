import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)

db = firestore.client()
print("✅ Firebase connected successfully!")

doc = db.collection("test").document("connection")
doc.set({"status": "Connected"})
print("✅ Test document written to Firestore!")