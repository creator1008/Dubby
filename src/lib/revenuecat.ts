"use client";

import { Capacitor } from "@capacitor/core";
import {
  Purchases,
  type CustomerInfo,
  type PurchasesPackage,
} from "@revenuecat/purchases-capacitor";
import { billingPlatform } from "@/lib/mobile";

let configured = false;
let currentUserId: string | null = null;

function apiKey(): string {
  const platform = Capacitor.getPlatform();
  if (platform === "ios") return process.env.NEXT_PUBLIC_REVENUECAT_IOS_API_KEY ?? "";
  if (platform === "android") return process.env.NEXT_PUBLIC_REVENUECAT_ANDROID_API_KEY ?? "";
  return "";
}

export type SubscriptionSummary = {
  active: boolean;
  entitlementIds: string[];
  activeProducts: string[];
  expirationDate: string | null;
  managementUrl: string | null;
};

export function summarizeCustomerInfo(info: CustomerInfo): SubscriptionSummary {
  const entitlementIds = Object.keys(info.entitlements.active);
  return {
    active: entitlementIds.length > 0,
    entitlementIds,
    activeProducts: info.activeSubscriptions,
    expirationDate: info.latestExpirationDate,
    managementUrl: info.managementURL,
  };
}

export async function syncRevenueCatUser(userId: string | null): Promise<void> {
  if (billingPlatform() !== "revenuecat") return;

  if (!configured) {
    const key = apiKey();
    if (!key) throw new Error("이 플랫폼의 RevenueCat 공개 SDK 키가 설정되지 않았습니다.");
    await Purchases.configure({ apiKey: key, appUserID: userId });
    configured = true;
    currentUserId = userId;
    return;
  }

  if (userId && currentUserId !== userId) {
    await Purchases.logIn({ appUserID: userId });
    currentUserId = userId;
  } else if (!userId && currentUserId) {
    await Purchases.logOut();
    currentUserId = null;
  }
}

export async function loadRevenueCatBilling(userId: string): Promise<{
  packages: PurchasesPackage[];
  customer: SubscriptionSummary;
}> {
  await syncRevenueCatUser(userId);
  const [offerings, { customerInfo }] = await Promise.all([
    Purchases.getOfferings(),
    Purchases.getCustomerInfo(),
  ]);
  return {
    packages: offerings.current?.availablePackages ?? [],
    customer: summarizeCustomerInfo(customerInfo),
  };
}

export async function purchaseRevenueCatPackage(
  userId: string,
  selectedPackage: PurchasesPackage,
): Promise<SubscriptionSummary> {
  await syncRevenueCatUser(userId);
  const { customerInfo } = await Purchases.purchasePackage({ aPackage: selectedPackage });
  return summarizeCustomerInfo(customerInfo);
}

export async function restoreRevenueCatPurchases(userId: string): Promise<SubscriptionSummary> {
  await syncRevenueCatUser(userId);
  const { customerInfo } = await Purchases.restorePurchases();
  return summarizeCustomerInfo(customerInfo);
}

export function isPurchaseCancellation(error: unknown): boolean {
  return Boolean(
    error &&
    typeof error === "object" &&
    "userCancelled" in error &&
    (error as { userCancelled?: boolean }).userCancelled,
  );
}
