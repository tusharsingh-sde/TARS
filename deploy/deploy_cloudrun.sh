#!/usr/bin/env bash
# Deploy TARS Backend and Frontend to Google Cloud Run with WebSocket affinity.
set -euo pipefail

REGION="${REGION:-asia-south1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"

if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  echo "Error: No GCP project set. Set PROJECT_ID=<id> or run 'gcloud config set project <id>'." >&2
  exit 1
fi

echo "==> Deploying TARS to Project: $PROJECT_ID (Region: $REGION)"

# 1. Secret Manager check for Gemini API Key
echo "[1/5] Checking Google Cloud Secret Manager for tars-gemini-api-key..."
if ! gcloud secrets describe tars-gemini-api-key --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "Secret tars-gemini-api-key not found. Enter your Gemini API key:"
  read -r -s GEMINI_KEY
  echo -n "$GEMINI_KEY" | gcloud secrets create tars-gemini-api-key --data-file=- --project="$PROJECT_ID"
fi

# 2. Build backend container
echo "[2/5] Building backend image..."
BACKEND_IMAGE="gcr.io/$PROJECT_ID/tars-backend:latest"
gcloud builds submit --project="$PROJECT_ID" --tag "$BACKEND_IMAGE" ./backend

# 3. Deploy backend to Cloud Run
echo "[3/5] Deploying tars-backend..."
gcloud run deploy tars-backend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --image="$BACKEND_IMAGE" \
  --platform=managed \
  --allow-unauthenticated \
  --timeout=3600 \
  --session-affinity \
  --cpu=2 \
  --memory=2Gi \
  --set-env-vars="TARS_GEMINI_MODEL=gemini-3.8-live,TARS_DATABASE_URL=sqlite:///./tars.db,TARS_OUTLET_MATCH_THRESHOLD=0.85,TARS_SKU_MATCH_THRESHOLD=0.80,PORT=8000" \
  --set-secrets="TARS_GEMINI_API_KEY=tars-gemini-api-key:latest"

BACKEND_URL=$(gcloud run services describe tars-backend --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')
echo "Backend URL: $BACKEND_URL"

# 4. Build frontend container
echo "[4/5] Building frontend image with VITE_API_URL=$BACKEND_URL..."
FRONTEND_IMAGE="gcr.io/$PROJECT_ID/tars-frontend:latest"
gcloud builds submit --project="$PROJECT_ID" --tag "$FRONTEND_IMAGE" ./frontend

# 5. Deploy frontend to Cloud Run
echo "[5/5] Deploying tars-frontend..."
gcloud run deploy tars-frontend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --image="$FRONTEND_IMAGE" \
  --platform=managed \
  --allow-unauthenticated \
  --timeout=300 \
  --cpu=1 \
  --memory=512Mi

FRONTEND_URL=$(gcloud run services describe tars-frontend --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')

echo "======================================================="
echo "TARS Deployment Complete!"
echo "Frontend URL: $FRONTEND_URL"
echo "Backend URL:  $BACKEND_URL/health"
echo "======================================================="

