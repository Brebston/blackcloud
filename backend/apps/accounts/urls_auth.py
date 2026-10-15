from django.urls import path

from . import views, views_passkeys

urlpatterns = [
    path("csrf/", views.CsrfView.as_view()),
    path("me/", views.MeView.as_view()),
    path("login/", views.LoginView.as_view()),
    path("login/2fa/", views.LoginTwoFactorView.as_view()),
    # Ключі доступу (passkeys): під /login/, щоб діяв і суворий ліміт частоти Traefik
    path("login/passkey/begin/", views_passkeys.PasskeyLoginBeginView.as_view()),
    path("login/passkey/finish/", views_passkeys.PasskeyLoginFinishView.as_view()),
    path("login/2fa/passkey/begin/", views_passkeys.PasskeySecondFactorBeginView.as_view()),
    path("login/2fa/passkey/finish/", views_passkeys.PasskeySecondFactorFinishView.as_view()),
    path("logout/", views.LogoutView.as_view()),
    path("register/", views.RegisterView.as_view()),
]
