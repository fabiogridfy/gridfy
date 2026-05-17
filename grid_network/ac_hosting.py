"""
ac_hosting.py — A&C para redes BT y MT.

Algoritmo:
  1. Expansión exponencial (1,2,4,8...) hasta encontrar el rango [lo, hi] donde falla
  2. Búsqueda binaria en [lo, hi] con precisión 1 kW
  3. PF con la potencia solicitada -> estado real de la red
  4. Admisible si hosting_max >= p_requested
"""
import json
import pandapower as pp


# Límite máximo de potencia a estudiar por nivel de tensión (kW)
P_MAX_KW = {"BT": 100_000, "MT": None}  # MT: TBD

def _parse_loading(msg: str) -> float:
    """Extract loading percentage from violation message like 'Sobrecarga de línea: X (99.5%)'"""
    import re
    m = re.search(r'\((\d+\.?\d*)%\)', msg)
    return float(m.group(1)) if m else 0.0


BT_LIMITS = {"v_min": 0.93, "v_max": 1.07, "line_max": 100.0, "trafo_max": 100.0}
MT_LIMITS = {"v_min": 0.95, "v_max": 1.05, "line_max": 100.0, "trafo_max": 100.0}

def get_limits(vn_kv):
    return BT_LIMITS if vn_kv < 1.0 else MT_LIMITS

def get_voltage_level(vn_kv):
    return "BT" if vn_kv < 1.0 else "MT"


def get_feeder_scope(net, index_bus):
    """
    Devuelve (feeder_lines_index, all_buses_in_feeder).
    - Si la red tiene columna Feeder_line: usa solo las líneas/buses del feeder del bus.
    - Si no: usa toda la red.
    - Siempre incluye TODOS los buses conectados a esas líneas (from_bus y to_bus),
      no solo los buses destino, para no perderse buses intermedios.
    """
    try:
        feeder = net.line.loc[net.line["to_bus"] == index_bus]["Feeder_line"].values[0]
        feeder_lines_index = net.line.loc[net.line["Feeder_line"] == feeder].index
        feeder_lines = net.line.loc[feeder_lines_index]
        # Todos los buses involucrados: origen y destino
        buses_in_feeder = set(feeder_lines["from_bus"]) | set(feeder_lines["to_bus"])
        buses_in_feeder = list(buses_in_feeder)
    except Exception:
        # Sin Feeder_line: toda la red
        feeder_lines_index = net.line.index
        buses_in_feeder = net.bus.index.tolist()
    return feeder_lines_index, buses_in_feeder


def _evaluate(net, index_bus, p_mw, feeder_lines_index, buses_to, limits, is_generation):
    """Añade elemento temporal, corre PF, evalúa, elimina. Devuelve (ok, violated)."""
    if is_generation:
        tmp_idx = pp.create_sgen(net, bus=index_bus, p_mw=p_mw, q_mvar=0.0, name="_ac_tmp")
    else:
        tmp_idx = pp.create_load(net, bus=index_bus, p_mw=p_mw, q_mvar=0.0, name="_ac_tmp")

    try:
        pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, max_iteration=50, init="auto")
        converged = True
    except Exception:
        converged = False

    if not converged:
        if is_generation: net.sgen.drop(index=tmp_idx, inplace=True)
        else: net.load.drop(index=tmp_idx, inplace=True)
        return False, ["Power flow no convergió"]

    violated = []

    for line_idx in feeder_lines_index:
        if line_idx in net.res_line.index:
            loading = net.res_line.at[line_idx, "loading_percent"]
            if loading >= limits["line_max"]:
                violated.append(f"Sobrecarga de línea: {net.line.at[line_idx,'name']} ({loading:.1f}%)")

    for bus_idx in buses_to:
        if bus_idx in net.res_bus.index:
            vm = net.res_bus.at[bus_idx, "vm_pu"]
            if vm < limits["v_min"]:
                violated.append(f"Subtensión en: {net.bus.at[bus_idx,'name']} ({vm:.4f} p.u.)")
            if vm > limits["v_max"]:
                violated.append(f"Sobretensión en: {net.bus.at[bus_idx,'name']} ({vm:.4f} p.u.)")

    for trafo_idx in net.trafo.index:
        if trafo_idx in net.res_trafo.index:
            loading = net.res_trafo.at[trafo_idx, "loading_percent"]
            if loading >= limits["trafo_max"]:
                violated.append(f"Sobrecarga de trafo: {net.trafo.at[trafo_idx,'name']} ({loading:.1f}%)")

    if is_generation: net.sgen.drop(index=tmp_idx, inplace=True)
    else: net.load.drop(index=tmp_idx, inplace=True)

    return len(violated) == 0, violated


def _search_generator(net, index_bus, feeder_lines_index, buses_to, limits, is_generation, p_max_kw=100_000):
    """
    Fase 1: expansión exponencial para acotar el rango.
    Fase 2: búsqueda binaria con precisión 1 kW.
    Yields ("progress", msg) | ("result", hosting_kw, violated, saturated_base)
    """
    # ── Estado base ───────────────────────────────────────────────────────────
    yield ("progress", "Comprobando estado base (0 kW adicionales)...")
    ok0, v0 = _evaluate(net, index_bus, 0.0, feeder_lines_index, buses_to, limits, is_generation)
    # Only block if base violations are HARD (outside absolute limits)
    # Filter out soft warnings — only real violations block the study
    hard_v0 = [v for v in v0 if any(x in v for x in
               ["Sobrecarga de línea", "Sobrecarga de trafo", "Subtensión", "Sobretensión"])]
    if not ok0 and hard_v0:
        yield ("progress", "⚠ La red ya viola límites sin nueva conexión")
        yield ("progress", "Violaciones: " + " | ".join(hard_v0[:3]))
        yield ("result", 0, v0, True)
        return
    elif not ok0:
        # Soft warnings only — continue study but warn
        yield ("progress", "⚠ Aviso: la red tiene condiciones fuera del rango óptimo (no bloquea el estudio)")

    # ── Fase 1: expansión exponencial ─────────────────────────────────────────
    yield ("progress", "Fase 1: expansión exponencial para acotar rango...")
    step = 1       # kW
    lo   = 0
    hi   = None
    last_violated = []

    while True:
        # Cap at p_max_kw
        test_kw = min(step, p_max_kw)
        ok, violated = _evaluate(net, index_bus, test_kw * 1e-3, feeder_lines_index, buses_to, limits, is_generation)
        status = "OK" if ok else "VIOLA"
        yield ("progress", f"  Expansion: {test_kw} kW -> {status}")
        if ok and test_kw < p_max_kw:
            lo = test_kw
            step *= 2
        elif ok and test_kw == p_max_kw:
            yield ("progress", f"Admisible hasta el limite maximo del estudio: {p_max_kw} kW")
            yield ("result", p_max_kw, [], False)
            return
        else:
            hi = test_kw
            last_violated = violated
            break

    yield ("progress", f"Rango acotado: [{lo} - {hi}] kW. Iniciando búsqueda binaria...")

    # ── Fase 2: búsqueda binaria ──────────────────────────────────────────────
    iterations = 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        iterations += 1
        ok, violated = _evaluate(net, index_bus, mid * 1e-3, feeder_lines_index, buses_to, limits, is_generation)
        status = 'OK' if ok else 'VIOLA'
        yield ("progress", f"  Binaria it.{iterations}: {mid} kW -> {status} | [{lo}-{hi}]")
        if ok:
            lo = mid
        else:
            hi = mid
            last_violated = violated

    yield ("progress", f"OK Hosting máximo = {lo} kW (expansión + {iterations} it. binaria)")
    yield ("result", lo, last_violated, False)


def _run_pf_with_connection(net, index_bus, p_mw, is_generation):
    """Corre PF con la potencia solicitada y devuelve resultados. No deja elemento en la red."""
    if is_generation:
        tmp_idx = pp.create_sgen(net, bus=index_bus, p_mw=p_mw, q_mvar=0.0, name="_ac_req")
    else:
        tmp_idx = pp.create_load(net, bus=index_bus, p_mw=p_mw, q_mvar=0.0, name="_ac_req")

    try:
        pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, max_iteration=50, init="auto")
        converged = True
    except Exception:
        converged = False

    import math
    def sf(v):
        if v is None: return None
        try:
            f = float(v)
            return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
        except: return None

    if converged:
        pf = {
            "converged": True,
            "buses":  [{"id": int(i), "vm_pu": sf(r["vm_pu"]), "va_degree": sf(r["va_degree"]),
                        "p_mw": sf(r["p_mw"]), "q_mvar": sf(r["q_mvar"])}
                       for i, r in net.res_bus.iterrows()],
            "lines":  [{"id": int(i), "loading_percent": sf(r["loading_percent"]),
                        "i_ka": sf(r["i_ka"]), "p_from_mw": sf(r["p_from_mw"])}
                       for i, r in net.res_line.iterrows()],
            "trafos": [{"id": int(i), "loading_percent": sf(r["loading_percent"]),
                        "p_hv_mw": sf(r["p_hv_mw"]), "q_hv_mvar": sf(r["q_hv_mvar"])}
                       for i, r in net.res_trafo.iterrows()],
        }
    else:
        pf = {"converged": False, "buses": [], "lines": [], "trafos": []}

    if is_generation: net.sgen.drop(index=tmp_idx, inplace=True)
    else: net.load.drop(index=tmp_idx, inplace=True)

    return pf




def _extract_critical_elements(net, feeder_lines_index, buses_to, limits, top_n=5):
    """
    Extrae los elementos más cercanos a sus límites del PF ya corrido.
    Devuelve dict con buses y líneas ordenados por proximidad al límite.
    Llama solo después de pp.runpp().
    """
    bus_results = []
    for bus_idx in buses_to:
        if bus_idx not in net.res_bus.index:
            continue
        vm = net.res_bus.at[bus_idx, "vm_pu"]
        if vm is None:
            continue
        name = net.bus.at[bus_idx, "name"]
        # Distancia al límite más cercano (v_min o v_max)
        dist_min = vm - limits["v_min"]   # positivo = OK, negativo = violado
        dist_max = limits["v_max"] - vm   # positivo = OK, negativo = violado
        dist = min(dist_min, dist_max)    # el menor margen
        violated = vm < limits["v_min"] or vm > limits["v_max"]
        bus_results.append({
            "name": str(name), "vm_pu": round(float(vm), 4),
            "v_min": float(limits["v_min"]), "v_max": float(limits["v_max"]),
            "margin_pu": round(float(dist), 4), "violated": bool(violated),
        })
    bus_results.sort(key=lambda x: x["margin_pu"])  # menor margen primero

    line_results = []
    for line_idx in feeder_lines_index:
        if line_idx not in net.res_line.index:
            continue
        loading = net.res_line.at[line_idx, "loading_percent"]
        if loading is None:
            continue
        name = net.line.at[line_idx, "name"]
        margin = limits["line_max"] - float(loading)  # negativo = violado
        violated = float(loading) >= limits["line_max"]
        line_results.append({
            "name": str(name), "loading_percent": round(float(loading), 2),
            "limit_percent": float(limits["line_max"]),
            "margin_percent": round(float(margin), 2), "violated": bool(violated),
        })
    line_results.sort(key=lambda x: x["margin_percent"])  # menor margen primero

    trafo_results = []
    for trafo_idx in net.trafo.index:
        if trafo_idx not in net.res_trafo.index:
            continue
        loading = net.res_trafo.at[trafo_idx, "loading_percent"]
        if loading is None:
            continue
        name = net.trafo.at[trafo_idx, "name"]
        margin = limits["trafo_max"] - float(loading)
        violated = float(loading) >= limits["trafo_max"]
        trafo_results.append({
            "name": str(name), "loading_percent": round(float(loading), 2),
            "limit_percent": float(limits["trafo_max"]),
            "margin_percent": round(float(margin), 2), "violated": bool(violated),
        })
    trafo_results.sort(key=lambda x: x["margin_percent"])

    return {
        "buses": bus_results[:top_n],
        "lines": line_results[:top_n],
        "trafos": trafo_results[:top_n],
    }


def _run_pf_breach(net, index_bus, hosting_max_kw, is_generation, feeder_lines_index, buses_to, limits):
    """
    Corre PF con hosting_max_kw + 1 kW para mostrar el estado de rotura.
    Devuelve (pf_results, critical_elements) o (None, None) si no converge.
    """
    breach_kw = hosting_max_kw + 1
    if is_generation:
        tmp_idx = pp.create_sgen(net, bus=index_bus, p_mw=breach_kw*1e-3, q_mvar=0.0, name="_ac_breach")
    else:
        tmp_idx = pp.create_load(net, bus=index_bus, p_mw=breach_kw*1e-3, q_mvar=0.0, name="_ac_breach")

    try:
        pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, max_iteration=50, init="auto")
        converged = True
    except Exception:
        converged = False

    if not converged:
        if is_generation: net.sgen.drop(index=tmp_idx, inplace=True)
        else: net.load.drop(index=tmp_idx, inplace=True)
        return None, None

    critical = _extract_critical_elements(net, feeder_lines_index, buses_to, limits)

    if is_generation: net.sgen.drop(index=tmp_idx, inplace=True)
    else: net.load.drop(index=tmp_idx, inplace=True)

    return breach_kw, critical

def ac_hosting_generator(net, terminal_name: str, is_generation: bool, p_requested_kw: int = 100, voltage: str = 'BT', supply_lat: float = None, supply_lon: float = None):
    """
    Generador SSE principal.
    Yields: "progress: msg" | "result: {json}" | "error: msg"
    """
    yield "progress: Iniciando análisis A&C..."

    # Límite máximo según nivel de tensión
    p_max_kw = P_MAX_KW.get(voltage, 100_000)
    if p_max_kw is None:
        p_max_kw = 100_000

    matches = net.bus.loc[net.bus["name"] == terminal_name]
    if matches.empty:
        yield f"error: Bus '{terminal_name}' no encontrado"
        return

    index_bus     = matches.index[0]
    vn_kv         = float(net.bus.at[index_bus, "vn_kv"])
    voltage_level = get_voltage_level(vn_kv)
    limits        = get_limits(vn_kv)
    element_type  = "generación" if is_generation else "demanda"

    yield f"progress: Bus: {terminal_name} | {voltage_level} ({vn_kv} kV) | {element_type}"
    yield f"progress: Limite maximo de estudio: {p_max_kw:,} kW"
    yield f"progress: Potencia solicitada: {p_requested_kw} kW"
    yield f"progress: Límites -> V:[{limits['v_min']}, {limits['v_max']}] p.u. | I<{limits['line_max']}%"

    if index_bus == net.ext_grid["bus"].iloc[0]:
        yield "result: " + json.dumps({
            "terminal": terminal_name, "bus_id": int(index_bus),
            "voltage_level": voltage_level, "vn_kv": vn_kv, "type": element_type,
            "p_requested_kw": p_requested_kw, "hosting_max_kw": None,
            "remaining_kw": None, "admissible": None, "limits_violated": [],
            "message": "Bus Slack — no aplica análisis A&C", "pf_results": None,
        }, ensure_ascii=False)
        return

    yield "progress: Identificando feeder y buses del ramal..."
    feeder_lines_index, buses_to = get_feeder_scope(net, index_bus)
    yield f"progress: Ramal -> {len(feeder_lines_index)} líneas | {len(buses_to)} buses"

    # ── Búsqueda ──────────────────────────────────────────────────────────────
    hosting_max_kw = 0
    violated       = []
    saturated_base = False

    for msg in _search_generator(net, index_bus, feeder_lines_index, buses_to, limits, is_generation, p_max_kw=p_max_kw):
        if msg[0] == "progress":
            yield f"progress: {msg[1]}"
        elif msg[0] == "result":
            hosting_max_kw = msg[1]
            violated       = msg[2]
            saturated_base = msg[3]

    # ── Admisibilidad ─────────────────────────────────────────────────────────
    remaining_kw = max(0, hosting_max_kw - p_requested_kw)

    if saturated_base:
        admissible = False
        message = "NO ADMISIBLE — la red ya está saturada sin nueva conexión"
        # PF con potencia solicitada igualmente para mostrar estado real
        yield f"progress: Corriendo Power Flow con potencia solicitada ({p_requested_kw} kW) para mostrar estado..."
        pf_results = _run_pf_with_connection(net, index_bus, p_requested_kw * 1e-3, is_generation)
        if pf_results["converged"]:
            vms   = [b["vm_pu"] for b in pf_results["buses"] if b["vm_pu"] is not None]
            loads = [l["loading_percent"] for l in pf_results["lines"] if l["loading_percent"] is not None]
            yield (f"progress: PF con {p_requested_kw} kW -> "
                   f"V_min={min(vms):.4f} p.u. | Carga_max={max(loads):.1f}%")
        result = {
            "terminal": terminal_name, "bus_id": int(index_bus),
            "voltage_level": voltage_level, "vn_kv": vn_kv, "type": element_type,
            "p_requested_kw": p_requested_kw, "hosting_max_kw": 0,
            "remaining_kw": 0, "admissible": False,
            "limits_violated": list(dict.fromkeys(violated)),
            "message": message, "pf_results": pf_results,
        }
        import math as _m2
        def _san2(obj):
            if isinstance(obj,float): return None if(_m2.isnan(obj) or _m2.isinf(obj)) else obj
            if isinstance(obj,dict): return {k:_san2(v) for k,v in obj.items()}
            if isinstance(obj,list): return [_san2(v) for v in obj]
            return obj
        yield "result: " + json.dumps(_san2(result), ensure_ascii=False)
        return
    elif hosting_max_kw >= p_requested_kw:
        admissible = True
        message = (f"ADMISIBLE — hosting máximo {hosting_max_kw} kW ≥ "
                   f"potencia solicitada {p_requested_kw} kW "
                   f"(capacidad restante: {remaining_kw} kW)")
    else:
        admissible = False
        message = (f"NO ADMISIBLE — hosting máximo {hosting_max_kw} kW "
                   f"< potencia solicitada {p_requested_kw} kW")

    # ── PF con potencia solicitada ────────────────────────────────────────────
    yield f"progress: Corriendo Power Flow con potencia solicitada ({p_requested_kw} kW)..."
    pf_results = _run_pf_with_connection(net, index_bus, p_requested_kw * 1e-3, is_generation)

    # Extract critical elements from the requested-power PF
    critical_requested = None
    if pf_results["converged"]:
        vms   = [b["vm_pu"] for b in pf_results["buses"] if b["vm_pu"] is not None]
        loads = [l["loading_percent"] for l in pf_results["lines"] if l["loading_percent"] is not None]
        yield (f"progress: PF con {p_requested_kw} kW -> "
               f"V_min={min(vms):.4f} p.u. | Carga_max={max(loads):.1f}%")
        # Re-run to extract critical — needed because _run_pf_with_connection cleans up
        if is_generation:
            tmp2 = pp.create_sgen(net, bus=index_bus, p_mw=p_requested_kw*1e-3, q_mvar=0.0, name="_ac_crit")
        else:
            tmp2 = pp.create_load(net, bus=index_bus, p_mw=p_requested_kw*1e-3, q_mvar=0.0, name="_ac_crit")
        try:
            pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, max_iteration=50, init="auto")
            critical_requested = _extract_critical_elements(net, feeder_lines_index, buses_to, limits)
        except Exception:
            pass
        if is_generation: net.sgen.drop(index=tmp2, inplace=True)
        else: net.load.drop(index=tmp2, inplace=True)
    else:
        yield f"progress: ⚠ PF con {p_requested_kw} kW no convergió"

    # Breach PF: hosting_max + 1 kW -> show what breaks first
    yield f"progress: Corriendo Power Flow de rotura ({hosting_max_kw + 1} kW)..."
    breach_kw, critical_breach = _run_pf_breach(
        net, index_bus, hosting_max_kw, is_generation, feeder_lines_index, buses_to, limits
    )
    if critical_breach:
        yield f"progress: PF de rotura completado — elementos críticos identificados"
    else:
        yield f"progress: ⚠ PF de rotura no convergió"

    # ── Acometida recommendation ─────────────────────────────────────────────
    from grid_network.conductor_db import recommend_acometida
    import math as _m3

    # Distance from supply point to connection bus
    dist_m = None
    if supply_lat is not None and supply_lon is not None:
        # Get bus coordinates using get_bus_geo (x=lon, y=lat)
        try:
            from grid_network.geodata import get_bus_geo
            geo = get_bus_geo(net, index_bus)
            if geo:
                bus_lat = geo["y"]   # y = latitude
                bus_lon = geo["x"]   # x = longitude
                if bus_lat and bus_lon and abs(bus_lat) < 90 and abs(bus_lon) < 180:
                    R = 6371000
                    lat1 = _m3.radians(supply_lat)
                    lon1 = _m3.radians(supply_lon)
                    lat2 = _m3.radians(bus_lat)
                    lon2 = _m3.radians(bus_lon)
                    dlat = lat2 - lat1
                    dlon = lon2 - lon1
                    a = (_m3.sin(dlat/2)**2 +
                         _m3.cos(lat1)*_m3.cos(lat2)*_m3.sin(dlon/2)**2)
                    dist_m = round(R * 2 * _m3.atan2(_m3.sqrt(a), _m3.sqrt(1-a)), 1)
        except Exception:
            pass

    acometida = recommend_acometida(
        p_kw       = p_requested_kw,
        voltage_kv = vn_kv,
        phases     = 3,
        dist_m     = dist_m,
    )
    if dist_m:
        yield f"progress: Acometida recomendada: {acometida['conductor']} | {dist_m} m | ΔV={acometida.get('vdrop_percent','—')}%"

    result = {
        "terminal":        terminal_name,
        "bus_id":          int(index_bus),
        "voltage_level":   voltage_level,
        "vn_kv":           vn_kv,
        "type":            element_type,
        "p_requested_kw":  p_requested_kw,
        "hosting_max_kw":  hosting_max_kw,
        "remaining_kw":    remaining_kw,
        "admissible":      admissible,
        "limits_violated": list(dict.fromkeys(violated)),
        "message":         message,
        "pf_results":      pf_results,
        "critical_requested": critical_requested,
        "breach_kw":       breach_kw,
        "critical_breach": critical_breach,
        "acometida":       acometida,
        "dist_m":          dist_m,
    }

    yield "progress: Generando informe..."

    # Sanitize: replace NaN/Inf with None before JSON serialization
    import math
    def _sanitize(obj):
        if isinstance(obj, float):
            return None if (math.isnan(obj) or math.isinf(obj)) else obj
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        return obj

    yield "result: " + json.dumps(_sanitize(result), ensure_ascii=False)
