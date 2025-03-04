import os
import requests
import json
import math

def get_users_in_group(jira_site, basic_auth_header, group_name):
    """
    Retrieve all users (accountId, displayName) in the given group, using Jira's group/member endpoint.
    Returns a list of dict: { "accountId": ..., "displayName": ... }
    """
    start_at = 0
    max_results = 50
    users = []

    while True:
        url = f"{jira_site}/rest/api/3/group/member"
        params = {
            "groupname": group_name,
            "startAt": start_at,
            "maxResults": max_results
        }
        resp = requests.get(url, headers=basic_auth_header, params=params)
        resp.raise_for_status()
        data = resp.json()

        for user in data.get("values", []):
            users.append({
                "accountId": user.get("accountId"),
                "displayName": user.get("displayName", "")
            })

        if data.get("isLast", True):
            break

        start_at += max_results

    return users

def fetch_emails_in_batches(org_id, bearer_token, account_ids):
    """
    Calls the Atlassian admin API in batches of up to 100 account IDs per request.
    Endpoint: POST /admin/v1/orgs/<orgId>/users/search
    Body: { "accountIds": [...], "expand": ["EMAIL"] }

    Returns a dict mapping accountId -> email, e.g. 
      { "712020:abc123": "user@example.com", ... }

    If a user has no email or is not found, the email is "".
    """
    url = f"https://api.atlassian.com/admin/v1/orgs/{org_id}/users/search"
    headers = {
        "Authorization": f"{bearer_token}",
        "Content-Type": "application/json"
    }

    email_map = {}
    chunk_size = 100

    for i in range(0, len(account_ids), chunk_size):
        chunk = account_ids[i : i + chunk_size]

        payload = {
            "accountIds": chunk,
            "expand": ["EMAIL"]
        }

        # Make the bulk request for this chunk
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()

        data = resp.json()  # { "data": [ { "accountId": "...", "email": "..." } ], ... }
        for entry in data.get("data", []):
            acct_id = entry.get("accountId")
            email   = entry.get("email") or ""
            if acct_id:
                email_map[acct_id] = email

        # If any account wasn't returned, it won't appear in data["data"],
        # so they remain absent from email_map. We'll handle that by defaulting to "" later.

    return email_map

def main():
    # ---------------------------
    # 1) Read environment vars
    # ---------------------------
    jira_site   = os.environ.get("JIRA_SITE") or "https://prudential-ps.atlassian.net"
    basic_auth  = os.environ.get("BASIC_AUTH")  # e.g. "Basic <base64string>"
    bearer_token = os.environ.get("BEARER_TOKEN")  # used for the admin API
    project_key = os.environ.get("PROJECT_KEY")
    issue_key   = os.environ.get("ISSUE_KEY")

    # The Atlassian org ID for the admin API
    org_id = os.environ.get("ORG_ID", "b4235a52-bd04-12a0-j718-68bd06255171")

    if not all([jira_site, basic_auth, bearer_token, project_key, issue_key, org_id]):
        raise ValueError("Missing one or more required env vars: "
                         "JIRA_SITE, BASIC_AUTH, BEARER_TOKEN, PROJECT_KEY, ISSUE_KEY, ORG_ID")

    # Headers for Jira calls
    jira_headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json"
    }

    # ---------------------------
    # 2) Collect group members
    # ---------------------------
    group_contributors     = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_ext_contributors = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_managers         = f"ATLASSIAN-{project_key}-MANAGERS"
    group_viewers          = f"ATLASSIAN-{project_key}-VIEWERS"
    group_ext_viewers      = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    managers         = get_users_in_group(jira_site, jira_headers, group_managers)
    contributors_int = get_users_in_group(jira_site, jira_headers, group_contributors)
    contributors_ext = get_users_in_group(jira_site, jira_headers, group_ext_contributors)
    viewers_int      = get_users_in_group(jira_site, jira_headers, group_viewers)
    viewers_ext      = get_users_in_group(jira_site, jira_headers, group_ext_viewers)

    # Merge lists
    all_contributors = contributors_int + contributors_ext
    all_viewers      = viewers_int + viewers_ext

    # Collect all unique accountIds across all roles
    unique_account_ids = set(u["accountId"] for u in (managers + all_contributors + all_viewers) if u["accountId"])
    print(f"Found {len(unique_account_ids)} unique accountIds to fetch emails for...")

    # ---------------------------
    # 3) Fetch Emails in Batches
    # ---------------------------
    account_ids_list = list(unique_account_ids)
    email_map = fetch_emails_in_batches(org_id, bearer_token, account_ids_list)

    # For any ID not in email_map, default to ""
    # (However, if the admin API didn't return an account for any reason, we won't have it in email_map.)
    def attach_email(user_list):
        for user in user_list:
            acct_id = user.get("accountId")
            if acct_id:
                user["emailAddress"] = email_map.get(acct_id, "")

    attach_email(managers)
    attach_email(all_contributors)
    attach_email(all_viewers)

    # ---------------------------
    # 4) Build ADF comment
    # ---------------------------
    doc_content = []
    
    # Manager Section
    doc_content.append(make_heading("Managers", level=2))
    doc_content.append(make_user_table(managers))

    # Contributors Section
    doc_content.append(make_heading("Contributors", level=2))
    doc_content.append(make_user_table(all_contributors))

    # Viewers Section
    doc_content.append(make_heading("Viewers", level=2))
    doc_content.append(make_user_table(all_viewers))

    adf_body = {
        "type": "doc",
        "version": 1,
        "content": doc_content
    }

    # ---------------------------
    # 5) Post the comment to Jira
    # ---------------------------
    comment_url = f"{jira_site}/rest/api/3/issue/{issue_key}/comment"
    payload = {"body": adf_body}

    response = requests.post(comment_url, headers=jira_headers, json=payload)
    if not response.ok:
        print("Jira response text:", response.text)
    response.raise_for_status()

    print(f"Successfully posted ADF comment to {issue_key} with {len(unique_account_ids)} accounts processed.")

def make_heading(text, level=2):
    """
    Creates an ADF heading node with the given text and level (h2 by default).
    """
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

def make_user_table(users):
    """
    Builds an ADF table node for the given list of user dicts 
    (each having 'displayName' and 'emailAddress').
    First row is header: Name | Email
    Then one row per user. 
    If emailAddress is empty, we show the accountId instead.
    """
    sorted_users = sorted(users, key=lambda x: x["displayName"].lower())

    rows = []
    # Header row
    rows.append({
        "type": "tableRow",
        "content": [
            table_cell_paragraph("Name"),
            table_cell_paragraph("Email")
        ]
    })

    # Each user row
    for u in sorted_users:
        display = u["displayName"]
        email   = u.get("emailAddress", "")
        acct_id = u.get("accountId", "")

        # If email is empty, fallback to accountId
        if not email:
            email = acct_id

        rows.append({
            "type": "tableRow",
            "content": [
                table_cell_paragraph(display),
                table_cell_paragraph(email)
            ]
        })

    return {
        "type": "table",
        "content": rows
    }

def table_cell_paragraph(cell_text):
    return {
        "type": "tableCell",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": cell_text
                    }
                ]
            }
        ]
    }

if __name__ == "__main__":
    main()
