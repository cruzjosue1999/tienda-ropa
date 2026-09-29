# Sistema de Tienda de Ropa 👕

Sistema completo en español para vender ropa por internet, mobile-first:

- **Tienda pública** (`/`) — instalable como app (PWA): catálogo con fotos, precios, tallas, stock, carrito y pago con tarjeta vía Stripe.
- **App de administración** (`/admin`) — instalable como app (PWA), protegida con contraseña: crear/editar/borrar productos, subir fotos, ajustar inventario, publicar u ocultar productos, ver pedidos y configurar Stripe.

## Arranque

```bash
cd ~/workspace/tienda-ropa-sistema
./start.sh                 # puerto 8080
PORT=9000 ./start.sh       # otro puerto
```

- Tienda: http://localhost:8080/
- Admin: http://localhost:8080/admin

Variables de entorno opcionales:

| Variable         | Uso                                                        |
|------------------|------------------------------------------------------------|
| `PORT`           | Puerto (default 8080)                                      |
| `ADMIN_PASSWORD` | Crea la contraseña de admin en el primer arranque          |
| `SECRET_KEY`     | Clave de sesiones (si no se da, se genera y se guarda)     |
| `BASE_URL`       | URL pública (ej: `https://tu-tienda.example.com`), necesaria para que Stripe redirija bien |

## Primera configuración

1. Abre `/admin`. Si es la primera vez, crea tu contraseña (mínimo 8 caracteres) o usa `ADMIN_PASSWORD`.
2. Ve a la pestaña **Ajustes**: pon el nombre de tu tienda y tus claves de Stripe:
   - Clave secreta (`sk_live_…`) — **solo vive en el servidor**, nunca se envía al cliente.
   - Clave pública (`pk_live_…`) y secreto del webhook (`whsec_…`, recomendado).
3. Sin claves de Stripe, el botón de pagar muestra un aviso de que el dueño debe configurarlas.
4. Crea productos en la pestaña **Productos**: foto, nombre, precio, tallas, SKU y stock.

## Cobros con tarjeta

El servidor valida precios y stock contra la base de datos y crea una **Stripe Checkout Session**; el cliente paga en la página segura de Stripe. Al confirmarse el pago (webhook o verificación al volver), el pedido se marca como pagado y se descuenta el inventario automáticamente.

Para el webhook en producción: en tu panel de Stripe agrega el endpoint `https://tu-dominio/api/stripe-webhook` y pega el `whsec_…` en Ajustes.

## Poner las apps en la pantalla principal (iPhone/Android)

1. Abre la tienda (`/`) en Safari/Chrome.
2. Compartir → **Añadir a pantalla de inicio**.
3. Repite con `/admin` para la app de administración (usa sus propios iconos y nombre).

## Publicar en internet

El servidor debe ser accesible públicamente (Stripe necesita alcanzar tu URL). Opciones: un túnel (ej: Cloudflare Tunnel) o un hosting (Render, Railway, VPS). Luego define `BASE_URL` con tu dominio.

## Datos

- Base de datos SQLite: `data/tienda.db`
- Fotos subidas: `data/uploads/` (máx. 5 MB, solo PNG/JPG/WEBP/GIF)
- Haz copia de seguridad de la carpeta `data/` periódicamente.

## API (resumen)

- `GET /api/products` — catálogo público (solo productos visibles)
- `POST /api/checkout` — `{items: [{id, size, qty}]}` → `{url}` de Stripe
- `POST /api/stripe-webhook` — eventos de Stripe
- `POST /api/confirm-payment` — verifica una sesión al volver de Stripe
- Admin (requiere login): `/api/admin/products` (CRUD), `/api/upload`, `/api/admin/orders`, `/api/admin/settings`, `/api/admin/change-password`
