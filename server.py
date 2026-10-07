import hashlib
import hmac
import json
import os
import sqlite3
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "orders.sqlite3"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
SITE_URL = os.environ.get("SITE_URL", f"http://localhost:{PORT}").rstrip("/")

BOOKS = [
    {"id": "small-things", "title": "Small Things Like These", "author": "Claire Keegan", "category": "Fiction", "price": 17.00, "rating": "4.9", "badge": "Bookseller pick", "image": "https://images.unsplash.com/photo-1544947950-fa07a98d237f?auto=format&fit=crop&w=480&h=680&q=82"},
    {"id": "wild-quiet", "title": "The Wild Quiet", "author": "Mara Ellison", "category": "Nature", "price": 21.00, "rating": "4.8", "badge": "New arrival", "image": "https://images.unsplash.com/photo-1519682337058-a94d519337bc?auto=format&fit=crop&w=480&h=680&q=82"},
    {"id": "long-way-home", "title": "The Long Way Home", "author": "Louise Erdrich", "category": "Fiction", "price": 18.50, "rating": "4.9", "badge": "Reader favorite", "image": "https://images.unsplash.com/photo-1512820790803-83ca734da794?auto=format&fit=crop&w=480&h=680&q=82"},
    {"id": "quiet-mind", "title": "A Quiet Mind", "author": "Oliver Burkeman", "category": "Mindful living", "price": 19.00, "rating": "4.7", "badge": "Worth keeping", "image": "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?auto=format&fit=crop&w=480&h=680&q=82"},
    {"id": "last-library", "title": "The Last Library", "author": "Freya Sampson", "category": "Fiction", "price": 16.00, "rating": "4.8", "badge": "Bookseller pick", "image": "https://images.unsplash.com/photo-1516979187457-637abb4f9353?auto=format&fit=crop&w=480&h=680&q=82"},
    {"id": "glass-garden", "title": "The Glass Garden", "author": "Tana French", "category": "Mystery", "price": 20.00, "rating": "4.9", "badge": "One more chapter", "image": "https://images.unsplash.com/photo-1519682337058-a94d519337bc?auto=format&fit=crop&w=480&h=680&q=82"},
    {"id": "gather-moss", "title": "Gathering Moss", "author": "Robin Wall Kimmerer", "category": "Nature", "price": 18.00, "rating": "4.9", "badge": "A little wonder", "image": "https://images.unsplash.com/photo-1476275466078-4007374efbbe?auto=format&fit=crop&w=480&h=680&q=82"},
    {"id": "blue-hour", "title": "The Blue Hour", "author": "Paula Hawkins", "category": "Mystery", "price": 22.00, "rating": "4.6", "badge": "New arrival", "image": "https://images.unsplash.com/photo-1509021436665-8f07dbf5bf1d?auto=format&fit=crop&w=480&h=680&q=82"},
]
BOOK_BY_ID = {book["id"]: book for book in BOOKS}


class ApiError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def initialize_database():
    with sqlite3.connect(DATABASE) as database:
        database.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                items TEXT NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                checkout_session TEXT,
                created_at INTEGER NOT NULL
            )"""
        )


def stripe_request(endpoint, form_data):
    request = Request(
        f"https://api.stripe.com/v1/{endpoint}",
        data=urlencode(form_data).encode(),
        headers={
            "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except HTTPError as error:
        detail = json.loads(error.read()).get("error", {}).get("message", "Payment provider rejected the request.")
        raise ApiError(detail, 502) from error
    except (URLError, TimeoutError) as error:
        raise ApiError("Could not reach the payment provider. Please try again.", 502) from error


class StoreHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https://images.unsplash.com data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        super().end_headers()

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 16_384:
                raise ValueError
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            raise ApiError("Please send a valid checkout request.", 400) from None

    def do_GET(self):
        if self.path == "/api/books":
            return self.send_json(200, BOOKS)
        if self.path.startswith("/api/"):
            return self.send_json(404, {"error": "Not found."})
        if self.path == "/":
            self.path = "/E-LIBRARY.HTML"
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/checkout":
            return self.create_checkout()
        if self.path == "/api/webhook":
            return self.handle_webhook()
        return self.send_json(404, {"error": "Not found."})

    def create_checkout(self):
        try:
            payload = self.read_json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items or len(items) > len(BOOKS):
                raise ApiError("Add a book to your bag before checkout.", 400)
            normalized = []
            amount = 0
            for item in items:
                if not isinstance(item, dict) or item.get("id") not in BOOK_BY_ID:
                    raise ApiError("Your bag contains an unavailable book. Please refresh and try again.", 400)
                quantity = item.get("quantity")
                if type(quantity) is not int or quantity < 1 or quantity > 10:
                    raise ApiError("Book quantities must be between 1 and 10.", 400)
                book = BOOK_BY_ID[item["id"]]
                amount += round(book["price"] * 100) * quantity
                normalized.append({"id": book["id"], "title": book["title"], "quantity": quantity, "unit_amount": round(book["price"] * 100)})
            if not STRIPE_SECRET_KEY:
                raise ApiError("Secure checkout is not configured yet. Add STRIPE_SECRET_KEY to the server environment.", 503)

            order_id = f"ord_{os.urandom(12).hex()}"
            form = {
                "mode": "payment",
                "success_url": f"{SITE_URL}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{SITE_URL}/?checkout=cancel",
                "client_reference_id": order_id,
                "metadata[order_id]": order_id,
                "billing_address_collection": "auto",
                "shipping_address_collection[allowed_countries][0]": "US",
            }
            for index, item in enumerate(normalized):
                form[f"line_items[{index}][price_data][currency]"] = "usd"
                form[f"line_items[{index}][price_data][unit_amount]"] = str(item["unit_amount"])
                form[f"line_items[{index}][price_data][product_data][name]"] = item["title"]
                form[f"line_items[{index}][quantity]"] = str(item["quantity"])

            with sqlite3.connect(DATABASE) as database:
                database.execute(
                    "INSERT INTO orders (id, items, amount, currency, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, json.dumps(normalized), amount, "usd", "pending", int(time.time())),
                )
            session = stripe_request("checkout/sessions", form)
            if not session.get("url") or not session.get("id"):
                raise ApiError("The payment provider did not create a checkout session.", 502)
            with sqlite3.connect(DATABASE) as database:
                database.execute("UPDATE orders SET checkout_session = ? WHERE id = ?", (session["id"], order_id))
            return self.send_json(200, {"url": session["url"]})
        except ApiError as error:
            return self.send_json(error.status, {"error": str(error)})
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return self.send_json(502, {"error": "Checkout could not be completed. Please try again."})

    def handle_webhook(self):
        if not STRIPE_WEBHOOK_SECRET:
            return self.send_json(503, {"error": "Webhook signing is not configured."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_000_000:
                raise ValueError
            body = self.rfile.read(length)
            signature_header = self.headers.get("Stripe-Signature", "")
            signature_parts = {}
            for part in signature_header.split(","):
                key, separator, value = part.partition("=")
                if separator:
                    signature_parts.setdefault(key, []).append(value)
            timestamp = int(signature_parts.get("t", ["0"])[0])
            if abs(int(time.time()) - timestamp) > 300:
                raise ValueError
            signed_payload = str(timestamp).encode() + b"." + body
            expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed_payload, hashlib.sha256).hexdigest()
            if not any(hmac.compare_digest(expected, value) for value in signature_parts.get("v1", [])):
                raise ValueError
            event = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "Invalid webhook signature or payload."})

        if event.get("type") == "checkout.session.completed":
            session = event.get("data", {}).get("object", {})
            order_id = session.get("metadata", {}).get("order_id")
            if order_id:
                try:
                    with sqlite3.connect(DATABASE) as database:
                        database.execute(
                            "UPDATE orders SET status = 'paid' WHERE id = ? AND checkout_session = ?",
                            (order_id, session.get("id")),
                        )
                except sqlite3.Error:
                    return self.send_json(500, {"error": "Could not update the order."})
        return self.send_json(200, {"received": True})

    def log_message(self, format, *args):
        print(f"{self.log_date_time_string()} {self.address_string()} {format % args}")


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), StoreHandler)
    print(f"The School of Lola is running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down The School of Lola.")
        server.server_close()