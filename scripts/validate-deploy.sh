#!/bin/bash
# Pre-deployment validation for codex-lb k8s manifest templates (in-repo).
# Usage: bash scripts/validate-deploy.sh
set -euo pipefail

DEPLOY_DIR="deploy/k8s"
ERRORS=0

echo "=== codex-lb deployment validation ==="

# 1. Check all required manifest files exist
REQUIRED_FILES="deployment.yaml service.yaml configmap.yaml hpa.yaml secret.example.yaml job-migrate.yaml pdb.yaml service-metrics.yaml servicemonitor.yaml"
for f in $REQUIRED_FILES; do
  if [[ ! -f "$DEPLOY_DIR/$f" ]]; then
    echo "  FAIL: Missing $DEPLOY_DIR/$f"
    ERRORS=$((ERRORS + 1))
  else
    echo "  OK: $DEPLOY_DIR/$f exists"
  fi
done

# 2. Validate YAML syntax (same file list as existence check)
DEPLOY_DIR="$DEPLOY_DIR" python3 -c "
import yaml, sys, os
deploy_dir = os.environ.get('DEPLOY_DIR', 'deploy/k8s')
files = ['deployment.yaml','service.yaml','configmap.yaml','hpa.yaml','service-metrics.yaml','servicemonitor.yaml','job-migrate.yaml','pdb.yaml','secret.example.yaml']
for f in files:
    path = os.path.join(deploy_dir, f)
    try:
        yaml.safe_load(open(path))
    except Exception as e:
        print(f'  FAIL: {f} — {e}')
        sys.exit(1)
print('  OK: All manifests are valid YAML')
" || ERRORS=$((ERRORS + 1))

# 3. Check no :latest tag in deployment
if grep -q ':latest' "$DEPLOY_DIR/deployment.yaml"; then
  echo "  FAIL: deployment.yaml contains :latest tag"
  ERRORS=$((ERRORS + 1))
else
  echo "  OK: No :latest tag in deployment"
fi

# 4. Check __IMAGE_TAG__ is present (needs substitution)
if grep -q '__IMAGE_TAG__' "$DEPLOY_DIR/deployment.yaml"; then
  echo "  WARN: deployment.yaml contains __IMAGE_TAG__ placeholder — substitute before applying"
fi

# 5. Check DATABASE_URL not in configmap
if grep -q 'DATABASE_URL' "$DEPLOY_DIR/configmap.yaml"; then
  echo "  FAIL: DATABASE_URL found in configmap (should be in Secret)"
  ERRORS=$((ERRORS + 1))
else
  echo "  OK: DATABASE_URL not in configmap"
fi

# 6. Check circuit breaker disabled
DEPLOY_DIR="$DEPLOY_DIR" python3 -c "
import yaml, os
deploy_dir = os.environ['DEPLOY_DIR']
d = yaml.safe_load(open(os.path.join(deploy_dir, 'configmap.yaml')))
if d['data'].get('CODEX_LB_CIRCUIT_BREAKER_ENABLED', 'false') == 'false':
    print('  OK: Circuit breaker disabled in configmap')
else:
    print('  WARN: Circuit breaker is ENABLED in configmap')
" 2>/dev/null || echo "  WARN: Could not check circuit breaker status"

echo ""
if [[ $ERRORS -eq 0 ]]; then
  echo "=== VALIDATION PASSED ==="
  exit 0
else
  echo "=== VALIDATION FAILED ($ERRORS errors) ==="
  exit 1
fi
