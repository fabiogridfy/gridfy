# ⚡ GridViewer — Santa Ana 2 MVP

Visor web interactivo de red eléctrica de baja tensión basado en pandapower.

## Arranque rápido

```bash
# 1. Pon el Excel en la misma carpeta que main.py
# 2. Instala dependencias
pip install fastapi uvicorn pandapower utm openpyxl

# 3. Arranca
uvicorn main:app --reload

# 4. Abre http://localhost:8000
```

O simplemente:
```bash
bash start.sh
```

## Qué hace el MVP

| Función | Descripción |
|---|---|
| 🗺️ Mapa interactivo | Red sobre cartografía oscura (CartoDB Dark) |
| 🟢 Nodos coloreados | Verde/amarillo/rojo según tensión (p.u.) |
| 📏 Líneas coloreadas | Según % de cargabilidad |
| ▶ Run Power Flow | Lanza `pp.runpp()` y actualiza colores en tiempo real |
| 🔍 Click en elemento | Panel lateral con parámetros detallados |
| ✏️ Edición | Modifica parámetros técnicos (sn_mva, tap_pos, etc.) y recalcula |

## API endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/network` | Topología completa (buses, líneas, trafos) |
| POST | `/api/powerflow` | Ejecuta power flow, devuelve resultados |
| GET | `/api/element/{type}/{id}` | Parámetros de un elemento |
| PATCH | `/api/element/{type}/{id}` | Modifica un parámetro |

## Estructura

```
grid_mvp/
├── main.py              # FastAPI backend
├── static/
│   └── index.html       # Frontend (Leaflet + JS puro)
├── Dataset_Sta_Ana2.xlsx
├── start.sh
└── README.md
```

## Próximos pasos sugeridos

- [ ] Estudios de capacidad nodal
- [ ] Carga de perfiles temporales (series horarias)
- [ ] Múltiples redes / escenarios
- [ ] Exportar resultados a Excel
- [ ] Docker Compose para despliegue
