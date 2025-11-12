"""Admin registrations for Shopify integration models."""
"""Admin hooks for the Shopify app.

The legacy Shopify admin models exposed charge logs and installed shops. These
concepts now live inside the ledger application (for billing activity) and the
merchant directory, so no dedicated admin registrations are required here.
"""
