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
from traitlets import Dict as TraitDict
from traitlets import Integer, Unicode, default
from traitlets.config import LoggingConfigurable

logger = logging.getLogger(__name__)


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge *override* into *base*, returning a new dict.

    Dict values are merged recursively; all other types are replaced by the
    override value.  Neither input is mutated.
    """
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


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

    # Allow namespace creation
    allow_namespace_creation = Unicode(
        "true",
        help="Whether to allow creating new namespaces (true/false). Set via JUPYTERCLUSTER_ALLOW_NAMESPACE_CREATION env var.",
    ).tag(config=True)

    def _get_allow_namespace_creation(self) -> bool:
        """Get allow_namespace_creation as boolean"""
        import os

        # Check environment variable first
        env_value = os.environ.get("JUPYTERCLUSTER_ALLOW_NAMESPACE_CREATION", None)
        if env_value is not None:
            return env_value.lower() in ("true", "1", "yes")
        # Fall back to config value
        return str(self.allow_namespace_creation).lower() in ("true", "1", "yes")

    # Security: Allowed Helm value keys (whitelist)
    allowed_helm_keys = TraitDict(
        {
            "hub": True,
            "proxy": True,
            "singleuser": True,
            "auth": True,
            "rbac": True,
            "ingress": True,
            "httpRoute": True,  # Allow but we'll disable if Gateway API not available
            "scheduling": True,
            "prePuller": True,
            "cull": True,
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
            self.storage_v1 = client.StorageV1Api()
        except Exception as e:
            self.log.error(f"Failed to initialize Kubernetes client: {e}")
            raise

    async def _check_storage_class_exists(self, storage_class: str) -> bool:
        """Check if a storage class exists in the cluster"""
        try:
            loop = asyncio.get_event_loop()
            storage_classes = await loop.run_in_executor(
                None, self.storage_v1.list_storage_class
            )
            for sc in storage_classes.items:
                if sc.metadata.name == storage_class:
                    return True
            return False
        except Exception as e:
            self.log.warning(f"Failed to check storage class {storage_class}: {e}")
            return False

    async def _check_node_labels_exist(self, required_labels: Dict[str, any]) -> bool:
        """Check if nodes have the required labels

        Args:
            required_labels: Dict of label key -> value or list of values

        Returns:
            True if at least one node has all required labels
        """
        try:
            loop = asyncio.get_event_loop()
            nodes = await loop.run_in_executor(None, self.core_v1.list_node)
            for node in nodes.items:
                node_labels = node.metadata.labels or {}
                # Check if any node has all required labels
                has_all = True
                for key, value in required_labels.items():
                    if key not in node_labels:
                        has_all = False
                        break
                    if isinstance(value, list):
                        if node_labels[key] not in value:
                            has_all = False
                            break
                    else:
                        if node_labels[key] != value:
                            has_all = False
                            break
                if has_all:
                    return True
            return False
        except Exception as e:
            self.log.warning(f"Failed to check node labels: {e}")
            return False

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

        # CRITICAL: Remove any namespace from values
        # Namespace is controlled via --namespace flag in Helm command, not in values
        # The JupyterHub Helm chart doesn't accept namespace in values
        if "namespace" in sanitized:
            self.log.warning(
                "Removed user-provided namespace from values (namespace is set via Helm --namespace flag)"
            )
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

                # Fix storage.extraVolumes and extraVolumeMounts - convert empty maps to empty lists
                if "storage" in singleuser:
                    storage = singleuser["storage"]
                    if isinstance(storage, dict):
                        if (
                            "extraVolumes" in storage
                            and isinstance(storage["extraVolumes"], dict)
                            and len(storage["extraVolumes"]) == 0
                        ):
                            self.log.warning("Converting empty extraVolumes map to empty list")
                            storage["extraVolumes"] = []
                        if (
                            "extraVolumeMounts" in storage
                            and isinstance(storage["extraVolumeMounts"], dict)
                            and len(storage["extraVolumeMounts"]) == 0
                        ):
                            self.log.warning("Converting empty extraVolumeMounts map to empty list")
                            storage["extraVolumeMounts"] = []

                        # Storage class validation is done async in _deploy_helm_release

                # Node affinity label validation is done async in _deploy_helm_release

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

        # Start with global defaults from JUPYTERCLUSTER_DEFAULT_HUB_VALUES env var.
        # These are applied first so that hub-specific values can override them.
        import os as _os

        merged_values: Dict = {}
        _global_defaults_raw = _os.environ.get("JUPYTERCLUSTER_DEFAULT_HUB_VALUES")
        if _global_defaults_raw:
            try:
                merged_values = json.loads(_global_defaults_raw)
            except json.JSONDecodeError:
                self.log.warning(
                    "Could not parse JUPYTERCLUSTER_DEFAULT_HUB_VALUES — skipping global defaults"
                )

        # Apply per-hub default values on top of global defaults (deep merge preserves
        # nested keys such as hub.resources that would be wiped by a shallow update)
        merged_values = _deep_merge(merged_values, self.default_values)

        if values:
            # CRITICAL: Validate and sanitize user-provided values
            sanitized_values = self._validate_helm_values(values)
            merged_values = _deep_merge(merged_values, sanitized_values)

        # Apply schema-defined fixed values last so they always win (server-side enforcement)
        _schema_raw = _os.environ.get("JUPYTERCLUSTER_HUB_VALUES_SCHEMA")
        if _schema_raw:
            try:
                _schema = json.loads(_schema_raw)
                for _path, _val in (_schema.get("fixed") or {}).items():
                    _parts = _path.split(".")
                    _cur = merged_values
                    for _p in _parts[:-1]:
                        _cur = _cur.setdefault(_p, {})
                    _cur[_parts[-1]] = _val
            except (json.JSONDecodeError, AttributeError):
                pass

        # Ensure required schema fields are present (JupyterHub Helm chart requires these)
        # These must be present even if empty to satisfy schema validation
        required_fields = {"hub": {}, "proxy": {}, "singleuser": {}, "ingress": {}}
        for field, default_value in required_fields.items():
            if field not in merged_values:
                merged_values[field] = default_value
            elif not isinstance(merged_values[field], dict):
                # If it's not a dict, make it a dict (shouldn't happen, but be safe)
                self.log.warning(f"Field '{field}' is not a dict, converting to dict")
                merged_values[field] = default_value

        # Auto-enable ingress/httpRoute when hosts are configured but enabled flag is missing.
        # This handles hubs created before the hostname field was added, and guards against the
        # form omitting the enabled flag (which would silently skip Ingress/HTTPRoute creation).
        _ingress = merged_values.get("ingress") or {}
        if isinstance(_ingress, dict) and _ingress.get("hosts"):
            _ingress["enabled"] = True
            merged_values["ingress"] = _ingress
        _http_route = merged_values.get("httpRoute") or {}
        if isinstance(_http_route, dict) and _http_route.get("hostnames"):
            _http_route["enabled"] = True
            merged_values["httpRoute"] = _http_route

        # CRITICAL: Do NOT add namespace to values - it's set via --namespace flag in Helm command
        # The JupyterHub Helm chart doesn't accept namespace in values, and we control
        # the namespace via the --namespace flag to ensure users cannot deploy to other namespaces
        # Remove namespace from values if user somehow provided it
        merged_values.pop("namespace", None)

        # Deploy using Helm (namespace is set via --namespace flag, not in values)
        await self._deploy_helm_release(merged_values)

        # Determine URL: prefer the hostname already configured in ingress/httpRoute
        # rather than discovering it from K8s (which has timing/availability issues).
        url = self._url_from_values(merged_values)
        if not url:
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
        loop = asyncio.get_event_loop()
        try:
            # Check if namespace exists
            try:
                await loop.run_in_executor(
                    None, lambda: self.core_v1.read_namespace(name=self.namespace)
                )
            except ApiException as e:
                if e.status == 404:
                    return 1  # Namespace doesn't exist, hub is stopped
                raise

            # Check if Helm release exists by checking for hub pods
            pods = await loop.run_in_executor(
                None, lambda: self.core_v1.list_namespaced_pod(namespace=self.namespace)
            )
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
        loop = asyncio.get_event_loop()
        # Check if namespace creation is allowed
        if not self._get_allow_namespace_creation():
            # Just verify namespace exists, don't create it
            try:
                await loop.run_in_executor(
                    None, lambda: self.core_v1.read_namespace(name=self.namespace)
                )
                self.log.info(f"Namespace {self.namespace} exists (namespace creation disabled)")
                return
            except ApiException as e:
                if e.status == 404:
                    raise RuntimeError(
                        f"Namespace {self.namespace} does not exist and namespace creation is disabled. "
                        f"Please create the namespace manually or enable namespace creation in JupyterCluster configuration."
                    )
                raise

        # Namespace creation is allowed - proceed with creation logic
        try:
            ns = await loop.run_in_executor(
                None, lambda: self.core_v1.read_namespace(name=self.namespace)
            )
            # Update labels to ensure ownership
            ns.metadata.labels.update(
                {
                    "jupytercluster.io/managed": "true",
                    "jupytercluster.io/hub": self.hub_name,
                    "jupytercluster.io/owner": self.owner,
                }
            )
            await loop.run_in_executor(
                None, lambda: self.core_v1.patch_namespace(name=self.namespace, body=ns)
            )
            self.log.debug(f"Namespace {self.namespace} already exists, updated labels")
        except ApiException as e:
            if e.status == 404:
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
                await loop.run_in_executor(
                    None, lambda: self.core_v1.create_namespace(body=namespace_body)
                )
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

        # Final sanitization: Convert empty maps to lists and validate resources
        # This must happen right before Helm deployment to catch any values that bypassed validation

        # Ensure required schema fields are present (JupyterHub Helm chart requires these)
        required_fields = {"hub": {}, "proxy": {}, "singleuser": {}, "ingress": {}}
        for field, default_value in required_fields.items():
            if field not in values:
                values[field] = default_value
            elif not isinstance(values[field], dict):
                self.log.warning(f"Field '{field}' is not a dict, converting to dict")
                values[field] = default_value

        if "singleuser" in values and isinstance(values["singleuser"], dict):
            if "storage" in values["singleuser"] and isinstance(
                values["singleuser"]["storage"], dict
            ):
                storage = values["singleuser"]["storage"]
                if (
                    "extraVolumes" in storage
                    and isinstance(storage["extraVolumes"], dict)
                    and len(storage["extraVolumes"]) == 0
                ):
                    self.log.warning(
                        "Converting empty extraVolumes map to empty list (final sanitization)"
                    )
                    storage["extraVolumes"] = []
                if (
                    "extraVolumeMounts" in storage
                    and isinstance(storage["extraVolumeMounts"], dict)
                    and len(storage["extraVolumeMounts"]) == 0
                ):
                    self.log.warning(
                        "Converting empty extraVolumeMounts map to empty list (final sanitization)"
                    )
                    storage["extraVolumeMounts"] = []

                # Final check: Validate storage class exists
                if "dynamic" in storage and isinstance(storage["dynamic"], dict):
                    storage_class = storage["dynamic"].get("storageClass")
                    if storage_class and not await self._check_storage_class_exists(storage_class):
                        self.log.warning(
                            f"Storage class '{storage_class}' not found in final check, using 'standard'"
                        )
                        storage["dynamic"]["storageClass"] = "standard"

        # Final check: Validate DB storage class
        if "db" in values and isinstance(values["db"], dict):
            if "pvc" in values["db"] and isinstance(values["db"]["pvc"], dict):
                db_storage_class = values["db"]["pvc"].get("storageClassName")
                if db_storage_class and not await self._check_storage_class_exists(db_storage_class):
                    self.log.warning(
                        f"DB storage class '{db_storage_class}' not found in final check, using 'standard'"
                    )
                    values["db"]["pvc"]["storageClassName"] = "standard"

        # Final check: Validate node affinity
        if "singleuser" in values and isinstance(values["singleuser"], dict):
            if "extraNodeAffinity" in values["singleuser"] and isinstance(
                values["singleuser"]["extraNodeAffinity"], dict
            ):
                required = values["singleuser"]["extraNodeAffinity"].get("required", [])
                if required:
                    required_labels = {}
                    for match_expressions in required:
                        if (
                            isinstance(match_expressions, dict)
                            and "matchExpressions" in match_expressions
                        ):
                            for expr in match_expressions["matchExpressions"]:
                                if isinstance(expr, dict) and expr.get("operator") == "In":
                                    key = expr.get("key")
                                    values_list = expr.get("values", [])
                                    if key and values_list:
                                        required_labels[key] = values_list

                    if required_labels and not await self._check_node_labels_exist(required_labels):
                        self.log.warning(
                            f"Node labels {required_labels} not found in final check, removing node affinity"
                        )
                        values["singleuser"]["extraNodeAffinity"] = {}

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
                "--cleanup-on-fail",  # Roll back newly created resources if upgrade fails
                self.helm_release_name,
                self.helm_chart,
                "--namespace",
                self.namespace,
                "--values",
                values_file,
            ]

            # Only ask Helm to create the namespace when the service account
            # has permission to do so.  When namespace creation is disabled,
            # the namespace must already exist.
            if self._get_allow_namespace_creation():
                cmd.insert(cmd.index("--values"), "--create-namespace")

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

                # Recover from a release stuck in "pending-install" state.
                # This happens when a previous install was interrupted before
                # Helm could record a successful revision.
                if "release: already exists" in error_msg or (
                    "cannot re-use a name that is still in use" in error_msg
                ):
                    self.log.warning(
                        f"Release {self.helm_release_name} is stuck in pending state — "
                        "attempting rollback then retry."
                    )
                    # Try rollback first (cheaper than uninstall)
                    rollback = await asyncio.create_subprocess_exec(
                        "helm",
                        "rollback",
                        self.helm_release_name,
                        "--namespace",
                        self.namespace,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await rollback.communicate()
                    if rollback.returncode != 0:
                        # No prior revision to roll back to — uninstall instead
                        self.log.warning(f"Rollback failed, uninstalling {self.helm_release_name}…")
                        uninstall = await asyncio.create_subprocess_exec(
                            "helm",
                            "uninstall",
                            self.helm_release_name,
                            "--namespace",
                            self.namespace,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await uninstall.communicate()

                    # Retry deployment
                    self.log.info("Retrying Helm deployment after recovery…")
                    retry = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    r_stdout, r_stderr = await retry.communicate()
                    if retry.returncode != 0:
                        error_msg = r_stderr.decode() if r_stderr else r_stdout.decode()
                        raise RuntimeError(f"Helm deployment failed after recovery: {error_msg}")
                    self.log.info(
                        f"Helm release {self.helm_release_name} deployed successfully after recovery"
                    )
                    return

                raise RuntimeError(f"Helm deployment failed: {error_msg}")

            self.log.info(f"Helm release {self.helm_release_name} deployed successfully")

        finally:
            # Clean up temp file
            Path(values_file).unlink(missing_ok=True)

    async def _delete_namespace(self, namespace: str):
        """Delete a Kubernetes namespace.

        Only called when ``allowNamespaceDeletion`` is enabled in config.
        The namespace must be managed by JupyterCluster (has the managed label).
        """
        loop = asyncio.get_event_loop()
        try:
            ns = await loop.run_in_executor(
                None, lambda: self.core_v1.read_namespace(name=namespace)
            )
            labels = ns.metadata.labels or {}
            if labels.get("jupytercluster.io/managed") != "true":
                self.log.warning(
                    f"Namespace {namespace} is not managed by JupyterCluster — skipping deletion"
                )
                return
            await loop.run_in_executor(
                None, lambda: self.core_v1.delete_namespace(name=namespace)
            )
            self.log.info(f"Deleted namespace {namespace}")
        except ApiException as e:
            if e.status == 404:
                self.log.debug(f"Namespace {namespace} already gone")
            else:
                raise

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

    def _url_from_values(self, values: Dict) -> str:
        """Return the hub URL derived from ingress/httpRoute config in *values*, or ''."""
        ingress = values.get("ingress") or {}
        if ingress.get("enabled"):
            hosts = ingress.get("hosts") or []
            host = hosts[0] if isinstance(hosts, list) and hosts else None
            if host and isinstance(host, str):
                return f"https://{host}"

        http_route = values.get("httpRoute") or {}
        if http_route.get("enabled"):
            hostnames = http_route.get("hostnames") or []
            host = hostnames[0] if isinstance(hostnames, list) and hostnames else None
            if host and isinstance(host, str):
                return f"https://{host}"

        return ""

    async def _wait_for_hub_ready(self) -> str:
        """Wait for hub to be ready and return its URL"""
        self.log.info(f"Waiting for hub {self.hub_name} to be ready...")

        # Wait for proxy service to be ready
        max_wait = self.start_timeout
        wait_interval = 5
        elapsed = 0

        while elapsed < max_wait:
            try:
                loop = asyncio.get_event_loop()
                # Check for proxy service
                services = await loop.run_in_executor(
                    None, lambda: self.core_v1.list_namespaced_service(namespace=self.namespace)
                )
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
                        loop2 = asyncio.get_event_loop()
                        ingresses = await loop2.run_in_executor(
                            None, lambda: net_v1.list_namespaced_ingress(namespace=self.namespace)
                        )
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
        self.log.warning(f"Hub not ready after {max_wait}s, returning placeholder URL")
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
