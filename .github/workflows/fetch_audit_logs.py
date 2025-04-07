#!/usr/bin/env python3
import os
import csv
import requests
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from google.cloud import storage

def convert_ms_to_iso(ms):
    """Convert milliseconds epoch to an ISO 8601 UTC datetime string."""
    try:
        ms_int = int(ms)
        dt = datetime.fromtimestamp(ms_int / 1000, timezone.utc)
        return dt.isoformat()
    except Exception as e:
        return ms

def fetch_jira_audit_logs():
    # Hardcoded Jira URL and endpoint
    jira_base_url = "https://prudential-ps.atlassian.net"
    token = os.environ.get("JIRA_BASIC_TOKEN")
    if not token:
        raise Exception("Missing required environment variable: JIRA_BASIC_TOKEN")
    endpoint = f"{jira_base_url}/rest/api/3/auditing/record"
    headers = {
        "Accept": "application/json",
        "Authorization": f"{token}"
    }
    
    # Calculate date range for the last 7 months in ISO 8601 using timezone-aware datetime
    to_date = datetime.now(timezone.utc)
    from_date = to_date - relativedelta(months=7)
    to_str = to_date.isoformat()
    from_str = from_date.isoformat()
    
    records = []
    offset = 0
    limit = 1000

    while True:
        params = {
            "from": from_str,
            "to": to_str,
            "offset": offset,
            "limit": limit
        }
        response = requests.get(endpoint, headers=headers, params=params)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch Jira audit logs: {response.status_code} {response.text}")
        data = response.json()
        # Assume records are under the 'records' key; if not, use the full response
        batch = data.get("records", data)
        records.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    return records

def fetch_confluence_audit_logs():
    # Hardcoded Confluence URL and endpoint (using the same token as Jira)
    confluence_base_url = "https://prudential-ps.atlassian.net/wiki"
    token = os.environ.get("JIRA_BASIC_TOKEN")
    if not token:
        raise Exception("Missing required environment variable: JIRA_BASIC_TOKEN")
    endpoint = f"{confluence_base_url}/rest/api/audit"
    headers = {
        "Accept": "application/json",
        "Authorization": f"{token}"
    }
    
    # Calculate date range for the last 7 months in epoch milliseconds using timezone-aware datetime
    end_date = datetime.now(timezone.utc)
    start_date = end_date - relativedelta(months=7)
    end_epoch = int(end_date.timestamp() * 1000)
    start_epoch = int(start_date.timestamp() * 1000)
    
    records = []
    start = 0
    limit = 1000

    while True:
        params = {
            "startDate": str(start_epoch),
            "endDate": str(end_epoch),
            "start": start,
            "limit": limit
        }
        response = requests.get(endpoint, headers=headers, params=params)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch Confluence audit logs: {response.status_code} {response.text}")
        data = response.json()
        # Assume audit records are under the 'results' key; if not, use the full response
        batch = data.get("results", data)
        # Convert the creationDate field from epoch milliseconds to ISO 8601, if present
        for record in batch:
            if "creationDate" in record:
                record["creationDate"] = convert_ms_to_iso(record["creationDate"])
        records.extend(batch)
        if len(batch) < limit:
            break
        start += limit

    return records

def write_csv(records, filename):
    if not records:
        print(f"No records found for {filename}.")
        return

    # Compute the union of all keys across records to include every field.
    all_keys = set()
    for record in records:
        all_keys.update(record.keys())
    keys = sorted(all_keys)

    with open(filename, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)
    print(f"CSV file '{filename}' created successfully.")

def upload_to_gcs(filename, bucket_name, destination_blob_name):
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(filename)
    print(f"Uploaded {filename} to gs://{bucket_name}/{destination_blob_name}")

def main():
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    
    # Fetch and save Jira audit logs
    print("Fetching Jira audit logs...")
    jira_records = fetch_jira_audit_logs()
    jira_csv_filename = f"jira_audit_{today}.csv"
    write_csv(jira_records, jira_csv_filename)
    
    # Fetch and save Confluence audit logs
    print("Fetching Confluence audit logs...")
    confluence_records = fetch_confluence_audit_logs()
    confluence_csv_filename = f"confluence_audit_{today}.csv"
    write_csv(confluence_records, confluence_csv_filename)
    
    # Retrieve Google Cloud Storage settings from environment variables
    bucket_name = os.environ.get("GOOGLE_CLOUD_BUCKET")
    folder = os.environ.get("GOOGLE_CLOUD_FOLDER", "")
    if not bucket_name:
        raise Exception("GOOGLE_CLOUD_BUCKET environment variable not set")
    
    jira_destination = f"{folder}/{jira_csv_filename}" if folder else jira_csv_filename
    confluence_destination = f"{folder}/{confluence_csv_filename}" if folder else confluence_csv_filename
    
    # Upload CSV files to Google Cloud Storage
    upload_to_gcs(jira_csv_filename, bucket_name, jira_destination)
    upload_to_gcs(confluence_csv_filename, bucket_name, confluence_destination)

if __name__ == "__main__":
    main()