from django.core.management.base import BaseCommand

from workspace import auth


class Command(BaseCommand):
    help = "Print a dev bearer token signed by the mock OIDC issuer."

    def add_arguments(self, parser):
        parser.add_argument("--role", default="sales_engineer", choices=sorted(auth.ROLES))
        parser.add_argument("--country", default="DE")
        parser.add_argument("--customer-group", default="standard")
        parser.add_argument("--sub", default=None)

    def handle(self, *args, role, country, customer_group, sub, **options):
        self.stdout.write(
            auth.mint_dev_token(sub=sub or f"{role}-demo", role=role, country=country, customer_group=customer_group)
        )
