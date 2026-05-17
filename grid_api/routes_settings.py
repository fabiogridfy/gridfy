"""
grid_api/routes_settings.py
Endpoints para gestión de la BBDD de conductores y configuración del sistema.
"""
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# ── Conductor DB endpoints ─────────────────────────────────────────────────────

class ConductorIn(BaseModel):
    name:         str
    r_ohm_per_km: float
    x_ohm_per_km: float
    i_max_a:      float   # in Amperes (converted to kA internally)


@router.get("/settings/conductors")
def list_conductors():
    """Lista todos los conductores en la BBDD."""
    from grid_network.conductor_db import list_conductors as _list
    return _list()


@router.post("/settings/conductors")
def add_conductor(body: ConductorIn):
    """Añade o actualiza un conductor en la BBDD."""
    import pandas as pd, openpyxl

    db_path = os.path.join(os.path.dirname(__file__), "..", "conductors_lv.xlsx")
    if not os.path.exists(db_path):
        raise HTTPException(404, "BBDD de conductores no encontrada")

    # Read existing
    xl  = pd.ExcelFile(db_path)
    df  = xl.parse('conductors_parameters')
    xl.close()

    # Add or update row
    new_row = {
        'conductor_type':       body.name,
        'resistance_p_ohm_km':  body.r_ohm_per_km,
        'reactance_ohm_km':     body.x_ohm_per_km,
        'I_max':                body.i_max_a,
    }
    mask = df['conductor_type'] == body.name
    if mask.any():
        df.loc[mask, 'resistance_p_ohm_km'] = body.r_ohm_per_km
        df.loc[mask, 'reactance_ohm_km']    = body.x_ohm_per_km
        df.loc[mask, 'I_max']               = body.i_max_a
    else:
        import pandas as _pd
        df = _pd.concat([df, _pd.DataFrame([new_row])], ignore_index=True)

    # Write back — preserve other sheets
    with pd.ExcelWriter(db_path, engine='openpyxl', mode='a',
                        if_sheet_exists='replace') as writer:
        df.to_excel(writer, sheet_name='conductors_parameters', index=False)

    # Invalidate in-memory cache
    from grid_network import conductor_db
    conductor_db._db_loaded = False
    conductor_db._conductor_db = {}

    return {"ok": True, "name": body.name}


@router.delete("/settings/conductors/{name}")
def delete_conductor(name: str):
    """Elimina un conductor de la BBDD."""
    import pandas as pd

    db_path = os.path.join(os.path.dirname(__file__), "..", "conductors_lv.xlsx")
    xl  = pd.ExcelFile(db_path)
    df  = xl.parse('conductors_parameters')
    xl.close()

    df = df[df['conductor_type'] != name]

    with pd.ExcelWriter(db_path, engine='openpyxl', mode='a',
                        if_sheet_exists='replace') as writer:
        df.to_excel(writer, sheet_name='conductors_parameters', index=False)

    from grid_network import conductor_db
    conductor_db._db_loaded = False
    conductor_db._conductor_db = {}

    return {"ok": True}


# ── Transformer defaults endpoint ─────────────────────────────────────────────

@router.get("/settings/transformers")
def list_transformer_defaults():
    """Lista los parámetros estándar de transformadores por potencia."""
    from grid_network.conductor_db import _TRAFO_DEFAULTS
    return [
        {"sn_kva": kva, **params}
        for kva, params in sorted(_TRAFO_DEFAULTS.items())
    ]
