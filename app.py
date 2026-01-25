from flask import Flask, jsonify, request, send_file
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)


# ---------------- PATHS ----------------
APP_NAME = "MiniStore"



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "database.db")


APP_PIN = "1234"



# ---------------- DB ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY,
            price INTEGER,
            qty INTEGER
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            sale_id TEXT PRIMARY KEY,
            date TEXT,
            total INTEGER
        )
    """)

    db.commit()
    db.close()


init_db()




def require_pin(req):
    return req.headers.get("X-PIN") == APP_PIN

# ---------------- HEALTH ----------------
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

# ---------------- PIN ----------------
@app.route("/api/unlock", methods=["POST"])
def unlock():
    pin = request.json.get("pin")
    if pin == APP_PIN:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


# ---------------- PRODUCTS ----------------
@app.route("/api/products", methods=["GET", "POST"])
def products():
    if not require_pin(request):
        return jsonify({"error": "PIN required"}), 403
    
    db = get_db()

    if request.method == "POST":
        if not require_pin(request):
            return jsonify({"error": "PIN required"}), 403

        db.execute("DELETE FROM products")
        for p in request.json:
            db.execute(
                "INSERT INTO products VALUES (?, ?, ?)",
                (p["name"], p["price"], p["qty"])
            )
        db.commit()
        db.close()
        return jsonify({"ok": True})

    rows = db.execute("SELECT * FROM products").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

# ---------------- SALES ----------------
@app.route("/api/sales", methods=["POST"])
def save_sale():
    if not require_pin(request):
        return jsonify({"error": "PIN required"}), 403

    data = request.json or {}
    sale_id = data.get("sale_id")
    total = data.get("total", 0)
    items = data.get("items", [])

    if not sale_id:
        return jsonify({"error": "sale_id required"}), 400

    if total <= 0 or not items:
        return jsonify({"ignored": True})

    db = get_db()

    # 🛑 DUPLICATE CHECK
    exists = db.execute(
        "SELECT 1 FROM sales WHERE sale_id = ?",
        (sale_id,)
    ).fetchone()

    if exists:
        db.close()
        print("⚠️ Duplicate sale ignored:", sale_id)
        return jsonify({"duplicate": True})

    try:
        # Reduce stock
        for item in items:
            name = item["name"]
            qty = int(item["qty"])

            row = db.execute(
                "SELECT qty FROM products WHERE name = ?",
                (name,)
            ).fetchone()

            if not row or row["qty"] < qty:
                raise Exception(f"Not enough stock for {name}")

            db.execute(
                "UPDATE products SET qty = qty - ? WHERE name = ?",
                (qty, name)
            )

        # Save sale
        db.execute(
            "INSERT INTO sales (sale_id, date, total) VALUES (?, ?, ?)",
            (sale_id, datetime.now().strftime("%Y-%m-%d"), total)
        )

        db.commit()
        return jsonify({"saved": True})

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400

    finally:
        db.close()


@app.route("/api/sales/daily")
def daily_sales():
    db = get_db()
    rows = db.execute("""
        SELECT date, SUM(total) total
        FROM sales GROUP BY date ORDER BY date DESC
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/sales/monthly")
def monthly_sales():
    db = get_db()
    rows = db.execute("""
        SELECT substr(date,1,7) month, SUM(total) total
        FROM sales GROUP BY month ORDER BY month DESC
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

# ---------------- FILES ----------------
@app.route("/api/backup")
def backup():
    if not require_pin(request):
        return jsonify({"error": "PIN required"}), 403
    return send_file(DB_PATH, as_attachment=True)

@app.route("/api/sales/monthly.csv")
def monthly_csv():
    db = get_db()
    rows = db.execute("""
        SELECT substr(date,1,7) month, SUM(total) total
        FROM sales GROUP BY month
    """).fetchall()
    db.close()

    csv = "month,total\n"
    for r in rows:
        csv += f"{r['month']},{r['total']}\n"

    path = os.path.join(DATA_DIR, "monthly.csv")
    with open(path, "w") as f:
        f.write(csv)

    return send_file(path, as_attachment=True)

# ---------------- START ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055)
