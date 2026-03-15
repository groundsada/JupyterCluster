"""Database models for JupyterCluster"""

import hashlib
import secrets
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Hub(Base):
    """Represents a JupyterHub instance managed by JupyterCluster"""

    __tablename__ = "hubs"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    namespace = Column(
        String(255), unique=True, nullable=False, index=True
    )  # UNIQUE: One hub per namespace
    owner = Column(
        String(255), nullable=False, index=True
    )  # Username of the owner - CRITICAL for permission checks
    helm_release_name = Column(String(255), unique=True, nullable=False)

    # Helm chart configuration
    helm_chart = Column(String(255), default="jupyterhub/jupyterhub")
    helm_chart_version = Column(String(50))
    values = Column(JSON, default=dict)  # Helm values override (sanitized before storage)

    # Status information
    status = Column(String(50), default="pending")  # pending, running, stopped, error
    url = Column(String(500))  # Access URL for the hub
    error_message = Column(Text)  # Last error message for debugging

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
    allowed_namespaces = Column(JSON, default=list)  # List of allowed namespace names (exact match)
    max_hubs = Column(Integer, default=None)  # Maximum number of hubs user can create

    # Per-user namespace management permissions (None = inherit global allowUserNamespaceManagement)
    can_create_namespaces = Column(Boolean, default=None)  # None = inherit global setting
    can_delete_namespaces = Column(Boolean, default=None)  # None = inherit global setting

    created = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)

    # API tokens belonging to this user
    tokens = relationship("APIToken", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(name={self.name}, admin={self.admin})>"


class APIToken(Base):
    """API authentication token.

    Mirrors JupyterHub's token model:
    - The raw token value is generated once and returned to the caller.
    - Only the SHA-256 hash is persisted; the raw value cannot be recovered.
    - ``prefix`` stores the first 4 characters of the raw token in plaintext
      to support "show my tokens" UIs without a full table scan.
    - Empty ``scopes`` list means the token inherits all of the owner's
      permissions (matching JupyterHub's default token behaviour).
    """

    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True)
    # SHA-256 hex digest of the raw token — never store plaintext
    hashed_token = Column(String(128), unique=True, nullable=False, index=True)
    # First 4 chars of the raw token (plaintext) for display / prefix lookup
    prefix = Column(String(16), nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255))  # Human-readable label, e.g. "ci-pipeline"
    scopes = Column(JSON, default=list)  # [] → inherit full user permissions
    note = Column(Text)  # Free-text description

    created = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # None → never expires
    last_activity = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="tokens")

    # ------------------------------------------------------------------
    # Factory — the only place raw tokens are ever created
    # ------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        user_id: int,
        name: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        expires_at: Optional[datetime] = None,
        note: Optional[str] = None,
    ) -> Tuple["APIToken", str]:
        """Create a new APIToken and return ``(orm_object, raw_token)``.

        The raw token is a 64-character hex string (256 bits of entropy).
        It is returned *once* and never stored.  Only the SHA-256 digest
        is persisted, following JupyterHub's token security model.

        Usage::

            token_orm, raw = APIToken.new(user_id=user.id, name="ci")
            db.add(token_orm)
            db.commit()
            # Send `raw` to the caller — it cannot be recovered later.
        """
        raw = secrets.token_hex(32)  # 64 hex chars, 256 bits
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        token = cls(
            hashed_token=hashed,
            prefix=raw[:4],
            user_id=user_id,
            name=name,
            scopes=scopes or [],
            expires_at=expires_at,
            note=note,
        )
        return token, raw

    @staticmethod
    def hash(raw: str) -> str:
        """Return the SHA-256 hex digest used for DB lookups."""
        return hashlib.sha256(raw.encode()).hexdigest()

    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at < datetime.utcnow()

    def to_dict(self, include_token: Optional[str] = None) -> dict:
        """Serialise to a JSON-safe dict.

        ``include_token`` should be passed only immediately after creation
        so that the raw value appears in the POST 201 response.
        """
        d = {
            "id": self.id,
            "name": self.name,
            "prefix": self.prefix,
            "user": self.user.name if self.user else None,
            "scopes": self.scopes or [],
            "note": self.note,
            "created": self.created.isoformat() if self.created else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }
        if include_token is not None:
            d["token"] = include_token
        return d

    def __repr__(self):
        return f"<APIToken(id={self.id}, user_id={self.user_id}, prefix={self.prefix!r})>"


class Config(Base):
    """Application configuration key-value store"""

    __tablename__ = "config"
    id = Column(Integer, primary_key=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)

    def __repr__(self):
        return f"<Config(key={self.key})>"
