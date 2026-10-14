"""One command for local runs: migrate, seed the demo project, serve on the given port.

    python dev.py            # http://127.0.0.1:8000
    python dev.py --port 8077
"""

import argparse
import os

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--port", default=os.environ.get("PORT", "8000"))
parser.add_argument("--host", default="127.0.0.1")
args = parser.parse_args()

# Must be set before Django reads settings: the mock systems and issuer live on this port.
os.environ["PORT"] = str(args.port)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402
import uvicorn  # noqa: E402
from django.core.management import call_command  # noqa: E402

django.setup()
call_command("migrate", verbosity=0)
call_command("seed_demo")
print(f"Console: http://{args.host}:{args.port}/")
uvicorn.run("config.asgi:application", host=args.host, port=int(args.port))
