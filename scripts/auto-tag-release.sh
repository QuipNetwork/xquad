#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Push the tag for a merged release MR. Invoked from release:auto-tag
# in .gitlab/ci/release.yml on every push to main.
#
# Queries the GitLab MR API for the merged MR whose merge_commit_sha or
# squash_commit_sha matches CI_COMMIT_SHA, then reads source_branch
# directly -- no commit message parsing. Both fields are checked
# because squash merges set merge_commit_sha to null and record the
# pushed SHA in squash_commit_sha instead; dropping either check would
# silently stop auto-tagging squash-merged (or non-squash-merged)
# release MRs. If the branch matches release/vX.Y.Z, the version is
# extracted and the git tag is pushed via `glab api`. The GitLab
# Release page is created separately by release:notes once git-cliff
# notes are ready. Any other push exits cleanly (exit 0), since a
# push to main that isn't a release merge is the expected common case,
# not a failure. The lookup is retried a bounded number of times before
# that conclusion is drawn -- see the comment on the loop for why an
# empty result is ambiguous now that the job starts at t=0.
#
# That exit-0 path is distinct from a failure to *query* the API in the
# first place. This script runs under `set -euo pipefail`, unlike the
# inline job script it replaced, so a failing `glab api` call now
# aborts the script with a non-zero exit instead of yielding an empty
# SOURCE_BRANCH and falling through to the "nothing to tag" exit 0.
# That is deliberate: a silently skipped release tag is worse than a
# red pipeline, the same invisible-failure class `needs: []` plus
# `interruptible: false` were added to close in release:auto-tag. Note
# that .gitlab-ci.yml's root `retry:` does not cover `script_failure`,
# so a failure of this kind will not be retried by GitLab -- it
# surfaces once, as a failed pipeline on main, and needs a human or a
# re-push to resolve.
#
# Environment (all set by the calling job):
#   CI_PROJECT_ID  -- GitLab project ID, used to scope the API calls.
#   CI_COMMIT_SHA  -- the pushed commit, matched against merge_commit_sha
#                     / squash_commit_sha to find the merged MR.
#   GITLAB_TOKEN   -- read by `glab api`; the job sets this from the
#                     masked GITLAB_API_TOKEN project variable (a
#                     project access token with api scope -- CI_JOB_TOKEN
#                     cannot query MR metadata).
#
# Requires `glab` and `jq` on PATH. The calling job's image ships glab;
# jq is installed in the job's before_script (`apk add --no-cache jq`).

set -euo pipefail

# Retried, because an empty result here is ambiguous: it means either
# "this push was not a merge" (the common case, exit 0 below) or "the MR
# API has not recorded merge_commit_sha yet". The two are
# indistinguishable from the response, and the second one resolves
# itself in seconds.
#
# That ambiguity only became reachable when release:auto-tag moved to
# `needs: []` (see .gitlab/ci/release.yml). The job used to sit behind
# the whole stage barrier and run the better part of an hour after the
# merge, by which point the MR object had long settled; it now starts at
# t=0 and races the push that triggered it. Losing that race would take
# the exit-0 path below and skip the release silently -- the same
# invisible-failure class this file's header argues against, so it costs
# 3 attempts to rule out.
#
# Bounded, not indefinite: a genuine non-merge push to main must still
# reach exit 0, and it pays the full retry budget to get there. Three
# attempts is two sleeps, so ~10s -- long enough for API propagation,
# cheap enough to spend on every ordinary push to main.
SOURCE_BRANCH=""
for attempt in 1 2 3; do
    SOURCE_BRANCH=$(glab api \
        "projects/${CI_PROJECT_ID}/merge_requests?state=merged&target_branch=main&order_by=updated_at&sort=desc&per_page=100" \
        | jq -r ".[] | select(.merge_commit_sha == \"${CI_COMMIT_SHA}\" or .squash_commit_sha == \"${CI_COMMIT_SHA}\") | .source_branch")

    if [ -n "$SOURCE_BRANCH" ]; then
        break
    fi

    if [ "$attempt" -lt 3 ]; then
        echo "No merged MR for ${CI_COMMIT_SHA} yet (attempt ${attempt}/3) -- retrying in 5s."
        sleep 5
    fi
done

if [ -z "$SOURCE_BRANCH" ]; then
    echo "No merged MR found for this commit -- nothing to tag."
    exit 0
fi

echo "Merged from branch: ${SOURCE_BRANCH}"

VERSION=$(printf '%s' "$SOURCE_BRANCH" | sed -n 's|^release/v||p')

if [ -z "$VERSION" ]; then
    echo "Source branch is not a release branch -- nothing to tag."
    exit 0
fi

echo "Detected release merge: v${VERSION}"
if ! TAG_OUT=$(glab api "projects/${CI_PROJECT_ID}/repository/tags" \
    --method POST \
    -f "tag_name=v${VERSION}" \
    -f "ref=${CI_COMMIT_SHA}" \
    -f "message=xquad v${VERSION}" 2>&1); then
    if echo "$TAG_OUT" | grep -qi "already exists"; then
        echo "Tag v${VERSION} already exists -- skipping."
    else
        echo "Failed to create tag v${VERSION}: ${TAG_OUT}" >&2
        exit 1
    fi
fi
