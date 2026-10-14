from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt

from . import views

urlpatterns = [
    path("oidc/.well-known/openid-configuration", views.oidc_configuration),
    path("oidc/jwks", views.oidc_jwks),
    path("oidc/token", csrf_exempt(views.oidc_token)),
    re_path(r"^(?P<path>(catalogue|sizing|pricing)/.*)$", views.upstream),
]
