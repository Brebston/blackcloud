import { ChangeEvent, FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { errorText, post } from "../api/client";
import Icon from "../components/Icon";

export default function RegisterPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
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
    if (!/^[a-z0-9][a-z0-9._-]{2,39}$/.test(form.username)) errs.username = "3–40 символів: малі латинські літери, цифри, . _ -";
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email)) errs.email = "Невірний email.";
    if (form.password.length < 12) errs.password = "Мінімум 12 символів.";
    if (form.password !== form.password2) errs.password2 = "Паролі не збігаються.";
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
          <h1>Реєстрація</h1>
        </div>
        <form onSubmit={submit} noValidate>
          <label>
            Ім'я користувача
            <input autoFocus autoComplete="username" value={form.username} onChange={set("username")} />
            {errors.username && <span className="field-error">{errors.username}</span>}
          </label>
          <label>
            Email
            <input type="email" autoComplete="email" value={form.email} onChange={set("email")} />
            {errors.email && <span className="field-error">{errors.email}</span>}
          </label>
          <label>
            Пароль
            <input type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
            {errors.password && <span className="field-error">{errors.password}</span>}
          </label>
          <label>
            Повторіть пароль
            <input type="password" autoComplete="new-password" value={form.password2} onChange={set("password2")} />
            {errors.password2 && <span className="field-error">{errors.password2}</span>}
          </label>
          <label>
            Код запрошення
            <input value={form.invite} onChange={set("invite")} />
          </label>
          {errors.form && <div className="form-error">{errors.form}</div>}
          <button className="btn btn-primary btn-block" disabled={busy}>
            {busy ? "Створення…" : "Створити акаунт"}
          </button>
          <div className="auth-foot">
            Вже є акаунт? <Link to="/login">Увійти</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
