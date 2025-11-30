import fetch, { Headers } from 'node-fetch';

export type BillingInterval = 'EVERY_30_DAYS' | 'ANNUAL';
export interface RecurringPrice {
  amount: number;
  currencyCode: string;
}

export interface CreateSubscriptionInput {
  name: string;
  price: RecurringPrice;
  interval: BillingInterval;
  returnUrl: string;
  trialDays?: number;
  usageCappedAmount?: RecurringPrice;
  usageTerms?: string;
}

export interface UsageRecordInput {
  subscriptionLineItemId: string;
  description: string;
  price: RecurringPrice;
}

export interface SubscriptionStatus {
  id: string;
  status: string;
  name: string;
  createdAt: string;
  currentPeriodEnd: string | null;
}

export class ShopifyBillingClient {
  private readonly endpoint: string;
  private readonly headers: Headers;

  constructor(
    private readonly shopDomain: string,
    private readonly accessToken: string,
    apiVersion = '2024-07',
  ) {
    this.endpoint = `https://${shopDomain}/admin/api/${apiVersion}/graphql.json`;
    this.headers = new Headers({
      'Content-Type': 'application/json',
      'X-Shopify-Access-Token': accessToken,
    });
  }

  /**
   * Create a recurring app subscription using appSubscriptionCreate.
   * Shopify docs: https://shopify.dev/docs/api/admin-graphql/latest/mutations/appSubscriptionCreate
   */
  async createRecurringSubscription(input: CreateSubscriptionInput) {
    const lineItems = [
      {
        plan: {
          appRecurringPricingDetails: {
            price: input.price,
            interval: input.interval,
            trialDays: input.trialDays,
          },
        },
      },
    ];

    if (input.usageCappedAmount && input.usageTerms) {
      lineItems.push({
        plan: {
          appUsagePricingDetails: {
            cappedAmount: input.usageCappedAmount,
            terms: input.usageTerms,
          },
        },
      });
    }

    const body = {
      query: `mutation CreateSubscription($name: String!, $returnUrl: URL!, $lineItems: [AppSubscriptionLineItemInput!]!) {
        appSubscriptionCreate(name: $name, returnUrl: $returnUrl, lineItems: $lineItems) {
          confirmationUrl
          appSubscription { id status }
          userErrors { field message }
        }
      }`,
      variables: {
        name: input.name,
        returnUrl: input.returnUrl,
        lineItems,
      },
    };

    const response = await this.execute(body, 'appSubscriptionCreate');
    return {
      confirmationUrl: response.confirmationUrl as string,
      subscription: response.appSubscription as { id: string; status: string },
      userErrors: response.userErrors ?? [],
    };
  }

  /**
   * Record usage against a subscription using appUsageRecordCreate.
   * Shopify docs: https://shopify.dev/docs/api/admin-graphql/latest/mutations/appUsageRecordCreate
   */
  async createUsageRecord(input: UsageRecordInput) {
    const body = {
      query: `mutation CreateUsage($subscriptionLineItemId: ID!, $description: String!, $price: MoneyInput!) {
        appUsageRecordCreate(subscriptionLineItemId: $subscriptionLineItemId, description: $description, price: $price) {
          appUsageRecord { id }
          userErrors { field message }
        }
      }`,
      variables: {
        subscriptionLineItemId: input.subscriptionLineItemId,
        description: input.description,
        price: input.price,
      },
    };

    const response = await this.execute(body, 'appUsageRecordCreate');
    return {
      usageRecordId: response.appUsageRecord?.id as string | undefined,
      userErrors: response.userErrors ?? [],
    };
  }

  /**
   * Handle the confirmation URL returned by appSubscriptionCreate for merchant approval.
   * Billing overview: https://shopify.dev/docs/apps/launch/billing/subscription-billing
   */
  handleConfirmationUrl(confirmationUrl?: string) {
    if (!confirmationUrl) {
      throw new Error('Missing confirmation URL for Shopify billing.');
    }
    return confirmationUrl;
  }

  /**
   * Query the current AppSubscription status.
   * Shopify docs: https://shopify.dev/docs/api/admin-graphql/latest/objects/AppSubscription
   */
  async getSubscriptionStatus(subscriptionId: string): Promise<SubscriptionStatus | null> {
    const body = {
      query: `query SubscriptionStatus($id: ID!) {
        node(id: $id) {
          ... on AppSubscription {
            id
            status
            name
            createdAt
            currentPeriodEnd
          }
        }
      }`,
      variables: { id: subscriptionId },
    };

    const data = await this.rawExecute(body);
    const node = data?.node;
    if (!node) {
      return null;
    }
    return {
      id: node.id,
      status: node.status,
      name: node.name,
      createdAt: node.createdAt,
      currentPeriodEnd: node.currentPeriodEnd,
    };
  }

  private async execute(body: Record<string, unknown>, key: string) {
    const data = await this.rawExecute(body);
    const result = data?.[key];
    if (!result) {
      throw new Error(`Shopify response missing ${key} payload.`);
    }

    const userErrors = result.userErrors as Array<{ field?: string[]; message: string }> | undefined;
    if (userErrors && userErrors.length) {
      console.error('Shopify user errors', userErrors);
    }

    return result;
  }

  private async rawExecute(body: Record<string, unknown>) {
    const res = await fetch(this.endpoint, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      throw new Error(`Shopify API request failed with status ${res.status}`);
    }

    const payload = await res.json();
    if (payload.errors) {
      console.error('Shopify GraphQL errors', payload.errors);
    }
    return payload.data;
  }
}
