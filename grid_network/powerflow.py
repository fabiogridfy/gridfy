"""
powerflow.py
Todo lo relacionado con cálculo de flujos de carga y diagnóstico de convergencia.
Responsabilidad única: recibir net → calcular → devolver resultados o diagnóstico.
"""
import math
import pandapower as pp
import pandapower.topology as top


def safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    except Exception:
        return None


def run_powerflow(net) -> dict:
    """
    Ejecuta el flujo de carga Newton-Raphson.
    Devuelve dict con resultados o lanza ValueError con diagnóstico si no converge.
    """
    try:
        pp.runpp(net, algorithm="nr", calculate_voltage_angles=True,
                 max_iteration=50, init="auto")
    except Exception as e:
        # Intentamos diagnóstico antes de rendirse
        diag = diagnose(net)
        raise ValueError(f"No convergió: {e} | Diagnóstico: {diag['summary']}") from e

    return {
        "converged": True,
        "buses": [
            {"id": int(i), "vm_pu": safe_float(r["vm_pu"]),
             "va_degree": safe_float(r["va_degree"]),
             "p_mw": safe_float(r["p_mw"]), "q_mvar": safe_float(r["q_mvar"])}
            for i, r in net.res_bus.iterrows()
        ],
        "lines": [
            {"id": int(i), "loading_percent": safe_float(r["loading_percent"]),
             "i_ka": safe_float(r["i_ka"]), "p_from_mw": safe_float(r["p_from_mw"])}
            for i, r in net.res_line.iterrows()
        ],
        "trafos": [
            {"id": int(i), "loading_percent": safe_float(r["loading_percent"]),
             "p_hv_mw": safe_float(r["p_hv_mw"]), "q_hv_mvar": safe_float(r["q_hv_mvar"])}
            for i, r in net.res_trafo.iterrows()
        ],
    }


def diagnose(net) -> dict:
    """
    Analiza la red sin ejecutar PF y devuelve un informe de posibles problemas.
    Útil para depurar cuando el PF no converge.
    """
    issues = []
    warnings = []

    # ── 1. Balance de potencia ────────────────────────────────────────────────
    total_load = float(net.load.p_mw.sum()) if len(net.load) > 0 else 0.0
    total_sgen = float(net.sgen.p_mw.sum()) if len(net.sgen) > 0 else 0.0
    net_balance = total_load - total_sgen  # positivo → red importa, negativo → red exporta

    if total_sgen > total_load * 2 and total_load > 0:
        issues.append(
            f"Generación ({total_sgen:.3f} MW) muy superior a carga ({total_load:.3f} MW). "
            f"La red exporta {abs(net_balance):.3f} MW. "
            f"Comprueba que P(MW) en Gen_Data es potencia operacional, no potencia contratada."
        )
    elif total_sgen > 0 and total_load == 0:
        issues.append(
            f"Hay {total_sgen:.3f} MW de generación pero 0 MW de carga. "
            f"Las cargas tienen P(MW) vacío y se cargaron a 0. "
            f"Usa Pot. contratada o introduce valores reales."
        )

    # ── 2. Buses sin suministro ───────────────────────────────────────────────
    try:
        unsupplied = top.unsupplied_buses(net)
        if unsupplied:
            issues.append(f"Buses sin suministro eléctrico: {sorted(unsupplied)}")
    except Exception:
        pass

    # ── 3. Cargas o sgens en buses inexistentes ───────────────────────────────
    valid_buses = set(net.bus.index)
    for idx, row in net.load.iterrows():
        if int(row["bus"]) not in valid_buses:
            issues.append(f"Load {idx} apunta a bus {row['bus']} que no existe")
    for idx, row in net.sgen.iterrows():
        if int(row["bus"]) not in valid_buses:
            issues.append(f"Sgen {idx} apunta a bus {row['bus']} que no existe")

    # ── 4. Líneas con R o X = 0 ───────────────────────────────────────────────
    zero_r = net.line[net.line["r_ohm_per_km"] == 0]
    if not zero_r.empty:
        warnings.append(f"{len(zero_r)} líneas con R=0 (pueden causar singularidad)")

    # ── 5. Transformador: relación de tensión ────────────────────────────────
    for idx, row in net.trafo.iterrows():
        hv_vn = net.bus.at[row["hv_bus"], "vn_kv"]
        lv_vn = net.bus.at[row["lv_bus"], "vn_kv"]
        if abs(row["vn_hv_kv"] - hv_vn) / max(hv_vn, 1) > 0.2:
            warnings.append(
                f"Trafo {idx}: vn_hv_kv={row['vn_hv_kv']} no coincide bien "
                f"con tensión del bus HV ({hv_vn} kV)"
            )

    # ── Resumen ───────────────────────────────────────────────────────────────
    summary = []
    if issues:
        summary.append(f"{len(issues)} problema(s) crítico(s)")
    if warnings:
        summary.append(f"{len(warnings)} aviso(s)")
    if not issues and not warnings:
        summary.append("Sin problemas detectados en topología/datos")

    return {
        "summary": " | ".join(summary) if summary else "OK",
        "issues": issues,
        "warnings": warnings,
        "balance": {
            "total_load_mw": round(total_load, 4),
            "total_sgen_mw": round(total_sgen, 4),
            "net_balance_mw": round(net_balance, 4),
            "note": "Positivo = red importa; Negativo = red exporta"
        },
        "counts": {
            "buses": len(net.bus),
            "lines": len(net.line),
            "trafos": len(net.trafo),
            "loads": len(net.load),
            "sgens": len(net.sgen),
        }
    }
