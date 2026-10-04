from django.urls import path

from . import views

urlpatterns = [
    path("conversations/", views.ConversationsView.as_view()),
    path("conversations/<uuid:pk>/", views.ConversationDetailView.as_view()),
    path("conversations/<uuid:pk>/messages/", views.MessagesView.as_view()),
    path("conversations/<uuid:pk>/read/", views.ReadView.as_view()),
    path("conversations/<uuid:pk>/leave/", views.LeaveView.as_view()),
    path("conversations/<uuid:pk>/members/", views.MembersView.as_view()),
    path("messages/<uuid:pk>/", views.MessageDetailView.as_view()),
]
