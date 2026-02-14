#!/bin/bash
# Quick test script for mfsada-temp namespace

set -e

NAMESPACE="mfsada-temp"
RELEASE_NAME="jupytercluster"
IMAGE_REPO="${IMAGE_REPO:-jupytercluster}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "=== Step 1: Create namespace ==="
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

echo "=== Step 2: Add JupyterHub Helm repo ==="
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/ || true
helm repo update

echo "=== Step 3: Create values file ==="
cat > /tmp/values-mfsada-temp.yaml <<EOF
image:
  repository: ${IMAGE_REPO}
  tag: ${IMAGE_TAG}
  pullPolicy: IfNotPresent

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
  enabled: false

persistence:
  enabled: true
  storageClass: ""
  size: 5Gi

resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 200m
    memory: 512Mi
EOF

echo "=== Step 4: Deploy JupyterCluster ==="
helm upgrade --install $RELEASE_NAME ./helm/jupytercluster \
  --namespace $NAMESPACE \
  --values /tmp/values-mfsada-temp.yaml \
  --wait \
  --timeout 5m

echo "=== Step 5: Wait for pods to be ready ==="
kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=jupytercluster \
  -n $NAMESPACE \
  --timeout 300s

echo "=== Step 6: Check deployment status ==="
kubectl get all -n $NAMESPACE

echo "=== Step 7: Test health endpoint ==="
kubectl port-forward -n $NAMESPACE svc/$RELEASE_NAME 8080:80 &
PF_PID=$!
sleep 3

if curl -f http://localhost:8080/api/health > /dev/null 2>&1; then
  echo "✓ Health check passed!"
else
  echo "✗ Health check failed"
fi

kill $PF_PID 2>/dev/null || true

echo ""
echo "=== Deployment complete! ==="
echo ""
echo "To access JupyterCluster:"
echo "  kubectl port-forward -n $NAMESPACE svc/$RELEASE_NAME 8080:80"
echo ""
echo "Then open: http://localhost:8080"
echo ""
echo "Login credentials:"
echo "  Admin: admin / admin123"
echo "  User:  testuser / test123"
echo ""
echo "To view logs:"
echo "  kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=jupytercluster -f"
echo ""
echo "To uninstall:"
echo "  helm uninstall $RELEASE_NAME -n $NAMESPACE"

