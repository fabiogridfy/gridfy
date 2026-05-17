"""
db/crud.py
Operaciones CRUD sobre la base de datos de redes.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from db.models import Network


def list_networks(db: Session):
    return db.query(Network).order_by(Network.created_at.desc()).all()


def get_network(db: Session, network_id: int):
    return db.query(Network).filter(Network.id == network_id).first()


def get_network_by_name(db: Session, name: str):
    return db.query(Network).filter(Network.name == name).first()


def create_network(db: Session, name: str, description: str, voltage: str,
                   excel_path: str, pickle_path: str = None):
    net = Network(
        name=name, description=description, voltage=voltage,
        excel_path=excel_path, pickle_path=pickle_path,
    )
    db.add(net)
    db.commit()
    db.refresh(net)
    return net


def update_pickle(db: Session, network_id: int, pickle_path: str):
    net = get_network(db, network_id)
    if net:
        net.pickle_path = pickle_path
        net.updated_at  = datetime.utcnow()
        db.commit()
        db.refresh(net)
    return net


def delete_network(db: Session, network_id: int):
    net = get_network(db, network_id)
    if net:
        db.delete(net)
        db.commit()
    return net


# ── Scenarios ─────────────────────────────────────────────────────────────────
from db.models import Scenario

def list_scenarios(db, network_id: int):
    return db.query(Scenario).filter(Scenario.network_id == network_id).order_by(Scenario.created_at.desc()).all()

def get_scenario(db, scenario_id: int):
    return db.query(Scenario).filter(Scenario.id == scenario_id).first()

def create_scenario(db, network_id: int, name: str, description: str, excel_path: str):
    s = Scenario(network_id=network_id, name=name, description=description, excel_path=excel_path)
    db.add(s); db.commit(); db.refresh(s)
    return s

def delete_scenario(db, scenario_id: int):
    s = get_scenario(db, scenario_id)
    if s: db.delete(s); db.commit()
    return s

def set_active_scenario(db, network_id: int, scenario_id: int | None):
    """Marca un escenario como activo (desactiva el resto de la red)."""
    db.query(Scenario).filter(Scenario.network_id == network_id).update({"is_active": False})
    if scenario_id:
        s = get_scenario(db, scenario_id)
        if s: s.is_active = True
    db.commit()
