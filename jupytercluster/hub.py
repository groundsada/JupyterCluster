"""Hub object - represents a JupyterHub instance managed by JupyterCluster"""

import logging
import traceback
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session
from traitlets import Dict as TraitDict
from traitlets import Instance, Unicode
from traitlets.config import LoggingConfigurable

from .orm import Hub as ORMHub
from .orm import HubEvent
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
    error_message = Unicode("")  # Last error message for debugging

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
        self.error_message = getattr(self.orm_hub, "error_message", None) or ""

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

        # CRITICAL: Validate and sanitize values before using them
        # This ensures httpRoute is disabled, extraVolumes/extraVolumeMounts are fixed, etc.
        spawner = self.get_spawner()
        merged_values = spawner._validate_helm_values(merged_values)

        # Update status
        self.status = "pending"
        self._save_to_orm()

        try:
            namespace, url = await spawner.start(values=merged_values)

            # Update status and URL
            self.status = "running"
            self.url = url
            self.last_activity = datetime.utcnow()
            self._save_to_orm()

            self.log.info(f"Hub {self.name} started successfully at {url}")
        except Exception as e:
            error_msg = str(e)
            error_traceback = traceback.format_exc()
            full_error = f"{error_msg}\n\nTraceback:\n{error_traceback}"

            self.log.error(f"Failed to start hub {self.name}: {e}")
            self.status = "error"
            self._save_to_orm()

            # Store error event in database
            self._log_error_event("start", full_error)

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
            error_msg = str(e)
            error_traceback = traceback.format_exc()
            full_error = f"{error_msg}\n\nTraceback:\n{error_traceback}"

            self.log.error(f"Failed to stop hub {self.name}: {e}")
            self.status = "error"
            self._save_to_orm()

            # Store error event in database
            self._log_error_event("stop", full_error)

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
        # Save error message if it exists
        if hasattr(self, "error_message"):
            if hasattr(self.orm_hub, "error_message"):
                self.orm_hub.error_message = self.error_message

    def _log_error_event(self, operation: str, error_message: str):
        """Log an error event - store in ORM object for later commit"""
        # Store error message on the hub's ORM object
        self.error_message = f"[{operation}] {error_message}"
        if hasattr(self.orm_hub, "error_message"):
            self.orm_hub.error_message = self.error_message

        # Also try to create a HubEvent if we can access the session
        # This is a best-effort - if session isn't available, error_message field will have it
        try:
            # Try to add event via relationship
            error_event = HubEvent(
                hub_id=self.orm_hub.id,
                event_type="error",
                message=f"{operation}: {error_message}",
                timestamp=datetime.utcnow(),
            )
            # Add to the relationship - will be committed when session commits
            self.orm_hub.events.append(error_event)
        except Exception as e:
            # If we can't add event, at least we have error_message
            self.log.warning(
                f"Could not create error event (error_message will still be stored): {e}"
            )

    def to_dict(self) -> Dict:
        """Convert to dictionary for API responses and templates"""
        return {
            "name": self.name,
            "namespace": self.namespace,
            "owner": self.owner,
            "helm_release_name": self.helm_release_name,
            "helm_chart": self.helm_chart,
            "helm_chart_version": self.helm_chart_version,
            "status": str(self.status),  # Ensure status is a string
            "url": self.url,
            "created": self.created.isoformat() if self.created else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "description": self.description,
            "values": self.values,  # Include values for template access
            "error_message": self.error_message,  # Include error message for debugging
        }
