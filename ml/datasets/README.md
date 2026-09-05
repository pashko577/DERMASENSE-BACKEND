# Datasets

## `flynn.csv` — pendiente

Conjunto clasico de permeabilidad percutanea (~90 compuestos) del que se derivo
la propia correlacion de Potts-Guy. Mientras no exista este archivo, no hay nada
que entrenar y `ml/` permanece fuera del camino de ejecucion.

Columnas esperadas por `ml/train_kp.py`:

| Columna | Tipo | Descripcion |
|---|---|---|
| `compound_name` | texto | Nombre del compuesto |
| `molecular_weight` | numero | g/mol |
| `log_p` | numero | Particion octanol/agua, preferentemente experimental |
| `log_kp_measured` | numero | Valor **medido**, no calculado |
| `source` | texto | Cita de la publicacion original |

El campo que decide si el dataset sirve es `log_kp_measured`. Si viniera de una
correlacion en lugar de una medida, entrenar sobre el solo reproduciria la
correlacion de partida con pasos extra.

Ver `docs/DATA_SOURCES.md` §8 en el repositorio frontend.
