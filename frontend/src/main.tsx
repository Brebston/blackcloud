import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ToastProvider } from "./components/Toast";
import { AuthProvider } from "./hooks/useAuth";
import { I18nProvider, storedLang } from "./i18n";
import { applyAppearance, storedAppearance } from "./lib/appearance";
import "./styles.css";

// Тема, акцент і мова з браузера — ще до першого рендеру, щоб не було «мигання»
const appearance = storedAppearance();
applyAppearance(appearance.theme, appearance.accent);
document.documentElement.lang = storedLang();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <I18nProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </I18nProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
