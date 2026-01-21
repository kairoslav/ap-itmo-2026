import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_app() -> Flask:
    app = Flask(__name__)

    db_path = os.environ.get("DB_PATH", "/data/user-service.db")

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
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
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

    @app.post("/users")
    def create_user():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        email = (body.get("email") or "").strip()
        if not name or not email:
            return (
                jsonify(
                    {
                        "error": "Bad Request",
                        "message": "Fields 'name' and 'email' are required.",
                    }
                ),
                400,
            )

        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO users (name, email, created_at) VALUES (?, ?, ?)",
                (name, email, utc_now_iso()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return (
                jsonify(
                    {
                        "error": "Conflict",
                        "message": "User with this email already exists.",
                    }
                ),
                409,
            )

        user_id = cur.lastrowid
        row = conn.execute("SELECT id, name, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        return jsonify(dict(row)), 201

    @app.get("/users")
    def list_users():
        conn = get_db()
        rows = conn.execute("SELECT id, name, email, created_at FROM users ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/users/<int:user_id>")
    def get_user(user_id: int):
        conn = get_db()
        row = conn.execute("SELECT id, name, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Not Found", "message": "User not found."}), 404
        return jsonify(dict(row))

    @app.put("/users/<int:user_id>")
    def update_user(user_id: int):
        body = request.get_json(silent=True) or {}
        name = body.get("name")
        email = body.get("email")

        conn = get_db()
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Not Found", "message": "User not found."}), 404

        fields: list[str] = []
        values: list[object] = []

        if name is not None:
            name = str(name).strip()
            if not name:
                return jsonify({"error": "Bad Request", "message": "Field 'name' cannot be empty."}), 400
            fields.append("name = ?")
            values.append(name)

        if email is not None:
            email = str(email).strip()
            if not email:
                return jsonify({"error": "Bad Request", "message": "Field 'email' cannot be empty."}), 400
            fields.append("email = ?")
            values.append(email)

        if not fields:
            return jsonify({"error": "Bad Request", "message": "No updatable fields provided."}), 400

        values.append(user_id)
        try:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "Conflict", "message": "User with this email already exists."}), 409

        updated = conn.execute("SELECT id, name, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        return jsonify(dict(updated))

    @app.delete("/users/<int:user_id>")
    def delete_user(user_id: int):
        conn = get_db()
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Not Found", "message": "User not found."}), 404
        return "", 204

    init_db()
    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
