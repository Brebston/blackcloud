import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ensureCsrf, errorText, post } from "../api/client";
import Icon from "../components/Icon";
import { useAuth } from "../hooks/useAuth";

const SPA_ROOTS = ["files", "shared", "trash", "calendar", "mail", "chat", "settings", "admin"];

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
      setError("Введіть логін і пароль.");
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
      setError("Код складається з 6 цифр.");
      return;
    }
    if (useBackup && clean.replace(/[-\s]/g, "").length !== 10) {
      setError("Резервний код має формат xxxxx-xxxxx.");
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
        <div className="auth-brand">
          <Icon name="cloud" size={32} />
          <h1>BlackCloud</h1>
        </div>
        {step === "password" ? (
          <form onSubmit={submitPassword} noValidate>
            <label>
              Логін або email
              <input autoFocus autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} />
            </label>
            <label>
              Пароль
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {error && <div className="form-error">{error}</div>}
            <button className="btn btn-primary btn-block" disabled={busy}>
              {busy ? "Вхід…" : "Увійти"}
            </button>
            <div className="auth-foot">
              Немає акаунта? <Link to="/register">Реєстрація за запрошенням</Link>
            </div>
          </form>
        ) : (
          <form onSubmit={submitCode} noValidate>
            <div className="auth-hint">
              <Icon name="shield" />
              {useBackup
                ? "Введіть один із резервних кодів. Кожен код працює лише один раз."
                : "Введіть 6-значний код із застосунку автентифікації."}
            </div>
            <label>
              {useBackup ? "Резервний код" : "Код підтвердження"}
              <input
                autoFocus
                inputMode={useBackup ? "text" : "numeric"}
                autoComplete="one-time-code"
                maxLength={useBackup ? 11 : 6}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="code-input"
              />
            </label>
            {error && <div className="form-error">{error}</div>}
            <button className="btn btn-primary btn-block" disabled={busy}>
              {busy ? "Перевірка…" : "Підтвердити"}
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
                {useBackup ? "Використати код із застосунку" : "Немає доступу до телефону? Резервний код"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
