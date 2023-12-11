#!/usr/bin/python3

"""
Small bot to create a pull request review queue on Slack
"""

import argparse
import os
import sys
import time
from slack_sdk.webhook import WebhookClient
from ghapi.all import GhApi


# pylint: disable=too-few-public-methods,invalid-name
class fg:
    """
    Set of constants to print colored output in the terminal
    """
    BOLD = '\033[1m'  # bold
    OK = '\033[32m'  # green
    INFO = '\033[33m'  # yellow
    ERROR = '\033[31m'  # red
    RESET = '\033[0m'  # reset


def msg_error(body, fail=True):
    """
    Print error and exit
    """
    print(f"{fg.ERROR}{fg.BOLD}Error:{fg.RESET} {body}")
    if fail:
        sys.exit(1)


def slack_notify(message: str, dry_run: bool):
    """
    Send notifications to Image Builder's Slack channel
    """
    url = os.getenv('SLACK_WEBHOOK_URL')
    github_server_url = os.getenv('GITHUB_SERVER_URL')
    github_repository = os.getenv('GITHUB_REPOSITORY')
    github_run_id = os.getenv('GITHUB_RUN_ID')
    github_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"

    print(message)

    if not dry_run and url:
        webhook = WebhookClient(url)
        response = webhook.send(
            text="fallback",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"<{github_url}|pr-review-queue>: {message}"
                    }
                }
            ])
        assert response.status_code == 200
        assert response.body == "ok"


def check_commit_status(component, ref, github_api):
    """
    Check whether the commit to be deployed has passed the CI tests
    """
    status = github_api.repos.get_combined_status_for_ref(owner='osbuild',repo=component,ref=ref)
    if status.state == "success":
        state = "🟢"
    elif status.state == "failure":
        state = "🔴"
    elif status.state == "pending":
        state = "🟠"
        single_status = github_api.repos.list_commit_statuses_for_ref(owner='osbuild',repo=component,ref=ref)
        if single_status == []:
            check_runs = github_api.checks.list_for_ref(owner='osbuild',repo=component,ref=ref, per_page=100)
            runs = check_runs["check_runs"]
            successful_runs = 0
            for run in runs:
                if run['status'] == "completed" and run['conclusion'] == "success":
                    successful_runs += 1
            if successful_runs == len(runs):
                status.state = f"success ({successful_runs}/{len(runs)})"
                state = "🟢"
            elif successful_runs < len(runs):
                status.state = f"failure ({successful_runs}/{len(runs)})"
                state = "🔴"
            else:
                print(f"Warning: something is terribly wrong: successful runs ({successful_runs}) should never be more than total runs ({len(runs)}).")
    else:
        state = status.state
    #print(f" * {ref} {state}")

    return status.state, state


def list_green_pull_requests(github_api, org, repo):
    """
    Get pull requests that match the following criteria:
        1. CI is green
        2. Not a draft
        3. No changes requested
        4. No merge conflicts
    """
    if repo:
        print(f"Fetching pull requests from one repository: {org}/{repo}")
        query = (f"repo:{org}/{repo} type:pr is:open")
        entire_org = False
    else:
        print(f"Fetching pull requests from an entire organisation: {org}")
        query = (f"org:{org} type:pr is:open")
        entire_org = True
    res = None

    try:
        res = github_api.search.issues_and_pull_requests(q=query, per_page=100, sort="updated",order="desc")
    except: # pylint: disable=bare-except
        print("Couldn't get any pull requests.")

    if res is not None:
        pull_requests = res["items"]
        print(f"{len(pull_requests)} pull requests retrieved.")
        message = "*Pull request review queue*\n"

        for pull_request in pull_requests:
            if entire_org: # necessary when iterating an organisation
                repo = pull_request.repository_url.split('/')[-1]
            for attempt in range(3):
                try:
                    pull_request_details = github_api.pulls.get(owner=org, repo=repo, pull_number=pull_request["number"])
                except: # pylint: disable=bare-except
                    time.sleep(2) # avoid API blocking
                else:
                    break
            else:
                print(f"Tried {attempt} times to get details for {pull_request.html_url}. Skipping.")

            if pull_request_details is not None:
                head = pull_request_details["head"]

                if pull_request_details["draft"] == True:
                    status = "draft"
                    state = "⚪"
                else:
                    status, state = check_commit_status (repo, head["sha"], github_api)

                print(f"* {pull_request.html_url} (+{pull_request_details["additions"]}/-{pull_request_details["deletions"]}) {state}")

                print(f"  Status: {status}")
                if status == "draft": # requirement 1: not a draft
                    continue
                elif "failure" in status or "pending" in status: # requirement 2: CI is a success
                    continue

                if pull_request_details["mergeable"] == True:
                    print("  Pull request is mergeable.")
                if pull_request_details["rebaseable"] == True:
                    print("  Pull request is rebaseable.")
                if pull_request_details["mergeable_state"] == "clean":
                    print("  Pull request is cleanly mergeable.")
                elif pull_request_details["mergeable_state"] == "dirty": # requirement 4: no merge conflicts
                    print("  Pull request has merge conflicts.")
                    continue
                else:
                    print(f"  Pull request's mergeable state is '{pull_request_details["mergeable_state"]}'.")

                user = pull_request.user
                message += f"* {repo}: [{pull_request.title}]({pull_request.html_url}) (+{pull_request_details["additions"]}/-{pull_request_details["deletions"]}) by [{user['login']}](https://github.com/{user['login']})\n"

        slack_notify(message, dry_run)

    else:
        print("Didn't get any pull requests.")


def main():
    """Get a commit that is at least a week old and green"""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--github-token", help="Set a token for github.com", required=True)
    parser.add_argument("--org", help="Set an organisation on github.com", required=True)
    parser.add_argument("--repo", help="Set a repo in `--org` on github.com", required=False)
    parser.add_argument("--dry-run", help="Don't send Slack notifications",
                        default=False, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    github_api = GhApi(owner='osbuild', token=args.github_token)
    list_green_pull_requests(github_api, args.org, args.repo, args.dry_run)



if __name__ == "__main__":
    main()
