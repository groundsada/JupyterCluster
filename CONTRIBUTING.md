# Contributing to JupyterCluster

Thank you for your interest in contributing to JupyterCluster!

## Development Setup

1. Clone the repository:
```bash
git clone https://github.com/jupytercluster/jupytercluster.git
cd jupytercluster
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -e ".[dev]"
```

4. Run tests:
```bash
pytest
```

## Project Structure

```
jupytercluster/
├── jupytercluster/          # Main package
│   ├── __init__.py
│   ├── app.py               # Main application
│   ├── hub.py               # Hub instance management
│   ├── spawner.py           # HubSpawner for Kubernetes
│   ├── auth.py              # Authentication & authorization
│   ├── orm.py               # Database models
│   └── api/                 # REST API handlers
│       ├── base.py
│       └── hubs.py
├── helm/                    # Helm chart
├── k8s/                     # Kubernetes manifests
├── tests/                   # Test suite
├── docs/                    # Documentation
└── README.md
```

## Code Style

- Follow PEP 8
- Use Black for formatting (line length: 100)
- Type hints where appropriate
- Docstrings for all public functions/classes

## Testing

- Write tests for new features
- Ensure all tests pass before submitting PR
- Aim for good test coverage

## Submitting Changes

1. Create a feature branch
2. Make your changes
3. Add tests
4. Ensure tests pass
5. Submit a pull request

## Areas for Contribution

- Helm integration implementation
- Additional authenticators
- Web UI
- Documentation
- Testing
- Performance improvements

