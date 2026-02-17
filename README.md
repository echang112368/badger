# Badger

Badger is a simple Django project that connects merchants and content creators. It lets merchants list their items and creators generate trackable links for those items. The project is organized into several apps, each responsible for a specific set of features.

## Apps Overview

### accounts
Handles user authentication and sign up flows.

- `custom_login_view(request)` – Authenticates a user and redirects them to the appropriate dashboard based on the user type (merchant or creator).
- `signup_choice_view(request)` – Presents a choice between merchant and creator sign up.
- `business_signup_view(request)` – Registers a new merchant and logs them in.
- `creator_signup_view(request)` – Registers a new creator and logs them in.

### collect
Manages short links and incoming webhooks.

- `redirect_view(request, short_code)` – Looks up a short code, appends any stored query parameters, sets a cookie, and redirects the user to the target URL.
- `webhook_view(request)` – Receives generic POST webhooks, verifies the reference, and logs the amount if provided.
- `stripe_webhook_view(request)` – Handles Stripe webhook events and prints the amount received.

### creators
Pages for content creators.

- `creator_earnings(request)` – Displays the creator's balance and transaction history.
- `creator_affiliate_companies(request)` – Shows merchants the creator is linked to and provides unique redirect links for each item.

### links
Utilities for linking creators and merchants.

- `link_summary(request)` – Returns a JSON list of a user’s current links and their status.
- `merchant_edit_creators(request)` – Formset view that lets a merchant add or remove linked creators.

### merchants
Pages for merchants and item management.

- `merchant_dashboard(request)` – Displays the merchant’s items and the creators they work with.
- `merchant_items(request)` – Lists existing items and lets merchants add or edit them.
- `delete_item(request)` – Deletes selected items owned by the merchant.
- `delete_creators(request)` – Removes selected creators from the merchant’s link list.
- `merchant_edit_creators(request)` – Placeholder page for editing creators.

### ledger
Handles financial transactions for both merchants and creators.

- `LedgerEntry` – Records commissions, payouts and payments. Each entry now has
  a `paid` boolean to track whether it has been settled. Utility methods allow
  balance calculations.

## Models Summary
- `CustomUser` – Extends Django’s user model with `is_merchant` and `is_creator` flags.
- `RedirectLink` – Stores a short code, destination URL, and optional query parameters.
- `CreatorMeta` – Extra information about creators, including a unique `uuid` assigned at signup.
- `MerchantMeta` – Extra information about merchants such as company name, an affiliate percentage, and a unique `uuid`.
- `MerchantItem` – An item that a merchant wishes to promote.
- `MerchantCreatorLink` – Relationship between a merchant and a creator with a status field.
- `LedgerEntry` – Financial ledger entry linked to either a creator or merchant.
  The `paid` field indicates if the entry has been settled.

## Project URLs
The main URL configuration (`random_links/urls.py`) wires the apps together:
- `/accounts/` for login and sign up.
- `/merchant/` for merchant pages.
- `/creators/` for creator pages.
- `/` handles short links and webhooks from the `collect` app.

## Running the Project
1. Install project dependencies.
2. Apply migrations with `python manage.py migrate`.
3. Start the server using `python manage.py runserver`.

## Payouts
`LedgerEntry` records can be settled through PayPal using the `send_mass_payouts` function. You can trigger a payout manually from the Django admin on the ledger entry list page. For automated payouts, run the management command `python manage.py send_payouts` on a schedule (e.g. via cron).

The admin "Send PayPal Payouts" button bypasses the date check and will pay out immediately. The management command retains the monthly schedule, only executing on the 15th.

This repository is a simple example and is not ready for production use without further security and feature work.

## Ngrok Issues
For referral_tracker.js, I am using a header to avoid ngrok warning from poping up and in development I am using a CORS_ALLOW_ALL_ORIGINS = true for all however that should be changed during final production as that is not secure. In referral_tracker for fetch it also incldues the header 'ngrok-skip-browser-warning': 'true' that can be removed before deployment. 

## Run Ngrok
Run with ngrok http 8000 --request-header-remove "ngrok-skip-browser-warning"

## Debugging issues
1. Issue where it seems that the injectiosn are not being applied 
- run python manage.py inject_scripts_all_merchants
- make sure you have turned on ngrok, entre the ngrok http url and approve it to turn it on

## Paypal API
1. Ensure that the right email credentials are set (
PAYPAL_INVOICER_EMAIL = "sb-sbodx44976751@business.example.com") in random_links/settings.py

## Shopify Compliance with Minimal Shopify CLI Usage

If your goal is to pass Shopify app verification while keeping Django in control, use Shopify CLI only for app config sync + (optionally) local tunnel.

### 1) Install Shopify CLI

Use one of these options:

```bash
# macOS
brew tap shopify/shopify
brew install shopify-cli

# npm (cross-platform)
npm install -g @shopify/cli @shopify/app
```

Then verify:

```bash
shopify version
shopify auth login
```

### 2) Keep Django as the app server

This repo already has Django OAuth + webhook routes for Shopify:

- OAuth start: `/shopify/oauth/authorize/`
- OAuth callback: `/shopify/oauth/callback/`
- Embedded app home: `/shopify/`
- Uninstall webhook: `/shopify/webhooks/app/uninstalled/`
- GDPR webhooks:
  - `/shopify/webhooks/customers/data_request/`
  - `/shopify/webhooks/customers/redact/`
  - `/shopify/webhooks/shop/redact/`

So you do **not** need a Node backend from Shopify CLI. CLI can just push app settings.

### 3) Configure environment variables (Django source of truth)

Set these in your `.env`/deployment environment:

- `SHOPIFY_API_KEY`
- `SHOPIFY_API_SECRET`
- `SHOPIFY_SCOPES`
- `SHOPIFY_APP_ORIGIN` (public base URL for your Django app)
- `SHOPIFY_REDIRECT_URI` (usually `${SHOPIFY_APP_ORIGIN}/shopify/oauth/callback/`)

This project already reads these settings and builds defaults for app URL and callback URL from `SHOPIFY_APP_ORIGIN`.

Recommended `.env` snippet:

```env
# Single source of truth for your public Shopify app URL.
SHOPIFY_APP_ORIGIN=https://your-current-domain-or-ngrok.ngrok-free.app

SHOPIFY_API_KEY=...
SHOPIFY_API_SECRET=...
SHOPIFY_SCOPES=read_products,write_discounts
```

Origin precedence is:

1. `SHOPIFY_APP_ORIGIN`
2. `SHOPIFY_APP_URL`
3. `NGROK_URL`
4. `NGROK_HOST`
5. `SHOPIFY_APP_HOST`
6. `SHOPIFY_APP_DOMAIN`

No hardcoded ngrok URL fallback is used anymore, so changing your tunnel only requires updating one env variable.

### 4) Create a minimal `shopify.app.toml`

At repo root, include only what verification needs (example values shown):

```toml
client_id = "<SHOPIFY_API_KEY>"
name = "Badger"
application_url = "https://your-domain.com/shopify/"
embedded = true

[auth]
redirect_urls = [
  "https://your-domain.com/shopify/oauth/callback/"
]

[webhooks]
api_version = "2024-07"

  [[webhooks.subscriptions]]
  topics = ["app/uninstalled"]
  uri = "https://your-domain.com/shopify/webhooks/app/uninstalled/"

  [[webhooks.subscriptions]]
  topics = ["customers/data_request"]
  uri = "https://your-domain.com/shopify/webhooks/customers/data_request/"

  [[webhooks.subscriptions]]
  topics = ["customers/redact"]
  uri = "https://your-domain.com/shopify/webhooks/customers/redact/"

  [[webhooks.subscriptions]]
  topics = ["shop/redact"]
  uri = "https://your-domain.com/shopify/webhooks/shop/redact/"
```

### 5) Use CLI only for config sync

```bash
shopify app config push
```

That updates Partner Dashboard config from TOML, while requests still hit Django endpoints.

### 6) What code changes were needed for minimal CLI flow

- Removed hardcoded ngrok fallback for `orders/create` webhook registration.
- Webhook registration now uses explicit URL if provided, otherwise derives URL from `SHOPIFY_APP_ORIGIN`.
- If neither exists, webhook registration fails clearly instead of sending Shopify to a stale tunnel URL.

This keeps webhook destinations controlled by Django environment config instead of local tunnel artifacts.

### Is this still good after deployment?

Yes. This setup is better for deployment because production should set `SHOPIFY_APP_ORIGIN` to your real domain
(for example `https://app.example.com`) and should not depend on ngrok. In development, point the same variable
to your current tunnel URL.
