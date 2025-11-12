"""Database models for the Shopify app integration."""

# The historical ``Shop`` and ``ShopifyChargeRecord`` models have been retired in
# favour of storing Shopify credentials on ``MerchantMeta`` records and
# representing Shopify billing activity directly on ``ledger.MerchantInvoice``
# instances.
