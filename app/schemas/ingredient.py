"""Catalogo de activos y procedencia por campo.

La regla que gobierna este modulo esta en DATA_SOURCES §3.3: la procedencia es
**por campo, no por fila**. Un mismo ingrediente puede tener el peso molecular
verificado y el logP apenas estimado, y esa diferencia tiene que sobrevivir el
viaje hasta la interfaz. Un `data_level` unico por fila borraria justo el matiz
que decide si un formulador puede confiar en el numero.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.simulation import DataLevel, RiskFlag

SourceType = Literal["experimental", "calculated", "predicted", "declared"]


class FieldSource(BaseModel):
    """Procedencia de un unico valor."""

    model_config = ConfigDict(extra="ignore")

    db: str = Field(description="PubChem, RDKit, CosIng, ficha del proveedor...")
    id: str | None = Field(default=None, description="Identificador estable, p. ej. 'CID 338'")
    version: str | None = None
    type: SourceType | None = None
    level: DataLevel


class DescriptorsRequest(BaseModel):
    smiles: str = Field(min_length=1, max_length=1000)


class DescriptorsResponse(BaseModel):
    """Descriptores calculados por RDKit a partir de la estructura.

    `source.level` es siempre `estimated`. `Crippen.MolLogP` es un logP
    *calculado*: llamarlo verificado seria exactamente el tipo de sobredeclaracion
    que el proyecto no admite.
    """

    molecular_weight: float
    log_p: float
    tpsa: float
    h_bond_donors: int
    h_bond_acceptors: int
    rotatable_bonds: int
    heavy_atoms: int
    canonical_smiles: str
    formula: str
    source: FieldSource


class ResolveRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=5, ge=1, le=20)


class PubChemCandidate(BaseModel):
    """Candidato de PubChem, con la advertencia de logP incorporada.

    PubChem devuelve casi siempre `XLogP3`, que es calculado por computadora, no
    medido. Un error de 0.5 en logP desplaza `log Kp` en 0.35: mas de un factor 2
    en permeabilidad. Por eso el candidato llega marcado y la curacion final es
    manual (DATA_SOURCES §3.2).
    """

    cid: int
    name: str
    iupac_name: str | None = None
    molecular_formula: str | None = None
    molecular_weight: float | None = None
    xlogp: float | None = None
    tpsa: float | None = None
    h_bond_donors: int | None = None
    h_bond_acceptors: int | None = None
    canonical_smiles: str | None = None
    sources: dict[str, FieldSource] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ResolveResponse(BaseModel):
    query: str
    candidates: list[PubChemCandidate]
    note: str


class IngredientRecord(BaseModel):
    """Fila de `public.ingredients`."""

    model_config = ConfigDict(extra="ignore")

    id: str
    owner_id: str | None = None
    name: str
    inci_name: str | None = None
    molecular_weight: float
    log_p: float
    pka: float | None = None
    category: str | None = None
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    sources: dict[str, FieldSource] = Field(default_factory=dict)
    data_level: DataLevel = "heuristic"
    max_use_concentration: float | None = None
    regulation_ref: str | None = None
    regulation_version: str | None = None
    regulation_checked_at: str | None = None


class IngredientListResponse(BaseModel):
    items: list[IngredientRecord]
    count: int
