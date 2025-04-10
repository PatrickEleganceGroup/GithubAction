import os
import requests
import json
import math
from fpdf import FPDF

# ----------------- Helper for text conversion ------------------
def to_latin1(text):
    """Converts a Unicode string to Latin-1, replacing unsupported characters."""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("latin-1", errors="replace").decode("latin-1")

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
                "displayName": to_latin1(user.get("displayName", ""))
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
                email_map[acct_id] = to_latin1(email)
    return email_map

# --------------- PDF Table Helpers (using built-in Helvetica) ------------------
def get_text_lines(pdf, text, width):
    """
    Splits text into a list of lines that fit within 'width' using simple word wrap.
    The text is first converted to Latin-1.
    """
    text = to_latin1(text)
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
    Draws a table row with wrapped text.
    All cells in the row are padded so they all have the same height.
    Checks for available space on the current page and adds a new page if needed.
    """
    # Calculate line counts per cell and the maximum
    cell_line_counts = [len(get_text_lines(pdf, cell, w - 2)) for cell, w in zip(row, col_widths)]
    max_lines = max(cell_line_counts) if cell_line_counts else 1
    row_height = max_lines * line_height

    # If not enough space on current page, add a page.
    if pdf.get_y() + row_height > pdf.page_break_trigger:
        pdf.add_page()

    x_start = pdf.get_x()
    y_start = pdf.get_y()
    
    # Write each cell's wrapped text with padding.
    for i, cell in enumerate(row):
        x = pdf.get_x()
        padded_lines = get_text_lines(pdf, cell, col_widths[i] - 2)
        padded_lines += [""] * (max_lines - len(padded_lines))
        cell_text = "\n".join(padded_lines)
        pdf.multi_cell(col_widths[i], line_height, cell_text, border=0)
        pdf.set_xy(x + col_widths[i], y_start)
    
    # Draw borders for each cell.
    x = x_start
    for w in col_widths:
        pdf.rect(x, y_start, w, row_height)
        x += w
    pdf.set_xy(x_start, y_start + row_height)

def draw_table_header(pdf, headers, col_widths, line_height):
    """
    Draws the table header row in bold.
    """
    pdf.set_font("Helvetica", "B", 10)
    draw_table_row(pdf, headers, col_widths, line_height)
    pdf.set_font("Helvetica", "", 10)

def generate_pdf_with_wrapping_tables(pdf_filename, managers, contributors, viewers, user_groups):
    """
    Creates a PDF with sections for Managers, Contributors, and Viewers formatted as tables.
    Each table has columns: Name, Email, Groups.
    Uses the built-in Helvetica font with text converted to Latin-1.
    """
    pdf = FPDF()
    pdf.add_page()
    # Set a lower bottom margin to reduce extra blank pages.
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.set_font("Helvetica", "", 10)
    
    # Define column widths and line height.
    col_widths = [45, 55, 90]
    line_height = 6

    def section_table(title, users):
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, to_latin1(title), ln=True)
        pdf.set_font("Helvetica", "", 10)
        header = ["Name", "Email", "Groups"]
        draw_table_header(pdf, header, col_widths, line_height)
        # Sort users alphabetically by displayName.
        sorted_users = sorted(users, key=lambda u: u.get("displayName", "").lower())
        for u in sorted_users:
            name = u.get("displayName", "")
            acct_id = u.get("accountId", "")
            email = u.get("emailAddress", "") or acct_id
            groups = user_groups.get(acct_id, set())
            group_str = ", ".join(sorted(groups))
            draw_table_row(pdf, [name, email, group_str], col_widths, line_height)
        # Use a smaller vertical gap after each table.
        pdf.ln(3)

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
        "additionalComment": {"body": to_latin1(comment_text)},
        "public": public,
        "temporaryAttachmentIds": temp_attachment_ids
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    print(f"Successfully attached PDF and added comment to {issue_key}.")

# ----------------- Issue Transition ----------------
def transition_issue_to_done(jira_site, basic_auth, issue_key, transition_id="121"):
    """
    Transitions the issue using the provided transition ID and sets the Resolution field to "Done".
    Sends payload:
      {
        "transition": {"id": "121"},
        "fields": {"resolution": {"name": "Done"}}
      }
    """
    url = f"{jira_site}/rest/api/3/issue/{issue_key}/transitions"
    payload = {"transition": {"id": transition_id}, "fields": {"resolution": {"name": "Done"}}}
    headers = {"Authorization": basic_auth, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        print("Transition error:", resp.text)
        raise
    print(f"Issue {issue_key} transitioned using transition ID {transition_id} with Resolution set to Done.")

# ----------------- Main ----------------
def main():
    # Read environment variables; these need to be set in your GitHub Actions environment.
    jira_site = os.environ.get("JIRA_SITE", "https://prudential-ps.atlassian.net")
    basic_auth = os.environ.get("BASIC_AUTH")          # e.g., "Basic <base64string>"
    bearer_token = os.environ.get("BEARER_TOKEN")        # Used for the admin API
    project_key = os.environ.get("PROJECT_KEY")          # e.g., "GTP00"
    issue_key = os.environ.get("ISSUE_KEY")              # e.g., "GTP00-123"
    org_id = os.environ.get("ORG_ID", "b4235a52-bd04-12a0-j718-68bd06255171")
    # Hardcoded JSM service desk ID (from your listing, e.g., 6)
    service_desk_id = 6

    if not all([jira_site, basic_auth, bearer_token, project_key, issue_key, org_id]):
        raise ValueError("Missing one or more required env vars: JIRA_SITE, BASIC_AUTH, BEARER_TOKEN, PROJECT_KEY, ISSUE_KEY, ORG_ID")
    
    jira_headers = {"Authorization": basic_auth, "Content-Type": "application/json"}

    # Collect group members.
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

    # Build a dictionary mapping accountId -> set of groups.
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

    # Fetch Emails.
    unique_ids = {u["accountId"] for u in (managers + all_contributors + all_viewers) if u.get("accountId")}
    print(f"Found {len(unique_ids)} unique accountIds.")
    account_ids = list(unique_ids)
    email_map = fetch_emails_in_batches(org_id, bearer_token, account_ids)
    def attach_email(user_list):
        for u in user_list:
            acct_id = u.get("accountId")
            if acct_id:
                u["emailAddress"] = to_latin1(email_map.get(acct_id, ""))
    attach_email(managers)
    attach_email(all_contributors)
    attach_email(all_viewers)

    # Generate PDF; filename uses the project key (e.g., "GTP00-UserList.pdf")
    pdf_filename = f"{project_key}-UserList.pdf"
    generate_pdf_with_wrapping_tables(pdf_filename, managers, all_contributors, all_viewers, user_groups)

    # JSM Attachment Flow: Upload PDF as temporary and attach with a comment.
    temp_ids = upload_temp_file_jsm(jira_site, basic_auth, service_desk_id, pdf_filename)
    comment_text = "The current Project Members have been attached with group info in a table."
    attach_temp_file_to_request(jira_site, basic_auth, issue_key, temp_ids, comment_text, public=True)

    # Transition the issue: use transition ID "121" and set Resolution = Done.
    transition_issue_to_done(jira_site, basic_auth, issue_key, transition_id="121")

    print(f"Done. PDF '{pdf_filename}' attached to {issue_key} and the issue transitioned to Done with Resolution set.")

if __name__ == "__main__":
    main()
