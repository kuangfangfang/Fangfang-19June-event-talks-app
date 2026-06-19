# PowerShell deployment script for serverless document pipeline
$ErrorActionPreference = "Stop"

# Helper function to check if a GCP resource exists without raising script terminating errors
function Test-GcpResource {
    param(
        [scriptblock]$Script
    )
    $origPreference = $ErrorActionPreference
    $global:ErrorActionPreference = "SilentlyContinue"
    $null = & $Script 2>$null
    $success = ($LASTEXITCODE -eq 0)
    $global:ErrorActionPreference = $origPreference
    return $success
}


# Configuration
$Region = "us-central1"
$DatasetId = "document_processing"
$TableId = "metadata"
$TopicId = "document-uploads"
$SubscriptionId = "document-processor-sub"
$ServiceName = "processor-service"
$SchemaFile = "scripts/bq_schema.json"

# Check active project
$ProjectId = gcloud config get-value project 2>$null
if ([string]::IsNullOrEmpty($ProjectId)) {
    Write-Error "Error: No active Google Cloud project set. Run 'gcloud config set project [PROJECT_ID]' first."
    Exit
}

Write-Host "============================================================"
Write-Host "Starting deployment for serverless document pipeline (PowerShell)"
Write-Host "Project ID : $ProjectId"
Write-Host "Region     : $Region"
Write-Host "============================================================"

# Define GCS bucket name (no underscores allowed)
$SafeProjectId = $ProjectId -replace '_', '-'
$BucketName = "document-ingestion-$SafeProjectId"

Write-Host "Step 1: Enabling Google Cloud APIs..."
gcloud services enable `
  storage.googleapis.com `
  pubsub.googleapis.com `
  run.googleapis.com `
  cloudbuild.googleapis.com `
  artifactregistry.googleapis.com `
  bigquery.googleapis.com

# Retrieve project number
$ProjectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"

Write-Host "Step 2: Creating Cloud Storage Bucket..."
if (Test-GcpResource { gcloud storage buckets describe "gs://$BucketName" }) {
    Write-Host "Bucket gs://$BucketName already exists."
} else {
    gcloud storage buckets create "gs://$BucketName" --location="$Region"
    Write-Host "Created bucket gs://$BucketName"
}

Write-Host "Step 3: Creating BigQuery Dataset and Table..."
# Create dataset
if (Test-GcpResource { bq show $DatasetId }) {
    Write-Host "BigQuery dataset '$DatasetId' already exists."
} else {
    bq mk --location="$Region" --dataset "$DatasetId"
    Write-Host "Created BigQuery dataset '$DatasetId'"
}

# Create table
if (Test-GcpResource { bq show "${DatasetId}.${TableId}" }) {
    Write-Host "BigQuery table '${DatasetId}.${TableId}' already exists."
} else {
    bq mk --table --schema="$SchemaFile" "${DatasetId}.${TableId}"
    Write-Host "Created BigQuery table '${DatasetId}.${TableId}' using schema $SchemaFile"
}

Write-Host "Step 4: Creating IAM Service Accounts..."
# Service account for Cloud Run processor
$RunSa = "cloudrun-processor"
$RunSaEmail = "${RunSa}@${ProjectId}.iam.gserviceaccount.com"
if (Test-GcpResource { gcloud iam service-accounts describe $RunSaEmail }) {
    Write-Host "Service account $RunSaEmail already exists."
} else {
    gcloud iam service-accounts create $RunSa `
      --display-name="Cloud Run Document Processor"
    Write-Host "Created service account $RunSa"
}

# Grant Cloud Run service account access to GCS and BigQuery
Write-Host "Granting IAM roles to Cloud Run service account..."
gcloud projects add-iam-policy-binding $ProjectId `
  --member="serviceAccount:${RunSaEmail}" `
  --role="roles/storage.objectViewer" > $null

gcloud projects add-iam-policy-binding $ProjectId `
  --member="serviceAccount:${RunSaEmail}" `
  --role="roles/bigquery.dataEditor" > $null

gcloud projects add-iam-policy-binding $ProjectId `
  --member="serviceAccount:${RunSaEmail}" `
  --role="roles/bigquery.user" > $null


# Service account for Pub/Sub subscription to invoke Cloud Run
$PubSubSa = "pubsub-cloudrun-invoker"
$PubSubSaEmail = "${PubSubSa}@${ProjectId}.iam.gserviceaccount.com"
if (Test-GcpResource { gcloud iam service-accounts describe $PubSubSaEmail }) {
    Write-Host "Service account $PubSubSaEmail already exists."
} else {
    gcloud iam service-accounts create $PubSubSa `
      --display-name="Pub/Sub Cloud Run Invoker"
    Write-Host "Created service account $PubSubSa"
}

# Allow Pub/Sub to generate OIDC tokens
Write-Host "Allowing Pub/Sub system agent to create OIDC tokens..."
gcloud projects add-iam-policy-binding $ProjectId `
  --member="serviceAccount:service-${ProjectNumber}@gcp-sa-pubsub.iam.gserviceaccount.com" `
  --role="roles/iam.serviceAccountTokenCreator" > $null

Write-Host "Step 5: Deploying Cloud Run Service..."
# Build and deploy processor service using --source (deploys via Cloud Build)
gcloud run deploy $ServiceName `
  --source=".\processor" `
  --region="$Region" `
  --service-account="$RunSaEmail" `
  --no-allow-unauthenticated `
  --update-env-vars "BQ_DATASET=${DatasetId},BQ_TABLE=${TableId}" `
  --quiet

# Fetch Cloud Run service URL
$ServiceUrl = gcloud run services describe $ServiceName --region="$Region" --format="value(status.url)"
Write-Host "Cloud Run Service deployed at: $ServiceUrl"

# Grant the Pub/Sub invoker service account permission to call Cloud Run
gcloud run services add-iam-policy-binding $ServiceName `
  --region="$Region" `
  --member="serviceAccount:${PubSubSaEmail}" `
  --role="roles/run.invoker" > $null

Write-Host "Step 6: Configuring Pub/Sub and Cloud Storage Trigger..."
# Create the Pub/Sub topic
if (Test-GcpResource { gcloud pubsub topics describe $TopicId }) {
    Write-Host "Pub/Sub topic '$TopicId' already exists."
} else {
    gcloud pubsub topics create $TopicId
    Write-Host "Created Pub/Sub topic '$TopicId'"
}

# Grant Cloud Storage permission to publish to our Pub/Sub topic
$GcsServiceAccount = gcloud storage service-agent --project="$ProjectId"
Write-Host "Granting Pub/Sub Publisher role to GCS service agent ($GcsServiceAccount)..."
gcloud pubsub topics add-iam-policy-binding $TopicId `
  --member="serviceAccount:${GcsServiceAccount}" `
  --role="roles/pubsub.publisher" > $null

# Create GCS Notifications
# Check if notifications exist first
$NotificationExists = $false
$origPreference = $ErrorActionPreference
$global:ErrorActionPreference = "SilentlyContinue"
$NotificationExists = gcloud storage buckets notifications list "gs://$BucketName" --format="value(topic)" 2>$null | Select-String -Pattern $TopicId
$global:ErrorActionPreference = $origPreference

if ($NotificationExists) {
    Write-Host "Cloud Storage notifications for topic '$TopicId' already configured."
} else {
    gcloud storage buckets notifications create "gs://$BucketName" --topic="$TopicId"
    Write-Host "Created GCS notification trigger to topic '$TopicId'"
}

# Create the Pub/Sub push subscription to Cloud Run
$SubExists = $false
$origPreference = $ErrorActionPreference
$global:ErrorActionPreference = "SilentlyContinue"
$null = gcloud pubsub subscriptions describe $SubscriptionId 2>$null
$SubExists = ($LASTEXITCODE -eq 0)
$global:ErrorActionPreference = $origPreference

if ($SubExists) {
    Write-Host "Pub/Sub subscription '$SubscriptionId' already exists. Updating endpoint..."
    gcloud pubsub subscriptions update $SubscriptionId `
      --push-endpoint="$ServiceUrl"
} else {
    gcloud pubsub subscriptions create $SubscriptionId `
      --topic="$TopicId" `
      --push-endpoint="$ServiceUrl" `
      --push-auth-service-account="$PubSubSaEmail"
    Write-Host "Created Pub/Sub Push subscription '$SubscriptionId'"
}

Write-Host "============================================================"
Write-Host "Deployment Complete!"
Write-Host "Ingestion Bucket: gs://${BucketName}"
Write-Host "Pub/Sub Topic   : $TopicId"
Write-Host "Cloud Run URL   : $ServiceUrl"
Write-Host "BigQuery Table  : ${ProjectId}.${DatasetId}.${TableId}"
Write-Host "============================================================"
Write-Host "Test the deployment by uploading a file:"
Write-Host "  gcloud storage cp scripts/test_doc.txt gs://${BucketName}/"
Write-Host "Then check BigQuery:"
Write-Host "  bq query --use_legacy_sql=false 'SELECT * FROM \`$ProjectId.$DatasetId.$TableId\`'"
Write-Host "============================================================"
