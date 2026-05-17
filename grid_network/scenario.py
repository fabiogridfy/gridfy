"""
grid_network/scenario.py
Aplica un escenario de carga/generacion a la red activa.

Formato Excel (valores siempre en kW/kVAR — la plataforma convierte a MW):
  Hoja "Cargas":     CUPS | Terminal | P (kW) | Q (kVAR)
  Hoja "Generacion": CUPS | Terminal | P (kW) | Q (kVAR)
"""
import pandas as pd


def _read_excel_safe(excel_path: str) -> tuple[dict, list]:
    """
    Lee todas las hojas del Excel y cierra el handle inmediatamente.
    Importante en Windows donde el archivo queda bloqueado si no se cierra.
    Devuelve (sheets_dict, sheet_names).
    """
    xl = pd.ExcelFile(excel_path)
    sheet_names = xl.sheet_names
    sheets = {}
    for name in sheet_names:
        sheets[name] = xl.parse(name)
    xl.close()
    return sheets, sheet_names


def _normalize_cups(cups: str) -> str:
    """
    Normaliza un CUPS eliminando el sufijo de punto de medida (0F, 1F, etc.)
    para hacer el matching robusto entre diferentes fuentes de datos.
    ES0157000001701998PS0F -> ES0157000001701998PS
    ES0157000001701998PS   -> ES0157000001701998PS
    """
    cups = str(cups).strip()
    # CUPS standard: 20 chars base + optional 2-char suffix (point code)
    # Standard length without suffix: 20 chars
    if len(cups) == 22 and cups[-2:].isalnum() and cups[-2].isdigit():
        return cups[:20]
    return cups


def apply_scenario(net, excel_path: str, voltage: str = "BT") -> dict:
    """
    Sobrescribe P/Q de cargas y generadores de la red segun el escenario.
    Matching por columna 'name' del elemento vs CUPS del Excel.
    Normaliza CUPS eliminando sufijos de punto de medida (0F, 1F...).
    Valores del Excel siempre en kW — se convierten a MW internamente.
    """
    factor = 1e-3  # kW -> MW siempre
    updated_loads = 0
    updated_sgens = 0
    not_found = []

    # Build normalized lookup tables for faster matching
    load_idx_by_cups  = {_normalize_cups(str(r['name'])): idx for idx, r in net.load.iterrows()}
    sgen_idx_by_cups  = {_normalize_cups(str(r['name'])): idx for idx, r in net.sgen.iterrows()}

    sheets, sheet_names = _read_excel_safe(excel_path)
    print(f"[scenario] Hojas encontradas: {sheet_names}")

    # ── Cargas ────────────────────────────────────────────────────────────────
    if "Cargas" in sheets:
        df = sheets["Cargas"]
        for _, row in df.iterrows():
            cups  = str(row.get("CUPS", "")).strip()
            p_raw = row.get("P (kW)", None)
            q_raw = row.get("Q (kVAR)", None)
            if not cups or pd.isna(p_raw):
                continue
            p_mw   = float(p_raw) * factor
            q_mvar = float(q_raw) * factor if pd.notna(q_raw) else 0.0
            norm_cups = _normalize_cups(cups)
            if norm_cups in load_idx_by_cups:
                idx = load_idx_by_cups[norm_cups]
                net.load.at[idx, "p_mw"]   = p_mw
                net.load.at[idx, "q_mvar"] = q_mvar
                updated_loads += 1
            else:
                not_found.append(f"Carga '{cups}' no encontrada en la red")

    # ── Generacion ────────────────────────────────────────────────────────────
    if "Generacion" in sheets:
        df = sheets["Generacion"]
        for _, row in df.iterrows():
            cups  = str(row.get("CUPS", "")).strip()
            p_raw = row.get("P (kW)", None)
            q_raw = row.get("Q (kVAR)", None)
            if not cups or pd.isna(p_raw):
                continue
            p_mw   = float(p_raw) * factor
            q_mvar = float(q_raw) * factor if pd.notna(q_raw) else 0.0
            norm_cups = _normalize_cups(cups)
            if norm_cups in sgen_idx_by_cups:
                idx = sgen_idx_by_cups[norm_cups]
                net.sgen.at[idx, "p_mw"]   = p_mw
                net.sgen.at[idx, "q_mvar"] = q_mvar
                updated_sgens += 1
            else:
                not_found.append(f"Generador '{cups}' no encontrado en la red")

    print(f"[scenario] Cargas actualizadas: {updated_loads} | Sgens actualizados: {updated_sgens}")
    if not_found:
        print(f"[scenario] No encontrados: {not_found}")

    return {
        "updated_loads": updated_loads,
        "updated_sgens": updated_sgens,
        "not_found":     not_found,
    }


def validate_scenario_excel(excel_path: str) -> list:
    """Valida estructura del Excel antes de guardarlo."""
    errors = []
    try:
        sheets, sheet_names = _read_excel_safe(excel_path)
    except Exception as e:
        return [f"No se puede leer el Excel: {e}"]

    if "Cargas" not in sheet_names and "Generacion" not in sheet_names:
        errors.append("El Excel debe tener al menos una hoja 'Cargas' o 'Generacion'")
        return errors

    for sheet in ["Cargas", "Generacion"]:
        if sheet not in sheet_names:
            continue
        df = sheets[sheet]
        for col in ["CUPS", "P (kW)"]:
            if col not in df.columns:
                errors.append(f"Columna '{col}' no encontrada en hoja '{sheet}'")

    return errors
