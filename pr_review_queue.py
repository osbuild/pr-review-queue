#!/usr/bin/python3

"""
Small bot to create a pull request review queue on Slack
"""

import argparse
import sys
import datetime
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


def check_commit_status(component, ref, github_api):
    """
    Check whether the commit to be deployed has passed the CI tests
    """
    status = github_api.repos.get_combined_status_for_ref(owner='osbuild',repo=component,ref=ref)
    #status = github_api.repos.list_commit_statuses_for_ref(owner='osbuild',repo=component,ref=ref)
    if status.state == "success":
        state = "🟢"
    elif status.state == "failure":
        state = "🔴"
    elif status.state == "pending":
        state = "🟠"
    else:
        state = status.state
    #print(f" * {ref} {state}")

    return status.state


def print_warnings(args):
    """
    Print out some warnings and notices if we're not in a standard
    """
    if args.dry_run:
        print("This is only a dry run, so no hashes will be passed to a subsequent job.")


def list_green_pull_requests(github_api, org, repo):
    """
    Get pull requests that match the following criteria:
        1. CI is green
        2. Not a draft
        3. No changes requested
        4. No merge conflicts
    """
    query = (f"repo:{org}/{repo} type:pr is:open")
    res = None

    try:
        res = github_api.search.issues_and_pull_requests(q=query, per_page=100, sort="updated",order="desc")
    except: # pylint: disable=bare-except
        print("Couldn't get any pull requests.")

    if res is not None:
        pull_requests = res["items"]
        print(f"{len(pull_requests)} pull requests retrieved.")

        for pull_request in pull_requests:
            # useful when iterating a whole organisation
            # repo = pull_request.repository_url.split('/')[-1]
            try:
                pull_request_details = github_api.pulls.get(owner=org, repo=repo, pull_number=pull_request["number"])
            except: # pylint: disable=bare-except
                print("Couldn't get pull request...")

            if pull_request_details is not None:
                print(f"* {pull_request.html_url}")
                head = pull_request_details["head"]

                status = check_commit_status (repo, head["sha"], github_api)
                print(f"  head: {head["sha"]}")
                print(f"  ci status: {status}")
                if pull_request_details["draft"] == True:
                    print("  Pull request is a draft.")

                if pull_request_details["mergeable"] == "true":
                    print("  Pull request is mergeable.")

                if pull_request_details["mergeable_state"] == "clean":
                    print("  Pull request is cleanly mergeable.")

    else:
        print("Didn't get any pull requests.")


def main():
    """Get a commit that is at least a week old and green"""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--github-token", help="Set a token for github.com", required=True)
    parser.add_argument("--org", help="Set an organisation on github.com", required=True)
    parser.add_argument("--repo", help="Set a repo in `--org` on github.com", required=True)
    parser.add_argument("--dry-run", help="Don't trigger creating a merge request",
                        default=False, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    print_warnings(args)

    github_api = GhApi(owner='osbuild', token=args.github_token)
    list_green_pull_requests(github_api, args.org, args.repo)



if __name__ == "__main__":
    main()
