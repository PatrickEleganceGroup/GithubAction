import os
import requests
from fpdf import FPDF

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
        resp = requests.get(url, headers=basic_auth_header, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for user in data.get("values", []):
            users.append({
                "accountId": user.get("accountId"),
                "displayName": user.get("displayName", "")
            })

        # If no more pages
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
        "Authorization": bearer_token,  # e.g. "Basic <base64>" or "Bearer <token>"
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
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("data", []):
            acct_id = entry.get("accountId")
            email = entry.get("email") or ""
            if acct_id:
                email_map[acct_id] = email

    return email_map


def generate_pdf_from_data(pdf_filename, managers, contributors, viewers):
    """
    Creates a simple PDF listing managers, contributors, viewers.
    Uses the fpdf library (version 1.x).
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    def add_title_section(title):
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, title, ln=True)
        pdf.set_font("Arial", size=12)

    def add_line(text=""):
        pdf.multi_cell(0, 6, text)

    # Helper to list users
    def list_users(title, user_list):
        add_title_section(title)
        if not user_list:
            add_line("No users found.\n")
            return
        for user in user_list:
            name = user.get("displayName", "")
            email = user.get("emailAddress", "") or user.get("accountId", "")
            add_line(f"{name} <{email}>")
        add_line()  # blank line after group

    list_users("Managers", managers)
    list_users("Contributors", contributors)
    list_users("Viewers", viewers)

    pdf.output(pdf_filename)
    print(f"Generated PDF: {pdf_filename}")


def attach_pdf_to_issue(jira_site, jira_headers, issue_key, pdf_filename):
    """
    Attach a PDF file to the specified Jira issue.
    """
    attachment_url = f"{jira_site}/rest/api/3/issue/{issue_key}/attachments"

    # Jira requires 'X-Atlassian-Token' to be 'no-check' for attachments
    headers = dict(jira_headers)
    headers["X-Atlassian-Token"] = "no-check"

    with open(pdf_filename, "rb") as f:
        files = {
            "file": (pdf_filename, f, "application/pdf")
        }
        resp = requests.post(attachment_url, headers=headers, files=files, timeout=30)
        resp.raise_for_status()
        print(f"Attached PDF '{pdf_filename}' to issue {issue_key}.")


def post_comment(jira_site, jira_headers, issue_key, comment_text):
    """
    Post a plain-text comment to the given Jira issue.
    """
    comment_url = f"{jira_site}/rest/api/3/issue/{issue_key}/comment"
    payload = {"body": comment_text}

    resp = requests.post(comment_url, headers=jira_headers, json=payload, timeout=30)
    resp.raise_for_status()
    print(f"Posted comment to {issue_key}.")


def transition_issue_to_done(jira_site, jira_headers, issue_key):
    """
    Transitions the issue to Done using the known ID 10010.
    """
    transitions_url = f"{jira_site}/rest/api/3/issue/{issue_key}/transitions"
    payload = {
        "transition": {
            "id": "10010"
        }
    }
    post_resp = requests.post(transitions_url, headers=jira_headers, json=payload, timeout=30)
    post_resp.raise_for_status()
    print(f"Issue {issue_key} transitioned to Done via transition ID 10010.")


def main():
    # ---------------------------
    # 1) Read environment vars
    # ---------------------------
    jira_site = os.environ.get("JIRA_SITE") or "https://prudential-ps.atlassian.net"
    basic_auth = os.environ.get("BASIC_AUTH")  # e.g. "Basic <base64string>"
    bearer_token = os.environ.get("BEARER_TOKEN")  # used for the admin API
    project_key = os.environ.get("PROJECT_KEY")
    issue_key = os.environ.get("ISSUE_KEY")
    org_id = os.environ.get("ORG_ID", "b4235a52-bd04-12a0-j718-68bd06255171")

    if not all([jira_site, basic_auth, bearer_token, project_key, issue_key, org_id]):
        raise ValueError(
            "Missing one or more required env vars: "
            "JIRA_SITE, BASIC_AUTH, BEARER_TOKEN, PROJECT_KEY, ISSUE_KEY, ORG_ID"
        )

    jira_headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json"
    }

    # ---------------------------
    # 2) Collect group members
    # ---------------------------
    group_managers = f"ATLASSIAN-{project_key}-MANAGERS"
    group_contributors_int = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_contributors_ext = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_viewers_int = f"ATLASSIAN-{project_key}-VIEWERS"
    group_viewers_ext = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    managers = get_users_in_group(jira_site, jira_headers, group_managers)
    contrib_int = get_users_in_group(jira_site, jira_headers, group_contributors_int)
    contrib_ext = get_users_in_group(jira_site, jira_headers, group_contributors_ext)
    view_int = get_users_in_group(jira_site, jira_headers, group_viewers_int)
    view_ext = get_users_in_group(jira_site, jira_headers, group_viewers_ext)

    # Combine certain lists
    all_contributors = contrib_int + contrib_ext
    all_viewers = view_int + view_ext

    # ---------------------------
    # 3) Fetch Emails in Batches
    # ---------------------------
    unique_account_ids = {
        u["accountId"]
        for u in (managers + all_contributors + all_viewers)
        if u["accountId"]
    }
    print(f"Found {len(unique_account_ids)} unique accountIds to fetch emails for...")

    account_ids_list = list(unique_account_ids)
    email_map = fetch_emails_in_batches(org_id, bearer_token, account_ids_list)

    # Attach the email address to each user
    def attach_email(user_list):
        for user in user_list:
            acct_id = user.get("accountId")
            if acct_id:
                user["emailAddress"] = email_map.get(acct_id, "")

    attach_email(managers)
    attach_email(all_contributors)
    attach_email(all_viewers)

    # ---------------------------
    # 4) Generate & Attach PDF
    # ---------------------------
    pdf_filename = f"{issue_key}-UserList.pdf"
    generate_pdf_from_data(pdf_filename, managers, all_contributors, all_viewers)
    attach_pdf_to_issue(jira_site, jira_headers, issue_key, pdf_filename)

    # ---------------------------
    # 5) Post short comment
    # ---------------------------
    short_comment = "The current Project Members have been attached."
    post_comment(jira_site, jira_headers, issue_key, short_comment)

    # ---------------------------
    # 6) Transition to Done (ID = 10010)
    # ---------------------------
    transition_issue_to_done(jira_site, jira_headers, issue_key)

    print(f"Successfully attached PDF, commented, and transitioned {issue_key} to Done.")


if __name__ == "__main__":
    main()
