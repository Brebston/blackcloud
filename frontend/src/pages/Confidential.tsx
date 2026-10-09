import { FormEvent, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { errorText, post } from "../api/client";
import Icon from "../components/Icon";
import { LanguageSwitcher, useT } from "../i18n";
import { formatDate } from "../lib/format";

interface OpenResult {
  requires_passcode: boolean;
  subject: string;
  from: string;
  expires_at?: string;
  render_url?: string;
}

/** Перегляд конфіденційного листа отримувачем. Токен — у фрагменті URL (/c#токен):
 *  він не потрапляє в журнали сервера та Referer і передається API лише в тілі POST. */
export default function ConfidentialPage() {
  const t = useT();
  const location = useLocation();
  const token = location.hash.replace(/^#/, "").trim();
  const [state, setState] = useState<OpenResult | null>(null);
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const open = async (code = "") => {
    setBusy(true);
    setError("");
    try {
      setState(await post<OpenResult>("/api/public/confidential/open/", { token, passcode: code }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (token) open();
    else setError(t("conf.badLink"));
  }, [token]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(passcode.trim())) return setError(t("conf.passcode6"));
    open(passcode.trim());
  };

  const ready = state && !state.requires_passcode && state.render_url;

  return (
    <div className={ready ? "public-page conf-page" : "auth-page"}>
      <div className={ready ? "conf-card card" : "auth-card"}>
        <div className="auth-lang">
          <LanguageSwitcher />
        </div>
        <div className="auth-brand">
          <Icon name="lock" size={28} />
          <h1>{t("conf.title")}</h1>
        </div>
        {busy && !state && <div className="muted">{t("common.loading")}</div>}
        {state?.requires_passcode && (
          <form onSubmit={submit} noValidate>
            <div className="auth-hint">
              <Icon name="shield" />
              {t("conf.enterPasscode", { from: state.from })}
            </div>
            <label>
              {t("conf.passcode")}
              <input
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                className="code-input"
                value={passcode}
                onChange={(e) => setPasscode(e.target.value)}
              />
            </label>
            {error && <div className="form-error">{error}</div>}
            <button className="btn btn-primary btn-block" disabled={busy}>
              {t("conf.open")}
            </button>
          </form>
        )}
        {ready && (
          <div className="stack">
            <div>
              <h3 className="reader-subject">{state.subject || t("mail.noSubject")}</h3>
              <div className="small muted">
                {t("mail.from")}: {state.from}
                {state.expires_at && ` · ${t("conf.until", { date: formatDate(state.expires_at) })}`}
              </div>
            </div>
            <iframe
              className="mail-frame"
              title={t("mail.content")}
              sandbox="allow-popups allow-popups-to-escape-sandbox"
              referrerPolicy="no-referrer"
              src={state.render_url}
            />
            <p className="small muted">{t("conf.recipientNote")}</p>
          </div>
        )}
        {!state?.requires_passcode && error && <div className="form-error">{error}</div>}
      </div>
    </div>
  );
}
