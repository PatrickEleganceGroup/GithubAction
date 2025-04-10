import os
import requests
import json
import math
from fpdf import FPDF  # fpdf2 is installed (pip install fpdf2)

# ----------------- Data Collection ------------------

def get_users_in_group(jira_site, basic_auth_header, group_name):
    """
    Retrieve all users from Jira's group/member endpoint.
    Returns a list of dicts with keys: accountId and displayName.
    """
    start_at = 0
    max_results = 50
    users = []
    while True:
        url = f"{jira_site}/rest/api/3/group/member"
        params = {"groupname": group_name, "startAt": start_at, "maxResults": max_results}
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
    Retrieve emails for account IDs in batches.
    Returns a dict { accountId -> email }.
    """
    url = f"https://api.atlassian.com/admin/v1/orgs/{org_id}/users/search"
    headers = {"Authorization": bearer_token, "Content-Type": "application/json"}
    email_map = {}
    chunk_size = 100
    for i in range(0, len(account_ids), chunk_size):
        chunk = account_ids[i: i + chunk_size]
        payload = {"accountIds": chunk, "expand": ["EMAIL"]}
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("data", []):
            acct_id = entry.get("accountId")
            email = entry.get("email") or ""
            if acct_id:
                email_map[acct_id] = email
    return email_map

# --------------- PDF Table Helpers (using fpdf2 with Unicode) ------------------

def get_text_lines(pdf, text, width):
    """
    Split text into a list of lines that fit within 'width' using simple word wrap.
    """
    words = text.split(' ')
    lines = []
    current_line = ""
    for word in words:
        test_line = word if current_line == "" else current_line + " " + word
        if pdf.get_string_width(test_line) > width:
            if current_line == "":
                lines.append(word)
                current_line = ""
            else:
                lines.append(current_line)
                current_line = word
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    return lines

def draw_table_row(pdf, row, col_widths, line_height):
    """
    Draws a table row. Each cell’s text is wrapped and padded so that every cell in the row gets the same height.
    """
    # Split each cell's text into lines
    cell_lines = [get_text_lines(pdf, cell, w - 2) for cell, w in zip(row, col_widths)]
    max_lines = max(len(lines) for lines in cell_lines)
    row_height = max_lines * line_height
    x_start = pdf.get_x()
    y_start = pdf.get_y()
    
    # For each cell, prepare the full text with extra blank lines if necessary
    for i, lines in enumerate(cell_lines):
        x = pdf.get_x()
        y = pdf.get_y()
        padded_lines = lines + [""] * (max_lines - len(lines))
        cell_text = "\n".join(padded_lines)
        pdf.multi_cell(col_widths[i], line_height, cell_text, border=0)
        pdf.set_xy(x + col_widths[i], y_start)
    
    # Draw borders for each cell in the row
    x = x_start
    for w in col_widths:
        pdf.rect(x, y_start, w, row_height)
        x += w
    pdf.set_xy(x_start, y_start + row_height)

def draw_table_header(pdf, headers, col_widths, line_height):
    """
    Draws the table header row in bold.
    """
    pdf.set_font("DejaVu", "B", 10)
    draw_table_row(pdf, headers, col_widths, line_height)
    pdf.set_font("DejaVu", "", 10)

def generate_pdf_with_wrapping_tables(pdf_filename, managers, contributors, viewers, user_groups):
    """
    Creates a PDF with sections for Managers, Contributors, and Viewers formatted as tables.
    Each table has columns: Name, Email, Groups.
    Uses a Unicode font (DejaVu Sans) via fpdf2.
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Add and set a Unicode font (ensure DejaVuSans.ttf is in your working directory)
    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
    pdf.set_font("DejaVu", "", 10)
    
    # Define column widths (should fit within A4 width) and line height
    col_widths = [45, 55, 90]
    line_height = 6

    def section_table(title, users):
        pdf.set_font("DejaVu", "B", 12)
        pdf.cell(0, 10, title, ln=True)
        pdf.set_font("DejaVu", "", 10)
        header = ["Name", "Email", "Groups"]
        draw_table_header(pdf, header, col_widths, line_height)
        # Sort users alphabetically by display name
        sorted_users = sorted(users, key=lambda u: u.get("displayName", "").lower())
        for u in sorted_users:
            name = u.get("displayName", "")
            acct_id = u.get("accountId", "")
            email = u.get("emailAddress", "") or acct_id
            groups = user_groups.get(acct_id, set())
            group_str = ", ".join(sorted(groups))
            draw_table_row(pdf, [name, email, group_str], col_widths, line_height)
        pdf.ln(6)

    section_table("Managers", managers)
    section_table("Contributors", contributors)
    section_table("Viewers", viewers)
    
    pdf.output(pdf_filename)
    print(f"Generated PDF: {pdf_filename}")

# ----------------- Service Desk Attachment ----------------

def upload_temp_file_jsm(jira_site, basic_auth, service_desk_id, pdf_filename):
    """
    Uploads the file as a temporary attachment to the given service desk ID.
    Returns a list of temporaryAttachmentId strings.
    """
    url = f"{jira_site}/rest/servicedeskapi/servicedesk/{service_desk_id}/attachTemporaryFile"
    headers = {"Authorization": basic_auth, "X-Atlassian-Token": "no-check", "Accept": "application/json"}
    with open(pdf_filename, "rb") as f:
        files = {"file": (pdf_filename, f, "application/pdf")}
        resp = requests.post(url, headers=headers, files=files, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    temp_ids = [item["temporaryAttachmentId"] for item in data.get("temporaryAttachments", [])]
    print(f"Uploaded '{pdf_filename}' as temporary attachment(s): {temp_ids}")
    return temp_ids

def attach_temp_file_to_request(jira_site, basic_auth, issue_key, temp_attachment_ids, comment_text, public=True):
    """
    Permanently attaches the temporary file(s) to a JSM request and adds a comment.
    """
    url = f"{jira_site}/rest/servicedeskapi/request/{issue_key}/attachment"
    headers = {"Authorization": basic_auth, "Content-Type": "application/json", "Accept": "application/json"}
    payload = {
        "additionalComment": {"body": comment_text},
        "public": public,
        "temporaryAttachmentIds": temp_attachment_ids
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    print(f"Successfully attached PDF and added comment to {issue_key}.")

# ----------------- Issue Transition ----------------

def transition_issue_to_done(jira_site, basic_auth, issue_key, transition_id="5"):
    """
    Transitions the issue using the provided transition ID.
    Sends the payload as: { "transition": { "id": "5" } }
    If a 400 error is returned, prints the response text.
    """
    url = f"{jira_site}/rest/api/3/issue/{issue_key}/transitions"
    payload = {"transition": {"id": transition_id}}
    headers = {"Authorization": basic_auth, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        print("Transition error:", resp.text)
        raise
    print(f"Issue {issue_key} transitioned using transition ID {transition_id}.")

# ----------------- Main ----------------

def main():
    # Read environment variables (adjust or set these in your environment)
    jira_site = os.environ.get("JIRA_SITE", "https://prudential-ps.atlassian.net")
    basic_auth = os.environ.get("BASIC_AUTH")          # e.g., "Basic <base64string>"
    bearer_token = os.environ.get("BEARER_TOKEN")        # Used for the admin API
    project_key = os.environ.get("PROJECT_KEY")          # e.g., "EGT00"
    issue_key = os.environ.get("ISSUE_KEY")              # e.g., "EGT00-123"
    org_id = os.environ.get("ORG_ID", "b4235a52-bd04-12a0-j718-68bd06255171")
    # Hardcoded JSM service desk ID for your project (from your listing, e.g., 6)
    service_desk_id = 6

    if not all([jira_site, basic_auth, bearer_token, project_key, issue_key, org_id]):
        raise ValueError("Missing one or more required env vars: JIRA_SITE, BASIC_AUTH, BEARER_TOKEN, PROJECT_KEY, ISSUE_KEY, ORG_ID")
    
    jira_headers = {"Authorization": basic_auth, "Content-Type": "application/json"}

    # Collect group members
    group_managers = f"ATLASSIAN-{project_key}-MANAGERS"
    group_contrib = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_extern = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_view = f"ATLASSIAN-{project_key}-VIEWERS"
    group_view_ext = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"
    
    managers = get_users_in_group(jira_site, jira_headers, group_managers)
    contrib = get_users_in_group(jira_site, jira_headers, group_contrib)
    extern = get_users_in_group(jira_site, jira_headers, group_extern)
    view = get_users_in_group(jira_site, jira_headers, group_view)
    view_ext = get_users_in_group(jira_site, jira_headers, group_view_ext)
    
    all_contributors = contrib + extern
    all_viewers = view + view_ext

    # Build a dictionary mapping each accountId to its set of groups
    user_groups = {}
    def add_group(user_list, group_name):
        for u in user_list:
            acct_id = u.get("accountId")
            if acct_id:
                user_groups.setdefault(acct_id, set()).add(group_name)
    add_group(managers, group_managers)
    add_group(contrib, group_contrib)
    add_group(extern, group_extern)
    add_group(view, group_view)
    add_group(view_ext, group_view_ext)

    # Fetch Emails
    unique_ids = {u["accountId"] for u in (managers + all_contributors + all_viewers) if u.get("accountId")}
    print(f"Found {len(unique_ids)} unique accountIds.")
    account_ids = list(unique_ids)
    email_map = fetch_emails_in_batches(org_id, bearer_token, account_ids)
    def attach_email(user_list):
        for u in user_list:
            acct_id = u.get("accountId")
            if acct_id:
                u["emailAddress"] = email_map.get(acct_id, "")
    attach_email(managers)
    attach_email(all_contributors)
    attach_email(all_viewers)

    # Generate PDF (the filename uses the project key, e.g., "EGT00-UserList.pdf")
    pdf_filename = f"{project_key}-UserList.pdf"
    generate_pdf_with_wrapping_tables(pdf_filename, managers, all_contributors, all_viewers, user_groups)

    # JSM Attachment Flow: Upload as temporary and then attach with a comment.
    temp_ids = upload_temp_file_jsm(jira_site, basic_auth, service_desk_id, pdf_filename)
    comment_text = "The current Project Members have been attached with group info in a table."
    attach_temp_file_to_request(jira_site, basic_auth, issue_key, temp_ids, comment_text, public=True)

    # Transition the issue (update the transition_id as required; here we use "5")
    transition_issue_to_done(jira_site, basic_auth, issue_key, transition_id="5")

    print(f"Done. PDF '{pdf_filename}' attached to {issue_key} and the issue transitioned to Done.")

if __name__ == "__main__":
    main()
