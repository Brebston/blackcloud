import { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Layout from "./components/Layout";
import { useAuth } from "./hooks/useAuth";
import { useT } from "./i18n";
import AdminPage from "./pages/Admin";
import CalendarPage from "./pages/Calendar";
import ConfidentialPage from "./pages/Confidential";
import ChatPage from "./pages/Chat";
import FilesPage from "./pages/Files";
import LoginPage from "./pages/Login";
import MailPage from "./pages/Mail";
import OfficeEditorPage from "./pages/OfficeEditor";
import PublicSharePage from "./pages/PublicShare";
import RegisterPage from "./pages/Register";
import SettingsPage from "./pages/Settings";
import SharedPage from "./pages/Shared";
import TrashPage from "./pages/Trash";

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const t = useT();
  if (loading) return <div className="splash">{t("common.loading")}</div>;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  return <Layout>{children}</Layout>;
}

// Без бічного меню: для повноекранного редактора
function ProtectedBare({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const t = useT();
  if (loading) return <div className="splash">{t("common.loading")}</div>;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/s" element={<PublicSharePage />} />
      <Route path="/s/:token" element={<PublicSharePage />} />
      <Route path="/c" element={<ConfidentialPage />} />
      <Route path="/files" element={<Protected><FilesPage /></Protected>} />
      <Route path="/shared" element={<Protected><SharedPage /></Protected>} />
      <Route path="/trash" element={<Protected><TrashPage /></Protected>} />
      <Route path="/calendar" element={<Protected><CalendarPage /></Protected>} />
      <Route path="/mail" element={<Protected><MailPage /></Protected>} />
      <Route path="/chat" element={<Protected><ChatPage /></Protected>} />
      <Route path="/chat/:id" element={<Protected><ChatPage /></Protected>} />
      <Route path="/settings" element={<Protected><SettingsPage /></Protected>} />
      <Route path="/edit/:id" element={<ProtectedBare><OfficeEditorPage /></ProtectedBare>} />
      <Route path="/admin" element={<Protected><AdminPage /></Protected>} />
      <Route path="*" element={<Navigate to="/files" replace />} />
    </Routes>
  );
}
