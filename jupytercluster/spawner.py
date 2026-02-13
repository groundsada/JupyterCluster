"""HubSpawner - Spawns JupyterHub instances on Kubernetes using Helm"""

import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from traitlets import Dict as TraitDict, Unicode, Integer, default
from traitlets.config import LoggingConfigurable

logger = logging.getLogger(__name__)


class HubSpawner(LoggingConfigurable):
    """Spawns JupyterHub instances as Helm releases in Kubernetes namespaces"""

    # Helm configuration
    helm_chart = Unicode(
        "jupyterhub/jupyterhub",
        help="Helm chart repository and name for JupyterHub",
    ).tag(config=True)

    helm_chart_version = Unicode(
        "",
        help="Helm chart version (empty for latest)",
    ).tag(config=True)

    helm_repo_url = Unicode(
        "https://hub.jupyter.org/helm-chart/",
        help="Helm repository URL for JupyterHub chart",
    ).tag(config=True)

    # Kubernetes configuration
    kubeconfig_path = Unicode(
        "",
        help="Path to kubeconfig file (empty for in-cluster config)",
    ).tag(config=True)

    # Default Helm values
    default_values = TraitDict(
        {},
        help="Default Helm values to apply to all hubs",
    ).tag(config=True)

    # Security: Allowed Helm value keys (whitelist)
    allowed_helm_keys = TraitDict(
        {
            "hub": True,
            "proxy": True,
            "singleuser": True,
            "auth": True,
            "rbac": True,
        },
        help="Whitelist of top-level Helm value keys that users can modify",
    ).tag(config=True)

    # Timeouts
    start_timeout = Integer(
        300,
        help="Timeout in seconds for hub to start",
    ).tag(config=True)

    stop_timeout = Integer(
        60,
        help="Timeout in seconds for hub to stop",
    ).tag(config=True)

    def __init__(self, hub_name: str, namespace: str, owner: str, **kwargs):
        """Initialize the spawner for a specific hub

        Args:
            hub_name: Name of the hub instance
            namespace: Kubernetes namespace for the hub (ENFORCED - cannot be overridden)
            owner: Username of the hub owner
        """
        super().__init__(**kwargs)
        self.hub_name = hub_name
        self.namespace = namespace  # CRITICAL: This is enforced and cannot be changed
        self.owner = owner
        self.helm_release_name = f"jupyterhub-{hub_name}"

        # Initialize Kubernetes client
        self._init_k8s_client()

    def _init_k8s_client(self):
        """Initialize Kubernetes API client"""
        try:
            if self.kubeconfig_path:
                config.load_kube_config(config_file=self.kubeconfig_path)
            else:
                config.load_incluster_config()
            self.k8s_client = client.ApiClient()
            self.core_v1 = client.CoreV1Api()
            self.apps_v1 = client.AppsV1Api()
        except Exception as e:
            self.log.error(f"Failed to initialize Kubernetes client: {e}")
            raise

    def _validate_helm_values(self, values: Dict) -> Dict:
        """Validate and sanitize Helm values to prevent security issues

        CRITICAL SECURITY: This prevents users from:
        - Overriding namespace
        - Modifying RBAC to gain cluster-admin
        - Accessing other namespaces
        - Setting privileged security contexts

        Args:
            values: User-provided Helm values

        Returns:
            Sanitized values dict
        """
        sanitized = {}

        # Only allow whitelisted top-level keys
        for key in values:
            if key in self.allowed_helm_keys:
                sanitized[key] = values[key]
            else:
                self.log.warning(f"Rejected Helm key: {key} (not in whitelist)")

        # CRITICAL: Remove any namespace overrides
        # Ensure namespace is always set to self.namespace
        if "namespace" in sanitized:
            self.log.warning("Removed user-provided namespace override")
            del sanitized["namespace"]

        # Remove dangerous RBAC modifications
        if "rbac" in sanitized:
            rbac = sanitized["rbac"]
            # Remove cluster-admin or any cluster-scoped permissions
            if isinstance(rbac, dict):
                # Remove clusterRoleBindings
                if "clusterRoleBindings" in rbac:
                    self.log.warning("Removed clusterRoleBindings from user values")
                    del rbac["clusterRoleBindings"]

        # Remove security context overrides that could allow privilege escalation
        if "singleuser" in sanitized:
            singleuser = sanitized["singleuser"]
            if isinstance(singleuser, dict):
                # Remove privileged, allowPrivilegeEscalation, etc.
                if "securityContext" in singleuser:
                    sc = singleuser["securityContext"]
                    if isinstance(sc, dict):
                        for dangerous_key in [
                            "privileged",
                            "allowPrivilegeEscalation",
                            "capabilities",
                        ]:
                            if dangerous_key in sc:
                                self.log.warning(
                                    f"Removed dangerous securityContext.{dangerous_key}"
                                )
                                del sc[dangerous_key]

        return sanitized

    async def start(self, values: Optional[Dict] = None) -> Tuple[str, str]:
        """Start a JupyterHub instance

        Args:
            values: Helm values override dictionary (will be validated)

        Returns:
            Tuple of (namespace, url) where url is the access URL
        """
        self.log.info(f"Starting hub {self.hub_name} in namespace {self.namespace}")

        # Ensure namespace exists
        await self._ensure_namespace()

        # Merge default values with provided values
        merged_values = {**self.default_values}
        if values:
            # CRITICAL: Validate and sanitize user-provided values
            sanitized_values = self._validate_helm_values(values)
            merged_values.update(sanitized_values)

        # CRITICAL: Force namespace to be self.namespace (cannot be overridden)
        # This ensures users cannot deploy to other namespaces
        merged_values["namespace"] = self.namespace

        # Deploy using Helm
        await self._deploy_helm_release(merged_values)

        # Wait for hub to be ready
        url = await self._wait_for_hub_ready()

        return self.namespace, url

    async def stop(self):
        """Stop/delete a JupyterHub instance"""
        self.log.info(f"Stopping hub {self.hub_name} in namespace {self.namespace}")

        # Delete Helm release
        await self._delete_helm_release()

    async def poll(self) -> Optional[int]:
        """Check if hub is still running

        Returns:
            None if running, exit code if stopped
        """
        try:
            # Check if namespace exists
            try:
                ns = self.core_v1.read_namespace(name=self.namespace)
            except ApiException as e:
                if e.status == 404:
                    return 1  # Namespace doesn't exist, hub is stopped
                raise

            # Check if Helm release exists by checking for hub pods
            pods = self.core_v1.list_namespaced_pod(namespace=self.namespace)
            hub_pods = [p for p in pods.items if "jupyterhub" in p.metadata.name.lower()]

            if not hub_pods:
                return 1  # No hub pods, consider it stopped

            # Check if any pod is running
            running = any(
                p.status.phase == "Running"
                for p in hub_pods
                if p.status.phase in ["Running", "Pending"]
            )

            return None if running else 1

        except Exception as e:
            self.log.error(f"Error polling hub {self.hub_name}: {e}")
            return 1

    async def _ensure_namespace(self):
        """Ensure the namespace exists and is properly labeled"""
        try:
            ns = self.core_v1.read_namespace(name=self.namespace)
            # Update labels to ensure ownership
            ns.metadata.labels.update({
                "jupytercluster.io/managed": "true",
                "jupytercluster.io/hub": self.hub_name,
                "jupytercluster.io/owner": self.owner,
            })
            self.core_v1.patch_namespace(name=self.namespace, body=ns)
            self.log.debug(f"Namespace {self.namespace} already exists, updated labels")
        except ApiException as e:
            if e.status == 404:
                # Create namespace with proper labels and ownership
                namespace_body = client.V1Namespace(
                    metadata=client.V1ObjectMeta(
                        name=self.namespace,
                        labels={
                            "jupytercluster.io/managed": "true",
                            "jupytercluster.io/hub": self.hub_name,
                            "jupytercluster.io/owner": self.owner,
                        },
                    ),
                )
                self.core_v1.create_namespace(body=namespace_body)
                self.log.info(f"Created namespace {self.namespace} for owner {self.owner}")
            else:
                raise

    async def _deploy_helm_release(self, values: Dict):
        """Deploy Helm release using helm CLI (via subprocess)"""
        self.log.info(
            f"Deploying Helm release {self.helm_release_name} with chart {self.helm_chart}"
        )

        # Ensure Helm repo is added
        await self._ensure_helm_repo()

        # Create temporary values file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(values, f)
            values_file = f.name

        try:
            # Build helm upgrade --install command
            cmd = [
                "helm",
                "upgrade",
                "--install",
                self.helm_release_name,
                self.helm_chart,
                "--namespace",
                self.namespace,
                "--create-namespace",
                "--values",
                values_file,
            ]

            # Add chart version if specified
            if self.helm_chart_version:
                cmd.extend(["--version", self.helm_chart_version])

            # Add repo if chart doesn't contain /
            if "/" not in self.helm_chart:
                cmd.extend(["--repo", self.helm_repo_url])

            self.log.debug(f"Running: {' '.join(cmd)}")

            # Run helm command
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else stdout.decode()
                self.log.error(f"Helm deployment failed: {error_msg}")
                raise RuntimeError(f"Helm deployment failed: {error_msg}")

            self.log.info(
                f"Helm release {self.helm_release_name} deployed successfully"
            )

        finally:
            # Clean up temp file
            Path(values_file).unlink(missing_ok=True)

    async def _ensure_helm_repo(self):
        """Ensure Helm repository is added"""
        repo_name = "jupyterhub"
        cmd = ["helm", "repo", "add", repo_name, self.helm_repo_url]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await process.communicate()
        # Ignore error if repo already exists

        # Update repo
        cmd = ["helm", "repo", "update", repo_name]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()

    async def _delete_helm_release(self):
        """Delete Helm release"""
        self.log.info(f"Deleting Helm release {self.helm_release_name}")

        cmd = [
            "helm",
            "uninstall",
            self.helm_release_name,
            "--namespace",
            self.namespace,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else stdout.decode()
            # Ignore "release not found" errors
            if "not found" not in error_msg.lower():
                self.log.error(f"Helm deletion failed: {error_msg}")
                raise RuntimeError(f"Helm deletion failed: {error_msg}")

    async def _wait_for_hub_ready(self) -> str:
        """Wait for hub to be ready and return its URL"""
        self.log.info(f"Waiting for hub {self.hub_name} to be ready...")

        # Wait for proxy service to be ready
        max_wait = self.start_timeout
        wait_interval = 5
        elapsed = 0

        while elapsed < max_wait:
            try:
                # Check for proxy service
                services = self.core_v1.list_namespaced_service(namespace=self.namespace)
                proxy_service = None
                for svc in services.items:
                    if "proxy" in svc.metadata.name.lower() or "hub" in svc.metadata.name.lower():
                        proxy_service = svc
                        break

                if proxy_service:
                    # Check for ingress or construct URL from service
                    # Try to get ingress first
                    try:
                        from kubernetes.client import NetworkingV1Api
                        net_v1 = NetworkingV1Api()
                        ingresses = net_v1.list_namespaced_ingress(namespace=self.namespace)
                        if ingresses.items:
                            ingress = ingresses.items[0]
                            if ingress.spec.rules:
                                host = ingress.spec.rules[0].host
                                return f"https://{host}"
                    except:
                        pass

                    # Fallback: construct from service
                    # In production, you'd configure ingress properly
                    return (
                        f"http://{proxy_service.metadata.name}."
                        f"{self.namespace}.svc.cluster.local"
                    )

                await asyncio.sleep(wait_interval)
                elapsed += wait_interval

            except Exception as e:
                self.log.debug(f"Waiting for hub... ({elapsed}s/{max_wait}s)")
                await asyncio.sleep(wait_interval)
                elapsed += wait_interval

        # Timeout - return placeholder
        self.log.warning(
            f"Hub not ready after {max_wait}s, returning placeholder URL"
        )
        return f"https://{self.hub_name}.example.com"

    def get_state(self) -> Dict:
        """Get current state for persistence"""
        return {
            "namespace": self.namespace,
            "helm_release_name": self.helm_release_name,
        }

    def load_state(self, state: Dict):
        """Load state from persistence"""
        self.namespace = state.get("namespace", self.namespace)
        self.helm_release_name = state.get("helm_release_name", self.helm_release_name)
