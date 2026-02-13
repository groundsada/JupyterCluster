# JupyterCluster

JupyterCluster is a multi-hub management system that allows administrators and users to provision and manage multiple JupyterHub instances on Kubernetes. Similar to how JupyterHub manages single-user notebook servers, JupyterCluster manages entire JupyterHub deployments.

## Architecture

JupyterCluster follows a similar design pattern to JupyterHub:

- **Hub**: Central orchestrator that manages JupyterHub instances
- **HubSpawner**: Kubernetes-based spawner that creates JupyterHub Helm releases in dedicated namespaces
- **Authenticator**: Handles authentication and authorization
- **RBAC**: Role-based access control (admin can manage all hubs, users can manage their own)
- **REST API**: For managing hubs programmatically

## Key Features

- **Admin capabilities**: Create, edit, delete, and manage all JupyterHub instances
- **User capabilities**: Deploy and manage their own JupyterHub instances
- **Namespace isolation**: Each JupyterHub instance runs in its own Kubernetes namespace
- **Helm integration**: Uses Helm to deploy and manage JupyterHub instances
- **Kubernetes-native**: Built specifically for Kubernetes environments
- **OAuth Authentication**: Supports OAuth via [OAuthenticator](https://github.com/jupyterhub/oauthenticator) (GitHub, Google, GitLab, etc.)
- **Web UI**: Modern web interface following JupyterHub's design principles

## Components

### Core Components

1. **jupytercluster/hub/**: Main Hub application
2. **jupytercluster/spawner/**: HubSpawner for creating JupyterHub instances
3. **jupytercluster/auth/**: Authentication and authorization
4. **jupytercluster/api/**: REST API handlers
5. **jupytercluster/orm/**: Database models for hub management

### Kubernetes Components

- **helm/jupytercluster/**: Helm chart for deploying JupyterCluster
- **k8s/**: Kubernetes manifests and CRDs

## Quick Start

### Prerequisites

- Kubernetes cluster (>= 1.28.0)
- Helm 3.5+
- kubectl configured

### Installation

```bash
# Install JupyterCluster
helm install jupytercluster ./helm/jupytercluster

# Or using the Helm repository (when available)
helm repo add jupytercluster https://jupytercluster.github.io/helm-chart
helm install jupytercluster jupytercluster/jupytercluster
```

## Development

```bash
# Install dependencies
pip install -e .

# Run tests
pytest

# Start development server
python -m jupytercluster
```

## Authentication

JupyterCluster supports multiple authentication methods:

- **Simple Authenticator**: Username/password (for development)
- **OAuthenticator**: OAuth via GitHub, Google, GitLab, etc. (see [OAUTH_SETUP.md](OAUTH_SETUP.md))

Configure authentication in your config file:

```python
# For OAuth (GitHub example)
c.JupyterCluster.authenticator_class = "jupytercluster.auth.OAuthenticatorWrapper"
c.OAuthenticatorWrapper.oauthenticator_class = "oauthenticator.github.GitHubOAuthenticator"
c.GitHubOAuthenticator.client_id = "your-client-id"
c.GitHubOAuthenticator.client_secret = "your-client-secret"
```

## License

BSD 3-Clause License

