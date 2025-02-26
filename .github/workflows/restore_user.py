#!/usr/bin/env python3

import os
import requests

# --- CONFIGURATION ---
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL")
# Basic token used for fetching users
BASIC_TOKEN = os.environ.get("JIRA_BASIC_TOKEN")
# Bearer token used for restoring access
BEARER_TOKEN = os.environ.get("JIRA_BEARER_TOKEN")

# Read TARGET_EMAIL from environment variables
TARGET_EMAIL = os.environ.get("TARGET_EMAIL", "default@example.com")

# Atlassian restore-access endpoint
ORG_ID = "ffd0f976-d0a5-418f-8ca5-a1d67cadc185"
RESTORE_ACCESS_URL_TEMPLATE = "https://api.atlassian.com/admin/v1/orgs/{org_id}/directory/users/{account_id}/restore-access"

def fetch_account_id():
    headers = {
        "Authorization": f"Basic {BASIC_TOKEN}",
        "Accept": "application/json",
        "User-Agent": "test-agent",
        "X-Atlassian-Token": "nocheck"
    }
    start_at = 0
    max_results = 50
    found_account_id = None

    while True:
        url = f"{JIRA_BASE_URL}/rest/api/3/users/search?startAt={start_at}&maxResults={max_results}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        users = response.json()
        if not users:
            break
        for user in users:
            email = user.get("emailAddress", "")
            if email.lower() == TARGET_EMAIL.lower():
                found_account_id = user.get("accountId", "")
                break
        if found_account_id:
            break
        start_at += max_results
    return found_account_id

def restore_access(account_id):
    url = RESTORE_ACCESS_URL_TEMPLATE.format(org_id=ORG_ID, account_id=account_id)
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Accept": "application/json"
    }
    response = requests.post(url, headers=headers)  # POST with empty body
    return response

def main():
    output_lines = []
    account_id = fetch_account_id()
    if not account_id:
        ticket_url = "https://prudential-ps.atlassian.net/servicedesk/customer/portal/6/group/10/create/44"
        message = (
            f"Your account was not found from the provided email address, {TARGET_EMAIL}. "
            "This could be because the email address is hidden especially for External Users. "
            "Please ensure this is the correct email and if it was correct and/or you're an External User, "
            f"please [log a ticket]({ticket_url})."
        )
        output_lines.append(message)
    else:
        message = f"Found accountId: {account_id}"
        output_lines.append(message)
        response = restore_access(account_id)
        response_message = f"Response Status: {response.status_code}"
        output_lines.append(response_message)
        try:
            json_response = response.json()
            json_message = f"Response JSON: {json_response}"
            output_lines.append(json_message)
        except ValueError:
            text_response = f"Response Text: {response.text}"
            output_lines.append(text_response)

    # Print only the final combined output once.
    final_output = "\n".join(output_lines)
    print(final_output)

if __name__ == "__main__":
    main()
