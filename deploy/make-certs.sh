#!/usr/bin/env sh
# Self-signed cert for local HTTPS. Browsers will warn — that is correct and
# expected; a real deployment swaps these for certs from a CA.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$DIR"
if [ -f "$DIR/adviser.crt" ]; then
  echo "certs already exist in $DIR — delete them to regenerate"
  exit 0
fi
openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "$DIR/adviser.key" -out "$DIR/adviser.crt" \
  -subj "/CN=adviser.local" \
  -addext "subjectAltName=DNS:localhost,DNS:adviser.local,IP:127.0.0.1" 2>/dev/null
chmod 600 "$DIR/adviser.key"
echo "wrote $DIR/adviser.crt and adviser.key"
