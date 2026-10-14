import uuid

from django.db import models


class PendingAction(models.Model):
    """A write the assistant proposed. Only the same user, through the UI, can confirm it."""

    PENDING, DONE, FAILED, EXPIRED = "pending", "done", "failed", "expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sub = models.CharField(max_length=200)
    tool = models.CharField(max_length=64)
    arguments = models.JSONField()
    item_revision = models.PositiveIntegerField()  # the revision the user saw when proposing
    summary = models.TextField()
    status = models.CharField(max_length=16, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()


class AuditEvent(models.Model):
    """Every tool call and confirmation: who, what, with which arguments, and the outcome."""

    at = models.DateTimeField(auto_now_add=True)
    sub = models.CharField(max_length=200)
    role = models.CharField(max_length=32)
    action = models.CharField(max_length=64)
    arguments = models.JSONField(default=dict)
    outcome = models.CharField(max_length=32)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-at"]
