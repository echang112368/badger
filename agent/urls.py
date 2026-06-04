from django.urls import path

from . import views

app_name = "agent"

urlpatterns = [
    path("conversations/", views.list_conversations, name="list_conversations"),
    path("conversations/new/", views.new_conversation, name="new_conversation"),
    path("conversations/<int:conversation_id>/delete/", views.delete_conversation, name="delete_conversation"),
    path("history/", views.conversation_history, name="history"),
    path("chat/", views.chat, name="chat"),
]
