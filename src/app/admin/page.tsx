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

export default function AdminPage() {
  const session = useAuthSession();
  const text = useAppDictionary();
  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [logs, setLogs] = useState<AccessLog[]>([]);
  const [selected, setSelected] = useState<AdminUserUsage | null>(null);
  const [query, setQuery] = useState("");
  const [delta, setDelta] = useState("10");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadUsers = useCallback(async (search = "") => {
    if (!api.admin) return;
    setLoading(true);
    try {
      setUsers(await api.admin.users(search));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLogs = useCallback(async () => {
    if (!api.admin) return;
    setLoading(true);
    try {
      setLogs(await api.admin.accessLogs());
    } finally {
      setLoading(false);
    }
  }, []);

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
    setSelected(await api.admin!.userUsage(userId));
    setMessage(null);
  };

  const adjustCredits = async () => {
    if (!selected) return;
    const minutes = Number(delta);
    if (!Number.isFinite(minutes) || minutes === 0 || note.trim().length < 2) {
      setMessage(text.invalidCreditAdjustment);
      return;
    }
    const result = await api.admin!.adjustCredits(
      selected.profile.id,
      minutes,
      note.trim(),
    );
    setMessage(`${text.creditsAdjusted} ${result.balance_minutes} ${text.minutes}`);
    setSelected(await api.admin!.userUsage(selected.profile.id));
    await loadUsers(query);
  };

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
                  className="admin-user-row"
                  key={user.id}
                  onClick={() => void inspectUser(user.id)}
                >
                  <span>
                    <strong>{user.display_name || text.noName}</strong>
                    <small>{user.email}</small>
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
                <div>
                  <h2>{selected.profile.display_name || text.noName}</h2>
                  <p>{selected.profile.email}</p>
                  <p className="muted">
                    {selected.profile.country || text.countryMissing} ·{" "}
                    {selected.profile.auth_provider || text.providerUnknown} · {text.registered}{" "}
                    {new Date(selected.profile.created_at).toLocaleString()}
                  </p>
                </div>
                <div className="credit-adjust">
                  <strong>
                    {text.credits}: {selected.credit_balance.toFixed(1)} {text.minutes}
                  </strong>
                  <input
                    type="number"
                    step="0.1"
                    value={delta}
                    onChange={(event) => setDelta(event.target.value)}
                    aria-label="조정할 크레딧"
                  />
                  <input
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder={text.adjustmentReason}
                  />
                  <button className="btn-primary" type="button" onClick={() => void adjustCredits()}>
                    {text.adjustCredits}
                  </button>
                </div>
                <div>
                  <h3>{text.recentUsage}</h3>
                  {selected.projects.map((project) => (
                    <div className="admin-usage-row" key={project.id}>
                      <span>{project.title}</span>
                      <span>{project.source_lang} → {project.target_lang}</span>
                      <span>{project.status}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <h3>{text.credits}</h3>
                  {selected.credits.slice(0, 20).map((entry) => (
                    <div className="admin-usage-row" key={entry.id}>
                      <span>{new Date(entry.created_at).toLocaleString()}</span>
                      <span>{entry.reason}</span>
                      <strong>
                        {entry.delta_minutes > 0 ? "+" : ""}
                        {entry.delta_minutes} {text.minutes}
                      </strong>
                    </div>
                  ))}
                </div>
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
              <span className={`status-chip ${log.status_code >= 400 ? "failed" : "completed"}`}>
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
