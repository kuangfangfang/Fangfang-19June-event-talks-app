import os
import base64
import json
import time
import logging
from flask import Flask, request, jsonify
from google.cloud import storage
from google.cloud import bigquery
from pypdf import PdfReader
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize GCP Clients (lazy loaded or initialized globally)
# For local testing, these can fail or be mocked if credentials are not present
def get_storage_client():
    try:
        return storage.Client()
    except Exception as e:
        logger.warning(f"Could not initialize GCS client: {e}. Local mode or missing credentials.")
        return None

def get_bigquery_client():
    try:
        return bigquery.Client()
    except Exception as e:
        logger.warning(f"Could not initialize BigQuery client: {e}. Local mode or missing credentials.")
        return None

# Simple keyword tagger
KEYWORDS = {
    "invoice": "invoice",
    "receipt": "receipt",
    "billing": "billing",
    "payment": "billing",
    "report": "report",
    "analysis": "report",
    "contract": "contract",
    "agreement": "contract",
    "confidential": "confidential",
    "secret": "confidential",
    "urgent": "urgent",
    "project": "project",
    "draft": "draft"
}

def extract_tags_from_text(text):
    tags = set()
    text_lower = text.lower()
    for word, tag in KEYWORDS.items():
        if word in text_lower:
            tags.add(tag)
    return list(tags) if tags else ["general"]

@app.route("/", methods=["POST"])
def process_event():
    """
    Receives Pub/Sub messages pushed to this endpoint.
    Expects GCS object creation notifications.
    """
    envelope = request.get_json(silent=True)
    if not envelope:
        msg = "No request body provided"
        logger.error(msg)
        return jsonify({"error": msg}), 400

    if not isinstance(envelope, dict) or "message" not in envelope:
        msg = "Invalid Pub/Sub message format (missing 'message' field)"
        logger.error(msg)
        return jsonify({"error": msg}), 400

    pubsub_message = envelope["message"]
    
    # Message data contains base64 encoded GCS object metadata
    if "data" not in pubsub_message:
        msg = "Invalid Pub/Sub message format (missing 'data' field)"
        logger.error(msg)
        return jsonify({"error": msg}), 400

    try:
        data_str = base64.b64decode(pubsub_message["data"]).decode("utf-8")
        event_data = json.loads(data_str)
    except Exception as e:
        msg = f"Failed to decode message data: {e}"
        logger.error(msg)
        return jsonify({"error": msg}), 400

    # Extract bucket and file name
    bucket_name = event_data.get("bucket")
    file_name = event_data.get("name")
    content_type = event_data.get("contentType", "")
    created_time = event_data.get("timeCreated", time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))

    if not bucket_name or not file_name:
        logger.warning("Event data does not contain bucket or file name. Skipping processing.")
        return jsonify({"status": "skipped", "reason": "No bucket or file name in event data"}), 200

    logger.info(f"Processing file: gs://{bucket_name}/{file_name} (Content-Type: {content_type})")

    file_bytes = None
    mock_content = event_data.get("mockContent")

    # If it contains mockContent (used for local testing), use it directly
    if mock_content:
        logger.info("Using mock content provided in payload for local testing.")
        if isinstance(mock_content, str):
            file_bytes = mock_content.encode("utf-8")
        else:
            file_bytes = bytes(mock_content)
    else:
        # Download file from GCS
        storage_client = get_storage_client()
        if not storage_client:
            msg = "GCS client not available. Cannot download file."
            logger.error(msg)
            return jsonify({"error": msg}), 500

        try:
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(file_name)
            file_bytes = blob.download_as_bytes()
            logger.info(f"Successfully downloaded {len(file_bytes)} bytes from GCS.")
        except Exception as e:
            msg = f"Failed to download file from GCS: {e}"
            logger.error(msg)
            return jsonify({"error": msg}), 500

    # Process based on content type/file extension
    word_count = 0
    tags = []
    processed_time = time.time()
    
    file_ext = os.path.splitext(file_name.lower())[1]

    # Dynamic & Realistic OCR / Metadata extraction simulation
    if file_ext == ".txt":
        try:
            text = file_bytes.decode("utf-8")
            words = text.split()
            word_count = len(words)
            tags = extract_tags_from_text(text)
            tags.append("txt")
            logger.info(f"Processed text file. Word count: {word_count}. Tags: {tags}")
        except Exception as e:
            logger.error(f"Error parsing text file: {e}")
            tags = ["error_parsing", "txt"]

    elif file_ext == ".pdf":
        try:
            pdf_file = io.BytesIO(file_bytes)
            reader = PdfReader(pdf_file)
            text_content = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_content.append(page_text)
            
            full_text = "\n".join(text_content)
            words = full_text.split()
            word_count = len(words)
            tags = extract_tags_from_text(full_text)
            tags.append("pdf")
            logger.info(f"Processed PDF file with {len(reader.pages)} pages. Word count: {word_count}. Tags: {tags}")
        except Exception as e:
            logger.error(f"Error parsing PDF file: {e}")
            tags = ["error_parsing", "pdf"]

    elif file_ext in [".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
        # Simulate OCR delay
        logger.info(f"Simulating OCR processing for image file {file_name}...")
        time.sleep(2.0)
        word_count = len(file_bytes) // 5000  # Simple simulated count based on size
        if word_count == 0:
            word_count = 120  # Default mock count
        tags = ["image", "ocr-simulated", "scanned"]
        logger.info(f"Simulated OCR finished. Word count: {word_count}. Tags: {tags}")

    else:
        # Default fallback for other file formats
        logger.info(f"Unsupported file format {file_ext}. Skipping deep processing.")
        word_count = 0
        tags = ["unsupported_format"]

    # Stream results into BigQuery
    bq_client = get_bigquery_client()
    project_id = os.environ.get("GCP_PROJECT") or (bq_client.project if bq_client else None)
    dataset_id = os.environ.get("BQ_DATASET", "document_processing")
    table_id = os.environ.get("BQ_TABLE", "metadata")
    
    row = {
        "filename": file_name,
        "bucket_name": bucket_name,
        "word_count": word_count,
        "tags": tags,
        "created_time": created_time,
        "processed_time": time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(processed_time))
    }

    if bq_client:
        table_ref = f"{project_id}.{dataset_id}.{table_id}"
        try:
            errors = bq_client.insert_rows_json(table_ref, [row])
            if errors:
                logger.error(f"BigQuery insertion errors: {errors}")
                # We do not crash the endpoint, but log it
            else:
                logger.info(f"Successfully streamed row to BigQuery table {table_ref}")
        except Exception as e:
            logger.error(f"Failed to stream metadata to BigQuery: {e}")
    else:
        logger.warning("BigQuery client not available. Skipping insertion to BigQuery.")

    # Return result to the caller (Pub/Sub will acknowledge on 2xx status)
    return jsonify({
        "status": "success",
        "processed_file": f"gs://{bucket_name}/{file_name}",
        "metadata": row
    }), 200

if __name__ == "__main__":
    # Get port from environment or default to 8080
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)
