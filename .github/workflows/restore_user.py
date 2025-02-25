#!/usr/bin/env python3

import os
import requests

# --- CONFIGURATION ---
JIRA_BASE_URL = "https://prudential-ps.atlassian.net"
USERNAME_OR_EMAIL = "srvpssaprjira04@prudential.com.sg"  # Your Jira account email
API_TOKEN = "ATATT3xFfGF0EFfaYwInM0kTqkiHvhD4_QXqLaarvpjTFpXC6N2l2toDIG8i7wWrlEJXZPlZK4C5dBX57oHgGKMYgXnV5Z1ZCzK-516jefrFQ-1BrqtVV2qViAEBS53U1N4jT45fgaaHayHgwBN7EnZV2DMx6S3pbusSJKHaoo7V72Z91WPVWF8=2F8CF568"

# Read TARGET_EMAIL from environment variables (set via the workflow)
TARGET_EMAIL = os.environ.get("TARGET_EMAIL", "default@example.com")

# Atlassian restore-access endpoint
ORG_ID = "b4235a52-bd04-12a0-j718-68bd06255171"
RESTORE_ACCESS_URL_TEMPLATE = "https://api.atlassian.com/admin/v1/orgs/{org_id}/directory/users/{account_id}/restore-access"

# Bearer token for restore-access endpoint
BEARER_TOKEN = "ATCTT3xFfGN0npB5HwrrcaA3AQvHQrV7r3_11rpJEwqC-gkhudBnLhC9sNbkAu75hYoMm-94-KbGyBOE7delh4bprW3EAKI3c7o5q_fwb4aVgichM4G73ZYQRUK8h-k_A31cYaSg-_RAM1pofsCgKId9gFsZO7PHHcZxqrof2uYWwfwn9ERLSZQ=30AEA3A2"

def fetch_account_id():
    auth = (USERNAME_OR_EMAIL, API_TOKEN)
    headers = {"Accept": "application/json"}
    start_at = 0
    max_results = 50
    found_account_id = None

    while True:
        url = f"{JIRA_BASE_URL}/rest/api/3/users/search?startAt={start_at}&maxResults={max_results}"
        response = requests.get(url, headers=headers, auth=auth)
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
        # ANSI hyperlink sequence (if supported)
        hyperlink = f"\033]8;;{ticket_url}\033\\log a ticket\033]8;;\033\\"
        message = (f"Your account was not found from the provided email address, {TARGET_EMAIL}. "
                   "This could be because the email address is hidden especially for External Users. "
                   "Please ensure this is the correct email and if it was correct and/or you're an External User, "
                   f"please {hyperlink} ({ticket_url}).")
        print(message)
        output_lines.append(message)
    else:
        message = f"Found accountId: {account_id}"
        print(message)
        output_lines.append(message)
        response = restore_access(account_id)
        response_message = f"Response Status: {response.status_code}"
        print(response_message)
        output_lines.append(response_message)
        try:
            json_response = response.json()
            json_message = f"Response JSON: {json_response}"
            print(json_message)
            output_lines.append(json_message)
        except ValueError:
            text_response = f"Response Text: {response.text}"
            print(text_response)
            output_lines.append(text_response)
    # Write output to a file for use in subsequent workflow steps
    with open("result.txt", "w") as f:
        f.write("\n".join(output_lines))

if __name__ == "__main__":
    main()
