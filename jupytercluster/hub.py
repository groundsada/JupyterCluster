"""Hub object - represents a JupyterHub instance managed by JupyterCluster"""

import logging
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session
from traitlets import Unicode, Dict as TraitDict, Instance
from traitlets.config import LoggingConfigurable

from .orm import Hub as ORMHub
from .spawner import HubSpawner

logger = logging.getLogger(__name__)


class HubInstance(LoggingConfigurable):
    """High-level wrapper around an ORM Hub object"""

    name = Unicode()
    namespace = Unicode()
    owner = Unicode()
    helm_release_name = Unicode()
    
    # Configuration
    helm_chart = Unicode("jupyterhub/jupyterhub")
    helm_chart_version = Unicode("")
    values = TraitDict()
    
    # Status
    status = Unicode("pending")  # pending, running, stopped, error
    url = Unicode("")
    
    # Metadata
    created = Instance(datetime)
    last_activity = Instance(datetime)
    description = Unicode("")

    def __init__(self, orm_hub: ORMHub, spawner_class=HubSpawner, **kwargs):
        """Initialize HubInstance from ORM Hub

        Args:
            orm_hub: Database model for the hub
            spawner_class: Class to use for spawning
        """
        super().__init__(**kwargs)
        self.orm_hub = orm_hub
        self.spawner_class = spawner_class
        self.spawner = None

        # Load attributes from ORM
        self._load_from_orm()

    def _load_from_orm(self):
        """Load attributes from ORM object"""
        self.name = self.orm_hub.name
        self.namespace = self.orm_hub.namespace
        self.owner = self.orm_hub.owner
        self.helm_release_name = self.orm_hub.helm_release_name
        self.helm_chart = self.orm_hub.helm_chart
        self.helm_chart_version = self.orm_hub.helm_chart_version or ""
        self.values = self.orm_hub.values or {}
        self.status = self.orm_hub.status
        self.url = self.orm_hub.url or ""
        self.created = self.orm_hub.created
        self.last_activity = self.orm_hub.last_activity
        self.description = self.orm_hub.description or ""

    def get_spawner(self) -> HubSpawner:
        """Get or create spawner for this hub"""
        if self.spawner is None:
            self.spawner = self.spawner_class(
                hub_name=self.name,
                namespace=self.namespace,
                owner=self.owner,
                helm_chart=self.helm_chart,
                helm_chart_version=self.helm_chart_version,
                default_values=self.values,
            )
            # Load state if available
            state = {
                "namespace": self.namespace,
                "helm_release_name": self.helm_release_name,
            }
            self.spawner.load_state(state)
        return self.spawner

    async def start(self, values: Optional[Dict] = None):
        """Start the hub instance"""
        self.log.info(f"Starting hub {self.name}")

        # Merge values
        merged_values = {**self.values}
        if values:
            merged_values.update(values)

        # Update status
        self.status = "pending"
        self._save_to_orm()

        try:
            spawner = self.get_spawner()
            namespace, url = await spawner.start(values=merged_values)

            # Update status and URL
            self.status = "running"
            self.url = url
            self.last_activity = datetime.utcnow()
            self._save_to_orm()

            self.log.info(f"Hub {self.name} started successfully at {url}")
        except Exception as e:
            self.log.error(f"Failed to start hub {self.name}: {e}")
            self.status = "error"
            self._save_to_orm()
            raise

    async def stop(self):
        """Stop the hub instance"""
        self.log.info(f"Stopping hub {self.name}")

        try:
            spawner = self.get_spawner()
            await spawner.stop()

            self.status = "stopped"
            self._save_to_orm()

            self.log.info(f"Hub {self.name} stopped successfully")
        except Exception as e:
            self.log.error(f"Failed to stop hub {self.name}: {e}")
            self.status = "error"
            self._save_to_orm()
            raise

    async def poll(self) -> Optional[int]:
        """Check if hub is still running"""
        spawner = self.get_spawner()
        return await spawner.poll()

    def _save_to_orm(self):
        """Save current state to ORM object"""
        self.orm_hub.status = self.status
        self.orm_hub.url = self.url
        self.orm_hub.last_activity = self.last_activity
        self.orm_hub.values = self.values
        if self.description:
            self.orm_hub.description = self.description

    def to_dict(self) -> Dict:
        """Convert to dictionary for API responses"""
        return {
            "name": self.name,
            "namespace": self.namespace,
            "owner": self.owner,
            "helm_release_name": self.helm_release_name,
            "helm_chart": self.helm_chart,
            "helm_chart_version": self.helm_chart_version,
            "status": self.status,
            "url": self.url,
            "created": self.created.isoformat() if self.created else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "description": self.description,
        }

