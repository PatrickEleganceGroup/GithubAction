import os
import requests

def get_users_in_group(jira_site, basic_auth_header, group_name):
    """
    Retrieve all users for the given Jira group name using pagination.
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
        resp = requests.get(url, headers=basic_auth_header, params=params)
        resp.raise_for_status()

        data = resp.json()
        for user in data.get("values", []):
            display_name = user.get("displayName", "")
            email_addr = user.get("emailAddress", "")
            users.add((display_name, email_addr))

        if data.get("isLast", True):
            break

        start_at += max_results

    return users

def main():
    jira_site = "https://prudential-ps.atlassian.net"
    basic_auth = os.environ.get("BASIC_AUTH")  # Already in form: "Basic dGhpc2lzc2VjcmV0"
    project_key = os.environ.get("PROJECT_KEY")
    issue_key = os.environ.get("ISSUE_KEY")

    if not all([jira_site, basic_auth, project_key, issue_key]):
        raise ValueError(
            "Missing one or more required env vars: JIRA_SITE, BASIC_AUTH, PROJECT_KEY, ISSUE_KEY."
        )

    # Prepare headers
    headers = {
        "Authorization": basic_auth,
        "Content-Type": "application/json"
    }

    # Group names to retrieve
    group_contributors = f"ATLASSIAN-{project_key}-CONTRIBUTORS"
    group_ext_contributors = f"ATLASSIAN-{project_key}-EXTERNAL-CONTRIBUTORS"
    group_managers = f"ATLASSIAN-{project_key}-MANAGERS"
    group_viewers = f"ATLASSIAN-{project_key}-VIEWERS"
    group_ext_viewers = f"ATLASSIAN-{project_key}-EXTERNAL-VIEWERS"

    # Query each group
    managers = get_users_in_group(jira_site, headers, group_managers)
    contributors_internal = get_users_in_group(jira_site, headers, group_contributors)
    contributors_external = get_users_in_group(jira_site, headers, group_ext_contributors)
    viewers_internal = get_users_in_group(jira_site, headers, group_viewers)
    viewers_external = get_users_in_group(jira_site, headers, group_ext_viewers)

    all_contributors = contributors_internal.union(contributors_external)
    all_viewers = viewers_internal.union(viewers_external)

    # Build comment content with three sections
    def format_role_section(role_name, user_set):
        lines = [role_name]  # e.g. "Managers"
        for (display, email) in sorted(user_set):
            lines.append(f"{display}|{email}")
        return "\n".join(lines)

    managers_section = format_role_section("Managers", managers)
    contributors_section = format_role_section("Contributors", all_contributors)
    viewers_section = format_role_section("Viewers", all_viewers)

    final_comment_body = "\n\n".join([managers_section, contributors_section, viewers_section])

    # Post the comment to the Jira issue
    comment_url = f"{jira_site}/rest/api/3/issue/{issue_key}/comment"
    payload = {
        "body": final_comment_body
    }

    post_resp = requests.post(comment_url, headers=headers, json=payload)
    post_resp.raise_for_status()

    print(f"Successfully posted comment to {issue_key}.")

if __name__ == "__main__":
    main()
