import uuid

from django.db import models


class Project(models.Model):
    """Working copy of a host project. The host application stays the source of truth."""

    id = models.CharField(primary_key=True, max_length=32)  # host project id, e.g. P-24117
    name = models.CharField(max_length=200)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.id


class PlacedItem(models.Model):
    """One object in the 3D room.

    `id` is the item's identity everywhere (scene, sizing, BOM, price). A re-size
    changes `variant_code` and `model_ref`, never `id`. `revision` gives
    optimistic concurrency so two writers cannot silently overwrite each other.
    """

    KINDS = [("drive", "Drive"), ("cabinet", "Cabinet"), ("panel", "Panel")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="items")
    tag = models.CharField(max_length=32)  # label shown in 3D, e.g. DRV-03
    kind = models.CharField(max_length=16, choices=KINDS)
    variant_code = models.CharField(max_length=64)
    model_ref = models.JSONField()  # {"asset_id": ..., "lods": {...}} from the catalogue
    position = models.JSONField(default=dict)  # {"x": 3, "y": 1, "rotation": 0} on the floor grid
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    sizing_inputs = models.JSONField(null=True, blank=True)
    sizing_result = models.JSONField(null=True, blank=True)  # last result from the sizing tool
    sized_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "tag"], name="unique_tag_per_project")]
        ordering = ["tag"]

    def __str__(self):
        return f"{self.project_id}/{self.tag}"
