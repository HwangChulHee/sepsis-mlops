#!/usr/bin/env bash
# Download the PhysioNet/CinC 2019 sepsis training data into data/raw/.
#
# Source: PhysioNet open S3 mirror (public, no credentials needed).
# The official content pages serve ~40k tiny .psv files individually and there is
# no project zip, so recursive wget is painfully slow (~4 files/s). The S3 mirror
# lets us pull the exact key list and download with a single parallel curl process.
#
# Result: data/raw/training_setA/ (20,336 patients) + training_setB/ (20,000) = 40,336.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$ROOT/data/raw"
S3="https://physionet-open.s3.amazonaws.com"
PREFIX="challenge-2019/1.0.0/training"
mkdir -p "$RAW"

list_keys() { # $1 = setname -> prints all p*.psv keys
  local set="$1" token="" resp url
  while :; do
    url="$S3/?list-type=2&prefix=$PREFIX/$set/&max-keys=1000"
    [ -n "$token" ] && url="$url&continuation-token=$(python3 -c \
      'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=""))' "$token")"
    resp="$(curl -s "$url")"
    echo "$resp" | grep -oE '<Key>[^<]*</Key>' | sed -E 's#</?Key>##g'
    token="$(echo "$resp" | grep -oE '<NextContinuationToken>[^<]*</NextContinuationToken>' \
             | sed -E 's#</?NextContinuationToken>##g')"
    [ -z "$token" ] && break
  done
}

download_set() { # $1 = setname
  local set="$1" cfg
  mkdir -p "$RAW/$set"
  cfg="$(mktemp)"
  # build a curl config (url + output pairs); -z skips files already present
  list_keys "$set" | grep 'psv' | awk -F/ -v s="$set" -v raw="$RAW" \
    '{print "url = \"'"$S3"'/"$0"\"\noutput = \""raw"/"s"/"$NF"\""}' > "$cfg"
  echo "[$set] downloading $(grep -c '^url' "$cfg") files..."
  curl -sf --parallel --parallel-max 50 --retry 3 --config "$cfg"
  rm -f "$cfg"
  echo "[$set] have $(ls "$RAW/$set"/*.psv 2>/dev/null | wc -l) files."
}

download_set training_setA
download_set training_setB
echo "Done. Total: $(find "$RAW" -name '*.psv' | wc -l) / 40336 files."
