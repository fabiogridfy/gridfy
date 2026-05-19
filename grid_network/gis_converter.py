"""
grid_network/gis_converter.py

Convierte el formato GIS (Tramo_baja, Transformador, Punto_D-C) al formato
Excel de Gridfy (Terminal, ExternalGrid, Lineas, Transformadores, Loads_Data).

Este es un camino alternativo de importación — el formato Excel de Gridfy
sigue siendo el formato canónico. Otros conversores GIS o fuentes externas
deben pasar por aquí o crear un conversor equivalente.

Columnas GIS usadas:
  Tramo_baja:   Nudo inicio, Nudo fin, Coordenada X/Y inicio/fin,
                Resistencia (Ω), Reactancia (Ω), Longitud real (m),
                Intensidad máxima (A), Tensión explotación (kV)
  Transformador: Nudo AT, Nudo BT, Potencia (kVA), Tensión primario/secundario,
                 Tensión de cortocircuito (%), Pérdidas vacío (kW),
                 Impedancia de cortocircuito (%)
  Punto_D-C:    CUPS, Nudo, Coordenada X/Y, Pot. contratada (kW), Fase
"""
import io
import pandas as pd
from grid_network.conductor_db import get_conductor_params, get_trafo_params
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


UTM_ZONE    = 30
UTM_LETTER  = "N"


def _float(val) -> float:
    """Parse float handling Spanish comma decimal separator."""
    if pd.isna(val):
        return 0.0
    return float(str(val).replace(",", ".").strip().lstrip("'"))


def _read_csv(content: bytes) -> pd.DataFrame:
    """Read CSV with automatic separator detection, handling BOM and encoding."""
    for enc in ["utf-8-sig", "latin-1", "cp1252"]:
        try:
            df = pd.read_csv(io.BytesIO(content), sep=None, engine="python",
                             encoding=enc, dtype=str)
            # Strip BOM from first column name if present
            df.columns = [c.lstrip("\ufeff") for c in df.columns]
            return df
        except Exception:
            continue
    raise ValueError("No se pudo leer el CSV — prueba UTF-8 o Latin-1")


def convert_gis_to_gridfy_excel(
    tramos_bytes: bytes,
    trafo_bytes:  bytes,
    loads_bytes:  bytes,
    utm_zone:     int = None,  # None = auto-detect
) -> bytes:
    """
    Convierte los 3 CSVs GIS a un Excel en formato Gridfy.
    Devuelve el contenido del Excel como bytes.
    """
    df_tramos = _read_csv(tramos_bytes)
    df_trafo  = _read_csv(trafo_bytes)
    df_loads  = _read_csv(loads_bytes)

    # ── 1. Extraer todos los buses únicos de los tramos ───────────────────────
    # Cada nudo tiene coordenadas asociadas al inicio o fin de algún tramo
    nudo_coords = {}  # nudo_id -> (X, Y)

    # Auto-detect UTM zone from first valid coordinate
    if utm_zone is None:
        for _, row in df_tramos.iterrows():
            xi_test = row.get("Coordenada X inicio", None)
            yi_test = row.get("Coordenada Y inicio", None)
            if xi_test and yi_test:
                try:
                    x_test = _float(xi_test)
                    y_test = _float(yi_test)
                    if x_test > 0 and y_test > 0:
                        utm_zone = _detect_utm_zone(x_test, y_test)
                        print(f"[gis_converter] Huso UTM detectado automáticamente: {utm_zone}N")
                        break
                except Exception:
                    continue
        if utm_zone is None:
            utm_zone = 30
            print("[gis_converter] Huso UTM: usando zona 30N por defecto")

    for _, row in df_tramos.iterrows():
        ni = str(row.get("Nudo inicio", "")).strip().strip("'")
        nf = str(row.get("Nudo fin",   "")).strip().strip("'")
        xi = row.get("Coordenada X inicio", None)
        yi = row.get("Coordenada Y inicio", None)
        xf = row.get("Coordenada X fin",    None)
        yf = row.get("Coordenada Y fin",    None)

        if ni and ni not in nudo_coords and xi and yi:
            nudo_coords[ni] = (_float(xi), _float(yi))
        if nf and nf not in nudo_coords and xf and yf:
            nudo_coords[nf] = (_float(xf), _float(yf))

    # Add trafo nudos — use same coordinate system as tramos (zona 30)
    for _, row in df_trafo.iterrows():
        nbt = str(row.get("Nudo BT", "")).strip().strip("'")
        nat = str(row.get("Nudo AT", "")).strip().strip("'")

        # BT node: use trafo coordinates (same zone as tramos)
        # Try columns in order of preference (zona 30 coords)
        cx = row.get("Coordenada X", None) or row.get("Coordenada X Z30", None)
        cy = row.get("Coordenada Y", None) or row.get("Coordenada Y Z30", None)

        if nbt and nbt not in nudo_coords:
            if cx and cy:
                nudo_coords[nbt] = (_float(cx), _float(cy))
            else:
                nudo_coords[nbt] = (0.0, 0.0)

        # AT node: use same coordinates as BT node (they share physical location)
        if nat and nat not in nudo_coords:
            if nbt in nudo_coords:
                nudo_coords[nat] = nudo_coords[nbt]
            elif cx and cy:
                nudo_coords[nat] = (_float(cx), _float(cy))
            else:
                nudo_coords[nat] = (0.0, 0.0)

    # Determine voltage per nudo from tramos
    nudo_tension = {}
    for _, row in df_tramos.iterrows():
        t = _float(row.get("Tensión explotación (kV)", 0.4))
        if t == 0:
            t = 0.4
        ni = str(row.get("Nudo inicio", "")).strip().strip("'")
        nf = str(row.get("Nudo fin",   "")).strip().strip("'")
        if ni: nudo_tension[ni] = t
        if nf: nudo_tension[nf] = t

    # HV nudo from trafo gets MT voltage
    for _, row in df_trafo.iterrows():
        nat = str(row.get("Nudo AT", "")).strip().strip("'")
        tHV = _float(row.get("Tensión explotación primario (kV)", 20))
        if tHV == 0: tHV = 20.0
        if nat: nudo_tension[nat] = tHV

    # ── 2. Terminal ───────────────────────────────────────────────────────────
    rows_terminal = []
    for nudo, (x, y) in nudo_coords.items():
        rows_terminal.append({
            "Nombre":  nudo,
            "Tensión": nudo_tension.get(nudo, 0.4),
            "X":       x,
            "Y":       y,
        })
    df_terminal = pd.DataFrame(rows_terminal)

    # ── 3. ExternalGrid — Nudo AT del transformador ───────────────────────────
    rows_ext = []
    for _, row in df_trafo.iterrows():
        nat = str(row.get("Nudo AT", "")).strip().strip("'")
        nombre = str(row.get("Denominación", "EXT_GRID")).strip().strip("'")
        rows_ext.append({"Nombre": nombre, "Terminal": nat})
    df_ext = pd.DataFrame(rows_ext) if rows_ext else pd.DataFrame(columns=["Nombre","Terminal"])

    # ── 4. Lineas ─────────────────────────────────────────────────────────────
    rows_lineas = []
    for idx, row in df_tramos.iterrows():
        ni  = str(row.get("Nudo inicio", "")).strip().strip("'")
        nf  = str(row.get("Nudo fin",   "")).strip().strip("'")
        if not ni or not nf or ni == nf:
            continue

        long_m  = _float(row.get("Longitud real (m)", 0)) or _float(row.get("Longitud calculada (m)", 0))
        long_km = long_m / 1000.0 if long_m > 0 else 0.001

        # Use conductor DB to get certified electrical parameters
        conductor_name = str(row.get("Conductor", "")).strip().strip("'")
        cond = get_conductor_params(conductor_name) if conductor_name else {}
        r_km    = cond.get('r_ohm_per_km', 0.641)
        x_km    = cond.get('x_ohm_per_km', 0.083)
        i_max_ka_db = cond.get('i_max_ka', None)

        i_max_a = _float(row.get("Intensidad máxima (A)", 0))
        i_max_ka = i_max_ka_db if i_max_ka_db else (i_max_a / 1000.0 if i_max_a > 0 else 0.100)

        nombre = str(row.get("Identificador", f"LIN_{idx}")).strip().strip("'")
        padre  = str(row.get("Padre", "")).strip().strip("'")

        rows_lineas.append({
            "Nombre":        nombre,
            "Terminal_i":    ni,
            "Terminal_j":    nf,
            "Longitud (km)": round(long_km, 6),
            "R/km":          round(r_km, 6),
            "X/km":          round(x_km, 6),
            "I max (kA)":    round(i_max_ka, 6),
            "Conductor":     conductor_name,
            "_Padre":        padre,  # auxiliar, se elimina antes de escribir el Excel
        })
    df_lineas = pd.DataFrame(rows_lineas)

    # ── 4b. Desdoblar terminales según feeder (Padre) ─────────────────────────
    # Regla: si en un terminal confluyen líneas con distinto Padre, ese terminal
    # se divide en N copias con sufijo _A, _B, _C... Excepción: terminales del
    # transformador (Nudo BT / Nudo AT), que mantienen la conexión original.
    from collections import defaultdict

    protected_terms = set()
    for _, trow in df_trafo.iterrows():
        nat = str(trow.get("Nudo AT", "")).strip().strip("'")
        nbt = str(trow.get("Nudo BT", "")).strip().strip("'")
        if nat: protected_terms.add(nat)
        if nbt: protected_terms.add(nbt)

    # terminal -> [(idx, side, padre), ...]
    term_to_lines = defaultdict(list)
    for idx, lrow in df_lineas.iterrows():
        term_to_lines[lrow["Terminal_i"]].append((idx, "i", lrow["_Padre"]))
        term_to_lines[lrow["Terminal_j"]].append((idx, "j", lrow["_Padre"]))

    # Decidir qué terminales se dividen y mapear cada Padre a un sufijo
    split_map = {}            # terminal -> {padre: suffix}
    loads_relocations = {}    # terminal_original -> terminal_A (para Loads_Data)
    new_terminal_rows = []
    terms_to_drop = []

    for term, entries in term_to_lines.items():
        if term in protected_terms:
            continue
        padres_no_vacios = sorted({p for (_, _, p) in entries if p})
        if len(padres_no_vacios) <= 1:
            continue  # mismo feeder (o todos sin padre) → no se divide

        # Asignar sufijos _A, _B, _C... (orden alfabético del Padre, determinista)
        suffixes = {}
        for i, p in enumerate(padres_no_vacios):
            if i < 26:
                suffixes[p] = chr(ord("A") + i)
            else:
                # Caso muy improbable, sigue con _AA, _AB, etc.
                suffixes[p] = "A" + chr(ord("A") + (i - 26))
        split_map[term] = suffixes

        # Renombrar columnas Terminal_i / Terminal_j en df_lineas
        # Si una línea no tiene Padre (vacío) se asigna a la copia _A por defecto
        first_suffix = sorted(suffixes.values())[0]
        for idx, side, padre in entries:
            suffix = suffixes.get(padre, first_suffix)
            col    = "Terminal_i" if side == "i" else "Terminal_j"
            df_lineas.at[idx, col] = f"{term}_{suffix}"

        # Crear las nuevas filas en df_terminal
        orig = df_terminal[df_terminal["Nombre"] == term]
        if not orig.empty:
            base = orig.iloc[0]
            for padre, suffix in suffixes.items():
                new_row = base.copy()
                new_row["Nombre"] = f"{term}_{suffix}"
                new_terminal_rows.append(new_row)
            terms_to_drop.append(term)

        # Cargas en este terminal → relocalizar a la copia _A
        loads_relocations[term] = f"{term}_{first_suffix}"

    if terms_to_drop:
        df_terminal = df_terminal[~df_terminal["Nombre"].isin(terms_to_drop)].reset_index(drop=True)
    if new_terminal_rows:
        df_terminal = pd.concat([df_terminal, pd.DataFrame(new_terminal_rows)],
                                ignore_index=True)

    if split_map:
        print(f"  [gis_converter] Terminales desdoblados por feeder: {len(split_map)}")

    # Limpia columna auxiliar
    df_lineas = df_lineas.drop(columns=["_Padre"], errors="ignore")

    # P3: Rename parallel lines (same Terminal_i + Terminal_j) with _1, _2 suffix
    pair_count = {}
    for idx, row in df_lineas.iterrows():
        pair = (row["Terminal_i"], row["Terminal_j"])
        pair_inv = (row["Terminal_j"], row["Terminal_i"])
        key = pair if pair <= pair_inv else pair_inv
        pair_count[key] = pair_count.get(key, 0) + 1
    pair_seen = {}
    for idx, row in df_lineas.iterrows():
        pair = (row["Terminal_i"], row["Terminal_j"])
        pair_inv = (row["Terminal_j"], row["Terminal_i"])
        key = pair if pair <= pair_inv else pair_inv
        if pair_count[key] > 1:
            pair_seen[key] = pair_seen.get(key, 0) + 1
            df_lineas.at[idx, "Nombre"] = (
                f"{row['Nombre']}_{row['Terminal_i'][-6:]}_{pair_seen[key]}"
            )

    # ── 5. Transformadores ────────────────────────────────────────────────────
    rows_trafos = []
    for _, row in df_trafo.iterrows():
        nat  = str(row.get("Nudo AT", "")).strip().strip("'")
        nbt  = str(row.get("Nudo BT", "")).strip().strip("'")
        code = str(row.get("Identificador", "TRF_001")).strip().strip("'")
        pot  = _float(row.get("Potencia (kVA)", 250))
        tHV  = _float(row.get("Tensión explotación primario (kV)", 20)) or 20.0
        tLV_raw = _float(row.get("Tensión explotación secundario (kV)", 0.4)) or 0.4
        # Normalise LV voltage: 0.42 kV (placa real) → 0.4 kV (base de la red BT)
        # El GIS exporta la tensión de placa (420V) pero la red BT se modela a 400V
        tLV = 0.4 if abs(tLV_raw - 0.42) < 0.02 else tLV_raw
        # Tensión cc: prefer "Impedancia de cortocircuito (%)" then "Tensión de cortocircuito (%)"
        # Get standard trafo parameters from DB (by power rating)
        trafo_db = get_trafo_params(pot)

        # Use GIS value if available and sensible, otherwise use DB standard
        vk_gis = _float(row.get("Impedancia de cortocircuito (%)", 0) or
                        row.get("Tensión de cortocircuito (%)", 0))
        vk  = vk_gis if vk_gis > 0 else trafo_db['vk']

        pfe_gis = _float(row.get("Pérdidas vacío (kW)", 0))
        pfe = pfe_gis if pfe_gis > 0 else trafo_db['pfe']

        pcc = _float(row.get("Pérdidas cortocircuito (kW)", 0))
        vkr = round((pcc / pot) * 100, 4) if pcc > 0 and pot > 0 else trafo_db['vkr']

        i0   = _float(row.get("Intensidad de vacío (A)", 0))
        i_nom_bt = pot / (tLV * 1.732) if tLV > 0 else 1
        i0_pct = round((i0 / i_nom_bt) * 100, 4) if i0 > 0 else trafo_db['i0']
        label    = str(row.get("Denominación", code)).strip().strip("'")

        rows_trafos.append({
            "code":                     code,
            "label":                    label,
            "substation":               label,
            "primary_node_code":        nat,
            "secondary_node_code":      nbt,
            "nominal_apparent_power_kVA": pot,
            "Tension HV":               tHV,
            "Tension LV":               tLV,
            "vk_percent":               vk,
            "vkr_percent":              vkr,
            "pfe_kw":                   pfe,
            "i0_percent":               i0_pct,
        })
    df_trafos = pd.DataFrame(rows_trafos)

    # ── 6. Loads_Data ─────────────────────────────────────────────────────────
    rows_loads = []
    for _, row in df_loads.iterrows():
        cups = str(row.get("CUPS", "")).strip()
        nudo = str(row.get("Nudo", "")).strip().strip("'")
        pot  = _float(row.get("Pot. contratada (kW)", 0))
        fase = str(row.get("Fase", "RST")).strip()
        if not cups or not nudo:
            continue
        rows_loads.append({
            "CUPS":                  cups,
            "Terminal":              nudo,
            "Fase":                  fase if fase not in ("nan", "") else "RST",
            "Pot. contratada (kW)":  pot,
            "P (MW)":                "",
            "Q (MVAR)":              "",
        })
    df_loads_out = pd.DataFrame(rows_loads)

    # Reasignar cargas conectadas a terminales que se han desdoblado → copia _A
    if not df_loads_out.empty and loads_relocations:
        df_loads_out["Terminal"] = df_loads_out["Terminal"].replace(loads_relocations)

    # ── 6b. Remove buses not connected to any line ───────────────────────────
    # Buses from Punto_D-C that don't appear in any tramo are isolated
    connected_nudos = set()
    for _, row in df_lineas.iterrows():
        connected_nudos.add(row["Terminal_i"])
        connected_nudos.add(row["Terminal_j"])
    # Also keep trafo nudos
    for _, row in df_trafos.iterrows():
        connected_nudos.add(row["primary_node_code"])
        connected_nudos.add(row["secondary_node_code"])

    # Filter terminal to only connected nudos
    df_terminal = df_terminal[df_terminal["Nombre"].isin(connected_nudos)].reset_index(drop=True)

    # Filter loads to only buses that exist in terminal
    if not df_loads_out.empty:
        df_loads_out = df_loads_out[df_loads_out["Terminal"].isin(connected_nudos)].reset_index(drop=True)

    # ── 7. Write Excel ────────────────────────────────────────────────────────
    BRAND = "037A68"; WHITE = "FFFFFF"; LIGHT = "E8F5F2"; MUTED = "F5F6F8"

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    def write_sheet(name: str, df: pd.DataFrame):
        ws = wb.create_sheet(name)
        ws.sheet_view.showGridLines = False
        for ci, col in enumerate(df.columns, 1):
            c = ws.cell(row=1, column=ci, value=col)
            c.font = Font(name="Arial", bold=True, color=WHITE, size=10)
            c.fill = PatternFill("solid", fgColor=BRAND)
            c.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(ci)].width = max(18, len(str(col)) + 4)
            ws.row_dimensions[1].height = 20
        for ri, (_, row) in enumerate(df.iterrows(), 2):
            bg = LIGHT if ri % 2 == 0 else MUTED
            for ci, val in enumerate(row, 1):
                c = ws.cell(row=ri, column=ci, value=None if pd.isna(val) else val)
                c.font = Font(name="Arial", size=9)
                c.fill = PatternFill("solid", fgColor=bg)
                c.alignment = Alignment(vertical="center")
            ws.row_dimensions[ri].height = 15

    write_sheet("Terminal",        df_terminal)
    write_sheet("ExternalGrid",    df_ext)
    write_sheet("Lineas",          df_lineas)
    write_sheet("Transformadores", df_trafos)
    if not df_loads_out.empty:
        write_sheet("Loads_Data",  df_loads_out)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def validate_gis_files(
    tramos_bytes: bytes,
    trafo_bytes:  bytes,
    loads_bytes:  bytes | None = None,
) -> list[str]:
    """
    Valida que los CSVs GIS tienen las columnas mínimas necesarias.
    Devuelve lista de errores (vacía = OK).
    """
    errors = []

    required_tramos = ["Nudo inicio", "Nudo fin", "Coordenada X inicio",
                       "Coordenada Y inicio", "Coordenada X fin", "Coordenada Y fin",
                       "Resistencia (Ω)", "Reactancia (Ω)"]
    required_trafo  = ["Nudo AT", "Nudo BT", "Potencia (kVA)"]
    required_loads  = ["CUPS", "Nudo"]

    try:
        df = _read_csv(tramos_bytes)
        for col in required_tramos:
            if col not in df.columns:
                errors.append(f"Tramo_baja: columna '{col}' no encontrada")
    except Exception as e:
        errors.append(f"No se pudo leer Tramo_baja: {e}")

    try:
        df = _read_csv(trafo_bytes)
        for col in required_trafo:
            if col not in df.columns:
                errors.append(f"Transformador: columna '{col}' no encontrada")
    except Exception as e:
        errors.append(f"No se pudo leer Transformador: {e}")

    if loads_bytes:
        try:
            df = _read_csv(loads_bytes)
            for col in required_loads:
                if col not in df.columns:
                    errors.append(f"Punto_D-C: columna '{col}' no encontrada")
        except Exception as e:
            errors.append(f"No se pudo leer Punto_D-C: {e}")

    return errors
