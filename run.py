import os
from pathlib import Path
import django
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from wsgiref.simple_server import make_server

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
Path(os.environ.get("DATABASE_PATH", "instance/parse_lab.db")).parent.mkdir(parents=True, exist_ok=True)
call_command("migrate", interactive=False, verbosity=0)
from catalog.seed import ensure_seed
ensure_seed()
host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", "6200"))
app = get_wsgi_application()
print(f"cc-parse-lab on http://{host}:{port}/login", flush=True)
make_server(host, port, app).serve_forever()
