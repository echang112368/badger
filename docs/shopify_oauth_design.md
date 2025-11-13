# Shopify OAuth & Billing Integration Redesign

## Overview

This document describes the new end-to-end Shopify installation experience for
the Badger app. The redesign modernises the OAuth flow to align with Shopify’s
latest embedded-app guidance, links Shopify stores to existing Badger merchant
accounts, and provisions Shopify Billing so that usage fees mirror the existing
PayPal-based payout lifecycle.

### Goals

* Support the public app installation journey from the Shopify App Store
  through to the embedded admin surface inside a merchant’s store.
* Allow merchants to authenticate with an existing Badger account (or create a
  new one) from the embedded view and seamlessly bind that account to their
  Shopify shop.
* Persist Shopify offline access tokens, authorised scopes, and session tokens
  in a consistent manner that survives reinstallations.
* Bootstrap Shopify Billing using the same recurring/usage charge pattern as
  our PayPal invoicing pipeline, ensuring a single source of truth for monthly
  fees and ad-hoc usage charges.


## Architecture

The redesign is centred on three cooperating components:

1. **`ShopifyOAuthService`** (`shopify_app/oauth.py`)
   * Encapsulates the OAuth state machine.
   * Generates secure state tokens and authorisation URLs.
   * Validates callback signatures, exchanges the authorization code for an
     offline token, and caches the resulting access token + scope in the user’s
     session.
   * Provides utilities for normalising shop domains and validating Shopify
     HMAC signatures across the entire app.

2. **Embedded onboarding view** (`embedded_app_home` in `views.py`)
   * Renders inside the Shopify admin iframe after installation.
   * Offers login/sign-up forms for merchants, linking the authenticated Badger
     user to the Shopify store using `_ensure_shopify_link`.
   * Persists tokens on the merchant metadata record and triggers billing
     provisioning when the merchant has a positive monthly fee configured.

3. **Billing return handler** (`billing_return` in `views.py`)
   * Receives Shopify’s billing confirmation redirect and surfaces a human-
     friendly confirmation message.
   * Relies on the shared billing helpers to confirm a recurring charge is
     active and ready to accept usage charges.


## Request Lifecycle

### 1. Installation Kick-off

* Shopify redirects the merchant to
  `/shopify_app/oauth/authorize/?shop=<store>`.
* `oauth_authorize` calls `ShopifyOAuthService.begin_installation` which:
  * Normalises the shop domain.
  * Generates a CSRF-resistant state token and stores it in the session.
  * Records the callback URL (respecting any configured override and upgrading
    to HTTPS when running on HTTP tunnels).
  * Returns Shopify’s authorisation URL constructed with the configured scopes.
* The merchant is redirected to Shopify to approve the app.

### 2. OAuth Callback

* Shopify calls `/shopify_app/oauth/callback/` after approval.
* `oauth_callback` detects classic OAuth (presence of `code`) vs. session token
  based login (`id_token`).
* For classic OAuth, the view delegates to
  `ShopifyOAuthService.complete_installation` which validates the HMAC, checks
  the stored state, exchanges the authorisation code for an offline token, and
  caches both the access token and authorised scopes in the session.
* Merchant metadata is updated (or created) with the new token, shop domain,
  business type, and a compact “authorisation line” storing the scopes and
  timestamp.
* The view responds with an HTML page (`oauth_callback_complete.html`) that
  redirects the top window back into the embedded app, ensuring the rest of the
  setup happens inside the Shopify admin iframe.

### 3. Embedded Account Linking

* Shopify loads `/shopify_app/` inside an iframe and includes the signed `shop`
  query parameter.
* `embedded_app_home` validates the HMAC using the shared helper.
* The cached token and scope from the callback are retrieved via
  `session_token_key` / `session_scope_key`.
* Merchant login or sign-up occurs via existing forms. Upon success,
  `_ensure_shopify_link`:
  * Prevents linking the same Shopify store to multiple Badger accounts.
  * Persists the access token, shop domain, optional company name, and the
    authorisation line (scope + timestamp) on `MerchantMeta`.
  * Flags the user as a merchant and, when a monthly fee is configured, invokes
    `billing.create_or_update_recurring_charge` with a return URL pointing to
    the new `billing_return` view.
* Session caches are cleared to avoid reusing tokens across users.

### 4. Billing Confirmation

* After the merchant accepts the recurring charge, Shopify redirects to
  `/shopify_app/billing/return/?shop=<store>`.
* `billing_return` confirms the merchant metadata exists and calls
  `billing.ensure_active_charge` to verify the charge status. The response page
  summarises success or provides actionable guidance when billing is pending or
  misconfigured.

### 5. Embedded Session Tokens

* When Shopify loads the embedded app using an `id_token`, the flow falls back
  to `_handle_session_token_callback`, reusing the cached access token or
  redirecting to onboarding if the store has not yet been linked.


## Data Storage

* **Session Keys**
  * `shopify_install_token:<shop>` – cached offline access token from the most
    recent installation.
  * `shopify_install_scope:<shop>` – authorised scope string for the same
    installation.
  * The existing session keys for the embedded shop, authorisation state, and
    onboarding flags are unchanged.

* **MerchantMeta**
  * `shopify_access_token` – canonical offline token.
  * `shopify_oauth_authorization_line` – stored as
    `scope=<scopes>;connected_at=<ISO8601>` to provide human-readable audit
    information.
  * Billing-related fields (`shopify_recurring_charge_id`,
    `shopify_billing_status`, etc.) are updated by the billing helpers in the
    same manner as PayPal invoicing to maintain parity between payment methods.


## Implementation Notes

* All Shopify REST calls remain versioned (`2024-07`) and leverage the shared
  `ShopifyClient` wrapper.
* `ShopifyOAuthService` is intentionally stateless beyond the `request` object,
  making it trivial to unit test by patching its methods in the Django views.
* The new templates are lightweight, responsive HTML pages designed to render
  cleanly both within the Shopify admin and in standalone windows.
* Billing bootstrap is guarded by the merchant’s monthly fee; merchants without
  a configured fee can still link their store without triggering charge
  creation.


## Deployment & Rollout

* No database migrations are required.
* Ensure the environment provides `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`,
  and the desired `SHOPIFY_SCOPES` before deploying.
* Update the Shopify App URL configuration to point to the new OAuth endpoints
  if necessary (the endpoint paths are unchanged, but the callback now renders
  an HTML bridge page).
* Validate billing return URLs inside the Shopify Partner dashboard so the
  `billing_return` view receives traffic after charge acceptance.


## Testing Strategy

* Unit tests cover:
  * Authorisation URL construction and session state persistence.
  * Access-token exchange payloads.
  * Embedded login & sign-up flows, including session cleanup.
  * OAuth callback templating and session cache population.
  * Billing return behaviour for both success and error states.
* Run `python manage.py test shopify_app` to execute the full suite.

