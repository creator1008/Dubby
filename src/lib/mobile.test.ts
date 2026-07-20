import { describe, expect, it } from "vitest";
import { billingPlatform } from "@/lib/mobile";
import { summarizeCustomerInfo } from "@/lib/revenuecat";
import type { CustomerInfo } from "@revenuecat/purchases-capacitor";

describe("mobile billing selection", () => {
  it("falls back to Stripe on web", () => {
    expect(billingPlatform("web")).toBe("stripe");
  });

  it("uses RevenueCat only on native store platforms", () => {
    expect(billingPlatform("ios")).toBe("revenuecat");
    expect(billingPlatform("android")).toBe("revenuecat");
  });

  it("summarizes active RevenueCat entitlements", () => {
    const info = {
      entitlements: { active: { starter: {} }, all: {}, verification: "NOT_REQUESTED" },
      activeSubscriptions: ["starter_monthly"],
      latestExpirationDate: "2026-08-17T00:00:00Z",
      managementURL: "https://apps.apple.com/account/subscriptions",
    } as unknown as CustomerInfo;

    expect(summarizeCustomerInfo(info)).toEqual({
      active: true,
      entitlementIds: ["starter"],
      activeProducts: ["starter_monthly"],
      expirationDate: "2026-08-17T00:00:00Z",
      managementUrl: "https://apps.apple.com/account/subscriptions",
    });
  });
});
