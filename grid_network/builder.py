"""
builder.py
Construye la red pandapower a partir del Excel.
Responsabilidad única: leer datos → crear net.
"""
import utm
import pandas as pd
import pandapower as pp


def find_bus(df_Terminal: pd.DataFrame, name: str):
    """Busca un bus por nombre exacto; si no lo encuentra, por coincidencia parcial."""
    exact = df_Terminal[df_Terminal["Nombre"] == name]
    if not exact.empty:
        return int(exact.index[0])
    for idx, row in df_Terminal.iterrows():
        if str(name) in str(row["Nombre"]) or str(row["Nombre"]) in str(name):
            return int(idx)
    return None


def build_network(excel_path: str, timezone: int = 30, geozone: str = "N") -> pp.pandapowerNet:
    """
    Lee el Excel y devuelve una red pandapower lista para simular.

    Hojas usadas: Terminal, ExternalGrid, Lineas, Transformadores, Loads_Data, Gen_Data
    """
    # ── Leer hojas ────────────────────────────────────────────────────────────
    df_Terminal = pd.read_excel(excel_path, sheet_name="Terminal")
    df_Lines    = pd.read_excel(excel_path, sheet_name="Lineas")
    df_ext_grid = pd.read_excel(excel_path, sheet_name="ExternalGrid")
    df_trafo    = pd.read_excel(excel_path, sheet_name="Transformadores")

    net = pp.create_empty_network()

    # ── Buses ─────────────────────────────────────────────────────────────────
    for _, x in df_Terminal.iterrows():
        lat, lon = utm.to_latlon(x["X"], x["Y"], timezone, geozone)
        pp.create_bus(net, vn_kv=x["Tensión"], name=x["Nombre"], geodata=(lon, lat))

    # helper con df_Terminal capturado
    def fb(name):
        return find_bus(df_Terminal, name)

    # ── External grid ─────────────────────────────────────────────────────────
    for _, x in df_ext_grid.iterrows():
        b = fb(x["Terminal"])
        if b is not None:
            pp.create_ext_grid(net, bus=b, vm_pu=1.0, name=x["Nombre"],
                               s_sc_max_mva=1000, rx_max=0.1, x0x_max=1, r0x0_max=0.1)

    # ── Líneas ────────────────────────────────────────────────────────────────
    has_conductor = "Conductor"   in df_Lines.columns
    has_seccion   = "seccion_mm2" in df_Lines.columns
    for _, x in df_Lines.iterrows():
        try:
            line_idx = pp.create_line_from_parameters(
                net,
                from_bus=fb(x["Terminal_i"]),
                to_bus=fb(x["Terminal_j"]),
                length_km=x["Longitud (km)"],
                r_ohm_per_km=x["R/km"],
                x_ohm_per_km=x["X/km"],
                r0_ohm_per_km=3 * x["R/km"],
                x0_ohm_per_km=3 * x["X/km"],
                c_nf_per_km=0,
                c0_nf_per_km=0,
                max_i_ka=x["I max (kA)"],
                name=x["Nombre"],
            )
            # Preservar metadatos del conductor para round-trip
            if has_conductor and pd.notna(x.get("Conductor")):
                net.line.at[line_idx, "conductor"]   = str(x["Conductor"])
            if has_seccion and pd.notna(x.get("seccion_mm2")):
                net.line.at[line_idx, "seccion_mm2"] = float(x["seccion_mm2"])
        except Exception as e:
            print(f"  [builder] Línea {_} error: {e}")

    # ── Transformadores ───────────────────────────────────────────────────────
    for _, x in df_trafo.iterrows():
        try:
            pp.create_transformer_from_parameters(
                net,
                hv_bus=fb(x["primary_node_code"]),
                lv_bus=fb(x["secondary_node_code"]),
                sn_mva=x["nominal_apparent_power_kVA"] * 1e-3,
                vn_hv_kv=x["Tension HV"],
                vn_lv_kv=x["Tension LV"],
                vkr_percent=x["vkr_percent"],
                vk_percent=x["vk_percent"],
                pfe_kw=x["pfe_kw"],
                i0_percent=x["i0_percent"],
                name=x["code"],
                vector_group="Dyn",
                vk0_percent=4, vkr0_percent=1.031746,
                mag0_percent=100, mag0_rx=0, si0_hv_partial=0.9, tap_pos=0,
            )
        except Exception as e:
            print(f"  [builder] Trafo {_} error: {e}")

    # ── Cargas (Loads_Data) ───────────────────────────────────────────────────
    try:
        df_loads = pd.read_excel(excel_path, sheet_name="Loads_Data")
        for _, x in df_loads.iterrows():
            b = fb(x["Terminal"])
            if b is None:
                print(f"  [builder] Load '{x.get('CUPS', _)}': bus no encontrado")
                continue
            # P(MW) tiene prioridad; si vacío usa Pot.contratada como nominal (no operacional)
            p_mw   = float(x["P (MW)"])   if pd.notna(x.get("P (MW)"))   else 0.0
            q_mvar = float(x["Q (MVAR)"]) if pd.notna(x.get("Q (MVAR)")) else 0.0
            nom_p  = float(x.get("Pot. contratada (kW)", 0)) * 1e-3
            pp.create_load(net, bus=b, name=str(x.get("CUPS", f"load_{_}")),
                           p_mw=p_mw, q_mvar=q_mvar,
                           max_p_mw=nom_p)   # guardamos la potencia contratada como límite
        print(f"  [builder] Cargas: {len(net.load)}")
    except Exception as e:
        print(f"  [builder] Loads_Data error: {e}")

    # ── Generadores (Gen_Data) ────────────────────────────────────────────────
    try:
        df_gen = pd.read_excel(excel_path, sheet_name="Gen_Data")
        for _, x in df_gen.iterrows():
            b = fb(x["Terminal"])
            if b is None:
                print(f"  [builder] Gen '{x.get('CUPS', _)}': bus no encontrado")
                continue
            p_mw   = float(x["P (MW)"])   if pd.notna(x.get("P (MW)"))   else 0.0
            q_mvar = float(x["Q (MVAR)"]) if pd.notna(x.get("Q (MVAR)")) else 0.0
            nom_p  = float(x.get("Pot. contratada (kW)", 0)) * 1e-3
            pp.create_sgen(net, bus=b, name=str(x.get("CUPS", f"gen_{_}")),
                           p_mw=p_mw, q_mvar=q_mvar,
                           max_p_mw=nom_p)
        print(f"  [builder] Generadores: {len(net.sgen)}")
    except Exception as e:
        print(f"  [builder] Gen_Data error: {e}")

    print(f"  [builder] Red lista: {len(net.bus)} buses | {len(net.line)} líneas | "
          f"{len(net.trafo)} trafos | {len(net.load)} cargas | {len(net.sgen)} sgens")
    return net
