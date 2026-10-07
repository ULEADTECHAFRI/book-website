# The School of Lola | Bookshop

A small, dependency-free online bookshop with a Python backend, SQLite order records, and Stripe-hosted checkout. The browser never handles card details; prices and book IDs are checked by the server.

## Run locally

Requires Python 3.10 or newer. From this folder:

```powershell
python server.py
```

Open [http://localhost:8000](http://localhost:8000). The catalogue and cart work without payment credentials. Checkout will explain how to enable payments until Stripe is configured.

## Enable payments

1. Create a Stripe account and use its test-mode secret key while developing.
2. Set the server environment variables in PowerShell, then start the server in that same terminal:

```powershell
$env:STRIPE_SECRET_KEY = "sk_test_..."
$env:SITE_URL = "http://localhost:8000"
python server.py
```

3. In Stripe, register `http://localhost:8000/api/webhook` for the `checkout.session.completed` event. Set the signing secret Stripe provides before restarting the server:

```powershell
$env:STRIPE_WEBHOOK_SECRET = "whsec_..."
```

Use Stripe CLI to forward test webhooks to the local endpoint, or configure the deployed endpoint in Stripe. Set `SITE_URL` to the public HTTPS origin in production. Set `HOST=0.0.0.0` only when the server must accept connections from outside the local machine. Never commit Stripe secrets or use test keys for live transactions.

The backend creates `orders.sqlite3` on first start. It records pending orders before redirecting to Stripe and marks them paid only after a timestamp-checked, signature-verified Stripe webhook. Keep the database and backups private.

The catalogue, prices, and product images are sample content; replace them with real inventory and properly licensed image assets before launch. This starter does not include shipping fulfillment, tax calculation, email receipts, account management, or a production deployment setup. Configure these and review applicable privacy, consumer, and tax requirements before taking live orders.

The Contact link uses `hello@schooloflola.example` as a placeholder. Replace it with the shop's real email address before publishing.