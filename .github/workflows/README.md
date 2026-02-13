# GitHub Actions Workflows

This directory contains GitHub Actions workflows for CI/CD.

## Workflows

### `ci.yml` - Continuous Integration
- **Triggers**: Push to main/develop, Pull Requests
- **Jobs**:
  - Lint: Code style checking (flake8, black, isort)
  - Test: Unit tests across Python 3.8-3.11
  - Build Docker: Build and push Docker images
  - Test Docker: Test Docker image functionality
  - Helm Lint: Validate Helm chart
  - Security Scan: Trivy vulnerability scanning
  - Integration Test: Full integration tests with kind cluster

### `pr.yml` - Pull Request Checks
- **Triggers**: PR opened, updated, reopened
- **Jobs**:
  - PR Checks: Run tests and code style checks
  - Build PR Image: Build Docker image for PR
  - Comment PR: Post status comment on PR

### `release.yml` - Release Workflow
- **Triggers**: Tag push (v*), Manual dispatch
- **Jobs**:
  - Release: Build package, Docker image, create GitHub release

### `branch-protection.yml` - Branch Protection
- **Triggers**: Push to main/develop
- **Jobs**:
  - Validate Branch: Check branch naming and commit messages
  - Enforce Standards: Run all tests and coverage checks

### `codeql.yml` - CodeQL Security Analysis
- **Triggers**: Push, PR, Weekly schedule
- **Jobs**:
  - Analyze: CodeQL security analysis

## Secrets Required

- `GITHUB_TOKEN` - Automatically provided by GitHub Actions
- Container registry credentials (if using external registry)

## Environment Variables

- `PYTHON_VERSION`: Python version for testing (default: 3.11)
- `REGISTRY`: Container registry (default: ghcr.io)
- `IMAGE_NAME`: Docker image name (default: repository name)
