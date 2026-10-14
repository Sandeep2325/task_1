from django.urls import path

from . import views

urlpatterns = [
    path("projects/<str:project_id>", views.project_layout),
    path("projects/<str:project_id>/bom", views.project_bom),
    path("projects/<str:project_id>/items/<str:tag>/sidebar", views.sidebar),
    path("projects/<str:project_id>/items/<str:tag>/resize", views.resize),
    path("projects/<str:project_id>/items/<str:tag>/sizing/stream", views.sizing_stream),
]
