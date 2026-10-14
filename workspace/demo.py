"""Demo project P-24117 from the brief: a water plant pump room."""

from mocks.systems import CATALOGUE

from .models import PlacedItem, Project

PROJECT_ID = "P-24117"

ITEMS = [
    # tag, variant, position, parent tag, sizing inputs
    ("CAB-01", "CWC-0800", {"x": 2, "y": 2, "rotation": 0}, None, None),
    ("CAB-02", "CWC-1200", {"x": 8, "y": 4, "rotation": 0}, None, None),
    ("DRV-03", "CWD-R7-090-4", {"x": 4, "y": 2, "rotation": 0}, "CAB-01", {"power_kw": 75, "voltage": 400, "load_pct": 100}),
    ("DRV-04", "CWD-R6-055-4", {"x": 6, "y": 2, "rotation": 0}, "CAB-01", {"power_kw": 45, "voltage": 400, "load_pct": 100}),
    ("PNL-S1", "CWP-S1", {"x": 3, "y": 5, "rotation": 90}, None, None),
]


def seed() -> Project:
    project, _ = Project.objects.update_or_create(id=PROJECT_ID, defaults={"name": "Water plant pump room"})
    by_tag = {}
    for tag, code, position, parent, inputs in ITEMS:
        entry = CATALOGUE[code]
        by_tag[tag], _ = PlacedItem.objects.update_or_create(
            project=project,
            tag=tag,
            defaults={
                "kind": entry["kind"],
                "variant_code": code,
                "model_ref": {"asset_id": entry["model"]["asset_id"], "lods": entry["model"]["lods"]},
                "position": position,
                "parent": by_tag.get(parent),
                "sizing_inputs": inputs,
                "sizing_result": None,
                "sized_at": None,
                "revision": 1,
            },
        )
    return project
