#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -e

# Configuration
REGION="us-central1"
DATASET_ID="document_processing"
TABLE_ID="metadata"
TOPIC_ID="document-uploads"
SUBSCRIPTION_ID="document-processor-sub"
SERVICE_NAME="processor-service"
SCHEMA_FILE="scripts/bq_schema.json"

# Check if gcloud is authenticated
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT_ID" ]; then
  echo "Error: No active Google Cloud project set. Run 'gcloud config set project [PROJECT_ID]' first."
  exit 1
fi

echo "============================================================"
echo "Starting deployment for serverless document pipeline"
echo "Project ID : $PROJECT_ID"
echo "Region     : $REGION"
echo "============================================================"

# Define GCS bucket name (globally unique, so we suffix it with the project ID)
# GCS bucket names cannot contain underscores, let's replace them if present
SAFE_PROJECT_ID=$(echo "$PROJECT_ID" | tr '_' '-')
BUCKET_NAME="document-ingestion-${SAFE_PROJECT_ID}"

echo "Step 1: Enabling Google Cloud APIs..."
gcloud services enable \
  storage.googleapis.com \
  pubsub.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  bigquery.googleapis.com

# Retrieve project number for IAM bindings
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

echo "Step 2: Creating Cloud Storage Bucket..."
if gcloud storage buckets describe "gs://${BUCKET_NAME}" &>/dev/null; then
  echo "Bucket gs://${BUCKET_NAME} already exists."
else
  gcloud storage buckets create "gs://${BUCKET_NAME}" --location="$REGION"
  echo "Created bucket gs://${BUCKET_NAME}"
fi

echo "Step 3: Creating BigQuery Dataset and Table..."
# Create dataset if it doesn't exist
if bq show "$DATASET_ID" &>/dev/null; then
  echo "BigQuery dataset '$DATASET_ID' already exists."
else
  bq mk --location="$REGION" --dataset "$DATASET_ID"
  echo "Created BigQuery dataset '$DATASET_ID'"
fi

# Create table if it doesn't exist
if bq show "${DATASET_ID}.${TABLE_ID}" &>/dev/null; then
  echo "BigQuery table '${DATASET_ID}.${TABLE_ID}' already exists."
else
  bq mk --table --schema="$SCHEMA_FILE" "${DATASET_ID}.${TABLE_ID}"
  echo "Created BigQuery table '${DATASET_ID}.${TABLE_ID}' using schema $SCHEMA_FILE"
fi

echo "Step 4: Creating IAM Service Accounts..."
# Service account for Cloud Run processor
RUN_SA="cloudrun-processor"
RUN_SA_EMAIL="${RUN_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$RUN_SA_EMAIL" &>/dev/null; then
  echo "Service account $RUN_SA_EMAIL already exists."
else
  gcloud iam service-accounts create "$RUN_SA" \
    --display-name="Cloud Run Document Processor"
  echo "Created service account $RUN_SA"
fi

# Grant Cloud Run service account access to GCS and BigQuery
echo "Granting IAM roles to Cloud Run service account..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUN_SA_EMAIL}" \
  --role="roles/storage.objectViewer" >/dev/null

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUN_SA_EMAIL}" \
  --role="roles/bigquery.dataEditor" >/dev/null

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUN_SA_EMAIL}" \
  --role="roles/bigquery.user" >/dev/null


# Service account for Pub/Sub subscription to invoke Cloud Run
PUBSUB_SA="pubsub-cloudrun-invoker"
PUBSUB_SA_EMAIL="${PUBSUB_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$PUBSUB_SA_EMAIL" &>/dev/null; then
  echo "Service account $PUBSUB_SA_EMAIL already exists."
else
  gcloud iam service-accounts create "$PUBSUB_SA" \
    --display-name="Pub/Sub Cloud Run Invoker"
  echo "Created service account $PUBSUB_SA"
fi

# Allow Pub/Sub to generate OIDC tokens
echo "Allowing Pub/Sub system agent to create OIDC tokens..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null

echo "Step 5: Deploying Cloud Run Service..."
# Build and deploy processor service using --source (deploys via Cloud Build)
gcloud run deploy "$SERVICE_NAME" \
  --source="./processor" \
  --region="$REGION" \
  --service-account="$RUN_SA_EMAIL" \
  --no-allow-unauthenticated \
  --update-env-vars "BQ_DATASET=${DATASET_ID},BQ_TABLE=${TABLE_ID}" \
  --quiet

# Fetch Cloud Run service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)")
echo "Cloud Run Service deployed at: $SERVICE_URL"

# Grant the Pub/Sub invoker service account permission to call Cloud Run
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${PUBSUB_SA_EMAIL}" \
  --role="roles/run.invoker" >/dev/null

echo "Step 6: Configuring Pub/Sub and Cloud Storage Trigger..."
# Create the Pub/Sub topic
if gcloud pubsub topics describe "$TOPIC_ID" &>/dev/null; then
  echo "Pub/Sub topic '$TOPIC_ID' already exists."
else
  gcloud pubsub topics create "$TOPIC_ID"
  echo "Created Pub/Sub topic '$TOPIC_ID'"
fi

# Grant Cloud Storage permission to publish to our Pub/Sub topic
GCS_SERVICE_ACCOUNT=$(gcloud storage service-agent --project="$PROJECT_ID")
echo "Granting Pub/Sub Publisher role to GCS service agent ($GCS_SERVICE_ACCOUNT)..."
gcloud pubsub topics add-iam-policy-binding "$TOPIC_ID" \
  --member="serviceAccount:${GCS_SERVICE_ACCOUNT}" \
  --role="roles/pubsub.publisher" >/dev/null

# Create GCS Notifications
# Check if notifications exist first
NOTIFICATION_EXISTS=$(gcloud storage buckets notifications list "gs://${BUCKET_NAME}" --format="value(topic)" 2>/dev/null | grep "$TOPIC_ID" || true)
if [ -n "$NOTIFICATION_EXISTS" ]; then
  echo "Cloud Storage notifications for topic '$TOPIC_ID' already configured."
else
  gcloud storage buckets notifications create "gs://${BUCKET_NAME}" --topic="$TOPIC_ID"
  echo "Created GCS notification trigger to topic '$TOPIC_ID'"
fi

# Create the Pub/Sub push subscription to Cloud Run
if gcloud pubsub subscriptions describe "$SUBSCRIPTION_ID" &>/dev/null; then
  echo "Pub/Sub subscription '$SUBSCRIPTION_ID' already exists. Updating endpoint..."
  # If it exists, update endpoint and service account
  gcloud pubsub subscriptions update "$SUBSCRIPTION_ID" \
    --push-endpoint="$SERVICE_URL"
else
  gcloud pubsub subscriptions create "$SUBSCRIPTION_ID" \
    --topic="$TOPIC_ID" \
    --push-endpoint="$SERVICE_URL" \
    --push-auth-service-account="$PUBSUB_SA_EMAIL"
  echo "Created Pub/Sub Push subscription '$SUBSCRIPTION_ID'"
fi

echo "============================================================"
echo "Deployment Complete!"
echo "Ingestion Bucket: gs://${BUCKET_NAME}"
echo "Pub/Sub Topic   : $TOPIC_ID"
echo "Cloud Run URL   : $SERVICE_URL"
echo "BigQuery Table  : ${PROJECT_ID}.${DATASET_ID}.${TABLE_ID}"
echo "============================================================"
echo "Test the deployment by uploading a file:"
echo "  gcloud storage cp scripts/test_doc.txt gs://${BUCKET_NAME}/"
echo "Then check BigQuery:"
echo "  bq query --use_legacy_sql=false 'SELECT * FROM \`$PROJECT_ID.$DATASET_ID.$TABLE_ID\`'"
echo "============================================================"
