from django.core.management.base import BaseCommand

from workspace import demo


class Command(BaseCommand):
    help = "Create (or reset) demo project P-24117 with two cabinets, two drives and a panel."

    def handle(self, *args, **options):
        project = demo.seed()
        self.stdout.write(self.style.SUCCESS(f"Seeded {project.id} with {project.items.count()} items"))
