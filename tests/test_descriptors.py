"""RDKit contra valores conocidos de PubChem.

Nota sobre que se afirma y que no. El peso molecular y la TPSA se comparan con
tolerancia estrecha: son magnitudes deterministas que RDKit y PubChem calculan
con la misma definicion. El **logP no se compara con PubChem**, y no es un
descuido: `Crippen.MolLogP` (RDKit) y `XLogP3` (PubChem) son dos algoritmos
distintos que discrepan de forma rutinaria en varias decimas. Exigir que
coincidan seria fijar en una prueba una igualdad que no existe.

El recuento de aceptores de enlace de hidrogeno tiene el mismo problema: RDKit
usa la definicion de Lipinski, mas restrictiva que la de PubChem.
"""

from __future__ import annotations

import pytest

from app.errors import ApiError

rdkit = pytest.importorskip("rdkit", reason="RDKit no esta instalado en este entorno")

from app.services.rdkit_descriptors import compute_descriptors, rdkit_version  # noqa: E402

ASPIRINA = "CC(=O)Oc1ccccc1C(=O)O"
CAFEINA = "Cn1cnc2c1c(=O)n(C)c(=O)n2C"
ACIDO_SALICILICO = "OC(=O)c1ccccc1O"


def test_aspirina_contra_pubchem_cid_2244() -> None:
    resultado = compute_descriptors(ASPIRINA)

    assert resultado.molecular_weight == pytest.approx(180.16, abs=0.05)
    assert resultado.tpsa == pytest.approx(63.6, abs=0.5)
    assert resultado.h_bond_donors == 1
    assert resultado.formula == "C9H8O4"


def test_cafeina_contra_pubchem_cid_2519() -> None:
    resultado = compute_descriptors(CAFEINA)

    assert resultado.molecular_weight == pytest.approx(194.19, abs=0.05)
    assert resultado.h_bond_donors == 0
    assert resultado.formula == "C8H10N4O2"

    # Aqui RDKit y PubChem NO coinciden, y conviene dejarlo escrito: PubChem
    # publica 58.4 A^2 y RDKit calcula 61.8. La discrepancia viene de como trata
    # cada implementacion los nitrogenos aromaticos del anillo de imidazol
    # fusionado. Se fija el valor de RDKit porque es el que sale de este
    # servicio; si algun dia cambia, la prueba debe fallar y obligar a mirarlo.
    assert resultado.tpsa == pytest.approx(61.8, abs=0.5)


def test_acido_salicilico_contra_pubchem_cid_338() -> None:
    resultado = compute_descriptors(ACIDO_SALICILICO)

    assert resultado.molecular_weight == pytest.approx(138.12, abs=0.05)
    assert resultado.h_bond_donors == 2
    assert resultado.formula == "C7H6O3"


def test_el_logp_nunca_se_declara_verificado() -> None:
    resultado = compute_descriptors(ASPIRINA)

    # DATA_SOURCES §3.3: un logP calculado viaja como 'estimated' hasta la
    # interfaz. Si esta linea falla, el producto empieza a mentir sobre la
    # calidad de sus datos.
    assert resultado.source.level == "estimated"
    assert resultado.source.type == "calculated"
    assert resultado.source.db == "RDKit"
    assert resultado.source.version == rdkit_version()


def test_el_logp_cae_en_el_rango_publicado() -> None:
    # No se compara con XLogP3: son algoritmos distintos. Solo se comprueba que
    # el valor sea fisicamente razonable para la aspirina (~1.2 experimental).
    resultado = compute_descriptors(ASPIRINA)
    assert 0.5 < resultado.log_p < 2.5


def test_smiles_invalido_es_error_de_validacion() -> None:
    with pytest.raises(ApiError) as excinfo:
        compute_descriptors("esto no es un SMILES")

    assert excinfo.value.code == "VALIDATION_ERROR"
    assert excinfo.value.http_status == 400


def test_smiles_canonico_es_estable() -> None:
    # El mismo compuesto escrito de dos formas debe producir el mismo canonico.
    uno = compute_descriptors("OC(=O)c1ccccc1O")
    otro = compute_descriptors("c1ccc(c(c1)C(=O)O)O")

    assert uno.canonical_smiles == otro.canonical_smiles


def test_endpoint_devuelve_descriptores(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/descriptors",
        json={"smiles": ASPIRINA},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["molecular_weight"] == pytest.approx(180.16, abs=0.05)
    assert body["source"]["level"] == "estimated"


def test_endpoint_rechaza_smiles_invalido(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/descriptors",
        json={"smiles": "%%%"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
