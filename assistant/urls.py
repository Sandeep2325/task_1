from django.urls import path

from . import views

urlpatterns = [
    path("assistant/tools", views.tool_list),
    path("assistant/tools/<str:name>", views.tool_invoke),
    path("confirmations/<uuid:action_id>/confirm", views.confirm),
]
