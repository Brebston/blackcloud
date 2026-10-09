// Рядки інтерфейсу: misc. Ключі мають префікс "misc.". en має містити ті самі ключі, що й uk.
export const uk = {
  "misc.close": "Закрити",
  "misc.errorStatus": "Помилка {status}",
  "misc.unknownError": "Невідома помилка",
};

export const en: Record<keyof typeof uk, string> = {
  "misc.close": "Close",
  "misc.errorStatus": "Error {status}",
  "misc.unknownError": "Unknown error",
};
