"""Cuanto vale la prediccion: error medido contra permeabilidades publicadas.

Responde a la pregunta que de verdad importa en una pantalla de formulacion:
*¿me puedo fiar de este log Kp para esta molecula concreta?*

No devuelve una prediccion nueva. Devuelve la de Potts-Guy —la misma que calcula
el motor en el navegador— acompanada del error que comete sobre compuestos
parecidos, medido contra datos experimentales publicados.

**Publico, sin sesion.** Es evidencia cientifica citable, igual para todo el
mundo: no gasta tokens, no toca datos de usuario y no revela nada que no este ya
publicado. Exigir sesion solo dejaria la pantalla de formulacion vacia para
quien todavia no ha entrado.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.deps import public_rate_limit
from app.services import validation as service

router = APIRouter(prefix="/validation", tags=["validacion"])


@router.get(
    "",
    summary="Prediccion de log Kp con su error medido sobre compuestos similares",
)
async def get_validation(
    molecular_weight: Annotated[float, Query(gt=0, le=100000)],
    log_p: Annotated[float, Query(ge=-10, le=15)],
    name: Annotated[str | None, Query(max_length=200)] = None,
    inci_name: Annotated[str | None, Query(max_length=200)] = None,
    neighbours: Annotated[int, Query(ge=3, le=25)] = service.NEIGHBOUR_COUNT,
    _: None = Depends(public_rate_limit(240, 60.0)),
) -> dict[str, object]:
    # El conjunto son ~230 filas cacheadas en memoria: calcular la vecindad aqui
    # es mas rapido que pedirsela a la base, y deja el criterio de "parecido"
    # escrito en Python, donde se puede leer y discutir.
    compounds = list(service.load_reference_set())

    result = service.evaluate(
        compounds,
        molecular_weight=molecular_weight,
        log_p=log_p,
        name=name,
        inci_name=inci_name,
        neighbours=neighbours,
    )
    result["query"] = {
        "name": name,
        "inci_name": inci_name,
        "molecular_weight": molecular_weight,
        "log_p": log_p,
    }
    return result
