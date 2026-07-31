#!/usr/bin/env bash
# Create an API CONNECTOR pointing at a PUBLIC, no-auth API — so any client org
# can have a working Call API demo in one command, with no credentials to
# provision, no signup, and nothing to redact from a recording.
#
# VERIFIED 2026-07-30: `POST /v2/api-connectors` creates a connector from code
# and returns its apiConnectorId. No admin UI step is needed to CREATE one.
# (Triggering it from a workbook still needs the Call API action — see
# skills/sigma-company-dashboard/reference/api-actions.md for its status.)
#
# Usage:
#   scripts/api/create-public-api-connector.sh                 # list the catalog
#   scripts/api/create-public-api-connector.sh weather         # create one
#   scripts/api/create-public-api-connector.sh --all
#
# Every endpoint below is keyless, free, HTTPS, and sends
# `access-control-allow-origin: *`, so the same URL also works from a browser-side
# Sigma plugin (see plugins/public-api-live/) when you want a zero-setup demo.
#
# Env: self-bootstrapped via _env.sh (loads .env, caches OAuth token)
set -euo pipefail
source "$(dirname "$0")/_env.sh"

catalog() {
  cat <<'TXT'
  weather    Open-Meteo current conditions   api.open-meteo.com/v1/forecast
             dynamic: latitude, longitude          (universally understood; ties
             to ops demos — weather vs demand/volume/logistics)
  fx         Frankfurter exchange rates      api.frankfurter.app/latest
             dynamic: from, to                     (finance / margin demos)
  holidays   Nager.Date public holidays      date.nager.at/api/v3/PublicHolidays
             dynamic: year, countryCode            (staffing / capacity demos)
  country    REST Countries lookup           restcountries.com/v3.1/name
             dynamic: name                         (enrichment demos)
TXT
}

mk() { # name | description | method | url | queryParams-json | pathParams-json
  local name="$1" desc="$2" method="$3" url="$4" qp="$5" pp="${6:-[]}"
  local payload
  payload=$(python3 - "$name" "$desc" "$method" "$url" "$qp" "$pp" <<'PY'
import json, sys
name, desc, method, url, qp, pp = sys.argv[1:7]
print(json.dumps({
    "name": name, "description": desc,
    "params": {"method": method, "url": url, "headers": [],
               "pathParams": json.loads(pp), "queryParams": json.loads(qp),
               "body": "", "bodyParams": []}}))
PY
)
  sigma_curl -X POST -H "Content-Type: application/json" --data "$payload" \
    "$SIGMA_BASE_URL/v2/api-connectors" \
  | python3 -c 'import sys,json
d=json.load(sys.stdin)
print("  created  %s  %s" % (d.get("apiConnectorId"), d.get("name")))'
}

create_one() {
  case "$1" in
    weather)
      mk "Open-Meteo Current Weather (public)" \
         "Keyless public weather API. Map latitude/longitude from a column or control." \
         GET "https://api.open-meteo.com/v1/forecast" \
         '[{"key":"latitude","mode":"dynamic","type":"string"},
           {"key":"longitude","mode":"dynamic","type":"string"},
           {"key":"current","mode":"static","value":"temperature_2m,wind_speed_10m"}]' ;;
    fx)
      mk "Frankfurter FX Rates (public)" \
         "Keyless ECB reference rates. Map from/to currency codes." \
         GET "https://api.frankfurter.app/latest" \
         '[{"key":"from","mode":"dynamic","type":"string"},
           {"key":"to","mode":"dynamic","type":"string"}]' ;;
    holidays)
      mk "Nager.Date Public Holidays (public)" \
         "Keyless public-holiday calendar. Map year and ISO country code." \
         GET "https://date.nager.at/api/v3/PublicHolidays" \
         '[]' \
         '[{"key":"year","mode":"dynamic","type":"string"},
           {"key":"countryCode","mode":"dynamic","type":"string"}]' ;;
    country)
      mk "REST Countries Lookup (public)" \
         "Keyless country reference data. Map a country name." \
         GET "https://restcountries.com/v3.1/name" \
         '[]' '[{"key":"name","mode":"dynamic","type":"string"}]' ;;
    *) echo "unknown: $1" >&2; return 2 ;;
  esac
}

case "${1:-}" in
  "")     echo "Public, keyless APIs this can wire up:"; catalog
          echo; echo "run: $(basename "$0") <weather|fx|holidays|country|--all>" ;;
  --all)  for k in weather fx holidays country; do create_one "$k"; done ;;
  *)      create_one "$1" ;;
esac
