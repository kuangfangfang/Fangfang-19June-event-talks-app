import os
import sys
import base64
import json
import argparse
import urllib.request
import mimetypes

def main():
    parser = argparse.ArgumentParser(description="Send a mock GCS Pub/Sub payload to the local processor service.")
    parser.add_argument("--file", required=True, help="Path to the local file to process.")
    parser.add_argument("--port", type=int, default=8080, help="Port where the local Flask app is running (default: 8080).")
    parser.add_argument("--bucket", default="my-mock-bucket", help="Mock GCS bucket name (default: my-mock-bucket).")
    args = parser.parse_args()

    file_path = args.file
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' does not exist.")
        sys.exit(1)

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    
    # Guess mime type
    content_type, _ = mimetypes.guess_type(file_path)
    if not content_type:
        content_type = "application/octet-stream"

    print(f"Reading file: {file_path} ({content_type}, {file_size} bytes)")

    # Read content
    # If text, send as string. If binary, send as list of integers.
    is_text = content_type.startswith("text/") or file_name.endswith(".txt")
    if is_text:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                mock_content = f.read()
        except UnicodeDecodeError:
            # Fallback to binary if encoding fails
            is_text = False

    if not is_text:
        with open(file_path, "rb") as f:
            mock_content = list(f.read()) # List of integers (JSON serializable)

    # 1. Create the mock GCS Object Resource representation
    gcs_object_metadata = {
        "kind": "storage#object",
        "id": f"{args.bucket}/{file_name}/1",
        "name": file_name,
        "bucket": args.bucket,
        "contentType": content_type,
        "size": str(file_size),
        "timeCreated": "2026-06-17T12:00:00Z",
        "updated": "2026-06-17T12:00:00Z",
        "mockContent": mock_content
    }

    # 2. Convert metadata to JSON string and base64-encode it
    metadata_json = json.dumps(gcs_object_metadata)
    encoded_data = base64.b64encode(metadata_json.encode("utf-8")).decode("utf-8")

    # 3. Wrap in Pub/Sub message envelope
    pubsub_envelope = {
        "message": {
            "data": encoded_data,
            "messageId": "mock-message-id-12345"
        }
    }

    # 4. Send the POST request to local server
    url = f"http://localhost:{args.port}/"
    headers = {"Content-Type": "application/json"}
    req_data = json.dumps(pubsub_envelope).encode("utf-8")

    print(f"Sending payload to {url}...")
    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req) as response:
            status_code = response.status
            response_body = response.read().decode("utf-8")
            print(f"\nResponse Status: {status_code}")
            
            # Format and print the JSON response
            try:
                formatted_response = json.dumps(json.loads(response_body), indent=2)
                print("Response Body:\n", formatted_response)
            except Exception:
                print("Response Body:\n", response_body)
    except urllib.error.HTTPError as e:
        print(f"\nHTTP Error {e.code}: {e.reason}")
        try:
            print("Response Body:\n", e.read().decode("utf-8"))
        except Exception:
            pass
    except urllib.error.URLError as e:
        print(f"\nConnection Error: {e.reason}")
        print("Please ensure the Flask app is running locally (e.g. run 'python processor/app.py').")

if __name__ == "__main__":
    main()
