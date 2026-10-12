from django.urls import path

from .views import SubscriptionsView, TestPushView, VapidKeyView

urlpatterns = [
    path("vapid-key/", VapidKeyView.as_view()),
    path("subscriptions/", SubscriptionsView.as_view()),
    path("test/", TestPushView.as_view()),
]
