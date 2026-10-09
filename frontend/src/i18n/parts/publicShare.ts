// Рядки інтерфейсу: publicShare. Ключі мають префікс "publicShare.". en має містити ті самі ключі, що й uk.
export const uk = {
  "publicShare.invalidLink": "Посилання недійсне",
  "publicShare.enterPassword": "Введіть пароль.",
  "publicShare.error": "Помилка",
  "publicShare.loading": "Завантаження…",
  "publicShare.availableUntil": "доступно до {date}",
  "publicShare.password": "Пароль",
  "publicShare.download": "Завантажити",
};

export const en: Record<keyof typeof uk, string> = {
  "publicShare.invalidLink": "This link is invalid",
  "publicShare.enterPassword": "Enter the password.",
  "publicShare.error": "Error",
  "publicShare.loading": "Loading…",
  "publicShare.availableUntil": "available until {date}",
  "publicShare.password": "Password",
  "publicShare.download": "Download",
};
