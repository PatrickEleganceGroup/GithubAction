import os
import requests
import json
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


def generate_pdf_with_tables(pdf_filename, managers, contributors, viewers, user_groups):
    """
    Creates a PDF with three tables: Managers, Contributors, and Viewers.
    Columns: Name | Email | Groups
    Uses fpdf to build a simple table-based layout.
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Helper: build a table for one user list
    def create_table(title, user_list):
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, title, ln=True)
        pdf.set_font("Arial", "B", 11)

        # Table header
        pdf.cell(50, 8, "Name", border=1)
        pdf.cell(60, 8, "Email", border=1)
        pdf.cell(0, 8, "Groups", border=1, ln=1)

        pdf.set_font("Arial", size=11)

        # Sort users by displayName for consistency
        sorted_users = sorted(user_list, key=lambda x: x.get("displayName", "").lower())

        for u in sorted_users:
            display_name = u.get("displayName") or ""
            acct_id = u.get("accountId", "")
            email = u.get("emailAddress", "") or acct_id

            # Convert the user's group set to a comma-separated string
            groups_set = user_groups.get(acct_id, set())
            group_str = ", ".join(sorted(groups_set))

            # Name cell
            pdf.cell(50, 8, display_name[:30], border=1)  # just in case line is long
            # Email cell
            pdf.cell(60, 8, email[:40], border=1)
            # Groups cell (can be wide, so we use '0' width so it extends to the end)
            pdf.cell(0, 8, group_str, border=1, ln=1)

        pdf.ln(10)  # blank space after each table

    # Build the tables
    create_table("Managers", managers)
    create_table("Contributors", contributors)
    create_table("Viewers", viewers)

    pdf.output(pdf_filename)
    print(f"Generated PDF: {pdf_filename}")


def upload_temp_file_jsm(jira_site, basic_auth, service_desk_id, pdf_filename):
    """
    1) Uploads a local file as a *temporary* attachment to the given service desk ID.
    2) Returns a list of 'temporaryAttachmentId' strings that can be used in a second request.
    Endpoint: POST /rest/servicedeskapi/servicedesk/{serviceDeskId}/attachTemporaryFile
    """
    url = f"{jira_site}/rest/servicedeskapi/servicedesk/{service_desk_id}/attachTemporaryFile"
    headers = {
        "Authorization": basic_auth,
        "X-Atlassian-Token": "no-check",
        "Accept": "application/json"
    }

    with open(pdf_filename, "rb") as f:
        files = {
            "file": (pdf_filename, f, "application/pdf")
        }
        resp = requests.post(url, headers=headers, files=files, timeout=30)

    resp.raise_for_status()
    data = resp.json()

    temp_ids = []
    for item in data.get("temporaryAttachments", []):
        temp_ids.append(item["temporaryAttachmentId"])

    print(f"Uploaded file '{pdf_filename}' as temporary attachment(s): {temp_ids}")
    return temp_ids


def attach_temp_file_to_request(jira_site, basic_auth, issue_key, temp_attachment_ids, comment_text, public=True):
    """
    Permanently attaches the *temporary* file(s) to a JSM request, and adds a comment in the same request.
    Endpoint: POST /rest/servicedeskapi/request/{issueIdOrKey}/attachment
    """
    url = f"{jira_site}/rest/servicedeskapi/request/{issue_key}/attachment"
    headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "additionalComment": {
            "body": comment_text
        },
        "public": public,
        "temporaryAttachmentIds": temp_attachment_ids
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    print(f"Successfully attached PDF and added comment to {issue_key}.")


def main():
    # ------------------------------------------------
    # 1) Read environment vars (adjust as needed)
    # ------------------------------------------------
    jira_site = os.environ.get("JIRA_SITE", "https://prudential-ps.atlassian.net")
    basic_auth = os.environ.get("BASIC_AUTH")  # e.g. "Basic <base64string>"
    bearer_token = os.environ.get("BEARER_TOKEN")  # used for the admin API
    project_key = os.environ.get("PROJECT_KEY")    # e.g. "PT"
    issue_key = os.environ.get("ISSUE_KEY")        # e.g. "PT-299"
    org_id = os.environ.get("ORG_ID", "b4235a52-bd04-12a0-j718-68bd06255171")

    # Hardcoded JSM desk ID = 6 (based on your listing). 
    service_desk_id = 6

    if not all([jira_site, basic_auth, bearer_token, project_key, issue_key, org_id]):
        raise ValueError(
            "Missing one or more required env vars: "
            "JIRA_SITE, BASIC_AUTH, BEARER_TOKEN, PROJECT_KEY, ISSUE_KEY, ORG_ID"
        )

    # ------------------------------------------------
    # 2) Collect group members from each group
    # ------------------------------------------------
    jira_headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json"
    }

    # Define the group names
    group_managers      = f"ATLASSIAN-{project_key}-MANAGERS"
    group_contrib_int   = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_contrib_ext   = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_view_int      = f"ATLASSIAN-{project_key}-VIEWERS"
    group_view_ext      = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    managers = get_users_in_group(jira_site, jira_headers, group_managers)
    contrib_int = get_users_in_group(jira_site, jira_headers, group_contrib_int)
    contrib_ext = get_users_in_group(jira_site, jira_headers, group_contrib_ext)
    view_int = get_users_in_group(jira_site, jira_headers, group_view_int)
    view_ext = get_users_in_group(jira_site, jira_headers, group_view_ext)

    # Combine for final display
    all_contributors = contrib_int + contrib_ext
    all_viewers = view_int + view_ext

    # ------------------------------------------------
    # 3) Build a dictionary of {accountId -> set of group names}
    # ------------------------------------------------
    user_groups = {}

    def add_group_name(user_list, full_group_name):
        for u in user_list:
            acct_id = u.get("accountId")
            if not acct_id:
                continue
            if acct_id not in user_groups:
                user_groups[acct_id] = set()
            user_groups[acct_id].add(full_group_name)

    add_group_name(managers,        group_managers)
    add_group_name(contrib_int,     group_contrib_int)
    add_group_name(contrib_ext,     group_contrib_ext)
    add_group_name(view_int,        group_view_int)
    add_group_name(view_ext,        group_view_ext)

    # ------------------------------------------------
    # 4) Fetch Emails in Batches
    # ------------------------------------------------
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

    # ------------------------------------------------
    # 5) Generate PDF with tables (Managers, Contributors, Viewers)
    # ------------------------------------------------
    # PDF name uses the PROJECT_KEY, not the issue key
    pdf_filename = f"{project_key}-UserList.pdf"
    generate_pdf_with_tables(pdf_filename, managers, all_contributors, all_viewers, user_groups)

    # ------------------------------------------------
    # 6) Two-step attachment flow in JSM
    # ------------------------------------------------
    # A) Upload as temporary
    temp_ids = upload_temp_file_jsm(jira_site, basic_auth, service_desk_id, pdf_filename)

    # B) Permanently attach + single comment
    comment_text = "The current Project Members have been attached with group information."
    attach_temp_file_to_request(jira_site, basic_auth, issue_key, temp_ids, comment_text, public=True)

    print(f"Done. PDF '{pdf_filename}' attached to {issue_key} with user groups in a table.")


if __name__ == "__main__":
    main()
