import { useEffect, useState } from "react";
import { errorText } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useT } from "../i18n";
import { biometricKey, isAlreadyRegistered, isCancelled, platformAuthenticatorAvailable, registerPasskey } from "../lib/webauthn";
import Icon from "./Icon";
import Modal from "./Modal";
import { useToast } from "./Toast";

const OFFER_KEY = "bc_offer_passkey";
const LATER_DAYS = 14;

// Вибір користувача — на цьому пристрої (Face ID одного пристрою не стосується іншого)
const choiceKey = (userId: string) => `bc_passkey_prompt:${userId}`;

function readChoice(userId: string): string | null {
  try {
    return localStorage.getItem(choiceKey(userId));
  } catch {
    return null;
  }
}

function saveChoice(userId: string, value: string) {
  try {
    localStorage.setItem(choiceKey(userId), value);
  } catch {
    /* приватний режим — просто не запам'ятовуємо */
  }
}

/**
 * Після входу паролем (або паролем + кодом) пропонує ввімкнути вхід через Face ID / Touch ID /
 * Windows Hello на цьому пристрої. Не показується, якщо пристрій цього не вміє, якщо вхід був
 * ключем доступу, або користувач обрав «Не зараз» (14 днів) чи «Більше не питати».
 */
export default function PasskeyPrompt() {
  const { user } = useAuth();
  const t = useT();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const method = t(biometricKey());

  useEffect(() => {
    if (!user) return;
    let offer = false;
    try {
      offer = sessionStorage.getItem(OFFER_KEY) === "1";
      sessionStorage.removeItem(OFFER_KEY);
    } catch {
      return;
    }
    if (!offer) return;
    const choice = readChoice(user.id);
    if (choice === "never" || (choice && choice.startsWith("later:") && Number(choice.slice(6)) > Date.now())) return;
    let cancelled = false;
    platformAuthenticatorAvailable().then((ok) => !cancelled && ok && setOpen(true));
    return () => {
      cancelled = true;
    };
  }, [user]);

  if (!open || !user) return null;

  const later = () => {
    saveChoice(user.id, `later:${Date.now() + LATER_DAYS * 86400_000}`);
    setOpen(false);
  };

  const never = () => {
    saveChoice(user.id, "never");
    setOpen(false);
  };

  const enable = async () => {
    setBusy(true);
    try {
      await registerPasskey({ platform: true });
      saveChoice(user.id, "never"); // на цьому пристрої вже є — більше не питаємо
      toast(t("passkey.prompt.done", { method }), "success");
      setOpen(false);
    } catch (err) {
      if (isAlreadyRegistered(err)) {
        saveChoice(user.id, "never");
        toast(t("passkey.prompt.already"), "info");
        setOpen(false);
      } else if (!isCancelled(err)) {
        toast(err instanceof DOMException ? t("passkey.failed") : errorText(err), "error");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={t("passkey.prompt.title", { method })} onClose={later}>
      <div className="stack">
        <div className="passkey-prompt-icon">
          <Icon name="lock" size={28} />
        </div>
        <p>{t("passkey.prompt.text")}</p>
        <button className="btn btn-primary btn-block" onClick={enable} disabled={busy}>
          {t("passkey.prompt.enable")}
        </button>
        <button className="btn btn-block" onClick={later} disabled={busy}>
          {t("passkey.prompt.later")}
        </button>
        <button className="link-btn center small" onClick={never} disabled={busy}>
          {t("passkey.prompt.never")}
        </button>
      </div>
    </Modal>
  );
}
