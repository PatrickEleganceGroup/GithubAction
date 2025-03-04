import os
import requests
import json

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

def fetch_emails_for_accounts(org_id, bearer_token, account_ids):
    """
    Calls the Atlassian admin API to fetch user details (including email) by accountIds.
    Endpoint: POST /admin/v1/orgs/<orgId>/users/search
    Body: { "accountIds": [...], "expand": ["EMAIL"] }
    Returns a dict mapping accountId -> email, e.g. { "712020:abc123": "user@example.com", ... }
    """
    url = f"https://api.atlassian.com/admin/v1/orgs/{org_id}/users/search"
    headers = {
        "Authorization": f"{bearer_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "accountIds": account_ids,   # array of all accountIds
        "expand": ["EMAIL"]
    }

    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()

    data = resp.json()  # Should look like { "data": [ { "accountId": "...", "email": "..." } ], "links": {} }
    email_map = {}
    for entry in data.get("data", []):
        acct_id = entry.get("accountId")
        email = entry.get("email")  # or might be None if not visible
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

    # The Atlassian org ID for the admin API
    # e.g. "b4235a52-bd04-12a0-j718-68bd06255171"
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
    group_contributors      = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_ext_contributors  = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_managers          = f"ATLASSIAN-{project_key}-MANAGERS"
    group_viewers           = f"ATLASSIAN-{project_key}-VIEWERS"
    group_ext_viewers       = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    managers            = get_users_in_group(jira_site, jira_headers, group_managers)
    contributors_int    = get_users_in_group(jira_site, jira_headers, group_contributors)
    contributors_ext    = get_users_in_group(jira_site, jira_headers, group_ext_contributors)
    viewers_int         = get_users_in_group(jira_site, jira_headers, group_viewers)
    viewers_ext         = get_users_in_group(jira_site, jira_headers, group_ext_viewers)

    # Merge sets
    all_contributors = contributors_int + contributors_ext
    all_viewers      = viewers_int + viewers_ext

    # Gather all unique accountIds
    unique_account_ids = set()
    for user in managers + all_contributors + all_viewers:
        if user["accountId"]:
            unique_account_ids.add(user["accountId"])

    # ---------------------------
    # 3) Fetch Emails in Bulk
    # ---------------------------
    # The admin API accepts up to 1000 accountIds in one POST. 
    # If there's more than 1000, you'd have to chunk them up.
    # For simplicity, assume it's less or break it up if needed.

    email_map = {}
    account_ids_list = list(unique_account_ids)
    # (Optional) If length > 1000, chunk it up in calls. 
    # Skipping chunk logic for brevity.

    # Single call
    emails_chunk = fetch_emails_for_accounts(org_id, bearer_token, account_ids_list)
    email_map.update(emails_chunk)

    # Now we can add the email to each user
    def attach_email(user_list):
        for user in user_list:
            acct_id = user.get("accountId")
            user["emailAddress"] = email_map.get(acct_id, "")

    attach_email(managers)
    attach_email(all_contributors)
    attach_email(all_viewers)

    # ---------------------------
    # 4) Build tables
    # ---------------------------
    def format_role_section(role_name, user_list):
        lines = [role_name]
        # Sort by displayName for consistent ordering
        sorted_users = sorted(user_list, key=lambda x: x["displayName"].lower())
        for u in sorted_users:
            display = u["displayName"]
            email   = u.get("emailAddress", "")
            lines.append(f"{display}|{email}")
        return "\n".join(lines)

    managers_section     = format_role_section("Managers", managers)
    contributors_section = format_role_section("Contributors", all_contributors)
    viewers_section      = format_role_section("Viewers", all_viewers)

    final_comment_body = "\n\n".join([managers_section, contributors_section, viewers_section])

    # ---------------------------
    # 5) Post the comment to Jira
    # ---------------------------
    comment_url = f"{jira_site}/rest/api/3/issue/{issue_key}/comment"
    payload = {"body": final_comment_body}

    response = requests.post(comment_url, headers=jira_headers, json=payload)
    if not response.ok:
        print("Jira response text:", response.text)
    response.raise_for_status()

    print(f"Successfully posted comment to {issue_key} with {len(unique_account_ids)} users processed.")

if __name__ == "__main__":
    main()
