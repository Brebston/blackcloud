// Рядки інтерфейсу: office. Ключі мають префікс "office.". en має містити ті самі ключі, що й uk.
export const uk = {
  "office.serverUnavailable": "Сервер документів недоступний",
  "office.backToFiles": "Назад до файлів",
  "office.document": "Документ",
  "office.autosaveHint": "Зміни зберігаються автоматично як нова версія файлу",
  "office.returnToFiles": "Повернутися до файлів",
};

export const en: Record<keyof typeof uk, string> = {
  "office.serverUnavailable": "The document server is unavailable",
  "office.backToFiles": "Back to files",
  "office.document": "Document",
  "office.autosaveHint": "Changes are saved automatically as a new file version",
  "office.returnToFiles": "Back to files",
};
