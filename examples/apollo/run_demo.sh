#!/usr/bin/env sh
# Rebuild the playbook's Apollo corpus graph end-to-end, agent-native (no API key):
# the JSON files under extractions/ and resolution.json stand in for the agent's answers.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${1:-/tmp/growmos-apollo-demo}
rm -rf "$WORK" && mkdir -p "$WORK" && cp -R "$HERE/docs" "$WORK/docs"
cd "$WORK"

growmos init --preset general --agent none
for f in "$HERE"/extractions/*.json; do
  ref=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['source'])" "$f")
  growmos apply extraction "$f" --source "$ref"
done
for t in PERSON LOCATION EVENT ORGANIZATION ARTIFACT; do
  python3 -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))[sys.argv[2]]))" "$HERE/resolution.json" $t \
    | growmos apply resolution - --type $t
done
mkdir -p .growmos/eval/gold && cp "$HERE"/gold/*.json .growmos/eval/gold/ && cp "$HERE/gold-aliases.json" .growmos/eval/aliases.json

echo; growmos status
echo; growmos query "Which locations are connected to people who flew on Apollo 11?" --triples-only
echo; printf '(Neil Armstrong) --[commanded]--> (Gemini 12)\n' | growmos check -
echo; growmos eval
echo; growmos doctor
echo; echo "Graph built in $WORK — try: cd $WORK && growmos next"
