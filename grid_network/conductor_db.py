"""
grid_network/conductor_db.py

Base de datos de parámetros de conductores y transformadores.
Carga la BBDD desde conductors_lv.xlsx y permite buscar por nombre de conductor.

El matching es case-insensitive y normaliza espacios para maximizar coincidencias.
Para conductores no encontrados devuelve valores de fallback basados en la sección.
"""
import os
import re
import pandas as pd

# ── Ruta al Excel de conductores ──────────────────────────────────────────────
_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "conductors_lv.xlsx")

# ── Cache en memoria ──────────────────────────────────────────────────────────
_conductor_db: dict[str, dict] = {}
_db_loaded = False


def _normalize(name: str) -> str:
    """Normaliza nombre de conductor para matching robusto."""
    s = str(name).strip().upper()
    s = re.sub(r'\s+', ' ', s)           # múltiples espacios → uno
    s = s.replace('X', 'X')              # unificar
    return s


def _load_db():
    global _conductor_db, _db_loaded
    if _db_loaded:
        return

    try:
        xl = pd.ExcelFile(_DB_PATH)
        df = xl.parse('conductors_parameters')
        xl.close()

        for _, row in df.iterrows():
            name = str(row.get('conductor_type', '')).strip()
            if not name or name == 'nan':
                continue
            seccion_raw = row.get('seccion_mm2')
            try:
                seccion = float(seccion_raw) if seccion_raw is not None and str(seccion_raw) != 'nan' else None
            except (TypeError, ValueError):
                seccion = None
            _conductor_db[_normalize(name)] = {
                'name':        name,
                'r_ohm_per_km': float(row.get('resistance_p_ohm_km', 0.641)),
                'x_ohm_per_km': float(row.get('reactance_ohm_km', 0.083)),
                'i_max_ka':     float(row.get('I_max', 100)) / 1000.0,  # A → kA
                'seccion_mm2':  seccion,
            }
        print(f"[conductor_db] Cargados {len(_conductor_db)} conductores")
    except Exception as e:
        print(f"[conductor_db] Warning: no se pudo cargar la BBDD — {e}")

    # Add missing conductors from GIS that aren't in the DB
    # These are standard values from cable manufacturer datasheets
    _FALLBACK_CONDUCTORS = {
        '1X50 AL UNIPOLAR SUBTERRÁNEO': {'r': 0.641,  'x': 0.083, 'i': 0.150},
        '2X10 CU TRENZADO AÉREO':       {'r': 1.830,  'x': 0.083, 'i': 0.065},
        'LC 4X80':                       {'r': 0.443,  'x': 0.083, 'i': 0.220},
        'MANGUERA CU(2X2.5)':            {'r': 8.000,  'x': 0.100, 'i': 0.020},
        'MANGUERA CU(2X25)':             {'r': 0.780,  'x': 0.075, 'i': 0.080},
        'MANGUERA CU 2X(2X4)':           {'r': 6.300,  'x': 0.090, 'i': 0.021},
        'RV 3X240 /150':                 {'r': 0.125,  'x': 0.065, 'i': 0.430},
        'RZ 2X50 AL':                    {'r': 0.641,  'x': 0.083, 'i': 0.145},
    }
    for name_upper, vals in _FALLBACK_CONDUCTORS.items():
        if name_upper not in _conductor_db:
            _conductor_db[name_upper] = {
                'name':         name_upper,
                'r_ohm_per_km': vals['r'],
                'x_ohm_per_km': vals['x'],
                'i_max_ka':     vals['i'],
                'seccion_mm2':  _extract_section_mm2(name_upper),
            }

    _db_loaded = True


def get_conductor_params(conductor_name: str) -> dict:
    """
    Busca los parámetros de un conductor por nombre.
    Devuelve dict con r_ohm_per_km, x_ohm_per_km, i_max_ka.
    Si no se encuentra, estima por sección o usa valores por defecto.
    """
    _load_db()

    norm = _normalize(conductor_name)

    # 1. Exact match
    if norm in _conductor_db:
        return _conductor_db[norm]

    # 2. Partial match — find entries where one contains the other
    for key, val in _conductor_db.items():
        if norm in key or key in norm:
            return val

    # 3. Estimate from section size in the name
    section = _extract_section_mm2(conductor_name)
    if section:
        # Copper resistivity: 17.241 Ω·mm²/km
        # Aluminium: 28.264 Ω·mm²/km
        is_al = any(x in norm for x in ['AL', 'ALUMIN'])
        rho = 28.264 if is_al else 17.241
        r_km = rho / section
        i_max = _estimate_imax(section, is_al)
        print(f"[conductor_db] '{conductor_name}' no encontrado — estimado por sección {section}mm²")
        return {'name': conductor_name, 'r_ohm_per_km': r_km, 'x_ohm_per_km': 0.083,
                'i_max_ka': i_max, 'seccion_mm2': section}

    # 4. Default fallback
    print(f"[conductor_db] '{conductor_name}' no encontrado — usando valores por defecto")
    return {'name': conductor_name, 'r_ohm_per_km': 0.641, 'x_ohm_per_km': 0.083,
            'i_max_ka': 0.100, 'seccion_mm2': None}


def _extract_section_mm2(name: str) -> float | None:
    """Extrae la sección en mm² del nombre del conductor."""
    # Match patterns like 4x16, 2X25, 3x150, 1x50
    matches = re.findall(r'\d+[xX](\d+(?:\.\d+)?)', name)
    if matches:
        return float(matches[0])
    # Or just a number followed by context
    matches = re.findall(r'(\d+(?:\.\d+)?)\s*mm', name, re.IGNORECASE)
    if matches:
        return float(matches[0])
    return None


def _estimate_imax(section_mm2: float, aluminium: bool) -> float:
    """Estima I_max en kA a partir de la sección."""
    # Approximate values for underground cables at 25°C
    cu_table = {2.5: 22, 4: 30, 6: 38, 10: 52, 16: 70, 25: 90,
                35: 110, 50: 135, 70: 165, 95: 200, 120: 230, 150: 260, 240: 350}
    al_table = {16: 55, 25: 70, 35: 85, 50: 105, 70: 130, 95: 160,
                120: 185, 150: 210, 240: 275}
    table = al_table if aluminium else cu_table
    # Find closest section
    keys = sorted(table.keys())
    closest = min(keys, key=lambda k: abs(k - section_mm2))
    return table[closest] / 1000.0


# ── Transformer defaults by power rating ─────────────────────────────────────
_TRAFO_DEFAULTS = {
    25:   {'vk': 4.0, 'vkr': 1.20, 'pfe': 0.065, 'i0': 2.30},
    50:   {'vk': 4.0, 'vkr': 1.60, 'pfe': 0.110, 'i0': 2.20},
    63:   {'vk': 4.0, 'vkr': 1.50, 'pfe': 0.130, 'i0': 2.10},
    100:  {'vk': 4.0, 'vkr': 1.40, 'pfe': 0.185, 'i0': 1.90},
    160:  {'vk': 4.0, 'vkr': 1.20, 'pfe': 0.270, 'i0': 1.70},
    200:  {'vk': 4.0, 'vkr': 1.20, 'pfe': 0.330, 'i0': 1.60},
    250:  {'vk': 4.0, 'vkr': 1.10, 'pfe': 0.400, 'i0': 1.50},
    315:  {'vk': 4.0, 'vkr': 1.10, 'pfe': 0.500, 'i0': 1.40},
    400:  {'vk': 4.0, 'vkr': 1.00, 'pfe': 0.600, 'i0': 1.30},
    500:  {'vk': 4.0, 'vkr': 1.00, 'pfe': 0.750, 'i0': 1.20},
    630:  {'vk': 4.0, 'vkr': 1.00, 'pfe': 0.900, 'i0': 1.10},
    800:  {'vk': 6.0, 'vkr': 1.00, 'pfe': 1.100, 'i0': 1.00},
    1000: {'vk': 6.0, 'vkr': 1.00, 'pfe': 1.300, 'i0': 0.90},
    1250: {'vk': 6.0, 'vkr': 1.00, 'pfe': 1.600, 'i0': 0.85},
    1600: {'vk': 6.0, 'vkr': 1.00, 'pfe': 1.900, 'i0': 0.80},
    2000: {'vk': 6.0, 'vkr': 1.00, 'pfe': 2.200, 'i0': 0.75},
}


def get_trafo_params(power_kva: float) -> dict:
    """
    Devuelve parámetros estándar de transformador BT/MT para una potencia dada.
    Basado en norma IEC 60076 y catálogos de fabricantes (ABB, Schneider, Ormazabal).
    """
    keys = sorted(_TRAFO_DEFAULTS.keys())
    closest = min(keys, key=lambda k: abs(k - power_kva))
    params = _TRAFO_DEFAULTS[closest].copy()
    params['sn_kva'] = power_kva
    return params


def list_conductors() -> list[dict]:
    """Lista todos los conductores disponibles en la BBDD."""
    _load_db()
    return sorted(_conductor_db.values(), key=lambda x: x['name'])


# ── Acometida recommendation ──────────────────────────────────────────────────

def recommend_acometida(
    p_kw: float,
    voltage_kv: float = 0.4,
    phases: int = 3,
    dist_m: float = None,
) -> dict:
    """
    Recomienda el conductor mínimo para una acometida dado:
    - p_kw: potencia a conectar en kW
    - voltage_kv: tensión de la red (0.4 kV BT)
    - phases: 3 = trifásico, 1 = monofásico
    - dist_m: distancia en metros (para info, no afecta sección)

    Criterio: sección mínima cuya I_max >= I_demanda (fp=1).
    Devuelve el conductor recomendado y los detalles del cálculo.
    """
    _load_db()

    # Current demand (fp=1)
    v = voltage_kv * 1000  # V
    if phases == 3:
        i_demand_a = (p_kw * 1000) / (v * 1.732)
    else:
        i_demand_a = (p_kw * 1000) / v

    # Filter conductors that are typical acometida cables
    # Prefer underground cables (MANGUERA CU, RV, XZ1) over aerial
    ACOMETIDA_KEYWORDS = ['MANGUERA', 'RV ', 'RV0', 'XZ1', 'RZ', 'VV', 'XLPE']

    candidates = []
    for key, cond in _conductor_db.items():
        name_up = cond['name'].upper()
        is_acometida = any(kw in name_up for kw in ACOMETIDA_KEYWORDS)
        if not is_acometida:
            continue
        i_max_a = cond['i_max_ka'] * 1000
        if i_max_a >= i_demand_a:
            # Extract section for sorting
            import re
            sec_match = re.search(r'[xX](\d+(?:\.\d+)?)', cond['name'])
            section = float(sec_match.group(1)) if sec_match else 999
            candidates.append({
                'name':      cond['name'],
                'i_max_a':   i_max_a,
                'r_ohm_per_km': cond['r_ohm_per_km'],
                'section':   section,
            })

    # Sort by section ascending — pick smallest that works
    candidates.sort(key=lambda x: (x['section'], x['i_max_a']))

    # Voltage drop check if distance given
    selected = candidates[0] if candidates else None
    vdrop_pct = None
    if selected and dist_m:
        # ΔV% = (P_kW * R_ohm_per_km * dist_km) / (V_kV² * 10)
        dist_km = dist_m / 1000
        vdrop_pct = round(
            (p_kw * selected['r_ohm_per_km'] * dist_km) / (voltage_kv**2 * 10), 2
        )

    return {
        'i_demand_a':        round(i_demand_a, 2),
        'conductor':         selected['name'] if selected else 'No encontrado',
        'i_max_a':           selected['i_max_a'] if selected else None,
        'section_mm2':       selected['section'] if selected else None,
        'vdrop_percent':     vdrop_pct,
        'dist_m':            dist_m,
        'phases':            phases,
        'p_kw':              p_kw,
        'candidates_count':  len(candidates),
        'note': (
            f"I demanda: {i_demand_a:.1f} A | "
            f"Cable: {selected['name'] if selected else '—'} "
            f"(I_max={selected['i_max_a']:.0f} A)" if selected else
            f"No hay cable en BBDD con I_max >= {i_demand_a:.1f} A"
        ),
    }
