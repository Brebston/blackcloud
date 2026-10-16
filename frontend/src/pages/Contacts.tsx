import { useNavigate } from "react-router-dom";
import { ContactsBook } from "../components/ContactsDialog";
import { useT } from "../i18n";

// Окремий розділ «Контакти» (у мобільному меню «Ще» і в бічному меню). «Написати» відкриває лист у пошті.
export default function ContactsPage() {
  const t = useT();
  const navigate = useNavigate();
  return (
    <div className="page contacts-page">
      <div className="page-head">
        <h2>{t("contacts.title")}</h2>
      </div>
      <div className="card">
        <ContactsBook onWrite={(to) => navigate("/mail", { state: { compose: { to } } })} />
      </div>
    </div>
  );
}
