# Testing JupyterCluster in Namespace: mfsada-temp

## Prerequisites

1. **Kubernetes cluster access** with permissions to:
   - Create namespaces
   - Deploy Helm charts
   - Create deployments, services, PVCs
   - Create RBAC resources (ServiceAccount, Role, RoleBinding)

2. **Tools installed**:
   - `kubectl` configured to access your cluster
   - `helm` v3.x
   - `docker` (for building the image locally, optional)

3. **Cluster requirements**:
   - Storage class available for PVCs
   - Ingress controller (optional, for external access)

## Step 1: Build and Push Docker Image

### Option A: Build locally and push to registry

```bash
# Build the image
docker build -t jupytercluster:latest .

# Tag for your registry (replace with your registry)
docker tag jupytercluster:latest <your-registry>/jupytercluster:latest

# Push to registry
docker push <your-registry>/jupytercluster:latest
```

### Option B: Use pre-built image from GitHub Container Registry

If you have the image from CI/CD:
```bash
# Pull from GHCR (if available)
docker pull ghcr.io/groundsada/jupytercluster:latest
```

## Step 2: Prepare Helm Chart Values

Create a values file for your namespace:

```bash
cat > values-mfsada-temp.yaml <<EOF
image:
  repository: jupytercluster  # or <your-registry>/jupytercluster
  tag: latest
  pullPolicy: IfNotPresent  # or Always if using registry

config:
  dbUrl: "sqlite:///data/jupytercluster.db"
  port: 8080
  defaultNamespacePrefix: "jupyterhub-"
  defaultHelmChart: "jupyterhub/jupyterhub"
  authenticator:
    class: "jupytercluster.auth.SimpleAuthenticator"
    users:
      admin: "admin123"
      testuser: "test123"
    adminUsers:
      admin: true

service:
  type: ClusterIP
  port: 80
  targetPort: 8080

ingress:
  enabled: true
  className: "nginx"  # or your ingress class
  hosts:
    - host: jupytercluster.mfsada-temp.example.com
      paths:
        - path: /
          pathType: Prefix

persistence:
  enabled: true
  storageClass: ""  # Use default storage class, or specify one
  size: 5Gi

resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 200m
    memory: 512Mi
EOF
```

## Step 3: Add JupyterHub Helm Repository

```bash
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/
helm repo update
```

## Step 4: Deploy JupyterCluster

```bash
# Create namespace (if it doesn't exist)
kubectl create namespace mfsada-temp

# Install JupyterCluster
helm install jupytercluster ./helm/jupytercluster \
  --namespace mfsada-temp \
  --values values-mfsada-temp.yaml \
  --wait \
  --timeout 5m
```

## Step 5: Verify Deployment

```bash
# Check all resources
kubectl get all -n mfsada-temp

# Check pods
kubectl get pods -n mfsada-temp

# Check pod logs
kubectl logs -n mfsada-temp -l app.kubernetes.io/name=jupytercluster

# Check service
kubectl get svc -n mfsada-temp

# Check ingress (if enabled)
kubectl get ingress -n mfsada-temp
```

## Step 6: Access the Application

### Option A: Port Forward

```bash
# Forward local port 8080 to service
kubectl port-forward -n mfsada-temp svc/jupytercluster 8080:80

# Access at http://localhost:8080
```

### Option B: Via Ingress

If ingress is enabled, access via the configured hostname:
```bash
# Add to /etc/hosts if using local cluster
# <ingress-ip> jupytercluster.mfsada-temp.example.com

# Access at http://jupytercluster.mfsada-temp.example.com
```

## Step 7: Test Basic Functionality

### 7.1 Login

1. Open the application in browser
2. Login with credentials from values file:
   - Username: `admin` / Password: `admin123`
   - Username: `testuser` / Password: `test123`

### 7.2 Create a Hub

1. Click "Create Hub" or navigate to `/hubs/create`
2. Fill in:
   - **Hub Name**: `test-hub-1`
   - **Description**: `Test hub for validation`
   - **Helm Values** (optional JSON):
     ```json
     {
       "hub": {
         "config": {
           "JupyterHub": {
             "authenticator_class": "dummy"
           }
         }
       }
     }
     ```
3. Click "Create"

### 7.3 Verify Hub Deployment

```bash
# Check if namespace was created
kubectl get namespace | grep jupyterhub-test-hub-1

# Check hub pods
kubectl get pods -n jupyterhub-test-hub-1

# Check Helm release
helm list -n jupyterhub-test-hub-1
```

### 7.4 Test API

```bash
# Get auth token (if using API)
# For now, test via port-forward with cookies

# Health check
curl http://localhost:8080/api/health

# List hubs (requires authentication)
curl -H "X-User: admin" -H "X-Admin: true" http://localhost:8080/api/hubs
```

## Step 8: Test Hub Management

### Start a Hub

1. Go to hub detail page: `/hubs/test-hub-1`
2. Click "Start" button
3. Wait for hub to be ready
4. Verify in Kubernetes:
   ```bash
   kubectl get pods -n jupyterhub-test-hub-1
   ```

### Stop a Hub

1. Go to hub detail page
2. Click "Stop" button
3. Verify pods are terminated:
   ```bash
   kubectl get pods -n jupyterhub-test-hub-1
   ```

### Delete a Hub

1. Go to hub detail page
2. Click "Delete" button
3. Verify namespace is removed:
   ```bash
   kubectl get namespace jupyterhub-test-hub-1
   # Should return: Error from server (NotFound)
   ```

## Step 9: Test Multi-User Scenarios

### Create Multiple Hubs

1. Create hub as `testuser`: `testuser-hub-1`
2. Create hub as `admin`: `admin-hub-1`
3. Verify:
   - `testuser` can only see their own hub
   - `admin` can see all hubs

### Test Namespace Isolation

```bash
# Verify each hub has its own namespace
kubectl get namespace | grep jupyterhub-

# Verify pods are isolated
kubectl get pods -n jupyterhub-testuser-hub-1
kubectl get pods -n jupyterhub-admin-hub-1
```

## Step 10: Test Security Features

### Test Helm Values Validation

Try creating a hub with malicious values:

```json
{
  "namespace": "kube-system",
  "rbac": {
    "clusterRoleBindings": [{"name": "evil-binding"}]
  }
}
```

Verify that:
- Namespace override is rejected
- Dangerous RBAC is stripped
- Hub is created in correct namespace

### Test Permission Checks

1. Login as `testuser`
2. Try to access `/hubs/admin-hub-1`
3. Should get 403 Forbidden

## Step 11: Monitor and Debug

### Check Logs

```bash
# JupyterCluster logs
kubectl logs -n mfsada-temp -l app.kubernetes.io/name=jupytercluster -f

# Hub logs (if hub is running)
kubectl logs -n jupyterhub-test-hub-1 -l app=jupyterhub -f
```

### Check Database

```bash
# Access the pod
kubectl exec -n mfsada-temp -it deployment/jupytercluster -- /bin/bash

# Check database (if SQLite)
sqlite3 /data/jupytercluster.db "SELECT * FROM hubs;"
sqlite3 /data/jupytercluster.db "SELECT * FROM users;"
```

### Check Events

```bash
# Namespace events
kubectl get events -n mfsada-temp --sort-by='.lastTimestamp'

# Hub namespace events
kubectl get events -n jupyterhub-test-hub-1 --sort-by='.lastTimestamp'
```

## Step 12: Cleanup

### Delete a Specific Hub

```bash
# Via UI or API
# Or manually:
helm uninstall jupyterhub-test-hub-1 -n jupyterhub-test-hub-1
kubectl delete namespace jupyterhub-test-hub-1
```

### Uninstall JupyterCluster

```bash
# Uninstall Helm release
helm uninstall jupytercluster -n mfsada-temp

# Delete PVC (if you want to remove data)
kubectl delete pvc -n mfsada-temp -l app.kubernetes.io/name=jupytercluster

# Delete namespace (optional)
kubectl delete namespace mfsada-temp
```

## Troubleshooting

### Pod Not Starting

```bash
# Check pod status
kubectl describe pod -n mfsada-temp -l app.kubernetes.io/name=jupytercluster

# Check events
kubectl get events -n mfsada-temp

# Check logs
kubectl logs -n mfsada-temp -l app.kubernetes.io/name=jupytercluster
```

### Image Pull Errors

```bash
# Verify image exists
docker pull <your-registry>/jupytercluster:latest

# Check image pull policy
kubectl get deployment -n mfsada-temp jupytercluster -o yaml | grep imagePullPolicy
```

### PVC Issues

```bash
# Check PVC status
kubectl get pvc -n mfsada-temp

# Check storage class
kubectl get storageclass

# Describe PVC
kubectl describe pvc -n mfsada-temp -l app.kubernetes.io/name=jupytercluster
```

### Hub Creation Fails

```bash
# Check JupyterCluster logs
kubectl logs -n mfsada-temp -l app.kubernetes.io/name=jupytercluster

# Verify Helm is available in pod
kubectl exec -n mfsada-temp deployment/jupytercluster -- helm version

# Check RBAC permissions
kubectl describe role -n mfsada-temp
kubectl describe rolebinding -n mfsada-temp
```

## Quick Test Script

Save this as `quick-test.sh`:

```bash
#!/bin/bash
set -e

NAMESPACE="mfsada-temp"
RELEASE_NAME="jupytercluster"

echo "=== Deploying JupyterCluster ==="
helm install $RELEASE_NAME ./helm/jupytercluster \
  --namespace $NAMESPACE \
  --create-namespace \
  --wait \
  --timeout 5m

echo "=== Waiting for pods ==="
kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=jupytercluster \
  -n $NAMESPACE \
  --timeout 300s

echo "=== Setting up port-forward ==="
kubectl port-forward -n $NAMESPACE svc/$RELEASE_NAME 8080:80 &
PF_PID=$!
sleep 3

echo "=== Testing health endpoint ==="
curl -f http://localhost:8080/api/health || echo "Health check failed"

echo "=== Cleaning up port-forward ==="
kill $PF_PID || true

echo "=== Test complete! ==="
echo "Access at: http://localhost:8080 (run: kubectl port-forward -n $NAMESPACE svc/$RELEASE_NAME 8080:80)"
```

Make it executable and run:
```bash
chmod +x quick-test.sh
./quick-test.sh
```

