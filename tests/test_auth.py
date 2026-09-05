"""401 sin JWT valido, 403 con recurso ajeno.

La segunda propiedad merece una nota. RLS ya impide que la simulacion de otro
usuario llegue siquiera a este servicio, asi que en produccion el 403 no deberia
ocurrir nunca. Lo que se prueba aqui es la defensa en profundidad: si una
politica se relajara por error, el servicio sigue negando el acceso en lugar de
convertirse en la via de fuga.
"""

from __future__ import annotations

from tests.conftest import OTHER_USER_ID, SIMULATION_ID, make_token, simulation_row


def test_health_no_requiere_autenticacion(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "capabilities" in body


def test_sin_encabezado_authorization_devuelve_401(client) -> None:
    response = client.post("/api/v1/descriptors", json={"smiles": "CCO"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_esquema_incorrecto_devuelve_401(client) -> None:
    response = client.post(
        "/api/v1/descriptors",
        json={"smiles": "CCO"},
        headers={"Authorization": "Basic dXN1YXJpbzpjbGF2ZQ=="},
    )

    assert response.status_code == 401


def test_token_con_firma_ajena_devuelve_401(client) -> None:
    intruso = make_token(secret="otro-secreto-completamente-distinto")

    response = client.post(
        "/api/v1/descriptors",
        json={"smiles": "CCO"},
        headers={"Authorization": f"Bearer {intruso}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_token_expirado_devuelve_401(client) -> None:
    caducado = make_token(expires_in_seconds=-60)

    response = client.post(
        "/api/v1/descriptors",
        json={"smiles": "CCO"},
        headers={"Authorization": f"Bearer {caducado}"},
    )

    assert response.status_code == 401


def test_token_con_audiencia_incorrecta_devuelve_401(client) -> None:
    ajeno = make_token(audience="otro-servicio")

    response = client.post(
        "/api/v1/descriptors",
        json={"smiles": "CCO"},
        headers={"Authorization": f"Bearer {ajeno}"},
    )

    assert response.status_code == 401


def test_sesion_anonima_no_puede_operar(client) -> None:
    anonimo = make_token(extra_claims={"is_anonymous": True})

    response = client.post(
        "/api/v1/descriptors",
        json={"smiles": "CCO"},
        headers={"Authorization": f"Bearer {anonimo}"},
    )

    assert response.status_code == 403


def test_simulacion_de_otro_usuario_devuelve_403(client, fake_db, auth_headers) -> None:
    # Se simula el peor caso: PostgREST devuelve una fila ajena porque una
    # politica de RLS fallo. El servicio debe negarla igualmente.
    fake_db.seed("simulations", [simulation_row(user_id=OTHER_USER_ID)])

    response = client.get(f"/api/v1/exports/{SIMULATION_ID}.xlsx", headers=auth_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_simulacion_inexistente_devuelve_404(client, fake_db, auth_headers) -> None:
    fake_db.seed("simulations", [])

    response = client.get(f"/api/v1/exports/{SIMULATION_ID}.xlsx", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_formato_de_error_es_el_del_contrato(client) -> None:
    response = client.post("/api/v1/descriptors", json={"smiles": "CCO"})

    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message", "details"}
