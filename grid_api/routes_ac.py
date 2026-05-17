"""
routes_ac.py - A&C con SSE + PDF report
"""
import json, io
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from grid_network.ac_hosting import ac_hosting_generator
from grid_api.routes_network import get_net

router = APIRouter()

from typing import Optional as _Opt

class ACRequest(BaseModel):
    terminal_name: str
    is_generation: bool = False
    p_requested_kw: int = 100
    voltage: str = 'BT'
    supply_lat: _Opt[float] = None
    supply_lon: _Opt[float] = None

@router.post("/ac/run")
def run_ac(body: ACRequest):
    net = get_net()
    def generate():
        try:
            for msg in ac_hosting_generator(
                net, body.terminal_name, body.is_generation,
                body.p_requested_kw, body.voltage,
                supply_lat=body.supply_lat, supply_lon=body.supply_lon,
            ):
                yield f"data: {msg}\n\n"
        except Exception as e:
            yield f"data: error: {e}\n\n"
        finally:
            yield "data: done\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

class ACReportRequest(BaseModel):
    result: dict

@router.post("/ac/report")
def generate_report(body: ACReportRequest):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.lib.enums import TA_CENTER
    except ImportError:
        return Response("pip install reportlab", status_code=500, media_type="text/plain")

    r = body.result
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    GREEN  = colors.HexColor("#39d98a")
    RED    = colors.HexColor("#ef4444")
    DARK   = colors.HexColor("#0d0f14")
    MUTED  = colors.HexColor("#64748b")
    ACCENT = colors.HexColor("#00e5ff")

    admissible   = r.get("admissible", False)
    result_color = GREEN if admissible else RED
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    story = []

    story.append(Paragraph("GridViewer — Informe A&C", ParagraphStyle("t", parent=styles["Title"], fontSize=20, textColor=DARK, spaceAfter=4)))
    story.append(Paragraph(f"Análisis de Acceso y Conexión · {now}", ParagraphStyle("s", parent=styles["Normal"], fontSize=10, textColor=MUTED, spaceAfter=14)))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=14))

    verdict = "ADMISIBLE" if admissible else "NO ADMISIBLE"
    story.append(Paragraph(f"{'✓' if admissible else '✗'}  {verdict}", ParagraphStyle("v", parent=styles["Normal"], fontSize=22, textColor=result_color, fontName="Helvetica-Bold", spaceAfter=6)))
    story.append(Paragraph(r.get("message", ""), ParagraphStyle("b", parent=styles["Normal"], fontSize=10, textColor=DARK, spaceAfter=12)))

    story.append(Paragraph("Datos del análisis", ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, textColor=DARK, spaceBefore=10, spaceAfter=6)))
    ac     = r.get("acometida") or {}
    dist_m = r.get("dist_m")
    rows = [
        ["Campo", "Valor"],
        ["Bus / Terminal",           r.get("terminal","—")],
        ["Nivel de tensión",         r.get("voltage_level","—")],
        ["Tipo de conexión",         r.get("type","—")],
        ["Potencia solicitada",       f"{r.get('p_requested_kw','—')} kW"],
        ["Hosting máximo calculado",  f"{r.get('hosting_max_kw','—')} kW"],
        ["Capacidad restante",        f"{r.get('remaining_kw','—')} kW"],
        ["Veredicto",                verdict],
    ]
    if ac:
        rows += [
            ["— ACOMETIDA RECOMENDADA —", ""],
            ["Cable recomendado",     ac.get("conductor","—")],
            ["Corriente demandada",   f"{ac.get('i_demand_a','—')} A  (I_max: {ac.get('i_max_a','—')} A)"],
            ["Sección",               f"{ac.get('section_mm2','—')} mm²"],
            ["Distancia estimada",    f"{dist_m} m" if dist_m else "—"],
            ["Caída de tensión",      f"{ac.get('vdrop_percent','—')} %" if ac.get('vdrop_percent') else "—"],
        ]
    ts = TableStyle([
        ("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f8fafc")]),
        ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#e2e8f0")),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),("TEXTCOLOR",(1,-1),(1,-1),result_color),
        # Acometida header row (row 8, only if present)
        *([
            ("BACKGROUND",(0,8),(-1,8),colors.HexColor("#037A68")),
            ("TEXTCOLOR",(0,8),(-1,8),colors.white),
            ("FONTNAME",(0,8),(-1,8),"Helvetica-Bold"),
            ("SPAN",(0,8),(-1,8)),
            ("TEXTCOLOR",(1,9),(1,9),colors.HexColor("#037A68")),
            ("FONTNAME",(1,9),(1,9),"Helvetica-Bold"),
        ] if ac else []),
    ])
    t = Table(rows, colWidths=[6*cm, 10*cm])
    t.setStyle(ts)
    story.append(t)
    story.append(Spacer(1, 0.4*cm))



    def make_table(rows, col_widths=None):
        col_widths = col_widths or [5*cm, 4*cm, 4*cm, 3*cm]
        t = Table(rows, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f8fafc")]),
            ("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#e2e8f0")),
            ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ]))
        return t

    def status_color(violated):
        return RED if violated else GREEN

    # ── SECTION 1: Estado tras A&C (con p_requested) ─────────────────────────
    story.append(Paragraph(
        f"1. Estado de la red con {r.get('p_requested_kw','—')} kW conectados",
        ParagraphStyle("h2c", parent=styles["Heading2"], fontSize=12, textColor=DARK, spaceBefore=14, spaceAfter=6)))
    story.append(Paragraph(
        "Elementos más próximos a sus límites técnicos tras la conexión solicitada.",
        ParagraphStyle("desc", parent=styles["Normal"], fontSize=9, textColor=MUTED, spaceAfter=8)))

    crit = r.get("critical_requested")
    if crit:
        # Buses
        if crit.get("buses"):
            story.append(Paragraph("Nodos — Tensión", ParagraphStyle("sh", parent=styles["Normal"], fontSize=10, fontName="Helvetica-Bold", textColor=DARK, spaceAfter=4)))
            bus_rows = [["Nodo", "V (p.u.)", "V_min / V_max", "Margen (p.u.)"]]
            def _fv(v, fmt='.4f'): return format(v, fmt) if v is not None else "—"
            for b in crit["buses"]:
                bus_rows.append([
                    b["name"][:35],
                    _fv(b.get("vm_pu")),
                    f"{_fv(b.get('v_min'),'.2f')} / {_fv(b.get('v_max'),'.2f')}",
                    _fv(b.get("margin_pu")),
                ])
            bt = make_table(bus_rows, [7*cm, 2.5*cm, 3*cm, 3.5*cm])
            for i, b in enumerate(crit["buses"], 1):
                if b.get("violated"):
                    bt.setStyle(TableStyle([("TEXTCOLOR",(1,i),(1,i),RED),("FONTNAME",(0,i),(-1,i),"Helvetica-Bold")]))
                elif b.get("margin_pu") is not None and b["margin_pu"] < 0.02:
                    bt.setStyle(TableStyle([("TEXTCOLOR",(1,i),(1,i),colors.HexColor("#b45309"))]))
            story.append(bt)
            story.append(Spacer(1, 0.2*cm))

        # Lines
        if crit.get("lines"):
            story.append(Paragraph("Líneas — Cargabilidad", ParagraphStyle("sh2", parent=styles["Normal"], fontSize=10, fontName="Helvetica-Bold", textColor=DARK, spaceAfter=4, spaceBefore=6)))
            line_rows = [["Línea", "Carga (%)", "Límite (%)", "Margen (%)"]]
            def _fmt(v, fmt): return format(v, fmt) if v is not None else "—"
            for l in crit["lines"]:
                line_rows.append([
                    l["name"][:35],
                    _fmt(l.get('loading_percent'), '.1f'),
                    _fmt(l.get('limit_percent'),   '.0f'),
                    _fmt(l.get('margin_percent'),  '.1f'),
                ])
            lt = make_table(line_rows, [7*cm, 2.5*cm, 2.5*cm, 4*cm])
            for i, l in enumerate(crit["lines"], 1):
                if l.get("violated"):
                    lt.setStyle(TableStyle([("TEXTCOLOR",(1,i),(1,i),RED),("FONTNAME",(0,i),(-1,i),"Helvetica-Bold")]))
                elif l.get("margin_percent") is not None and l["margin_percent"] < 10:
                    lt.setStyle(TableStyle([("TEXTCOLOR",(1,i),(1,i),colors.HexColor("#b45309"))]))
            story.append(lt)

        # Trafos
        if crit.get("trafos"):
            story.append(Paragraph("Transformadores", ParagraphStyle("sh3", parent=styles["Normal"], fontSize=10, fontName="Helvetica-Bold", textColor=DARK, spaceAfter=4, spaceBefore=6)))
            tr_rows = [["Transformador", "Carga (%)", "Límite (%)", "Margen (%)"]]
            for t2 in crit["trafos"]:
                tr_rows.append([t2["name"][:35], f"{t2['loading_percent']:.1f}", f"{t2['limit_percent']:.0f}", f"{t2['margin_percent']:.1f}"])
            story.append(make_table(tr_rows, [7*cm, 2.5*cm, 2.5*cm, 4*cm]))
    else:
        story.append(Paragraph("Power Flow no disponible para este escenario.",
            ParagraphStyle("na", parent=styles["Normal"], fontSize=9, textColor=MUTED)))

    story.append(Spacer(1, 0.4*cm))

    # ── SECTION 2: Estado de rotura (hosting_max + 1 kW) ─────────────────────
    breach_kw = r.get("breach_kw")
    crit_b    = r.get("critical_breach")
    story.append(Paragraph(
        f"2. Estado de rotura de límites ({breach_kw if breach_kw else '—'} kW)",
        ParagraphStyle("h2d", parent=styles["Heading2"], fontSize=12, textColor=RED, spaceBefore=14, spaceAfter=6)))
    story.append(Paragraph(
        f"Elementos que superan sus límites al conectar {breach_kw if breach_kw else '—'} kW "
        f"(hosting máximo + 1 kW). Los marcados en rojo son los que provocan el rechazo.",
        ParagraphStyle("desc2", parent=styles["Normal"], fontSize=9, textColor=MUTED, spaceAfter=8)))

    if crit_b:
        violated_buses = [b for b in crit_b.get("buses",[]) if b["violated"]]
        violated_lines = [l for l in crit_b.get("lines",[]) if l["violated"]]
        violated_trafos= [t2 for t2 in crit_b.get("trafos",[]) if t2["violated"]]

        if violated_buses:
            story.append(Paragraph("Nodos con violación de tensión:", ParagraphStyle("sv", parent=styles["Normal"], fontSize=10, fontName="Helvetica-Bold", textColor=RED, spaceAfter=4)))
            br = [["Nodo", "V (p.u.)", "Límite", "Desviación (p.u.)"]]
            for b in violated_buses:
                dev = min(b["vm_pu"] - b["v_min"], b["v_max"] - b["vm_pu"])
                br.append([b["name"][:35], str(b["vm_pu"]), f"{b['v_min']}–{b['v_max']}", f"{dev:.4f}"])
            bt2 = make_table(br, [7*cm, 2.5*cm, 3*cm, 3.5*cm])
            bt2.setStyle(TableStyle([("TEXTCOLOR",(1,1),(-1,-1),RED)]))
            story.append(bt2)
            story.append(Spacer(1,0.2*cm))

        if violated_lines:
            story.append(Paragraph("Líneas con sobrecarga:", ParagraphStyle("sl", parent=styles["Normal"], fontSize=10, fontName="Helvetica-Bold", textColor=RED, spaceAfter=4, spaceBefore=4)))
            lr = [["Línea", "Carga (%)", "Límite (%)", "Exceso (%)"]]
            for l in violated_lines:
                lr.append([l["name"][:35], f"{l['loading_percent']:.1f}", f"{l['limit_percent']:.0f}", f"{abs(l['margin_percent']):.1f}"])
            lt2 = make_table(lr, [7*cm, 2.5*cm, 2.5*cm, 4*cm])
            lt2.setStyle(TableStyle([("TEXTCOLOR",(1,1),(-1,-1),RED)]))
            story.append(lt2)

        if violated_trafos:
            story.append(Paragraph("Transformadores con sobrecarga:", ParagraphStyle("st", parent=styles["Normal"], fontSize=10, fontName="Helvetica-Bold", textColor=RED, spaceAfter=4, spaceBefore=4)))
            tr2 = [["Transformador", "Carga (%)", "Límite (%)", "Exceso (%)"]]
            for t2 in violated_trafos:
                tr2.append([t2["name"][:35], f"{t2['loading_percent']:.1f}", f"{t2['limit_percent']:.0f}", f"{abs(t2['margin_percent']):.1f}"])
            story.append(make_table(tr2, [7*cm, 2.5*cm, 2.5*cm, 4*cm]))

        if not violated_buses and not violated_lines and not violated_trafos:
            story.append(Paragraph("No se identificaron violaciones explícitas (posible problema de convergencia).",
                ParagraphStyle("nv", parent=styles["Normal"], fontSize=9, textColor=MUTED)))
    else:
        story.append(Paragraph("Power Flow de rotura no disponible (posible no convergencia).",
            ParagraphStyle("nb", parent=styles["Normal"], fontSize=9, textColor=MUTED)))

    story.append(Spacer(1, 0.6*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MUTED))
    story.append(Paragraph("Informe generado por GridViewer · pandapower",
        ParagraphStyle("f", parent=styles["Normal"], fontSize=8, textColor=MUTED, alignment=TA_CENTER, spaceBefore=8)))

    doc.build(story)
    buf.seek(0)
    fname = f"AC_{r.get('terminal','bus').replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(content=buf.read(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
