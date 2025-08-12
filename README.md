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
