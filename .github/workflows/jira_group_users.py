import os
import requests
import json
import math
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


# -----------------------------------------------------------------------------
# Functions for building a properly-wrapping table with FPDF
# -----------------------------------------------------------------------------

def nb_lines(pdf, text, col_width):
    """
    Approximate how many lines of text 'text' will take when wrapped to 'col_width'.
    Uses the current font in 'pdf' to measure text width.
    A small adjustment is subtracted from col_width to account for cell borders/margins.
    """
    effective_width = col_width - 4  # a small margin for cell edges
    if effective_width <= 0:
        return 1
    text_width = pdf.get_string_width(text)
    # Each full 'effective_width' is 1 line
    lines = text_width / float(effective_width)
    return math.ceil(lines)


def table_row(pdf, col_texts, col_widths, line_height=6):
    """
    Prints a row with wrapping text in each column.
    col_texts: list of strings for each column
    col_widths: list of widths for each column
    line_height: height of each text line
    """
    # 1) Calculate the max number of lines needed by any column in this row
    max_num_lines = 1
    for i, text in enumerate(col_texts):
        lines_needed = nb_lines(pdf, text, col_widths[i])
        if lines_needed > max_num_lines:
            max_num_lines = lines_needed

    row_height = max_num_lines * line_height
    x0 = pdf.get_x()
    y0 = pdf.get_y()

    # 2) Print each column's text with multi_cell, but keep them on the same row
    for i, text in enumerate(col_texts):
        w = col_widths[i]
        # Save the current X position for this col
        current_x = pdf.get_x()
        current_y = pdf.get_y()

        # Multi-cell for wrapping within this column
        pdf.multi_cell(w, line_height, text, border=1, align='L')

        # Move the cursor to the right edge of this column,
        # so the next column starts there
        pdf.set_xy(current_x + w, current_y)

    # 3) Now move down to the next line (lowest point of this row)
    pdf.set_xy(x0, y0 + row_height)


def table_header(pdf, headers, col_widths, line_height=6):
    """
    Prints a header row in bold. 
    """
    pdf.set_font("Arial", style="B", size=10)
    table_row(pdf, headers, col_widths, line_height=line_height)
    pdf.set_font("Arial", size=10)


def generate_pdf_with_wrapping_tables(pdf_filename, managers, contributors, viewers, user_groups):
    """
    Creates a PDF with three sections (Managers, Contributors, Viewers) in table format.
    Columns: Name, Email, Groups
    Proper wrapping for each cell.
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=10)

    # We'll define fixed widths for columns
    col_widths = [45, 55, 90]  # total ~ 190, fits on A4 (210mm wide minus margins)
    line_height = 6

    def section_table(title, user_list):
        # Title
        pdf.set_font("Arial", style="B", size=14)
        pdf.cell(0, 10, title, ln=True)
        pdf.set_font("Arial", size=10)

        # Header row
        headers = ["Name", "Email", "Groups"]
        table_header(pdf, headers, col_widths, line_height=line_height)

        # Each user row
        # Sort the users by displayName for consistency
        sorted_users = sorted(user_list, key=lambda x: x.get("displayName", "").lower())

        for u in sorted_users:
            name = u.get("displayName") or ""
            acct_id = u.get("accountId", "")
            email = u.get("emailAddress", "") or acct_id

            # Gather group names from user_groups dict
            group_set = user_groups.get(acct_id, set())
            group_str = ", ".join(sorted(group_set))

            row_texts = [name, email, group_str]
            table_row(pdf, row_texts, col_widths, line_height=line_height)

        pdf.ln(6)  # blank space after the table

    # Create the 3 sections
    section_table("Managers", managers)
    section_table("Contributors", contributors)
    section_table("Viewers", viewers)

    pdf.output(pdf_filename)
    print(f"Generated PDF: {pdf_filename}")


# -----------------------------------------------------------------------------
# Service Desk (JSM) attachment flow
# -----------------------------------------------------------------------------
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
    project_key = os.environ.get("PROJECT_KEY")    # e.g. "EGT00"
    issue_key = os.environ.get("ISSUE_KEY")        # e.g. "EGT00-123"
    org_id = os.environ.get("ORG_ID", "b4235a52-bd04-12a0-j718-68bd06255171")

    # Hardcoded JSM desk ID = 6 (from your listing). 
    service_desk_id = 6

    if not all([jira_site, basic_auth, bearer_token, project_key, issue_key, org_id]):
        raise ValueError(
            "Missing one or more required env vars: "
            "JIRA_SITE, BASIC_AUTH, BEARER_TOKEN, PROJECT_KEY, ISSUE_KEY, ORG_ID"
        )

    jira_headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json"
    }

    # ------------------------------------------------
    # 2) Collect group members from each group
    # ------------------------------------------------
    group_managers    = f"ATLASSIAN-{project_key}-MANAGERS"
    group_contrib_int = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_contrib_ext = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_view_int    = f"ATLASSIAN-{project_key}-VIEWERS"
    group_view_ext    = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    managers = get_users_in_group(jira_site, jira_headers, group_managers)
    contrib_int = get_users_in_group(jira_site, jira_headers, group_contrib_int)
    contrib_ext = get_users_in_group(jira_site, jira_headers, group_contrib_ext)
    view_int = get_users_in_group(jira_site, jira_headers, group_view_int)
    view_ext = get_users_in_group(jira_site, jira_headers, group_view_ext)

    all_contributors = contrib_int + contrib_ext
    all_viewers = view_int + view_ext

    # ------------------------------------------------
    # 3) Build a dictionary of {accountId -> set of group names}
    # ------------------------------------------------
    user_groups = {}

    def add_group_name(user_list, full_group_name):
        for u in user_list:
            acct_id = u.get("accountId")
            if acct_id:
                if acct_id not in user_groups:
                    user_groups[acct_id] = set()
                user_groups[acct_id].add(full_group_name)

    add_group_name(managers,    group_managers)
    add_group_name(contrib_int, group_contrib_int)
    add_group_name(contrib_ext, group_contrib_ext)
    add_group_name(view_int,    group_view_int)
    add_group_name(view_ext,    group_view_ext)

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
    # 5) Generate PDF (with wrapping columns)
    # ------------------------------------------------
    # PDF filename uses the project key, e.g. "EGT00-UserList.pdf"
    pdf_filename = f"{project_key}-UserList.pdf"
    generate_pdf_with_wrapping_tables(pdf_filename, managers, all_contributors, all_viewers, user_groups)

    # ------------------------------------------------
    # 6) Two-step attachment flow in JSM
    # ------------------------------------------------
    # A) Upload as temporary
    temp_ids = upload_temp_file_jsm(jira_site, basic_auth, service_desk_id, pdf_filename)

    # B) Permanently attach + single comment
    comment_text = "The current Project Members have been attached, now with wrapping columns!"
    attach_temp_file_to_request(jira_site, basic_auth, issue_key, temp_ids, comment_text, public=True)

    print(f"Done. PDF '{pdf_filename}' attached to {issue_key} with columns that wrap properly.")


if __name__ == "__main__":
    main()
