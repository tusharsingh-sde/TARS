<#
.SYNOPSIS
    Builds and deploys TARS Backend & Frontend to Google Cloud Run with WebSocket affinity.
.DESCRIPTION
    Requires Google Cloud SDK (gcloud CLI) and an active authenticated GCP project.
.PARAMETER ProjectId
    GCP Project ID. If omitted, uses current gcloud config.
.PARAMETER Region
    GCP Region. Defaults to 'asia-south1' (Mumbai).
#>

[CmdletBinding()]
param(
    [string]$ProjectId,
    [string]$Region = "asia-south1"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Error "gcloud CLI is required but not found in PATH. Install Google Cloud SDK."
}

if (-not $ProjectId) {
    $ProjectId = (gcloud config get-value project 2>$null).Trim()
    if (-not $ProjectId -or $ProjectId -eq "(unset)") {
        Write-Error "No GCP project set. Pass -ProjectId <id> or run 'gcloud config set project <id>'."
    }
}

Write-Host "==> Target GCP Project: $ProjectId" -ForegroundColor Cyan
Write-Host "==> Target Region:      $Region" -ForegroundColor Cyan

# 1. Check or prompt for Gemini API Key Secret in Secret Manager
Write-Host "`n[1/5] Checking Google Cloud Secret Manager for 'tars-gemini-api-key'..." -ForegroundColor Yellow
$secretExists = gcloud secrets describe tars-gemini-api-key --project=$ProjectId 2>$null
if (-not $secretExists) {
    Write-Host "Secret 'tars-gemini-api-key' does not exist." -ForegroundColor Yellow
    $apiKey = Read-Host -Prompt "Enter your Google Gemini API Key"
    if ($apiKey) {
        $apiKey | gcloud secrets create tars-gemini-api-key --data-file=- --project=$ProjectId
        Write-Host "Created secret 'tars-gemini-api-key'." -ForegroundColor Green
    } else {
        Write-Warning "Skipped creating secret. Ensure TARS_GEMINI_API_KEY is configured before starting the service."
    }
} else {
    Write-Host "Secret 'tars-gemini-api-key' exists." -ForegroundColor Green
}

# 2. Build & Push Backend Container via Cloud Build
Write-Host "`n[2/5] Building backend container image via Cloud Build..." -ForegroundColor Yellow
$BackendImage = "gcr.io/$ProjectId/tars-backend:latest"
gcloud builds submit --project=$ProjectId --tag $BackendImage ./backend

# 3. Deploy Backend to Cloud Run
Write-Host "`n[3/5] Deploying tars-backend to Cloud Run..." -ForegroundColor Yellow
gcloud run deploy tars-backend `
    --project=$ProjectId `
    --region=$Region `
    --image=$BackendImage `
    --platform=managed `
    --allow-unauthenticated `
    --timeout=3600 `
    --session-affinity `
    --cpu=2 `
    --memory=2Gi `
    --set-env-vars="TARS_GEMINI_MODEL=gemini-3.8-live,TARS_DATABASE_URL=sqlite:///./tars.db,TARS_OUTLET_MATCH_THRESHOLD=0.85,TARS_SKU_MATCH_THRESHOLD=0.80,PORT=8000" `
    --set-secrets="TARS_GEMINI_API_KEY=tars-gemini-api-key:latest"

$BackendUrl = (gcloud run services describe tars-backend --project=$ProjectId --region=$Region --format='value(status.url)' 2>$null).Trim()
Write-Host "Backend URL: $BackendUrl" -ForegroundColor Green

# 4. Build & Push Frontend Container with Backend URL
Write-Host "`n[4/5] Building frontend container image with VITE_API_URL=$BackendUrl..." -ForegroundColor Yellow
$FrontendImage = "gcr.io/$ProjectId/tars-frontend:latest"
# Pass VITE_API_URL as build-arg to Cloud Build
gcloud builds submit --project=$ProjectId --tag $FrontendImage --substitutions=_API_URL=$BackendUrl ./frontend

# 5. Deploy Frontend to Cloud Run
Write-Host "`n[5/5] Deploying tars-frontend to Cloud Run..." -ForegroundColor Yellow
gcloud run deploy tars-frontend `
    --project=$ProjectId `
    --region=$Region `
    --image=$FrontendImage `
    --platform=managed `
    --allow-unauthenticated `
    --timeout=300 `
    --cpu=1 `
    --memory=512Mi

$FrontendUrl = (gcloud run services describe tars-frontend --project=$ProjectId --region=$Region --format='value(status.url)' 2>$null).Trim()

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host "TARS Deployment Complete!" -ForegroundColor Green
Write-Host "Frontend (Rep & Manager PWA): $FrontendUrl" -ForegroundColor Cyan
Write-Host "Backend API & Health Check:   $BackendUrl/health" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

