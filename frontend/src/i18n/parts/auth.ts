// Рядки інтерфейсу: auth. Ключі мають префікс "auth.". en має містити ті самі ключі, що й uk.
export const uk = {
  "auth.title": "Реєстрація",
  "auth.badUsername": "3–40 символів: малі латинські літери, цифри, . _ -",
  "auth.badEmail": "Невірний email.",
  "auth.pwTooShort": "Мінімум 12 символів.",
  "auth.pwMismatch": "Паролі не збігаються.",
  "auth.username": "Ім'я користувача",
  "auth.email": "Email",
  "auth.password": "Пароль",
  "auth.repeatPassword": "Повторіть пароль",
  "auth.invite": "Код запрошення",
  "auth.creating": "Створення…",
  "auth.createAccount": "Створити акаунт",
  "auth.haveAccount": "Вже є акаунт?",
  "auth.signIn": "Увійти",
};

export const en: Record<keyof typeof uk, string> = {
  "auth.title": "Sign up",
  "auth.badUsername": "3–40 characters: lowercase Latin letters, digits, . _ -",
  "auth.badEmail": "Invalid email.",
  "auth.pwTooShort": "At least 12 characters.",
  "auth.pwMismatch": "Passwords don't match.",
  "auth.username": "Username",
  "auth.email": "Email",
  "auth.password": "Password",
  "auth.repeatPassword": "Confirm password",
  "auth.invite": "Invite code",
  "auth.creating": "Creating…",
  "auth.createAccount": "Create account",
  "auth.haveAccount": "Already have an account?",
  "auth.signIn": "Sign in",
};
