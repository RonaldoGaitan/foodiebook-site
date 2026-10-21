#!/usr/bin/env bash
# Run by .github/workflows/stats.yml every morning.
set -euo pipefail
python3 dashboard/build.py /tmp/stats/index.html
npx -y staticrypt@3.5.4 /tmp/stats/index.html -d stats --short --remember 90 -c false \
  --template-title "Foodie Book dashboard" --template-instructions "Enter the dashboard password."
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add stats/index.html
git commit -m "Update stats dashboard" || exit 0
git push
gh api -X POST "repos/$GITHUB_REPOSITORY/pages/builds" || true
