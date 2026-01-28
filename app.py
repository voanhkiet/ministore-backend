from flask import Flask, jsonify, request, send_file
import sqlite3
import os
import sys
from datetime import datetime

app = Flask(__name__)
def log(msg):
    print(f"[MINISTORE] {datetime.now().isoformat()} | {msg}", flush=True)

# ---------------- PATHS ----------------
APP_NAME = "MiniStore"



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "database.db")


APP_PIN = "1234"
if os.environ.get("RESET_DB") == "1":
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)



# ---------------- DB ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    log("🗄️ Initializing database")
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    db.execute("""
        INSERT OR IGNORE INTO meta (key, value)
        VALUES ('schema_version', '2')
    """)

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





def migrate_db():
    db = get_db()

    # Ensure meta table exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    row = db.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()

    current_version = int(row["value"]) if row else 1

    if current_version < 2:
        log("🛠 Migrating DB to schema v2")

        cols = db.execute("PRAGMA table_info(sales)").fetchall()
        col_names = [c["name"] for c in cols]

        if "sale_id" not in col_names:
            db.execute("ALTER TABLE sales ADD COLUMN sale_id TEXT")

        db.execute("""
            UPDATE meta SET value = '2' WHERE key = 'schema_version'
        """)

    db.commit()
    db.close()


log("🚀 App starting")
init_db()
log("✅ Database initialized")

migrate_db()



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
    log("📦 Products fetched")
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
        log(f"📦 Products updated count={len(request.json)}")

        return jsonify({"ok": True})

    rows = db.execute("SELECT * FROM products").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

# ---------------- SALES ----------------
@app.route("/api/sales", methods=["POST"])
def save_sale():
    log("📥 /api/sales called")
    if not require_pin(request):
        return jsonify({"error": "PIN required"}), 403

    data = request.get_json(force=True)
    sale_id = data.get("sale_id")
    total = int(data.get("total", 0))
    items = data.get("items", [])

    log(f"🧾 SALE PAYLOAD sale_id={sale_id} total={total}")
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
        log(f"🔁 DUPLICATE sale ignored: {sale_id}")
        db.close()
        return jsonify({"duplicate": True})

    try:
        # 🔻 Check stock 
        for item in items:
            row = db.execute(
                "SELECT qty FROM products WHERE name = ?",
                (item["name"],)
            ).fetchone()

            if not row:
                raise Exception(f"Product not found: {item['name']}")

            if row["qty"] < item["qty"]:
                log(f"❌ STOCK ERROR {item['name']} have={row['qty']} need={item['qty']}")
                raise Exception(f"Not enough stock for {item['name']}")


        # 🔻 Apply stock
        for item in items:
            db.execute(
                "UPDATE products SET qty = qty - ? WHERE name = ?",
                (item["qty"], item["name"])
            )

        # 💾 Save sale

        db.execute(
            "INSERT INTO sales (sale_id, date, total) VALUES (?, ?, ?)",
            (sale_id, datetime.now().strftime("%Y-%m-%d"), total)
        )

        db.commit()
        log(f"✅ SALE SAVED sale_id={sale_id} total={total}")

        return jsonify({"saved": True})

    except Exception as e:
        db.rollback()
        log(f"🔥 SALE FAILED sale_id={sale_id} error={e}")
        return jsonify({"error": str(e)}), 400

    finally:
        db.close()


@app.route("/api/debug/sales")
def debug_sales():
    if not require_pin(request):
        return jsonify({"error": "PIN required"}), 403

    db = get_db()
    rows = db.execute("""
        SELECT sale_id, date, total
        FROM sales
        ORDER BY date DESC
        LIMIT 20
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])



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
