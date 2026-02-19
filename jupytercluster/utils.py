"""Utility functions for JupyterCluster"""

import datetime
import json
import logging
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively convert date/datetime objects to ISO format strings for JSON serialization."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(elem) for elem in obj]
    return obj


def parse_config(config_str: str) -> Dict[str, Any]:
    """
    Parse configuration string as YAML or JSON.

    Args:
        config_str: Configuration string in YAML or JSON format

    Returns:
        Parsed configuration as a dictionary

    Raises:
        ValueError: If the string cannot be parsed as either YAML or JSON
    """
    if not config_str or not config_str.strip():
        return {}

    config_str = config_str.strip()

    # Try YAML first (YAML is a superset of JSON, so valid JSON is also valid YAML)
    try:
        parsed_config = yaml.safe_load(config_str) or {}
        return _sanitize_for_json(parsed_config)
    except yaml.YAMLError as e:
        # If YAML fails, try JSON
        try:
            parsed_config = json.loads(config_str)
            return _sanitize_for_json(parsed_config)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid YAML or JSON: {str(e)}")


def format_config(config: Dict[str, Any], format: str = "yaml") -> str:
    """
    Format configuration dictionary as YAML or JSON string.

    Args:
        config: Configuration dictionary
        format: Output format, either "yaml" or "json"

    Returns:
        Formatted configuration string
    """
    if format.lower() == "yaml":
        return yaml.safe_dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)
    else:
        return json.dumps(config, indent=2, sort_keys=False)


def get_minimal_hub_values() -> Dict[str, Any]:
    """
    Get minimal hub values that work in any Kubernetes cluster (Kind, production, etc.)

    This configuration:
    - Uses dummy authenticator (no auth setup needed)
    - Uses standard storage class (available in most clusters)
    - No node affinity (works on any node)
    - No ingress (access via port-forward or NodePort)
    - Minimal resources

    Returns:
        Dictionary of minimal Helm values
    """
    return {
        "hub": {
            "config": {"JupyterHub": {"authenticator_class": "dummy"}},
            "service": {"type": "ClusterIP"},
        },
        "singleuser": {
            "storage": {"type": "none"},  # No persistent storage
            "image": {"name": "quay.io/jupyter/scipy-notebook", "tag": "latest"},
            "cpu": {"limit": 1, "guarantee": 0.1},
            "memory": {"limit": "1G", "guarantee": "128M"},
        },
        "ingress": {"enabled": False},
        "proxy": {"service": {"type": "ClusterIP"}},
    }


def get_production_hub_values(
    storage_class: str = "rook-ceph-block",
    region: str = "us-west",
    ingress_class: str = "haproxy",
    ingress_host: str = None,
) -> Dict[str, Any]:
    """
    Get production-ready hub values with Ceph storage and region affinity.

    Args:
        storage_class: Storage class to use (default: rook-ceph-block)
        region: Kubernetes region label value (default: us-west)
        ingress_class: Ingress class name (default: haproxy)
        ingress_host: Ingress hostname (optional)

    Returns:
        Dictionary of production Helm values
    """
    values = {
        "hub": {
            "config": {
                "JupyterHub": {"authenticator_class": "dummy"}  # Should be overridden in production
            },
            "service": {"type": "ClusterIP"},
            "db": {
                "type": "sqlite-pvc",
                "pvc": {
                    "accessModes": ["ReadWriteOnce"],
                    "storage": "1Gi",
                    "storageClassName": storage_class,
                },
            },
            "resources": {
                "limits": {"cpu": "2", "memory": "1Gi"},
                "requests": {"cpu": "100m", "memory": "512Mi"},
            },
        },
        "singleuser": {
            "storage": {
                "type": "dynamic",
                "capacity": "5Gi",
                "dynamic": {
                    "storageClass": storage_class,
                    "pvcNameTemplate": "claim-{username}{servername}",
                    "volumeNameTemplate": "volume-{username}{servername}",
                    "storageAccessModes": ["ReadWriteOnce"],
                },
                "extraVolumes": [],
                "extraVolumeMounts": [],
            },
            "image": {"name": "quay.io/jupyter/scipy-notebook", "tag": "2024-04-22"},
            "cpu": {"limit": 3, "guarantee": 3},
            "memory": {"limit": "10G", "guarantee": "10G"},
            "extraNodeAffinity": {
                "required": [
                    {
                        "matchExpressions": [
                            {
                                "key": "topology.kubernetes.io/region",
                                "operator": "In",
                                "values": [region],
                            }
                        ]
                    }
                ]
            },
        },
        "ingress": {"enabled": True, "ingressClassName": ingress_class},
        "proxy": {"service": {"type": "ClusterIP"}},
    }

    if ingress_host:
        values["ingress"]["hosts"] = [ingress_host]

    return values
