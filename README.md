# Overview

This script creates a Pull Request review queue that is actionable. It is structured in multiple sections, pinging authors about changes being requested or reviewers about completing their work.

It currently runs on a daily schedule and sends messages to a Slack channel.

The Slack member ids are encrypted and can be decrypted or re-encrypted using the `encrypt_slack_nicks.py` script.

## Usage

`python3 pr_review_queue.py --github-token $GITHUB_TOKEN --org $GITHUB_ORG --repo $GITHUB_REPO`

alternatively you can also set `GITHUB_TOKEN` as environment variable and call

`python3 pr_review_queue.py --org $GITHUB_ORG --repo $GITHUB_REPO`
