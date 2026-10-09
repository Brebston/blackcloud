import { ChangeEvent, FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { errorText, post } from "../api/client";
import Icon from "../components/Icon";
import { useT } from "../i18n";

export default function RegisterPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const t = useT();
  const [form, setForm] = useState({ username: "", email: "", password: "", password2: "", invite: params.get("invite") || "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: ChangeEvent<HTMLInputElement>) => {
    setForm({ ...form, [k]: e.target.value });
    setErrors({ ...errors, [k]: "", form: "" });
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!/^[a-z0-9][a-z0-9._-]{2,39}$/.test(form.username)) errs.username = t("auth.badUsername");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email)) errs.email = t("auth.badEmail");
    if (form.password.length < 12) errs.password = t("auth.pwTooShort");
    if (form.password !== form.password2) errs.password2 = t("auth.pwMismatch");
    if (Object.keys(errs).length) {
      setErrors(errs);
      return;
    }
    setBusy(true);
    try {
      await post("/api/auth/register/", {
        username: form.username,
        email: form.email,
        password: form.password,
        invite: form.invite,
      });
      navigate("/login?registered=1");
    } catch (err) {
      setErrors({ form: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <Icon name="cloud" size={32} />
          <h1>{t("auth.title")}</h1>
        </div>
        <form onSubmit={submit} noValidate>
          <label>
            {t("auth.username")}
            <input autoFocus autoComplete="username" value={form.username} onChange={set("username")} />
            {errors.username && <span className="field-error">{errors.username}</span>}
          </label>
          <label>
            {t("auth.email")}
            <input type="email" autoComplete="email" value={form.email} onChange={set("email")} />
            {errors.email && <span className="field-error">{errors.email}</span>}
          </label>
          <label>
            {t("auth.password")}
            <input type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
            {errors.password && <span className="field-error">{errors.password}</span>}
          </label>
          <label>
            {t("auth.repeatPassword")}
            <input type="password" autoComplete="new-password" value={form.password2} onChange={set("password2")} />
            {errors.password2 && <span className="field-error">{errors.password2}</span>}
          </label>
          <label>
            {t("auth.invite")}
            <input value={form.invite} onChange={set("invite")} />
          </label>
          {errors.form && <div className="form-error">{errors.form}</div>}
          <button className="btn btn-primary btn-block" disabled={busy}>
            {busy ? t("auth.creating") : t("auth.createAccount")}
          </button>
          <div className="auth-foot">
            {t("auth.haveAccount")} <Link to="/login">{t("auth.signIn")}</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
