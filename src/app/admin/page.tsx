"use client";

import { useCallback, useEffect, useState } from "react";
import { isAdminSession, useAuthSession } from "@/components/app/AuthBoundary";
import { api } from "@/lib/api";
import type {
  AccessLog,
  AdminUser,
  AdminUserUsage,
} from "@/lib/ui-types";
import { useAppDictionary } from "@/lib/i18n/locale-context";

type Tab = "users" | "logs";
type DetailSection = "credits" | "payments" | "dubs";

export default function AdminPage() {
  const session = useAuthSession();
  const text = useAppDictionary();
  const [tab, setTab] = useState<Tab>("users");
  const [detailSection, setDetailSection] = useState<DetailSection>("credits");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [logs, setLogs] = useState<AccessLog[]>([]);
  const [selected, setSelected] = useState<AdminUserUsage | null>(null);
  const [query, setQuery] = useState("");
  const [delta, setDelta] = useState("10");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadUsers = useCallback(async (search = "") => {
    if (!api.admin) return;
    setLoading(true);
    setError(null);
    try {
      setUsers(await api.admin.users(search));
    } catch (err) {
      setError(err instanceof Error ? err.message : text.adminPermissionDenied);
    } finally {
      setLoading(false);
    }
  }, [text.adminPermissionDenied]);

  const loadLogs = useCallback(async () => {
    if (!api.admin) return;
    setLoading(true);
    setError(null);
    try {
      setLogs(await api.admin.accessLogs());
    } catch (err) {
      setError(err instanceof Error ? err.message : text.adminPermissionDenied);
    } finally {
      setLoading(false);
    }
  }, [text.adminPermissionDenied]);

  useEffect(() => {
    if (!isAdminSession(session) || !api.admin) return;
    const task = window.setTimeout(() => void loadUsers(), 0);
    return () => window.clearTimeout(task);
  }, [loadUsers, session]);

  if (!session) {
    return <p className="form-msg err">{text.adminLoginRequired}</p>;
  }
  if (!isAdminSession(session)) {
    return <p className="form-msg err">{text.adminPermissionDenied}</p>;
  }
  if (!api.admin) {
    return <p className="form-msg err">{text.adminApiRealOnly}</p>;
  }

  const inspectUser = async (userId: string) => {
    setError(null);
    setMessage(null);
    setDetailSection("credits");
    setSelected(await api.admin!.userUsage(userId));
  };

  const adjustCredits = async () => {
    if (!selected) return;
    const minutes = Number(delta);
    if (!Number.isFinite(minutes) || minutes === 0 || note.trim().length < 2) {
      setMessage(text.invalidCreditAdjustment);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.admin!.adjustCredits(
        selected.profile.id,
        minutes,
        note.trim(),
      );
      setMessage(`${text.creditsAdjusted} ${result.balance_minutes} ${text.minutes}`);
      setSelected(await api.admin!.userUsage(selected.profile.id));
      await loadUsers(query);
    } catch (err) {
      setError(err instanceof Error ? err.message : text.invalidCreditAdjustment);
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async () => {
    if (!selected) return;
    const next = !selected.profile.is_active;
    setBusy(true);
    setError(null);
    try {
      const result = await api.admin!.setUserActive(selected.profile.id, next);
      setSelected({
        ...selected,
        profile: { ...selected.profile, ...result.profile },
      });
      setMessage(next ? text.userActivated : text.userDeactivated);
      await loadUsers(query);
    } catch (err) {
      setError(err instanceof Error ? err.message : text.adminPermissionDenied);
    } finally {
      setBusy(false);
    }
  };

  const payments = selected?.payments ?? { purchases: [], subscriptions: [] };
  const jobs = selected?.jobs ?? [];

  return (
    <>
      <div className="app-hero-row">
        <div>
          <h1>{text.adminTitle}</h1>
          <p className="muted">{text.adminDescription}</p>
        </div>
      </div>

      <div className="admin-tabs">
        <button
          className={tab === "users" ? "btn-primary" : "btn-ghost"}
          type="button"
          onClick={() => {
            setTab("users");
            void loadUsers(query);
          }}
        >
          {text.userManagement}
        </button>
        <button
          className={tab === "logs" ? "btn-primary" : "btn-ghost"}
          type="button"
          onClick={() => {
            setTab("logs");
            void loadLogs();
          }}
        >
          {text.accessLogs}
        </button>
      </div>

      {error && <p className="form-msg err">{error}</p>}

      {tab === "users" ? (
        <div className="admin-grid">
          <section className="app-panel">
            <form
              className="admin-search"
              onSubmit={(event) => {
                event.preventDefault();
                void loadUsers(query);
              }}
            >
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={text.searchUser}
              />
              <button className="btn-primary" type="submit">{text.search}</button>
            </form>
            {loading && <p className="muted">{text.loading}</p>}
            <div className="admin-user-list">
              {users.map((user) => (
                <button
                  type="button"
                  className={`admin-user-row${
                    selected?.profile.id === user.id ? " selected" : ""
                  }`}
                  key={user.id}
                  onClick={() => void inspectUser(user.id)}
                >
                  <span>
                    <strong>{user.display_name || text.noName}</strong>
                    <small>{user.email}</small>
                  </span>
                  <span
                    className={`status-chip ${
                      user.is_active === false ? "failed" : "completed"
                    }`}
                  >
                    {user.is_active === false ? text.inactive : text.active}
                  </span>
                  <span>{user.country || "—"}</span>
                  <span>{user.project_count}건</span>
                  <span>{Number(user.credit_balance).toFixed(1)}분</span>
                </button>
              ))}
            </div>
          </section>

          <section className="app-panel admin-detail">
            {!selected ? (
              <p className="muted">{text.selectUser}</p>
            ) : (
              <>
                <div className="admin-detail-head">
                  <div>
                    <h2>{selected.profile.display_name || text.noName}</h2>
                    <p>{selected.profile.email}</p>
                    <p className="muted">
                      {selected.profile.country || text.countryMissing} ·{" "}
                      {selected.profile.auth_provider || text.providerUnknown} ·{" "}
                      {text.registered}{" "}
                      {new Date(selected.profile.created_at).toLocaleString()}
                    </p>
                  </div>
                  <button
                    type="button"
                    className={
                      selected.profile.is_active ? "btn-ghost" : "btn-primary"
                    }
                    disabled={busy}
                    onClick={() => void toggleActive()}
                  >
                    {selected.profile.is_active
                      ? text.deactivateUser
                      : text.activateUser}
                  </button>
                </div>

                <div className="admin-section-tabs">
                  {(
                    [
                      ["credits", text.creditHistory],
                      ["payments", text.paymentHistory],
                      ["dubs", text.dubbingHistory],
                    ] as const
                  ).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      className={
                        detailSection === id ? "btn-primary" : "btn-ghost"
                      }
                      onClick={() => setDetailSection(id)}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                {detailSection === "credits" && (
                  <>
                    <div className="credit-adjust">
                      <strong>
                        {text.credits}: {selected.credit_balance.toFixed(1)}{" "}
                        {text.minutes}
                      </strong>
                      <input
                        type="number"
                        step="0.1"
                        value={delta}
                        onChange={(event) => setDelta(event.target.value)}
                        aria-label={text.adjustCredits}
                      />
                      <input
                        value={note}
                        onChange={(event) => setNote(event.target.value)}
                        placeholder={text.adjustmentReason}
                      />
                      <button
                        className="btn-primary"
                        type="button"
                        disabled={busy}
                        onClick={() => void adjustCredits()}
                      >
                        {text.allocateCredits}
                      </button>
                    </div>
                    <div>
                      <h3>{text.creditHistory}</h3>
                      {selected.credits.length === 0 ? (
                        <p className="muted">{text.noCreditHistory}</p>
                      ) : (
                        selected.credits.map((entry) => (
                          <div className="admin-usage-row" key={entry.id}>
                            <span>
                              {new Date(entry.created_at).toLocaleString()}
                            </span>
                            <span>{entry.reason}</span>
                            <strong>
                              {entry.delta_minutes > 0 ? "+" : ""}
                              {entry.delta_minutes} {text.minutes}
                            </strong>
                          </div>
                        ))
                      )}
                    </div>
                  </>
                )}

                {detailSection === "payments" && (
                  <div>
                    <h3>{text.subscriptions}</h3>
                    {payments.subscriptions.length === 0 ? (
                      <p className="muted">{text.noSubscriptions}</p>
                    ) : (
                      payments.subscriptions.map((sub) => (
                        <div
                          className="admin-usage-row"
                          key={sub.stripe_subscription_id}
                        >
                          <span>{sub.status}</span>
                          <span>{sub.price_id || "—"}</span>
                          <span>
                            {sub.current_period_end
                              ? new Date(
                                  sub.current_period_end,
                                ).toLocaleDateString()
                              : "—"}
                          </span>
                        </div>
                      ))
                    )}
                    <h3>{text.purchases}</h3>
                    {payments.purchases.length === 0 ? (
                      <p className="muted">{text.noPurchases}</p>
                    ) : (
                      payments.purchases.map((row) => (
                        <div className="admin-usage-row" key={row.id}>
                          <span>
                            {new Date(row.created_at).toLocaleString()}
                          </span>
                          <span>{row.external_reference || row.reason}</span>
                          <strong>
                            +{row.delta_minutes} {text.minutes}
                          </strong>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {detailSection === "dubs" && (
                  <div>
                    <h3>{text.dubbingJobs}</h3>
                    {jobs.length === 0 ? (
                      <p className="muted">{text.noDubbingHistory}</p>
                    ) : (
                      jobs.map((job) => (
                        <div className="admin-usage-row" key={job.id}>
                          <span>{job.project_title || job.project_id}</span>
                          <span>
                            {job.kind} · {job.status}
                          </span>
                          <span>
                            {Number(job.charged_minutes || 0).toFixed(1)}
                            {text.minutes}
                          </span>
                          <span>
                            {new Date(job.created_at).toLocaleString()}
                          </span>
                        </div>
                      ))
                    )}
                    <h3>{text.projects}</h3>
                    {selected.projects.length === 0 ? (
                      <p className="muted">{text.noProjects}</p>
                    ) : (
                      selected.projects.map((project) => (
                        <div className="admin-usage-row" key={project.id}>
                          <span>{project.title}</span>
                          <span>
                            {project.source_lang} → {project.target_lang}
                          </span>
                          <span>{project.status}</span>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {message && <p className="form-msg">{message}</p>}
              </>
            )}
          </section>
        </div>
      ) : (
        <section className="app-panel admin-log-list">
          {loading && <p className="muted">{text.loading}</p>}
          {logs.map((log) => (
            <div className="admin-log-row" key={log.id}>
              <time>{new Date(log.created_at).toLocaleString()}</time>
              <strong>{log.method}</strong>
              <span>{log.path}</span>
              <span
                className={`status-chip ${
                  log.status_code >= 400 ? "failed" : "completed"
                }`}
              >
                {log.status_code}
              </span>
              <span>{log.email || log.ip_address || "anonymous"}</span>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
