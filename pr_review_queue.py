#!/usr/bin/python3

"""
Small bot to create a pull request review queue on Slack
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
import yaml
from cryptography.fernet import Fernet
from ghapi.all import GhApi
from slack_sdk.webhook import WebhookClient

# To only create the decrypted Slack nick tuple list once we make it a
# global variable
slack_nicks = []

# Using Slack format
SLACK_FORMAT = True

DEFAULT_ENCODING = os.getenv("DEFAULT_ENCODING", "utf-8")
DEFAULT_JIRA_TIMEOUT_SEC = int(
    os.getenv("DEFAULT_JIRA_TIMEOUT_SEC", f"{5 * 60}"))


def format_link(text, link):
    """Format a link in slack or markdown style"""
    if SLACK_FORMAT:
        return f"<{link}|{text}>"

    return f"[{text}]({link})"


def decrypt(data, key):
    """
    Given a filename (str) and key (bytes), it decrypts the file
    """
    cipher_suite = Fernet(key)
    decrypted_data = []

    for k, v in data.items():
        decrypted_value = cipher_suite.decrypt(v.encode()).decode('utf-8')
        decrypted_data.append((k, decrypted_value))

    return decrypted_data


def decrypt_yaml(file_path, key):
    """
    Open a yaml file with encrypted data and return the decrypted values
    """
    with open(file_path, 'r', encoding=DEFAULT_ENCODING) as file:
        encrypted_data = yaml.safe_load(file)

    decrypted_data = decrypt(encrypted_data, key)
    return decrypted_data


def init_slack_userlist():
    """
    Decrypt and set a global variable holding GitHub logins
    and Slack userids
    """
    # pylint: disable=global-statement
    global slack_nicks
    key = os.getenv('SLACK_NICKS_KEY')
    if key:
        yaml_file_path = "slack_nicks_encrypted.yaml"
        slack_nicks = decrypt_yaml(yaml_file_path, key)
    else:
        print("No key provided to decrypt Slack nicks.")


def get_slack_userid(github_login):
    """
    Return the unencrypted Slack userid
    """
    username = format_link(f"@{github_login}", f"https://github.com/{github_login}")
    if slack_nicks:
        for github_username, slack_userid in slack_nicks:
            if github_username == github_login:
                username = f"<@{slack_userid}>"

    return username


def mask_slack_userids(message):
    """
    Revert the slack userids for debug output to github link
    :param message: the message to be masked
    :return: the same as message but without slack userids
    """
    ret = message
    if slack_nicks:
        for github_username, slack_userid in slack_nicks:
            username = format_link(f"@{github_username}", f"https://github.com/{github_username}")
            ret = ret.replace(f"<@{slack_userid}>", username)
        return ret
    return "no valid slack_nicks - masking full message"


def slack_notify(message: str, dry_run: bool):
    """
    Send notifications to Image Builder's Slack channel
    """
    url = os.getenv('SLACK_WEBHOOK_URL')
    github_server_url = os.getenv('GITHUB_SERVER_URL')
    github_repository = os.getenv('GITHUB_REPOSITORY')
    github_run_id = os.getenv('GITHUB_RUN_ID')
    github_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"

    # Only print the entire message outside of GitHub Actions to avoid
    # leaking Slack userids
    print("--- Message ---")
    if github_run_id:
        print(mask_slack_userids(message))
    else:
        print(message)

    if dry_run:
        print("This is just a dry run, not sending Slack notifications.")
        sys.exit(0)

    if url:
        webhook = WebhookClient(url)
        response = webhook.send(text=f"<{github_url}|pr-review-queue>: {message}")
        assert response.status_code == 200, f"Error {response.status_code}\n{response.body}"
        assert response.body == "ok"
    else:
        print("No Slack webhook supplied.")


def get_last_updated_days(date_str):
    """
    Return the number of days since a PR was last updated
    """
    # Convert Slack's date string to a datetime object
    input_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    current_date = datetime.now(timezone.utc)
    last_updated_days = (current_date - input_date).days

    return last_updated_days


def get_check_runs(github_api, repo, head):
    """
    Return the combined status of GitHub checks as strong and a state emoji
    """
    check_runs = github_api.checks.list_for_ref(repo=repo, ref=head, per_page=100)
    runs = check_runs["check_runs"]
    total_count = check_runs["total_count"]
    successful_runs = 0

    # Both successful and skipped runs count as success
    for run in runs:
        if (run['status'] == "completed" and
                (run['conclusion'] == "success" or
                 run['conclusion'] == "skipped")):
            successful_runs += 1

    if successful_runs == total_count:
        status = "success"
        state = "🟢"
    elif successful_runs < total_count:
        status = "failure"
        state = "🔴"
    else:
        print(f"Warning: something is terribly wrong: successful runs ({successful_runs}) "
              f"should never be more than total runs ({total_count}).")
        sys.exit(1)

    return status, state


def get_commit_status(github_api, repo, pull_request_details):
    """
    Check whether the HEAD commit has passed the CI tests
    """
    head = pull_request_details["head"]
    combined_status = "failure"  # failure by default

    # Check GitHub run status
    check_run_status, state = get_check_runs(github_api, repo, head["sha"])
    # Exit early if there are failed check runs
    if check_run_status == "failure":
        return combined_status, state

    # Check external CI status
    status = github_api.repos.get_combined_status_for_ref(repo=repo, ref=head["sha"])

    if (status.state == "success" and
            check_run_status == "success"):
        state = "🟢"
        combined_status = "success"
    elif status.state == "failure":
        state = "🔴"
    # For simplicity, we consider "pending" as "failure" unless there are check runs
    elif status.state == "pending":
        state = "🟠"
        # Check if the state is not really 'pending' but if there is actually none
        single_status = github_api.repos.list_commit_statuses_for_ref(repo=repo, ref=head["sha"])
        if single_status == []:
            # The combined_status should still be a success if all check runs have passed
            if check_run_status == "success":
                state = "🟢"
                combined_status = "success"
    else:
        state = status.state

    return combined_status, state


def get_archived_repos(github_api, org):
    """
    Return a list of archived or disabled repositories
    """
    res = None

    try:
        res = github_api.repos.list_for_org(org)
    except:  # pylint: disable=bare-except
        print(f"Couldn't get repositories for organisation {org}.")

    archived_repos = []

    if res is not None:
        for repo in res:
            if repo["archived"] is True or repo["disabled"] is True:
                archived_repos.append(repo["name"])

    if archived_repos:
        archived_repos_string = ", ".join(archived_repos)
        print(f"The following repositories are archived or disabled and will be ignored:\n  {archived_repos_string}")

    return archived_repos


def get_pull_request_details(github_api, repo, pull_request):
    """
    Return a pull_request_details object
    """

    pull_request_details = None
    for attempt in range(3):
        try:
            pull_request_details = github_api.pulls.get(repo=repo, pull_number=pull_request["number"])
        except:  # pylint: disable=bare-except
            time.sleep(2)  # avoid API blocking
        else:
            break
    else:
        print(f"Tried {attempt} times to get details for {pull_request.html_url}. Skipping.")

    if pull_request_details is not None:
        return pull_request_details

    print("Couldn't get any pull requests details.")
    sys.exit(1)


def get_review_state(github_api, repo, pull_request, state):
    """
    Iterate over reviews associated with a pull requested and return True if changes have been requested
    """
    reviews = github_api.pulls.list_reviews(repo=repo, pull_number=pull_request["number"])
    review_state = False

    for review in reviews:
        if review["state"] == state:
            review_state = True
            continue

    return review_state


def get_pull_request_properties(github_api, pull_request, org, repo):
    """
    Return a dictionary of all relevant pull request properties
    """
    pr_properties = {}

    pull_request_details = get_pull_request_details(github_api, repo, pull_request)

    pr_properties["number"] = pull_request["number"]
    pr_properties["html_url"] = pull_request.html_url
    pr_properties["title"] = pull_request.title
    pr_properties["org"] = org
    pr_properties["repo"] = repo
    pr_properties["created_at"] = pull_request.created_at
    pr_properties["updated_at"] = pull_request.updated_at
    pr_properties["last_updated_days"] = get_last_updated_days(pull_request.updated_at)
    pr_properties["login"] = get_slack_userid(pull_request.user['login'])
    pr_properties["requested_reviewers"] = pull_request_details["requested_reviewers"]
    pr_properties["additions"] = pull_request_details["additions"]
    pr_properties["deletions"] = pull_request_details["deletions"]
    pr_properties["draft"] = pull_request_details["draft"]
    pr_properties["mergeable"] = pull_request_details["mergeable"]
    pr_properties["rebaseable"] = pull_request_details["rebaseable"]
    pr_properties["mergeable_state"] = pull_request_details["mergeable_state"]
    pr_properties["changes_requested"] = get_review_state(github_api, repo, pull_request, "CHANGES_REQUESTED")
    pr_properties["approved"] = get_review_state(github_api, repo, pull_request, "APPROVED")
    pr_properties["status"], pr_properties["state"] = get_commit_status(github_api, repo, pull_request_details)

    return pr_properties


def get_pull_request_list(github_api, org, repo):
    """
    Return a list of pull requests with their properties
    """
    pull_request_list = []
    res = None
    archived_repos = []

    if repo:
        print(f"Fetching pull requests from one repository: {org}/{repo}")
        query = f"repo:{org}/{repo} type:pr is:open"
        entire_org = False
    else:
        print(f"Fetching pull requests from an entire organisation: {org}")
        query = f"org:{org} type:pr is:open"
        entire_org = True
        archived_repos = get_archived_repos(github_api, org)

    try:
        res = github_api.search.issues_and_pull_requests(q=query, per_page=100, sort="updated", order="asc")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print("Couldn't get any pull requests.", e)

    if res is not None:
        pull_requests = res["items"]
        print(f"{len(pull_requests)} pull requests retrieved.")

        for pull_request in pull_requests:
            if entire_org:  # necessary when iterating over an organisation
                repo = pull_request.repository_url.split('/')[-1]
                if archived_repos and repo in archived_repos:
                    print(f" * Repository '{org}/{repo}' is archived or disabled. Skipping.")
                    continue

            pull_request_props = get_pull_request_properties(github_api, pull_request, org, repo)
            print(f" * Processing {pull_request.html_url} {pull_request_props['state']}")
            pull_request_list.append(pull_request_props)

    return pull_request_list


def generate_jira_link(jira_key):
    """
    Generate a Jira link and verify that it exists
    """
    jira_url = f"https://issues.redhat.com/browse/{jira_key}"
    response = requests.head(jira_url, timeout=DEFAULT_JIRA_TIMEOUT_SEC)
    return f"<{jira_url}|:jira-1992:{jira_key}>" if response.status_code == 200 else jira_key


def find_jira_key(pr_title, pr_html_url):
    """
    Look for a Jira key, when found generate a hyperlink and return the new pr_title_link
    """
    pr_title_link = format_link(pr_title, pr_html_url)

    match = re.match(r"([A-Z]+\-\d+)([: -]+)(.+)", pr_title)
    if match:
        jira_key, separator, title_remainder = match.groups()
        if jira_key:
            pr_title_link = f"{generate_jira_link(jira_key)}{separator}{format_link(title_remainder, pr_html_url)}"

    return pr_title_link


def create_pr_review_queue(pull_request_list):
    """
    Return a filtered list of pull requests according to these criteria:
        1. CI is green
        2. Not a draft
    The resulting pull requests are grouped into four sections:
        1. ‘Needs reviewer’: ping author about finding a reviewer
        2. ‘Needs changes’: ping author to do the changes
        3. ‘Needs review’: ping assigned reviewer/s
        4. ‘Needs conflict resolution’: ping author to rebase/fix conflicts
    """
    needs_reviewer = []
    needs_changes = []
    needs_review = []
    needs_conflict_resolution = []

    for pull_request in pull_request_list:
        pr_title_link = find_jira_key(pull_request['title'], pull_request['html_url'])
        entry = (
            f"*{pull_request['repo']}*: {pr_title_link}"
            f" (+{pull_request['additions']}/-{pull_request['deletions']})"
            f" updated {pull_request['last_updated_days']}d ago"
        )

        if pull_request['status'] == 'success' and not pull_request['draft']:
            # 1. Needs reviewer
            if (not pull_request['changes_requested'] and
                not pull_request['approved'] and
                not pull_request['requested_reviewers'] and
                    pull_request['mergeable_state'] != 'dirty'):
                needs_reviewer.append(entry)
            # 2. Needs changes
            elif (pull_request['changes_requested'] and
                  pull_request['mergeable_state'] != 'dirty'):
                needs_changes.append(f"{entry} needs changes by {pull_request['login']}")
            # 3. Needs review
            elif (not pull_request['changes_requested'] and
                  not pull_request['approved'] and
                  pull_request['requested_reviewers'] and
                  pull_request['mergeable_state'] != 'dirty'):
                reviewers = ', '.join(
                    get_slack_userid(requested_reviewer['login'])
                    for requested_reviewer in pull_request['requested_reviewers'])
                needs_review.append(f"{entry} {reviewers}")
            # 4. Needs conflict resolution or rebasing
            elif (not pull_request['changes_requested'] and
                  (pull_request['mergeable_state'] in {'dirty', 'behind'})):
                needs_conflict_resolution.append(f"{entry} {pull_request['login']}")

    return needs_reviewer, needs_changes, needs_review, needs_conflict_resolution


def main():
    """Create a pull request review queue"""
    parser = argparse.ArgumentParser(allow_abbrev=False)

    # GhApi() supports pulling the token out of the env - so if it's
    # set - we don't need to force this in the params
    if os.getenv("GITHUB_TOKEN"):
        token_arg_required = False
    else:
        token_arg_required = True

    parser.add_argument("--github-token", help="Set a token for github.com", required=token_arg_required)
    parser.add_argument("--org", help="Set an organisation on github.com", required=True)
    parser.add_argument("--repo", help="Set a repo in `--org` on github.com", required=False)
    parser.add_argument("--queue", help="Create a review queue", default=True,
                        action=argparse.BooleanOptionalAction)
    parser.add_argument("--slack-format", help="Generate slack format, otherwise use markdown", default=True,
                        action=argparse.BooleanOptionalAction)
    parser.add_argument("--dry-run", help="Don't send Slack notifications", default=False,
                        action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    # pylint: disable=global-statement
    global SLACK_FORMAT
    SLACK_FORMAT = args.slack_format

    github_api = GhApi(owner=args.org, token=args.github_token)

    init_slack_userlist()
    pull_request_list = get_pull_request_list(github_api, args.org, args.repo)

    if args.queue:
        needs_reviewer, needs_changes, needs_review, needs_conflict_resolution = create_pr_review_queue(
            pull_request_list)
        if (not needs_reviewer and
                not needs_changes and
                not needs_review and
                not needs_conflict_resolution
                ):
            print("No pull requests found that match our criteria. Exiting.")
            sys.exit(0)

        message = prepare_message(needs_changes, needs_conflict_resolution, needs_review, needs_reviewer)

        slack_notify(message, args.dry_run)


def prepare_message(needs_changes, needs_conflict_resolution, needs_review, needs_reviewer):
    """Return the message in either slack or markdown style"""
    if SLACK_FORMAT:
        message = "Good morning, image builders! :meow_wave:"
        if needs_reviewer:
            message += "\n\n:frog-derp: *We need a reviewer*\n  • " + \
                       "\n  • ".join(needs_reviewer)
        if needs_changes:
            message += "\n\n:changes_requested: *We need changes*\n  • " + \
                       "\n  • ".join(needs_changes)
        if needs_review:
            message += "\n\n:frog-flushed: *We need a review*\n  • " + \
                       "\n  • ".join(needs_review)
        if needs_conflict_resolution:
            message += "\n\n:expressionless-meow: *Update required*\n  • " + \
                       "\n  • ".join(needs_conflict_resolution)
    else:
        message = "Good morning team!"
        if needs_reviewer:
            message += "\n\n**We need a reviewer**\n  * " + \
                       "\n  * ".join(needs_reviewer)
        if needs_changes:
            message += "\n\n**We need changes**\n  * " + \
                       "\n  * ".join(needs_changes)
        if needs_review:
            message += "\n\n**We need a review**\n  * " + \
                       "\n  * ".join(needs_review)
        if needs_conflict_resolution:
            message += "\n\n**Update required**\n  * " + \
                       "\n  * ".join(needs_conflict_resolution)
    return message


if __name__ == "__main__":
    main()
