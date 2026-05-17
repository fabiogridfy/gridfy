"""
grid_network/manager.py
Gestiona qué red está activa en memoria y las operaciones de persistencia.
Una única instancia global (singleton) compartida por todos los routers.
"""
import os
import pandapower as pp
from grid_network.builder import build_network


class NetworkManager:
    """Singleton que mantiene la red activa y su metadato de BD."""

    def __init__(self):
        self._net        = None   # pandapowerNet activa
        self._network_id = None   # id en BD
        self._name       = None   # nombre legible

    # ── Getters ───────────────────────────────────────────────────────────────
    @property
    def net(self):
        return self._net

    @property
    def network_id(self):
        return self._network_id

    @property
    def name(self):
        return self._name

    def is_loaded(self):
        return self._net is not None

    # ── Cargar desde Excel ────────────────────────────────────────────────────
    def load_from_excel(self, excel_path: str, network_id: int, name: str):
        """Construye la red desde el Excel y la pone como activa."""
        self._net        = build_network(excel_path)
        self._network_id = network_id
        self._name       = name

    # ── Cargar desde pickle ───────────────────────────────────────────────────
    def load_from_pickle(self, pickle_path: str, network_id: int, name: str):
        """Carga la red desde un pickle guardado (con modificaciones)."""
        self._net        = pp.from_pickle(pickle_path)
        self._network_id = network_id
        self._name       = name

    # ── Guardar estado actual ─────────────────────────────────────────────────
    def save_to_pickle(self, pickle_path: str):
        """Serializa la red activa a disco."""
        if self._net is None:
            raise RuntimeError("No hay red cargada")
        os.makedirs(os.path.dirname(pickle_path), exist_ok=True)
        pp.to_pickle(self._net, pickle_path)

    # ── Reset ─────────────────────────────────────────────────────────────────
    def unload(self):
        self._net        = None
        self._network_id = None
        self._name       = None


# Instancia global — importada por todos los routers
network_manager = NetworkManager()
