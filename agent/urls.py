from django.urls import path

from . import views

app_name = "agent"

urlpatterns = [
    path("history/", views.conversation_history, name="history"),
    path("chat/", views.chat, name="chat"),
]
