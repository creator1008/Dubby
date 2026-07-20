"use client";

import type { PurchasesPackage } from "@revenuecat/purchases-capacitor";
import { useEffect, useState } from "react";
import { useAuthSession } from "@/components/app/AuthBoundary";
import { api } from "@/lib/api";
import { billingPlatform } from "@/lib/mobile";
import { useAppDictionary, useLocale } from "@/lib/i18n/locale-context";
import type { Locale } from "@/lib/i18n/dictionaries";
import {
  isPurchaseCancellation,
  loadRevenueCatBilling,
  purchaseRevenueCatPackage,
  restoreRevenueCatPurchases,
  type SubscriptionSummary,
} from "@/lib/revenuecat";

const BILLING_COPY: Record<Locale, Record<string, string>> = {
  ko: {
    status: "구독 상태", active: "활성", inactive: "활성 구독 없음",
    renewal: "다음 만료/갱신", connect: "RevenueCat Offering에 판매 패키지를 연결해 주세요.",
    buy: "구매", restore: "구매 복원", manage: "구독 관리", starter: "Starter (구독)",
    monthly: "매월 사용할 수 있는 더빙 시간을 제공합니다.", subscribe: "Starter 구독",
    extra: "추가 크레딧", oneTime: "일회성 충전 팩", pack: "30분 크레딧 팩",
  },
  en: {
    status: "Subscription status", active: "Active", inactive: "No active subscription",
    renewal: "Next expiry/renewal", connect: "Connect a sales package to the RevenueCat Offering.",
    buy: "Buy", restore: "Restore purchases", manage: "Manage subscription",
    starter: "Starter subscription", monthly: "Includes dubbing time every month.",
    subscribe: "Subscribe to Starter", extra: "Extra credits", oneTime: "One-time top-up",
    pack: "30-minute credit pack",
  },
  vi: {
    status: "Trạng thái đăng ký", active: "Đang hoạt động", inactive: "Không có đăng ký hoạt động",
    renewal: "Hết hạn/gia hạn tiếp theo", connect: "Hãy kết nối gói bán hàng với RevenueCat Offering.",
    buy: "Mua", restore: "Khôi phục giao dịch", manage: "Quản lý đăng ký",
    starter: "Gói Starter", monthly: "Cung cấp thời lượng lồng tiếng mỗi tháng.",
    subscribe: "Đăng ký Starter", extra: "Tín dụng bổ sung", oneTime: "Gói nạp một lần",
    pack: "Gói tín dụng 30 phút",
  },
};

export default function BillingPage() {
  const text = useAppDictionary();
  const { locale } = useLocale();
  const copy = BILLING_COPY[locale];
  const session = useAuthSession();
  const mode = billingPlatform();
  const [credits, setCredits] = useState<number | null>(null);
  const [packages, setPackages] = useState<PurchasesPackage[]>([]);
  const [subscription, setSubscription] = useState<SubscriptionSummary | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.credits().then((data) => setCredits(data.balance_minutes)).catch((err: Error) => setMsg(err.message));
  }, []);

  useEffect(() => {
    if (mode !== "revenuecat" || !session) return;
    void loadRevenueCatBilling(session.user.id)
      .then((result) => {
        setPackages(result.packages);
        setSubscription(result.customer);
      })
      .catch((error: Error) => setMsg(error.message))
      .finally(() => setBusy(false));
  }, [mode, session]);

  const buyWeb = async (kind: "subscription" | "credits") => {
    setBusy(true);
    setMsg(null);
    try {
      const { url } = await api.checkout(kind);
      window.location.assign(url);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Checkout을 시작하지 못했습니다.");
      setBusy(false);
    }
  };

  const buyNative = async (selectedPackage: PurchasesPackage) => {
    if (!session) return;
    setBusy(true);
    setMsg(null);
    try {
      const result = await purchaseRevenueCatPackage(session.user.id, selectedPackage);
      setSubscription(result);
      setMsg("구매가 완료되었습니다. 크레딧은 스토어 확인 후 반영됩니다.");
      window.dispatchEvent(new Event("credits-changed"));
    } catch (error) {
      if (!isPurchaseCancellation(error)) {
        setMsg(error instanceof Error ? error.message : "스토어 구매를 완료하지 못했습니다.");
      }
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    if (!session) return;
    setBusy(true);
    setMsg(null);
    try {
      const result = await restoreRevenueCatPurchases(session.user.id);
      setSubscription(result);
      setMsg(result.active ? "구매 내역과 구독을 복원했습니다." : "복원할 활성 구독이 없습니다.");
      window.dispatchEvent(new Event("credits-changed"));
    } catch (error) {
      setMsg(error instanceof Error ? error.message : "구매 내역을 복원하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="app-hero-row">
        <div>
          <h1>{text.credits}</h1>
          <p className="muted">
            {text.billingDescription}
          </p>
        </div>
        <span className="credits-pill">
          {text.remaining} {credits === null ? "—" : `${credits.toFixed(1)} ${text.minutes}`}
        </span>
      </div>

      <div className="app-panel" style={{ display: "grid", gap: "1rem" }}>
        {mode === "revenuecat" ? (
          <>
            <div className="billing-status">
              <strong>{copy.status}</strong>
              <span className={`status-chip ${subscription?.active ? "completed" : ""}`}>
                {subscription?.active ? copy.active : copy.inactive}
              </span>
              {subscription?.expirationDate && (
                <p className="muted">{copy.renewal}: {new Date(subscription.expirationDate).toLocaleDateString()}</p>
              )}
            </div>
            {packages.length === 0 && !busy ? (
              <p className="muted">{copy.connect}</p>
            ) : packages.map((item) => (
              <div className="billing-product" key={item.identifier}>
                <div>
                  <h2>{item.product.title}</h2>
                  <p className="muted">{item.product.description}</p>
                </div>
                <button
                  className="btn-primary"
                  type="button"
                  disabled={busy}
                  onClick={() => void buyNative(item)}
                >
                  {item.product.priceString} {copy.buy}
                </button>
              </div>
            ))}
            <div className="action-row">
              <button className="btn-ghost" type="button" disabled={busy} onClick={() => void restore()}>
                {copy.restore}
              </button>
              {subscription?.managementUrl && (
                <button
                  className="btn-ghost"
                  type="button"
                  onClick={() => window.open(subscription.managementUrl!, "_blank", "noopener")}
                >
                  {copy.manage}
                </button>
              )}
            </div>
          </>
        ) : (
          <>
            <div>
              <h2 style={{ margin: "0 0 0.35rem", fontFamily: "var(--font-syne)" }}>
                {copy.starter}
              </h2>
              <p className="muted" style={{ margin: 0 }}>
                {copy.monthly}
              </p>
              <div className="action-row">
                <button className="btn-primary" type="button" disabled={busy} onClick={() => void buyWeb("subscription")}>
                  {copy.subscribe}
                </button>
              </div>
            </div>
            <div>
              <h2 style={{ margin: "0 0 0.35rem", fontFamily: "var(--font-syne)" }}>
                {copy.extra}
              </h2>
              <p className="muted" style={{ margin: 0 }}>{copy.oneTime}</p>
              <div className="action-row">
                <button className="btn-ghost" type="button" disabled={busy} onClick={() => void buyWeb("credits")}>
                  {copy.pack}
                </button>
              </div>
            </div>
          </>
        )}
        {msg && <p className="form-msg">{msg}</p>}
      </div>
    </>
  );
}
