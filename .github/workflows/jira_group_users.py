import os
import json
import requests

def get_users_in_group(jira_site, headers, group_name):
    """
    Example stub for pagination logic. 
    Returns a set of tuples (displayName, emailAddress).
    """
    start_at = 0
    max_results = 50
    users = set()

    while True:
        url = f"{jira_site}/rest/api/3/group/member"
        params = {
            "groupname": group_name,
            "startAt": start_at,
            "maxResults": max_results
        }
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()

        data = resp.json()
        for user in data.get("values", []):
            display_name = user.get("displayName", "")
            email_addr = user.get("emailAddress", "")
            users.add((display_name, email_addr))

        # If isLast is True, no more pages
        if data.get("isLast", True):
            break

        start_at += max_results

    return users

def main():
    # Hard-coded site or read from env, your choice
    jira_site = os.environ.get("JIRA_SITE", "https://prudential-ps.atlassian.net")
    basic_auth = os.environ.get("BASIC_AUTH")  # e.g. "Basic <encoded string>"
    project_key = os.environ.get("PROJECT_KEY")
    issue_key = os.environ.get("ISSUE_KEY")

    # Make sure everything is provided
    if not all([jira_site, basic_auth, project_key, issue_key]):
        raise ValueError("Missing one or more required env vars: "
                         "JIRA_SITE, BASIC_AUTH, PROJECT_KEY, ISSUE_KEY.")

    headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json"
    }

    # Example group names
    group_contributors = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_ext_contributors = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_managers = f"ATLASSIAN-{project_key}-MANAGERS"
    group_viewers = f"ATLASSIAN-{project_key}-VIEWERS"
    group_ext_viewers = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    # Retrieve sets of users
    managers = get_users_in_group(jira_site, headers, group_managers)
    contributors_internal = get_users_in_group(jira_site, headers, group_contributors)
    contributors_external = get_users_in_group(jira_site, headers, group_ext_contributors)
    viewers_internal = get_users_in_group(jira_site, headers, group_viewers)
    viewers_external = get_users_in_group(jira_site, headers, group_ext_viewers)

    all_contributors = contributors_internal.union(contributors_external)
    all_viewers = viewers_internal.union(viewers_external)

    # Create the ADF comment body
    adf_comment_body = create_adf_comment_body(
        managers=managers,
        contributors=all_contributors,
        viewers=all_viewers
    )

    # Prepare the payload
    comment_url = f"{jira_site}/rest/api/3/issue/{issue_key}/comment"
    payload = {
        "body": adf_comment_body
    }

    # Debug prints before sending
    print("== DEBUG INFO ==")
    print("Constructed comment URL:", comment_url)
    print("Payload being sent (ADF doc):")
    print(json.dumps(payload, indent=2))

    # Send the request
    response = requests.post(comment_url, headers=headers, json=payload)

    # Debug the response
    print("Response status code:", response.status_code)
    print("Response text:", response.text)

    # Raise exception if 4xx/5xx
    response.raise_for_status()

    print(f"== SUCCESS: Posted ADF comment to '{issue_key}'! ==")

def create_adf_comment_body(managers, contributors, viewers):
    """
    Builds an ADF doc with three sections (Managers, Contributors, Viewers),
    each section containing a heading + table of Name/Email.
    """
    return {
        "type": "doc",
        "version": 1,
        "content": [
            heading_paragraph("Managers"),
            make_user_table(managers),
            heading_paragraph("Contributors"),
            make_user_table(contributors),
            heading_paragraph("Viewers"),
            make_user_table(viewers),
        ]
    }

def heading_paragraph(heading_text):
    """
    Returns a simple paragraph with text (e.g. 'Managers').
    """
    return {
        "type": "paragraph",
        "content": [
            {
                "type": "text",
                "text": heading_text
            }
        ]
    }

def make_user_table(users):
    """
    Returns an ADF 'table' node for a set of (displayName, emailAddress) tuples.
    First row is header: Name | Email
    Each subsequent row is one user.
    """
    rows = []
    # Header row
    rows.append({
        "type": "tableRow",
        "content": [
            table_cell_paragraph("Name"),
            table_cell_paragraph("Email")
        ]
    })
    # One row per user
    for (display_name, email) in sorted(users):
        rows.append({
            "type": "tableRow",
            "content": [
                table_cell_paragraph(display_name),
                table_cell_paragraph(email)
            ]
        })

    return {
        "type": "table",
        "content": rows
    }

def table_cell_paragraph(cell_text):
    """
    Creates a single table cell with one paragraph of text.
    """
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
