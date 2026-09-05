"""Descriptores moleculares con RDKit.

Esta es la razon fuerte de que el servicio sea Python. RDKit es un binario C++
sin equivalente en el navegador; todo lo demas que vive aqui podria discutirse,
esto no.

La importacion es diferida a proposito. RDKit es un wheel de ~150 MB y su
ausencia no debe impedir que arranque el resto de la API (reportes, Excel,
regulatorio y voz no dependen de el). Sin RDKit, este endpoint responde
`503`; los demas siguen funcionando. Es la misma via de escape que describe el
README §7.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.errors import ApiError
from app.schemas.ingredient import DescriptorsResponse, FieldSource


@lru_cache(maxsize=1)
def _rdkit() -> tuple[Any, Any, Any, str]:
    try:
        import rdkit
        from rdkit import Chem, RDLogger
        from rdkit.Chem import Descriptors

        # RDKit escribe los SMILES invalidos en stderr; el error ya viaja en la
        # respuesta HTTP y ensuciar el log no aporta nada.
        RDLogger.DisableLog("rdApp.error")
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            "El calculo de descriptores no esta disponible: RDKit no esta instalado.",
            {"hint": "pip install rdkit"},
        ) from exc
    return Chem, Descriptors, rdkit, str(rdkit.__version__)


def rdkit_version() -> str | None:
    """Version instalada, o `None` si RDKit no esta disponible."""
    try:
        return _rdkit()[3]
    except ApiError:
        return None


def compute_descriptors(smiles: str) -> DescriptorsResponse:
    """SMILES → descriptores, con la procedencia pegada al resultado."""
    chem, descriptors, _, version = _rdkit()

    molecule = chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ApiError(
            "VALIDATION_ERROR",
            "El SMILES no describe una molecula valida.",
            {"smiles": smiles},
        )

    from rdkit.Chem import rdMolDescriptors

    return DescriptorsResponse(
        molecular_weight=round(descriptors.MolWt(molecule), 2),
        # Crippen: un logP *calculado*. De ahi que el nivel sea 'estimated'.
        log_p=round(descriptors.MolLogP(molecule), 2),
        tpsa=round(descriptors.TPSA(molecule), 2),
        h_bond_donors=int(descriptors.NumHDonors(molecule)),
        h_bond_acceptors=int(descriptors.NumHAcceptors(molecule)),
        rotatable_bonds=int(descriptors.NumRotatableBonds(molecule)),
        heavy_atoms=int(molecule.GetNumHeavyAtoms()),
        canonical_smiles=chem.MolToSmiles(molecule),
        formula=rdMolDescriptors.CalcMolFormula(molecule),
        source=FieldSource(
            db="RDKit",
            version=version,
            type="calculated",
            level="estimated",
        ),
    )
