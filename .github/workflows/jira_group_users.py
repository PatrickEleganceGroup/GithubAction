import os
import requests
import json
import math

def get_users_in_group(jira_site, basic_auth_header, group_name):
    """
    Retrieve all users (accountId, displayName) from Jira's group/member endpoint.
    Returns a list of dict: { "accountId": ..., "displayName": ... }.
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
    Calls the Atlassian admin API in batches (up to 100 account IDs per request).
    Returns a dict { accountId -> email }.
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

        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()

        data = resp.json()
        for entry in data.get("data", []):
            acct_id = entry.get("accountId")
            email   = entry.get("email") or ""
            if acct_id:
                email_map[acct_id] = email

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
    org_id      = os.environ.get("ORG_ID", "b4235a52-bd04-12a0-j718-68bd06255171")

    if not all([jira_site, basic_auth, bearer_token, project_key, issue_key, org_id]):
        raise ValueError("Missing one or more required env vars: "
                         "JIRA_SITE, BASIC_AUTH, BEARER_TOKEN, PROJECT_KEY, ISSUE_KEY, ORG_ID")

    jira_headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json"
    }

    # ---------------------------
    # 2) Collect group members
    # ---------------------------
    # We'll keep track of which full group names each user is in.
    user_groups = {}  # { accountId: set_of_full_group_names }

    def add_group_name(user_list, full_group_name):
        """
        For each user in 'user_list', add the 'full_group_name' (e.g. 'ATLASSIAN-GTP00-MANAGERS')
        to that user's set in user_groups.
        """
        for u in user_list:
            acct_id = u.get("accountId")
            if acct_id:
                if acct_id not in user_groups:
                    user_groups[acct_id] = set()
                user_groups[acct_id].add(full_group_name)

    # Full group names
    group_contributors_full = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_ext_contributors_full = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_managers_full     = f"ATLASSIAN-{project_key}-MANAGERS"
    group_viewers_full      = f"ATLASSIAN-{project_key}-VIEWERS"
    group_ext_viewers_full  = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    # Fetch from Jira
    managers         = get_users_in_group(jira_site, jira_headers, group_managers_full)
    contributors_int = get_users_in_group(jira_site, jira_headers, group_contributors_full)
    contributors_ext = get_users_in_group(jira_site, jira_headers, group_ext_contributors_full)
    viewers_int      = get_users_in_group(jira_site, jira_headers, group_viewers_full)
    viewers_ext      = get_users_in_group(jira_site, jira_headers, group_ext_viewers_full)

    # Combine certain lists for final display
    all_contributors = contributors_int + contributors_ext
    all_viewers      = viewers_int + viewers_ext

    # Track which group each user belongs to
    add_group_name(managers,         group_managers_full)
    add_group_name(contributors_int, group_contributors_full)
    add_group_name(contributors_ext, group_ext_contributors_full)
    add_group_name(viewers_int,      group_viewers_full)
    add_group_name(viewers_ext,      group_ext_viewers_full)

    # Collect all unique accountIds
    unique_account_ids = set(u["accountId"] for u in (
        managers + all_contributors + all_viewers
    ) if u["accountId"])

    print(f"Found {len(unique_account_ids)} unique accountIds to fetch emails for...")

    # ---------------------------
    # 3) Fetch Emails in Batches
    # ---------------------------
    account_ids_list = list(unique_account_ids)
    email_map = fetch_emails_in_batches(org_id, bearer_token, account_ids_list)

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
    doc_content.append(make_user_table(managers, user_groups))

    # Contributors Section
    doc_content.append(make_heading("Contributors", level=2))
    doc_content.append(make_user_table(all_contributors, user_groups))

    # Viewers Section
    doc_content.append(make_heading("Viewers", level=2))
    doc_content.append(make_user_table(all_viewers, user_groups))

    adf_body = {
        "type": "doc",
        "version": 1,
        "content": doc_content
    }

    comment_url = f"{jira_site}/rest/api/3/issue/{issue_key}/comment"
    payload = {"body": adf_body}

    response = requests.post(comment_url, headers=jira_headers, json=payload)
    if not response.ok:
        print("Jira response text:", response.text)
    response.raise_for_status()

    print(f"Successfully posted ADF comment to {issue_key} with {len(unique_account_ids)} accounts processed.")

def make_heading(text, level=2):
    """
    Creates an ADF heading node (e.g. h2).
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

def make_user_table(users, user_groups):
    """
    Builds an ADF table node for a list of user dicts 
    (each having 'displayName', 'emailAddress', 'accountId').
    Columns: Name | Email | Groups
      - If emailAddress is empty, show the accountId
      - Groups are the *full* group names from user_groups[accountId], comma-separated
    """
    sorted_users = sorted(users, key=lambda x: x["displayName"].lower())

    rows = []
    # Header row
    rows.append({
        "type": "tableRow",
        "content": [
            table_cell_paragraph("Name"),
            table_cell_paragraph("Email"),
            table_cell_paragraph("Groups")
        ]
    })

    for u in sorted_users:
        display = u["displayName"]
        email   = u.get("emailAddress", "")
        acct_id = u.get("accountId", "")

        if not email:
            email = acct_id

        # Combine all full group names (sorted for consistency)
        group_set  = user_groups.get(acct_id, set())
        group_list = sorted(list(group_set))
        groups_str = ",".join(group_list)

        rows.append({
            "type": "tableRow",
            "content": [
                table_cell_paragraph(display),
                table_cell_paragraph(email),
                table_cell_paragraph(groups_str)
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
