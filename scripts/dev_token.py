"""Emite un JWT de desarrollo para probar la API a mano.

Sirve para una cosa y solo una: firmar un token con el `SUPABASE_JWT_SECRET`
**local** para poder llamar a los endpoints con `curl` sin montar el frontend. El
token que produce no vale contra el Supabase real —solo lo acepta un servidor que
comparta ese mismo secreto de desarrollo—, y eso es exactamente lo que se quiere:
no hay forma de que un token de este script abra nada en produccion.

Requiere `SUPABASE_JWT_SECRET`, asi que no funciona con proyectos que firmen con
claves asimetricas. Ahi el token hay que sacarlo de la sesion real del frontend
(`supabase.auth.getSession()`), porque solo Supabase tiene la clave privada.

Uso:
    python scripts/dev_token.py
    python scripts/dev_token.py --user-id <uuid> --expires-in 7200
    curl -H "Authorization: Bearer $(python scripts/dev_token.py)" ...
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jose import jwt  # noqa: E402

from app.config import get_settings  # noqa: E402

# UUID fijo por defecto: asi las filas que se creen probando pertenecen siempre
# al mismo usuario y se pueden limpiar de una vez.
DEFAULT_USER_ID = "11111111-1111-4111-8111-111111111111"


def main() -> int:
    parser = argparse.ArgumentParser(description="Emite un JWT de desarrollo.")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="Valor del claim 'sub'")
    parser.add_argument("--email", default="formulador@ejemplo.test")
    parser.add_argument("--expires-in", type=int, default=3600, help="Segundos de validez")
    parser.add_argument(
        "--curl",
        action="store_true",
        help="Imprime el encabezado completo en vez del token pelado",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.supabase_jwt_secret:
        print(
            "SUPABASE_JWT_SECRET no esta configurado.\n\n"
            "Este script solo puede firmar tokens simetricos. Si el proyecto usa\n"
            "JWT Signing Keys asimetricas, la clave privada la tiene Supabase y el\n"
            "token hay que obtenerlo de la sesion real del frontend:\n"
            "  const { data } = await supabase.auth.getSession()\n"
            "  data.session.access_token",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": args.user_id,
            "aud": settings.supabase_jwt_audience,
            "role": "authenticated",
            "email": args.email,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=args.expires_in)).timestamp()),
        },
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )

    print(f"Authorization: Bearer {token}" if args.curl else token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
