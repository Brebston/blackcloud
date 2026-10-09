import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ensureCsrf, errorText, post } from "../api/client";
import Icon from "../components/Icon";
import { useAuth } from "../hooks/useAuth";
import { LanguageSwitcher, useT } from "../i18n";

const SPA_ROOTS = ["files", "shared", "trash", "calendar", "mail", "chat", "settings", "admin", "edit"];

function safeNext(next: string | null): { spa: boolean; path: string } {
  // Лише відносні шляхи цього ж сайту (захист від open redirect)
  if (!next || !/^\/[^/\\]/.test(next)) return { spa: true, path: "/files" };
  const root = next.split(/[/?#]/)[1];
  return { spa: SPA_ROOTS.includes(root), path: next };
}

export default function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const t = useT();
  const [step, setStep] = useState<"password" | "2fa">("password");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [useBackup, setUseBackup] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const finish = async () => {
    await ensureCsrf(true);
    await refresh();
    const next = safeNext(params.get("next"));
    if (next.spa) navigate(next.path, { replace: true });
    else window.location.assign(next.path);
  };

  const submitPassword = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!login.trim() || !password) {
      setError(t("login.enterBoth"));
      return;
    }
    setBusy(true);
    try {
      const r = await post<{ status: string }>("/api/auth/login/", { login: login.trim(), password });
      setPassword("");
      if (r.status === "2fa_required") {
        await ensureCsrf(true);
        setStep("2fa");
      } else {
        await finish();
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    const clean = code.trim();
    if (!useBackup && !/^\d{6}$/.test(clean.replace(/\s/g, ""))) {
      setError(t("login.code6"));
      return;
    }
    // Нові коди: xxxx-xxxx-xxxx-xxxx (16 символів); старі, видані раніше: xxxxx-xxxxx
    if (useBackup && ![10, 16].includes(clean.replace(/[-\s]/g, "").length)) {
      setError(t("login.backupFormat"));
      return;
    }
    setBusy(true);
    try {
      const r = await post<{ status: string; backup_codes_remaining?: number }>("/api/auth/login/2fa/", { code: clean });
      if (r.backup_codes_remaining !== undefined && r.backup_codes_remaining < 3) {
        sessionStorage.setItem("bc_backup_warning", String(r.backup_codes_remaining));
      }
      await finish();
    } catch (err) {
      setError(errorText(err));
      if (errorText(err).includes("Увійдіть знову")) {
        setStep("password");
        setCode("");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-lang">
          <LanguageSwitcher />
        </div>
        <div className="auth-brand">
          <Icon name="cloud" size={32} />
          <h1>BlackCloud</h1>
        </div>
        {step === "password" ? (
          <form onSubmit={submitPassword} noValidate>
            <label>
              {t("login.login")}
              <input autoFocus autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} />
            </label>
            <label>
              {t("login.password")}
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {error && <div className="form-error">{error}</div>}
            <button className="btn btn-primary btn-block" disabled={busy}>
              {busy ? t("login.signingIn") : t("login.signIn")}
            </button>
            <div className="auth-foot">
              {t("login.noAccount")} <Link to="/register">{t("login.register")}</Link>
            </div>
          </form>
        ) : (
          <form onSubmit={submitCode} noValidate>
            <div className="auth-hint">
              <Icon name="shield" />
              {useBackup ? t("login.backupHint") : t("login.totpHint")}
            </div>
            <label>
              {useBackup ? t("login.backupCode") : t("login.code")}
              <input
                autoFocus
                inputMode={useBackup ? "text" : "numeric"}
                autoComplete="one-time-code"
                maxLength={useBackup ? 24 : 6}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="code-input"
              />
            </label>
            {error && <div className="form-error">{error}</div>}
            <button className="btn btn-primary btn-block" disabled={busy}>
              {busy ? t("login.checking") : t("login.confirm")}
            </button>
            <div className="auth-foot">
              <button
                type="button"
                className="link-btn"
                onClick={() => {
                  setUseBackup(!useBackup);
                  setCode("");
                  setError("");
                }}
              >
                {useBackup ? t("login.useApp") : t("login.useBackup")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
