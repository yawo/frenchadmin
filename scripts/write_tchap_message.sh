#!/bin/bash

set -e

# Configuration
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd $PROJECT_DIR

# Load environment variables from .env file
export $(grep -v '^#' .env | xargs)

# Sending message's time management
MX_TXN="`date "+%s"`$(( RANDOM % 9999 ))"

# Message to send
BODY=$1

# Sending message's formatting
FORMATTED_BODY=$(echo "$BODY" | sed -e 's/\*\*\([^*]*\)\*\*/<strong>\1<\/strong>/g' \
                                     -e 's/^#### \(.*\)/<h4>\1<\/h4>/' \
                                     -e 's/^### \(.*\)/<h3>\1<\/h3>/' \
                                     -e 's/^## \(.*\)/<h2>\1<\/h2>/' \
                                     -e 's/^# \(.*\)/<h1>\1<\/h1>/' \
                                     -e 's/^- \(.*\)/<li>\1<\/li>/' )

# Request for sending the message avec formatage
curl -X PUT \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json' \
  --header "Authorization: Bearer $TCHAP_ACCOUNT_TOKEN" \
  -d "{
    \"msgtype\": \"m.text\",
    \"body\": \"$BODY\",
    \"format\": \"org.matrix.custom.html\",
    \"formatted_body\": \"$FORMATTED_BODY\"
  }" \
  "$TCHAP_SERVER/_matrix/client/v3/rooms/$TCHAP_ROOM_TOKEN/send/m.room.message/$MX_TXN"