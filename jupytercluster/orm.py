"""Database models for JupyterCluster"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Hub(Base):
    """Represents a JupyterHub instance managed by JupyterCluster"""

    __tablename__ = "hubs"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    namespace = Column(String(255), unique=True, nullable=False, index=True)  # UNIQUE: One hub per namespace
    owner = Column(String(255), nullable=False, index=True)  # Username of the owner - CRITICAL for permission checks
    helm_release_name = Column(String(255), unique=True, nullable=False)

    # Helm chart configuration
    helm_chart = Column(String(255), default="jupyterhub/jupyterhub")
    helm_chart_version = Column(String(50))
    values = Column(JSON, default=dict)  # Helm values override (sanitized before storage)

    # Status information
    status = Column(String(50), default="pending")  # pending, running, stopped, error
    url = Column(String(500))  # Access URL for the hub

    # Metadata
    created = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)
    description = Column(Text)

    # Relationships
    events = relationship("HubEvent", back_populates="hub", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Hub(name={self.name}, namespace={self.namespace}, owner={self.owner})>"


class HubEvent(Base):
    """Events/logs for hub operations"""

    __tablename__ = "hub_events"

    id = Column(Integer, primary_key=True)
    hub_id = Column(Integer, ForeignKey("hubs.id"), nullable=False)
    event_type = Column(String(50), nullable=False)  # created, updated, deleted, error
    message = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    hub = relationship("Hub", back_populates="events")

    def __repr__(self):
        return f"<HubEvent(hub_id={self.hub_id}, type={self.event_type}, time={self.timestamp})>"


class User(Base):
    """Users in JupyterCluster (admins and regular users)"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    admin = Column(Boolean, default=False, nullable=False)

    # Namespace restrictions (optional - for limiting which namespaces users can deploy to)
    allowed_namespace_prefixes = Column(JSON, default=list)  # List of allowed namespace prefixes
    max_hubs = Column(Integer, default=None)  # Maximum number of hubs user can create

    created = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<User(name={self.name}, admin={self.admin})>"
