#!/usr/bin/env bash
# Run by .github/workflows/stats.yml every morning.
set -euo pipefail
echo "== 1/3 Reading Sentry and PostHog"
python3 dashboard/build.py /tmp/stats/index.html
echo "== 2/3 Adding the password"
npx -y staticrypt@3.5.4 /tmp/stats/index.html -d stats --short --remember 90 -c false \
  --template-title "Foodie Book dashboard" --template-instructions "Enter the dashboard password."
echo "== 3/3 Publishing to foodiebook.app/stats/"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add stats/index.html
git commit -m "Update stats dashboard" || exit 0
git push origin HEAD:main || { echo "::error::GitHub refused the save. Set Settings > Actions > General > Workflow permissions to Read and write."; exit 1; }
gh api -X POST "repos/$GITHUB_REPOSITORY/pages/builds" || true
