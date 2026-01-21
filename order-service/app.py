import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class UpstreamUnavailable(Exception):
    service: str
    details: str


@dataclass(frozen=True)
class UpstreamBadResponse(Exception):
    service: str
    status_code: int
    body: str


def create_app() -> Flask:
    app = Flask(__name__)

    db_path = os.environ.get("DB_PATH", "/data/order-service.db")
    user_service_url = os.environ.get("USER_SERVICE_URL", "http://user-service:5000")
    notification_service_url = os.environ.get(
        "NOTIFICATION_SERVICE_URL", "http://notification-service:5001"
    )
    http_timeout = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "2"))

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
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT NOT NULL,
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
        if isinstance(err, UpstreamUnavailable):
            return (
                jsonify(
                    {
                        "error": "Service Unavailable",
                        "message": f"{err.service} is unavailable.",
                        "details": err.details,
                    }
                ),
                503,
            )
        if isinstance(err, UpstreamBadResponse):
            return (
                jsonify(
                    {
                        "error": "Bad Gateway",
                        "message": f"{err.service} returned an unexpected response.",
                        "status_code": err.status_code,
                        "body": err.body[:1000],
                    }
                ),
                502,
            )
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

    def fetch_user(user_id: int) -> dict | None:
        try:
            resp = requests.get(f"{user_service_url}/users/{user_id}", timeout=http_timeout)
        except requests.RequestException as exc:
            raise UpstreamUnavailable("user-service", str(exc)) from exc

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise UpstreamBadResponse("user-service", resp.status_code, resp.text)
        return resp.json()

    def send_notification(*, user_id: int, order_id: int, message: str) -> tuple[bool, str | None]:
        payload = {"user_id": user_id, "order_id": order_id, "message": message}
        try:
            resp = requests.post(
                f"{notification_service_url}/notify",
                json=payload,
                timeout=http_timeout,
            )
        except requests.RequestException as exc:
            return False, str(exc)

        if resp.status_code not in (200, 201):
            return False, f"Unexpected status {resp.status_code}: {resp.text[:500]}"
        return True, None

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/orders")
    def create_order():
        body = request.get_json(silent=True) or {}
        user_id = body.get("user_id")
        item = (body.get("item") or "").strip()
        amount = body.get("amount")

        if user_id is None or not item or amount is None:
            return (
                jsonify(
                    {
                        "error": "Bad Request",
                        "message": "Fields 'user_id', 'item', and 'amount' are required.",
                    }
                ),
                400,
            )

        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Bad Request", "message": "Field 'user_id' must be an integer."}), 400

        try:
            amount_int = int(amount)
        except (TypeError, ValueError):
            return jsonify({"error": "Bad Request", "message": "Field 'amount' must be an integer."}), 400
        if amount_int <= 0:
            return jsonify({"error": "Bad Request", "message": "Field 'amount' must be > 0."}), 400

        user = fetch_user(user_id_int)
        if user is None:
            return jsonify({"error": "Bad Request", "message": "User does not exist."}), 400

        conn = get_db()
        cur = conn.execute(
            "INSERT INTO orders (user_id, item, amount, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id_int, item, amount_int, "created", utc_now_iso()),
        )
        conn.commit()

        order_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, user_id, item, amount, status, created_at FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        order = dict(row)

        ok, err = send_notification(
            user_id=user_id_int,
            order_id=order_id,
            message=f"Order #{order_id} created for user #{user_id_int}.",
        )
        order["notification_sent"] = ok
        if err:
            order["notification_error"] = err

        return jsonify(order), 201

    @app.get("/orders")
    def list_orders():
        conn = get_db()
        rows = conn.execute(
            "SELECT id, user_id, item, amount, status, created_at FROM orders ORDER BY id DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/orders/<int:order_id>")
    def get_order(order_id: int):
        conn = get_db()
        row = conn.execute(
            "SELECT id, user_id, item, amount, status, created_at FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Not Found", "message": "Order not found."}), 404
        return jsonify(dict(row))

    @app.put("/orders/<int:order_id>")
    def update_order(order_id: int):
        body = request.get_json(silent=True) or {}
        status = body.get("status")
        item = body.get("item")
        amount = body.get("amount")

        conn = get_db()
        existing = conn.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()
        if existing is None:
            return jsonify({"error": "Not Found", "message": "Order not found."}), 404

        fields: list[str] = []
        values: list[object] = []

        if status is not None:
            status = str(status).strip()
            if not status:
                return jsonify({"error": "Bad Request", "message": "Field 'status' cannot be empty."}), 400
            fields.append("status = ?")
            values.append(status)

        if item is not None:
            item = str(item).strip()
            if not item:
                return jsonify({"error": "Bad Request", "message": "Field 'item' cannot be empty."}), 400
            fields.append("item = ?")
            values.append(item)

        if amount is not None:
            try:
                amount_int = int(amount)
            except (TypeError, ValueError):
                return jsonify({"error": "Bad Request", "message": "Field 'amount' must be an integer."}), 400
            if amount_int <= 0:
                return jsonify({"error": "Bad Request", "message": "Field 'amount' must be > 0."}), 400
            fields.append("amount = ?")
            values.append(amount_int)

        if not fields:
            return jsonify({"error": "Bad Request", "message": "No updatable fields provided."}), 400

        values.append(order_id)
        conn.execute(f"UPDATE orders SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()

        row = conn.execute(
            "SELECT id, user_id, item, amount, status, created_at FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        return jsonify(dict(row))

    @app.delete("/orders/<int:order_id>")
    def delete_order(order_id: int):
        conn = get_db()
        cur = conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Not Found", "message": "Order not found."}), 404
        return "", 204

    init_db()
    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", "5002"))
    app.run(host="0.0.0.0", port=port)
