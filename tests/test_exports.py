"""El libro Excel tiene las 7 hojas del README §16 y no inventa datos."""

from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from tests.conftest import SIMULATION_ID, simulation_row

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(client, fake_db, auth_headers, **params):  # noqa: ANN001, ANN201
    fake_db.seed("simulations", [simulation_row()])
    fake_db.seed("ai_reports", [])
    return client.get(f"/api/v1/exports/{SIMULATION_ID}.xlsx", headers=auth_headers, params=params)


def test_devuelve_un_xlsx_descargable(client, fake_db, auth_headers) -> None:
    response = _download(client, fake_db, auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == _XLSX
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.xlsx"')


def test_contiene_las_siete_hojas(client, fake_db, auth_headers) -> None:
    response = _download(client, fake_db, auth_headers)
    book = load_workbook(BytesIO(response.content))

    assert book.sheetnames == [
        "0. Portada",
        "1. Formulacion",
        "2. Propiedades",
        "3. Evidencia",
        "4. Simulacion",
        "5. Machine Learning",
        "6. Analisis IA",
        "7. Revision regulatoria",
    ]


def test_la_hoja_de_ml_declara_que_no_hay_modelo(client, fake_db, auth_headers) -> None:
    # ADR-002: no hay modelo entrenado. La hoja debe decirlo, no sugerir uno.
    response = _download(client, fake_db, auth_headers)
    book = load_workbook(BytesIO(response.content))
    hoja = book["5. Machine Learning"]

    texto = " ".join(str(cell.value or "") for row in hoja.iter_rows() for cell in row)
    assert "Potts-Guy" in texto
    assert "No se emplea ningun modelo de aprendizaje automatico" in texto
    assert "No cuantificada" in texto


def test_el_indice_de_irritacion_se_etiqueta_como_heuristico(client, fake_db, auth_headers) -> None:
    response = _download(client, fake_db, auth_headers)
    book = load_workbook(BytesIO(response.content))

    texto = " ".join(
        str(cell.value or "")
        for nombre in ("4. Simulacion", "6. Analisis IA")
        for row in book[nombre].iter_rows()
        for cell in row
    )
    assert "HEURISTICO" in texto or "heuristico" in texto


def test_sin_reporte_de_ia_la_hoja_6_lo_dice(client, fake_db, auth_headers) -> None:
    # La exportacion no puede depender de que el proveedor de IA este disponible.
    response = _download(client, fake_db, auth_headers)
    book = load_workbook(BytesIO(response.content))

    hoja = book["6. Analisis IA"]
    texto = " ".join(str(cell.value or "") for row in hoja.iter_rows() for cell in row)
    assert "No se ha generado ningun reporte" in texto


def test_la_hoja_regulatoria_incluye_el_descargo(client, fake_db, auth_headers) -> None:
    response = _download(client, fake_db, auth_headers)
    book = load_workbook(BytesIO(response.content))

    texto = " ".join(
        str(cell.value or "") for row in book["7. Revision regulatoria"].iter_rows() for cell in row
    )
    assert "No es una validacion regulatoria" in texto


def test_puede_omitirse_la_revision_regulatoria(client, fake_db, auth_headers) -> None:
    response = _download(client, fake_db, auth_headers, include_regulatory=False)
    book = load_workbook(BytesIO(response.content))

    texto = " ".join(
        str(cell.value or "") for row in book["7. Revision regulatoria"].iter_rows() for cell in row
    )
    assert "No ejecutada" in texto
