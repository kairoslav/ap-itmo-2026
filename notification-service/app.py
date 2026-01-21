import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_app() -> Flask:
    app = Flask(__name__)

    db_path = os.environ.get("DB_PATH", "/data/notification-service.db")

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            g.db = conn
        return g.db

    def init_db() -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    order_id INTEGER,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """.strip()
            )
            conn.commit()
        finally:
            conn.close()

    @app.teardown_appcontext
    def close_db(_exc: BaseException | None) -> None:
        conn: sqlite3.Connection | None = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(Exception)
    def handle_error(err: Exception):  # type: ignore[override]
        if isinstance(err, HTTPException):
            return (
                jsonify(
                    {
                        "error": err.name,
                        "message": err.description,
                    }
                ),
                err.code,
            )
        return jsonify({"error": "Internal Server Error", "message": str(err)}), 500

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/notify")
    def notify():
        body = request.get_json(silent=True) or {}
        message = (body.get("message") or "").strip()
        if not message:
            return jsonify({"error": "Bad Request", "message": "Field 'message' is required."}), 400

        user_id = body.get("user_id")
        order_id = body.get("order_id")

        conn = get_db()
        cur = conn.execute(
            "INSERT INTO notifications (user_id, order_id, message, created_at) VALUES (?, ?, ?, ?)",
            (user_id, order_id, message, utc_now_iso()),
        )
        conn.commit()

        notification_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, user_id, order_id, message, created_at FROM notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()

        print(f"[notification-service] {row['created_at']} user_id={row['user_id']} order_id={row['order_id']} message={row['message']}")
        return jsonify(dict(row)), 201

    @app.get("/notifications")
    def list_notifications():
        conn = get_db()
        rows = conn.execute(
            "SELECT id, user_id, order_id, message, created_at FROM notifications ORDER BY id DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    init_db()
    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port)
