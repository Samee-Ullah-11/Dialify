"""Dialify — turn PDFs, images and spreadsheets of phone numbers into a
click-to-call contact list.

Run:  python app.py   then open http://localhost:5000
- Guests can upload a file and preview extracted contacts, but saving,
  exporting and sharing require a free account.
- Each account sees only its own contacts.
"""
import csv
import io
import os
import re
import secrets
import time
from functools import wraps

from flask import (Flask, Response, jsonify, render_template, request,
                   session)
from werkzeug.security import check_password_hash, generate_password_hash

import db
from extractor import apply_country_code, extract_any, tesseract_available

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SECRET_FILE = os.path.join(BASE_DIR, ".secret_key")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".csv", ".xlsx", ".xlsm"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

# Persistent session secret so logins survive restarts
if os.path.exists(SECRET_FILE):
    app.secret_key = open(SECRET_FILE).read()
else:
    app.secret_key = secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(app.secret_key)

db.init_db()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Please sign in first.", "auth": True}), 401
        return f(*args, **kwargs)
    return wrapper


# ---------- pages ----------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/sw.js")
def service_worker():
    # Served from the root so the service worker's scope covers the whole app
    return app.send_static_file("sw.js")


@app.get("/share/<token>")
def share_page(token):
    owner = db.get_share_owner(token)
    if owner is None:
        return "This share link is invalid or has been revoked.", 404
    contacts = db.list_contacts(owner)
    return render_template("share.html", contacts=contacts)


# ---------- auth ----------

@app.post("/api/register")
def api_register():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Please enter your name."}), 400
    user_id = db.create_user(email, generate_password_hash(password), name)
    if user_id is None:
        return jsonify({"error": "That email is already registered — sign in instead."}), 400
    session["user_id"] = user_id
    return jsonify({"ok": True, "email": email})


@app.post("/api/login")
def api_login():
    data = request.get_json(force=True)
    user = db.get_user_by_email(data.get("email") or "")
    if not user or not check_password_hash(user["password_hash"], data.get("password") or ""):
        return jsonify({"error": "Wrong email or password."}), 400
    session["user_id"] = user["id"]
    return jsonify({"ok": True, "email": user["email"]})


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"user": None, "ocr": tesseract_available()})
    user = db.get_user(session["user_id"])
    if not user:
        session.clear()
        return jsonify({"user": None, "ocr": tesseract_available()})
    return jsonify({
        "user": {"email": user["email"], "name": user["name"], "default_cc": user["default_cc"]},
        "ocr": tesseract_available(),
    })


@app.post("/api/settings")
@login_required
def api_settings():
    data = request.get_json(force=True)
    cc = re.sub(r"\D", "", data.get("default_cc") or "")
    db.set_default_cc(session["user_id"], cc)
    return jsonify({"ok": True})


# ---------- extraction (guests allowed) ----------

@app.post("/api/extract")
def api_extract():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file received."}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        return jsonify({"error": f"Unsupported file type {ext}. Use PDF, image, CSV or Excel."}), 400
    safe_name = re.sub(r"[^\w.-]", "_", os.path.basename(file.filename))
    path = os.path.join(UPLOAD_DIR, f"{int(time.time())}_{safe_name}")
    file.save(path)
    try:
        contacts = extract_any(path)
    except Exception as e:
        return jsonify({"error": f"Could not read file: {e}"}), 500
    return jsonify({"contacts": contacts})


# ---------- contacts (account only) ----------

@app.get("/api/contacts")
@login_required
def api_contacts():
    return jsonify(db.list_contacts(session["user_id"]))


@app.post("/api/contacts/save")
@login_required
def api_save():
    data = request.get_json(force=True)
    user = db.get_user(session["user_id"])
    contacts = data.get("contacts") or []
    for c in contacts:
        c["phone"] = apply_country_code(str(c.get("phone", "")), user["default_cc"])
    contacts = [c for c in contacts if c.get("phone")]
    inserted = db.save_contacts(session["user_id"], contacts, replace=data.get("mode") == "replace")
    return jsonify({"inserted": inserted, "total": len(db.list_contacts(session["user_id"]))})


@app.post("/api/contacts/<int:contact_id>/update")
@login_required
def api_update(contact_id):
    data = request.get_json(force=True)
    if data.get("delete"):
        db.delete_contact(session["user_id"], contact_id)
    else:
        db.update_contact(session["user_id"], contact_id, data)
    return jsonify({"ok": True})


# ---------- share links ----------

@app.post("/api/share")
@login_required
def api_share():
    token = db.create_share(session["user_id"])
    return jsonify({"url": request.url_root.rstrip("/") + "/share/" + token})


@app.post("/api/share/revoke")
@login_required
def api_share_revoke():
    db.revoke_share(session["user_id"])
    return jsonify({"ok": True})


# ---------- exports (account only) ----------

@app.get("/export/csv")
@login_required
def export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Name", "Phone", "Tags", "Status"])
    for c in db.list_contacts(session["user_id"]):
        w.writerow([c["name"], c["phone"], c["tags"], c["status"]])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


@app.get("/export/vcf")
@login_required
def export_vcf():
    lines = []
    for c in db.list_contacts(session["user_id"]):
        name = c["name"] or c["phone"]
        lines += [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"FN:{name}",
            f"TEL;TYPE=CELL:{c['phone']}",
            "END:VCARD",
        ]
    return Response(
        "\r\n".join(lines) + "\r\n",
        mimetype="text/vcard",
        headers={"Content-Disposition": "attachment; filename=contacts.vcf"},
    )


if __name__ == "__main__":
    # host 0.0.0.0 so you can open it from your phone on the same Wi-Fi
    app.run(host="0.0.0.0", port=5000, debug=False)
