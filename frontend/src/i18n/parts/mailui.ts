// Рядки інтерфейсу: mailui. Ключі мають префікс "mailui.". en має містити ті самі ключі, що й uk.
export const uk = {
  "mailui.range": "{from}–{to} з {total}",
  "mailui.prevPage": "Новіші",
  "mailui.nextPage": "Старіші",
  "mailui.star": "Позначити зірочкою",
  "mailui.unstar": "Зняти зірочку",
  "mailui.hasAttachments": "Є вкладення",
  "mailui.backToList": "Назад до списку",
  "mailui.folders": "Теки пошти",
};

export const en: Record<keyof typeof uk, string> = {
  "mailui.range": "{from}–{to} of {total}",
  "mailui.prevPage": "Newer",
  "mailui.nextPage": "Older",
  "mailui.star": "Star",
  "mailui.unstar": "Unstar",
  "mailui.hasAttachments": "Has attachments",
  "mailui.backToList": "Back to list",
  "mailui.folders": "Mail folders",
};
