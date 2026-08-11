import sqlite3
from flask import Flask, jsonify, render_template

DB_FILE = "readings.db"

app = Flask(__name__)


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/latest")
def api_latest():
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM readings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {})


@app.route("/api/history")
def api_history():
    limit = 500
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    data = [dict(r) for r in reversed(rows)]
    return jsonify(data)


def main():
    # host="0.0.0.0" makes it reachable from other devices on your network
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()