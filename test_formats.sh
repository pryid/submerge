#!/usr/bin/env bash
set -euo pipefail

URL="${1:?usage: $0 https://domain/sub-merge/subId}"

raw="$(curl -fsSL "$URL?format=base64")"
[[ "${raw:0:20}" != mixed-port* ]]

html="$(curl -fsSL -H 'Accept: text/html' "$URL")"
html_head="${html:0:100}"
[[ "$html_head" == *html* || "$html_head" == *HTML* || "$html_head" == *DOCTYPE* ]]

mihomo="$(curl -fsSL -A 'mihomo/1.19.0' "$URL")"
[[ "${mihomo:0:200}" == *mixed-port* ]]
[[ "$mihomo" == *proxy-providers:* ]]
[[ "$mihomo" == *proxy-groups:* ]]
[[ "$mihomo" == *rule-providers:* ]]
[[ "$mihomo" == *rules:* ]]
[[ "$mihomo" == *"?format=base64"* ]]
[[ "$mihomo" == *"MATCH,🎛 1. Режим работы"* ]]

koala="$(curl -fsSL -A 'Koala' "$URL")"
[[ "${koala:0:200}" == *mixed-port* ]]

echo "OK"
