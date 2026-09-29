#!/usr/bin/env bash
# Arranca el sistema de tienda de ropa.
# Uso: ./start.sh   (puerto por defecto 8080; cámbialo con PORT=9000 ./start.sh)
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8080}"

if [ ! -d venv ]; then
  echo "Creando entorno virtual e instalando dependencias…"
  python3 -m venv venv
  ./venv/bin/pip install -q flask stripe waitress pillow
fi

export PORT
echo "Iniciando tienda en el puerto $PORT…"
exec ./venv/bin/python -c "
import os
os.environ.setdefault('PORT', '$PORT')
from waitress import serve
import app as tienda
tienda.main_serve = True
# init_db + seed de ADMIN_PASSWORD ocurren en serve_app()
import app
app.init_db()
pw = os.environ.get('ADMIN_PASSWORD')
with app.app.app_context():
    if pw and not app.admin_password_set():
        from werkzeug.security import generate_password_hash
        app.set_setting('admin_password_hash', generate_password_hash(pw))
        print('Contraseña de admin creada desde ADMIN_PASSWORD.')
    if not app.admin_password_set():
        print('AVISO: aún no hay contraseña de admin. Créala en /admin/setup')
print(f'Tienda lista en http://localhost:{os.environ[\"PORT\"]}  |  Admin en http://localhost:{os.environ[\"PORT\"]}/admin')
serve(app.app, host='0.0.0.0', port=int(os.environ['PORT']))
"
