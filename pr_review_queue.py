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


def slack_notify(message:str, dry_run: bool):
    """
    Send notifications to Image Builder's Slack channel
    """
    url = os.getenv('SLACK_WEBHOOK_URL')
    github_server_url = os.getenv('GITHUB_SERVER_URL')
    github_repository = os.getenv('GITHUB_REPOSITORY')
    github_run_id = os.getenv('GITHUB_RUN_ID')
    github_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"

    print(message)
    if dry_run:
        print("This is just a dry run, not sending Slack notifications.")
        sys.exit(0)

    if url:
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
        assert response.status_code == 200, f"Error {response.status_code}\n{response.body}"
        assert response.body == "ok"
    else:
        print("No Slack webhook supplied.")


def get_check_runs(github_api, repo, head):
    """
    Return the combined status of GitHub checks as strong and a state emoji
    """
    check_runs = github_api.checks.list_for_ref(repo=repo,ref=head, per_page=100)
    runs = check_runs["check_runs"]
    total_count = check_runs["total_count"]
    successful_runs = 0

    for run in runs:
        if run['status'] == "completed" and run['conclusion'] == "success":
            successful_runs += 1

    if successful_runs == total_count:
        status = "success"
        state = "🟢"
    elif successful_runs < total_count:
        status = "failure"
        state = "🔴"
    else:
        print(f"Warning: something is terribly wrong: successful runs ({successful_runs}) should never be more than total runs ({total_count}).")
        sys.exit(1)

    return status, state


def get_commit_status(github_api, repo, pull_request_details):
    """
    Check whether the HEAD commit has passed the CI tests
    """
    head = pull_request_details["head"]
    combined_status = "failure" # failure by default

    # Check GitHub run status
    check_run_status, state = get_check_runs(github_api, repo, head["sha"])
    # Exit early if there are failed check runs
    if check_run_status == "failure":
        return combined_status, state

    # Check external CI status
    status = github_api.repos.get_combined_status_for_ref(repo=repo,ref=head["sha"])

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
        single_status = github_api.repos.list_commit_statuses_for_ref(repo=repo,ref=head["sha"])
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
    except: # pylint: disable=bare-except
        print(f"Couldn't get repositories for organisation {org}.")

    archived_repos = []

    if res is not None:
        for repo in res:
            if repo["archived"] == True or repo["disabled"] == True:
                archived_repos.append(repo["name"])

    if archived_repos != []:
        archived_repos_string = ", ".join(archived_repos)
        print(f"The following repositories are archived or disabled and will be ignored:\n  {archived_repos_string}")

    return archived_repos


def get_pull_request_details(github_api, repo, pull_request):
    """
    Return a pull_request_details object
    """
    for attempt in range(3):
        try:
            pull_request_details = github_api.pulls.get(repo=repo, pull_number=pull_request["number"])
        except: # pylint: disable=bare-except
            time.sleep(2) # avoid API blocking
        else:
            break
    else:
        print(f"Tried {attempt} times to get details for {pull_request.html_url}. Skipping.")

    if pull_request_details is not None:
        return pull_request_details
    else:
        print("Couldn't get any pull requests details.")
        sys.exit(1)


def get_changes_requested(github_api, repo, pull_request):
    """
    Iterate over reviews associated with a pull requested and return True if changes have been requested
    """
    reviews = github_api.pulls.list_reviews(repo=repo,pull_number=pull_request["number"])
    changes_requested = False

    for review in reviews:
        if review["state"] == "CHANGES_REQUESTED":
            changes_requested = True
            continue

    return changes_requested


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
    pr_properties["login"] = pull_request.user['login']
    pr_properties["additions"] = pull_request_details["additions"]
    pr_properties["deletions"] = pull_request_details["deletions"]
    pr_properties["draft"] = pull_request_details["draft"]
    pr_properties["mergeable"] = pull_request_details["mergeable"]
    pr_properties["rebaseable"] = pull_request_details["rebaseable"]
    pr_properties["mergeable_state"] = pull_request_details["mergeable_state"]
    pr_properties["changes_requested"] = get_changes_requested(github_api, repo, pull_request)
    pr_properties["status"], pr_properties["state"] = get_commit_status(github_api, repo, pull_request_details)

    return pr_properties


def get_pull_request_list(github_api, org, repo):
    """
    Return a list of pull requests with their properties
    """
    pull_request_list = []
    res = None

    if repo:
        print(f"Fetching pull requests from one repository: {org}/{repo}")
        query = (f"repo:{org}/{repo} type:pr is:open")
        entire_org = False
    else:
        print(f"Fetching pull requests from an entire organisation: {org}")
        query = (f"org:{org} type:pr is:open")
        entire_org = True
        archived_repos = get_archived_repos(github_api, org)

    try:
        res = github_api.search.issues_and_pull_requests(q=query, per_page=100, sort="updated",order="asc")
    except: # pylint: disable=bare-except
        print("Couldn't get any pull requests.")

    if res is not None:
        pull_requests = res["items"]
        print(f"{len(pull_requests)} pull requests retrieved.")

        for pull_request in pull_requests:
            if entire_org: # necessary when iterating over an organisation
                repo = pull_request.repository_url.split('/')[-1]
                if archived_repos != [] and repo in archived_repos:
                    print(f" * Repository '{org}/{repo}' is archived or disabled. Skipping.")
                    continue

            print(f" * Processing {pull_request.html_url}")
            pull_request_props = get_pull_request_properties(github_api, pull_request, org, repo)
            pull_request_list.append(pull_request_props)

    return pull_request_list


def create_pr_review_queue(pull_request_list):
    """
    Return a filtered list of pull requests according to these criteria:
        1. CI is green
        2. Not a draft
        3. No changes requested
        4. No merge conflicts
    """
    pr_review_queue = []
    i = 0
    for pull_request in pull_request_list:
        if (pull_request["status"] == "success" and
            pull_request["draft"] == False and
            pull_request["changes_requested"] == False and
            pull_request["mergeable_state"] != "dirty"):
            i += 1
            entry = (f"{i}. *{pull_request['repo']}*:"
                     f" <{pull_request['html_url']}|{pull_request['title']}>"
                     f" (+{pull_request['additions']}/-{pull_request['deletions']})"
                     f" by <https://github.com/{pull_request['login']}|{pull_request['login']}>")
            pr_review_queue.append(entry)

    return pr_review_queue


def main():
    """Create a pull request review queue"""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--github-token", help="Set a token for github.com", required=True)
    parser.add_argument("--org", help="Set an organisation on github.com", required=True)
    parser.add_argument("--repo", help="Set a repo in `--org` on github.com", required=False)
    parser.add_argument("--queue", help="Create a review queue", default=True,
                        action=argparse.BooleanOptionalAction)
    parser.add_argument("--dry-run", help="Don't send Slack notifications", default=False,
                        action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    github_api = GhApi(owner=args.org, token=args.github_token)

    pull_request_list = get_pull_request_list(github_api, args.org, args.repo)

    if args.queue:
        pr_review_queue = create_pr_review_queue(pull_request_list)
        if pr_review_queue == []:
            print("No pull requests found that match our criteria. Exiting.")
            sys.exit(0)

        message = ("Good morning, image builders! :meow_wave: Here are a couple of PRs :pull-request:"
                   "that could use your :eyes:\n" + "\n".join(pr_review_queue))
        slack_notify(message, args.dry_run)


if __name__ == "__main__":
    main()
