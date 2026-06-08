from django.urls import path

from . import views

app_name = "agent"

urlpatterns = [
    path("conversations/", views.list_conversations, name="list_conversations"),
    path("conversations/new/", views.new_conversation, name="new_conversation"),
    path("conversations/<int:conversation_id>/delete/", views.delete_conversation, name="delete_conversation"),
    path("history/", views.conversation_history, name="history"),
    path("chat/", views.chat, name="chat"),
    path("chat/stream/", views.chat_stream, name="chat_stream"),
    path("rate-calculator/calculate/", views.rate_calculator_calculate, name="rate_calculator_calculate"),
    path("rate-report/save/", views.rate_report_save, name="rate_report_save"),
    path("rate-report/list/", views.rate_report_list, name="rate_report_list"),
]
